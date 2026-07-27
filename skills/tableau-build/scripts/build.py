"""Gate and commit the ``build`` step of the tableau-dashboard-plugin.

This is the orchestration core of the ``tableau-build`` skill (CONTRACT.md step 8): the
final, non-skippable step that turns the approved ``IMPLEMENTATION-SPEC.md`` plus
``DATA-MODEL.md`` into a Replace-Data-Source-ready Tableau workbook. The manifest *schema*
lives in :mod:`manifest`; this module owns the parts that touch ``STATE.md`` and the
filesystem:

1. **Entry gate** - build refuses to run until both required reads are present and their
   producers resolved (CONTRACT.md §4.1): ``IMPLEMENTATION-SPEC.md`` at ``current_version``
   (from ``spec``) - the construct-by-construct mapping - and ``DATA-MODEL.md`` +
   ``data/*.csv`` (from ``data``) - the fields the workbook binds to.
2. **Versioning** - build is a *deliverable* written under ``mock-version/v_N/`` beside the
   mock and spec it was built from (CONTRACT.md §4.3). Per §4.3 only the leading deliverable
   (``mock``) bumps ``current_version``; build writes into the mock's current version and
   **overwrites in place** on a re-run.
3. **STATE.md transition** - committing flips ``build`` to ``approved``. Build is the last
   step, so there is nothing downstream to stale (§4.2 is a no-op here).

4. **Assembly** - ``build`` turns the validated manifest into the deliverable: the XML comes
   from :mod:`twb` (pure, correct by construction), and this module supplies the CSV header
   rows it needs, runs both migrated validators over the result, and packages the ``.twb``
   plus the CSVs into a ``.twbx``.

The module is pure and stdlib-only (it does **not** import the router) so the contract test
can call its functions directly, exactly like ``spec.py``. The CLI exposes ``precheck``
(before authoring - reports the target ``v_N`` and the inputs), ``validate`` (the manifest
schema check), ``build`` (manifest -> validated ``.twbx``), and ``commit`` (after approval).
Program output goes to stdout; diagnostics through ``logging``.

Keep ``STEP_ORDER`` in lock-step with CONTRACT.md §1.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import logging
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import twb
from manifest import load_manifest, placed_layout_ids, validate_manifest

logger = logging.getLogger(__name__)

# --- Canonical constants (mirror of CONTRACT.md §1 / §3 / §4.3) --------------

STATE_FILENAME = "STATE.md"
DATA_MODEL_FILENAME = "DATA-MODEL.md"
SPEC_FILENAME = "IMPLEMENTATION-SPEC.md"
#: Optional read: styling. Absent (branding skipped) means Tableau's own defaults.
DESIGN_TOKENS_FILENAME = "DESIGN-TOKENS.md"
#: Build-internal, not a handoff artifact - hence lowercase (CONTRACT.md §3).
MANIFEST_FILENAME = "build-manifest.json"
WORKBOOK_FILENAME = "dashboard.twbx"
#: The unpackaged workbook, kept beside the .twbx: it is what the validators read, and it
#: stays on disk after a failed build so the XML can be inspected.
TWB_FILENAME = "dashboard.twb"
VERSION_DIR = "mock-version"
DATA_DIR = "data"
SCAFFOLD_DATA_DIR = "scaffold/sample-data"

#: Required reads that gate this step, and their producer steps (CONTRACT.md §4.1).
SPEC_STEP = "spec"
DATA_STEP = "data"

#: This step.
BUILD_STEP = "build"

#: The 8 step names in canonical order (mirror of CONTRACT.md §1).
STEP_ORDER: tuple[str, ...] = (
    "init", "intake", "data", "brand", "plan", "mock", "spec", "build",
)

#: Statuses that satisfy the ordering gate - the producer step is "resolved" (§4.1).
RESOLVED_STATUSES = frozenset({"approved", "skipped"})


# --- STATE.md reading (shared shape with spec.py / route.py) -----------------

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


# Metadata lines build reads (never writes: only ``mock`` bumps current_version, §4.3).
_CURRENT_VERSION_LINE = re.compile(r"^\s*-\s*current_version\s*:\s*(\S+)", re.MULTILINE)
_TARGET_VERSION_LINE = re.compile(
    r"^\s*-\s*target_tableau_version\s*:\s*([^\n#]+)", re.MULTILINE
)


def read_current_version(text: str) -> str:
    """Read the ``current_version`` metadata value from STATE.md.

    This is the version directory holding the mock and its spec, and where the workbook and
    its manifest are written.

    Args:
        text: The full contents of a ``STATE.md`` file.

    Returns:
        The recorded version (e.g. ``"v_2"``), or ``"v_1"`` if no line is present.
    """
    match = _CURRENT_VERSION_LINE.search(text)
    return match.group(1) if match else "v_1"


def read_target_tableau_version(text: str) -> str:
    """Read the ``target_tableau_version`` metadata value from STATE.md.

    Captured at ``init`` and never re-asked (CONTRACT.md §2); it drives the workbook's
    ``version`` attribute and any version-specific XML, and the manifest must carry it.

    Args:
        text: The full contents of a ``STATE.md`` file.

    Returns:
        The recorded target version, or ``""`` when the line is absent.
    """
    match = _TARGET_VERSION_LINE.search(text)
    return match.group(1).strip() if match else ""


# --- STATE.md rewriting (shared shape with spec.py / state.py) ---------------

def _format_step_row(cells: list[str]) -> str:
    """Render a Steps-table row with the canonical column widths (matches init/spec)."""
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


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def _sample_csvs(project_root: Path) -> list[str]:
    """Return the CSVs backing the build, preferring ``data/`` over the demo scaffold.

    Per CONTRACT.md §3.1 the csv read is satisfied by either the analyst's ``data/`` or the
    ``scaffold/sample-data/`` demo example, so a demo run is never wrongly blocked.

    Args:
        project_root: The analyst's project directory.

    Returns:
        Project-relative CSV paths (empty when neither location holds one).
    """
    for directory in (DATA_DIR, SCAFFOLD_DATA_DIR):
        found = sorted((project_root / directory).glob("*.csv"))
        if found:
            return [f"{directory}/{path.name}" for path in found]
    return []


def entry_gate_blocker(project_root: Path) -> Optional[str]:
    """Return why build may not run yet, or ``None`` if it may.

    Build refuses to run unless ``STATE.md`` exists and, for each required read
    (CONTRACT.md §4.1): ``spec`` is resolved and ``IMPLEMENTATION-SPEC.md`` exists at
    ``current_version``, and ``data`` is resolved with ``DATA-MODEL.md`` plus at least one
    CSV on disk.

    Args:
        project_root: The analyst's project directory.

    Returns:
        A human-readable blocker message, or ``None`` when the gate is open.
    """
    state_path = project_root / STATE_FILENAME
    if not state_path.exists():
        return (
            "No STATE.md found. Run 'tableau-init' first to scaffold the project "
            "and initialize STATE.md before running 'tableau-build'."
        )

    text = state_path.read_text(encoding="utf-8-sig")
    statuses = parse_statuses(text)
    version = read_current_version(text)

    spec_status = statuses.get(SPEC_STEP, "pending")
    if spec_status not in RESOLVED_STATUSES:
        return (
            f"Step 'spec' is '{spec_status}', not resolved. Run 'tableau-spec' first; "
            f"'tableau-build' builds from the spec's construct mapping instead of guessing."
        )
    if not (project_root / VERSION_DIR / version / SPEC_FILENAME).exists():
        return (
            f"'{VERSION_DIR}/{version}/{SPEC_FILENAME}' is missing on disk even though "
            f"step 'spec' is '{spec_status}'. Re-run 'tableau-spec' to regenerate it."
        )

    data_status = statuses.get(DATA_STEP, "pending")
    if data_status not in RESOLVED_STATUSES:
        return (
            f"Step 'data' is '{data_status}', not resolved. Run 'tableau-data' first; "
            f"'tableau-build' binds every field to the documented data model."
        )
    if not (project_root / DATA_MODEL_FILENAME).exists():
        return (
            f"'{DATA_MODEL_FILENAME}' is missing on disk even though step 'data' is "
            f"'{data_status}'. Re-run 'tableau-data' to regenerate it."
        )
    if not _sample_csvs(project_root):
        return (
            f"No CSV found in '{DATA_DIR}/' or '{SCAFFOLD_DATA_DIR}/' even though step "
            f"'data' is '{data_status}'. Re-run 'tableau-data' to write the samples."
        )
    return None


# --- precheck ----------------------------------------------------------------

@dataclass(frozen=True)
class PrecheckResult:
    """The state build needs before deriving a manifest.

    Attributes:
        can_run: True when the entry gate is open (spec + data resolved, artifacts present).
        blocker: Why build cannot run, when ``can_run`` is False; else ``None``.
        version: The ``current_version`` dir holding the mock/spec, built into in place.
        spec_path: ``mock-version/<version>/IMPLEMENTATION-SPEC.md`` (the constructs to build).
        data_model_path: ``DATA-MODEL.md`` (the field authority).
        csv_paths: The CSVs backing the workbook (``data/`` or the scaffold demo).
        manifest_path: ``mock-version/<version>/build-manifest.json`` (what to author).
        workbook_path: ``mock-version/<version>/dashboard.twbx`` (the deliverable).
        manifest_exists: Whether a manifest already exists (refine vs. derive fresh).
        target_tableau_version: STATE.md's target version, which the manifest must carry.
    """

    can_run: bool
    blocker: Optional[str]
    version: str
    spec_path: str
    data_model_path: str
    csv_paths: list[str]
    manifest_path: str
    workbook_path: str
    manifest_exists: bool
    target_tableau_version: str


def precheck(project_dir: Path | str) -> PrecheckResult:
    """Report whether build may run, what it reads, and where it writes.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`PrecheckResult`. When the entry gate is closed, ``can_run`` is False and
        ``blocker`` explains why; the other fields are placeholders.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return PrecheckResult(False, blocker, "v_1", "", "", [], "", "", False, "")

    text = (project_root / STATE_FILENAME).read_text(encoding="utf-8-sig")
    version = read_current_version(text)
    manifest_rel = f"{VERSION_DIR}/{version}/{MANIFEST_FILENAME}"

    return PrecheckResult(
        can_run=True,
        blocker=None,
        version=version,
        spec_path=f"{VERSION_DIR}/{version}/{SPEC_FILENAME}",
        data_model_path=DATA_MODEL_FILENAME,
        csv_paths=_sample_csvs(project_root),
        manifest_path=manifest_rel,
        workbook_path=f"{VERSION_DIR}/{version}/{WORKBOOK_FILENAME}",
        manifest_exists=(project_root / manifest_rel).exists(),
        target_tableau_version=read_target_tableau_version(text),
    )


# --- Spec reconciliation (CONTRACT.md §1.1) ----------------------------------
# The spec's Layout section is the mock's geometry, machine-checked at step 7. The manifest
# must carry *that* tree, not a re-derived one, so the workbook cannot silently disagree
# with the approved spec. These three patterns mirror tableau-spec's reconcile.py; the
# skills are self-contained (CONTRACT.md §7) and cannot import each other.
_LAYOUT_HEADING = re.compile(r"^##\s+Layout\b.*$", re.MULTILINE)
_NEXT_SECTION = re.compile(r"^##\s", re.MULTILINE)
_FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def spec_layout(spec_text: str) -> Optional[dict]:
    """Return the layout object from the spec's ``## Layout`` fenced JSON block.

    Args:
        spec_text: The contents of an ``IMPLEMENTATION-SPEC.md``.

    Returns:
        The parsed layout (``canvas`` + ``root``), or ``None`` when the spec has no
        parseable Layout JSON block.
    """
    heading = _LAYOUT_HEADING.search(spec_text)
    if heading is None:
        return None
    section = spec_text[heading.end():]
    next_section = _NEXT_SECTION.search(section)
    if next_section is not None:
        section = section[: next_section.start()]
    block = _FENCED_BLOCK.search(section)
    if block is None:
        return None
    try:
        layout = json.loads(block.group(1))
    except json.JSONDecodeError:
        return None
    return layout if isinstance(layout, dict) else None


def _normalized_node(node: object) -> object:
    """Reduce a layout node to the geometry that must survive the copy into the manifest.

    Args:
        node: A layout node (or anything, when the tree is malformed).

    Returns:
        A hashable ``(id, type, size, children)`` tuple, ``None`` for a non-object node.
        Sizes are rounded to 2 decimals so re-serialised floats still compare equal.
    """
    if not isinstance(node, dict):
        return None
    size = node.get("size")
    numeric_size = (
        round(float(size), 2)
        if isinstance(size, (int, float)) and not isinstance(size, bool) else None
    )
    children = node.get("children")
    return (
        str(node.get("id", "")).strip(),
        str(node.get("type", "")).strip(),
        numeric_size,
        tuple(_normalized_node(child) for child in children)
        if isinstance(children, list) else None,
    )


def spec_layout_errors(spec_text: str, manifest_document: dict) -> list[str]:
    """Check the manifest's layout tree against the approved spec's.

    Args:
        spec_text: The contents of the approved ``IMPLEMENTATION-SPEC.md``.
        manifest_document: The parsed build manifest.

    Returns:
        A list of error messages naming the dropped/invented zones, or reporting a geometry
        that differs; empty when the manifest carries the spec's tree.
    """
    from_spec = spec_layout(spec_text)
    if from_spec is None:
        return [
            f"'{SPEC_FILENAME}' has no parseable '## Layout' JSON block - the manifest's "
            f"layout must come from the spec. Re-run 'tableau-spec' to regenerate it."
        ]

    spec_ids = placed_layout_ids(from_spec)
    manifest_layout = manifest_document.get("layout")
    manifest_ids = placed_layout_ids(manifest_layout)

    errors: list[str] = []
    dropped = sorted(spec_ids - manifest_ids)
    if dropped:
        errors.append(
            "layout drops zone(s) the spec places: " + ", ".join(dropped)
            + f" (copy the '## Layout' tree from {SPEC_FILENAME})"
        )
    invented = sorted(manifest_ids - spec_ids)
    if invented:
        errors.append(
            "layout places zone(s) the spec does not: " + ", ".join(invented)
            + f" (copy the '## Layout' tree from {SPEC_FILENAME})"
        )
    if errors:
        return errors

    # Same zones, but the geometry (canvas, sizes, nesting, orientation) must match too -
    # it is the mock's geometry the spec carried here for exactly this reason (§1.1).
    if not isinstance(manifest_layout, dict):
        return ["layout: must be the spec's layout object ('canvas' + 'root')"]
    if manifest_layout.get("canvas") != from_spec.get("canvas"):
        errors.append(
            f"layout canvas {manifest_layout.get('canvas')} differs from the spec's "
            f"{from_spec.get('canvas')} - copy the '## Layout' tree from {SPEC_FILENAME}"
        )
    if _normalized_node(manifest_layout.get("root")) != _normalized_node(
        from_spec.get("root")
    ):
        errors.append(
            f"layout geometry differs from the spec's approved tree (sizes, nesting, or "
            f"vert/horz orientation) - copy the '## Layout' tree from {SPEC_FILENAME} "
            f"verbatim rather than re-deriving it"
        )
    return errors


# --- validate ----------------------------------------------------------------

@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating the build manifest on disk.

    Attributes:
        ok: True when the manifest parses and every schema check passes.
        errors: The fail-fast messages, each naming the offending entry.
        version: The version directory the manifest was read from.
        manifest_path: Project-relative path of the manifest that was validated.
    """

    ok: bool
    errors: list[str]
    version: str
    manifest_path: str


def validate(project_dir: Path | str) -> ValidationResult:
    """Validate ``build-manifest.json`` at ``current_version``.

    Two checks: the schema (:func:`manifest.validate_manifest`, against ``DATA-MODEL.md``
    and STATE.md's target version) and the reconciliation against the approved spec
    (:func:`spec_layout_errors`, so the manifest carries the spec's container tree rather
    than a re-derived one).

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`ValidationResult`; ``ok`` is False when the entry gate is closed, the
        manifest is missing/unparseable, or any check fails.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return ValidationResult(False, [blocker], "v_1", "")

    text = (project_root / STATE_FILENAME).read_text(encoding="utf-8-sig")
    version = read_current_version(text)
    manifest_rel = f"{VERSION_DIR}/{version}/{MANIFEST_FILENAME}"

    document, load_error = load_manifest(project_root / manifest_rel)
    if document is None:
        return ValidationResult(False, [load_error or ""], version, manifest_rel)

    errors = validate_manifest(
        document,
        (project_root / DATA_MODEL_FILENAME).read_text(encoding="utf-8-sig"),
        read_target_tableau_version(text),
    )
    errors += spec_layout_errors(
        (project_root / VERSION_DIR / version / SPEC_FILENAME).read_text(
            encoding="utf-8-sig"
        ),
        document,
    )
    return ValidationResult(not errors, errors, version, manifest_rel)


# --- build (manifest -> validated .twbx) --------------------------------------

#: The 2026.1 XSD requires ``<explain-data>``, which a workbook targeting 2024.2-2025.x must
#: not carry. That single "missing child element" complaint is the documented version shift
#: and the only XSD error a build tolerates.
_VERSION_SHIFT_MARKERS = ("Missing child element", "explain-data")


def read_csv_header(csv_path: Path) -> list[str]:
    """Return a CSV's header row - the physical schema and its column order.

    Args:
        csv_path: Path to the CSV file.

    Returns:
        The column names in file order (empty for an empty file).
    """
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.reader(handle):
            return [name.strip() for name in row]
    return []


def create_twbx(twb_path: Path, csv_paths: list[Path]) -> Path:
    """Package a ``.twb`` and its CSVs into a flat ``.twbx`` beside it.

    Flat (no directory entries) is what ``directory='.'`` in the workbook's connection
    expects: Tableau unpacks the archive and finds each CSV next to the ``.twb``.

    Args:
        twb_path: The workbook XML file to package.
        csv_paths: The CSVs the workbook reads.

    Returns:
        The path of the written ``.twbx``.
    """
    twbx_path = twb_path.with_suffix(".twbx")
    with zipfile.ZipFile(twbx_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(twb_path, twb_path.name)
        for csv_path in csv_paths:
            archive.write(csv_path, csv_path.name)
    return twbx_path


@dataclass(frozen=True)
class BuildResult:
    """Outcome of assembling the workbook from the validated manifest.

    Attributes:
        ok: True when the workbook was built, validated, and packaged.
        errors: Why it was not; empty when ``ok``.
        version: The version directory built into.
        twb_path: Project-relative path of the workbook XML (written even on a validator
            failure, so the XML can be inspected).
        twbx_path: Project-relative path of the package; ``""`` when nothing was packaged.
        warnings: Non-fatal notes (a skipped XSD check, the documented version shift).
    """

    ok: bool
    errors: list[str]
    version: str
    twb_path: str = ""
    twbx_path: str = ""
    warnings: list[str] = field(default_factory=list)


def _semantic_errors(twb_path: Path) -> list[str]:
    """Run the migrated semantic validator and return one message per failed check.

    Args:
        twb_path: The workbook XML to check.

    Returns:
        ``"<check name>: <detail>"`` messages; empty when every check passes.
    """
    import validate_twb  # same scripts/ dir - already importable, no subprocess

    report = validate_twb.TwbValidator(str(twb_path)).validate()
    return [
        f"{result.name}: {detail}"
        for result in report.results if not result.passed
        for detail in (result.details or ["check failed"])
    ]


def _xsd_errors(twb_path: Path, target_tableau_version: str) -> tuple[list[str], list[str]]:
    """Run the migrated XSD validator, tolerating the documented version shift.

    Args:
        twb_path: The workbook XML to check.
        target_tableau_version: STATE.md's target (CONTRACT.md §2).

    Returns:
        ``(errors, warnings)``. For the 2026.1+ target every schema error is fatal; for
        2024.2-2025.x the single ``<explain-data>`` complaint is downgraded to a warning.
        When ``lxml`` is absent the check is skipped with a warning.
    """
    if importlib.util.find_spec("lxml") is None:
        return [], ["XSD check skipped: lxml is not installed (pip install lxml)."]

    import validate_twb_xsd  # imports lxml at module level - guarded above

    schema = validate_twb_xsd.load_schema(validate_twb_xsd.XSD_PATH)
    _, raw_errors = validate_twb_xsd.validate(twb_path, schema)
    messages = [f"line {error.line}: {error.message}" for error in raw_errors]
    if target_tableau_version.strip() == twb.TARGET_2026:
        return messages, []

    # At most *one* such error is the documented shift; a second is a real problem, so
    # forgive by position rather than by message text.
    forgiven = [
        index for index, message in enumerate(messages)
        if all(marker in message for marker in _VERSION_SHIFT_MARKERS)
    ][:1]
    fatal = [
        message for index, message in enumerate(messages) if index not in forgiven
    ]
    warnings = [
        f"XSD (expected for the {target_tableau_version} target): {messages[index]}"
        for index in forgiven
    ]
    return fatal, warnings


def build_workbook(project_dir: Path | str) -> BuildResult:
    """Assemble, validate, and package the workbook from the validated manifest.

    The manifest must validate first (:func:`validate`), so the assembler never sees an
    entry it cannot build. The XML then goes through both migrated validators before it is
    packaged: a workbook that fails either is left on disk unpackaged, for debugging.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`BuildResult`.
    """
    project_root = Path(project_dir)
    validation = validate(project_root)
    if not validation.ok:
        return BuildResult(False, validation.errors, validation.version)

    version = validation.version
    version_dir = project_root / VERSION_DIR / version
    document, _ = load_manifest(version_dir / MANIFEST_FILENAME)

    # Drop any previous package before anything else can fail: 'commit' approves on the
    # .twbx's existence, so a stale one left behind by a failed build would be approved.
    (version_dir / WORKBOOK_FILENAME).unlink(missing_ok=True)

    on_disk = {
        (project_root / relative).name: project_root / relative
        for relative in _sample_csvs(project_root)
    }
    headers = {name: read_csv_header(path) for name, path in on_disk.items()}
    wanted = [
        str(source.get("csv", "")).strip() for source in document.get("datasources", [])
    ]
    missing = sorted({name for name in wanted if name not in headers})
    if missing:
        return BuildResult(
            False,
            [
                f"datasource csv '{name}' is not on disk (found: "
                f"{', '.join(sorted(headers)) or 'none'}) - the workbook would bind to "
                f"nothing. Re-run 'tableau-data' or fix the manifest's 'csv'."
                for name in missing
            ],
            version,
        )

    twb_path = version_dir / TWB_FILENAME
    tokens_path = project_root / DESIGN_TOKENS_FILENAME
    twb_path.write_text(
        twb.render_workbook(
            document,
            (project_root / DATA_MODEL_FILENAME).read_text(encoding="utf-8-sig"),
            headers,
            # Optional read (CONTRACT.md §4.1): no branding step, no styling overrides.
            tokens_path.read_text(encoding="utf-8-sig") if tokens_path.exists() else "",
        ),
        encoding="utf-8",
    )
    twb_relative = f"{VERSION_DIR}/{version}/{TWB_FILENAME}"

    target = read_target_tableau_version(
        (project_root / STATE_FILENAME).read_text(encoding="utf-8-sig")
    )
    errors = _semantic_errors(twb_path)
    xsd_errors, warnings = _xsd_errors(twb_path, target)
    errors += xsd_errors
    if errors:
        # Leave the .twb for inspection, but never package a workbook that failed a check.
        return BuildResult(False, errors, version, twb_relative, warnings=warnings)

    # Only the CSVs the manifest actually binds to: an unrelated file in data/ has no
    # business shipping inside the analyst's deliverable.
    create_twbx(twb_path, [on_disk[name] for name in dict.fromkeys(wanted)])
    logger.info(f"Built {twb_relative} and its .twbx from the validated manifest.")
    return BuildResult(
        ok=True,
        errors=[],
        version=version,
        twb_path=twb_relative,
        twbx_path=f"{VERSION_DIR}/{version}/{WORKBOOK_FILENAME}",
        warnings=warnings,
    )


# --- commit ------------------------------------------------------------------

@dataclass
class CommitResult:
    """Outcome of committing the build step's result to STATE.md.

    Attributes:
        ok: True when STATE.md was updated; False when the commit was refused.
        message: Human-readable explanation (the refusal reason when not ``ok``).
        version: The version directory the (attempted) build lives in.
        errors: Manifest validation errors that refused the commit, when any.
    """

    ok: bool
    message: str
    version: str = "v_1"
    errors: list[str] = field(default_factory=list)


def commit(project_dir: Path | str) -> CommitResult:
    """Validate the manifest and approve the build step.

    Build is non-skippable, so commit only ever sets ``approved``, and it is the last step,
    so nothing downstream goes stale (§4.2). It writes into the mock's ``current_version``
    and never bumps it (§4.3), so a re-run overwrites the same ``v_N`` in place.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`CommitResult`. ``ok`` is False (STATE.md untouched) when the entry gate is
        closed or the manifest is missing / invalid.
    """
    project_root = Path(project_dir)
    validation = validate(project_root)
    if not validation.ok:
        if not validation.manifest_path:  # entry gate closed
            return CommitResult(False, validation.errors[0], validation.version)
        return CommitResult(
            False,
            f"Cannot approve build: '{validation.manifest_path}' is missing or does not "
            f"validate. Fix the entries named below and re-run commit.",
            version=validation.version,
            errors=validation.errors,
        )

    # The workbook is the deliverable, so approval requires one on disk. The validators ran
    # in 'build' and refused to package a workbook that failed them - no need to re-run.
    workbook_relative = f"{VERSION_DIR}/{validation.version}/{WORKBOOK_FILENAME}"
    if not (project_root / workbook_relative).exists():
        return CommitResult(
            False,
            f"Cannot approve build: '{workbook_relative}' does not exist. Run "
            f"'build.py build' to assemble and package the workbook first.",
            version=validation.version,
        )

    state_path = project_root / STATE_FILENAME
    text = state_path.read_text(encoding="utf-8-sig")
    state_path.write_text(
        apply_status_updates(text, {BUILD_STEP: "approved"}), encoding="utf-8"
    )

    logger.info(f"Set build -> approved at {validation.version} (current_version untouched).")
    return CommitResult(
        ok=True,
        message=f"build -> approved ({validation.version})",
        version=validation.version,
    )


# --- CLI ---------------------------------------------------------------------

def format_precheck(result: PrecheckResult) -> str:
    """Render a :class:`PrecheckResult` as a human-readable, plain-ASCII block."""
    # Plain ASCII only: this prints to a Windows cp1252 console, which would raise
    # UnicodeEncodeError on emoji / box-drawing glyphs.
    if not result.can_run:
        return f"[BLOCKED] tableau-build cannot run.\n{result.blocker}"

    lines = [
        "[BUILD] precheck OK - tableau-build can run.",
        f"  build from     : {result.spec_path}",
        f"  fields from    : {result.data_model_path}",
        f"  data           : {', '.join(result.csv_paths)}",
        f"  target version : {result.target_tableau_version}",
        f"  write manifest : {result.manifest_path}",
        f"  workbook       : {result.workbook_path}",
    ]
    if result.manifest_exists:
        lines.append(
            "    -> a build-manifest.json already exists at this version; refine it in place."
        )
    lines.append(
        "  the manifest is the builder's only input: datasources, worksheets "
        "(chart type + shelves/encodings), the spec's layout tree, actions, parameters."
    )
    return "\n".join(lines)


def format_validation(result: ValidationResult) -> str:
    """Render a :class:`ValidationResult` as the fail-fast manifest report."""
    # Plain ASCII only (see format_precheck).
    if result.ok:
        return f"[OK] {result.manifest_path} validates - every entry is buildable."
    if not result.manifest_path:  # the entry gate is closed, not a bad manifest
        return f"[BLOCKED] tableau-build cannot run.\n{result.errors[0]}"
    lines = [f"[INVALID] {result.manifest_path}:"]
    lines += [f"  - {error}" for error in result.errors]
    lines.append("Fix the entries named above and re-run.")
    return "\n".join(lines)


def format_build(result: BuildResult) -> str:
    """Render a :class:`BuildResult` as the assembly report."""
    # Plain ASCII only (see format_precheck).
    lines: list[str]
    if result.ok:
        lines = [
            f"[BUILT] {result.twbx_path}",
            f"  workbook xml : {result.twb_path} (semantic + XSD validated)",
        ]
    else:
        lines = ["[INVALID] the workbook did not build:"]
        lines += [f"  - {error}" for error in result.errors]
        if result.twb_path:
            lines.append(f"  the XML is at {result.twb_path} for inspection; nothing packaged.")
    lines += [f"  [WARN] {warning}" for warning in result.warnings]
    return "\n".join(lines)


def format_commit(result: CommitResult) -> str:
    """Render a :class:`CommitResult` as a human-readable, plain-ASCII block."""
    # Plain ASCII only (see format_precheck).
    if not result.ok:
        text = f"[REFUSED] {result.message}"
        for error in result.errors:
            text += f"\n  - {error}"
        return text

    return "\n".join([
        f"[BUILD] {result.message}.",
        f"  manifest: {VERSION_DIR}/{result.version}/{MANIFEST_FILENAME} (validated)",
        f"  deliverable: {VERSION_DIR}/{result.version}/{WORKBOOK_FILENAME}",
        "  the pipeline is complete - run 'tableau-route' to confirm.",
    ])


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point: run ``precheck``, ``validate``, or ``commit`` and print it.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``2`` when build is blocked/refused/invalid
        or on a usage error.
    """
    parser = argparse.ArgumentParser(
        description="Gate, validate, and commit the tableau-build step.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("precheck", "Report whether build may run, what it reads, and where it writes."),
        ("validate", "Schema-check the build manifest against DATA-MODEL.md and the layout."),
        ("build", "Assemble, validate, and package the workbook from the manifest."),
        ("commit", "Approve the build in STATE.md (never bumps current_version)."),
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
        validation = validate(project_dir)
        print(format_validation(validation))
        return 0 if validation.ok else 2

    if args.command == "build":
        build_result = build_workbook(project_dir)
        print(format_build(build_result))
        return 0 if build_result.ok else 2

    commit_result = commit(project_dir)
    print(format_commit(commit_result))
    return 0 if commit_result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
