"""Gate, validate, and commit the ``plan`` step of the tableau-dashboard-plugin.

This is the executable core of the ``tableau-plan`` skill (CONTRACT.md step 5): the
non-skippable step that turns the request + data model into a strict, complete
``DASHBOARD-PLAN.md``. The plan *content* is authored by the model; this script owns
the parts that must be **mechanically guaranteed** rather than left to model prose:

1. **Entry gate** - plan refuses to run until its required read ``DATA-MODEL.md`` is
   present and its producer step ``data`` is resolved (CONTRACT.md §4.1). Screen size,
   layout, and every element are decided against real field names, so the data model
   must exist first.
2. **Plan schema** - on approval the produced ``DASHBOARD-PLAN.md`` must contain every
   **required** section (screen size, layout grid, elements, filters, interactions) so
   ``tableau-mock`` can never silently drop a requirement. The validator also enforces
   that every KPI / chart / filter / action carries a **stable, unique id** and that
   each interaction uses a term from the shared vocabulary (CONTRACT.md §6).
3. **STATE.md transition** - committing flips ``plan`` to ``approved`` and propagates
   staleness (CONTRACT.md §4.2): re-running flips every downstream ``approved`` step
   (``mock`` / ``spec`` / ``build``) to ``stale`` so the demo and workbook can never
   silently disagree with a changed plan.

The module is intentionally pure and stdlib-only (it does **not** import the router)
so the contract test can call its functions directly, exactly like ``init.py`` /
``intake.py`` / ``route.py``. The CLI exposes three subcommands the skill runs at three
moments - ``precheck`` (before authoring), ``validate`` (to self-check a draft), and
``commit`` (after approval). What the CLI prints to stdout is the program's *output*;
diagnostics go through ``logging``.

Keep ``STEP_ORDER`` and ``INTERACTION_VOCABULARY`` below in lock-step with CONTRACT.md
§1 and §6 respectively.
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

# --- Canonical constants (mirror of CONTRACT.md §1 / §3 / §6) ----------------

STATE_FILENAME = "STATE.md"
PLAN_FILENAME = "DASHBOARD-PLAN.md"

#: The required read that gates this step, and its producer step (CONTRACT.md §4.1).
DATA_MODEL_FILENAME = "DATA-MODEL.md"
DATA_STEP = "data"

#: Optional reads that enrich the plan but never block it (CONTRACT.md §1). PRD.md is
#: preferred; the raw request (production, then the scaffold/ demo) is the fallback.
PRD_FILENAME = "PRD.md"
REQUEST_FILENAME = "DASHBOARD-REQUEST.md"
SCAFFOLD_REQUEST = "scaffold/EXAMPLE-DASHBOARD-REQUEST.md"
DESIGN_TOKENS_FILENAME = "DESIGN-TOKENS.md"

#: This step.
PLAN_STEP = "plan"

#: The 8 step names in canonical order (mirror of CONTRACT.md §1). Used to decide
#: which steps are "downstream of plan" for staleness propagation (§4.2).
STEP_ORDER: tuple[str, ...] = (
    "init", "intake", "data", "brand", "plan", "mock", "spec", "build",
)

#: Statuses that satisfy the ordering gate - the producer step is "resolved" (§4.1).
RESOLVED_STATUSES = frozenset({"approved", "skipped"})

#: Plan sections that MUST be present, or the plan is rejected and the mock is blocked.
#: Screen size is decided here (not in mock) so slot sizing is correct downstream.
PLAN_REQUIRED_SECTIONS: tuple[str, ...] = (
    "Screen Size", "Layout Grid", "Elements", "Filters", "Interactions",
)

#: Recommended sections - proposed while planning, reported when absent, never blocking.
#: "Suggestions" is where the skill proposes KPIs/patterns beyond the literal request.
PLAN_RECOMMENDED_SECTIONS: tuple[str, ...] = ("Summary", "Suggestions")

#: The shared interactions vocabulary (CONTRACT.md §6). The Interactions table's
#: ``interaction`` column must use one of these intent-level terms so plan -> mock ->
#: spec stay aligned.
INTERACTION_VOCABULARY: tuple[str, ...] = (
    "toggle panel", "swap view", "drill", "cross-filter", "highlight", "parameter swap",
)
_INTERACTION_TERMS = frozenset(INTERACTION_VOCABULARY)


# --- STATE.md reading (shared shape with intake.py / route.py) ---------------

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


# --- Plan schema -------------------------------------------------------------

# Matches a markdown ATX heading line, capturing its text (drops leading "#"s and
# any trailing "#"s): "## Layout Grid" -> "Layout Grid".
_HEADING_LINE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")

# A markdown table separator cell: "---", ":---", "---:", ":---:".
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")


def _markdown_headings(text: str) -> list[str]:
    """Extract the text of every markdown heading in a document.

    Args:
        text: Markdown document contents.

    Returns:
        The heading texts, in document order (e.g. ``["Screen Size", "Elements"]``).
    """
    return [
        match.group(1).strip()
        for raw_line in text.splitlines()
        if (match := _HEADING_LINE.match(raw_line.strip()))
    ]


def _markdown_tables(text: str) -> list[list[list[str]]]:
    """Split a markdown document into its pipe tables.

    Contiguous runs of lines beginning with ``|`` are grouped into one table; each
    table is a list of rows, each row a list of trimmed cell strings.

    Args:
        text: Markdown document contents.

    Returns:
        A list of tables (each a list of cell-rows).
    """
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("|"):
            current.append([cell.strip() for cell in line.strip("|").split("|")])
            continue
        if current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _is_separator_row(cells: list[str]) -> bool:
    """Return True if a table row is the ``|---|---|`` header/body separator.

    Args:
        cells: The row's trimmed cell values.

    Returns:
        True when every non-empty cell looks like a separator dash run.
    """
    non_empty = [cell for cell in cells if cell]
    return bool(non_empty) and all(_SEPARATOR_CELL.match(cell) for cell in non_empty)


def _validate_id_tables(text: str) -> list[str]:
    """Check stable-id and interaction-vocabulary rules across the plan's tables.

    Every table whose first column header is ``id`` (the Elements, Filters, and
    Interactions tables) is checked: each data row must carry a non-empty id, ids must
    be unique across the whole plan (later steps reference them), and any table with an
    ``interaction`` column must use only the shared vocabulary (CONTRACT.md §6).

    Args:
        text: The contents of a ``DASHBOARD-PLAN.md`` file.

    Returns:
        A list of human-readable problem strings (empty when all id-tables are clean).
    """
    problems: list[str] = []
    all_ids: list[str] = []

    for table in _markdown_tables(text):
        rows = [row for row in table if not _is_separator_row(row)]
        if len(rows) < 2:  # need a header plus at least one data row to matter
            continue
        header = [cell.lower() for cell in rows[0]]
        if not header or header[0] != "id":
            continue  # not an id-bearing table (e.g. the Layout Grid's slot table)

        term_index = header.index("interaction") if "interaction" in header else None
        for row in rows[1:]:
            row_id = row[0].strip() if row else ""
            if not row_id:
                problems.append("an id-table row has an empty 'id' cell")
                continue
            all_ids.append(row_id)
            if term_index is not None and len(row) > term_index:
                term = row[term_index].strip()
                if term and term.lower() not in _INTERACTION_TERMS:
                    problems.append(
                        f"interaction '{row_id}' uses term '{term}', not in the shared "
                        f"vocabulary ({', '.join(INTERACTION_VOCABULARY)})"
                    )

    duplicates = sorted({rid for rid in all_ids if all_ids.count(rid) > 1})
    problems.extend(
        f"duplicate id '{dup}' - ids must be unique across the plan" for dup in duplicates
    )
    return problems


@dataclass(frozen=True)
class PlanValidation:
    """Result of validating a ``DASHBOARD-PLAN.md`` against the strict schema.

    Attributes:
        ok: True iff the plan is complete - no required section is missing and no
            id/vocabulary problem was found.
        missing_required: Required sections absent from the plan (CONTRACT.md schema).
        missing_recommended: Recommended sections absent (reported, never blocking).
        problems: Stable-id / interaction-vocabulary problems (see
            :func:`_validate_id_tables`).
    """

    ok: bool
    missing_required: list[str]
    missing_recommended: list[str]
    problems: list[str]


def validate_plan(text: str) -> PlanValidation:
    """Validate a plan's section coverage, stable ids, and interaction vocabulary.

    A plan is *valid* when it contains every required section and has no id or
    vocabulary problem. Section names are matched case-insensitively as substrings of
    the document's headings, so a plan that keeps richer headings (e.g.
    ``## Screen Size & Device``) still validates, and extra custom sections are allowed.

    Args:
        text: The contents of a ``DASHBOARD-PLAN.md`` file.

    Returns:
        A :class:`PlanValidation`.
    """
    headings_blob = "\n".join(_markdown_headings(text)).lower()
    missing_required = [
        section for section in PLAN_REQUIRED_SECTIONS
        if section.lower() not in headings_blob
    ]
    missing_recommended = [
        section for section in PLAN_RECOMMENDED_SECTIONS
        if section.lower() not in headings_blob
    ]
    problems = _validate_id_tables(text)
    ok = not missing_required and not problems
    return PlanValidation(ok, missing_required, missing_recommended, problems)


def render_plan_template() -> str:
    """Return the canonical DASHBOARD-PLAN template text this skill ships.

    Reads ``references/DASHBOARD-PLAN-TEMPLATE.md`` so there is a single source of
    truth for the strict schema - the same file the skill hands the model to fill in.
    It is, by construction, schema-complete (all required sections, unique ids, valid
    interaction terms).

    Returns:
        The template file contents.

    Raises:
        FileNotFoundError: If the bundled template is missing.
    """
    # This script lives in ``<skill>/scripts/``; references/ sits at the skill root.
    template_path = (
        Path(__file__).resolve().parent.parent / "references" / "DASHBOARD-PLAN-TEMPLATE.md"
    )
    if not template_path.is_file():
        raise FileNotFoundError(f"Plan template not found: {template_path}")
    return template_path.read_text(encoding="utf-8-sig")


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

    Only rows inside the ``## Steps`` table whose step name is a key in ``updates`` are
    touched; every other line (metadata, prose, untouched rows) is preserved
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


def _downstream_stale_updates(statuses: dict[str, str]) -> dict[str, str]:
    """Compute which downstream steps must flip to ``stale`` (CONTRACT.md §4.2).

    Every step ordered after ``plan`` that is currently ``approved`` becomes ``stale``;
    steps already ``pending`` / ``skipped`` / ``stale`` are left as-is.

    Args:
        statuses: The current ``{step_name: status}`` mapping.

    Returns:
        ``{step_name: "stale"}`` for each downstream step that was ``approved``.
    """
    plan_index = STEP_ORDER.index(PLAN_STEP)
    return {
        step: "stale"
        for step in STEP_ORDER[plan_index + 1:]
        if statuses.get(step) == "approved"
    }


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def entry_gate_blocker(project_root: Path) -> Optional[str]:
    """Return why plan may not run yet, or ``None`` if it may.

    Plan refuses to run unless ``STATE.md`` exists, its required read's producer
    (``data``) is resolved, and ``DATA-MODEL.md`` actually exists on disk (CONTRACT.md
    §4.1). ``intake`` and ``brand`` are *optional* reads and never gate plan.

    Args:
        project_root: The analyst's project directory.

    Returns:
        A human-readable blocker message, or ``None`` when the gate is open.
    """
    state_path = project_root / STATE_FILENAME
    if not state_path.exists():
        return (
            "No STATE.md found. Run 'tableau-init' first to scaffold the project "
            "and initialize STATE.md before running 'tableau-plan'."
        )

    data_status = parse_statuses(state_path.read_text(encoding="utf-8-sig")).get(
        DATA_STEP, "pending"
    )
    if data_status not in RESOLVED_STATUSES:
        return (
            f"Step 'data' is '{data_status}', not resolved. Run 'tableau-data' first; "
            f"'tableau-plan' needs '{DATA_MODEL_FILENAME}' before it can plan against "
            f"real field names."
        )

    if not (project_root / DATA_MODEL_FILENAME).exists():
        return (
            f"'{DATA_MODEL_FILENAME}' is missing on disk even though step 'data' is "
            f"'{data_status}'. Re-run 'tableau-data' to regenerate it."
        )
    return None


# --- precheck ----------------------------------------------------------------

@dataclass(frozen=True)
class PrecheckResult:
    """The state plan needs to know before authoring a DASHBOARD-PLAN.md.

    Attributes:
        can_run: True when the entry gate is open (data resolved, DATA-MODEL.md present).
        blocker: Why plan cannot run, when ``can_run`` is False; else ``None``.
        plan_exists: Whether a ``DASHBOARD-PLAN.md`` already exists (refine vs overwrite).
        requirements_source: Where the request will be read from - ``"PRD.md"``
            (preferred), ``REQUEST_FILENAME``, ``SCAFFOLD_REQUEST`` (demo fallback), or
            ``"none"`` (plan against the data model alone).
        tokens_present: Whether ``DESIGN-TOKENS.md`` exists (else neutral styling).
        plan_status: The plan step's current status (to detect a re-run).
    """

    can_run: bool
    blocker: Optional[str]
    plan_exists: bool
    requirements_source: str
    tokens_present: bool
    plan_status: str


def _resolve_requirements_source(project_root: Path) -> str:
    """Pick the requirements input, preferring the PRD, then the raw request (§3.1).

    Args:
        project_root: The analyst's project directory.

    Returns:
        ``PRD_FILENAME``, ``REQUEST_FILENAME``, ``SCAFFOLD_REQUEST``, or ``"none"``.
    """
    if (project_root / PRD_FILENAME).exists():
        return PRD_FILENAME
    if (project_root / REQUEST_FILENAME).exists():
        return REQUEST_FILENAME
    if (project_root / SCAFFOLD_REQUEST).exists():
        return SCAFFOLD_REQUEST
    return "none"


def precheck(project_dir: Path | str) -> PrecheckResult:
    """Report whether plan may run and what it should read.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`PrecheckResult`. When the entry gate is closed, ``can_run`` is False
        and ``blocker`` explains why; the other fields are placeholders.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return PrecheckResult(False, blocker, False, "none", False, "unknown")

    statuses = parse_statuses((project_root / STATE_FILENAME).read_text(encoding="utf-8-sig"))
    return PrecheckResult(
        can_run=True,
        blocker=None,
        plan_exists=(project_root / PLAN_FILENAME).exists(),
        requirements_source=_resolve_requirements_source(project_root),
        tokens_present=(project_root / DESIGN_TOKENS_FILENAME).exists(),
        plan_status=statuses.get(PLAN_STEP, "pending"),
    )


# --- commit ------------------------------------------------------------------

@dataclass
class CommitResult:
    """Outcome of committing the plan step's result to STATE.md.

    Attributes:
        ok: True when STATE.md was updated; False when the commit was refused.
        message: Human-readable explanation (the refusal reason when not ``ok``).
        staled_steps: Downstream steps flipped to ``stale`` by this commit.
        missing_required: Required plan sections that were absent (on a refusal).
        missing_recommended: Recommended sections absent (informational note).
        problems: Stable-id / vocabulary problems that blocked approval (on a refusal).
    """

    ok: bool
    message: str
    staled_steps: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_recommended: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def commit(project_dir: Path | str) -> CommitResult:
    """Validate and approve the plan in STATE.md, propagating staleness.

    Plan is **non-skippable**, so commit only ever sets ``approved``. The project's
    ``DASHBOARD-PLAN.md`` must exist and pass :func:`validate_plan` (all required
    sections, unique non-empty ids, vocabulary-conformant interactions) or the commit
    is refused so the model fixes it and re-commits. On success every downstream
    ``approved`` step is flipped to ``stale`` (CONTRACT.md §4.2) - a no-op on a first
    run, but the guard that keeps a re-run from silently disagreeing with the mock,
    spec, and workbook.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`CommitResult`. ``ok`` is False (and STATE.md is left untouched) when
        the entry gate is closed or the plan is incomplete/invalid.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return CommitResult(False, blocker)

    plan_path = project_root / PLAN_FILENAME
    if not plan_path.exists():
        return CommitResult(
            False,
            f"Cannot approve plan: '{PLAN_FILENAME}' does not exist. Author it first "
            f"using references/DASHBOARD-PLAN-TEMPLATE.md.",
        )

    validation = validate_plan(plan_path.read_text(encoding="utf-8-sig"))
    if not validation.ok:
        reasons: list[str] = []
        if validation.missing_required:
            reasons.append(
                f"missing required section(s): {', '.join(validation.missing_required)}"
            )
        if validation.problems:
            reasons.append("; ".join(validation.problems))
        return CommitResult(
            False,
            f"'{PLAN_FILENAME}' is incomplete - {'; '.join(reasons)}. Fix and re-run commit.",
            missing_required=validation.missing_required,
            missing_recommended=validation.missing_recommended,
            problems=validation.problems,
        )

    state_path = project_root / STATE_FILENAME
    text = state_path.read_text(encoding="utf-8-sig")
    statuses = parse_statuses(text)

    stale_updates = _downstream_stale_updates(statuses)
    updates = {PLAN_STEP: "approved", **stale_updates}
    state_path.write_text(apply_status_updates(text, updates), encoding="utf-8")

    staled_steps = sorted(stale_updates, key=STEP_ORDER.index)
    logger.info(f"Set plan -> approved; marked stale: {staled_steps or 'none'}.")
    return CommitResult(
        ok=True,
        message="plan -> approved",
        staled_steps=staled_steps,
        missing_recommended=validation.missing_recommended,
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
        return f"[BLOCKED] tableau-plan cannot run.\n{result.blocker}"

    lines = ["[PLAN] precheck OK - tableau-plan can run.", "  DATA-MODEL.md: present (required read)."]
    lines.append(f"  DASHBOARD-PLAN.md exists : {'yes' if result.plan_exists else 'no'}")
    if result.plan_exists:
        lines.append(
            "    -> a plan already exists; offer REFINE vs OVERWRITE - never silently "
            "overwrite the analyst's work."
        )

    if result.requirements_source == "none":
        lines.append(
            "  requirements : none found - plan against DATA-MODEL.md and confirm scope "
            "with the analyst."
        )
    elif result.requirements_source == SCAFFOLD_REQUEST:
        lines.append(
            f"  requirements : {SCAFFOLD_REQUEST} (DEMO fallback - say so; no PRD.md or "
            f"root {REQUEST_FILENAME})."
        )
    else:
        lines.append(f"  requirements : {result.requirements_source} (used to shape the plan).")

    lines.append(
        f"  DESIGN-TOKENS.md: {'present' if result.tokens_present else 'absent - neutral styling'}"
        " (optional read)."
    )

    rerun_note = (
        " (re-run; downstream approved steps will be marked stale on commit)"
        if result.plan_status in ("approved", "stale")
        else ""
    )
    lines.append(f"  plan status  : {result.plan_status}{rerun_note}")
    return "\n".join(lines)


def format_validation(validation: PlanValidation) -> str:
    """Render a :class:`PlanValidation` as a human-readable block.

    Args:
        validation: The validation result to render.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    # Plain ASCII only (see format_precheck).
    if validation.ok:
        lines = ["[OK] DASHBOARD-PLAN.md is complete (all required sections, unique ids)."]
        if validation.missing_recommended:
            lines.append(
                f"  note: no {', '.join(validation.missing_recommended)} section(s) - "
                f"optional, but 'Suggestions' is where you propose extra value."
            )
        return "\n".join(lines)

    lines = ["[INVALID] DASHBOARD-PLAN.md is incomplete."]
    if validation.missing_required:
        lines.append(f"  missing required section(s): {', '.join(validation.missing_required)}")
    for problem in validation.problems:
        lines.append(f"  problem: {problem}")
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

    lines = [f"[PLAN] {result.message}."]
    if result.missing_recommended:
        lines.append(
            f"  note: plan has no {', '.join(result.missing_recommended)} section(s) "
            f"- optional, left as-is."
        )
    if result.staled_steps:
        lines.append(
            f"  downstream marked stale: {', '.join(result.staled_steps)} "
            f"(re-run these in order)."
        )
    lines.append(
        "  next: open a fresh conversation and run 'tableau-mock' (or 'tableau-route')."
    )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point: run ``precheck``, ``validate``, or ``commit`` and print it.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``2`` when plan is blocked/refused/invalid
        or on a usage error.
    """
    parser = argparse.ArgumentParser(
        description="Gate, validate, and commit the tableau-plan step.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("precheck", "Report whether plan may run and what to read."),
        ("validate", "Check a draft DASHBOARD-PLAN.md against the strict schema."),
        ("commit", "Approve the plan in STATE.md and propagate staleness."),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument(
            "project_dir", nargs="?", default=".", help="Project directory (default: cwd)."
        )

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "precheck":
        precheck_result = precheck(args.project_dir)
        print(format_precheck(precheck_result))
        return 0 if precheck_result.can_run else 2

    if args.command == "validate":
        plan_path = Path(args.project_dir) / PLAN_FILENAME
        if not plan_path.exists():
            print(f"[INVALID] '{PLAN_FILENAME}' not found in '{args.project_dir}'.")
            return 2
        validation = validate_plan(plan_path.read_text(encoding="utf-8-sig"))
        print(format_validation(validation))
        return 0 if validation.ok else 2

    commit_result = commit(args.project_dir)
    print(format_commit(commit_result))
    return 0 if commit_result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
