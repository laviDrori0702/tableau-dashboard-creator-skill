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

Scope: datasources, placeholder worksheets, a one-zone dashboard sized from the layout
canvas, windows, and version targeting. Worksheet bodies (shelves, encodings, marks) and the
dashboard's zone tree are the next ticket.
"""

from __future__ import annotations

import hashlib
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass

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
#: Adding or removing one changes Tableau's behaviour - keep the set as-is.
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

#: The single dashboard this ticket emits (the zone tree from ``layout.root`` is next).
DASHBOARD_NAME = "Dashboard 1"

#: Dashboard zones live in a 100,000 x 100,000 virtual coordinate space.
ZONE_SPACE = "100000"


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


def connection_id(csv_name: str) -> str:
    """Return the ``textscan.*`` named-connection id for a CSV filename."""
    return f"textscan.{_hashed(f'conn:{csv_name}')}"


def object_id(csv_name: str) -> str:
    """Return the object-graph object id for a CSV filename."""
    return f"{csv_name}_{_hashed(f'obj:{csv_name}').upper()}"


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


def _render_relation(parent: ET.Element, csv_name: str, columns: list[Column]) -> ET.Element:
    """Render a live ``<relation>`` over a local CSV, with its physical ``<columns>``.

    This is redundancy locations 1 and 3 (``connection > relation`` and
    ``object-graph > object > properties > relation``), which must stay identical - hence
    one renderer for both. The connection is always live: no ``<extract>`` is ever emitted
    (the ``.twbx`` carries the CSVs, and the analyst does Replace Data Source anyway).

    Args:
        parent: The element to append the relation to.
        csv_name: The CSV filename.
        columns: The physical schema.

    Returns:
        The relation element.
    """
    stem = csv_name.rsplit(".", 1)[0]
    relation = ET.SubElement(parent, "relation", {
        "connection": connection_id(csv_name),
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
    parent: ET.Element, csv_name: str, columns: list[Column]
) -> None:
    """Render redundancy location 2: the capability record plus one record per column.

    Args:
        parent: The ``<connection class='federated'>`` element.
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
        ET.SubElement(record, "object-id").text = f"[{object_id(csv_name)}]"


def _render_datasource(
    parent: ET.Element, name: str, csv_name: str, columns: list[Column], version: str
) -> None:
    """Render one inline, live-connection datasource over a single CSV.

    Args:
        parent: The ``<datasources>`` element.
        name: The datasource name from the manifest (its caption).
        csv_name: The CSV the datasource reads.
        columns: The physical schema, rendered into all four required locations.
        version: The workbook document version (the datasource carries it too).
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
        "caption": name, "name": connection_id(csv_name),
    })
    # directory='.' - the CSV sits beside the .twb inside the .twbx.
    ET.SubElement(named_connection, "connection", {
        "class": "textscan", "directory": ".", "filename": csv_name,
        "password": "", "server": "",
    })
    _render_relation(connection, csv_name, columns)          # location 1
    _render_metadata_records(connection, csv_name, columns)  # location 2

    ET.SubElement(datasource, "aliases", {"enabled": "yes"})
    # location 4: the UI-level columns, led by the table's internal object-id column.
    ET.SubElement(datasource, "column", {
        "caption": csv_name,
        "datatype": "table",
        "name": f"[__tableau_internal_object_id__].[{object_id(csv_name)}]",
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
    ET.SubElement(datasource, "layout", {
        "dim-ordering": "alphabetic", "measure-ordering": "alphabetic",
        "show-structure": "true",
    })

    object_graph = ET.SubElement(datasource, "object-graph")
    objects = ET.SubElement(object_graph, "objects")
    graph_object = ET.SubElement(objects, "object", {
        "caption": csv_name, "id": object_id(csv_name),
    })
    properties = ET.SubElement(graph_object, "properties", {"context": ""})
    _render_relation(properties, csv_name, columns)          # location 3


# --- Worksheets, dashboard, windows --------------------------------------------

def _render_worksheet(parent: ET.Element, name: str) -> None:
    """Render a placeholder worksheet body (the shelves/marks are the next ticket).

    Args:
        parent: The ``<worksheets>`` element.
        name: The sheet name, which its window must match exactly.
    """
    worksheet = ET.SubElement(parent, "worksheet", {"name": name})
    table = ET.SubElement(worksheet, "table")
    view = ET.SubElement(table, "view")
    ET.SubElement(view, "datasources")
    ET.SubElement(view, "aggregation", {"value": "true"})
    ET.SubElement(table, "style")
    panes = ET.SubElement(table, "panes")
    pane = ET.SubElement(
        panes, "pane", {"selection-relaxation-option": "selection-relaxation-allow"}
    )
    ET.SubElement(ET.SubElement(pane, "view"), "breakdown", {"value": "auto"})
    ET.SubElement(pane, "mark", {"class": "Automatic"})
    ET.SubElement(table, "rows")
    ET.SubElement(table, "cols")
    ET.SubElement(worksheet, "simple-id", {"uuid": simple_id("worksheet", name)})


def _render_zone_style(parent: ET.Element, margin: str) -> None:
    """Append the four-format ``<zone-style>`` block Tableau writes on every zone."""
    zone_style = ET.SubElement(parent, "zone-style")
    for attribute, value in (
        ("border-color", "#000000"), ("border-style", "none"), ("border-width", "0"),
        ("margin", margin),
    ):
        ET.SubElement(zone_style, "format", {"attr": attribute, "value": value})


def _render_dashboard(parent: ET.Element, canvas: dict, root_zone_id: str) -> None:
    """Render the dashboard: its size from the mock's canvas, and one root zone.

    The zone *tree* (``layout.root``) is the next ticket; what this pins is the geometry
    the mock was approved at, so the workbook opens at the right size.

    Args:
        parent: The ``<dashboards>`` element.
        canvas: The layout's ``canvas`` object (``width`` / ``height`` in px).
        root_zone_id: The id of the root zone (the dashboard window's ``active`` target).
    """
    dashboard = ET.SubElement(parent, "dashboard", {
        "enable-sort-zone-taborder": "true", "name": DASHBOARD_NAME,
    })
    ET.SubElement(dashboard, "style")
    width = str(int(canvas.get("width", 1000)))
    height = str(int(canvas.get("height", 800)))
    ET.SubElement(dashboard, "size", {
        "maxheight": height, "maxwidth": width, "minheight": height, "minwidth": width,
    })
    zones = ET.SubElement(dashboard, "zones")
    root_zone = ET.SubElement(zones, "zone", {
        "h": ZONE_SPACE, "id": root_zone_id, "type-v2": "layout-basic",
        "w": ZONE_SPACE, "x": "0", "y": "0",
    })
    _render_zone_style(root_zone, "8")  # zone-style is always the zone's last child
    ET.SubElement(dashboard, "simple-id", {"uuid": simple_id("dashboard", DASHBOARD_NAME)})


def _render_windows(
    parent: ET.Element, worksheet_names: list[str], root_zone_id: str
) -> None:
    """Render one hidden window per worksheet plus the dashboard window.

    Args:
        parent: The ``<workbook>`` element.
        worksheet_names: The sheet names, in manifest order.
        root_zone_id: The zone the dashboard window opens on.
    """
    windows = ET.SubElement(parent, "windows", {"source-height": "30"})

    for name in worksheet_names:
        # hidden: these sheets only ever appear embedded in the dashboard.
        window = ET.SubElement(
            windows, "window", {"class": "worksheet", "hidden": "true", "name": name}
        )
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
        ET.SubElement(window, "simple-id", {"uuid": simple_id("window", name)})

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


def render_workbook(
    manifest_document: dict,
    data_model_text: str,
    csv_headers: dict[str, list[str]],
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

    Returns:
        The workbook XML, ready to write as ``dashboard.twb``.
    """
    target = str(manifest_document.get("target_tableau_version", "")).strip()
    version = workbook_version(target)

    workbook = ET.Element("workbook", {
        "original-version": version,
        "source-build": SOURCE_BUILD,
        "source-platform": "win",
        "version": version,
        "xmlns:user": "http://www.tableausoftware.com/xml/user",
    })

    format_manifest = ET.SubElement(workbook, "document-format-change-manifest")
    for flag in FORMAT_CHANGE_FLAGS:
        ET.SubElement(format_manifest, flag)

    field_types = documented_field_types(data_model_text)
    datasources = ET.SubElement(workbook, "datasources")
    for entry in manifest_document.get("datasources", []):
        csv_name = str(entry.get("csv", "")).strip()
        _render_datasource(
            datasources,
            str(entry.get("name", "")).strip(),
            csv_name,
            _columns_for(csv_headers.get(csv_name, []), field_types.get(csv_name, {})),
            version,
        )

    worksheet_names = [
        str(worksheet.get("name", "")).strip()
        for worksheet in manifest_document.get("worksheets", [])
    ]
    if worksheet_names:  # the XSD requires >=1 <worksheet>; omit the element otherwise
        worksheets = ET.SubElement(workbook, "worksheets")
        for name in worksheet_names:
            _render_worksheet(worksheets, name)

    layout = manifest_document.get("layout")
    canvas = layout.get("canvas", {}) if isinstance(layout, dict) else {}
    root_zone_id = "1"  # zone ids are sequential from 1; the tree is the next ticket
    _render_dashboard(ET.SubElement(workbook, "dashboards"), canvas, root_zone_id)
    _render_windows(workbook, worksheet_names, root_zone_id)

    if target == TARGET_2026:
        explain_data = ET.SubElement(workbook, "explain-data", {
            "enabled-for-viewer": "false", "extreme-values-enabled-for-all": "false",
        })
        ET.SubElement(explain_data, "explanation-types")

    ET.indent(workbook, space="  ")
    return (
        "<?xml version='1.0' encoding='utf-8' ?>\n\n"
        + ET.tostring(workbook, encoding="unicode")
        + "\n"
    )
