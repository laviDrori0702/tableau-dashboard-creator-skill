"""The STATE.md domain of the tableau-data skill: read the gate, write the transition.

The data step's only contract with the rest of the pipeline (besides DATA-MODEL.md) is
``STATE.md``. This module owns both directions of that contract:

* **Reading** (:func:`parse_statuses`) - tolerant per-step status parsing, the same
  shape ``intake.py`` / ``route.py`` use.
* **The entry gate** (:func:`entry_gate_blocker`) - data refuses to run until ``init``
  is ``approved`` and ``STATE.md`` exists (CONTRACT.md §4.1).
* **Writing** (:func:`apply_status_updates`, :func:`set_data_mode`,
  :func:`downstream_stale_updates`) - flip ``data`` to ``approved``, record the
  acquisition ``data_mode``, and propagate staleness to downstream steps (CONTRACT.md §4.2).

Stdlib-only, so the contract test (and :mod:`data`) can call these without ``requests``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from constants import DATA_STEP, INIT_STEP, STATE_FILENAME, STEP_ORDER


# --- STATE.md reading (shared shape with intake.py / route.py) ---------------

def parse_statuses(text: str) -> dict[str, str]:
    """Parse the per-step statuses out of a STATE.md manifest.

    Parsing is tolerant (matching the router's parser): only genuine
    ``| order | step | skill | status |`` rows whose step name is known
    contribute; header, separator, and stray rows are ignored.

    Args:
        text: The full contents of a ``STATE.md`` file.

    Returns:
        A ``{step_name: status}`` mapping (both lower-cased).
    """
    statuses: dict[str, str] = {}
    in_steps = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Section headers toggle which parser applies.
        if line.lower().startswith("## steps"):
            in_steps = True
            continue
        if line.startswith("## "):  # any other section ends the Steps table
            in_steps = False

        if in_steps and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) < 4:
                continue
            step_name, status = cells[1].lower(), cells[3].lower()
            if step_name in STEP_ORDER:
                statuses[step_name] = status
    return statuses


# --- STATE.md rewriting (shared shape with intake.py) ------------------------

def _format_step_row(cells: list[str]) -> str:
    """Render a Steps-table row with the canonical column widths.

    Matches the alignment ``init.render_state_md`` produces (``order<5 | step<6 |
    skill<14 | status<8``) so a rewritten row stays visually consistent.

    Args:
        cells: The four cell values ``[order, step, skill, status]``.

    Returns:
        The formatted ``| ... |`` table row (no trailing newline).
    """
    order, step, skill, status = cells[0], cells[1], cells[2], cells[3]
    return f"| {order:<5} | {step:<6} | {skill:<14} | {status:<8} |"


def apply_status_updates(text: str, updates: dict[str, str]) -> str:
    """Rewrite the status cell of one or more Steps-table rows.

    Only rows inside the ``## Steps`` table whose step name is a key in ``updates``
    are touched; every other line (metadata, prose, untouched rows) is preserved
    byte-for-byte. The trailing newline of the input is preserved.

    Args:
        text: The full ``STATE.md`` contents.
        updates: ``{step_name: new_status}`` for the rows to rewrite (step names
            lower-cased).

    Returns:
        The updated ``STATE.md`` contents.
    """
    out_lines: list[str] = []
    in_steps = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()

        if stripped.lower().startswith("## steps"):
            in_steps = True
            out_lines.append(raw_line)
            continue
        if stripped.startswith("## "):  # any other section ends the Steps table
            in_steps = False

        if in_steps and stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 4 and cells[1].lower() in updates:
                cells[3] = updates[cells[1].lower()]
                out_lines.append(_format_step_row(cells))
                continue

        out_lines.append(raw_line)

    result = "\n".join(out_lines)
    if text.endswith("\n"):
        result += "\n"
    return result


def set_data_mode(text: str, mode: str) -> str:
    """Rewrite the ``- data_mode: ...`` metadata line in a STATE.md manifest.

    Only the metadata line is touched; any trailing inline comment (e.g.
    ``# csv | published-ds``) and every other line are preserved. If no ``data_mode``
    line exists the text is returned unchanged (older manifests stay valid).

    Args:
        text: The full ``STATE.md`` contents.
        mode: The new mode to record (e.g. :data:`constants.DATA_MODE_PUBLISHED_DS`).

    Returns:
        The updated ``STATE.md`` contents (trailing newline preserved).
    """
    out_lines: list[str] = []
    for raw_line in text.splitlines():
        match = re.match(r"^(\s*-\s*data_mode\s*:\s*)(\S+)(.*)$", raw_line)
        if match:
            out_lines.append(f"{match.group(1)}{mode}{match.group(3)}")
        else:
            out_lines.append(raw_line)
    result = "\n".join(out_lines)
    if text.endswith("\n"):
        result += "\n"
    return result


def downstream_stale_updates(statuses: dict[str, str]) -> dict[str, str]:
    """Compute which downstream steps must flip to ``stale`` (CONTRACT.md §4.2).

    Every step ordered after ``data`` that is currently ``approved`` becomes
    ``stale``; steps already ``pending`` / ``skipped`` / ``stale`` are left as-is.

    Args:
        statuses: The current ``{step_name: status}`` mapping.

    Returns:
        ``{step_name: "stale"}`` for each downstream step that was ``approved``.
    """
    data_index = STEP_ORDER.index(DATA_STEP)
    return {
        step: "stale"
        for step in STEP_ORDER[data_index + 1:]
        if statuses.get(step) == "approved"
    }


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def entry_gate_blocker(project_root: Path) -> Optional[str]:
    """Return why data may not run yet, or ``None`` if it may.

    Data refuses to run unless ``STATE.md`` exists and ``init`` is ``approved``. It
    has no producer-gated required reads (CONTRACT.md §1), so this is its only gate.

    Args:
        project_root: The analyst's project directory.

    Returns:
        A human-readable blocker message, or ``None`` when the gate is open.
    """
    state_path = project_root / STATE_FILENAME
    if not state_path.exists():
        return (
            "No STATE.md found. Run 'tableau-init' first to scaffold the project "
            "and initialize STATE.md before running 'tableau-data'."
        )

    init_status = parse_statuses(state_path.read_text(encoding="utf-8-sig")).get(
        INIT_STEP, "pending"
    )
    if init_status != "approved":
        return (
            f"Step 'init' is '{init_status}', not 'approved'. Run 'tableau-init' "
            f"first; 'tableau-data' cannot run until init is approved."
        )
    return None
