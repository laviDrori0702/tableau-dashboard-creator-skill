"""Gate, profile, validate, and commit the ``data`` step of tableau-dashboard-plugin.

This is the executable core of the ``tableau-data`` skill (CONTRACT.md step 3),
the non-skippable step that turns analyst-provided data into the canonical handoff
artifact ``DATA-MODEL.md``. The CSVs under ``data/`` are the single source of truth
for the field names that ``tableau-mock`` and ``tableau-build`` consume, so this
script owns the things that must be **mechanically guaranteed** rather than left to
model prose:

1. **Entry gate** - data refuses to run until ``init`` is ``approved`` in
   ``STATE.md`` (and ``STATE.md`` exists at all). This mirrors the ordering rule in
   CONTRACT.md §4.1: a step does not run before its prerequisites are resolved.
   ``data`` has no producer-gated *required reads* (CONTRACT.md §1), so init-approved
   is its only precondition.
2. **CSV profiling** - reading the provided CSVs and inferring a Tableau-friendly
   type per column is deterministic work, so the script does it (``profile`` writes a
   schema-complete ``DATA-MODEL.md`` field table the model then enriches with prose).
3. **Header <-> model validation** - on approval the documented field names in
   ``DATA-MODEL.md`` must match the real CSV headers on disk **exactly** (case
   included). A typo or casing drift is *reported, never silently accepted*, because
   a mismatch here would break Replace Data Source downstream (CONTRACT.md §3.2).
4. **STATE.md transition** - committing flips ``data`` to ``approved`` and propagates
   staleness (CONTRACT.md §4.2): every downstream ``approved`` step becomes ``stale``
   so the pipeline can never silently disagree with changed data.

There are exactly **two** data-acquisition routes (CONTRACT.md §3.2): Route 1 -
``data_mode: csv`` (the default) and Route 2 - ``published-ds`` (VizQL Data Service).
This module implements **both**: Route 1 by profiling analyst-provided CSVs
(``profile``), and Route 2 by sampling published sources through the VizQL Data Service
(``pull``), with the network half delegated to the sibling :mod:`vds` module
(CONTRACT.md §7). There is deliberately **no** synthesized/random data path: the
guaranteed floor is the ``scaffold/sample-data/`` demo CSVs (CONTRACT.md §3.1),
surfaced as a clearly-labelled demo - never invented rows.

The module's stdlib-only core (gate, profiling, validation, STATE.md rewriting) is kept
importable without third-party packages so the contract test can call those functions
directly, exactly like ``init.py`` / ``intake.py``. The published-ds ``pull`` path needs
``requests`` (via :mod:`vds`), so :mod:`vds` is imported **locally inside** :func:`pull`
rather than at module top - importing ``data`` never requires ``requests``. The CLI
exposes four subcommands the skill runs at four moments - ``precheck`` (before
authoring), ``profile`` (csv route field tables), ``pull`` (published-ds route sample),
and ``commit`` (after approval). What the CLI prints to stdout is the program's *output*;
diagnostics go through ``logging``.

Keep ``STEP_ORDER`` below in lock-step with the ordered step list in CONTRACT.md §1.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Canonical constants (mirror of CONTRACT.md §1 / §2 / §3) ----------------

STATE_FILENAME = "STATE.md"
DATA_MODEL_FILENAME = "DATA-MODEL.md"

#: This step, and the upstream step whose approval gates it (CONTRACT.md §4.1).
DATA_STEP = "data"
INIT_STEP = "init"

#: The 8 step names in canonical order (mirror of CONTRACT.md §1). Used to decide
#: which steps are "downstream of data" for staleness propagation (§4.2).
STEP_ORDER: tuple[str, ...] = (
    "init", "intake", "data", "brand", "plan", "mock", "spec", "build",
)

#: Route 1 (csv): production CSVs (preferred) and the scaffold/ demo fallback (§3.1).
DATA_DIR = "data"
SCAFFOLD_DATA_DIR = "scaffold/sample-data"

#: Route 2 (published-ds): the inputs the VDS pull reads (CONTRACT.md §3.2).
DATASOURCES_FILENAME = "datasources.json"
ENV_FILENAME = ".env"

#: STATE.md ``data_mode`` metadata values (CONTRACT.md §2). A successful ``pull`` flips
#: the recorded mode to ``published-ds``; ``profile`` leaves the default ``csv``.
DATA_MODE_CSV = "csv"
DATA_MODE_PUBLISHED_DS = "published-ds"

#: Row-limit policy for the VDS sample (CONTRACT.md §3.2): default 100, silent up to
#: 1000, and a value above 1000 needs explicit analyst confirmation before the pull.
DEFAULT_ROW_LIMIT = 100
MAX_SILENT_ROW_LIMIT = 1000

#: The Tableau-friendly column types ``profile`` infers and ``DATA-MODEL.md`` records.
#: Ordered narrowest-first; that order is the inference precedence in ``infer_type``.
TYPE_BOOLEAN = "boolean"
TYPE_INTEGER = "integer"
TYPE_REAL = "real"
TYPE_DATE = "date"
TYPE_DATETIME = "datetime"
TYPE_STRING = "string"
TYPES: tuple[str, ...] = (
    TYPE_BOOLEAN, TYPE_INTEGER, TYPE_REAL, TYPE_DATE, TYPE_DATETIME, TYPE_STRING,
)

#: Acquisition tiers recorded in DATA-MODEL.md (CONTRACT.md §3.2). The csv route
#: produces one of the first two; the published-ds route (``pull``) records the third.
TIER_CSV_PROVIDED = "csv (provided in data/)"
TIER_CSV_DEMO = "csv (demo - scaffold/sample-data/)"
TIER_PUBLISHED_DS = "published-ds (VDS query)"

#: How many data rows ``profile`` reads to infer types and gather sample values.
PROFILE_SAMPLE_ROWS = 200
#: How many distinct sample values to show per field in DATA-MODEL.md.
SAMPLE_VALUES_SHOWN = 3

#: Accepted strict date / datetime formats for type inference (no locale guessing).
_DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%Y/%m/%d")
_DATETIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
)
#: Literal tokens (lower-cased) that count as boolean values.
_BOOLEAN_TOKENS = frozenset({"true", "false"})


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


# --- Type inference ----------------------------------------------------------

def _all_match(values: list[str], predicate) -> bool:
    """Return True if ``predicate`` holds for every non-empty value.

    Empty/whitespace-only cells are treated as missing and never veto a type, so a
    column with a few blanks still infers its real type. An all-empty column has no
    evidence for any narrow type and is handled by the caller (falls through to
    string).

    Args:
        values: The raw cell strings for one column.
        predicate: A ``str -> bool`` test for a single non-empty value.

    Returns:
        True iff at least one non-empty value exists and all satisfy ``predicate``.
    """
    non_empty = [value for value in values if value.strip() != ""]
    return bool(non_empty) and all(predicate(value.strip()) for value in non_empty)


def _is_integer(value: str) -> bool:
    """bool: True if ``value`` is a base-10 integer (optionally signed)."""
    text = value.lstrip("+-")
    return text.isdigit() and text != ""


def _is_real(value: str) -> bool:
    """bool: True if ``value`` parses as a float (covers ints, decimals, exp)."""
    try:
        float(value)
        return True
    except ValueError:
        return False


def _matches_any_format(value: str, formats: tuple[str, ...]) -> bool:
    """bool: True if ``value`` parses under at least one ``strptime`` format."""
    for fmt in formats:
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


def infer_type(values: list[str]) -> str:
    """Infer the Tableau-friendly type of a column from its values.

    Inference is conservative and narrowest-first: a column is only given a narrow
    type when **every** non-empty value fits it. The order (boolean, integer, real,
    date, datetime) means ``1``/``0`` columns become integers (not booleans) and
    whole-number columns become integers (not reals). Anything that does not fit a
    narrow type - or an all-empty column - is ``string``.

    Args:
        values: The raw cell strings for one column (header excluded).

    Returns:
        One of :data:`TYPES`.
    """
    if _all_match(values, lambda value: value.lower() in _BOOLEAN_TOKENS):
        return TYPE_BOOLEAN
    if _all_match(values, _is_integer):
        return TYPE_INTEGER
    if _all_match(values, _is_real):
        return TYPE_REAL
    if _all_match(values, lambda value: _matches_any_format(value, _DATE_FORMATS)):
        return TYPE_DATE
    if _all_match(values, lambda value: _matches_any_format(value, _DATETIME_FORMATS)):
        return TYPE_DATETIME
    return TYPE_STRING


# --- CSV profiling -----------------------------------------------------------

@dataclass(frozen=True)
class FieldProfile:
    """One column of a profiled CSV.

    Attributes:
        name: The column header exactly as it appears in the CSV (the documented
            field name; case is significant).
        type: The inferred type, one of :data:`TYPES`.
        role: A suggested Tableau role (``Measure`` for numeric, else ``Dimension``);
            a starting point the model may refine.
        samples: A few distinct example values, for the analyst's eyes.
        description: A field description. Blank (``""``) for the csv route - the model
            fills it in. For the published-ds route it is pre-filled from authoritative
            VDS metadata where available (CONTRACT.md §3.2).
    """

    name: str
    type: str
    role: str
    samples: list[str]
    description: str = ""


@dataclass(frozen=True)
class CsvProfile:
    """A profiled CSV file (one data source - CONTRACT.md §3.2 "csv = datasource").

    Attributes:
        filename: The CSV's base name (e.g. ``sales_orders.csv``); the data-source key.
        row_count: Number of data rows read (capped at :data:`PROFILE_SAMPLE_ROWS`).
        fields: One :class:`FieldProfile` per column, in file order.
    """

    filename: str
    row_count: int
    fields: list[FieldProfile]


def _suggest_role(column_type: str) -> str:
    """str: Suggest a Tableau role from a column type (numeric -> Measure)."""
    return "Measure" if column_type in (TYPE_INTEGER, TYPE_REAL) else "Dimension"


def _distinct_samples(values: list[str], limit: int) -> list[str]:
    """Return up to ``limit`` distinct non-empty values, preserving first-seen order.

    Args:
        values: The raw cell strings for one column.
        limit: Maximum number of samples to return.

    Returns:
        Distinct, non-empty sample values (order-preserving), at most ``limit``.
    """
    seen: list[str] = []
    for value in values:
        stripped = value.strip()
        if stripped and stripped not in seen:
            seen.append(stripped)
            if len(seen) >= limit:
                break
    return seen


def profile_csv(csv_path: Path | str) -> CsvProfile:
    """Read a CSV and profile each column's type, role, and sample values.

    Only the first :data:`PROFILE_SAMPLE_ROWS` data rows are read - enough to infer
    types reliably without loading large files. The header row supplies the field
    names exactly (case preserved), since those names are the contract downstream
    steps build against.

    Args:
        csv_path: Path to the CSV file to profile.

    Returns:
        A :class:`CsvProfile`.

    Raises:
        FileNotFoundError: If ``csv_path`` does not exist.
        ValueError: If the file has no header row.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path}")

    # utf-8-sig tolerates an Excel-exported BOM on the first header cell.
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(f"CSV '{path.name}' is empty (no header row).")

        columns: list[list[str]] = [[] for _ in header]
        row_count = 0
        for row in reader:
            if row_count >= PROFILE_SAMPLE_ROWS:
                break
            row_count += 1
            for index in range(len(header)):
                # A short row leaves trailing columns missing; treat as empty.
                columns[index].append(row[index] if index < len(row) else "")

    fields = []
    for name, cells in zip(header, columns):
        column_type = infer_type(cells)
        fields.append(FieldProfile(
            name=name,
            type=column_type,
            role=_suggest_role(column_type),
            samples=_distinct_samples(cells, SAMPLE_VALUES_SHOWN),
        ))
    return CsvProfile(filename=path.name, row_count=row_count, fields=fields)


# --- DATA-MODEL.md rendering -------------------------------------------------

def _md_table_cell(text: str) -> str:
    """Make free-text safe for a single GitHub-flavored-markdown table cell.

    Real data (especially VDS field descriptions and sample values) can contain
    newlines and ``|`` pipes - both of which corrupt a markdown table: a newline splits
    the row across physical lines, and a pipe is read as a column separator. Newlines
    (and carriage returns) are collapsed to spaces and pipes are backslash-escaped so
    the value stays in one cell on one row.

    This is applied to the Sample-values and Description cells only - never to the
    Field name (which :func:`parse_data_model` reads back and validates against the CSV
    header verbatim).

    Args:
        text: The raw cell text.

    Returns:
        The text rendered safe for one markdown table cell.
    """
    return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def render_data_model(profiles: list[CsvProfile], tier: str) -> str:
    """Render the DATA-MODEL.md handoff artifact from profiled CSVs.

    The output is schema-complete and machine-re-parseable (:func:`parse_data_model`
    recovers the field names + types): a top section recording the acquisition tier
    (CONTRACT.md §3.2) followed by one section per data source, each with a field
    table. The Description column is left blank for the model to fill with judgment
    (what each field means); everything else is mechanically derived.

    Args:
        profiles: The profiled CSVs to document, in presentation order.
        tier: The acquisition tier to record (e.g. :data:`TIER_CSV_PROVIDED`).

    Returns:
        The complete DATA-MODEL.md contents, terminated by a trailing newline.
    """
    # Build line-by-line so each table (header + separator + rows) stays contiguous:
    # a blank line between the separator and the rows would split the table in
    # GitHub-flavored markdown.
    lines = [
        "# Data Model",
        "",
        "> Managed by tableau-dashboard-plugin (tableau-data). "
        "See CONTRACT.md before hand-editing.",
        "",
        "## Acquisition",
        "",
        f"- tier: {tier}",
        "- Each CSV under `data/` is one data source (CONTRACT.md §3.2). "
        "Documented field names below must match the CSV headers exactly so "
        "**Replace Data Source** can swap in live data later.",
    ]

    for profile in profiles:
        lines += [
            "",
            f"## Data source: `{profile.filename}`",
            "",
            f"- rows profiled: {profile.row_count}",
            "",
            "| Field | Type | Role | Sample values | Description |",
            "|-------|------|------|---------------|-------------|",
        ]
        for field_profile in profile.fields:
            samples = _md_table_cell(", ".join(field_profile.samples))
            description = _md_table_cell(field_profile.description)
            lines.append(
                f"| {field_profile.name} | {field_profile.type} | "
                f"{field_profile.role} | {samples} | {description} |"
            )

    return "\n".join(lines) + "\n"


# --- DATA-MODEL.md parsing (the validator's other half) ----------------------

# A data-source section heading, capturing the CSV filename token inside backticks
# or bare: "## Data source: `sales.csv`" -> "sales.csv".
_DATASOURCE_HEADING = re.compile(
    r"^#{1,6}\s+data source:\s*`?([^`\s|]+\.csv)`?\s*$", re.IGNORECASE
)
# The acquisition tier line: "- tier: csv (provided in data/)".
_TIER_LINE = re.compile(r"^-\s*tier\s*:\s*(.+?)\s*$", re.IGNORECASE)


def _looks_like_field_header(cells: list[str]) -> bool:
    """bool: True if a table row is the ``| Field | Type | ... |`` header."""
    return (
        len(cells) >= 2
        and cells[0].lower() == "field"
        and cells[1].lower() == "type"
    )


def _is_separator_row(cells: list[str]) -> bool:
    """bool: True if a table row is the ``|---|---|`` separator."""
    return all(set(cell) <= set("-: ") and cell for cell in cells)


def parse_data_model(text: str) -> dict[str, list[tuple[str, str]]]:
    """Recover the documented (field name, type) pairs per data source.

    Scans for ``## Data source: `name.csv``` headings and, within each, reads the
    field table's first two columns (Field, Type), skipping the header and separator
    rows. This is the inverse of :func:`render_data_model`; it is also what
    :func:`validate_headers` checks the real CSV headers against, so it must keep
    working even after the model edits the Description/Role columns.

    Args:
        text: The contents of a ``DATA-MODEL.md`` file.

    Returns:
        ``{csv_filename: [(field_name, type), ...]}`` in document order. Field names
        keep their exact case (the comparison downstream is case-sensitive).
    """
    documented: dict[str, list[tuple[str, str]]] = {}
    current: Optional[str] = None
    in_field_table = False

    for raw_line in text.splitlines():
        line = raw_line.strip()

        heading_match = _DATASOURCE_HEADING.match(line)
        if heading_match:
            current = heading_match.group(1)
            documented.setdefault(current, [])
            in_field_table = False
            continue

        if line.startswith("## "):  # a non-data-source section ends the current one
            current = None
            in_field_table = False
            continue

        if current is None or not line.startswith("|"):
            continue

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if _looks_like_field_header(cells):
            in_field_table = True
            continue
        if not in_field_table or _is_separator_row(cells):
            continue
        if len(cells) >= 2 and cells[0]:
            documented[current].append((cells[0], cells[1].lower()))

    return documented


def parse_acquisition_tier(text: str) -> Optional[str]:
    """Return the acquisition tier recorded in a DATA-MODEL.md, or None if absent.

    Args:
        text: The contents of a ``DATA-MODEL.md`` file.

    Returns:
        The tier string (e.g. ``"csv (provided in data/)"``) or ``None``.
    """
    for raw_line in text.splitlines():
        match = _TIER_LINE.match(raw_line.strip())
        if match:
            return match.group(1)
    return None


# --- Header <-> model validation ---------------------------------------------

@dataclass(frozen=True)
class HeaderCheck:
    """The result of comparing a CSV's real headers to its documented field names.

    Attributes:
        ok: True iff the documented field names match the CSV headers exactly
            (same names, same case; order is not enforced).
        missing: Field names documented in DATA-MODEL.md but absent from the CSV
            header (a deletion or a typo/casing drift in the doc).
        extra: Headers present in the CSV but not documented (an undocumented
            column, or the other side of a typo/casing drift).
    """

    ok: bool
    missing: list[str]
    extra: list[str]


def validate_headers(actual: list[str], documented: list[str]) -> HeaderCheck:
    """Compare a CSV's real headers to its documented field names, case-sensitively.

    The match is **exact**: ``Region`` and ``region`` are different fields, so a
    casing drift or a typo surfaces as a ``missing``/``extra`` pair rather than being
    silently accepted (CONTRACT.md §3.2 - the names are the Replace-Data-Source
    contract). Order is not enforced; duplicates are de-duplicated for reporting.

    Args:
        actual: The header cells read from the CSV file.
        documented: The field names recorded in DATA-MODEL.md.

    Returns:
        A :class:`HeaderCheck`. ``ok`` is True iff both ``missing`` and ``extra``
        are empty.
    """
    actual_set = set(actual)
    documented_set = set(documented)
    missing = [name for name in documented if name not in actual_set]
    extra = [name for name in actual if name not in documented_set]
    # De-duplicate while preserving order (a column could legitimately repeat).
    missing = list(dict.fromkeys(missing))
    extra = list(dict.fromkeys(extra))
    return HeaderCheck(ok=(not missing and not extra), missing=missing, extra=extra)


def read_csv_header(csv_path: Path | str) -> list[str]:
    """Read just the header row of a CSV file.

    Args:
        csv_path: Path to the CSV file.

    Returns:
        The header cells (case preserved).

    Raises:
        FileNotFoundError: If ``csv_path`` does not exist.
        ValueError: If the file has no header row.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        try:
            return next(csv.reader(handle))
        except StopIteration:
            raise ValueError(f"CSV '{path.name}' is empty (no header row).")


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
        mode: The new mode to record (e.g. :data:`DATA_MODE_PUBLISHED_DS`).

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


def _downstream_stale_updates(statuses: dict[str, str]) -> dict[str, str]:
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


# --- Data-source resolution (CONTRACT.md §3.1 / §3.2) ------------------------

def _list_csvs(directory: Path) -> list[Path]:
    """Return the CSV files directly under ``directory``, sorted by name."""
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.csv"))


def resolve_csv_source(project_root: Path) -> tuple[str, list[Path]]:
    """Pick the CSV source, preferring production over the demo fallback (§3.1).

    Args:
        project_root: The analyst's project directory.

    Returns:
        A ``(source, csv_paths)`` tuple. ``source`` is ``"data"`` (production CSVs
        in ``data/``), ``"scaffold"`` (the demo ``scaffold/sample-data/`` fallback),
        or ``"none"`` (no CSVs anywhere). ``csv_paths`` is empty when ``"none"``.
    """
    provided = _list_csvs(project_root / DATA_DIR)
    if provided:
        return "data", provided
    demo = _list_csvs(project_root / SCAFFOLD_DATA_DIR)
    if demo:
        return "scaffold", demo
    return "none", []


def _tier_for_source(source: str) -> str:
    """str: The acquisition tier label for a resolved CSV source."""
    return TIER_CSV_DEMO if source == "scaffold" else TIER_CSV_PROVIDED


def _has_non_csv_data_files(project_root: Path) -> bool:
    """bool: True if ``data/`` holds files but none are CSV (e.g. only .xlsx)."""
    data_dir = project_root / DATA_DIR
    if not data_dir.is_dir():
        return False
    files = [item for item in data_dir.iterdir() if item.is_file()]
    return bool(files) and not any(item.suffix.lower() == ".csv" for item in files)


# --- precheck ----------------------------------------------------------------

@dataclass(frozen=True)
class PrecheckResult:
    """The state data needs to know before profiling.

    Attributes:
        can_run: True when the entry gate is open (init approved, STATE.md present).
        blocker: Why data cannot run, when ``can_run`` is False; else ``None``.
        csv_source: ``"data"`` | ``"scaffold"`` | ``"none"`` (CONTRACT.md §3.1).
        csv_files: Base names of the CSVs at the resolved source.
        has_datasources_json: Whether ``datasources.json`` (Route 2) is present.
        has_env: Whether ``.env`` (Route 2 creds) is present.
        has_non_csv_data: ``data/`` holds non-CSV files only (e.g. an .xlsx export).
        data_model_exists: Whether a ``DATA-MODEL.md`` already exists.
        data_status: The data step's current status (to detect a re-run).
    """

    can_run: bool
    blocker: Optional[str]
    csv_source: str
    csv_files: list[str]
    has_datasources_json: bool
    has_env: bool
    has_non_csv_data: bool
    data_model_exists: bool
    data_status: str


def precheck(project_dir: Path | str) -> PrecheckResult:
    """Report whether data may run and which acquisition route is available.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`PrecheckResult`. When the entry gate is closed, ``can_run`` is
        False and ``blocker`` explains why; the other fields are placeholders.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return PrecheckResult(
            False, blocker, "none", [], False, False, False, False, "unknown",
        )

    source, csv_paths = resolve_csv_source(project_root)
    statuses = parse_statuses((project_root / STATE_FILENAME).read_text(encoding="utf-8-sig"))
    return PrecheckResult(
        can_run=True,
        blocker=None,
        csv_source=source,
        csv_files=[path.name for path in csv_paths],
        has_datasources_json=(project_root / DATASOURCES_FILENAME).exists(),
        has_env=(project_root / ENV_FILENAME).exists(),
        has_non_csv_data=_has_non_csv_data_files(project_root),
        data_model_exists=(project_root / DATA_MODEL_FILENAME).exists(),
        data_status=statuses.get(DATA_STEP, "pending"),
    )


# --- profile -----------------------------------------------------------------

@dataclass
class ProfileResult:
    """Outcome of profiling the resolved CSVs into DATA-MODEL.md.

    Attributes:
        ok: True when DATA-MODEL.md was written; False when refused.
        message: Human-readable explanation (the refusal reason when not ``ok``).
        tier: The acquisition tier recorded, when written.
        profiled: Base names of the CSVs profiled.
        is_demo: True when the source was the scaffold/ demo fallback (§3.1).
    """

    ok: bool
    message: str
    tier: Optional[str] = None
    profiled: list[str] = field(default_factory=list)
    is_demo: bool = False


def profile(project_dir: Path | str, force: bool = False) -> ProfileResult:
    """Profile the resolved CSVs and write DATA-MODEL.md.

    Reads the production ``data/*.csv`` (or the ``scaffold/sample-data/`` demo
    fallback), infers a type per column, and writes a schema-complete DATA-MODEL.md
    the model then enriches with field descriptions. Non-destructive by default: an
    existing DATA-MODEL.md is preserved unless ``force`` is set, so the model's
    enrichments are never silently clobbered (it should ``Edit`` instead).

    Args:
        project_dir: The analyst's project directory.
        force: Overwrite an existing DATA-MODEL.md (e.g. after the CSVs changed).

    Returns:
        A :class:`ProfileResult`. ``ok`` is False (DATA-MODEL.md untouched) when the
        gate is closed, no CSVs exist, or one exists and ``force`` is not set.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return ProfileResult(False, blocker)

    source, csv_paths = resolve_csv_source(project_root)
    if source == "none":
        return ProfileResult(
            False,
            "No CSVs found in 'data/' or 'scaffold/sample-data/'. Provide CSV "
            "file(s) in 'data/', or use the published-ds route (datasources.json + "
            ".env). There is no synthesized-data path (CONTRACT.md §3.2).",
        )

    data_model_path = project_root / DATA_MODEL_FILENAME
    if data_model_path.exists() and not force:
        return ProfileResult(
            False,
            f"'{DATA_MODEL_FILENAME}' already exists. Edit it in place to refine, or "
            f"re-run 'profile --force' to regenerate from the CSVs (this discards "
            f"prior field descriptions).",
        )

    profiles = [profile_csv(path) for path in csv_paths]
    tier = _tier_for_source(source)
    data_model_path.write_text(render_data_model(profiles, tier), encoding="utf-8")
    logger.info(
        f"Wrote {DATA_MODEL_FILENAME} from {len(profiles)} CSV(s); tier='{tier}'."
    )
    return ProfileResult(
        ok=True,
        message=f"wrote {DATA_MODEL_FILENAME} ({len(profiles)} data source(s))",
        tier=tier,
        profiled=[profile_obj.filename for profile_obj in profiles],
        is_demo=(source == "scaffold"),
    )


# --- commit ------------------------------------------------------------------

@dataclass
class CommitResult:
    """Outcome of committing the data step's result to STATE.md.

    Attributes:
        ok: True when STATE.md was updated; False when the commit was refused.
        message: Human-readable explanation (the refusal reason when not ``ok``).
        staled_steps: Downstream steps flipped to ``stale`` by this commit.
        validated: Base names of CSV data sources whose headers matched the doc.
        mismatches: ``{csv_filename: HeaderCheck}`` for data sources whose headers
            did not match (only populated on a validation refusal).
    """

    ok: bool
    message: str
    staled_steps: list[str] = field(default_factory=list)
    validated: list[str] = field(default_factory=list)
    mismatches: dict[str, HeaderCheck] = field(default_factory=dict)


def _resolve_documented_csv(project_root: Path, filename: str) -> Optional[Path]:
    """Find a documented CSV on disk: production ``data/`` then scaffold demo (§3.1).

    Args:
        project_root: The analyst's project directory.
        filename: The CSV base name documented in DATA-MODEL.md.

    Returns:
        The path to the CSV, or ``None`` if it exists at neither location.
    """
    for directory in (DATA_DIR, SCAFFOLD_DATA_DIR):
        candidate = project_root / directory / filename
        if candidate.is_file():
            return candidate
    return None


def commit(project_dir: Path | str) -> CommitResult:
    """Validate headers against DATA-MODEL.md and record data as approved.

    The data step is non-skippable (CONTRACT.md §1), so the only commit status is
    ``approved``. Before approving, every documented data source's field names must
    match its real CSV header **exactly** (CONTRACT.md §3.2); any mismatch refuses
    the commit and leaves STATE.md untouched so the model fixes the drift. On success
    every downstream ``approved`` step is flipped to ``stale`` (CONTRACT.md §4.2).

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`CommitResult`. ``ok`` is False (STATE.md untouched) when the gate
        is closed, DATA-MODEL.md is missing, or any header check fails.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return CommitResult(False, blocker)

    data_model_path = project_root / DATA_MODEL_FILENAME
    if not data_model_path.exists():
        return CommitResult(
            False,
            f"Cannot approve data: '{DATA_MODEL_FILENAME}' does not exist. Run "
            f"'profile' to generate it first.",
        )

    documented = parse_data_model(data_model_path.read_text(encoding="utf-8-sig"))
    if not documented:
        return CommitResult(
            False,
            f"'{DATA_MODEL_FILENAME}' documents no data sources. It must have at "
            f"least one '## Data source: `name.csv`' section with a field table.",
        )

    validated: list[str] = []
    mismatches: dict[str, HeaderCheck] = {}
    for filename, fields in documented.items():
        csv_path = _resolve_documented_csv(project_root, filename)
        if csv_path is None:
            mismatches[filename] = HeaderCheck(
                ok=False, missing=[name for name, _ in fields], extra=[],
            )
            continue
        check = validate_headers(read_csv_header(csv_path), [name for name, _ in fields])
        if check.ok:
            validated.append(filename)
        else:
            mismatches[filename] = check

    if mismatches:
        return CommitResult(
            False,
            _format_mismatch_message(mismatches),
            validated=validated,
            mismatches=mismatches,
        )

    state_path = project_root / STATE_FILENAME
    text = state_path.read_text(encoding="utf-8-sig")
    statuses = parse_statuses(text)
    stale_updates = _downstream_stale_updates(statuses)
    updates = {DATA_STEP: "approved", **stale_updates}
    state_path.write_text(apply_status_updates(text, updates), encoding="utf-8")

    staled_steps = sorted(stale_updates, key=STEP_ORDER.index)
    logger.info(f"Set data -> approved; marked stale: {staled_steps or 'none'}.")
    return CommitResult(
        ok=True,
        message="data -> approved",
        staled_steps=staled_steps,
        validated=validated,
    )


def _format_mismatch_message(mismatches: dict[str, HeaderCheck]) -> str:
    """Render a header-mismatch refusal naming each offending data source.

    Args:
        mismatches: ``{csv_filename: HeaderCheck}`` for failing data sources.

    Returns:
        A single human-readable refusal message.
    """
    parts = [
        f"Header <-> model mismatch in {len(mismatches)} data source(s); "
        f"DATA-MODEL.md field names must match the CSV headers exactly."
    ]
    for filename, check in mismatches.items():
        details = []
        if check.missing:
            details.append(f"documented but not in CSV: {', '.join(check.missing)}")
        if check.extra:
            details.append(f"in CSV but not documented: {', '.join(check.extra)}")
        parts.append(f"  {filename}: {'; '.join(details) or 'CSV not found on disk'}")
    return "\n".join(parts)


# --- pull (Route 2: published-ds via VizQL Data Service, CONTRACT.md §3.2) ----

@dataclass
class PullResult:
    """Outcome of sampling published sources via VDS into data/ + DATA-MODEL.md.

    Attributes:
        ok: True when the CSVs and DATA-MODEL.md were written; False when refused or
            the pull failed (in which case nothing was written - STATE.md untouched).
        message: Human-readable explanation (the refusal/failure reason when not ``ok``).
        tier: The acquisition tier recorded, when written (:data:`TIER_PUBLISHED_DS`).
        written: Base names of the ``data/<slug>.csv`` files written.
        row_counts: ``{csv_filename: rows_pulled}`` for each written source.
        row_limit: The row cap applied to the sample.
    """

    ok: bool
    message: str
    tier: Optional[str] = None
    written: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    row_limit: int = 0


def _csv_cell(value) -> str:
    """Serialize a VDS cell value for the pulled CSV.

    Booleans are lower-cased (``true``/``false``) so the written CSV round-trips through
    type inference consistently; ``None`` becomes empty; everything else is ``str()``.

    Args:
        value: A JSON value from a VDS query row.

    Returns:
        The cell's string form for the CSV.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _profile_pulled_source(slug: str, fields, rows: list[dict]) -> CsvProfile:
    """Build a :class:`CsvProfile` from VDS metadata + sampled rows.

    Types and descriptions come from the authoritative VDS metadata (CONTRACT.md §3.2);
    only the sample values are derived from the pulled rows. The result renders through
    the same :func:`render_data_model` the csv route uses.

    Args:
        slug: The data-source slug (the CSV base name without extension).
        fields: The ``vds.FieldMeta`` list for this source (metadata order).
        rows: The sampled rows (dicts keyed by field caption).

    Returns:
        A :class:`CsvProfile` for the source.
    """
    field_profiles: list[FieldProfile] = []
    for meta in fields:
        column = [_csv_cell(row.get(meta.caption)) for row in rows]
        field_profiles.append(FieldProfile(
            name=meta.caption,
            type=meta.model_type,
            role=_suggest_role(meta.model_type),
            samples=_distinct_samples(column, SAMPLE_VALUES_SHOWN),
            description=meta.description,
        ))
    return CsvProfile(filename=f"{slug}.csv", row_count=len(rows), fields=field_profiles)


def _write_pulled_csv(csv_path: Path, captions: list[str], rows: list[dict]) -> None:
    """Write a pulled sample to ``data/<slug>.csv`` (headers = field captions).

    Args:
        csv_path: Destination path (its parent ``data/`` is created if needed).
        captions: The field captions, in metadata order (the CSV header).
        rows: The sampled rows (dicts keyed by caption).
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(captions)
        for row in rows:
            writer.writerow([_csv_cell(row.get(caption)) for caption in captions])


def pull(project_dir: Path | str, row_limit: int = DEFAULT_ROW_LIMIT,
         confirm_large: bool = False, force: bool = False, session=None) -> PullResult:
    """Sample published data sources via the VizQL Data Service (CONTRACT.md §3.2).

    Route 2 of the two acquisition routes. Reads ``datasources.json`` + the nearest
    ``.env``, signs in with a Personal Access Token, and for each listed source pulls a
    capped sample through VDS (``read-metadata`` then ``query-datasource``). On full
    success it writes one ``data/<slug>.csv`` per source and a ``DATA-MODEL.md`` whose
    types/descriptions come from the authoritative VDS metadata, and records
    ``data_mode: published-ds`` in STATE.md.

    The pull is **atomic**: every network call runs before any file is written, so a
    failure on any source leaves ``data/``, ``DATA-MODEL.md``, and STATE.md untouched -
    there is no partial or synthesized fallback (CONTRACT.md §3.2).

    Args:
        project_dir: The analyst's project directory.
        row_limit: Rows to cap the sample at (default 100, silent up to 1000).
        confirm_large: Required to be True when ``row_limit`` exceeds 1000.
        force: Re-pull over a prior pull's output - overwrites an existing DATA-MODEL.md
            and re-samples the CSVs. It will **not** overwrite an analyst's own dropped
            CSVs (only data/ that a prior published-ds pull wrote, identified by tier).
        session: An HTTP session to use (injected by tests); a real one is created
            when ``None``.

    Returns:
        A :class:`PullResult`. ``ok`` is False (nothing written) when the gate is
        closed, production CSVs already exist (that is Route 1), DATA-MODEL.md exists
        without ``force``, the row limit is too large unconfirmed, or any VDS call fails.
    """
    project_root = Path(project_dir)
    data_model_path = project_root / DATA_MODEL_FILENAME

    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return PullResult(False, blocker)

    # Real CSVs in data/ always win (CONTRACT.md §3.1/§3.2) - that is the csv route.
    # The one exception: --force re-pulls over data/ that a *prior published-ds pull*
    # wrote (identified by the recorded tier), so re-pulling never clobbers an analyst's
    # own dropped CSVs.
    source, _csv_paths = resolve_csv_source(project_root)
    if source == "data":
        prior_tier = (
            parse_acquisition_tier(data_model_path.read_text(encoding="utf-8-sig"))
            if data_model_path.exists() else None
        )
        from_prior_pull = force and prior_tier == TIER_PUBLISHED_DS
        if not from_prior_pull:
            hint = (
                "Run 'profile' instead"
                if prior_tier != TIER_PUBLISHED_DS
                else "re-run with '--force' to re-pull over them"
            )
            return PullResult(
                False,
                f"Production CSV(s) already exist in 'data/' - that is the csv route "
                f"(Route 1). {hint}. The published-ds pull only fills an empty data/ "
                f"(or re-pulls its own prior output with --force).",
            )

    if not (project_root / DATASOURCES_FILENAME).exists():
        return PullResult(
            False,
            f"No '{DATASOURCES_FILENAME}' found. The published-ds route needs it (one "
            f"entry per published source); copy scaffold/EXAMPLE-datasources.json to the "
            f"project root, or drop CSV(s) in data/ for the csv route.",
        )

    if data_model_path.exists() and not force:
        return PullResult(
            False,
            f"'{DATA_MODEL_FILENAME}' already exists. Edit it in place to refine, or "
            f"re-run 'pull --force' to re-sample from VDS (this overwrites the pulled "
            f"CSVs and discards prior field descriptions).",
        )

    if row_limit > MAX_SILENT_ROW_LIMIT and not confirm_large:
        return PullResult(
            False,
            f"row_limit {row_limit} exceeds {MAX_SILENT_ROW_LIMIT}. Confirm the larger "
            f"sample with the analyst, then re-run 'pull --row-limit {row_limit} "
            f"--confirm-large' (CONTRACT.md §3.2).",
            row_limit=row_limit,
        )

    # The network half lives in vds (requests-backed); import it lazily so importing
    # 'data' never requires requests (the stdlib-only contract test imports 'data').
    import vds

    if session is None:
        session = vds.make_session()

    try:
        refs = vds.parse_datasources_json(project_root / DATASOURCES_FILENAME)
        conn = vds.load_connection(project_root)
        token, site_id = vds.sign_in(conn, session)

        # Pull everything into memory first; only write once all sources succeed.
        pulled: list[tuple[str, list, list[dict]]] = []
        slug_owner: dict[str, str] = {}
        for ref in refs:
            slug = vds.slugify(ref.ds_name)
            if slug in slug_owner:
                raise vds.VdsError(
                    f"Data sources '{slug_owner[slug]}' and '{ref.ds_name}' both map to "
                    f"'{slug}.csv'. Rename one source so each gets a distinct CSV."
                )
            slug_owner[slug] = ref.ds_name
            luid = vds.resolve_luid(
                conn, token, site_id, ref.ds_name, ref.project_name, session
            )
            fields = vds.read_metadata(conn, token, luid, session)
            rows = vds.query_rows(
                conn, token, luid, [meta.caption for meta in fields], row_limit, session
            )
            pulled.append((slug, fields, rows))
    except vds.VdsError as error:
        logger.error(f"Published-ds pull failed: {error}")
        return PullResult(False, str(error), row_limit=row_limit)

    # All sources succeeded - write the CSVs and DATA-MODEL.md, then flip data_mode.
    profiles: list[CsvProfile] = []
    row_counts: dict[str, int] = {}
    for slug, fields, rows in pulled:
        csv_path = project_root / DATA_DIR / f"{slug}.csv"
        _write_pulled_csv(csv_path, [meta.caption for meta in fields], rows)
        profiles.append(_profile_pulled_source(slug, fields, rows))
        row_counts[f"{slug}.csv"] = len(rows)

    data_model_path.write_text(
        render_data_model(profiles, TIER_PUBLISHED_DS), encoding="utf-8"
    )

    state_path = project_root / STATE_FILENAME
    state_path.write_text(
        set_data_mode(state_path.read_text(encoding="utf-8-sig"), DATA_MODE_PUBLISHED_DS),
        encoding="utf-8",
    )

    written = [profile.filename for profile in profiles]
    logger.info(
        f"Pulled {len(written)} published source(s) via VDS (rowLimit={row_limit}); "
        f"wrote {DATA_MODEL_FILENAME} and set data_mode=published-ds."
    )
    return PullResult(
        ok=True,
        message=f"pulled {len(written)} published source(s) via VDS",
        tier=TIER_PUBLISHED_DS,
        written=written,
        row_counts=row_counts,
        row_limit=row_limit,
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
        return f"[BLOCKED] tableau-data cannot run.\n{result.blocker}"

    lines = ["[DATA] precheck OK - tableau-data can run."]
    lines.append(f"  data_mode      : csv")
    if result.csv_source == "data":
        lines.append(f"  csv source     : data/ ({', '.join(result.csv_files)}) - production input.")
    elif result.csv_source == "scaffold":
        lines.append(
            f"  csv source     : scaffold/sample-data/ ({', '.join(result.csv_files)}) "
            f"- DEMO fallback; say so (no CSVs in data/)."
        )
    else:
        lines.append(f"  csv source     : none found (no data/*.csv, no scaffold/sample-data/*.csv).")
    lines.append(f"  datasources.json: {'present' if result.has_datasources_json else 'absent'}")
    lines.append(f"  .env creds      : {'present' if result.has_env else 'absent'}")
    lines.append(f"  DATA-MODEL.md   : {'yes' if result.data_model_exists else 'no'}")

    rerun_note = (
        " (re-run; downstream approved steps will be marked stale on commit)"
        if result.data_status in ("approved", "stale")
        else ""
    )
    lines.append(f"  data status    : {result.data_status}{rerun_note}")

    # Recommended action (situations A / B / C of the plan).
    if result.csv_source != "none":
        lines.append("  -> Route 1 (CSV). Run 'profile' to write DATA-MODEL.md, then enrich + commit.")
    elif result.has_datasources_json and result.has_env:
        lines.append(
            "  -> Route 2 (published-ds) detected. Run 'pull' to sample each source via "
            "the VizQL Data Service into data/ + DATA-MODEL.md, then enrich + commit. "
            "(Drop CSVs in data/ instead to use Route 1.)"
        )
    elif result.has_datasources_json:
        lines.append(
            "  -> Route 2 (published-ds) inputs incomplete: datasources.json present but "
            "no .env creds. Copy scaffold/.env.example to .env and fill it, then 'pull'."
        )
    else:
        action = "  -> No data available. Either drop CSV(s) in data/, or add datasources.json + .env "
        action += "(published-ds). To demo the workflow, add the scaffold/sample-data/ examples via tableau-init."
        lines.append(action)
        if result.has_non_csv_data:
            lines.append(
                "  note: data/ holds non-CSV file(s). CSV mode reads only *.csv - "
                "export to CSV, or use the published-ds route."
            )
    return "\n".join(lines)


def format_profile(result: ProfileResult) -> str:
    """Render a :class:`ProfileResult` as a human-readable block.

    Args:
        result: The profile result to render.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    if not result.ok:
        return f"[REFUSED] {result.message}"

    lines = [f"[DATA] {result.message}."]
    lines.append(f"  tier          : {result.tier}")
    lines.append(f"  data sources  : {', '.join(result.profiled)}")
    if result.is_demo:
        lines.append(
            "  note: profiled the scaffold/sample-data/ DEMO CSVs - tell the analyst "
            "this is demo data, not their real source."
        )
    lines.append(
        "  next: enrich each field's Description in DATA-MODEL.md, present it for "
        "approval, then run 'commit'."
    )
    return "\n".join(lines)


def format_pull(result: PullResult) -> str:
    """Render a :class:`PullResult` as a human-readable block.

    Args:
        result: The pull result to render.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    if not result.ok:
        return f"[REFUSED] {result.message}"

    lines = [f"[DATA] {result.message}."]
    lines.append(f"  tier          : {result.tier}")
    lines.append(f"  row limit     : {result.row_limit}")
    sources = ", ".join(
        f"{name} ({result.row_counts.get(name, 0)} rows)" for name in result.written
    )
    lines.append(f"  data sources  : {sources}")
    lines.append("  data_mode set to 'published-ds' in STATE.md.")
    lines.append(
        "  next: enrich/refine each field's Description in DATA-MODEL.md (types and any "
        "VDS descriptions are pre-filled), present it for approval, then run 'commit'."
    )
    return "\n".join(lines)


def format_commit(result: CommitResult) -> str:
    """Render a :class:`CommitResult` as a human-readable block.

    Args:
        result: The commit result to render.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    if not result.ok:
        return f"[REFUSED] {result.message}"

    lines = [f"[DATA] {result.message}."]
    if result.validated:
        lines.append(f"  validated headers == documented fields for: {', '.join(result.validated)}")
    if result.staled_steps:
        lines.append(
            f"  downstream marked stale: {', '.join(result.staled_steps)} "
            f"(re-run these in order)."
        )
    lines.append("  next: open a fresh conversation and run 'tableau-route'.")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point: run ``precheck``, ``profile``, ``pull``, or ``commit``.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``2`` when data is blocked/refused or on
        a usage error.
    """
    import sys

    parser = argparse.ArgumentParser(
        description="Gate, profile, pull, validate, and commit the tableau-data step.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    precheck_parser = subparsers.add_parser(
        "precheck", help="Report whether data may run and which route is available."
    )
    precheck_parser.add_argument(
        "project_dir", nargs="?", default=".", help="Project directory (default: cwd)."
    )

    profile_parser = subparsers.add_parser(
        "profile", help="Profile the resolved CSVs into DATA-MODEL.md (csv route)."
    )
    profile_parser.add_argument(
        "project_dir", nargs="?", default=".", help="Project directory (default: cwd)."
    )
    profile_parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing DATA-MODEL.md (discards prior descriptions).",
    )

    pull_parser = subparsers.add_parser(
        "pull",
        help="Sample published sources via the VizQL Data Service (published-ds route).",
    )
    pull_parser.add_argument(
        "project_dir", nargs="?", default=".", help="Project directory (default: cwd)."
    )
    pull_parser.add_argument(
        "--row-limit", type=int, default=DEFAULT_ROW_LIMIT,
        help=f"Max rows to sample per source (default {DEFAULT_ROW_LIMIT}; silent up to "
             f"{MAX_SILENT_ROW_LIMIT}).",
    )
    pull_parser.add_argument(
        "--confirm-large", action="store_true",
        help=f"Required when --row-limit exceeds {MAX_SILENT_ROW_LIMIT} (analyst-confirmed).",
    )
    pull_parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing DATA-MODEL.md and re-sample the CSVs from VDS.",
    )

    commit_parser = subparsers.add_parser(
        "commit", help="Validate headers and record data as approved in STATE.md."
    )
    commit_parser.add_argument(
        "project_dir", nargs="?", default=".", help="Project directory (default: cwd)."
    )

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "precheck":
        precheck_result = precheck(args.project_dir)
        print(format_precheck(precheck_result))
        return 0 if precheck_result.can_run else 2

    if args.command == "profile":
        profile_result = profile(args.project_dir, force=args.force)
        print(format_profile(profile_result))
        return 0 if profile_result.ok else 2

    if args.command == "pull":
        pull_result = pull(
            args.project_dir,
            row_limit=args.row_limit,
            confirm_large=args.confirm_large,
            force=args.force,
        )
        print(format_pull(pull_result))
        return 0 if pull_result.ok else 2

    commit_result = commit(args.project_dir)
    print(format_commit(commit_result))
    return 0 if commit_result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
