"""The deterministic ``.twb`` assembler of tableau-build (CONTRACT.md step 8).

Everything Tableau is strict about - the order of ``<workbook>``'s children, the uniqueness
of generated ids, the fact that every datasource column must appear in *four* places, the
version-dependent ``<explain-data>`` element - is expressed here as code rather than as a
checklist an agent has to remember. A manifest that :mod:`manifest` accepted therefore
builds a workbook that is correct by construction, which is what stops "Tableau refuses to
open the file" at the source.

The module is **pure** and stdlib-only: it takes the manifest, the ``DATA-MODEL.md`` text and
the CSV header rows, and returns XML as a string. No filesystem, no ``STATE.md`` - that is
:mod:`build`'s job, and it is why the tests can drive :func:`render_workbook` directly.

Two authorities, deliberately kept apart:

* the **CSV header** is the physical schema (which columns exist, and in what order) - an
  incomplete ``relation > columns`` is a schema mismatch the moment Tableau loads the file;
* ``DATA-MODEL.md`` is the **field authority** (CONTRACT.md §3) - it supplies each column's
  type, from which the role, aggregation and remote-type all follow.

The manifest's own ``fields[].type`` is validated but not read here: one authority, no drift.

Scope: datasources, worksheet bodies (:mod:`worksheet` owns the shelves, encodings, marks
and styling), the dashboard's size and zone tree (:mod:`zones` owns the geometry), windows,
and version targeting.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import NamedTuple

import features
import worksheet
import zones
from manifest import documented_field_types

logger = logging.getLogger(__name__)

# --- Version targeting (CONTRACT.md §2 values) -------------------------------

#: STATE.md's ``target_tableau_version`` for the newer Tableau line.
TARGET_2026 = "2026.1+"

#: Workbook ``version`` / ``original-version`` per target. 18.1 is the document format
#: Tableau 2024.2-2025.x writes; 26.1 is 2026.1's.
_WORKBOOK_VERSION = {TARGET_2026: "26.1"}
_DEFAULT_WORKBOOK_VERSION = "18.1"

#: Free-text provenance string; no verified 2026.1 build string exists, so both targets
#: carry the one we have observed.
SOURCE_BUILD = "2025.1.10 (20251.25.1121.1650)"

#: The document-format feature flags, copied verbatim from a Tableau-saved workbook.
#: Adding or removing one changes Tableau's behaviour - keep the set as-is. Tableau writes
#: them alphabetically, which is why :data:`SORT_FORMAT_FLAG` can just be sorted in.
FORMAT_CHANGE_FLAGS: tuple[str, ...] = (
    "AccessibleZoneTabOrder",
    "AnimationOnByDefault",
    "AutoCreateAndUpdateDSDPhoneLayouts",
    "MarkAnimation",
    "ObjectModelEncapsulateLegacy",
    "ObjectModelTableType",
    "SchemaViewerObjectModel",
    "SetMembershipControl",
    "SheetIdentifierTracking",
    "WindowsPersistSimpleIdentifiers",
    "ZoneFriendlyName",
)

#: The extra flag a workbook containing a sorted worksheet carries
#: (WORKSHEETS.md:512, seen in ``bar-chart-sorted.twb`` and ``custom-tooltip.twb``).
SORT_FORMAT_FLAG = "SortTagCleanup"

#: The single dashboard the builder emits.
DASHBOARD_NAME = "Dashboard 1"

#: Dashboard size when the layout carries no canvas. ``manifest.validate_manifest`` requires
#: numeric ``canvas.width``/``height``, so this only guards a direct call to the assembler.
DEFAULT_CANVAS = {"width": 1000, "height": 800}

#: The dashboard's minimum size in px, whatever the mock's canvas: the smallest window a
#: business dashboard still reads on. The canvas drives the zone *proportions*, not this floor
#: - see :func:`_render_dashboard`.
MIN_DASHBOARD_WIDTH = 1100
MIN_DASHBOARD_HEIGHT = 800


# --- Column types -------------------------------------------------------------

@dataclass(frozen=True)
class TypeFacts:
    """What one DATA-MODEL.md type implies everywhere a column is rendered.

    Attributes:
        remote_type: The OLEDB type code in the column's metadata-record.
        aggregation: Tableau's default aggregation for the type.
        role: ``dimension`` / ``measure`` - derived from the type, mirroring
            ``datamodel.suggest_role`` rather than parsing the model's Role column.
        type_attr: The ``type`` attribute of the UI-level ``<column>``.
        text_like: Whether the metadata-record carries the string trio
            (``scale`` / ``width`` / ``collation``).
    """

    remote_type: int
    aggregation: str
    role: str
    type_attr: str
    text_like: bool = False


#: DATA-MODEL.md type -> everything derived from it. 129/133/20/5 are attested in the
#: legacy scaffold; 135 (datetime) and 11 (boolean) are the standard OLEDB codes.
TYPE_FACTS: dict[str, TypeFacts] = {
    "string": TypeFacts(129, "Count", "dimension", "nominal", text_like=True),
    "integer": TypeFacts(20, "Sum", "measure", "quantitative"),
    "real": TypeFacts(5, "Sum", "measure", "quantitative"),
    "date": TypeFacts(133, "Year", "dimension", "ordinal"),
    "datetime": TypeFacts(135, "Year", "dimension", "ordinal"),
    "boolean": TypeFacts(11, "Count", "dimension", "nominal"),
}

#: A CSV column the data model does not document still has to exist in the physical schema,
#: or Tableau reports a schema mismatch on load. String is the lossless fallback.
FALLBACK_TYPE = "string"


@dataclass(frozen=True)
class Column:
    """One physical CSV column, built once and rendered into all four locations.

    Attributes:
        name: The column name as it appears in the CSV header row.
        ordinal: Its zero-based position in that header row.
        datatype: The DATA-MODEL.md type (a key of :data:`TYPE_FACTS`).
    """

    name: str
    ordinal: int
    datatype: str

    @property
    def facts(self) -> TypeFacts:
        """TypeFacts: Everything the datatype implies (remote-type, role, aggregation)."""
        return TYPE_FACTS[self.datatype]

    @property
    def caption(self) -> str:
        """str: The UI caption Tableau shows (``order_date`` -> ``Order Date``)."""
        return worksheet.caption_for(self.name)


# --- Deterministic ids ---------------------------------------------------------
# ponytail: ids are hash-derived, not random - the same manifest rebuilds byte-identical,
# which makes uniqueness testable and a re-run diff-free.

def _hashed(seed: str) -> str:
    """Return a stable 32-character hex id for ``seed``."""
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:32]


def datasource_id(name: str) -> str:
    """Return the ``federated.*`` id for a datasource name."""
    return f"federated.{_hashed(f'ds:{name}')}"


def connection_id(datasource_name: str, csv_name: str) -> str:
    """Return the ``textscan.*`` named-connection id for one datasource's CSV.

    The datasource name is part of the seed, not just the CSV: two datasources may read the
    same CSV, and ids must stay unique across the whole workbook.
    """
    return f"textscan.{_hashed(f'conn:{datasource_name}:{csv_name}')}"


def object_id(datasource_name: str, csv_name: str) -> str:
    """Return the object-graph object id for one datasource's CSV (see
    :func:`connection_id` on why the datasource name is in the seed)."""
    return f"{csv_name}_{_hashed(f'obj:{datasource_name}:{csv_name}').upper()}"


def action_name(index: int, seed: str) -> str:
    """Return the ``name`` attribute of one dashboard action.

    Tableau's own shape is ``[Action<n>_<32 upper-case hex>]``. The index makes it unique
    even for two actions with the same caption and target; the hash makes it stable.

    Args:
        index: The action's 1-based position among all the workbook's actions.
        seed: What the action does (its caption and endpoints).

    Returns:
        The bracketed action name.
    """
    return f"[Action{index}_{_hashed(f'action:{seed}')[:32].upper()}]"


def simple_id(kind: str, name: str) -> str:
    """Return the braced, upper-case UUID a ``<simple-id>`` carries.

    Args:
        kind: What the id identifies (``worksheet`` / ``dashboard`` / ``window``).
        name: The sheet or window name.

    Returns:
        A ``{XXXXXXXX-...}`` UUID, stable for the ``(kind, name)`` pair.
    """
    return "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"twb:{kind}:{name}")).upper() + "}"


# --- Datasources ---------------------------------------------------------------

def _columns_for(header: list[str], field_types: dict[str, str]) -> list[Column]:
    """Build the column list for one CSV from its header row and the data model.

    Args:
        header: The physical column order, as read from the CSV's header row.
        field_types: ``{field name: type}`` documented for this CSV.

    Returns:
        One :class:`Column` per header entry, in physical order.
    """
    columns: list[Column] = []
    for ordinal, name in enumerate(header):
        datatype = field_types.get(name, FALLBACK_TYPE)
        columns.append(
            Column(name, ordinal, datatype if datatype in TYPE_FACTS else FALLBACK_TYPE)
        )
    return columns


def _render_relation(
    parent: ET.Element, datasource_name: str, csv_name: str, columns: list[Column]
) -> ET.Element:
    """Render a live ``<relation>`` over a local CSV, with its physical ``<columns>``.

    This is redundancy locations 1 and 3 (``connection > relation`` and
    ``object-graph > object > properties > relation``), which must stay identical - hence
    one renderer for both. The connection is always live: no ``<extract>`` is ever emitted
    (the ``.twbx`` carries the CSVs, and the analyst does Replace Data Source anyway).

    Args:
        parent: The element to append the relation to.
        datasource_name: The owning datasource (part of the connection id's seed).
        csv_name: The CSV filename.
        columns: The physical schema.

    Returns:
        The relation element.
    """
    stem = csv_name.rsplit(".", 1)[0]
    relation = ET.SubElement(parent, "relation", {
        "connection": connection_id(datasource_name, csv_name),
        "name": csv_name,
        "table": f"[{stem}#csv]",
        "type": "table",
    })
    columns_element = ET.SubElement(relation, "columns", {
        "character-set": "UTF-8", "header": "yes", "locale": "en_US", "separator": ",",
    })
    for column in columns:
        ET.SubElement(columns_element, "column", {
            "datatype": column.datatype, "name": column.name, "ordinal": str(column.ordinal),
        })
    return relation


def _render_metadata_records(
    parent: ET.Element, datasource_name: str, csv_name: str, columns: list[Column]
) -> None:
    """Render redundancy location 2: the capability record plus one record per column.

    Args:
        parent: The ``<connection class='federated'>`` element.
        datasource_name: The owning datasource (part of the object id's seed).
        csv_name: The CSV filename (the records' ``parent-name``).
        columns: The physical schema.
    """
    records = ET.SubElement(parent, "metadata-records")
    parent_name = f"[{csv_name}]"

    capability = ET.SubElement(records, "metadata-record", {"class": "capability"})
    ET.SubElement(capability, "remote-name")
    ET.SubElement(capability, "remote-type").text = "0"
    ET.SubElement(capability, "parent-name").text = parent_name
    ET.SubElement(capability, "remote-alias")
    ET.SubElement(capability, "aggregation").text = "Count"
    ET.SubElement(capability, "contains-null").text = "true"
    attributes = ET.SubElement(capability, "attributes")
    for attribute_name, value in (
        ("character-set", "UTF-8"), ("collation", "en_US"), ("field-delimiter", ","),
        ("header-row", "true"), ("locale", "en_US"), ("single-char", ""),
    ):
        ET.SubElement(
            attributes, "attribute", {"datatype": "string", "name": attribute_name}
        ).text = f'"{value}"'

    for column in columns:
        facts = column.facts
        record = ET.SubElement(records, "metadata-record", {"class": "column"})
        ET.SubElement(record, "remote-name").text = column.name
        ET.SubElement(record, "remote-type").text = str(facts.remote_type)
        ET.SubElement(record, "local-name").text = f"[{column.name}]"
        ET.SubElement(record, "parent-name").text = parent_name
        ET.SubElement(record, "remote-alias").text = column.name
        ET.SubElement(record, "ordinal").text = str(column.ordinal)
        ET.SubElement(record, "local-type").text = column.datatype
        ET.SubElement(record, "aggregation").text = facts.aggregation
        if facts.text_like:
            ET.SubElement(record, "scale").text = "1"
            ET.SubElement(record, "width").text = "1073741823"
        ET.SubElement(record, "contains-null").text = "true"
        if facts.text_like:
            # Tableau rewrites the collation from the system locale on save; the value only
            # has to be a valid one.
            ET.SubElement(record, "collation", {"flag": "0", "name": "LEN_RUS"})
        ET.SubElement(record, "object-id").text = (
            f"[{object_id(datasource_name, csv_name)}]"
        )


def _render_datasource(
    parent: ET.Element, name: str, csv_name: str, columns: list[Column], version: str,
    derived: list[worksheet.FieldRef], instances: list[worksheet.FieldRef],
    parameters: list[features.Parameter], formats: dict[str, str],
) -> None:
    """Render one inline, live-connection datasource over a single CSV.

    Args:
        parent: The ``<datasources>`` element.
        name: The datasource name from the manifest (its caption).
        csv_name: The CSV the datasource reads.
        columns: The physical schema, rendered into all four required locations.
        version: The workbook document version (the datasource carries it too).
        derived: Calculated and binned columns the worksheets introduce. They are not in
            the CSV, so they appear only among the UI-level columns - but they must appear
            *here* as well as in each worksheet, or Tableau drops them on save.
        instances: Column-instances the datasource itself must declare. Only table calcs
            need this: the calc lives on the instance, and a datasource that does not carry
            it loses the field from the data pane on save.
        parameters: Parameters this datasource's own calculations read - the datasource
            declares the dependency, domain-less, the way Desktop writes it.
        formats: ``{bracketed column name: format pattern}`` from the worksheets'
            ``number_formats``. On the column rather than only in a worksheet's style rules,
            because that is where Desktop reads a field's format for mark labels and axis
            ticks (issue #59).
    """
    datasource = ET.SubElement(parent, "datasource", {
        "caption": name,
        "inline": "true",
        "name": datasource_id(name),
        "version": version,
    })

    connection = ET.SubElement(datasource, "connection", {"class": "federated"})
    named_connections = ET.SubElement(connection, "named-connections")
    named_connection = ET.SubElement(named_connections, "named-connection", {
        "caption": name, "name": connection_id(name, csv_name),
    })
    # directory='.' - the CSV sits beside the .twb inside the .twbx.
    ET.SubElement(named_connection, "connection", {
        "class": "textscan", "directory": ".", "filename": csv_name,
        "password": "", "server": "",
    })
    _render_relation(connection, name, csv_name, columns)          # location 1
    _render_metadata_records(connection, name, csv_name, columns)  # location 2

    ET.SubElement(datasource, "aliases", {"enabled": "yes"})
    # location 4: the UI-level columns, led by the table's internal object-id column.
    ET.SubElement(datasource, "column", {
        "caption": csv_name,
        "datatype": "table",
        "name": f"[__tableau_internal_object_id__].[{object_id(name, csv_name)}]",
        "role": "measure",
        "type": "quantitative",
    })
    for column in columns:
        facts = column.facts
        attributes = {
            "caption": column.caption,
            "datatype": column.datatype,
            "name": f"[{column.name}]",
            "role": facts.role,
            "type": facts.type_attr,
        }
        pattern = formats.get(attributes["name"], "")
        if pattern:
            attributes["default-format"] = pattern
        ET.SubElement(datasource, "column", dict(sorted(attributes.items())))
    for reference in derived:
        # No number_formats fallback here: a calculated field declares its own format
        # (``calculated_fields[].format``), which is already on its column.
        worksheet.render_column(datasource, reference)
    for reference in instances:
        worksheet.render_column_instance(datasource, reference)
    ET.SubElement(datasource, "layout", {
        "dim-ordering": "alphabetic", "measure-ordering": "alphabetic",
        "show-structure": "true",
    })
    if parameters:
        # A calculation here reads [Parameters].[x], so the datasource declares that
        # dependency itself - between <layout> and <object-graph>, where Desktop writes it.
        dependencies = ET.SubElement(
            datasource, "datasource-dependencies",
            {"datasource": features.PARAMETERS_DATASOURCE},
        )
        for parameter in parameters:
            features.render_parameter_column(dependencies, parameter, with_domain=False)

    object_graph = ET.SubElement(datasource, "object-graph")
    objects = ET.SubElement(object_graph, "objects")
    graph_object = ET.SubElement(objects, "object", {
        "caption": csv_name, "id": object_id(name, csv_name),
    })
    properties = ET.SubElement(graph_object, "properties", {"context": ""})
    _render_relation(properties, name, csv_name, columns)          # location 3


# --- Interactions (CONTRACT.md §6) ---------------------------------------------

@dataclass
class Interactions:
    """Everything the manifest's ``objects`` and ``actions`` add to the workbook.

    Resolved in one pass because the four outputs are the same few facts seen from different
    elements: a quick filter is a zone *and* a worksheet filter *and* a dashboard-level
    declaration, and an action is only renderable once every element id has been turned into
    the sheet name Tableau addresses it by.

    Attributes:
        leaves: ``{element id: Leaf}`` for the control zones (filter cards, parameter
            controls) - merged into the dashboard's leaf map.
        declarations: ``{datasource name: [FieldRef]}`` - the fields a filter card needs the
            *dashboard* to declare as well as the worksheet.
        controlled_parameters: The parameters a control zone puts on the dashboard - the only
            ones the dashboard declares. A parameter a *sheet* reads is that sheet's business
            (Desktop drops it from the dashboard's declarations on save).
        sheet_actions: The filter / highlight actions.
        parameter_actions: The parameter actions.
    """

    leaves: dict[str, zones.Leaf] = field(default_factory=dict)
    declarations: dict[str, list[worksheet.FieldRef]] = field(default_factory=dict)
    controlled_parameters: list[features.Parameter] = field(default_factory=list)
    sheet_actions: list[features.SheetAction] = field(default_factory=list)
    parameter_actions: list[features.ParameterAction] = field(default_factory=list)


def _plan_interactions(
    manifest_document: dict,
    plans: list[PlannedWorksheet],
    parameters: list[features.Parameter],
) -> Interactions:
    """Resolve the manifest's control objects and actions against the planned worksheets.

    Mutates the worksheet plans: a quick filter injects the filter it is the UI for, and a
    parameter action's source field is added to the sheet's declared fields (an action
    referencing an instance the worksheet never declared is one Tableau drops).

    Args:
        manifest_document: The parsed build manifest.
        plans: The resolved worksheets (mutated).
        parameters: The resolved parameters.

    Returns:
        The :class:`Interactions`.
    """
    interactions = Interactions()
    by_element = {
        str(planned.entry.get("element_id", "")).strip(): planned
        for planned in plans if str(planned.entry.get("element_id", "")).strip()
    }
    by_sheet = {planned.plan.name: planned for planned in plans}
    by_parameter = {parameter.name: parameter for parameter in parameters}

    for entry in manifest_document.get("objects") or []:
        if not isinstance(entry, dict):
            continue
        element_id = str(entry.get("element_id", "")).strip()
        kind = str(entry.get("kind", "")).strip().lower()
        if kind == "filter":
            _plan_quick_filter(entry, element_id, by_sheet, interactions)
        elif kind == "parameter":
            parameter = by_parameter.get(str(entry.get("parameter", "")).strip())
            if parameter is not None:
                interactions.leaves[element_id] = zones.Leaf(
                    kind=kind, param=parameter.reference,
                    # The widget follows the domain: Desktop writes 'slider' for a range and
                    # 'compact' for a member list, and rewrites the workbook otherwise.
                    mode="slider" if parameter.domain_type == "range" else "compact",
                    title=str(entry.get("title", "") or "").strip(),
                )
                interactions.controlled_parameters.append(parameter)

    _plan_actions(manifest_document.get("actions") or [], by_element, by_parameter,
                  interactions)
    return interactions


def _plan_quick_filter(
    entry: dict, element_id: str, by_sheet: dict[str, PlannedWorksheet],
    interactions: Interactions,
) -> None:
    """Resolve one filter card: its zone, its worksheet filter, and its declaration.

    A card is injected into the *named* worksheet only. "Apply to all sheets using this data
    source" is a Desktop-side choice the analyst makes on the built workbook - a manifest that
    wants two sheets filtered declares two cards.

    Args:
        entry: The manifest ``objects`` entry.
        element_id: The zone the card fills.
        by_sheet: ``{sheet name: PlannedWorksheet}``.
        interactions: The accumulator (mutated).
    """
    planned = by_sheet.get(str(entry.get("worksheet", "")).strip())
    field_name = str(entry.get("field", "")).strip()
    if planned is None or not field_name:
        return  # validate_manifest has already named this entry
    reference = planned.plan.resolver.reference(field_name)
    if reference is None:
        return

    interactions.leaves[element_id] = zones.Leaf(
        kind="filter",
        param=planned.plan.reference_of(reference),
        sheet=planned.plan.name,
        mode=str(entry.get("mode", "")).strip(),
        title=str(entry.get("title", "") or "").strip(),
    )
    # The card is the UI for a worksheet filter; without the filter there is nothing to
    # control. A filter the manifest already declared on that field stays as it is - an
    # explicit member list is a narrower filter than "all members", not a duplicate.
    already_filtered = any(
        existing.reference.instance_name == reference.instance_name
        for existing in planned.plan.filters
    )
    if not already_filtered:
        planned.plan.filters.append(
            worksheet.FilterPlan(reference=reference, all_members=True)
        )
    interactions.declarations.setdefault(planned.datasource, []).append(reference)


def _plan_actions(
    entries: list, by_element: dict[str, PlannedWorksheet],
    by_parameter: dict[str, features.Parameter], interactions: Interactions,
) -> None:
    """Resolve the manifest's actions into renderable ones, numbering them as it goes.

    Args:
        entries: The manifest's ``actions`` list.
        by_element: ``{element id: PlannedWorksheet}`` - how an action's endpoints become
            the sheet names Tableau addresses.
        by_parameter: ``{parameter name: Parameter}``.
        interactions: The accumulator (mutated).
    """
    index = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        caption = str(entry.get("name", "")).strip()
        kind = str(entry.get("type", "")).strip().lower()
        source = by_element.get(str(entry.get("source", "")).strip())
        if source is None or not (kind in features.ACTION_COMMANDS or kind == "parameter"):
            continue  # validate_manifest has already named this entry
        activation = features.ACTIVATIONS.get(
            str(entry.get("run_on", "")).strip().lower(), "on-select"
        )
        targets = entry.get("targets")
        for target in targets if isinstance(targets, list) else [targets]:
            target_name = str(target or "").strip()
            index += 1
            name = action_name(index, f"{caption}:{target_name}")
            if kind == "parameter":
                parameter = by_parameter.get(target_name)
                reference = source.plan.resolver.reference(
                    str(entry.get("field", "")).strip()
                )
                if parameter is None or reference is None:
                    continue
                # The action reads a field off the clicked mark, so the sheet has to declare
                # it even when it is not on a shelf.
                source.plan.declared.append(reference)
                interactions.parameter_actions.append(features.ParameterAction(
                    name=name, caption=caption, source=source.plan.name,
                    source_field=source.plan.reference_of(reference),
                    target_parameter=parameter.reference,
                    clear_value=parameter.clear_value, activation=activation,
                ))
                continue
            target_planned = by_element.get(target_name)
            if target_planned is None:
                continue
            interactions.sheet_actions.append(features.SheetAction(
                name=name, caption=caption, kind=kind, source=source.plan.name,
                target=target_planned.plan.name, activation=activation,
            ))


def _parameters_read_by(
    references: list[worksheet.FieldRef], parameters: list[features.Parameter]
) -> list[features.Parameter]:
    """Return the parameters any of the given fields' formulas reference, in declared order."""
    formulas = " ".join(
        reference.formula for reference in references if reference.formula
    )
    return [parameter for parameter in parameters if parameter.reference in formulas]


def _attach_parameters(
    plans: list[PlannedWorksheet], parameters: list[features.Parameter]
) -> None:
    """Give each worksheet the parameters its calculations read.

    A calculated field's formula is the only place a worksheet can reference a parameter, so
    the formulas are what decide it - no manifest key to keep in sync.

    Args:
        plans: The resolved worksheets (mutated).
        parameters: The resolved parameters.
    """
    for planned in plans:
        planned.plan.parameters = _parameters_read_by(planned.plan.all_refs, parameters)


# --- Worksheets, dashboard, windows --------------------------------------------

def _dashboard_leaves(
    plans: list[PlannedWorksheet], objects: object
) -> dict[str, zones.Leaf]:
    """Map every layout element id to what fills its zone.

    The manifest addresses zones by ``element_id`` - the mock's language - while Tableau
    addresses them by sheet name and zone type; this is where the two meet, once, for both
    the views and the non-view objects.

    Args:
        plans: The resolved worksheets.
        objects: The manifest's optional ``objects`` list.

    Returns:
        ``{element id: Leaf}`` for :func:`zones.render_zones`.
    """
    leaves: dict[str, zones.Leaf] = {}
    for _, entry, plan in plans:
        element_id = str(entry.get("element_id", "")).strip()
        if not element_id:
            continue
        # Only the colour legend is placed in the dashboard; size/shape keys stay on the
        # worksheet's own right edge, where they do not compete for the element's box.
        # 'legend: false' opts out entirely - a KPI card coloured by a semantic up/down field
        # needs the colour but not the 22px key, which would eat its number (issue #65). The
        # sheet's own right-edge legend card is unaffected either way.
        legend = None
        if entry.get("legend") is not False:  # absent means the default: keep the legend
            legend = next(
                (zones.Legend(reference, pane_id)
                 for card_type, reference, pane_id in worksheet.legend_cards(plan)
                 if card_type == "color"),
                None,
            )
        # First claimant wins: two views on one zone is a manifest bug, and picking the
        # later one would silently move the analyst's chart.
        leaves.setdefault(element_id, zones.Leaf(
            worksheet=plan.name,
            title=str(entry.get("title", "") or "").strip(),
            legend=legend,
            # A KPI card is one cell; every other chart type scales with its zone. That is the
            # only distinction the zone's <layout-cache> needs (issue #59).
            single_cell=plan.spec.kpi_card,
        ))

    for entry in objects if isinstance(objects, list) else []:
        if not isinstance(entry, dict):
            continue
        element_id = str(entry.get("element_id", "")).strip()
        if not element_id:
            continue
        leaves.setdefault(element_id, zones.Leaf(
            kind=str(entry.get("kind", "") or "").strip().lower(),
            title=str(entry.get("title", "") or "").strip(),
            text=str(entry.get("text", "") or "").strip(),
        ))
    return leaves


def _render_dashboard(
    parent: ET.Element,
    canvas: dict,
    root: object,
    leaves: dict[str, zones.Leaf],
    tokens: worksheet.DesignTokens,
    interactions: Interactions,
) -> zones.RenderedZones:
    """Render the dashboard: range-sized above a fixed floor, with the spec's zone tree.

    Args:
        parent: The ``<dashboards>`` element.
        canvas: The layout's ``canvas`` object (``width`` / ``height`` in px) - what px
            measures inside the tree are scaled against, not the dashboard's size.
        root: The layout tree's ``root`` node.
        leaves: ``{element id: Leaf}`` - what fills each leaf zone.
        tokens: The design tokens, for the generated title zones.
        interactions: The resolved control objects - what the dashboard must declare,
            parameter controls included.

    Returns:
        The :class:`zones.RenderedZones`. The root id is the dashboard window's ``active``
        target; :func:`_render_windows` hides exactly the embedded sheets, so a sheet that is
        embedded nowhere keeps its tab instead of becoming unreachable.
    """
    dashboard = ET.SubElement(parent, "dashboard", {
        "enable-sort-zone-taborder": "true", "name": DASHBOARD_NAME,
    })
    ET.SubElement(dashboard, "style")
    width = str(int(canvas.get("width", DEFAULT_CANVAS["width"])))
    height = str(int(canvas.get("height", DEFAULT_CANVAS["height"])))
    # Range-sized, no maximum: zone geometry is in a normalised 100,000-unit space, so the
    # proportions the analyst approved hold at any window size, while a hard maximum (a fixed
    # size) only forces the viewer to scroll a wide screen's worth of empty margin. The floor
    # is the standard minimum, not the mock's canvas - the canvas is a design surface, and
    # pinning the minimum to it would make a wide mock unopenable on a laptop.
    ET.SubElement(dashboard, "size", {
        "minheight": str(MIN_DASHBOARD_HEIGHT), "minwidth": str(MIN_DASHBOARD_WIDTH),
        "sizing-mode": "range",
    })
    _render_dashboard_declarations(dashboard, interactions)
    rendered = zones.render_zones(
        ET.SubElement(dashboard, "zones"),
        root if isinstance(root, dict) else {"type": "vert", "children": []},
        {"width": int(width), "height": int(height)},
        leaves,
        tokens,
    )
    ET.SubElement(dashboard, "simple-id", {"uuid": simple_id("dashboard", DASHBOARD_NAME)})
    return rendered


def _render_dashboard_declarations(
    dashboard: ET.Element, interactions: Interactions,
) -> None:
    """Declare the datasources and fields the dashboard's own control zones read.

    A filter card or a parameter control is evaluated by the *dashboard*, not by the sheet it
    filters, so a card whose field only the worksheet declares crashes Tableau on open. The
    blocks go between ``<size>`` and ``<zones>`` - the XSD's order.

    Args:
        dashboard: The ``<dashboard>`` element.
        interactions: The resolved control objects, including the parameters its control zones
            put on the dashboard - the only parameters declared here.
    """
    if not (interactions.declarations or interactions.controlled_parameters):
        return
    declared = ET.SubElement(dashboard, "datasources")
    for name in sorted(interactions.declarations):
        ET.SubElement(
            declared, "datasource", {"caption": name, "name": datasource_id(name)}
        )
    if interactions.controlled_parameters:
        ET.SubElement(declared, "datasource", {"name": features.PARAMETERS_DATASOURCE})

    for name in sorted(interactions.declarations):
        dependencies = ET.SubElement(
            dashboard, "datasource-dependencies", {"datasource": datasource_id(name)}
        )
        by_instance = {
            reference.instance_name: reference
            for reference in interactions.declarations[name]
        }
        for instance_name in sorted(by_instance):
            reference = by_instance[instance_name]
            worksheet.render_column(dependencies, reference)
            worksheet.render_column_instance(dependencies, reference)
    if interactions.controlled_parameters:
        dependencies = ET.SubElement(
            dashboard, "datasource-dependencies",
            {"datasource": features.PARAMETERS_DATASOURCE},
        )
        for parameter in interactions.controlled_parameters:
            features.render_parameter_column(dependencies, parameter)


def _render_zoom(
    parent: ET.Element, plan: worksheet.WorksheetPlan, wrapped: bool = True
) -> None:
    """Render a sheet's fit as a ``<zoom>``, in or under a ``<viewpoint>``.

    Standard fit is the *absence* of a zoom, so a sheet that keeps it (a scrolling text table)
    gets no viewpoint at all rather than a zoom Tableau would have to interpret.

    Args:
        parent: The ``<window>`` the viewpoint belongs to, or the ``<viewpoint>`` itself.
        plan: The worksheet whose fit is being written.
        wrapped: Whether the ``<viewpoint>`` still has to be created (a worksheet window owns
            one; the dashboard window's ``<viewpoints>`` list already named it per sheet).
    """
    zoom_type = plan.zoom_type
    if not zoom_type:
        return
    viewpoint = ET.SubElement(parent, "viewpoint") if wrapped else parent
    ET.SubElement(viewpoint, "zoom", {"type": zoom_type})


def _render_windows(
    parent: ET.Element,
    plans: list[worksheet.WorksheetPlan],
    root_zone_id: str,
    embedded: set[str],
) -> None:
    """Render one window per worksheet plus the dashboard window.

    Args:
        parent: The ``<workbook>`` element.
        plans: The resolved worksheets, in manifest order.
        root_zone_id: The zone the dashboard window opens on.
        embedded: Names of worksheets the dashboard embeds in a zone; only those get
            ``hidden='true'``. Hiding a sheet no zone shows makes it unreachable - Tableau
            renders no tab for it, so the analyst opens a workbook with nothing in it.
    """
    windows = ET.SubElement(parent, "windows", {"source-height": "30"})

    for plan in plans:
        attributes = {"class": "worksheet", "name": plan.name}
        if plan.name in embedded:
            attributes["hidden"] = "true"
        window = ET.SubElement(windows, "window", dict(sorted(attributes.items())))
        cards = ET.SubElement(window, "cards")
        left = ET.SubElement(cards, "edge", {"name": "left"})
        left_strip = ET.SubElement(left, "strip", {"size": "160"})
        for card_type in ("pages", "filters", "marks"):
            ET.SubElement(left_strip, "card", {"type": card_type})
        top = ET.SubElement(cards, "edge", {"name": "top"})
        for card_type, size in (("columns", "2147483647"), ("rows", "2147483647"),
                                ("title", "31")):
            ET.SubElement(
                ET.SubElement(top, "strip", {"size": size}), "card", {"type": card_type}
            )
        # A colour- or size-encoded chart with no legend renders, but reads as broken.
        legends = worksheet.legend_cards(plan)
        if legends:
            right_strip = ET.SubElement(
                ET.SubElement(cards, "edge", {"name": "right"}), "strip", {"size": "160"}
            )
            for card_type, column, pane_id in legends:
                ET.SubElement(right_strip, "card", {
                    "pane-specification-id": pane_id, "param": column, "type": card_type,
                })
        # The sheet's own fit, on its own tab. Without it Tableau opens every tab on Standard
        # and the analyst reviews 15 charts sitting in whitespace.
        _render_zoom(window, plan)
        ET.SubElement(window, "simple-id", {"uuid": simple_id("window", plan.name)})

    dashboard_window = ET.SubElement(windows, "window", {
        "class": "dashboard", "maximized": "true", "name": DASHBOARD_NAME,
    })
    viewpoints = ET.SubElement(dashboard_window, "viewpoints")
    for plan in plans:
        # The same fit again, this time as the dashboard sees the embedded sheet.
        _render_zoom(
            ET.SubElement(viewpoints, "viewpoint", {"name": plan.name}), plan, wrapped=False
        )
    ET.SubElement(dashboard_window, "active", {"id": root_zone_id})
    ET.SubElement(
        dashboard_window, "simple-id", {"uuid": simple_id("window", DASHBOARD_NAME)}
    )


# --- The workbook --------------------------------------------------------------

def workbook_version(target_tableau_version: str) -> str:
    """Return the document-format version for a STATE.md target (CONTRACT.md §2).

    Args:
        target_tableau_version: ``2024.2-2025.x`` or ``2026.1+``.

    Returns:
        ``"26.1"`` for the 2026.1+ target, ``"18.1"`` otherwise.
    """
    return _WORKBOOK_VERSION.get(
        target_tableau_version.strip(), _DEFAULT_WORKBOOK_VERSION
    )


def _build_resolvers(
    manifest_document: dict, field_types: dict[str, dict[str, str]]
) -> dict[str, worksheet.FieldResolver]:
    """Build one :class:`worksheet.FieldResolver` per declared datasource.

    Args:
        manifest_document: The parsed build manifest.
        field_types: ``{csv: {field: type}}`` from ``DATA-MODEL.md``.

    Returns:
        ``{datasource name: resolver}``. Each resolver knows the datasource's federated id,
        its CSV's field types, and its calculated fields' formulas.
    """
    calculated: dict[str, dict[str, worksheet.CalculatedField]] = {}
    for entry in manifest_document.get("calculated_fields", []) or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        source = str(entry.get("datasource", "")).strip()
        if not (name and source):
            continue
        # No 'type' means a numeric result - the overwhelmingly common calculated field.
        calculated.setdefault(source, {})[name] = worksheet.CalculatedField(
            formula=str(entry.get("formula", "")).strip(),
            datatype=str(entry.get("type", "")).strip().lower() or "real",
            number_format=str(entry.get("format", "")).strip(),
        )

    # The resolver only needs the role and the UI type from each DATA-MODEL.md type; passing
    # a plain mapping keeps :mod:`worksheet` free of an import back into this module.
    type_facts = {
        datatype: (facts.role, facts.type_attr) for datatype, facts in TYPE_FACTS.items()
    }
    resolvers: dict[str, worksheet.FieldResolver] = {}
    for entry in manifest_document.get("datasources", []) or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        csv_name = str(entry.get("csv", "")).strip()
        resolvers[name] = worksheet.FieldResolver(
            datasource_id(name),
            name,
            field_types.get(csv_name, {}),
            calculated.get(name, {}),
            type_facts,
        )
    return resolvers


class PlannedWorksheet(NamedTuple):
    """One resolved worksheet and the two things about it the plan does not carry.

    The manifest entry travels alongside the plan because the *dashboard* needs what a
    :class:`worksheet.WorksheetPlan` deliberately knows nothing about - the ``element_id`` the
    sheet fills and its title - while the datasource name is what the derived-column pass
    groups by.

    Attributes:
        datasource: The datasource name the worksheet reads.
        entry: The raw ``worksheets`` entry from the manifest.
        plan: The resolved plan the worksheet body is rendered from.
    """

    datasource: str
    entry: dict
    plan: worksheet.WorksheetPlan


def _plan_worksheets(
    manifest_document: dict, resolvers: dict[str, worksheet.FieldResolver]
) -> list[PlannedWorksheet]:
    """Resolve every manifest worksheet into a :class:`PlannedWorksheet`."""
    plans: list[PlannedWorksheet] = []
    for entry in manifest_document.get("worksheets", []) or []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("datasource", "")).strip()
        resolver = resolvers.get(source)
        if resolver is None:  # validate_manifest already named this worksheet
            continue
        plans.append(
            PlannedWorksheet(source, entry, worksheet.plan_worksheet(entry, resolver))
        )
    return plans


def _collect_derived_columns(
    manifest_document: dict,
    resolvers: dict[str, worksheet.FieldResolver],
    plans: list[PlannedWorksheet],
) -> dict[str, list[worksheet.FieldRef]]:
    """Collect the non-CSV columns each datasource must declare.

    Two kinds: the manifest's ``calculated_fields``, and the binned columns a histogram
    introduces on a shelf. Both are declared at the datasource level as well as inside each
    worksheet that uses them - Tableau drops a worksheet-only calculation on save.

    Args:
        manifest_document: The parsed build manifest.
        resolvers: The per-datasource resolvers.
        plans: The resolved worksheets.

    Returns:
        ``{datasource name: [FieldRef]}``, de-duplicated and in a stable order.
    """
    derived: dict[str, dict[str, worksheet.FieldRef]] = {}
    for entry in manifest_document.get("calculated_fields", []) or []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("datasource", "")).strip()
        resolver = resolvers.get(source)
        name = str(entry.get("name", "")).strip()
        if resolver is None or not name:
            continue
        reference = resolver.reference(name)
        if reference is not None:
            derived.setdefault(source, {}).setdefault(reference.column_name, reference)

    for planned in plans:
        for reference in worksheet.bin_columns(planned.plan):
            derived.setdefault(planned.datasource, {}).setdefault(
                reference.column_name, reference
            )

    return {source: list(columns.values()) for source, columns in derived.items()}


def _collect_number_formats(plans: list[PlannedWorksheet]) -> dict[str, dict[str, str]]:
    """Collect the default number format each datasource's columns carry.

    Issue #59: a worksheet's ``number_formats`` entry was emitted only as a ``cell`` style
    rule, which formats a text table's cells but is not what Desktop consults for **mark
    labels** or **axis ticks** - so ``$#,##0`` bars rendered ``19,241.21``. A field's default
    format is an attribute on its ``<column>``, and the columns are rendered once per
    datasource, so the formats have to be gathered across every worksheet first.

    Args:
        plans: The resolved worksheets.

    Returns:
        ``{datasource name: {bracketed column name: format pattern}}``.
    """
    formats: dict[str, dict[str, str]] = {}
    for planned in plans:
        for reference, pattern in planned.plan.number_formats:
            declared = formats.setdefault(planned.datasource, {})
            existing = declared.setdefault(reference.column_name, pattern)
            if existing != pattern:
                # One column, one default format: first worksheet wins, because silently
                # taking the last would make the workbook depend on manifest ordering.
                logger.warning(
                    f"[WARN] {planned.datasource}.{reference.column_name} is asked for two "
                    f"number formats ({existing!r} and {pattern!r}); keeping {existing!r}"
                )
    return formats


def _collect_table_calc_instances(
    plans: list[PlannedWorksheet],
) -> dict[str, list[worksheet.FieldRef]]:
    """Collect the table-calc column-instances each datasource must declare.

    Args:
        plans: The resolved worksheets.

    Returns:
        ``{datasource name: [FieldRef]}``, de-duplicated by instance name and in a stable
        order. A plain instance is not included: only a table calc carries state (its type and
        addressing) that the datasource has to remember.
    """
    instances: dict[str, dict[str, worksheet.FieldRef]] = {}
    for planned in plans:
        for reference in planned.plan.all_refs:
            if reference.table_calc:
                instances.setdefault(planned.datasource, {}).setdefault(
                    reference.instance_name, reference
                )
    return {source: list(found.values()) for source, found in instances.items()}


def _plan_zone_visibility(
    manifest_document: dict, controlled: dict[str, str]
) -> list[features.ZoneVisibility]:
    """Qualify each ``visibility`` field against the datasource that declares it.

    Args:
        manifest_document: The parsed build manifest.
        controlled: ``{zone id: calculated field name}`` from the layout walk.

    Returns:
        One :class:`features.ZoneVisibility` per controlled zone, in zone order. A field no
        ``calculated_fields`` entry declares is dropped - ``validate_manifest`` has already
        named it, and a datagraph pointing at a field Tableau cannot find hides the zone for
        good.
    """
    owner: dict[str, str] = {}
    for entry in manifest_document.get("calculated_fields") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        source = str(entry.get("datasource", "")).strip()
        if name and source:
            owner.setdefault(name, source)

    visibilities: list[features.ZoneVisibility] = []
    for zone_id in sorted(controlled, key=int):
        field_name = controlled[zone_id]
        source = owner.get(field_name)
        if source:
            visibilities.append(features.ZoneVisibility(
                zone_id=zone_id, field=f"[{datasource_id(source)}].[{field_name}]"
            ))
    return visibilities


def _visibility_requests(node: object) -> list[tuple[dict, str]]:
    """Collect ``(layout node, field name)`` for every node carrying a ``visibility`` key."""
    if not isinstance(node, dict):
        return []
    requests = []
    field_name = str(node.get("visibility", "") or "").strip()
    if field_name:
        requests.append((node, field_name))
    for child in node.get("children") or []:
        requests.extend(_visibility_requests(child))
    return requests


def _element_ids(node: dict) -> list[str]:
    """Return the node's own element id first, then its descendants' - the search order."""
    ids = []
    element_id = str(node.get("id", "") or "").strip()
    if element_id:
        ids.append(element_id)
    for child in node.get("children") or []:
        if isinstance(child, dict):
            ids.extend(_element_ids(child))
    return ids


def _attach_visibility_fields(layout: dict, plans: list[PlannedWorksheet]) -> None:
    """Put every zone-visibility field on the Detail shelf of a sheet that can resolve it.

    Dynamic Zone Visibility evaluates the field off the *view*: a boolean no sheet in the
    dashboard places is one Tableau cannot read, and the zone silently never toggles - the
    ``<datagraph>`` alone is not enough. A Desktop-saved DZV workbook puts the field on the
    controlled sheet's Detail shelf, so that is the sheet preferred here; a controlled
    container (or a controlled object zone) falls back to any sheet on the field's datasource,
    which is all Tableau needs to evaluate it.

    Args:
        layout: The manifest's ``layout`` value.
        plans: The resolved worksheets (mutated).
    """
    by_element = {
        str(planned.entry.get("element_id", "")).strip(): planned
        for planned in plans if str(planned.entry.get("element_id", "")).strip()
    }
    for node, field_name in _visibility_requests(layout.get("root")):
        candidates = [by_element[element_id] for element_id in _element_ids(node)
                      if element_id in by_element]
        candidates += [planned for planned in plans if planned not in candidates]
        for planned in candidates:
            reference = planned.plan.resolver.reference(field_name)
            if reference is None:
                continue
            if not any(existing.instance_name == reference.instance_name
                       for existing in planned.plan.detail):
                planned.plan.detail.append(reference)
            break


def render_workbook(
    manifest_document: dict,
    data_model_text: str,
    csv_headers: dict[str, list[str]],
    design_tokens_text: str = "",
) -> str:
    """Assemble a validated build manifest into ``.twb`` XML.

    Children of ``<workbook>`` are emitted in the order the 2026.1 XSD's ``WorkbookFile-CT``
    prescribes. Two elements are *omitted rather than emitted empty*, because the schema
    requires at least one child of each: ``<worksheets>`` when the manifest declares none,
    and ``<thumbnails>`` always (Tableau regenerates thumbnails on save).

    Args:
        manifest_document: The parsed, validated ``build-manifest.json``.
        data_model_text: The contents of ``DATA-MODEL.md`` (the type authority).
        csv_headers: ``{csv filename: header row}`` - the physical schema and its order.
        design_tokens_text: The contents of ``DESIGN-TOKENS.md``; ``""`` when the analyst
            skipped branding, in which case Tableau's own defaults apply.

    Returns:
        The workbook XML, ready to write as ``dashboard.twb``.
    """
    target = str(manifest_document.get("target_tableau_version", "")).strip()
    version = workbook_version(target)
    tokens = worksheet.parse_design_tokens(design_tokens_text)

    workbook = ET.Element("workbook", {
        "original-version": version,
        "source-build": SOURCE_BUILD,
        "source-platform": "win",
        "version": version,
        "xmlns:user": "http://www.tableausoftware.com/xml/user",
    })

    field_types = documented_field_types(data_model_text)
    resolvers = _build_resolvers(manifest_document, field_types)
    plans = _plan_worksheets(manifest_document, resolvers)
    parameters = features.plan_parameters(manifest_document.get("parameters"))
    layout = manifest_document.get("layout")
    if not isinstance(layout, dict):
        layout = {}

    interactions = _plan_interactions(manifest_document, plans, parameters)
    # Before _attach_parameters: a visibility field's formula reads a parameter, and the sheet
    # it lands on has to declare that parameter or Tableau cannot resolve the calculation.
    _attach_visibility_fields(layout, plans)
    _attach_parameters(plans, parameters)
    derived = _collect_derived_columns(manifest_document, resolvers, plans)

    # The zone tree is rendered into a detached element first: whether the workbook carries a
    # <datagraph> (and its four format flags) is only known once the layout has been walked,
    # and the format manifest is the workbook's *first* child.
    dashboards = ET.Element("dashboards")
    rendered = _render_dashboard(
        dashboards,
        layout.get("canvas", {}),
        layout.get("root"),
        {
            **_dashboard_leaves(plans, manifest_document.get("objects", [])),
            **interactions.leaves,
        },
        tokens,
        interactions,
    )
    visibilities = _plan_zone_visibility(manifest_document, rendered.visibility)

    worksheet_entries = manifest_document.get("worksheets") or []
    flags = FORMAT_CHANGE_FLAGS
    if any(entry.get("sort") for entry in worksheet_entries):
        flags = flags + (SORT_FORMAT_FLAG,)
    if visibilities:
        flags = flags + features.DZV_FORMAT_FLAGS
    if interactions.parameter_actions:
        flags = flags + features.PARAMETER_ACTION_FORMAT_FLAGS
    format_manifest = ET.SubElement(workbook, "document-format-change-manifest")
    for flag in sorted(flags):  # Tableau writes them alphabetically
        ET.SubElement(format_manifest, flag)

    datasources = ET.SubElement(workbook, "datasources")
    table_calcs = _collect_table_calc_instances(plans)
    number_formats = _collect_number_formats(plans)
    for entry in manifest_document.get("datasources", []):
        csv_name = str(entry.get("csv", "")).strip()
        name = str(entry.get("name", "")).strip()
        _render_datasource(
            datasources,
            name,
            csv_name,
            _columns_for(csv_headers.get(csv_name, []), field_types.get(csv_name, {})),
            version,
            derived.get(name, []),
            table_calcs.get(name, []),
            _parameters_read_by(derived.get(name, []), parameters),
            number_formats.get(name, {}),
        )
    features.render_parameters_datasource(datasources, parameters, version)

    if any(planned.plan.spec.geographic for planned in plans):
        # A map needs its <mapsources> at *both* levels: the workbook's declares the source,
        # each map worksheet's view references it. One without the other renders no basemap.
        ET.SubElement(
            ET.SubElement(workbook, "mapsources"), "mapsource", {"name": worksheet.MAPSOURCE_NAME}
        )

    features.render_actions(
        workbook, DASHBOARD_NAME, interactions.sheet_actions,
        interactions.parameter_actions,
    )

    if plans:  # the XSD requires >=1 <worksheet>; omit the element otherwise
        worksheets = ET.SubElement(workbook, "worksheets")
        for planned in plans:
            worksheet.render_worksheet(
                worksheets, planned.plan, tokens,
                simple_id("worksheet", planned.plan.name),
            )

    workbook.append(dashboards)
    _render_windows(
        workbook, [planned.plan for planned in plans], rendered.root_zone_id,
        rendered.embedded,
    )
    features.render_datagraph(
        workbook, visibilities, simple_id("dashboard", DASHBOARD_NAME)
    )

    if target == TARGET_2026:
        explain_data = ET.SubElement(workbook, "explain-data", {
            "enabled-for-viewer": "false", "extreme-values-enabled-for-all": "false",
        })
        ET.SubElement(explain_data, "explanation-types")

    ET.indent(workbook, space="  ")
    return worksheet.unwrap_cdata(
        "<?xml version='1.0' encoding='utf-8' ?>\n\n"
        + ET.tostring(workbook, encoding="unicode")
        + "\n"
    )
