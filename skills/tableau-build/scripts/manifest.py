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
from typing import Optional

#: Sections a manifest must carry (actions/parameters may be empty lists, never absent -
#: "None" must be said explicitly so a forgotten section is not mistaken for "no actions").
REQUIRED_KEYS: tuple[str, ...] = (
    "target_tableau_version", "datasources", "worksheets", "layout", "actions", "parameters",
)

#: Mark/chart types the builder knows how to emit. Add a type here only once the builder
#: has a validated snippet for it - an unknown type must fail loudly, not build blank.
CHART_TYPES = frozenset({
    "bar", "line", "area", "pie", "scatter", "map", "text", "table",
    "heatmap", "histogram", "treemap", "bullet", "gantt", "boxplot",
})

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

#: Date parts a shelf/encoding entry may request of a date field.
DATE_PARTS = frozenset({
    "year", "quarter", "month", "week", "day", "hour", "minute", "date",
})

# A data-source heading in DATA-MODEL.md: "## Data source: `sales.csv`" -> "sales.csv".
_DATASOURCE_HEADING = re.compile(
    r"^#{1,6}\s+data source:\s*`?([^`\s|]+\.csv)`?\s*$", re.IGNORECASE
)


def documented_fields(data_model_text: str) -> dict[str, set[str]]:
    """Recover ``{csv filename: {field name}}`` from a DATA-MODEL.md.

    Only the field table's first column is needed here (types are the data step's
    business); the parse is the tolerant mirror of ``tableau-data``'s renderer, so it keeps
    working after the model enriches the Role/Description columns.

    Args:
        data_model_text: The contents of a ``DATA-MODEL.md`` file.

    Returns:
        A mapping of CSV filename to the set of field names documented for it.
    """
    documented: dict[str, set[str]] = {}
    current: Optional[str] = None
    in_field_table = False

    for raw_line in data_model_text.splitlines():
        line = raw_line.strip()
        heading = _DATASOURCE_HEADING.match(line)
        if heading:
            current = heading.group(1)
            documented.setdefault(current, set())
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
        documented[current].add(name)
    return documented


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
    node: object, path: str, ids: list[str], container_ids: set[str], errors: list[str]
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
    """
    if not isinstance(node, dict):
        errors.append(f"{path}: every layout node must be a JSON object")
        return

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
            child, f"{path}.children[{index}]", ids, container_ids, errors
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


def _validate_layout(layout: object, errors: list[str]) -> tuple[list[str], set[str]]:
    """Validate the layout section and return what it places.

    Args:
        layout: The manifest's ``layout`` value.
        errors: Accumulator for validation errors.

    Returns:
        ``(placed ids, container ids)``. A container id is filled by its children, so it
        needs no worksheet or object of its own.
    """
    if not isinstance(layout, dict):
        errors.append("layout: must be an object with 'canvas' and 'root' (copy the spec's)")
        return [], set()

    canvas = layout.get("canvas")
    if not (
        isinstance(canvas, dict)
        and isinstance(canvas.get("width"), (int, float))
        and isinstance(canvas.get("height"), (int, float))
    ):
        errors.append("layout.canvas: needs numeric 'width' and 'height' (the mock's px size)")

    ids: list[str] = []
    container_ids: set[str] = set()
    if isinstance(layout.get("root"), dict):
        _collect_layout_ids(layout["root"], "layout.root", ids, container_ids, errors)
    else:
        errors.append("layout.root: must be a container object ('type' + 'children')")

    duplicates = sorted({placed for placed in ids if ids.count(placed) > 1})
    if duplicates:
        errors.append("layout places id(s) more than once: " + ", ".join(duplicates))
    return ids, container_ids


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
    """Return the ``(where, entry)`` pairs a worksheet's shelves/encodings reference.

    Each entry is either a bare field name (``"revenue"``) or an object carrying the
    aggregation / date part the builder must apply
    (``{"field": "revenue", "aggregation": "sum"}``).

    Args:
        worksheet: One ``worksheets`` entry.

    Returns:
        ``(where, entry)`` pairs, where ``where`` is e.g. ``shelves.rows`` / ``encodings.color``.
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
    if isinstance(entry, dict):
        field_name = _bare_field(entry.get("field"))
        # A JSON null (or any other non-string) means "not specified", same as a missing
        # key - never coerce it to a string, or `null` reads as the literal text "none".
        raw_aggregation = entry.get("aggregation", "")
        aggregation = raw_aggregation.strip().lower() if isinstance(raw_aggregation, str) else ""
        raw_date_part = entry.get("date_part", "")
        date_part = raw_date_part.strip().lower() if isinstance(raw_date_part, str) else ""
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
    if not isinstance(worksheets, list) or not worksheets:
        errors.append("worksheets: must be a non-empty list (one per mock zone that is a view)")
        return filled

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
    return filled


def _validate_objects(
    objects: object, layout_ids: set[str], container_ids: set[str], errors: list[str]
) -> set[str]:
    """Validate the non-worksheet dashboard objects and return the zones they fill.

    Args:
        objects: The manifest's optional ``objects`` value.
        layout_ids: The element ids the layout tree places.
        container_ids: Mapped-container ids (filled by their children, not an object of
            their own - CONTRACT.md §1.1).
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
    return filled


def _validate_actions(
    actions: object, known_ids: set[str], parameter_names: set[str], errors: list[str]
) -> None:
    """Validate the dashboard actions' types and endpoints.

    The **source** is always a zone (the view whose marks the analyst clicks). What a
    **target** is depends on the type: ``filter`` / ``highlight`` target other zones, a
    ``parameter`` action targets a declared parameter, and ``set`` / ``url`` actions target
    a set / a URL that this schema does not model - those are left to the builder.

    Args:
        actions: The manifest's ``actions`` value.
        known_ids: Element ids an action may point at (the layout's zones).
        parameter_names: Declared parameter names (the targets of a parameter action).
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

        source = str(action.get("source", "")).strip()
        if not source:
            errors.append(f"{label}: needs a 'source' element id")
        elif source not in known_ids:
            errors.append(f"{label}: source '{source}' is not a zone in the layout tree")

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
        if not str(parameter.get("data_type", "")).strip():
            errors.append(f"{label}: needs a 'data_type' (string/integer/real/boolean/date)")
    return seen


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

    layout_ids, container_ids = _validate_layout(manifest_document.get("layout"), errors)
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

    filled |= _validate_objects(
        manifest_document.get("objects", []), zone_ids, container_ids, errors
    )

    # A leaf zone nothing fills would build an empty container - the spec mapped it to
    # something, so the translation dropped it.
    unfilled = sorted(leaf_zone_ids - filled)
    if unfilled:
        errors.append(
            "layout zone(s) that nothing fills: " + ", ".join(unfilled)
            + " (add a worksheet with that element_id, or an 'objects' entry for a "
            "filter card / text / image zone)"
        )

    parameter_names = _validate_parameters(manifest_document.get("parameters", []), errors)
    _validate_actions(
        manifest_document.get("actions", []), zone_ids, parameter_names, errors
    )
    return errors
