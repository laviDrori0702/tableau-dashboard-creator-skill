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
from typing import Iterator, NamedTuple, Optional

from features import ACTIVATIONS, CLEAR_VALUE_TAGS, PARAMETER_TYPES
# Names, not the module: half the functions below take a parameter called ``worksheet``.
from worksheet import (
    CHART_SPECS,
    DATE_PART_DERIVATIONS,
    ENCODING_ORDER,
    FIT_ZOOMS,
    NO_FORMAT,
    REFERENCE_LINE_FORMULAS,
    REFERENCE_LINE_LABEL_TYPES,
    REFERENCE_LINE_SCOPES,
    SHEET_FORMAT_KEYS,
    TABLE_CALC_PREFIXES,
    TEXT_ALIGNMENTS,
    VERTICAL_ALIGNMENTS,
    CalculatedField,
    aggregate_calculated_fields,
    parse_design_tokens,
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

#: Dashboard action types (the Tableau constructs behind CONTRACT.md §6's vocabulary), and
#: the ones this builder knows the *name* of but emits nothing for. An unemitted type has to
#: fail validation: accepting it would build a dashboard whose interaction is silently absent,
#: and CONTRACT.md §6's `drill` - the only term that reaches for a set action - is buildable
#: as a parameter action instead.
ACTION_TYPES = frozenset({"filter", "highlight", "parameter"})
UNBUILDABLE_ACTION_TYPES = frozenset({"set", "url"})

#: Non-worksheet dashboard objects, for layout zones no view fills (a filter card, a title,
#: a logo, a legend). Every *leaf* zone must be filled by a worksheet or one of these; a
#: mapped container (see ``container_ids`` below) is filled by its children instead.
OBJECT_KINDS = frozenset({"filter", "parameter", "text", "image", "legend", "button", "blank"})

#: Valid container orientations in the layout tree (mirror of the spec's Layout section).
CONTAINER_TYPES = frozenset({"vert", "horz"})

#: Layout/mapping ids with this prefix are dashboard actions, not zones (plan convention).
INTERACTION_PREFIX = "int-"

#: Aggregations a shelf/encoding entry may request. ``"none"`` pins a dimension/exact value;
#: on a plain measure it is the analyst asking for a *discrete* pill (a row header, not an
#: axis), and on an aggregate calculated field it means "do not re-aggregate" - see
#: :meth:`worksheet.FieldResolver.reference`.
AGGREGATIONS = frozenset({
    "sum", "avg", "min", "max", "count", "countd", "median", "attr", "none",
})

#: Date parts a shelf/encoding entry may request of a date field, and the encoding names an
#: ``encodings`` block may use. Both come off the builder's tables for the same reason as
#: :data:`CHART_TYPES`: an entry this module accepts but the builder has no case for is
#: dropped silently, and Tableau shows no sign of the missing encoding.
DATE_PARTS = frozenset(DATE_PART_DERIVATIONS)
ENCODING_NAMES = frozenset(ENCODING_ORDER)

#: How a sheet may be told to fill its zone, and the ``format`` block's alignment values -
#: both read off the builder's own tables, same reason as :data:`CHART_TYPES`. The block's
#: key set is the builder's :data:`SHEET_FORMAT_KEYS`, used directly.
FITS = frozenset(FIT_ZOOMS)
FORMAT_ALIGNMENTS: dict[str, frozenset[str]] = {
    "align": TEXT_ALIGNMENTS, "vertical_align": VERTICAL_ALIGNMENTS,
}

#: A colour value in a ``format`` block: a hex, or ``none`` for "no such border/line".
_FORMAT_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

#: Types a quick-filter card can render: it lists the field's *members*, so a date or numeric
#: field belongs in a worksheet filter with bounds instead (see :data:`zones.FILTER_MODES`).
CARD_FILTER_TYPES = frozenset({"string", "boolean"})

#: The type a Dynamic Zone Visibility field must have - a zone is shown or hidden, nothing else.
VISIBILITY_TYPE = "boolean"

#: The parameter data types a parameter action may target: exactly those whose reset value
#: has an attested ``<clear-option>`` serialization. Deselecting has to reset the parameter
#: (:func:`features.serialize_clear_value`), or a panel the parameter reveals has nothing to
#: hide it again and the viewer is stuck - so a type we could not serialize the reset for is
#: rejected rather than built without it. ``real``, ``date`` and ``datetime`` are the ones
#: still out (issue #49).
PARAMETER_ACTION_TARGET_TYPES = frozenset(CLEAR_VALUE_TAGS)

#: Which field types may feed which parameter data type. A parameter action writes the
#: clicked mark's value straight into the parameter with no conversion, and Desktop's Change
#: Parameter editor only offers fields of the parameter's own type - a workbook pairing them
#: otherwise is one Desktop refuses on open. Whole and decimal numbers count as one type
#: here, the way Tableau's own number family does. Keys mirror
#: :data:`PARAMETER_ACTION_TARGET_TYPES`; the lookup below relies on that.
PARAMETER_ACTION_FIELD_TYPES: dict[str, frozenset[str]] = {
    "string": frozenset({"string"}),
    "integer": frozenset({"integer", "real"}),
    "boolean": frozenset({"boolean"}),
}

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

def walk_layout(node: object, path: str = "root") -> Iterator[tuple[str, object]]:
    """Yield ``(path, node)`` for the layout subtree at ``node``, parents before children.

    The one recursion over the tree that reads it: :func:`_collect_layout_ids` and
    ``twb``'s visibility wiring each filter what they need out of it. (``zones`` keeps its
    own recursion - it divides a rectangle and nests XML on the way down, so it needs the
    call stack, not a flat sequence of nodes.) Every child is yielded whatever its type,
    because a caller that validates has to see a non-object node to reject it; only nodes
    that are objects with a ``children`` *list* are descended into.

    Args:
        node: The JSON value at this position in the tree.
        path: Human-readable position of ``node`` (e.g. ``layout.root.children[1]``).

    Yields:
        ``(path, node)`` per node, depth-first and pre-order.
    """
    yield path, node
    if isinstance(node, dict) and isinstance(node.get("children"), list):
        for index, child in enumerate(node["children"]):
            yield from walk_layout(child, f"{path}.children[{index}]")


def _collect_layout_ids(
    root: object, path: str, ids: list[str], container_ids: set[str], errors: list[str],
    visibility: Optional[list[tuple[str, str]]] = None,
) -> None:
    """Walk a layout subtree, recording every placed id and any structural error.

    A node is a container (``type`` + non-empty ``children``), a leaf (``id``), or both (a
    **mapped container**, e.g. a DZV panel that is itself an element and holds further
    zones - CONTRACT.md §1.1). A mapped container's id is recorded in ``container_ids``
    because its children fill it; only *leaf* zones need a worksheet or object of their own.
    Sibling ``size`` percentages were already reconciled against the mock by
    ``tableau-spec``; here the tree only has to be structurally sound and consistent with
    the worksheets.

    Args:
        root: The JSON value at the root of the subtree.
        path: Human-readable position of ``root`` (e.g. ``layout.root``) for messages.
        ids: Accumulator for placed element ids.
        container_ids: Accumulator for the ids of nodes that hold children.
        errors: Accumulator for validation errors.
        visibility: Accumulator for ``(path, field name)`` of every node whose zone is shown
            or hidden by a boolean field (Dynamic Zone Visibility), or ``None`` to ignore them.
    """
    for node_path, node in walk_layout(root, path):
        if not isinstance(node, dict):
            errors.append(f"{node_path}: every layout node must be a JSON object")
            continue

        if visibility is not None and node.get("visibility") is not None:
            shown_by = node.get("visibility")
            if isinstance(shown_by, str) and shown_by.strip():
                visibility.append((node_path, shown_by.strip()))
            else:
                errors.append(
                    f"{node_path}: 'visibility' must be the name of a boolean calculated "
                    f"field (got {shown_by!r})"
                )

        element_id, children = node.get("id"), node.get("children")
        if element_id is None and children is None:
            errors.append(
                f"{node_path}: a node needs an 'id' (leaf), 'children' (container), or both"
            )
            continue
        if isinstance(element_id, str) and element_id.strip():
            placed = element_id.strip()
            ids.append(placed)
            if children is not None:
                container_ids.add(placed)
            if placed.startswith(INTERACTION_PREFIX):
                errors.append(
                    f"{node_path}: interaction id '{placed}' occupies no zone - dashboard "
                    f"actions belong in 'actions', never in the layout tree (CONTRACT.md §1.1)"
                )
        elif element_id is not None:
            errors.append(f"{node_path}: 'id' must be a non-empty string")

        if children is None:
            continue
        if node.get("type") not in CONTAINER_TYPES:
            errors.append(
                f"{node_path}: container 'type' must be 'vert' or 'horz' "
                f"(got {node.get('type')!r})"
            )
        # walk_layout descends the list itself; an unusable 'children' is only reported here.
        if not isinstance(children, list) or not children:
            errors.append(f"{node_path}: 'children' must be a non-empty list")


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


def _aggregate_calculated_fields(manifest_document: dict) -> dict[str, frozenset[str]]:
    """Return ``{datasource name: {calculated fields that aggregate}}``.

    Args:
        manifest_document: The whole manifest.

    Returns:
        Per datasource, the calculated fields whose formula aggregates directly or references
        one that does - the builder's own closure (:func:`worksheet.aggregate_calculated_fields`),
        so validation and emission cannot disagree about which fields these are.
    """
    formulas: dict[str, dict[str, CalculatedField]] = {}
    for entry in manifest_document.get("calculated_fields") or []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("datasource", "")).strip()
        name = str(entry.get("name", "")).strip()
        if source and name:
            formulas.setdefault(source, {})[name] = CalculatedField(
                str(entry.get("formula", "")), ""
            )
    return {
        source: aggregate_calculated_fields(declared)
        for source, declared in formulas.items()
    }


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


class FieldCatalog(NamedTuple):
    """One datasource's field authority - what a field reference may resolve to.

    ``errors`` is the *same* accumulator list every other validator appends to - the tuple
    holds a reference to that list, it does not copy it.

    Attributes:
        fields: Field names the datasource offers (declared + calculated) - what a
            reference may name.
        source: The datasource name, for the message.
        aggregate_calcs: The calculated fields that already aggregate; asking one of them
            for a *second* aggregation is rejected.
        errors: Accumulator for validation errors.
    """

    fields: set[str]
    source: str
    aggregate_calcs: frozenset[str]
    errors: list[str]


def _field_catalogs(
    declared_fields: dict[str, set[str]],
    calculated: dict[str, set[str]],
    aggregate_calcs: dict[str, frozenset[str]],
    errors: list[str],
) -> dict[str, FieldCatalog]:
    """Index one :class:`FieldCatalog` per declared datasource.

    Args:
        declared_fields: ``{datasource: {field}}`` from :func:`_validate_datasources`.
        calculated: ``{datasource: {calculated field}}`` from
            :func:`_calculated_field_names`.
        aggregate_calcs: ``{datasource: {calculated field that already aggregates}}`` from
            :func:`_aggregate_calculated_fields`.
        errors: Accumulator for validation errors, shared by every catalog.

    Returns:
        ``{datasource name: FieldCatalog}``, keyed by the *declared* datasources - a
        calculated field on an undeclared source is reported by its own validator.
    """
    return {
        source: FieldCatalog(
            fields=fields | calculated.get(source, set()),
            source=source,
            aggregate_calcs=aggregate_calcs.get(source, frozenset()),
            errors=errors,
        )
        for source, fields in declared_fields.items()
    }


def _validate_reference(
    label: str, where: str, entry: object, catalog: FieldCatalog,
) -> None:
    """Validate one shelf/encoding entry against a datasource's fields.

    Args:
        label: The worksheet's error label.
        where: The shelf/encoding the entry sits on.
        entry: The raw entry (a field name, or an object with ``field`` +
            optional ``aggregation`` / ``date_part``).
        catalog: The datasource's field authority, and the error accumulator.
    """
    errors = catalog.errors
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
    if field_name not in catalog.fields:
        errors.append(
            f"{label}: {where} references '{field_name}', which is not a field of "
            f"datasource '{catalog.source}' nor a declared calculated field"
        )
    if aggregation and aggregation not in AGGREGATIONS:
        errors.append(
            f"{label}: {where} '{field_name}' has unknown aggregation '{aggregation}' "
            f"(expected one of: {', '.join(sorted(AGGREGATIONS))})"
        )
    elif aggregation not in ("", "none") and field_name in catalog.aggregate_calcs:
        # Issue #62: SUM(SUM([profit]) / SUM([revenue])) is an error Tableau refuses at load.
        # The builder emits the un-aggregated 'usr:' instance for such a field whatever the
        # key says, so an aggregation here would be silently discarded - caught instead.
        errors.append(
            f"{label}: {where} '{field_name}' asks for aggregation '{aggregation}', but it "
            f"is a calculated field that already aggregates - Tableau refuses a second "
            f"aggregation. Drop the key, or use \"aggregation\": \"none\" to say "
            f"'do not re-aggregate'"
        )
    if date_part and date_part not in DATE_PARTS:
        errors.append(
            f"{label}: {where} '{field_name}' has unknown date_part '{date_part}' "
            f"(expected one of: {', '.join(sorted(DATE_PARTS))})"
        )
    if field_name in catalog.aggregate_calcs:
        # Issue #62, same rule as the aggregation check above: a date part and a bin are both
        # derived from a row-level value, which an aggregate calculated field does not have.
        # MIN([order_date]) with "date_part": "year" reached Desktop as [tyr:...] and was
        # refused with the same "user-defined aggregate" error the usr: fix removes.
        for key, value in (("date_part", date_part), ("bin", bin_size)):
            if value in ("", None):
                continue
            errors.append(
                f"{label}: {where} '{field_name}' asks for {key} '{value}', but it is a "
                f"calculated field that already aggregates - Tableau cannot derive a "
                f"{key} from a field with no row-level value"
            )
    if bin_size is not None and not (
        isinstance(bin_size, (int, float)) and not isinstance(bin_size, bool) and bin_size > 0
    ):
        errors.append(
            f"{label}: {where} '{field_name}' has a 'bin' of {bin_size!r} - a bin width "
            f"must be a positive number (e.g. 500 for a histogram of 0-500, 500-1000, ...)"
        )
    if table_calc and table_calc not in TABLE_CALC_PREFIXES:
        errors.append(
            f"{label}: {where} '{field_name}' has unknown table_calc '{table_calc}' "
            f"(expected one of: {', '.join(sorted(TABLE_CALC_PREFIXES))})"
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

    # 'legend' opts out of the generated dashboard legend zone (issue #65). A truthy string
    # like "false" would read as opt-in, so only a real boolean is accepted.
    legend = worksheet.get("legend")
    if legend is not None and not isinstance(legend, bool):
        errors.append(
            f"{label}: 'legend' must be true or false (it suppresses the generated "
            f"dashboard legend zone); got {legend!r}"
        )

    sort = worksheet.get("sort")
    if isinstance(sort, dict):
        order = sort.get("order")
        if sort.get("by") is None and not (isinstance(order, list) and order):
            errors.append(
                f"{label}: 'sort' has neither 'by' (the measure to sort on) nor 'order' "
                f"(an explicit member list) - one of the two is required"
            )

    fit = worksheet.get("fit")
    if isinstance(fit, str) and fit.strip().lower() not in FITS:
        errors.append(
            f"{label}: unknown fit '{fit}' (expected one of: {', '.join(sorted(FITS))})"
        )

    _validate_sheet_format(label, worksheet.get("format"), errors)


def _validate_sheet_format(label: str, block: object, errors: list[str]) -> None:
    """Reject a ``format`` block the builder would render as nothing.

    A misspelled key or a colour Tableau cannot parse is the worst kind of formatting bug:
    the workbook opens, nothing is wrong with it, and the sheet simply is not formatted.

    Args:
        label: The worksheet's error label.
        block: The worksheet's ``format`` value, if any.
        errors: Accumulator for validation errors.
    """
    if block is None:
        return
    if not isinstance(block, dict):
        errors.append(
            f"{label}: 'format' must be an object "
            f"(keys: {', '.join(sorted(SHEET_FORMAT_KEYS))})"
        )
        return

    for key, value in block.items():
        if key not in SHEET_FORMAT_KEYS:
            errors.append(
                f"{label}: unknown format key '{key}' "
                f"(expected one of: {', '.join(sorted(SHEET_FORMAT_KEYS))})"
            )
            continue
        text = value.strip().lower() if isinstance(value, str) else ""
        if not text:
            errors.append(f"{label}: format.{key} needs a value")
        elif key in FORMAT_ALIGNMENTS:
            if text not in FORMAT_ALIGNMENTS[key]:
                errors.append(
                    f"{label}: format.{key} '{value}' is not an alignment "
                    f"(expected one of: {', '.join(sorted(FORMAT_ALIGNMENTS[key]))})"
                )
        elif not _FORMAT_COLOR.match(text) and not (
            # A border or a line can be turned off; a background cannot be shaded "none".
            text == NO_FORMAT and key != "shading"
        ):
            errors.append(
                f"{label}: format.{key} '{value}' is not a '#rrggbb' colour"
                + ("" if key == "shading" else f" nor '{NO_FORMAT}'")
            )


def _validate_worksheets(
    worksheets: object,
    catalogs: dict[str, FieldCatalog],
    layout_ids: set[str],
    container_ids: set[str],
    errors: list[str],
    palette_names: Optional[set[str]] = None,
) -> set[str]:
    """Validate the worksheets against the datasources and the layout tree.

    Args:
        worksheets: The manifest's ``worksheets`` value.
        catalogs: ``{datasource: FieldCatalog}`` from :func:`_field_catalogs` - the
            declared datasources, and what a reference to each may resolve to.
        layout_ids: The element ids the layout tree places.
        container_ids: Mapped-container ids (filled by their children, not a view of
            their own - CONTRACT.md §1.1).
        errors: Accumulator for validation errors.
        palette_names: The lower-cased field names DESIGN-TOKENS.md carries a series table
            for - what a worksheet's ``palette`` key may name (issue #67).

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

        # A 'palette' that names no series table would silently fall back to the whole
        # ordered list - the exact wrong-colours symptom the key exists to fix (issue #67).
        palette = str(worksheet.get("palette", "")).strip()
        if palette and palette.lower() not in (palette_names or set()):
            known = ", ".join(sorted(palette_names or set())) or "none"
            errors.append(
                f"{label}: palette '{palette}' names no series table in DESIGN-TOKENS.md "
                f"under '### Chart series colors' (tables found: {known})"
            )

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
        if source not in catalogs:
            errors.append(
                f"{label}: unknown datasource '{source}' "
                f"(declared: {', '.join(sorted(catalogs)) or 'none'})"
            )
            continue

        catalog = catalogs[source]
        for where, entry in _worksheet_field_references(worksheet):
            _validate_reference(label, where, entry, catalog)
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


class ValidationContext(NamedTuple):
    """The tables the objects/actions validators all read, travelling as one value.

    ``errors`` is the *same* accumulator list every other validator appends to - the tuple
    holds a reference to that list, it does not copy it.

    Attributes:
        views: ``{element id: ViewZone}`` - the zones a filter card may filter and an action
            may run from or to.
        field_types: ``{datasource: {field: type}}``.
        parameter_types: ``{declared parameter name: data type}``.
        errors: Accumulator for validation errors.
    """

    views: dict[str, ViewZone]
    field_types: dict[str, dict[str, str]]
    parameter_types: dict[str, str]
    errors: list[str]


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
    context: ValidationContext,
) -> set[str]:
    """Validate the non-worksheet dashboard objects and return the zones they fill.

    Args:
        objects: The manifest's optional ``objects`` value.
        layout_ids: The element ids the layout tree places.
        container_ids: Mapped-container ids (filled by their children, not an object of
            their own - CONTRACT.md §1.1).
        context: The shared validation tables, and the error accumulator.

    Returns:
        The element ids the objects fill.
    """
    errors = context.errors
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
            _validate_filter_card(label, dashboard_object, context)
        elif kind == "parameter":
            parameter = str(dashboard_object.get("parameter", "")).strip()
            if parameter not in context.parameter_types:
                errors.append(
                    f"{label}: 'parameter' is {parameter or 'missing'} - a parameter control "
                    f"names one declared parameter (declared: "
                    f"{', '.join(sorted(context.parameter_types)) or 'none'})"
                )
    return filled


def _validate_filter_card(
    label: str, dashboard_object: dict, context: ValidationContext,
) -> None:
    """Validate one quick-filter card against the worksheet it filters.

    A card is the UI for one worksheet's filter, so it has to name that worksheet *and* a
    field of the worksheet's datasource. The field also has to be one whose members a card can
    list - a card over a date or a measure would need bounds, which belong on the worksheet's
    ``filters`` instead.

    Args:
        label: The object's error label.
        dashboard_object: The ``objects`` entry.
        context: The shared validation tables, and the error accumulator.
    """
    errors = context.errors
    sheet_names = {view.name: view.datasource for view in context.views.values()}
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
        datatype = context.field_types.get(sheet_names[sheet], {}).get(field_name)
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
    actions: object, known_ids: set[str], context: ValidationContext,
) -> None:
    """Validate the dashboard actions' types and endpoints.

    The **source** is always a *view* zone: an action runs off the marks the analyst clicks,
    and a text or filter zone has none. What a **target** is depends on the type: ``filter`` /
    ``highlight`` target other view zones, and a ``parameter`` action targets a declared
    parameter (and needs the ``field`` whose value it writes there). ``set`` / ``url`` are
    rejected outright - see :data:`UNBUILDABLE_ACTION_TYPES`.

    Args:
        actions: The manifest's ``actions`` value.
        known_ids: Element ids an action may point at (the layout's zones).
        context: The shared validation tables, and the error accumulator - ``views`` are the
            zones an action may run from or to, ``field_types`` types a parameter action's
            source field, and ``parameter_types`` holds its possible targets, whose data type
            is what :data:`PARAMETER_ACTION_TARGET_TYPES` is checked against.
    """
    views, field_types = context.views, context.field_types
    parameter_types, errors = context.parameter_types, context.errors
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
        if action_type in UNBUILDABLE_ACTION_TYPES:
            errors.append(
                f"{label}: '{action_type}' actions are not emitted by this builder - a "
                f"dashboard that needs one would open with the interaction missing. Express "
                f"a drill as a 'parameter' action (CONTRACT.md section 6) instead"
            )
        elif action_type not in ACTION_TYPES:
            errors.append(
                f"{label}: unknown action type '{action_type}' "
                f"(expected one of: {', '.join(sorted(ACTION_TYPES))})"
            )

        run_on = str(action.get("run_on", "")).strip().lower()
        if run_on and run_on not in ACTIVATIONS:
            errors.append(
                f"{label}: unknown run_on '{run_on}' "
                f"(expected one of: {', '.join(sorted(ACTIVATIONS))})"
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

        # The clicked mark's field is what lands in the parameter, so the targets loop
        # below needs its type as well as the target's. Empty when either end is already
        # broken - that error is reported once, above, rather than again per target.
        source_field = _bare_field(action.get("field")) or ""
        source_field_type = (
            field_types.get(views[source].datasource, {}).get(source_field, "")
            if action_type == "parameter" and source in views else ""
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
            elif action_type == "parameter" and target not in parameter_types:
                errors.append(
                    f"{label}: target '{target}' is not a declared parameter "
                    f"(declared: {', '.join(sorted(parameter_types)) or 'none'})"
                )
            elif (
                action_type == "parameter"
                and parameter_types[target] not in PARAMETER_ACTION_TARGET_TYPES
            ):
                errors.append(
                    f"{label}: target parameter '{target}' is "
                    f"'{parameter_types[target]}' - a parameter action's target must be one "
                    f"of {', '.join(sorted(PARAMETER_ACTION_TARGET_TYPES))}, because only "
                    f"those have an attested reset-value serialization and without the reset "
                    f"a zone the parameter reveals never hides again. Redeclare it as "
                    f"whichever of those the value really is, with a 'values' domain"
                )
            elif (
                action_type == "parameter"
                and source_field_type
                and source_field_type
                not in PARAMETER_ACTION_FIELD_TYPES[parameter_types[target]]
            ):
                errors.append(
                    f"{label}: field '{source_field}' is '{source_field_type}' but target "
                    f"parameter '{target}' is '{parameter_types[target]}' - a parameter "
                    f"action writes the clicked mark's value straight into the parameter, so "
                    f"Desktop only offers fields of the parameter's own type. Match them"
                )


def _validate_parameters(parameters: object, errors: list[str]) -> dict[str, str]:
    """Validate the parameters' names, types, current values, and uniqueness.

    Args:
        parameters: The manifest's ``parameters`` value.
        errors: Accumulator for validation errors.

    Returns:
        ``{declared parameter name: data type}`` - what a parameter action or a parameter
        control may target, and the type each one has.
    """
    seen: dict[str, str] = {}
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

        data_type = str(parameter.get("data_type", "")).strip().lower()
        seen[name] = data_type
        if not data_type:
            errors.append(f"{label}: needs a 'data_type' (string/integer/real/boolean/date)")
        elif data_type not in PARAMETER_TYPES:
            errors.append(
                f"{label}: unknown data_type '{data_type}' "
                f"(expected one of: {', '.join(sorted(PARAMETER_TYPES))})"
            )

        # A parameter *is* its current value: the value is the column's calculation, so
        # without one the control opens on nothing and every calc reading it is undefined.
        if parameter.get("current_value") is None:
            errors.append(
                f"{label}: needs a 'current_value' - the value the parameter opens on, and "
                f"the value a parameter action resets it to"
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

    Tableau shows or hides a zone on a single boolean value, so the name has to be either a
    declared *calculated* field of type ``boolean`` or a declared ``boolean`` **parameter** -
    Desktop binds the datagraph straight to ``[Parameters].[Name]`` for the latter, which is
    the simpler wiring when a parameter action is what drives the zone (no comparison calc in
    between). A CSV column would need one value per view, which is not something the manifest
    can promise.

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
    boolean_parameters = {
        str(entry.get("name", "")).strip()
        for entry in manifest_document.get("parameters") or []
        if isinstance(entry, dict)
        and str(entry.get("data_type", "")).strip().lower() == VISIBILITY_TYPE
    }
    for path, field_name in bindings:
        if field_name not in booleans | boolean_parameters:
            errors.append(
                f"{path}: visibility '{field_name}' is not a declared '{VISIBILITY_TYPE}' "
                f"calculated field or parameter (declared boolean: "
                f"{', '.join(sorted(booleans | boolean_parameters)) or 'none'})"
            )


def validate_manifest(
    manifest_document: dict,
    data_model_text: str,
    target_tableau_version: str,
    design_tokens_text: str = "",
) -> list[str]:
    """Validate a build manifest against the data model and the project's target version.

    Every message names the offending entry (worksheet, field, element id) so a bad
    spec-to-manifest translation is fixed row-by-row instead of re-derived.

    Args:
        manifest_document: The parsed ``build-manifest.json``.
        data_model_text: The contents of ``DATA-MODEL.md`` (the field authority).
        target_tableau_version: STATE.md's ``target_tableau_version`` (CONTRACT.md §2).
        design_tokens_text: The contents of ``DESIGN-TOKENS.md`` (``""`` when there was no
            branding step) - the authority for a worksheet's ``palette`` name.

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
    catalogs = _field_catalogs(
        declared_fields, calculated, _aggregate_calculated_fields(manifest_document), errors
    )
    filled = _validate_worksheets(
        manifest_document.get("worksheets"), catalogs, zone_ids, container_ids, errors,
        set(parse_design_tokens(design_tokens_text).field_palettes),
    )

    context = ValidationContext(
        views=_view_zones(manifest_document.get("worksheets")),
        field_types=_field_types(manifest_document, data_model_text),
        parameter_types=_validate_parameters(manifest_document.get("parameters", []), errors),
        errors=errors,
    )
    filled |= _validate_objects(
        manifest_document.get("objects", []), zone_ids, container_ids, context
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

    _validate_actions(manifest_document.get("actions", []), zone_ids, context)
    return errors
