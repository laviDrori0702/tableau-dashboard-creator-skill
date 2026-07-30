"""The build-manifest core of tableau-build (CONTRACT.md step 8).

The **build manifest** is the machine-readable JSON an agent derives from
``IMPLEMENTATION-SPEC.md`` + ``DATA-MODEL.md``, and the only thing the deterministic
workbook builder reads. Putting a validated schema between the prose spec and the XML
generator is what makes a bad spec-to-manifest translation fail **before** any XML exists:
every error below names the offending entry (worksheet, field, element id) so the agent can
fix that one row instead of re-deriving the whole manifest.

The module is pure and stdlib-only (no filesystem beyond :func:`load_manifest`, no STATE.md)
so the contract test can drive :func:`validate_manifest` directly.

What a manifest carries, and what is checked:

* ``target_tableau_version`` - must equal STATE.md's (CONTRACT.md §2).
* ``datasources`` - one per ``data/`` CSV ("csv = datasource", §3.2); every declared field
  must be documented in ``DATA-MODEL.md`` for that CSV.
* ``worksheets`` - chart type from :data:`CHART_TYPES`, a unique name, an ``element_id`` that
  occupies a zone in the layout tree, and shelf/encoding fields that resolve to a declared
  field or a declared calculated field.
* ``layout`` - the spec's container tree (CONTRACT.md §1.1), each *leaf* zone filled by a
  worksheet or an ``objects`` entry (a mapped container is filled by its children instead).
* ``actions`` / ``parameters`` - endpoints that exist, types from the shared interactions
  vocabulary (§6).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NamedTuple, Optional

from features import ACTIVATIONS, PARAMETER_TYPES
# Names, not the module: half the functions below take a parameter called ``worksheet``.
from worksheet import (
    CHART_SPECS,
    DATE_PART_DERIVATIONS,
    ENCODING_ORDER,
    REFERENCE_LINE_FORMULAS,
    REFERENCE_LINE_LABEL_TYPES,
    REFERENCE_LINE_SCOPES,
    TABLE_CALC_PREFIXES,
)
from zones import FILTER_MODES

#: Sections a manifest must carry (actions/parameters may be empty lists, never absent -
#: "None" must be said explicitly so a forgotten section is not mistaken for "no actions").
REQUIRED_KEYS: tuple[str, ...] = (
    "target_tableau_version", "datasources", "worksheets", "layout", "actions", "parameters",
)

#: Mark/chart types the builder knows how to emit - read straight off the builder's table,
#: so a type can never be accepted here and rendered blank there. Add a chart type by adding
#: a :data:`worksheet.CHART_SPECS` row.
CHART_TYPES = frozenset(CHART_SPECS)

#: Chart types that overlay two measures on one axis pair - they need exactly two entries on
#: the rows shelf, or the second axis (and the whole point of the chart) is missing.
DUAL_AXIS_TYPES = frozenset({"dual-axis", "combo"})

#: Dashboard action types (the Tableau constructs behind CONTRACT.md §6's vocabulary).
ACTION_TYPES = frozenset({"filter", "highlight", "parameter", "set", "url"})

#: Non-worksheet dashboard objects, for layout zones no view fills (a filter card, a title,
#: a logo, a legend). Every *leaf* zone must be filled by a worksheet or one of these; a
#: mapped container (see ``container_ids`` below) is filled by its children instead.
OBJECT_KINDS = frozenset({"filter", "parameter", "text", "image", "legend", "button", "blank"})

#: Valid container orientations in the layout tree (mirror of the spec's Layout section).
CONTAINER_TYPES = frozenset({"vert", "horz"})

#: Layout/mapping ids with this prefix are dashboard actions, not zones (plan convention).
INTERACTION_PREFIX = "int-"

#: Aggregations a shelf/encoding entry may request ("none" pins a dimension/exact value).
AGGREGATIONS = frozenset({
    "sum", "avg", "min", "max", "count", "countd", "median", "attr", "none",
})

#: Date parts a shelf/encoding entry may request of a date field, and the encoding names an
#: ``encodings`` block may use. Both come off the builder's tables for the same reason as
#: :data:`CHART_TYPES`: an entry this module accepts but the builder has no case for is
#: dropped silently, and Tableau shows no sign of the missing encoding.
DATE_PARTS = frozenset(DATE_PART_DERIVATIONS)
ENCODING_NAMES = frozenset(ENCODING_ORDER)

#: Table calculations a shelf entry may ask for, and the parameter data types / action
#: activations the builder can emit - all read off the builder's own tables.
TABLE_CALCS = frozenset(TABLE_CALC_PREFIXES)
PARAMETER_DATA_TYPES = frozenset(PARAMETER_TYPES)
ACTION_RUN_ON = frozenset(ACTIVATIONS)

#: Types a quick-filter card can render: it lists the field's *members*, so a date or numeric
#: field belongs in a worksheet filter with bounds instead (see :data:`zones.FILTER_MODES`).
CARD_FILTER_TYPES = frozenset({"string", "boolean"})

#: The type a Dynamic Zone Visibility field must have - a zone is shown or hidden, nothing else.
VISIBILITY_TYPE = "boolean"

# A data-source heading in DATA-MODEL.md: "## Data source: `sales.csv`" -> "sales.csv".
_DATASOURCE_HEADING = re.compile(
    r"^#{1,6}\s+data source:\s*`?([^`\s|]+\.csv)`?\s*$", re.IGNORECASE
)


def documented_field_types(data_model_text: str) -> dict[str, dict[str, str]]:
    """Recover ``{csv filename: {field name: type}}`` from a DATA-MODEL.md.

    ``DATA-MODEL.md`` is the field authority (CONTRACT.md §3), so the builder takes each
    column's Tableau datatype from here rather than from the manifest - one authority, no
    drift. The parse is the tolerant mirror of ``tableau-data``'s renderer, so it keeps
    working after the model enriches the Role/Description columns.

    Args:
        data_model_text: The contents of a ``DATA-MODEL.md`` file.

    Returns:
        A mapping of CSV filename to ``{field name: lower-cased type}``. A field whose Type
        cell is blank maps to ``""``.
    """
    documented: dict[str, dict[str, str]] = {}
    current: Optional[str] = None
    in_field_table = False

    for raw_line in data_model_text.splitlines():
        line = raw_line.strip()
        heading = _DATASOURCE_HEADING.match(line)
        if heading:
            current = heading.group(1)
            documented.setdefault(current, {})
            in_field_table = False
            continue
        if line.startswith("## "):  # a non-data-source section ends the current one
            current = None
            in_field_table = False
            continue
        if current is None:
            continue
        if not line.startswith("|"):
            in_field_table = False  # a non-table line ends the field table
            continue

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        name = cells[0] if cells else ""
        # Only rows under a "| Field | Type |" header count: any other table in the section
        # (a sample-rows preview, say) would otherwise donate its first column as fields.
        if len(cells) >= 2 and name.lower() == "field" and cells[1].lower() == "type":
            in_field_table = True
            continue
        if not in_field_table or not name or set(name) <= set("-: "):
            continue  # separator row, or a table that is not the field table
        documented[current][name] = cells[1].lower() if len(cells) >= 2 else ""
    return documented


def documented_fields(data_model_text: str) -> dict[str, set[str]]:
    """Recover ``{csv filename: {field name}}`` from a DATA-MODEL.md.

    Args:
        data_model_text: The contents of a ``DATA-MODEL.md`` file.

    Returns:
        A mapping of CSV filename to the set of field names documented for it.
    """
    return {
        csv_name: set(fields)
        for csv_name, fields in documented_field_types(data_model_text).items()
    }


def load_manifest(path: Path | str) -> tuple[Optional[dict], Optional[str]]:
    """Read and JSON-parse a build manifest.

    Args:
        path: Path to the ``build-manifest.json``.

    Returns:
        ``(document, None)`` on success, or ``(None, error message)`` when the file is
        missing, unparseable, or not a JSON object. The message names the file so the
        agent can fix it without a stack trace.
    """
    manifest_path = Path(path)
    if not manifest_path.exists():
        return None, f"'{manifest_path.name}' does not exist at '{manifest_path}'."
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return None, f"'{manifest_path.name}' is not valid JSON: {exc}"
    if not isinstance(document, dict):
        return None, f"'{manifest_path.name}' must be a JSON object with the build sections."
    return document, None


# --- Layout tree --------------------------------------------------------------

def _collect_layout_ids(
    node: object, path: str, ids: list[str], container_ids: set[str], errors: list[str],
    visibility: Optional[list[tuple[str, str]]] = None,
) -> None:
    """Walk one layout node, recording every placed id and any structural error.

    A node is a container (``type`` + non-empty ``children``), a leaf (``id``), or both (a
    **mapped container**, e.g. a DZV panel that is itself an element and holds further
    zones - CONTRACT.md §1.1). A mapped container's id is recorded in ``container_ids``
    because its children fill it; only *leaf* zones need a worksheet or object of their own.
    Sibling ``size`` percentages were already reconciled against the mock by
    ``tableau-spec``; here the tree only has to be structurally sound and consistent with
    the worksheets.

    Args:
        node: The JSON value at this position in the tree.
        path: Human-readable position (e.g. ``layout.root.children[1]``) for messages.
        ids: Accumulator for placed element ids.
        container_ids: Accumulator for the ids of nodes that hold children.
        errors: Accumulator for validation errors.
        visibility: Accumulator for ``(path, field name)`` of every node whose zone is shown
            or hidden by a boolean field (Dynamic Zone Visibility), or ``None`` to ignore them.
    """
    if not isinstance(node, dict):
        errors.append(f"{path}: every layout node must be a JSON object")
        return

    if visibility is not None and node.get("visibility") is not None:
        shown_by = node.get("visibility")
        if isinstance(shown_by, str) and shown_by.strip():
            visibility.append((path, shown_by.strip()))
        else:
            errors.append(
                f"{path}: 'visibility' must be the name of a boolean calculated field "
                f"(got {shown_by!r})"
            )

    element_id, children = node.get("id"), node.get("children")
    if element_id is None and children is None:
        errors.append(f"{path}: a node needs an 'id' (leaf), 'children' (container), or both")
        return
    if isinstance(element_id, str) and element_id.strip():
        placed = element_id.strip()
        ids.append(placed)
        if children is not None:
            container_ids.add(placed)
        if placed.startswith(INTERACTION_PREFIX):
            errors.append(
                f"{path}: interaction id '{placed}' occupies no zone - dashboard actions "
                f"belong in 'actions', never in the layout tree (CONTRACT.md §1.1)"
            )
    elif element_id is not None:
        errors.append(f"{path}: 'id' must be a non-empty string")

    if children is None:
        return
    if node.get("type") not in CONTAINER_TYPES:
        errors.append(
            f"{path}: container 'type' must be 'vert' or 'horz' (got {node.get('type')!r})"
        )
    if not isinstance(children, list) or not children:
        errors.append(f"{path}: 'children' must be a non-empty list")
        return
    for index, child in enumerate(children):
        _collect_layout_ids(
            child, f"{path}.children[{index}]", ids, container_ids, errors, visibility
        )


def placed_layout_ids(layout: object) -> set[str]:
    """Return every element id a layout tree places, ignoring structural problems.

    Used to compare a manifest's tree against the approved spec's (``build.py``); the
    structural verdict comes from :func:`validate_manifest`.

    Args:
        layout: A layout object (``{"canvas": ..., "root": ...}``) or ``root`` itself.

    Returns:
        The placed element ids.
    """
    if not isinstance(layout, dict):
        return set()
    root = layout.get("root", layout)
    ids: list[str] = []
    _collect_layout_ids(root, "root", ids, set(), [])
    return set(ids)


def _validate_layout(
    layout: object, errors: list[str]
) -> tuple[list[str], set[str], list[tuple[str, str]]]:
    """Validate the layout section and return what it places.

    Args:
        layout: The manifest's ``layout`` value.
        errors: Accumulator for validation errors.

    Returns:
        ``(placed ids, container ids, visibility bindings)``. A container id is filled by its
        children, so it needs no worksheet or object of its own; a visibility binding is the
        ``(path, field name)`` of a zone under Dynamic Zone Visibility.
    """
    if not isinstance(layout, dict):
        errors.append("layout: must be an object with 'canvas' and 'root' (copy the spec's)")
        return [], set(), []

    canvas = layout.get("canvas")
    if not (
        isinstance(canvas, dict)
        and isinstance(canvas.get("width"), (int, float))
        and isinstance(canvas.get("height"), (int, float))
    ):
        errors.append("layout.canvas: needs numeric 'width' and 'height' (the mock's px size)")

    ids: list[str] = []
    container_ids: set[str] = set()
    visibility: list[tuple[str, str]] = []
    if isinstance(layout.get("root"), dict):
        _collect_layout_ids(
            layout["root"], "layout.root", ids, container_ids, errors, visibility
        )
    else:
        errors.append("layout.root: must be a container object ('type' + 'children')")

    duplicates = sorted({placed for placed in ids if ids.count(placed) > 1})
    if duplicates:
        errors.append("layout places id(s) more than once: " + ", ".join(duplicates))
    return ids, container_ids, visibility


# --- Datasources / worksheets -------------------------------------------------

def _bare_field(reference: object) -> str:
    """Normalise a field reference to a bare name (``[revenue]`` -> ``revenue``)."""
    return str(reference).strip().strip("[]").strip() if reference is not None else ""


def _validate_datasources(
    datasources: object, data_model_text: str, errors: list[str]
) -> dict[str, set[str]]:
    """Validate the datasources against DATA-MODEL.md.

    Args:
        datasources: The manifest's ``datasources`` value.
        data_model_text: The contents of ``DATA-MODEL.md``.
        errors: Accumulator for validation errors.

    Returns:
        ``{datasource name: {declared field names}}`` for the entries that parsed, so the
        worksheet pass can resolve field references.
    """
    documented = documented_fields(data_model_text)
    declared: dict[str, set[str]] = {}

    if not isinstance(datasources, list) or not datasources:
        errors.append("datasources: must be a non-empty list (one entry per data/ CSV)")
        return declared

    for index, source in enumerate(datasources):
        label = f"datasources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{label}: must be an object with 'name', 'csv', 'fields'")
            continue
        name, csv_name = str(source.get("name", "")).strip(), str(source.get("csv", "")).strip()
        label = f"datasources[{index}] '{name or '?'}'"
        if not name:
            errors.append(f"{label}: needs a 'name' (how worksheets refer to it)")
            continue  # an unnamed source would register as '' and swallow bad references
        if name in declared:
            errors.append(f"{label}: duplicate datasource name '{name}'")
            continue  # keep the first entry's fields; the duplicate is the bug to fix

        if csv_name not in documented:
            errors.append(
                f"{label}: csv '{csv_name}' is not documented in DATA-MODEL.md "
                f"(documented: {', '.join(sorted(documented)) or 'none'})"
            )

        fields = source.get("fields")
        names: set[str] = set()
        if not isinstance(fields, list) or not fields:
            errors.append(f"{label}: 'fields' must be a non-empty list of {{name, type}}")
        else:
            for field_index, field in enumerate(fields):
                if not isinstance(field, dict) or not str(field.get("name", "")).strip():
                    errors.append(f"{label}: fields[{field_index}] needs a 'name'")
                    continue
                field_name = str(field["name"]).strip()
                if not str(field.get("type", "")).strip():
                    errors.append(f"{label}: field '{field_name}' needs a 'type'")
                if csv_name in documented and field_name not in documented[csv_name]:
                    errors.append(
                        f"{label}: field '{field_name}' is not in DATA-MODEL.md for "
                        f"'{csv_name}' (a calculated field belongs in 'calculated_fields')"
                    )
                names.add(field_name)
        declared[name] = names
    return declared


def _calculated_field_names(
    manifest_document: dict, datasource_names: set[str], errors: list[str]
) -> dict[str, set[str]]:
    """Validate the optional ``calculated_fields`` and index them by datasource.

    Args:
        manifest_document: The whole manifest.
        datasource_names: The declared datasource names.
        errors: Accumulator for validation errors.

    Returns:
        ``{datasource name: {calculated field names}}``.
    """
    calculated: dict[str, set[str]] = {}
    entries = manifest_document.get("calculated_fields", [])
    if not isinstance(entries, list):
        errors.append("calculated_fields: must be a list of {name, formula, datasource}")
        return calculated

    for index, entry in enumerate(entries):
        label = f"calculated_fields[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label}: must be an object with 'name', 'formula', 'datasource'")
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            errors.append(f"{label}: needs a 'name'")
            continue
        if not str(entry.get("formula", "")).strip():
            errors.append(f"{label} '{name}': needs a 'formula'")
        source = str(entry.get("datasource", "")).strip()
        if source not in datasource_names:
            errors.append(f"{label} '{name}': unknown datasource '{source}'")
            continue
        calculated.setdefault(source, set()).add(name)
    return calculated


def _worksheet_field_references(worksheet: dict) -> list[tuple[str, object]]:
    """Return the ``(where, entry)`` pairs a worksheet's fields are referenced from.

    Each entry is either a bare field name (``"revenue"``) or an object carrying what the
    builder must apply (``{"field": "revenue", "aggregation": "sum"}``). Shelves and
    encodings place fields on the view; ``filters``, ``sort``, ``tooltip`` and
    ``number_formats`` are the optional modifiers that any chart type may carry, and their
    fields have to resolve just as strictly - a filter on a field that does not exist is a
    filter Tableau silently ignores.

    Args:
        worksheet: One ``worksheets`` entry.

    Returns:
        ``(where, entry)`` pairs, where ``where`` is e.g. ``shelves.rows`` / ``sort.by``.
    """
    references: list[tuple[str, object]] = []
    shelves = worksheet.get("shelves")
    if isinstance(shelves, dict):
        for shelf, entries in shelves.items():
            for entry in entries if isinstance(entries, list) else [entries]:
                references.append((f"shelves.{shelf}", entry))
    encodings = worksheet.get("encodings")
    if isinstance(encodings, dict):
        for encoding, entry in encodings.items():
            references.append((f"encodings.{encoding}", entry))

    for key in ("filters", "tooltip", "number_formats", "reference_lines"):
        entries = worksheet.get(key)
        if isinstance(entries, list):
            for index, entry in enumerate(entries):
                references.append((f"{key}[{index}]", entry))

    sort = worksheet.get("sort")
    if isinstance(sort, dict):
        references.append(("sort", sort))
        if sort.get("by") is not None:
            references.append(("sort.by", sort["by"]))
    return references


def _validate_reference(
    label: str, where: str, entry: object, available: set[str], source: str,
    errors: list[str],
) -> None:
    """Validate one shelf/encoding entry against a datasource's fields.

    Args:
        label: The worksheet's error label.
        where: The shelf/encoding the entry sits on.
        entry: The raw entry (a field name, or an object with ``field`` +
            optional ``aggregation`` / ``date_part``).
        available: Field names the worksheet's datasource offers (declared + calculated).
        source: The datasource name, for the message.
        errors: Accumulator for validation errors.
    """
    bin_size: object = None
    table_calc = ""
    if isinstance(entry, dict):
        raw_calc = entry.get("table_calc")
        table_calc = raw_calc.strip() if isinstance(raw_calc, str) else ""
        field_name = _bare_field(entry.get("field"))
        # A JSON null (or any other non-string) means "not specified", same as a missing
        # key - never coerce it to a string, or `null` reads as the literal text "none".
        raw_aggregation = entry.get("aggregation", "")
        aggregation = raw_aggregation.strip().lower() if isinstance(raw_aggregation, str) else ""
        raw_date_part = entry.get("date_part", "")
        date_part = raw_date_part.strip().lower() if isinstance(raw_date_part, str) else ""
        bin_size = entry.get("bin")
    else:
        field_name, aggregation, date_part = _bare_field(entry), "", ""

    if not field_name:
        errors.append(f"{label}: {where} entry needs a 'field'")
        return
    if "(" in field_name:
        # e.g. "SUM([revenue])" copied verbatim out of the spec's construct cell.
        errors.append(
            f"{label}: {where} references '{field_name}' - name the field alone and put "
            f"the aggregation in the entry, e.g. "
            f'{{"field": "revenue", "aggregation": "sum"}}'
        )
        return
    if field_name not in available:
        errors.append(
            f"{label}: {where} references '{field_name}', which is not a field of "
            f"datasource '{source}' nor a declared calculated field"
        )
    if aggregation and aggregation not in AGGREGATIONS:
        errors.append(
            f"{label}: {where} '{field_name}' has unknown aggregation '{aggregation}' "
            f"(expected one of: {', '.join(sorted(AGGREGATIONS))})"
        )
    if date_part and date_part not in DATE_PARTS:
        errors.append(
            f"{label}: {where} '{field_name}' has unknown date_part '{date_part}' "
            f"(expected one of: {', '.join(sorted(DATE_PARTS))})"
        )
    if bin_size is not None and not (
        isinstance(bin_size, (int, float)) and not isinstance(bin_size, bool) and bin_size > 0
    ):
        errors.append(
            f"{label}: {where} '{field_name}' has a 'bin' of {bin_size!r} - a bin width "
            f"must be a positive number (e.g. 500 for a histogram of 0-500, 500-1000, ...)"
        )
    if table_calc and table_calc not in TABLE_CALCS:
        errors.append(
            f"{label}: {where} '{field_name}' has unknown table_calc '{table_calc}' "
            f"(expected one of: {', '.join(sorted(TABLE_CALCS))})"
        )


def _validate_modifiers(label: str, worksheet: dict, errors: list[str]) -> None:
    """Reject modifier entries the builder would have to drop.

    A filter with nothing to filter on, a sort with nothing to sort by, or an encoding the
    builder has no case for is an entry the assembler cannot render - and one Tableau would
    show no sign of. Naming it here is the difference between "the filter is missing" and
    "the manifest row is incomplete".

    Args:
        label: The worksheet's error label.
        worksheet: One ``worksheets`` entry.
        errors: Accumulator for validation errors.
    """
    encodings = worksheet.get("encodings")
    if isinstance(encodings, dict):
        # The field behind each encoding is checked by _validate_reference; only the
        # encoding *name* is checked here, so a typo'd 'colour' fails instead of vanishing.
        for name in encodings:
            if str(name).strip().lower() not in ENCODING_NAMES:
                errors.append(
                    f"{label}: unknown encoding '{name}' "
                    f"(expected one of: {', '.join(sorted(ENCODING_NAMES))})"
                )

    filters = worksheet.get("filters")
    if isinstance(filters, list):
        for index, entry in enumerate(filters):
            if not isinstance(entry, dict):
                errors.append(f"{label}: filters[{index}] must be an object with a 'field'")
                continue
            values = entry.get("values")
            if not (isinstance(values, list) and values) and (
                entry.get("min") is None and entry.get("max") is None
            ):
                errors.append(
                    f"{label}: filters[{index}] has nothing to filter on - give it "
                    f"'values' (a member list) or 'min'/'max' (a range)"
                )

    lines = worksheet.get("reference_lines")
    if isinstance(lines, list):
        for index, entry in enumerate(lines):
            if not isinstance(entry, dict):
                errors.append(
                    f"{label}: reference_lines[{index}] must be an object with a 'field'"
                )
                continue
            for key, allowed in (
                ("formula", REFERENCE_LINE_FORMULAS),
                ("scope", REFERENCE_LINE_SCOPES),
                ("label_type", REFERENCE_LINE_LABEL_TYPES),
            ):
                value = entry.get(key)
                if isinstance(value, str) and value.strip().lower() not in allowed:
                    errors.append(
                        f"{label}: reference_lines[{index}] has unknown {key} "
                        f"'{value}' (expected one of: {', '.join(sorted(allowed))})"
                    )

    sort = worksheet.get("sort")
    if isinstance(sort, dict):
        order = sort.get("order")
        if sort.get("by") is None and not (isinstance(order, list) and order):
            errors.append(
                f"{label}: 'sort' has neither 'by' (the measure to sort on) nor 'order' "
                f"(an explicit member list) - one of the two is required"
            )


def _validate_worksheets(
    worksheets: object,
    declared_fields: dict[str, set[str]],
    calculated: dict[str, set[str]],
    layout_ids: set[str],
    container_ids: set[str],
    errors: list[str],
) -> set[str]:
    """Validate the worksheets against the datasources and the layout tree.

    Args:
        worksheets: The manifest's ``worksheets`` value.
        declared_fields: ``{datasource: {field}}`` from :func:`_validate_datasources`.
        calculated: ``{datasource: {calculated field}}``.
        layout_ids: The element ids the layout tree places.
        container_ids: Mapped-container ids (filled by their children, not a view of
            their own - CONTRACT.md §1.1).
        errors: Accumulator for validation errors.

    Returns:
        The element ids the worksheets fill.
    """
    filled: set[str] = set()
    if not isinstance(worksheets, list):
        errors.append("worksheets: must be a list (one entry per mock zone that is a view)")
        return filled
    # An empty list is legal: a dashboard of nothing but objects (a title, a logo, filter
    # cards) has no view. The "every leaf zone is filled" check below is what actually
    # catches a translation that dropped the views.

    seen_names: set[str] = set()
    for index, worksheet in enumerate(worksheets):
        label = f"worksheets[{index}]"
        if not isinstance(worksheet, dict):
            errors.append(f"{label}: must be an object")
            continue
        name = str(worksheet.get("name", "")).strip()
        label = f"worksheets[{index}] '{name or '?'}'"
        if not name:
            errors.append(f"{label}: needs a 'name' (the Tableau sheet name)")
        elif name in seen_names:
            errors.append(f"{label}: duplicate worksheet name '{name}'")
        seen_names.add(name)

        chart_type = str(worksheet.get("chart_type", "")).strip().lower()
        if chart_type not in CHART_TYPES:
            errors.append(
                f"{label}: unknown chart_type '{chart_type}' "
                f"(expected one of: {', '.join(sorted(CHART_TYPES))})"
            )
        elif chart_type in DUAL_AXIS_TYPES:
            shelves = worksheet.get("shelves")
            rows = shelves.get("rows") if isinstance(shelves, dict) else None
            if not (isinstance(rows, list) and len(rows) == 2):
                errors.append(
                    f"{label}: chart_type '{chart_type}' needs exactly two measures on "
                    f"'shelves.rows' (the two axes to overlay); got "
                    f"{len(rows) if isinstance(rows, list) else 0}"
                )

        element_id = str(worksheet.get("element_id", "")).strip()
        if not element_id:
            errors.append(f"{label}: needs an 'element_id' (the spec/layout zone it fills)")
        elif element_id in container_ids:
            errors.append(
                f"{label}: element_id '{element_id}' is a mapped container - it is filled "
                f"by its children, not by a view (CONTRACT.md §1.1)"
            )
        elif element_id not in layout_ids:
            errors.append(
                f"{label}: element_id '{element_id}' is not a zone in the layout tree"
            )
        else:
            filled.add(element_id)

        source = str(worksheet.get("datasource", "")).strip()
        if source not in declared_fields:
            errors.append(
                f"{label}: unknown datasource '{source}' "
                f"(declared: {', '.join(sorted(declared_fields)) or 'none'})"
            )
            continue

        available = declared_fields[source] | calculated.get(source, set())
        for where, entry in _worksheet_field_references(worksheet):
            _validate_reference(label, where, entry, available, source, errors)
        _validate_modifiers(label, worksheet, errors)
    return filled


class ViewZone(NamedTuple):
    """One worksheet as the rest of the manifest addresses it.

    Attributes:
        name: The Tableau sheet name.
        datasource: The datasource the sheet reads (whose fields a card may filter).
    """

    name: str
    datasource: str


def _view_zones(worksheets: object) -> dict[str, ViewZone]:
    """Map ``{element id: ViewZone}`` for the worksheets that name a zone.

    A filter card and a filter/highlight action are both defined in terms of a *view*: the
    card filters one sheet, and an action runs from one sheet's marks to another's. This is
    the one place the manifest's element ids are turned into that.

    Args:
        worksheets: The manifest's ``worksheets`` value.

    Returns:
        ``{element id: ViewZone}``; malformed entries are skipped (already reported).
    """
    views: dict[str, ViewZone] = {}
    for entry in worksheets if isinstance(worksheets, list) else []:
        if not isinstance(entry, dict):
            continue
        element_id = str(entry.get("element_id", "")).strip()
        name = str(entry.get("name", "")).strip()
        if element_id and name:
            views.setdefault(
                element_id, ViewZone(name, str(entry.get("datasource", "")).strip())
            )
    return views


def _field_types(
    manifest_document: dict, data_model_text: str
) -> dict[str, dict[str, str]]:
    """Return ``{datasource name: {field name: type}}`` for every field a sheet can name.

    Merges the CSV's documented types (``DATA-MODEL.md``, the field authority) with the
    manifest's calculated fields, so a check like "is this field a discrete dimension?" has
    one place to look.

    Args:
        manifest_document: The whole manifest.
        data_model_text: The contents of ``DATA-MODEL.md``.

    Returns:
        The merged type map.
    """
    documented = documented_field_types(data_model_text)
    types: dict[str, dict[str, str]] = {}
    for entry in manifest_document.get("datasources") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if name:
            types[name] = dict(documented.get(str(entry.get("csv", "")).strip(), {}))
    for entry in manifest_document.get("calculated_fields") or []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("datasource", "")).strip()
        name = str(entry.get("name", "")).strip()
        if source in types and name:
            # No 'type' means a numeric result - the same default the assembler applies.
            types[source][name] = str(entry.get("type", "")).strip().lower() or "real"
    return types


def _validate_objects(
    objects: object, layout_ids: set[str], container_ids: set[str],
    views: dict[str, ViewZone], field_types: dict[str, dict[str, str]],
    parameter_names: set[str], errors: list[str],
) -> set[str]:
    """Validate the non-worksheet dashboard objects and return the zones they fill.

    Args:
        objects: The manifest's optional ``objects`` value.
        layout_ids: The element ids the layout tree places.
        container_ids: Mapped-container ids (filled by their children, not an object of
            their own - CONTRACT.md §1.1).
        views: ``{element id: ViewZone}`` - the sheets a filter card may filter.
        field_types: ``{datasource: {field: type}}``.
        parameter_names: The declared parameter names.
        errors: Accumulator for validation errors.

    Returns:
        The element ids the objects fill.
    """
    filled: set[str] = set()
    if not isinstance(objects, list):
        errors.append("objects: must be a list of {element_id, kind}")
        return filled

    for index, dashboard_object in enumerate(objects):
        label = f"objects[{index}]"
        if not isinstance(dashboard_object, dict):
            errors.append(f"{label}: must be an object with 'element_id' and 'kind'")
            continue
        element_id = str(dashboard_object.get("element_id", "")).strip()
        label = f"objects[{index}] '{element_id or '?'}'"
        if element_id in container_ids:
            errors.append(
                f"{label}: element_id is a mapped container - it is filled by its children, "
                f"not by an object (CONTRACT.md §1.1)"
            )
        elif element_id not in layout_ids:
            errors.append(f"{label}: element_id is not a zone in the layout tree")
        else:
            filled.add(element_id)

        kind = str(dashboard_object.get("kind", "")).strip().lower()
        if kind not in OBJECT_KINDS:
            errors.append(
                f"{label}: unknown kind '{kind}' "
                f"(expected one of: {', '.join(sorted(OBJECT_KINDS))})"
            )
        elif kind == "filter":
            _validate_filter_card(
                label, dashboard_object, views, field_types, errors
            )
        elif kind == "parameter":
            parameter = str(dashboard_object.get("parameter", "")).strip()
            if parameter not in parameter_names:
                errors.append(
                    f"{label}: 'parameter' is {parameter or 'missing'} - a parameter control "
                    f"names one declared parameter (declared: "
                    f"{', '.join(sorted(parameter_names)) or 'none'})"
                )
    return filled


def _validate_filter_card(
    label: str, dashboard_object: dict, views: dict[str, ViewZone],
    field_types: dict[str, dict[str, str]], errors: list[str],
) -> None:
    """Validate one quick-filter card against the worksheet it filters.

    A card is the UI for one worksheet's filter, so it has to name that worksheet *and* a
    field of the worksheet's datasource. The field also has to be one whose members a card can
    list - a card over a date or a measure would need bounds, which belong on the worksheet's
    ``filters`` instead.

    Args:
        label: The object's error label.
        dashboard_object: The ``objects`` entry.
        views: ``{element id: ViewZone}``.
        field_types: ``{datasource: {field: type}}``.
        errors: Accumulator for validation errors.
    """
    sheet_names = {view.name: view.datasource for view in views.values()}
    sheet = str(dashboard_object.get("worksheet", "")).strip()
    field_name = _bare_field(dashboard_object.get("field"))

    if not field_name:
        errors.append(f"{label}: a filter card needs a 'field' (the field it filters on)")
    if sheet not in sheet_names:
        errors.append(
            f"{label}: a filter card needs a 'worksheet' naming the sheet it filters "
            f"- '{sheet}' is not one "
            f"(declared: {', '.join(sorted(sheet_names)) or 'none'})"
        )
    elif field_name:
        datatype = field_types.get(sheet_names[sheet], {}).get(field_name)
        if datatype is None:
            errors.append(
                f"{label}: field '{field_name}' is not a field of worksheet '{sheet}'s "
                f"datasource '{sheet_names[sheet]}'"
            )
        elif datatype not in CARD_FILTER_TYPES:
            errors.append(
                f"{label}: field '{field_name}' is a '{datatype}' - a filter card lists a "
                f"field's members, so it needs one of: "
                f"{', '.join(sorted(CARD_FILTER_TYPES))}. For a date or numeric range, put "
                f"'filters' with min/max on worksheet '{sheet}' instead"
            )

    mode = str(dashboard_object.get("mode", "")).strip()
    if mode and mode not in FILTER_MODES:
        errors.append(
            f"{label}: unknown filter mode '{mode}' "
            f"(expected one of: {', '.join(sorted(FILTER_MODES))})"
        )


def _validate_actions(
    actions: object, known_ids: set[str], parameter_names: set[str],
    views: dict[str, ViewZone], field_types: dict[str, dict[str, str]],
    errors: list[str],
) -> None:
    """Validate the dashboard actions' types and endpoints.

    The **source** is always a *view* zone: an action runs off the marks the analyst clicks,
    and a text or filter zone has none. What a **target** is depends on the type: ``filter`` /
    ``highlight`` target other view zones, a ``parameter`` action targets a declared parameter
    (and needs the ``field`` whose value it writes there), and ``set`` / ``url`` actions target
    a set / a URL that this schema does not model - those are left to the builder.

    Args:
        actions: The manifest's ``actions`` value.
        known_ids: Element ids an action may point at (the layout's zones).
        parameter_names: Declared parameter names (the targets of a parameter action).
        views: ``{element id: ViewZone}`` - the zones an action may run from or to.
        field_types: ``{datasource: {field: type}}``, for a parameter action's source field.
        errors: Accumulator for validation errors.
    """
    if not isinstance(actions, list):
        errors.append("actions: must be a list (use [] when the dashboard has none)")
        return

    for index, action in enumerate(actions):
        label = f"actions[{index}]"
        if not isinstance(action, dict):
            errors.append(f"{label}: must be an object with 'name', 'type', 'source', 'targets'")
            continue
        name = str(action.get("name", "")).strip()
        label = f"actions[{index}] '{name or '?'}'"
        if not name:
            errors.append(f"{label}: needs a 'name'")

        action_type = str(action.get("type", "")).strip().lower()
        if action_type not in ACTION_TYPES:
            errors.append(
                f"{label}: unknown action type '{action_type}' "
                f"(expected one of: {', '.join(sorted(ACTION_TYPES))})"
            )

        run_on = str(action.get("run_on", "")).strip().lower()
        if run_on and run_on not in ACTION_RUN_ON:
            errors.append(
                f"{label}: unknown run_on '{run_on}' "
                f"(expected one of: {', '.join(sorted(ACTION_RUN_ON))})"
            )

        source = str(action.get("source", "")).strip()
        if not source:
            errors.append(f"{label}: needs a 'source' element id")
        elif source not in known_ids:
            errors.append(f"{label}: source '{source}' is not a zone in the layout tree")
        elif source not in views:
            errors.append(
                f"{label}: source '{source}' is not a view - an action runs off the marks "
                f"the analyst clicks, so its source must be a zone a worksheet fills"
            )
        elif action_type == "parameter":
            source_field = _bare_field(action.get("field"))
            if not source_field:
                errors.append(
                    f"{label}: a parameter action needs a 'field' - the field whose value "
                    f"the clicked mark writes into the parameter"
                )
            elif source_field not in field_types.get(views[source].datasource, {}):
                errors.append(
                    f"{label}: field '{source_field}' is not a field of source "
                    f"'{source}'s datasource '{views[source].datasource}'"
                )

        targets = action.get("targets", [])
        target_names = [
            str(target).strip()
            for target in (targets if isinstance(targets, list) else [targets])
        ]
        if not target_names:
            errors.append(f"{label}: needs at least one 'targets' entry")
        for target in target_names:
            if not target:
                errors.append(f"{label}: has an empty 'targets' entry")
            elif action_type in {"filter", "highlight"} and target not in known_ids:
                errors.append(
                    f"{label}: target '{target}' is not a zone in the layout tree"
                )
            elif action_type in {"filter", "highlight"} and target not in views:
                errors.append(
                    f"{label}: target '{target}' is not a view - a {action_type} action "
                    f"acts on a worksheet's marks, not on a text or control zone"
                )
            elif action_type == "parameter" and target not in parameter_names:
                errors.append(
                    f"{label}: target '{target}' is not a declared parameter "
                    f"(declared: {', '.join(sorted(parameter_names)) or 'none'})"
                )


def _validate_parameters(parameters: object, errors: list[str]) -> set[str]:
    """Validate the parameters' names, types, and uniqueness.

    Args:
        parameters: The manifest's ``parameters`` value.
        errors: Accumulator for validation errors.

    Returns:
        The declared parameter names (what a parameter action may target).
    """
    seen: set[str] = set()
    if not isinstance(parameters, list):
        errors.append("parameters: must be a list (use [] when the dashboard has none)")
        return seen

    for index, parameter in enumerate(parameters):
        label = f"parameters[{index}]"
        if not isinstance(parameter, dict):
            errors.append(f"{label}: must be an object with 'name' and 'data_type'")
            continue
        name = str(parameter.get("name", "")).strip()
        label = f"parameters[{index}] '{name or '?'}'"
        if not name:
            errors.append(f"{label}: needs a 'name'")
        elif name in seen:
            errors.append(f"{label}: duplicate parameter name '{name}'")
        seen.add(name)

        data_type = str(parameter.get("data_type", "")).strip().lower()
        if not data_type:
            errors.append(f"{label}: needs a 'data_type' (string/integer/real/boolean/date)")
        elif data_type not in PARAMETER_DATA_TYPES:
            errors.append(
                f"{label}: unknown data_type '{data_type}' "
                f"(expected one of: {', '.join(sorted(PARAMETER_DATA_TYPES))})"
            )

        # A parameter has one domain: a member list, a numeric range, or anything. Two
        # domains would make the control's behaviour depend on which one the builder read.
        values, span = parameter.get("values"), parameter.get("range")
        if values is not None and not (isinstance(values, list) and values):
            errors.append(
                f"{label}: 'values' must be a non-empty list of the allowed values"
            )
        if span is not None:
            if not (
                isinstance(span, dict)
                and isinstance(span.get("min"), (int, float))
                and isinstance(span.get("max"), (int, float))
            ):
                errors.append(f"{label}: 'range' needs numeric 'min' and 'max'")
            elif values:
                errors.append(
                    f"{label}: has both 'values' and 'range' - a parameter's domain is a "
                    f"member list or a range, not both"
                )
    return seen


def _validate_visibility(
    bindings: list[tuple[str, str]], manifest_document: dict, errors: list[str]
) -> None:
    """Validate every layout node's ``visibility`` field (Dynamic Zone Visibility).

    Tableau shows or hides a zone on a single boolean value, and the builder qualifies that
    field against the datasource that declares it - so the field has to be a declared
    *calculated* field of type ``boolean``. A CSV column would need one value per view, which
    is not something the manifest can promise.

    Args:
        bindings: ``(path, field name)`` pairs from the layout walk.
        manifest_document: The whole manifest.
        errors: Accumulator for validation errors.
    """
    if not bindings:
        return
    booleans = {
        str(entry.get("name", "")).strip()
        for entry in manifest_document.get("calculated_fields") or []
        if isinstance(entry, dict)
        and str(entry.get("type", "")).strip().lower() == VISIBILITY_TYPE
    }
    for path, field_name in bindings:
        if field_name not in booleans:
            errors.append(
                f"{path}: visibility '{field_name}' is not a declared calculated field of "
                f"type '{VISIBILITY_TYPE}' (declared boolean: "
                f"{', '.join(sorted(booleans)) or 'none'})"
            )


def validate_manifest(
    manifest_document: dict, data_model_text: str, target_tableau_version: str
) -> list[str]:
    """Validate a build manifest against the data model and the project's target version.

    Every message names the offending entry (worksheet, field, element id) so a bad
    spec-to-manifest translation is fixed row-by-row instead of re-derived.

    Args:
        manifest_document: The parsed ``build-manifest.json``.
        data_model_text: The contents of ``DATA-MODEL.md`` (the field authority).
        target_tableau_version: STATE.md's ``target_tableau_version`` (CONTRACT.md §2).

    Returns:
        A list of error messages; empty when the manifest is buildable.
    """
    errors: list[str] = []
    missing = [key for key in REQUIRED_KEYS if key not in manifest_document]
    if missing:
        errors.append(f"missing required section(s): {', '.join(missing)}")

    declared_version = str(manifest_document.get("target_tableau_version", "")).strip()
    if not declared_version:
        errors.append(
            f"target_tableau_version: missing - copy STATE.md's '{target_tableau_version}' "
            f"(it drives the workbook's version attribute)"
        )
    elif declared_version != target_tableau_version:
        errors.append(
            f"target_tableau_version '{declared_version}' does not match STATE.md's "
            f"'{target_tableau_version}' - copy STATE.md's value"
        )

    layout_ids, container_ids, visibility = _validate_layout(
        manifest_document.get("layout"), errors
    )
    zone_ids = {
        element_id for element_id in layout_ids
        if not element_id.startswith(INTERACTION_PREFIX)
    }
    # A mapped container (a DZV panel, say) is filled by its children, not by a view of
    # its own - only leaf zones need a worksheet or an objects entry.
    leaf_zone_ids = zone_ids - container_ids

    declared_fields = _validate_datasources(
        manifest_document.get("datasources"), data_model_text, errors
    )
    calculated = _calculated_field_names(
        manifest_document, set(declared_fields), errors
    )
    filled = _validate_worksheets(
        manifest_document.get("worksheets"), declared_fields, calculated, zone_ids,
        container_ids, errors
    )

    views = _view_zones(manifest_document.get("worksheets"))
    field_types = _field_types(manifest_document, data_model_text)
    parameter_names = _validate_parameters(manifest_document.get("parameters", []), errors)
    filled |= _validate_objects(
        manifest_document.get("objects", []), zone_ids, container_ids, views, field_types,
        parameter_names, errors,
    )
    _validate_visibility(visibility, manifest_document, errors)

    # A leaf zone nothing fills would build an empty container - the spec mapped it to
    # something, so the translation dropped it.
    unfilled = sorted(leaf_zone_ids - filled)
    if unfilled:
        errors.append(
            "layout zone(s) that nothing fills: " + ", ".join(unfilled)
            + " (add a worksheet with that element_id, or an 'objects' entry for a "
            "filter card / text / image zone)"
        )

    _validate_actions(
        manifest_document.get("actions", []), zone_ids, parameter_names, views, field_types,
        errors,
    )
    return errors
