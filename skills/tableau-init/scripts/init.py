"""Scaffold a fresh tableau-dashboard-plugin project and initialize STATE.md.

This is the executable core of the ``tableau-init`` skill (CONTRACT.md step 1).
Run in an empty (or partially populated) project directory, it:

1. copies this skill's ``skeleton/`` templates into a ``scaffold/`` folder in the
   project — ``EXAMPLE-DASHBOARD-REQUEST.md``, ``EXAMPLE-datasources.json``,
   ``.env.example``, ``branding/EXAMPLE-branding.md``, and a starter
   ``sample-data/`` — so the project can run a full demo immediately, and
2. writes a schema-valid ``STATE.md`` manifest recording the analyst's chosen
   ``target_tableau_version`` so the downstream build step never has to re-ask.

Everything under ``scaffold/`` is a demo example. Downstream skills always prefer
the analyst's *production* files at the project root (``DASHBOARD-REQUEST.md``,
``datasources.json``, ``branding/``, ``data/``) and fall back to the ``scaffold/``
example only to trial the workflow. ``init`` deliberately does **not** create the
production files — their absence is the signal of what the analyst still owes.

Scaffolding is **idempotent and non-destructive**: a file that already exists on
disk is never overwritten, so re-running on an established project preserves the
analyst's edits (including pipeline progress recorded in an existing STATE.md).

The module is intentionally pure and stdlib-only so the contract test can call
``scaffold_project`` directly. ``main`` is a thin CLI; the summary it prints to
stdout is the program's *output*, while diagnostics go through ``logging``.

Keep ``WORKFLOW_STEPS`` below in lock-step with the Steps table in ``CONTRACT.md``
§2 (the canonical schema).
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Canonical STATE.md schema (mirror of CONTRACT.md §2) --------------------

#: Allowed values for the ``target_tableau_version`` metadata field. Captured at
#: init and never re-asked; drives tableau-build's workbook version attribute.
ALLOWED_TARGET_VERSIONS: tuple[str, ...] = ("2024.2-2025.x", "2026.1+")

#: The default, zero-credential data acquisition mode.
DEFAULT_DATA_MODE = "csv"

#: The first deliverable version directory.
DEFAULT_CURRENT_VERSION = "v_1"

STATE_FILENAME = "STATE.md"


@dataclass(frozen=True)
class WorkflowStep:
    """One row of the STATE.md Steps table.

    Attributes:
        order: 1-based position in the canonical sequence.
        name: Short step name (e.g. ``"plan"``).
        skill: The skill that owns the step (e.g. ``"tableau-plan"``).
        initial_status: The status written at init time. ``init`` itself is
            ``approved`` (scaffolding just completed) so the router advances to
            ``intake``; every later step starts ``pending``.
    """

    order: int
    name: str
    skill: str
    initial_status: str


# The 8 ordered steps with their init-time statuses (CONTRACT.md §2).
WORKFLOW_STEPS: tuple[WorkflowStep, ...] = (
    WorkflowStep(1, "init", "tableau-init", "approved"),
    WorkflowStep(2, "intake", "tableau-intake", "pending"),
    WorkflowStep(3, "data", "tableau-data", "pending"),
    WorkflowStep(4, "brand", "tableau-brand", "pending"),
    WorkflowStep(5, "plan", "tableau-plan", "pending"),
    WorkflowStep(6, "mock", "tableau-mock", "pending"),
    WorkflowStep(7, "spec", "tableau-spec", "pending"),
    WorkflowStep(8, "build", "tableau-build", "pending"),
)


# --- STATE.md rendering ------------------------------------------------------

def render_state_md(
    target_version: str,
    data_mode: str = DEFAULT_DATA_MODE,
    current_version: str = DEFAULT_CURRENT_VERSION,
) -> str:
    """Render the STATE.md manifest text for a freshly initialized project.

    The output conforms to the canonical schema in CONTRACT.md §2: a Metadata
    section and an 8-row Steps table with ``init`` ``approved`` and steps 2-8
    ``pending``.

    Args:
        target_version: One of :data:`ALLOWED_TARGET_VERSIONS`.
        data_mode: How ``tableau-data`` will acquire rows; defaults to ``csv``.
        current_version: The active deliverable version directory.

    Returns:
        The complete STATE.md file contents, terminated by a trailing newline.

    Raises:
        ValueError: If ``target_version`` is not an allowed value.
    """
    if target_version not in ALLOWED_TARGET_VERSIONS:
        allowed = " | ".join(ALLOWED_TARGET_VERSIONS)
        raise ValueError(
            f"Invalid target_tableau_version '{target_version}'. "
            f"Allowed values: {allowed}."
        )

    # Build the Steps table with aligned columns to match the canonical look.
    header = "| order | step   | skill          | status   |"
    separator = "|-------|--------|----------------|----------|"
    rows = [
        f"| {step.order:<5} | {step.name:<6} | {step.skill:<14} | {step.initial_status:<8} |"
        for step in WORKFLOW_STEPS
    ]
    steps_table = "\n".join([header, separator, *rows])

    allowed_versions_comment = " | ".join(ALLOWED_TARGET_VERSIONS)
    return (
        "# Project State\n"
        "\n"
        "> Managed by tableau-dashboard-plugin skills. See CONTRACT.md before hand-editing.\n"
        "\n"
        "## Metadata\n"
        f"- target_tableau_version: {target_version}   # {allowed_versions_comment}\n"
        f"- data_mode: {data_mode}                          # csv | published-ds\n"
        f"- current_version: {current_version}                    # v_1, v_2, ...\n"
        "\n"
        "## Steps\n"
        f"{steps_table}\n"
    )


# --- Scaffolding -------------------------------------------------------------

@dataclass
class ScaffoldResult:
    """Outcome of a :func:`scaffold_project` run.

    Attributes:
        created: Project-relative POSIX paths of files this run wrote.
        skipped: Project-relative POSIX paths that already existed and were left
            untouched (the non-destructive guarantee).
        state_created: True if this run wrote a new ``STATE.md``; False if one
            already existed and was preserved.
    """

    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    state_created: bool = False


def _default_skeleton_dir() -> Path:
    """Path: the ``skeleton/`` directory this skill ships with."""
    # This script lives in ``<skill>/scripts/``; the skeleton sits at the skill root.
    return Path(__file__).resolve().parent.parent / "skeleton"


def scaffold_project(
    project_dir: Path | str,
    target_version: str,
    skeleton_dir: Optional[Path | str] = None,
) -> ScaffoldResult:
    """Scaffold a project skeleton and initialize its STATE.md.

    Every skeleton file is copied into ``project_dir`` only if a file does not
    already exist at that path, so user edits (and an existing ``STATE.md`` that
    records pipeline progress) are never clobbered. The target version is
    validated up front and recorded into the new ``STATE.md``.

    Args:
        project_dir: The directory to scaffold (created if absent).
        target_version: One of :data:`ALLOWED_TARGET_VERSIONS`, recorded into
            ``STATE.md`` as ``target_tableau_version``.
        skeleton_dir: Source templates directory; defaults to this skill's own
            ``skeleton/``.

    Returns:
        A :class:`ScaffoldResult` describing what was created vs. preserved.

    Raises:
        ValueError: If ``target_version`` is invalid.
        FileNotFoundError: If ``skeleton_dir`` does not exist.
    """
    if target_version not in ALLOWED_TARGET_VERSIONS:
        allowed = " | ".join(ALLOWED_TARGET_VERSIONS)
        raise ValueError(
            f"Invalid target_tableau_version '{target_version}'. "
            f"Allowed values: {allowed}."
        )

    project_root = Path(project_dir)
    source = Path(skeleton_dir) if skeleton_dir is not None else _default_skeleton_dir()
    if not source.is_dir():
        raise FileNotFoundError(f"Skeleton directory not found: {source}")

    project_root.mkdir(parents=True, exist_ok=True)
    result = ScaffoldResult()

    logger.info(f"Scaffolding project at '{project_root}' from skeleton '{source}'.")

    # Copy every skeleton file, file-by-file, so a project that already holds
    # some of these inputs keeps them and only gains the ones it is missing.
    for src_file in sorted(source.rglob("*")):
        if not src_file.is_file():
            continue
        relative = src_file.relative_to(source)
        dest_file = project_root / relative
        rel_posix = relative.as_posix()

        if dest_file.exists():
            logger.info(f"Skipping existing '{rel_posix}' (preserving user content).")
            result.skipped.append(rel_posix)
            continue

        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest_file)
        logger.info(f"Created '{rel_posix}'.")
        result.created.append(rel_posix)

    _write_state_md(project_root, target_version, result)
    return result


def _write_state_md(
    project_root: Path,
    target_version: str,
    result: ScaffoldResult,
) -> None:
    """Write STATE.md if absent, recording the outcome on ``result``.

    An existing STATE.md is preserved rather than reset, so re-running init on a
    project mid-pipeline never discards recorded progress.

    Args:
        project_root: The project directory being scaffolded.
        target_version: The validated target Tableau version to record.
        result: The :class:`ScaffoldResult` to update (mutated in place).
    """
    state_path = project_root / STATE_FILENAME
    if state_path.exists():
        logger.info(f"Skipping existing '{STATE_FILENAME}' (preserving pipeline state).")
        result.skipped.append(STATE_FILENAME)
        result.state_created = False
        return

    state_path.write_text(render_state_md(target_version), encoding="utf-8")
    logger.info(f"Created '{STATE_FILENAME}' (target_tableau_version={target_version}).")
    result.created.append(STATE_FILENAME)
    result.state_created = True


# --- CLI ---------------------------------------------------------------------

def format_summary(result: ScaffoldResult, project_dir: Path | str) -> str:
    """Render a :class:`ScaffoldResult` as a human-readable summary block.

    Args:
        result: The scaffolding result to render.
        project_dir: The project directory that was scaffolded.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    # Plain ASCII only: this prints to the console, which on Windows is cp1252
    # and would raise UnicodeEncodeError on emoji/box-drawing glyphs.
    lines = [f"[INIT] Scaffolded project at '{project_dir}'"]
    if result.created:
        lines.append(f"  created ({len(result.created)}): {', '.join(result.created)}")
    if result.skipped:
        lines.append(f"  preserved ({len(result.skipped)}): {', '.join(result.skipped)}")
    lines.append(
        "  scaffold/ holds demo examples; create your real DASHBOARD-REQUEST.md at "
        "the project root (or paste your request into tableau-intake)."
    )
    if result.state_created:
        lines.append("  next: run 'tableau-route' to confirm the next step (expected: tableau-intake).")
    else:
        lines.append("  STATE.md already existed and was preserved; run 'tableau-route' to see where you are.")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point: scaffold a project and print a summary.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``2`` on a usage/validation error.
    """
    parser = argparse.ArgumentParser(
        description="Scaffold a tableau-dashboard-plugin project and initialize STATE.md.",
    )
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="Project directory to scaffold (default: current directory).",
    )
    parser.add_argument(
        "--target-version",
        required=True,
        choices=ALLOWED_TARGET_VERSIONS,
        help="Target Tableau Desktop version recorded into STATE.md.",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        result = scaffold_project(args.project_dir, args.target_version)
    except (ValueError, FileNotFoundError) as error:
        logger.error(f"Scaffolding failed: {error}")
        return 2

    print(format_summary(result, args.project_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
