"""Gate and commit the ``spec`` step of the tableau-dashboard-plugin.

This is the orchestration core of the ``tableau-spec`` skill (CONTRACT.md step 7): the
non-skippable step that turns the approved ``mock.html`` into an ``IMPLEMENTATION-SPEC.md``
mapping every mock element to a Tableau construct, so ``tableau-build`` never has to guess.
The spec *prose* is authored by the model; the coverage-reconciliation and simplest-
primitive guard live in :mod:`reconcile`. This module owns the parts that touch
``STATE.md`` and the filesystem:

1. **Entry gate** - spec refuses to run until both required reads are present and their
   producers resolved (CONTRACT.md §4.1): ``DASHBOARD-PLAN.md`` (from ``plan``) - the
   blueprint whose ids the mock/spec share - and ``mock.html`` at ``current_version`` (from
   ``mock``) - the elements the spec must map.
2. **Versioning** - the spec is a *deliverable* written under ``mock-version/v_N/``
   alongside the mock it was specced from (CONTRACT.md §4.3). Per §4.3, **only the leading
   deliverable (``mock``) bumps ``current_version``**; ``spec`` writes into the mock's
   current version and overwrites in place on a re-run. A new spec *version* is created by
   re-running the mock (which bumps and stales spec), so spec never touches ``current_version``.
3. **STATE.md transition** - committing flips ``spec`` to ``approved`` and propagates
   staleness to ``build`` (§4.2), so the workbook can never silently disagree with a
   re-specced mock.

The module is pure and stdlib-only (it does **not** import the router) so the contract
test can call its functions directly, exactly like ``mock.py`` / ``tableau-data``'s
``state.py``. The CLI exposes ``precheck`` (before authoring - reports the version dir + the
elements to map), ``validate`` (the reconciliation checklist + guard on a draft), and
``commit`` (after approval). Program output goes to stdout; diagnostics through ``logging``.

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

from reconcile import SpecValidation, mock_element_ids, reconcile

logger = logging.getLogger(__name__)

# --- Canonical constants (mirror of CONTRACT.md §1 / §3 / §4.3) --------------

STATE_FILENAME = "STATE.md"
PLAN_FILENAME = "DASHBOARD-PLAN.md"
MOCK_FILENAME = "mock.html"
SPEC_FILENAME = "IMPLEMENTATION-SPEC.md"
VERSION_DIR = "mock-version"

#: Required reads that gate this step, and their producer steps (CONTRACT.md §4.1).
PLAN_STEP = "plan"
MOCK_STEP = "mock"

#: This step.
SPEC_STEP = "spec"

#: The 8 step names in canonical order (mirror of CONTRACT.md §1). Used to decide which
#: steps are "downstream of spec" for staleness propagation (§4.2).
STEP_ORDER: tuple[str, ...] = (
    "init", "intake", "data", "brand", "plan", "mock", "spec", "build",
)

#: Statuses that satisfy the ordering gate - the producer step is "resolved" (§4.1).
RESOLVED_STATUSES = frozenset({"approved", "skipped"})


# --- STATE.md reading (shared shape with mock.py / route.py) -----------------

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


# Matches the ``- current_version: v_3`` metadata line (value read-only; spec never
# rewrites it - only the leading deliverable ``mock`` bumps it, CONTRACT.md §4.3).
_CURRENT_VERSION_LINE = re.compile(
    r"^\s*-\s*current_version\s*:\s*(\S+)", re.MULTILINE
)


def read_current_version(text: str) -> str:
    """Read the ``current_version`` metadata value from STATE.md.

    This is the version directory where the approved ``mock.html`` lives and where spec
    writes its ``IMPLEMENTATION-SPEC.md`` alongside it.

    Args:
        text: The full contents of a ``STATE.md`` file.

    Returns:
        The recorded version (e.g. ``"v_2"``), or ``"v_1"`` if no line is present.
    """
    match = _CURRENT_VERSION_LINE.search(text)
    return match.group(1) if match else "v_1"


# --- STATE.md rewriting (shared shape with mock.py / state.py) ---------------

def _format_step_row(cells: list[str]) -> str:
    """Render a Steps-table row with the canonical column widths (matches init/mock)."""
    order, step, skill, status = cells[0], cells[1], cells[2], cells[3]
    return f"| {order:<5} | {step:<6} | {skill:<14} | {status:<8} |"


def apply_status_updates(text: str, updates: dict[str, str]) -> str:
    """Rewrite the status cell of one or more Steps-table rows.

    Only rows inside the ``## Steps`` table whose step name is a key in ``updates`` are
    touched; every other line is preserved byte-for-byte. The trailing newline is kept.

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

    Every step ordered after ``spec`` that is currently ``approved`` becomes ``stale``
    (i.e. ``build``); steps already ``pending`` / ``skipped`` / ``stale`` are left as-is.

    Args:
        statuses: The current ``{step_name: status}`` mapping.

    Returns:
        ``{step_name: "stale"}`` for each downstream step that was ``approved``.
    """
    spec_index = STEP_ORDER.index(SPEC_STEP)
    return {
        step: "stale"
        for step in STEP_ORDER[spec_index + 1:]
        if statuses.get(step) == "approved"
    }


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def entry_gate_blocker(project_root: Path) -> Optional[str]:
    """Return why spec may not run yet, or ``None`` if it may.

    Spec refuses to run unless ``STATE.md`` exists and, for each required read
    (CONTRACT.md §4.1): ``plan`` is resolved and ``DASHBOARD-PLAN.md`` exists, and
    ``mock`` is resolved and ``mock.html`` exists at ``current_version``.

    Args:
        project_root: The analyst's project directory.

    Returns:
        A human-readable blocker message, or ``None`` when the gate is open.
    """
    state_path = project_root / STATE_FILENAME
    if not state_path.exists():
        return (
            "No STATE.md found. Run 'tableau-init' first to scaffold the project "
            "and initialize STATE.md before running 'tableau-spec'."
        )

    text = state_path.read_text(encoding="utf-8-sig")
    statuses = parse_statuses(text)

    plan_status = statuses.get(PLAN_STEP, "pending")
    if plan_status not in RESOLVED_STATUSES:
        return (
            f"Step 'plan' is '{plan_status}', not resolved. Run 'tableau-plan' first; "
            f"'tableau-spec' shares the plan's element ids."
        )
    if not (project_root / PLAN_FILENAME).exists():
        return (
            f"'{PLAN_FILENAME}' is missing on disk even though step 'plan' is "
            f"'{plan_status}'. Re-run 'tableau-plan' to regenerate it."
        )

    mock_status = statuses.get(MOCK_STEP, "pending")
    if mock_status not in RESOLVED_STATUSES:
        return (
            f"Step 'mock' is '{mock_status}', not resolved. Run 'tableau-mock' first; "
            f"'tableau-spec' maps every element the mock rendered to a Tableau construct."
        )
    version = read_current_version(text)
    mock_path = project_root / VERSION_DIR / version / MOCK_FILENAME
    if not mock_path.exists():
        return (
            f"'{VERSION_DIR}/{version}/{MOCK_FILENAME}' is missing on disk even though "
            f"step 'mock' is '{mock_status}'. Re-run 'tableau-mock' to regenerate it."
        )
    return None


# --- precheck ----------------------------------------------------------------

@dataclass(frozen=True)
class PrecheckResult:
    """The state spec needs before authoring an IMPLEMENTATION-SPEC.md.

    Attributes:
        can_run: True when the entry gate is open (plan + mock resolved, artifacts present).
        blocker: Why spec cannot run, when ``can_run`` is False; else ``None``.
        version: The ``current_version`` dir holding the mock, and where the spec is written.
        mock_path: ``mock-version/<version>/mock.html`` (the elements to map).
        target_path: ``mock-version/<version>/IMPLEMENTATION-SPEC.md`` (where to author).
        spec_exists: Whether the IMPLEMENTATION-SPEC.md already exists (refine vs author fresh).
        element_ids: The mock element ids that must each be mapped to a construct.
    """

    can_run: bool
    blocker: Optional[str]
    version: str
    mock_path: str
    target_path: str
    spec_exists: bool
    element_ids: list[str]


def precheck(project_dir: Path | str) -> PrecheckResult:
    """Report whether spec may run, where to write it, and what it must map.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`PrecheckResult`. When the entry gate is closed, ``can_run`` is False
        and ``blocker`` explains why; the other fields are placeholders.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return PrecheckResult(False, blocker, "v_1", "", "", False, [])

    text = (project_root / STATE_FILENAME).read_text(encoding="utf-8-sig")
    version = read_current_version(text)

    mock_rel = f"{VERSION_DIR}/{version}/{MOCK_FILENAME}"
    target_rel = f"{VERSION_DIR}/{version}/{SPEC_FILENAME}"
    element_ids = mock_element_ids(
        (project_root / mock_rel).read_text(encoding="utf-8-sig")
    )
    return PrecheckResult(
        can_run=True,
        blocker=None,
        version=version,
        mock_path=mock_rel,
        target_path=target_rel,
        spec_exists=(project_root / target_rel).exists(),
        element_ids=element_ids,
    )


# --- commit ------------------------------------------------------------------

@dataclass
class CommitResult:
    """Outcome of committing the spec step's result to STATE.md.

    Attributes:
        ok: True when STATE.md was updated; False when the commit was refused.
        message: Human-readable explanation (the refusal reason when not ``ok``).
        version: The version directory the (attempted) spec lives in.
        staled_steps: Downstream steps flipped to ``stale`` by this commit.
        validation: The reconciliation/guard result that gated the commit, when one ran.
    """

    ok: bool
    message: str
    version: str = "v_1"
    staled_steps: list[str] = field(default_factory=list)
    validation: Optional[SpecValidation] = None


def commit(project_dir: Path | str) -> CommitResult:
    """Reconcile the IMPLEMENTATION-SPEC.md and approve the spec step.

    The spec is non-skippable, so commit only ever sets ``approved``. The
    ``mock-version/<current_version>/IMPLEMENTATION-SPEC.md`` must exist and pass
    :func:`reconcile.reconcile` (every mock element mapped, every advanced-feature
    escalation justified, and a consistent Layout container tree) or the commit is
    refused. On success ``spec`` is approved and
    every downstream ``approved`` step (``build``) is flipped to ``stale`` (§4.2). Spec
    writes into the mock's ``current_version`` and never bumps it - only the leading
    deliverable (``mock``) does (§4.3), so a re-run overwrites the spec in place.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`CommitResult`. ``ok`` is False (STATE.md untouched) when the entry gate
        is closed or the spec is missing / leaves an element unmapped / has an unjustified
        advanced-feature escalation.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return CommitResult(False, blocker)

    text = (project_root / STATE_FILENAME).read_text(encoding="utf-8-sig")
    statuses = parse_statuses(text)
    version = read_current_version(text)

    mock_path = project_root / VERSION_DIR / version / MOCK_FILENAME
    spec_path = project_root / VERSION_DIR / version / SPEC_FILENAME

    if not spec_path.exists():
        return CommitResult(
            False,
            f"Cannot approve spec: '{VERSION_DIR}/{version}/{SPEC_FILENAME}' does not "
            f"exist. Author it first (map every mock element to a Tableau construct).",
            version=version,
        )

    validation = reconcile(
        mock_path.read_text(encoding="utf-8-sig"),
        spec_path.read_text(encoding="utf-8-sig"),
    )
    if not validation.ok:
        reasons: list[str] = []
        if validation.unmapped:
            reasons.append("unmapped mock element(s): " + ", ".join(validation.unmapped))
        if validation.unjustified:
            reasons.append(
                "unjustified advanced feature(s): "
                + ", ".join(
                    f"{item.id} ({'/'.join(item.advanced_features)})"
                    for item in validation.unjustified
                )
            )
        if validation.layout_errors:
            reasons.append("layout problem(s): " + "; ".join(validation.layout_errors))
        return CommitResult(
            False,
            f"'{SPEC_FILENAME}' does not fully reconcile with the mock - "
            f"{'; '.join(reasons)}. Fix and re-run commit.",
            version=version,
            validation=validation,
        )

    stale_updates = _downstream_stale_updates(statuses)
    updated = apply_status_updates(text, {SPEC_STEP: "approved", **stale_updates})
    (project_root / STATE_FILENAME).write_text(updated, encoding="utf-8")

    staled_steps = sorted(stale_updates, key=STEP_ORDER.index)
    logger.info(
        f"Set spec -> approved at {version}; marked stale: {staled_steps or 'none'}."
    )
    return CommitResult(
        ok=True,
        message=f"spec -> approved ({version})",
        version=version,
        staled_steps=staled_steps,
        validation=validation,
    )


# --- CLI ---------------------------------------------------------------------

def format_precheck(result: PrecheckResult) -> str:
    """Render a :class:`PrecheckResult` as a human-readable, plain-ASCII block."""
    # Plain ASCII only: this prints to a Windows cp1252 console, which would raise
    # UnicodeEncodeError on emoji / box-drawing glyphs.
    if not result.can_run:
        return f"[BLOCKED] tableau-spec cannot run.\n{result.blocker}"

    lines = [
        "[SPEC] precheck OK - tableau-spec can run.",
        f"  map from       : {result.mock_path}",
        f"  must map       : {len(result.element_ids)} mock element(s) -> "
        f"a Tableau construct each, nothing unmapped.",
        f"  elements       : {', '.join(result.element_ids) or '(none)'}",
        f"  write spec to  : {result.target_path}",
    ]
    if result.spec_exists:
        lines.append(
            "    -> an IMPLEMENTATION-SPEC.md already exists at this version; refine it in place."
        )
    lines.append(
        "  default each element to the SIMPLEST sufficient Tableau primitive; justify any "
        "escalation to DZV / LOD / table calc / parameter action against the simpler option."
    )
    lines.append(
        "  the spec must also carry a '## Layout' fenced-JSON container tree derived from "
        "the mock's geometry (canvas + nested vert/horz + % sizes; see the template)."
    )
    return "\n".join(lines)


def format_validation(validation: SpecValidation) -> str:
    """Render a :class:`SpecValidation` as the reconciliation checklist + guard report."""
    # Plain ASCII only (see format_precheck): [x]/[ ] checkboxes, no Unicode ticks.
    lines = ["Coverage reconciliation (mock element -> Tableau construct):"]
    for item in validation.items:
        if not item.mapped:
            lines.append(f"  [ ] {item.id}: UNMAPPED   <- no mapping row in the spec")
            continue
        if item.advanced_features and not item.justified:
            lines.append(
                f"  [ ] {item.id}: {item.construct}   <- uses "
                f"{'/'.join(item.advanced_features)} with NO justification"
            )
            continue
        note = (
            f" (advanced: {'/'.join(item.advanced_features)}, justified)"
            if item.advanced_features else ""
        )
        lines.append(f"  [x] {item.id}: {item.construct}{note}")

    lines.append("Layout container tree:")
    if validation.layout_errors:
        lines.extend(f"  [ ] {error}" for error in validation.layout_errors)
    else:
        lines.append("  [x] present and consistent with the Element Mapping")

    for note in validation.notes:
        lines.append(f"  note: {note}")

    lines.append(
        "[OK] every mock element maps to a construct; escalations are justified; "
        "the layout tree is consistent."
        if validation.ok
        else "[INVALID] spec does not fully reconcile with the mock - fix the items above and re-run."
    )
    return "\n".join(lines)


def format_commit(result: CommitResult) -> str:
    """Render a :class:`CommitResult` as a human-readable, plain-ASCII block."""
    # Plain ASCII only (see format_precheck).
    if not result.ok:
        text = f"[REFUSED] {result.message}"
        if result.validation is not None:
            text += "\n" + format_validation(result.validation)
        return text

    lines = [f"[SPEC] {result.message}."]
    if result.staled_steps:
        lines.append(
            f"  downstream marked stale: {', '.join(result.staled_steps)} "
            f"(re-run these in order)."
        )
    lines.append(f"  deliverable: {VERSION_DIR}/{result.version}/{SPEC_FILENAME}")
    lines.append(
        "  next: open a fresh conversation and run 'tableau-build' (or 'tableau-route')."
    )
    return "\n".join(lines)


def _resolve_paths(project_dir: Path) -> tuple[Path, Path]:
    """Resolve (mock.html, IMPLEMENTATION-SPEC.md) paths for the validate subcommand.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        ``(mock_path, spec_path)`` the next commit would reconcile (both at current_version).
    """
    state_path = project_dir / STATE_FILENAME
    version = (
        read_current_version(state_path.read_text(encoding="utf-8-sig"))
        if state_path.exists() else "v_1"
    )
    base = project_dir / VERSION_DIR / version
    return base / MOCK_FILENAME, base / SPEC_FILENAME


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point: run ``precheck``, ``validate``, or ``commit`` and print it.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``2`` when spec is blocked/refused/invalid
        or on a usage error.
    """
    parser = argparse.ArgumentParser(
        description="Gate, validate, and commit the tableau-spec step.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("precheck", "Report whether spec may run, where to write it, and what to map."),
        ("validate", "Coverage reconciliation + simplest-primitive guard on the draft spec."),
        ("commit", "Approve the spec in STATE.md and propagate staleness."),
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
        mock_path, spec_path = _resolve_paths(project_dir)
        if not mock_path.exists() or not spec_path.exists():
            missing = MOCK_FILENAME if not mock_path.exists() else SPEC_FILENAME
            print(f"[INVALID] '{missing}' not found (expected spec at '{spec_path}').")
            return 2
        validation = reconcile(
            mock_path.read_text(encoding="utf-8-sig"),
            spec_path.read_text(encoding="utf-8-sig"),
        )
        print(format_validation(validation))
        return 0 if validation.ok else 2

    commit_result = commit(project_dir)
    print(format_commit(commit_result))
    return 0 if commit_result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
