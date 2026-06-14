"""The data-model domain of the tableau-data skill: CSVs <-> DATA-MODEL.md.

This is the stdlib-only core that turns analyst-provided CSVs into the canonical
``DATA-MODEL.md`` handoff artifact and validates them back:

* **Type inference** (:func:`infer_type`) - a conservative, narrowest-first guess of a
  Tableau-friendly column type from its values.
* **CSV profiling** (:func:`profile_csv`) - read a CSV's header + a capped row sample
  into a :class:`CsvProfile` (one :class:`FieldProfile` per column).
* **DATA-MODEL.md rendering** (:func:`render_data_model`) - write a schema-complete,
  machine-re-parseable field table the model then enriches with prose.
* **DATA-MODEL.md parsing** (:func:`parse_data_model`, :func:`parse_acquisition_tier`) -
  the inverse of rendering: recover the documented (field, type) pairs and the tier.
* **Header <-> model validation** (:func:`validate_headers`, :func:`read_csv_header`) -
  prove the documented field names match the real CSV headers exactly (CONTRACT.md §3.2).

It imports nothing beyond the standard library and :mod:`constants`, so the contract
test (and :mod:`data`) can call these functions without ``requests`` on the path.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from constants import (
    PROFILE_SAMPLE_ROWS,
    SAMPLE_VALUES_SHOWN,
    TYPE_BOOLEAN,
    TYPE_DATE,
    TYPE_DATETIME,
    TYPE_INTEGER,
    TYPE_REAL,
    TYPE_STRING,
)

#: Accepted strict date / datetime formats for type inference (no locale guessing).
_DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%Y/%m/%d")
_DATETIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
)
#: Literal tokens (lower-cased) that count as boolean values.
_BOOLEAN_TOKENS = frozenset({"true", "false"})


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
        One of :data:`constants.TYPES`.
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
        type: The inferred type, one of :data:`constants.TYPES`.
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
        row_count: Number of data rows read (capped at :data:`constants.PROFILE_SAMPLE_ROWS`).
        fields: One :class:`FieldProfile` per column, in file order.
    """

    filename: str
    row_count: int
    fields: list[FieldProfile]


def suggest_role(column_type: str) -> str:
    """str: Suggest a Tableau role from a column type (numeric -> Measure)."""
    return "Measure" if column_type in (TYPE_INTEGER, TYPE_REAL) else "Dimension"


def distinct_samples(values: list[str], limit: int) -> list[str]:
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

    Only the first :data:`constants.PROFILE_SAMPLE_ROWS` data rows are read - enough to
    infer types reliably without loading large files. The header row supplies the field
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
            role=suggest_role(column_type),
            samples=distinct_samples(cells, SAMPLE_VALUES_SHOWN),
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
        tier: The acquisition tier to record (e.g. :data:`constants.TIER_CSV_PROVIDED`).

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
