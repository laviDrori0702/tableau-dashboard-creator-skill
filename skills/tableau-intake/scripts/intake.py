"""Gate, validate, and commit the ``intake`` step of the tableau-dashboard-plugin.

This is the executable core of the ``tableau-intake`` skill (CONTRACT.md step 2):
the optional, idempotent step that turns a free-form ``DASHBOARD-REQUEST.md`` (or
pasted text) into a structured ``PRD.md``. The PRD *content* is authored by the
model; this script owns the three things that must be **mechanically guaranteed**
rather than left to model prose:

1. **Entry gate** - intake refuses to run until ``init`` is ``approved`` in
   ``STATE.md`` (and ``STATE.md`` exists at all). This mirrors the ordering rule
   in CONTRACT.md §4.1: a step does not run before its prerequisites are resolved.
2. **PRD schema** - on approval the produced ``PRD.md`` must contain the required
   core sections (``Overview``, ``Visualizations``). The common ``KPIs`` /
   ``Filters`` / ``Additional Notes`` sections are *recommended but optional*: not
   every dashboard has KPIs or filters, so a PRD that legitimately omits them is
   still valid. The skill proposes them while refining; the validator never refuses
   on a missing recommended section.
3. **STATE.md transition** - intake is the first step that *mutates* an existing
   ``STATE.md`` (init only creates it, route only reads it). Committing flips
   ``intake`` to ``approved``/``skipped`` and propagates staleness (CONTRACT.md
   §4.2): re-running flips every downstream ``approved`` step to ``stale`` so the
   pipeline can never silently disagree with a changed request.

The module is intentionally pure and stdlib-only (it does **not** import the
router) so the contract test can call its functions directly, exactly like
``init.py`` / ``route.py``. The CLI exposes two subcommands the skill runs at two
moments - ``precheck`` (before authoring) and ``commit`` (after approval). What
the CLI prints to stdout is the program's *output*; diagnostics go through
``logging``.

Keep ``STEP_ORDER`` below in lock-step with the ordered step list in CONTRACT.md §1.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Canonical constants (mirror of CONTRACT.md §1 / §2 / §3) ----------------

STATE_FILENAME = "STATE.md"
PRD_FILENAME = "PRD.md"

#: Production request input (preferred) and its scaffold/ demo fallback (§3.1).
REQUEST_FILENAME = "DASHBOARD-REQUEST.md"
SCAFFOLD_REQUEST = "scaffold/EXAMPLE-DASHBOARD-REQUEST.md"

#: This step, and the upstream step whose approval gates it (CONTRACT.md §4.1).
INTAKE_STEP = "intake"
INIT_STEP = "init"

#: The 8 step names in canonical order (mirror of CONTRACT.md §1). Used to decide
#: which steps are "downstream of intake" for staleness propagation (§4.2).
STEP_ORDER: tuple[str, ...] = (
    "init", "intake", "data", "brand", "plan", "mock", "spec", "build",
)

#: Statuses the commit subcommand may write for the intake step.
COMMIT_STATUSES: tuple[str, ...] = ("approved", "skipped")

#: PRD sections every dashboard PRD must have: what it is for + what it shows.
PRD_REQUIRED_SECTIONS: tuple[str, ...] = ("Overview", "Visualizations")

#: Common but genuinely optional PRD sections. Proposed while refining, never
#: required - not every dashboard has KPIs or filters.
PRD_RECOMMENDED_SECTIONS: tuple[str, ...] = ("KPIs", "Filters", "Additional Notes")


# --- STATE.md reading --------------------------------------------------------

# Matches a markdown ATX heading line, capturing its text (drops leading "#"s and
# any trailing "#"s): "## Overview" -> "Overview".
_HEADING_LINE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")


def _resolve_state_path(target: Path) -> Path:
    """Resolve the STATE.md path for a project directory or a direct file path.

    Args:
        target: Either a project directory (which may contain ``STATE.md``) or a
            path to a ``STATE.md`` file directly.

    Returns:
        The path where ``STATE.md`` is expected. It may or may not exist.
    """
    if target.is_dir():
        return target / STATE_FILENAME
    return target


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


# --- PRD schema --------------------------------------------------------------

def _markdown_headings(text: str) -> list[str]:
    """Extract the text of every markdown heading in a document.

    Args:
        text: Markdown document contents.

    Returns:
        The heading texts, in document order (e.g. ``["Overview", "KPIs"]``).
    """
    return [
        match.group(1).strip()
        for raw_line in text.splitlines()
        if (match := _HEADING_LINE.match(raw_line.strip()))
    ]


def validate_prd(text: str) -> tuple[bool, list[str], list[str]]:
    """Check a PRD's section coverage against the canonical schema.

    A PRD is *valid* when it contains every **required** section; recommended
    sections are reported when absent but never make a PRD invalid (CONTRACT
    feedback: not all dashboards have KPIs or filters). Section names are matched
    case-insensitively as substrings of the document's headings, so a refined PRD
    that keeps custom headings (e.g. ``## Overview & Audience``) still validates -
    and extra custom sections are always allowed.

    Args:
        text: The contents of a ``PRD.md`` file.

    Returns:
        A ``(ok, missing_required, missing_recommended)`` tuple. ``ok`` is True
        iff ``missing_required`` is empty.
    """
    headings_blob = "\n".join(_markdown_headings(text)).lower()
    missing_required = [
        section for section in PRD_REQUIRED_SECTIONS
        if section.lower() not in headings_blob
    ]
    missing_recommended = [
        section for section in PRD_RECOMMENDED_SECTIONS
        if section.lower() not in headings_blob
    ]
    return (not missing_required, missing_required, missing_recommended)


def render_prd_template() -> str:
    """Return the canonical PRD template text this skill ships.

    Reads ``references/PRD-TEMPLATE.md`` so there is a single source of truth for
    the schema-complete starting PRD - the same file the skill hands the model to
    fill in. It is, by construction, schema-complete (contains the required core).

    Returns:
        The template file contents.

    Raises:
        FileNotFoundError: If the bundled template is missing.
    """
    # This script lives in ``<skill>/scripts/``; references/ sits at the skill root.
    template_path = Path(__file__).resolve().parent.parent / "references" / "PRD-TEMPLATE.md"
    if not template_path.is_file():
        raise FileNotFoundError(f"PRD template not found: {template_path}")
    return template_path.read_text(encoding="utf-8-sig")


# --- STATE.md rewriting ------------------------------------------------------

def _format_step_row(cells: list[str]) -> str:
    """Render a Steps-table row with the canonical column widths.

    Matches the alignment ``init.render_state_md`` produces (``order<5 | step<6 |
    skill<14 | status<8``) so a rewritten row stays visually consistent with the
    rest of the table.

    Args:
        cells: The four cell values ``[order, step, skill, status]``.

    Returns:
        The formatted ``| ... |`` table row (no trailing newline).
    """
    order, step, skill, status = cells[0], cells[1], cells[2], cells[3]
    return f"| {order:<5} | {step:<6} | {skill:<14} | {status:<8} |"


def apply_status_updates(text: str, updates: dict[str, str]) -> str:
    """Rewrite the status cell of one or more Steps-table rows.

    Only rows inside the ``## Steps`` table whose step name is a key in
    ``updates`` are touched; every other line (metadata, prose, untouched rows) is
    preserved byte-for-byte. The trailing newline of the input is preserved.

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


def _downstream_stale_updates(statuses: dict[str, str]) -> dict[str, str]:
    """Compute which downstream steps must flip to ``stale`` (CONTRACT.md §4.2).

    Every step ordered after ``intake`` that is currently ``approved`` becomes
    ``stale``; steps already ``pending`` / ``skipped`` / ``stale`` are left as-is.

    Args:
        statuses: The current ``{step_name: status}`` mapping.

    Returns:
        ``{step_name: "stale"}`` for each downstream step that was ``approved``.
    """
    intake_index = STEP_ORDER.index(INTAKE_STEP)
    return {
        step: "stale"
        for step in STEP_ORDER[intake_index + 1:]
        if statuses.get(step) == "approved"
    }


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def entry_gate_blocker(project_root: Path) -> Optional[str]:
    """Return why intake may not run yet, or ``None`` if it may.

    Intake refuses to run unless ``STATE.md`` exists and ``init`` is ``approved``.

    Args:
        project_root: The analyst's project directory.

    Returns:
        A human-readable blocker message, or ``None`` when the gate is open.
    """
    state_path = project_root / STATE_FILENAME
    if not state_path.exists():
        return (
            "No STATE.md found. Run 'tableau-init' first to scaffold the project "
            "and initialize STATE.md before running 'tableau-intake'."
        )

    init_status = parse_statuses(state_path.read_text(encoding="utf-8-sig")).get(
        INIT_STEP, "pending"
    )
    if init_status != "approved":
        return (
            f"Step 'init' is '{init_status}', not 'approved'. Run 'tableau-init' "
            f"first; 'tableau-intake' cannot run until init is approved."
        )
    return None


# --- precheck ----------------------------------------------------------------

@dataclass(frozen=True)
class PrecheckResult:
    """The state intake needs to know before authoring a PRD.

    Attributes:
        can_run: True when the entry gate is open (init approved, STATE.md present).
        blocker: Why intake cannot run, when ``can_run`` is False; else ``None``.
        prd_exists: Whether a ``PRD.md`` already exists at the project root. When
            True the skill must offer refine-vs-overwrite, never silently overwrite.
        request_source: Where the free-form request will be read from -
            ``"DASHBOARD-REQUEST.md"`` (production), ``SCAFFOLD_REQUEST`` (demo
            fallback), or ``"none"`` (the analyst will paste text).
        intake_status: The intake step's current status (to detect a re-run).
    """

    can_run: bool
    blocker: Optional[str]
    prd_exists: bool
    request_source: str
    intake_status: str


def _resolve_request_source(project_root: Path) -> str:
    """Pick the request input, preferring production over the demo fallback (§3.1).

    Args:
        project_root: The analyst's project directory.

    Returns:
        ``REQUEST_FILENAME``, ``SCAFFOLD_REQUEST``, or ``"none"``.
    """
    if (project_root / REQUEST_FILENAME).exists():
        return REQUEST_FILENAME
    if (project_root / SCAFFOLD_REQUEST).exists():
        return SCAFFOLD_REQUEST
    return "none"


def precheck(project_dir: Path | str) -> PrecheckResult:
    """Report whether intake may run and what it should read.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`PrecheckResult`. When the entry gate is closed, ``can_run`` is
        False and ``blocker`` explains why; the other fields are placeholders.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return PrecheckResult(False, blocker, False, "none", "unknown")

    statuses = parse_statuses((project_root / STATE_FILENAME).read_text(encoding="utf-8-sig"))
    return PrecheckResult(
        can_run=True,
        blocker=None,
        prd_exists=(project_root / PRD_FILENAME).exists(),
        request_source=_resolve_request_source(project_root),
        intake_status=statuses.get(INTAKE_STEP, "pending"),
    )


# --- commit ------------------------------------------------------------------

@dataclass
class CommitResult:
    """Outcome of committing the intake step's result to STATE.md.

    Attributes:
        ok: True when STATE.md was updated; False when the commit was refused.
        message: Human-readable explanation (the refusal reason when not ``ok``).
        status_set: The status written for ``intake`` (``approved``/``skipped``),
            or ``None`` when refused.
        staled_steps: Downstream steps flipped to ``stale`` by this commit.
        missing_required: Required PRD sections that were absent (only populated
            on an approval refusal).
        missing_recommended: Recommended PRD sections that were absent (reported as
            an informational note; never blocks an approval).
    """

    ok: bool
    message: str
    status_set: Optional[str] = None
    staled_steps: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_recommended: list[str] = field(default_factory=list)


def commit(project_dir: Path | str, status: str) -> CommitResult:
    """Record the intake step's result in STATE.md and propagate staleness.

    On ``approved`` the project's ``PRD.md`` must exist and contain the required
    core sections, or the commit is refused (so the model fixes it and re-commits).
    On either status, every downstream ``approved`` step is flipped to ``stale``
    (CONTRACT.md §4.2) - a no-op on a first run, but the guard that keeps a re-run
    from silently disagreeing with the rest of the pipeline.

    Args:
        project_dir: The analyst's project directory.
        status: The status to record for ``intake`` - one of :data:`COMMIT_STATUSES`.

    Returns:
        A :class:`CommitResult`. ``ok`` is False (and STATE.md is left untouched)
        when the entry gate is closed or an approval's PRD is incomplete.

    Raises:
        ValueError: If ``status`` is not an allowed commit status.
    """
    if status not in COMMIT_STATUSES:
        allowed = " | ".join(COMMIT_STATUSES)
        raise ValueError(f"Invalid intake status '{status}'. Allowed: {allowed}.")

    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return CommitResult(False, blocker)

    missing_recommended: list[str] = []
    if status == "approved":
        prd_path = project_root / PRD_FILENAME
        if not prd_path.exists():
            return CommitResult(
                False,
                f"Cannot approve intake: '{PRD_FILENAME}' does not exist. Author it "
                f"first, or commit '--status skipped' to skip this step.",
            )
        ok, missing_required, missing_recommended = validate_prd(
            prd_path.read_text(encoding="utf-8-sig")
        )
        if not ok:
            return CommitResult(
                False,
                f"'{PRD_FILENAME}' is missing required section(s): "
                f"{', '.join(missing_required)}. Add them and re-run commit.",
                missing_required=missing_required,
                missing_recommended=missing_recommended,
            )

    state_path = project_root / STATE_FILENAME
    text = state_path.read_text(encoding="utf-8-sig")
    statuses = parse_statuses(text)

    stale_updates = _downstream_stale_updates(statuses)
    updates = {INTAKE_STEP: status, **stale_updates}
    state_path.write_text(apply_status_updates(text, updates), encoding="utf-8")

    staled_steps = sorted(stale_updates, key=STEP_ORDER.index)
    logger.info(f"Set intake -> {status}; marked stale: {staled_steps or 'none'}.")
    return CommitResult(
        ok=True,
        message=f"intake -> {status}",
        status_set=status,
        staled_steps=staled_steps,
        missing_recommended=missing_recommended,
    )


# --- CLI ---------------------------------------------------------------------

def format_precheck(result: PrecheckResult) -> str:
    """Render a :class:`PrecheckResult` as a human-readable block.

    Args:
        result: The precheck result to render.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    # Plain ASCII only: this prints to the console, which on Windows is cp1252
    # and would raise UnicodeEncodeError on emoji/box-drawing glyphs.
    if not result.can_run:
        return f"[BLOCKED] tableau-intake cannot run.\n{result.blocker}"

    lines = ["[INTAKE] precheck OK - tableau-intake can run."]
    lines.append(f"  PRD.md exists : {'yes' if result.prd_exists else 'no'}")
    if result.prd_exists:
        lines.append(
            "    -> a PRD.md already exists; offer REFINE vs OVERWRITE - never "
            "silently overwrite the analyst's work."
        )

    if result.request_source == "none":
        lines.append(
            "  request source: none found - ask the analyst to paste the request text."
        )
    elif result.request_source == SCAFFOLD_REQUEST:
        lines.append(
            f"  request source: {SCAFFOLD_REQUEST} (DEMO fallback - say so; there is "
            f"no root {REQUEST_FILENAME})."
        )
    else:
        lines.append(f"  request source: {result.request_source} (production input).")

    rerun_note = (
        " (re-run; downstream approved steps will be marked stale on commit)"
        if result.intake_status in ("approved", "stale")
        else ""
    )
    lines.append(f"  intake status : {result.intake_status}{rerun_note}")
    return "\n".join(lines)


def format_commit(result: CommitResult) -> str:
    """Render a :class:`CommitResult` as a human-readable block.

    Args:
        result: The commit result to render.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    # Plain ASCII only (see format_precheck).
    if not result.ok:
        return f"[REFUSED] {result.message}"

    lines = [f"[INTAKE] {result.message}."]
    if result.missing_recommended:
        lines.append(
            f"  note: PRD has no {', '.join(result.missing_recommended)} section(s) "
            f"- optional, left as-is."
        )
    if result.staled_steps:
        lines.append(
            f"  downstream marked stale: {', '.join(result.staled_steps)} "
            f"(re-run these in order)."
        )
    lines.append(
        "  next: open a fresh conversation and run 'tableau-route' (or 'tableau-data')."
    )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point: run ``precheck`` or ``commit`` and print the result.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``2`` when intake is blocked/refused
        or on a usage error.
    """
    parser = argparse.ArgumentParser(
        description="Gate, validate, and commit the tableau-intake step.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    precheck_parser = subparsers.add_parser(
        "precheck", help="Report whether intake may run and what to read."
    )
    precheck_parser.add_argument(
        "project_dir", nargs="?", default=".", help="Project directory (default: cwd)."
    )

    commit_parser = subparsers.add_parser(
        "commit", help="Record intake's result in STATE.md and propagate staleness."
    )
    commit_parser.add_argument(
        "project_dir", nargs="?", default=".", help="Project directory (default: cwd)."
    )
    commit_parser.add_argument(
        "--status", required=True, choices=COMMIT_STATUSES,
        help="Status to record for the intake step.",
    )

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "precheck":
        precheck_result = precheck(args.project_dir)
        print(format_precheck(precheck_result))
        return 0 if precheck_result.can_run else 2

    commit_result = commit(args.project_dir, args.status)
    print(format_commit(commit_result))
    return 0 if commit_result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
