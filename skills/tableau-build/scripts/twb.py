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
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import worksheet
import zones
from manifest import documented_field_types

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
        return self.name.replace("_", " ").title()


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
    derived: list[worksheet.FieldRef],
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
        ET.SubElement(datasource, "column", {
            "caption": column.caption,
            "datatype": column.datatype,
            "name": f"[{column.name}]",
            "role": facts.role,
            "type": facts.type_attr,
        })
    for reference in derived:
        worksheet.render_column(datasource, reference)
    ET.SubElement(datasource, "layout", {
        "dim-ordering": "alphabetic", "measure-ordering": "alphabetic",
        "show-structure": "true",
    })

    object_graph = ET.SubElement(datasource, "object-graph")
    objects = ET.SubElement(object_graph, "objects")
    graph_object = ET.SubElement(objects, "object", {
        "caption": csv_name, "id": object_id(name, csv_name),
    })
    properties = ET.SubElement(graph_object, "properties", {"context": ""})
    _render_relation(properties, name, csv_name, columns)          # location 3


# --- Worksheets, dashboard, windows --------------------------------------------

def _dashboard_leaves(
    plans: list[tuple[str, dict, worksheet.WorksheetPlan]], objects: object
) -> dict[str, zones.Leaf]:
    """Map every layout element id to what fills its zone.

    The manifest addresses zones by ``element_id`` - the mock's language - while Tableau
    addresses them by sheet name and zone type; this is where the two meet, once, for both
    the views and the non-view objects.

    Args:
        plans: The resolved worksheets, as ``(datasource, manifest entry, plan)``.
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
        legend = next(
            ((reference, pane_id)
             for card_type, reference, pane_id in worksheet.legend_cards(plan)
             if card_type == "color"),
            (),
        )
        # First claimant wins: two views on one zone is a manifest bug, and picking the
        # later one would silently move the analyst's chart.
        leaves.setdefault(element_id, zones.Leaf(
            worksheet=plan.name,
            title=str(entry.get("title", "") or "").strip(),
            legend=legend,
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
) -> tuple[str, set[str]]:
    """Render the dashboard: fixed-size at the mock's canvas, with the spec's zone tree.

    Args:
        parent: The ``<dashboards>`` element.
        canvas: The layout's ``canvas`` object (``width`` / ``height`` in px).
        root: The layout tree's ``root`` node.
        leaves: ``{element id: Leaf}`` - what fills each leaf zone.
        tokens: The design tokens, for the generated title zones.

    Returns:
        ``(root zone id, embedded sheet names)``. The root id is the dashboard window's
        ``active`` target; :func:`_render_windows` hides exactly the embedded sheets, so a
        sheet that is embedded nowhere keeps its tab instead of becoming unreachable.
    """
    dashboard = ET.SubElement(parent, "dashboard", {
        "enable-sort-zone-taborder": "true", "name": DASHBOARD_NAME,
    })
    ET.SubElement(dashboard, "style")
    width = str(int(canvas.get("width", DEFAULT_CANVAS["width"])))
    height = str(int(canvas.get("height", DEFAULT_CANVAS["height"])))
    # A fixed-size dashboard is what makes the mock's geometry hold: with sizing-mode
    # 'automatic' Tableau re-flows the zones to the viewer's window instead.
    ET.SubElement(dashboard, "size", {
        "maxheight": height, "maxwidth": width, "minheight": height, "minwidth": width,
    })
    root_zone_id, embedded = zones.render_zones(
        ET.SubElement(dashboard, "zones"),
        root if isinstance(root, dict) else {"type": "vert", "children": []},
        {"width": int(width), "height": int(height)},
        leaves,
        tokens,
    )
    ET.SubElement(dashboard, "simple-id", {"uuid": simple_id("dashboard", DASHBOARD_NAME)})
    return root_zone_id, embedded


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
    worksheet_names = [plan.name for plan in plans]

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
        ET.SubElement(window, "simple-id", {"uuid": simple_id("window", plan.name)})

    dashboard_window = ET.SubElement(windows, "window", {
        "class": "dashboard", "maximized": "true", "name": DASHBOARD_NAME,
    })
    viewpoints = ET.SubElement(dashboard_window, "viewpoints")
    for name in worksheet_names:
        # Never a self-closing viewpoint: without entire-view zoom the sheet does not fill
        # its allocated space.
        ET.SubElement(
            ET.SubElement(viewpoints, "viewpoint", {"name": name}),
            "zoom", {"type": "entire-view"},
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
    calculated: dict[str, dict[str, tuple[str, str]]] = {}
    for entry in manifest_document.get("calculated_fields", []) or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        source = str(entry.get("datasource", "")).strip()
        if not (name and source):
            continue
        # No 'type' means a numeric result - the overwhelmingly common calculated field.
        calculated.setdefault(source, {})[name] = (
            str(entry.get("formula", "")).strip(),
            str(entry.get("type", "")).strip().lower() or "real",
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


def _plan_worksheets(
    manifest_document: dict, resolvers: dict[str, worksheet.FieldResolver]
) -> list[tuple[str, dict, worksheet.WorksheetPlan]]:
    """Resolve every manifest worksheet into ``(datasource, manifest entry, plan)``.

    The entry travels with the plan because the *dashboard* needs what the plan deliberately
    does not carry - the ``element_id`` the sheet fills and its title - while the datasource
    name is what the derived-column pass groups by.
    """
    plans: list[tuple[str, dict, worksheet.WorksheetPlan]] = []
    for entry in manifest_document.get("worksheets", []) or []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("datasource", "")).strip()
        resolver = resolvers.get(source)
        if resolver is None:  # validate_manifest already named this worksheet
            continue
        plans.append((source, entry, worksheet.plan_worksheet(entry, resolver)))
    return plans


def _collect_derived_columns(
    manifest_document: dict,
    resolvers: dict[str, worksheet.FieldResolver],
    plans: list[tuple[str, dict, worksheet.WorksheetPlan]],
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

    for source, _, plan in plans:
        for reference in worksheet.bin_columns(plan):
            derived.setdefault(source, {}).setdefault(reference.column_name, reference)

    return {source: list(columns.values()) for source, columns in derived.items()}


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

    worksheet_entries = manifest_document.get("worksheets") or []
    flags = FORMAT_CHANGE_FLAGS
    if any(entry.get("sort") for entry in worksheet_entries):
        flags = tuple(sorted(flags + (SORT_FORMAT_FLAG,)))

    format_manifest = ET.SubElement(workbook, "document-format-change-manifest")
    for flag in flags:
        ET.SubElement(format_manifest, flag)

    field_types = documented_field_types(data_model_text)
    resolvers = _build_resolvers(manifest_document, field_types)
    plans = _plan_worksheets(manifest_document, resolvers)
    derived = _collect_derived_columns(manifest_document, resolvers, plans)

    datasources = ET.SubElement(workbook, "datasources")
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
        )

    if any(plan.spec.geographic for _, _, plan in plans):
        # A map needs its <mapsources> at *both* levels: the workbook's declares the source,
        # each map worksheet's view references it. One without the other renders no basemap.
        ET.SubElement(
            ET.SubElement(workbook, "mapsources"), "mapsource", {"name": worksheet.MAPSOURCE_NAME}
        )

    if plans:  # the XSD requires >=1 <worksheet>; omit the element otherwise
        worksheets = ET.SubElement(workbook, "worksheets")
        for _, _, plan in plans:
            worksheet.render_worksheet(
                worksheets, plan, tokens, simple_id("worksheet", plan.name)
            )

    layout = manifest_document.get("layout") if isinstance(manifest_document.get("layout"), dict) else {}
    root_zone_id, embedded = _render_dashboard(
        ET.SubElement(workbook, "dashboards"),
        layout.get("canvas", {}),
        layout.get("root"),
        _dashboard_leaves(plans, manifest_document.get("objects", [])),
        tokens,
    )
    _render_windows(
        workbook, [plan for _, _, plan in plans], root_zone_id, embedded
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
