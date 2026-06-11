"""Compute the next skill to run in the tableau-dashboard-plugin workflow.

This is the executable mirror of the ordering rule documented in ``CONTRACT.md``.
It reads a project's ``STATE.md`` manifest and reports the single next skill the
analyst should run, honoring the ordering gate (a step may only run once every
artifact in its *required reads* exists and its producer step is resolved).

The module is intentionally pure and stdlib-only so the contract test can call
``compute_next_step`` directly. ``main`` is a thin CLI that prints the
recommendation; the human-readable line it writes to stdout is the program's
*output*, not logging — diagnostics (e.g. a malformed ``STATE.md``) go through
the ``logging`` module instead.

Keep ``STEPS`` below in lock-step with the ordered step table in ``CONTRACT.md``.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Canonical workflow definition (mirror of CONTRACT.md §1) ----------------

#: Statuses that satisfy the ordering gate — the step is considered "resolved".
RESOLVED_STATUSES = frozenset({"approved", "skipped"})

#: Every status word a step may legally carry in STATE.md.
VALID_STATUSES = frozenset({"pending", "approved", "skipped", "stale"})

STATE_FILENAME = "STATE.md"


@dataclass(frozen=True)
class RequiredRead:
    """A producer-gated input a step must have before it may run.

    Attributes:
        producer_step: The step name (e.g. ``"data"``) that produces the artifact.
        artifact: Path to the artifact relative to the project root. May contain
            ``{version}``, substituted with ``current_version`` from STATE.md, and
            may end in a glob (``sample-data/*.csv``) meaning "at least one match".
    """

    producer_step: str
    artifact: str


@dataclass(frozen=True)
class Step:
    """One step in the ordered workflow.

    Attributes:
        order: 1-based position in the canonical sequence.
        name: Short step name used in STATE.md (e.g. ``"plan"``).
        skill: The skill that owns the step (e.g. ``"tableau-plan"``).
        required_reads: Producer-gated inputs that gate this step (CONTRACT.md §4.1).
            Optional reads (from skippable producers) are deliberately excluded —
            they enrich output but never block.
    """

    order: int
    name: str
    skill: str
    required_reads: tuple[RequiredRead, ...] = ()


# The ordered step list. Required reads gate ordering; optional reads are omitted.
STEPS: tuple[Step, ...] = (
    Step(1, "init", "tableau-init"),
    Step(2, "intake", "tableau-intake"),
    Step(3, "data", "tableau-data"),
    Step(4, "brand", "tableau-brand"),
    Step(5, "plan", "tableau-plan", (
        RequiredRead("data", "DATA-MODEL.md"),
    )),
    Step(6, "mock", "tableau-mock", (
        RequiredRead("plan", "DASHBOARD-PLAN.md"),
        RequiredRead("data", "sample-data/*.csv"),
    )),
    Step(7, "spec", "tableau-spec", (
        RequiredRead("plan", "DASHBOARD-PLAN.md"),
        RequiredRead("mock", "mock-version/{version}/mock.html"),
    )),
    Step(8, "build", "tableau-build", (
        RequiredRead("spec", "mock-version/{version}/IMPLEMENTATION-SPEC.md"),
        RequiredRead("data", "DATA-MODEL.md"),
        RequiredRead("data", "sample-data/*.csv"),
    )),
)

#: Step name -> Step, for quick producer lookups.
STEP_BY_NAME: dict[str, Step] = {step.name: step for step in STEPS}

#: The skill that scaffolds a fresh project (recommended when STATE.md is absent).
INIT_SKILL = "tableau-init"


# --- Result type -------------------------------------------------------------

@dataclass(frozen=True)
class RouteResult:
    """The router's recommendation for a project.

    Attributes:
        kind: One of ``"fresh"`` (no STATE.md), ``"ready"`` (a step is runnable),
            ``"blocked"`` (the candidate is gated by an unresolved/missing
            upstream artifact, so ``next_skill`` points at that blocker), or
            ``"done"`` (every step resolved).
        next_skill: The skill to run next, or ``None`` when ``kind == "done"``.
        next_step: The step name to run next, or ``None`` when ``kind == "done"``.
        reason: Human-readable explanation of why this is the next step.
    """

    kind: str
    next_skill: Optional[str]
    next_step: Optional[str]
    reason: str

    @property
    def is_done(self) -> bool:
        """bool: True when the pipeline is complete (no step left to run)."""
        return self.kind == "done"


@dataclass
class ProjectState:
    """Parsed contents of a project's STATE.md.

    Attributes:
        metadata: Lower-cased ``key -> value`` pairs from the Metadata section.
        statuses: ``step_name -> status`` from the Steps table.
    """

    metadata: dict[str, str] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)

    @property
    def current_version(self) -> str:
        """str: The active deliverable version directory (defaults to ``v_1``)."""
        return self.metadata.get("current_version", "v_1")


# --- Parsing -----------------------------------------------------------------

# Matches a "- key: value" metadata line, ignoring any trailing "# comment".
_METADATA_LINE = re.compile(r"^-\s*([A-Za-z_]+)\s*:\s*(.+?)\s*(?:#.*)?$")


def _resolve_state_path(target: Path) -> Path:
    """Resolve the STATE.md path for a directory or a direct file path.

    Args:
        target: Either a project directory (which may contain ``STATE.md``) or a
            path to a ``STATE.md`` file directly.

    Returns:
        The path where ``STATE.md`` is expected. It may or may not exist.
    """
    if target.is_dir():
        return target / STATE_FILENAME
    return target


def parse_state(state_path: Path) -> ProjectState:
    """Parse a STATE.md manifest into metadata and per-step statuses.

    Parsing is tolerant: unknown metadata keys are kept, and unrecognized step
    rows are skipped with a warning rather than raising, so a slightly
    hand-edited manifest still routes.

    Args:
        state_path: Path to the ``STATE.md`` file (must exist).

    Returns:
        The parsed :class:`ProjectState`.
    """
    state = ProjectState()
    text = state_path.read_text(encoding="utf-8-sig")  # tolerate UTF-8 BOM

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
            _parse_step_row(line, state)
            continue

        metadata_match = _METADATA_LINE.match(line)
        if metadata_match:
            key, value = metadata_match.group(1).lower(), metadata_match.group(2).strip()
            state.metadata[key] = value

    return state


def _parse_step_row(line: str, state: ProjectState) -> None:
    """Parse one Steps-table row and record its status into ``state``.

    Header and separator rows (and any row whose step name is unknown) are
    silently ignored — only genuine ``| order | step | skill | status |`` rows
    contribute.

    Args:
        line: A markdown table row beginning with ``|``.
        state: The :class:`ProjectState` being populated (mutated in place).
    """
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    if len(cells) < 4:
        return

    step_name, status = cells[1].lower(), cells[3].lower()
    if step_name not in STEP_BY_NAME:
        return  # header row ("step"), separator ("---"), or stray text

    if status not in VALID_STATUSES:
        logger.warning(
            f"Step '{step_name}' has unknown status '{status}' in STATE.md; "
            f"treating it as 'pending'."
        )
        status = "pending"
    state.statuses[step_name] = status


# --- Routing -----------------------------------------------------------------

def _artifact_exists(project_root: Path, artifact: str, version: str) -> bool:
    """Check whether a required-read artifact is present on disk.

    Supports ``{version}`` substitution and a trailing glob (meaning "at least
    one match").

    Args:
        project_root: The project directory that holds the artifacts.
        artifact: The artifact path relative to ``project_root``.
        version: The ``current_version`` value used to fill ``{version}``.

    Returns:
        True if the artifact (or at least one glob match) exists.
    """
    relative = artifact.format(version=version)
    if "*" in relative:
        return any(project_root.glob(relative))
    return (project_root / relative).exists()


def _gate_blocker(step: Step, state: ProjectState, project_root: Path) -> Optional[RouteResult]:
    """Return a "blocked" result if any required read of ``step`` is unmet.

    Implements the ordering gate (CONTRACT.md §4.1): each required read's
    producer must be resolved AND its artifact must exist on disk. The first
    unmet read wins, and the recommendation points at the producer step.

    Args:
        step: The candidate step about to be recommended.
        state: The parsed project state.
        project_root: The project directory (for artifact existence checks).

    Returns:
        A :class:`RouteResult` of kind ``"blocked"`` if gated, else ``None``.
    """
    for read in step.required_reads:
        producer = STEP_BY_NAME[read.producer_step]
        producer_status = state.statuses.get(read.producer_step, "pending")

        if producer_status not in RESOLVED_STATUSES:
            return RouteResult(
                kind="blocked",
                next_skill=producer.skill,
                next_step=producer.name,
                reason=(
                    f"'{step.skill}' needs '{read.artifact}' from step "
                    f"'{producer.name}', but '{producer.name}' is "
                    f"'{producer_status}'. Run '{producer.skill}' first."
                ),
            )

        if not _artifact_exists(project_root, read.artifact, state.current_version):
            return RouteResult(
                kind="blocked",
                next_skill=producer.skill,
                next_step=producer.name,
                reason=(
                    f"'{step.skill}' needs '{read.artifact}', but it is missing "
                    f"on disk even though step '{producer.name}' is "
                    f"'{producer_status}'. Re-run '{producer.skill}' to "
                    f"regenerate it."
                ),
            )
    return None


def compute_next_step(target: Path | str) -> RouteResult:
    """Compute the next skill to run for a project.

    Args:
        target: The project directory (which may contain ``STATE.md``) or a path
            to a ``STATE.md`` file directly.

    Returns:
        A :class:`RouteResult`. ``kind`` is ``"fresh"`` when no ``STATE.md``
        exists, ``"ready"`` when a step is directly runnable, ``"blocked"`` when
        an unresolved/missing upstream artifact gates the candidate, or
        ``"done"`` when every step is resolved.
    """
    target_path = Path(target)
    state_path = _resolve_state_path(target_path)

    if not state_path.exists():
        return RouteResult(
            kind="fresh",
            next_skill=INIT_SKILL,
            next_step="init",
            reason=(
                "No STATE.md found - this is a fresh project. Run "
                f"'{INIT_SKILL}' to scaffold it and initialize STATE.md."
            ),
        )

    project_root = state_path.parent
    state = parse_state(state_path)

    for step in STEPS:  # canonical order
        status = state.statuses.get(step.name, "pending")
        if status in RESOLVED_STATUSES:
            continue

        # First unresolved step is the candidate; verify its ordering gate.
        blocker = _gate_blocker(step, state, project_root)
        if blocker is not None:
            return blocker

        verb = "Re-run" if status == "stale" else "Run"
        reason = (
            f"Step '{step.name}' is '{status}'. {verb} '{step.skill}' next."
        )
        if status == "stale":
            reason += " (An upstream change marked it stale.)"
        return RouteResult(
            kind="ready",
            next_skill=step.skill,
            next_step=step.name,
            reason=reason,
        )

    return RouteResult(
        kind="done",
        next_skill=None,
        next_step=None,
        reason=(
            "All steps are resolved (approved or skipped). The pipeline is "
            f"complete - your deliverables are under 'mock-version/"
            f"{state.current_version}/'."
        ),
    )


# --- CLI ---------------------------------------------------------------------

def format_recommendation(result: RouteResult) -> str:
    """Render a :class:`RouteResult` as a human-readable recommendation block.

    Args:
        result: The routing result to render.

    Returns:
        A multi-line string suitable for printing to the analyst.
    """
    # Plain ASCII only: this prints to the console, which on Windows is cp1252
    # and would raise UnicodeEncodeError on emoji/box-drawing glyphs.
    if result.is_done:
        header = "[DONE] Pipeline complete"
    elif result.kind == "fresh":
        header = f"[NEXT] {result.next_skill} (fresh project)"
    elif result.kind == "blocked":
        header = f"[BLOCKED] run upstream first: {result.next_skill}"
    else:
        header = f"[NEXT] {result.next_skill}"
    return f"{header}\n{result.reason}"


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point: print the next-step recommendation for a project.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``). The first argument is
            the project directory or STATE.md path; defaults to the current
            directory.

    Returns:
        Process exit code (always ``0``).
    """
    args = sys.argv[1:] if argv is None else argv
    target = args[0] if args else "."
    result = compute_next_step(target)
    print(format_recommendation(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
