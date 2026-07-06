"""Gate, version, and commit the ``mock`` step of the tableau-dashboard-plugin.

This is the orchestration core of the ``tableau-mock`` skill (CONTRACT.md step 6): the
non-skippable step that turns the strict ``DASHBOARD-PLAN.md`` into an interactive
``mock.html`` demo populated from the real sample CSVs. The mock *markup* is authored by
the model; the coverage checklist and slot-sizing guard live in :mod:`coverage`. This
module owns the parts that touch ``STATE.md`` and the filesystem:

1. **Entry gate** - mock refuses to run until both required reads are present and their
   producers resolved (CONTRACT.md §4.1): ``DASHBOARD-PLAN.md`` (from ``plan``) - the
   blueprint it builds against - and at least one sample CSV under ``data/`` (from
   ``data``), with the ``scaffold/sample-data/`` demo as the accepted fallback (§3.1).
2. **Versioning** - the mock is a *deliverable* written under ``mock-version/v_N/``
   (CONTRACT.md §4.3): committing after a prior approval bumps ``current_version`` to a
   fresh ``v_N`` (preserving the previous copy); before approval it overwrites the
   current one.
3. **STATE.md transition** - committing flips ``mock`` to ``approved`` and propagates
   staleness to ``spec``/``build`` (§4.2), so the spec and workbook can never silently
   disagree with a re-rendered mock.

The module is pure and stdlib-only (it does **not** import the router) so the contract
test can call its functions directly, exactly like ``plan.py`` / ``tableau-data``'s
``state.py``. The CLI exposes ``precheck`` (before authoring - reports the target version
dir + the coverage targets), ``validate`` (the coverage checklist + guard on a draft),
and ``commit`` (after approval). Program output goes to stdout; diagnostics through
``logging``.

Keep ``STEP_ORDER`` in lock-step with CONTRACT.md §1.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from coverage import (
    LAYOUT_MANIFEST_ID,
    MockValidation,
    parse_plan_coverage,
    validate_mock,
)

logger = logging.getLogger(__name__)

# --- Canonical constants (mirror of CONTRACT.md §1 / §3 / §4.3) --------------

STATE_FILENAME = "STATE.md"
PLAN_FILENAME = "DASHBOARD-PLAN.md"
MOCK_FILENAME = "mock.html"
VERSION_DIR = "mock-version"
DESIGN_TOKENS_FILENAME = "DESIGN-TOKENS.md"

#: Required reads that gate this step, and their producer steps (CONTRACT.md §4.1).
PLAN_STEP = "plan"
DATA_STEP = "data"
#: The sample CSV gate is satisfied by production data/ OR the scaffold/ demo (§3.1).
CSV_GLOBS: tuple[str, ...] = ("data/*.csv", "scaffold/sample-data/*.csv")

#: This step.
MOCK_STEP = "mock"

#: The 8 step names in canonical order (mirror of CONTRACT.md §1). Used to decide
#: which steps are "downstream of mock" for staleness propagation (§4.2).
STEP_ORDER: tuple[str, ...] = (
    "init", "intake", "data", "brand", "plan", "mock", "spec", "build",
)

#: Statuses that satisfy the ordering gate - the producer step is "resolved" (§4.1).
RESOLVED_STATUSES = frozenset({"approved", "skipped"})


# --- STATE.md reading (shared shape with plan.py / route.py) -----------------

def parse_statuses(text: str) -> dict[str, str]:
    """Parse the per-step statuses out of a STATE.md manifest.

    Parsing is tolerant (matching the router's parser): only genuine
    ``| order | step | skill | status |`` rows whose step name is known contribute;
    header, separator, and stray rows are ignored.

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


# Matches the ``- current_version: v_3`` metadata line, capturing prefix/value/suffix.
_CURRENT_VERSION_LINE = re.compile(
    r"^(\s*-\s*current_version\s*:\s*)(\S+)(.*)$", re.MULTILINE
)


def read_current_version(text: str) -> str:
    """Read the ``current_version`` metadata value from STATE.md.

    Args:
        text: The full contents of a ``STATE.md`` file.

    Returns:
        The recorded version (e.g. ``"v_2"``), or ``"v_1"`` if no line is present.
    """
    match = _CURRENT_VERSION_LINE.search(text)
    return match.group(2) if match else "v_1"


def bump_version(version: str) -> str:
    """Return the next version directory name after ``version``.

    ``v_3`` -> ``v_4``. A malformed value falls back to ``v_2`` (one past the implicit
    ``v_1``) so a hand-broken manifest still advances rather than crashing.

    Args:
        version: The current version string (e.g. ``"v_3"``).

    Returns:
        The bumped version string (e.g. ``"v_4"``).
    """
    match = re.match(r"^v_(\d+)$", version.strip())
    return f"v_{int(match.group(1)) + 1}" if match else "v_2"


def set_current_version(text: str, version: str) -> str:
    """Rewrite the ``- current_version: ...`` metadata line in a STATE.md manifest.

    Only the value is replaced; any trailing inline comment (e.g. ``# v_1, v_2, ...``)
    and every other line are preserved. If no line exists the text is returned
    unchanged (older manifests stay valid). Mirrors ``state.set_data_mode``.

    Args:
        text: The full ``STATE.md`` contents.
        version: The new version to record (e.g. ``"v_2"``).

    Returns:
        The updated ``STATE.md`` contents.
    """
    return _CURRENT_VERSION_LINE.sub(
        lambda m: f"{m.group(1)}{version}{m.group(3)}", text, count=1
    )


def target_version(current_version: str, mock_status: str) -> str:
    """Decide which ``v_N`` the next mock.html should be written into (CONTRACT.md §4.3).

    Re-running a deliverable **after its step was approved** bumps to a fresh version,
    preserving the prior copy; re-running before approval overwrites the current one.

    Args:
        current_version: The ``current_version`` recorded in STATE.md.
        mock_status: The mock step's current status.

    Returns:
        The version directory name to write the mock into.
    """
    return bump_version(current_version) if mock_status == "approved" else current_version


# --- STATE.md rewriting (shared shape with plan.py / state.py) ---------------

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

    Only rows inside the ``## Steps`` table whose step name is a key in ``updates`` are
    touched; every other line (metadata, prose, untouched rows) is preserved
    byte-for-byte. The trailing newline of the input is preserved.

    Args:
        text: The full ``STATE.md`` contents.
        updates: ``{step_name: new_status}`` for the rows to rewrite (lower-cased).

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

    Every step ordered after ``mock`` that is currently ``approved`` becomes ``stale``;
    steps already ``pending`` / ``skipped`` / ``stale`` are left as-is.

    Args:
        statuses: The current ``{step_name: status}`` mapping.

    Returns:
        ``{step_name: "stale"}`` for each downstream step that was ``approved``.
    """
    mock_index = STEP_ORDER.index(MOCK_STEP)
    return {
        step: "stale"
        for step in STEP_ORDER[mock_index + 1:]
        if statuses.get(step) == "approved"
    }


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def _has_csv(project_root: Path) -> bool:
    """Return True if any sample CSV exists in data/ or the scaffold/ demo (§3.1)."""
    return any(next(project_root.glob(pattern), None) is not None for pattern in CSV_GLOBS)


def entry_gate_blocker(project_root: Path) -> Optional[str]:
    """Return why mock may not run yet, or ``None`` if it may.

    Mock refuses to run unless ``STATE.md`` exists and, for each required read
    (CONTRACT.md §4.1): ``plan`` is resolved and ``DASHBOARD-PLAN.md`` exists, and
    ``data`` is resolved and at least one sample CSV is present (production ``data/``
    or the ``scaffold/`` demo).

    Args:
        project_root: The analyst's project directory.

    Returns:
        A human-readable blocker message, or ``None`` when the gate is open.
    """
    state_path = project_root / STATE_FILENAME
    if not state_path.exists():
        return (
            "No STATE.md found. Run 'tableau-init' first to scaffold the project "
            "and initialize STATE.md before running 'tableau-mock'."
        )

    statuses = parse_statuses(state_path.read_text(encoding="utf-8-sig"))

    plan_status = statuses.get(PLAN_STEP, "pending")
    if plan_status not in RESOLVED_STATUSES:
        return (
            f"Step 'plan' is '{plan_status}', not resolved. Run 'tableau-plan' first; "
            f"'tableau-mock' builds directly from '{PLAN_FILENAME}'."
        )
    if not (project_root / PLAN_FILENAME).exists():
        return (
            f"'{PLAN_FILENAME}' is missing on disk even though step 'plan' is "
            f"'{plan_status}'. Re-run 'tableau-plan' to regenerate it."
        )

    data_status = statuses.get(DATA_STEP, "pending")
    if data_status not in RESOLVED_STATUSES:
        return (
            f"Step 'data' is '{data_status}', not resolved. Run 'tableau-data' first; "
            f"'tableau-mock' populates the demo from the sample CSVs."
        )
    if not _has_csv(project_root):
        return (
            "No sample CSV found in 'data/' or 'scaffold/sample-data/' even though step "
            "'data' is resolved. Re-run 'tableau-data' to regenerate the samples."
        )
    return None


# --- precheck ----------------------------------------------------------------

@dataclass(frozen=True)
class PrecheckResult:
    """The state mock needs before authoring a mock.html.

    Attributes:
        can_run: True when the entry gate is open (plan + data resolved, artifacts present).
        blocker: Why mock cannot run, when ``can_run`` is False; else ``None``.
        target_version: The ``v_N`` directory the mock.html should be written into.
        target_path: ``mock-version/<target_version>/mock.html`` (where to author).
        is_rerun_after_approval: True when this run bumps the version (mock was approved).
        mock_exists: Whether the target mock.html already exists (refine vs author fresh).
        tokens_present: Whether ``DESIGN-TOKENS.md`` exists (else neutral styling).
        canvas: The planned ``(width, height)`` to render at, or ``None`` if unparseable.
        element_count, filter_count, interaction_count: Sizes of the coverage sets.
    """

    can_run: bool
    blocker: Optional[str]
    target_version: str
    target_path: str
    is_rerun_after_approval: bool
    mock_exists: bool
    tokens_present: bool
    canvas: Optional[tuple[int, int]]
    element_count: int
    filter_count: int
    interaction_count: int


def precheck(project_dir: Path | str) -> PrecheckResult:
    """Report whether mock may run, where to write it, and what it must cover.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`PrecheckResult`. When the entry gate is closed, ``can_run`` is False
        and ``blocker`` explains why; the other fields are placeholders.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return PrecheckResult(
            False, blocker, "v_1", "", False, False, False, None, 0, 0, 0
        )

    text = (project_root / STATE_FILENAME).read_text(encoding="utf-8-sig")
    mock_status = parse_statuses(text).get(MOCK_STEP, "pending")
    version = target_version(read_current_version(text), mock_status)
    target_rel = f"{VERSION_DIR}/{version}/{MOCK_FILENAME}"

    spec = parse_plan_coverage(
        (project_root / PLAN_FILENAME).read_text(encoding="utf-8-sig")
    )
    return PrecheckResult(
        can_run=True,
        blocker=None,
        target_version=version,
        target_path=target_rel,
        is_rerun_after_approval=(mock_status == "approved"),
        mock_exists=(project_root / target_rel).exists(),
        tokens_present=(project_root / DESIGN_TOKENS_FILENAME).exists(),
        canvas=spec.canvas,
        element_count=len(spec.element_ids),
        filter_count=len(spec.filter_ids),
        interaction_count=len(spec.interaction_ids),
    )


# --- commit ------------------------------------------------------------------

@dataclass
class CommitResult:
    """Outcome of committing the mock step's result to STATE.md.

    Attributes:
        ok: True when STATE.md was updated; False when the commit was refused.
        message: Human-readable explanation (the refusal reason when not ``ok``).
        version: The version directory the (attempted) mock lives in.
        staled_steps: Downstream steps flipped to ``stale`` by this commit.
        validation: The coverage/guard result that gated the commit, when one ran.
    """

    ok: bool
    message: str
    version: str = "v_1"
    staled_steps: list[str] = field(default_factory=list)
    validation: Optional[MockValidation] = None


def commit(project_dir: Path | str) -> CommitResult:
    """Validate the target mock.html and approve the mock step, propagating staleness.

    The mock is non-skippable, so commit only ever sets ``approved``. The target
    ``mock-version/<v_N>/mock.html`` must exist and pass :func:`coverage.validate_mock`
    (full coverage + slot-sizing guard) or the commit is refused. On success
    ``current_version`` is set to the target version (a bump on a re-run after approval,
    CONTRACT.md §4.3), ``mock`` is approved, and every downstream ``approved`` step
    (``spec``/``build``) is flipped to ``stale`` (§4.2).

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`CommitResult`. ``ok`` is False (STATE.md untouched) when the entry
        gate is closed or the mock is missing / has coverage gaps / fails the guard.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return CommitResult(False, blocker)

    text = (project_root / STATE_FILENAME).read_text(encoding="utf-8-sig")
    statuses = parse_statuses(text)
    mock_status = statuses.get(MOCK_STEP, "pending")
    version = target_version(read_current_version(text), mock_status)
    mock_path = project_root / VERSION_DIR / version / MOCK_FILENAME

    if not mock_path.exists():
        return CommitResult(
            False,
            f"Cannot approve mock: '{VERSION_DIR}/{version}/{MOCK_FILENAME}' does not "
            f"exist. Author it first (render the plan at its screen size).",
            version=version,
        )

    plan_text = (project_root / PLAN_FILENAME).read_text(encoding="utf-8-sig")
    validation = validate_mock(plan_text, mock_path.read_text(encoding="utf-8-sig"))
    if not validation.ok:
        reasons: list[str] = []
        if validation.gaps:
            reasons.append(
                "coverage gaps: "
                + ", ".join(f"{item.kind} '{item.label}'" for item in validation.gaps)
            )
        if validation.missing_boxes:
            reasons.append("no layout box for: " + ", ".join(validation.missing_boxes))
        if validation.guard_violations:
            reasons.append("; ".join(validation.guard_violations))
        return CommitResult(
            False,
            f"'{MOCK_FILENAME}' does not fully cover the plan - {'; '.join(reasons)}. "
            f"Fix and re-run commit.",
            version=version,
            validation=validation,
        )

    stale_updates = _downstream_stale_updates(statuses)
    updated = apply_status_updates(text, {MOCK_STEP: "approved", **stale_updates})
    updated = set_current_version(updated, version)
    (project_root / STATE_FILENAME).write_text(updated, encoding="utf-8")

    staled_steps = sorted(stale_updates, key=STEP_ORDER.index)
    logger.info(
        f"Set mock -> approved at {version}; marked stale: {staled_steps or 'none'}."
    )
    return CommitResult(
        ok=True,
        message=f"mock -> approved ({version})",
        version=version,
        staled_steps=staled_steps,
        validation=validation,
    )


# --- CLI ---------------------------------------------------------------------

def format_precheck(result: PrecheckResult) -> str:
    """Render a :class:`PrecheckResult` as a human-readable block.

    Args:
        result: The precheck result to render.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    # Plain ASCII only: this prints to a Windows cp1252 console, which would raise
    # UnicodeEncodeError on emoji / box-drawing glyphs.
    if not result.can_run:
        return f"[BLOCKED] tableau-mock cannot run.\n{result.blocker}"

    canvas = (
        f"{result.canvas[0]}x{result.canvas[1]}px" if result.canvas
        else "UNKNOWN (plan has no parseable Screen Size)"
    )
    lines = [
        "[MOCK] precheck OK - tableau-mock can run.",
        f"  render at      : {canvas} (from DASHBOARD-PLAN.md Screen Size).",
        f"  must cover     : {result.element_count} element(s), "
        f"{result.filter_count} filter(s), {result.interaction_count} interaction(s).",
        f"  write mock to  : {result.target_path}",
    ]
    if result.is_rerun_after_approval:
        lines.append(
            f"    -> re-run after approval: version bumped to {result.target_version} "
            f"(prior version preserved; CONTRACT.md §4.3)."
        )
    if result.mock_exists:
        lines.append("    -> a mock.html already exists at this version; refine it in place.")
    lines.append(
        f"  DESIGN-TOKENS.md: {'present' if result.tokens_present else 'absent - neutral styling'}"
        " (optional read)."
    )
    lines.append(
        f"  tag every rendered KPI/chart/filter/interaction with data-plan-id=\"<id>\" "
        f"and embed the '{LAYOUT_MANIFEST_ID}' JSON layout manifest, or coverage fails."
    )
    return "\n".join(lines)


def format_validation(validation: MockValidation) -> str:
    """Render a :class:`MockValidation` as the coverage checklist + guard report.

    Args:
        validation: The validation result to render.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    # Plain ASCII only (see format_precheck): [x]/[ ] checkboxes, no Unicode ticks.
    lines = ["Coverage checklist (plan -> mock):"]
    for item in validation.coverage:
        mark = "x" if item.rendered else " "
        gap = "" if item.rendered else "   <- MISSING from mock"
        lines.append(f"  [{mark}] {item.kind}: {item.label}{gap}")
    for missing in validation.missing_boxes:
        lines.append(
            f"  [ ] element '{missing}': no geometry box in layout manifest   <- MISSING"
        )

    lines.append("Slot-sizing guard:")
    if validation.guard_violations:
        for violation in validation.guard_violations:
            lines.append(f"  [ ] {violation}")
    else:
        lines.append("  [x] all element boxes in-bounds, readable, and the canvas is well-filled.")

    for note in validation.notes:
        lines.append(f"  note: {note}")

    lines.append(
        "[OK] mock.html fully covers the plan." if validation.ok
        else "[INVALID] mock.html does not fully cover the plan - fix the items above and re-run."
    )
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
        text = f"[REFUSED] {result.message}"
        if result.validation is not None:
            text += "\n" + format_validation(result.validation)
        return text

    lines = [f"[MOCK] {result.message}."]
    if result.staled_steps:
        lines.append(
            f"  downstream marked stale: {', '.join(result.staled_steps)} "
            f"(re-run these in order)."
        )
    lines.append(f"  deliverable: {VERSION_DIR}/{result.version}/{MOCK_FILENAME}")
    lines.append(
        "  next: open a fresh conversation and run 'tableau-spec' (or 'tableau-route')."
    )
    return "\n".join(lines)


def _target_mock_path(project_dir: Path) -> Path:
    """Resolve the mock.html path for the target version (for the validate subcommand).

    Args:
        project_dir: The analyst's project directory.

    Returns:
        The path to the mock.html the next commit would validate.
    """
    state_path = project_dir / STATE_FILENAME
    if not state_path.exists():
        return project_dir / VERSION_DIR / "v_1" / MOCK_FILENAME
    text = state_path.read_text(encoding="utf-8-sig")
    mock_status = parse_statuses(text).get(MOCK_STEP, "pending")
    version = target_version(read_current_version(text), mock_status)
    return project_dir / VERSION_DIR / version / MOCK_FILENAME


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point: run ``precheck``, ``validate``, or ``commit`` and print it.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``2`` when mock is blocked/refused/invalid
        or on a usage error.
    """
    parser = argparse.ArgumentParser(
        description="Gate, validate, and commit the tableau-mock step.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("precheck", "Report whether mock may run, where to write it, and what to cover."),
        ("validate", "Coverage checklist + slot-sizing guard on the target mock.html."),
        ("commit", "Approve the mock in STATE.md (versioned) and propagate staleness."),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument(
            "project_dir", nargs="?", default=".", help="Project directory (default: cwd)."
        )

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    project_dir = Path(args.project_dir)

    if args.command == "precheck":
        precheck_result = precheck(project_dir)
        print(format_precheck(precheck_result))
        return 0 if precheck_result.can_run else 2

    if args.command == "validate":
        mock_path = _target_mock_path(project_dir)
        plan_path = project_dir / PLAN_FILENAME
        if not mock_path.exists() or not plan_path.exists():
            missing = MOCK_FILENAME if not mock_path.exists() else PLAN_FILENAME
            print(f"[INVALID] '{missing}' not found (expected mock at '{mock_path}').")
            return 2
        validation = validate_mock(
            plan_path.read_text(encoding="utf-8-sig"),
            mock_path.read_text(encoding="utf-8-sig"),
        )
        print(format_validation(validation))
        return 0 if validation.ok else 2

    commit_result = commit(project_dir)
    print(format_commit(commit_result))
    return 0 if commit_result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
