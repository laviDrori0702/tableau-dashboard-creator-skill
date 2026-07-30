"""Contract test for tableau-build's dashboard layout engine (CONTRACT.md step 8).

The spec's ``## Layout`` container tree is what the analyst approved the mock at, so the
workbook's zone tree has to *be* that tree - not an approximation of it. What is pinned here
is everything the mock's geometry depends on and an LLM checklist got wrong:

* the zone hierarchy is the container tree one-to-one (a test walks both);
* the dashboard is fixed-size at the mock's canvas, and sibling zones reproduce the declared
  ``size`` percentages within rounding, tiling their parent exactly;
* zone ids are sequential and unique, every zone carries a ``friendly-name``, and
  ``<zone-style>`` is the last child everywhere;
* a titled element gets its own text zone (styled from ``DESIGN-TOKENS.md``) and a
  colour-encoded chart gets a legend zone, both by construction.

:mod:`zones` is pure and stdlib-only, so the geometry is driven directly rather than through
a built ``.twbx``.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import build
import worksheet
import zones

CANVAS = {"width": 1366, "height": 768}

#: The demo project's approved spec - a real 4-level tree with a mapped container in it.
DEMO_SPEC = (
    Path(__file__).resolve().parent.parent
    / "demo" / "output" / "mock-version" / "v_1" / "IMPLEMENTATION-SPEC.md"
)

#: The root margin, in zone units, at CANVAS - what every child of the root is inset by.
INSET_X = round(zones.ROOT_MARGIN_PX / CANVAS["width"] * zones.ZONE_SPACE)
INSET_Y = round(zones.ROOT_MARGIN_PX / CANVAS["height"] * zones.ZONE_SPACE)


def _render(root, leaves=None, canvas=None, tokens=None):
    """Render one layout tree and return ``(<zones> element, root zone id, embedded)``."""
    container = ET.Element("zones")
    root_id, embedded = zones.render_zones(
        container,
        root,
        canvas or CANVAS,
        leaves or {},
        tokens or worksheet.DesignTokens(),
    )
    return container, root_id, embedded


def _tree(node):
    """Reduce a rendered zone element to ``(kind, [children])`` for a shape comparison."""
    kind = node.get("name") or node.get("param") or node.get("type-v2") or "?"
    return kind, [_tree(child) for child in node.findall("zone")]


# --- Hierarchy ----------------------------------------------------------------

def test_zone_hierarchy_mirrors_the_container_tree():
    """The whole point: the approved container tree survives into the workbook (AC #1)."""
    root = {
        "type": "vert",
        "children": [
            {"type": "horz", "size": 20, "children": [
                {"id": "kpi-a", "size": 50},
                {"id": "kpi-b", "size": 50},
            ]},
            {"id": "chart-trend", "size": 80},
        ],
    }
    leaves = {
        "kpi-a": zones.Leaf(worksheet="A"),
        "kpi-b": zones.Leaf(worksheet="B"),
        "chart-trend": zones.Leaf(worksheet="Trend"),
    }

    container, _, _ = _render(root, leaves)

    assert _tree(container.find("zone")) == (
        "layout-basic", [
            ("vert", [
                ("horz", [("A", []), ("B", [])]),
                ("Trend", []),
            ]),
        ],
    )


def _walk_both(node, zone, path="root"):
    """Assert one layout node and one zone agree, then recurse into their children."""
    children = node.get("children") or []
    zone_children = zone.findall("zone")

    assert len(zone_children) == len(children), f"{path}: child count differs"
    if children:
        assert zone.get("type-v2") == "layout-flow", f"{path}: container is not a flow zone"
        assert zone.get("param") == node.get("type"), f"{path}: orientation differs"
    for index, (child, child_zone) in enumerate(zip(children, zone_children), start=1):
        _walk_both(child, child_zone, f"{path}.{index}")


def test_the_demo_projects_layout_tree_maps_one_to_one():
    """AC #1, on the real thing: the demo's approved container tree, walked against the
    zone tree it produces. A dropped, added or re-oriented container fails here."""
    layout = build.spec_layout(DEMO_SPEC.read_text(encoding="utf-8-sig"))
    assert layout is not None, f"the demo spec at {DEMO_SPEC} carries no layout tree"

    container, _, _ = _render(layout["root"], canvas=layout["canvas"])

    _walk_both(layout["root"], container.find("zone").find("zone"))


def test_a_mapped_container_is_one_zone_holding_its_children():
    """A node with both an 'id' and 'children' (a DZV panel) is filled by its children."""
    root = {
        "type": "vert",
        "children": [
            {"id": "pnl-detail", "type": "horz", "size": 100, "children": [
                {"id": "chart-a", "size": 50},
                {"id": "chart-b", "size": 50},
            ]},
        ],
    }
    leaves = {"chart-a": zones.Leaf(worksheet="A"), "chart-b": zones.Leaf(worksheet="B")}

    container, _, _ = _render(root, leaves)
    panel = container.find("zone/zone/zone")

    assert panel.get("type-v2") == "layout-flow" and panel.get("param") == "horz"
    assert panel.get("friendly-name") == "H-pnl-detail"
    assert [zone.get("name") for zone in panel.findall("zone")] == ["A", "B"]


def test_the_root_zone_is_layout_basic_and_children_are_inset_by_its_margin():
    """The root fills the coordinate space; its 8px margin insets everything below it."""
    container, root_id, _ = _render({"type": "vert", "children": [{"id": "a", "size": 100}]})
    root_zone = container.find("zone")

    assert root_id == "1"
    assert root_zone.attrib == {
        "friendly-name": "Dashboard", "h": "100000", "id": "1",
        "type-v2": "layout-basic", "w": "100000", "x": "0", "y": "0",
    }
    flow = root_zone.find("zone")
    assert (int(flow.get("x")), int(flow.get("y"))) == (INSET_X, INSET_Y)
    assert int(flow.get("w")) == zones.ZONE_SPACE - 2 * INSET_X
    assert int(flow.get("h")) == zones.ZONE_SPACE - 2 * INSET_Y


# --- Geometry -----------------------------------------------------------------

def test_sibling_sizes_reproduce_the_declared_percentages():
    """A 20/80 vert split is a 20/80 split of the parent's height (AC #2)."""
    root = {"type": "vert", "children": [
        {"id": "top", "size": 20}, {"id": "bottom", "size": 80},
    ]}

    container, _, _ = _render(root)
    top, bottom = container.findall("zone/zone/zone")
    extent = zones.ZONE_SPACE - 2 * INSET_Y

    assert int(top.get("h")) == pytest.approx(extent * 0.20, abs=1)
    assert int(bottom.get("h")) == pytest.approx(extent * 0.80, abs=1)
    # Same x/w: a vert split only divides the height.
    assert top.get("w") == bottom.get("w") and top.get("x") == bottom.get("x")


def test_siblings_tile_their_parent_exactly():
    """Rounding must never leave a gap or an overlap - the last child absorbs the remainder."""
    root = {"type": "horz", "children": [
        {"id": "a", "size": 33}, {"id": "b", "size": 33}, {"id": "c", "size": 34},
    ]}

    container, _, _ = _render(root)
    parent = container.find("zone/zone")
    children = parent.findall("zone")

    edge = int(parent.get("x"))
    for child in children:
        assert int(child.get("x")) == edge
        edge += int(child.get("w"))
    assert edge == int(parent.get("x")) + int(parent.get("w"))


def test_children_without_a_size_share_what_is_left():
    """A tree that declares some sizes and not others must still fill the parent."""
    root = {"type": "vert", "children": [
        {"id": "a", "size": 50}, {"id": "b"}, {"id": "c"},
    ]}

    container, _, _ = _render(root)
    parent = container.find("zone/zone")
    heights = [int(child.get("h")) for child in parent.findall("zone")]

    assert sum(heights) == int(parent.get("h"))
    assert heights[1] == heights[2]  # the two undeclared children split the other half
    assert heights[0] == pytest.approx(heights[1] * 2, abs=2)


def test_percentages_that_do_not_total_100_are_normalised():
    """The spec's sizes are proportions; a tree summing to 90 still fills the dashboard."""
    root = {"type": "horz", "children": [{"id": "a", "size": 30}, {"id": "b", "size": 60}]}

    container, _, _ = _render(root)
    parent = container.find("zone/zone")
    left, right = parent.findall("zone")

    assert int(left.get("w")) + int(right.get("w")) == int(parent.get("w"))
    assert int(right.get("w")) == pytest.approx(2 * int(left.get("w")), abs=2)


# --- Invariants Tableau enforces ----------------------------------------------

def test_zone_ids_are_sequential_unique_integers():
    """Ids are cross-referenced by <active id> and device layouts, so they must be sane."""
    root = {"type": "vert", "children": [
        {"type": "horz", "size": 50, "children": [{"id": "a", "size": 100}]},
        {"id": "b", "size": 50},
    ]}

    container, _, _ = _render(root)
    ids = [int(zone.get("id")) for zone in container.iter("zone")]

    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert ids == list(range(1, len(ids) + 1))


def test_every_zone_has_a_friendly_name():
    """ZoneFriendlyName is in the format manifest; a nameless zone reads as a bug (AC #3)."""
    root = {"type": "vert", "children": [
        {"type": "horz", "size": 50, "children": [{"id": "a", "size": 100}]},
        {"id": "b", "size": 50},
    ]}

    container, _, _ = _render(root, {"a": zones.Leaf(worksheet="A", title="Chart A")})

    for zone in container.iter("zone"):
        assert (zone.get("friendly-name") or "").strip(), ET.tostring(zone)


def test_zone_style_is_always_the_last_child():
    """Tableau reads <zone-style> positionally: anything after it is ignored."""
    root = {"type": "vert", "children": [
        {"type": "horz", "size": 100, "children": [{"id": "a", "size": 100}]},
    ]}

    container, _, _ = _render(root, {"a": zones.Leaf(worksheet="A", title="T")})

    for zone in container.iter("zone"):
        assert [child.tag for child in zone][-1] == "zone-style", ET.tostring(zone)
        assert len(zone.findall("zone-style")) == 1


# --- Content ------------------------------------------------------------------

def test_a_worksheet_zone_names_its_sheet_and_reports_it_as_embedded():
    """The dashboard embeds the sheet; the caller hides exactly the embedded ones."""
    container, _, embedded = _render(
        {"type": "vert", "children": [{"id": "chart", "size": 100}]},
        {"chart": zones.Leaf(worksheet="Revenue Trend")},
    )
    zone = container.find("zone/zone/zone")

    assert zone.get("name") == "Revenue Trend"
    assert zone.get("type-v2") is None  # a sheet zone is typed by its 'name', not type-v2
    assert embedded == {"Revenue Trend"}


def test_a_titled_element_gets_a_text_zone_above_its_sheet():
    """The spec's title becomes a styled text zone; the sheet's own title is suppressed."""
    tokens = worksheet.DesignTokens(
        font_family="Inter", title_size=14, title_color="#1a2b3c", present=True
    )
    container, _, _ = _render(
        {"type": "vert", "children": [{"id": "chart", "size": 100}]},
        {"chart": zones.Leaf(worksheet="Trend", title="Revenue over time")},
        tokens=tokens,
    )
    wrapper = container.find("zone/zone/zone")
    title_zone, sheet_zone = wrapper.findall("zone")

    assert wrapper.get("param") == "vert"  # the title stacks above the sheet
    assert title_zone.get("type-v2") == "text"
    assert title_zone.get("is-fixed") == "true"
    assert title_zone.get("fixed-size") == str(zones.TITLE_HEIGHT_PX)
    run = title_zone.find("formatted-text/run")
    assert run.text == "Revenue over time"
    assert run.attrib == {
        "bold": "true", "fontcolor": "#1a2b3c", "fontname": "Inter", "fontsize": "14",
    }
    assert sheet_zone.get("name") == "Trend" and sheet_zone.get("show-title") == "false"


def test_an_untitled_sheet_keeps_its_own_title_and_needs_no_wrapper():
    """No title in the manifest means Tableau's own styled sheet title - and one zone less."""
    container, _, _ = _render(
        {"type": "vert", "children": [{"id": "chart", "size": 100}]},
        {"chart": zones.Leaf(worksheet="Trend")},
    )
    zone = container.find("zone/zone/zone")

    assert zone.get("name") == "Trend"
    assert zone.get("show-title") is None
    assert zone.findall("zone") == []


def test_a_colour_encoded_chart_gets_a_legend_zone_below_it():
    """A colour encoding with no key in the dashboard reads as a broken chart."""
    container, _, _ = _render(
        {"type": "vert", "children": [{"id": "chart", "size": 100}]},
        {"chart": zones.Leaf(
            worksheet="Trend", legend=("[federated.abc].[none:region:nk]", "0")
        )},
    )
    wrapper = container.find("zone/zone/zone")
    sheet_zone, legend_zone = wrapper.findall("zone")

    assert sheet_zone.get("name") == "Trend"
    assert legend_zone.get("type-v2") == "color"
    assert legend_zone.get("name") == "Trend"  # the sheet whose encoding it keys
    assert legend_zone.get("param") == "[federated.abc].[none:region:nk]"
    assert legend_zone.get("pane-specification-id") == "0"
    assert legend_zone.get("fixed-size") == str(zones.LEGEND_HEIGHT_PX)


def test_a_titled_colour_encoded_chart_stacks_title_sheet_legend():
    """All three parts share the element's box, and the sheet takes what is left."""
    container, _, _ = _render(
        {"type": "vert", "children": [{"id": "chart", "size": 100}]},
        {"chart": zones.Leaf(worksheet="Trend", title="T", legend=("[c]", "1"))},
    )
    wrapper = container.find("zone/zone/zone")
    parts = wrapper.findall("zone")

    assert [zone.get("type-v2") or zone.get("name") for zone in parts] == [
        "text", "Trend", "color",
    ]
    assert sum(int(zone.get("h")) for zone in parts) == int(wrapper.get("h"))


@pytest.mark.parametrize("kind, type_v2", [
    ("text", "text"), ("blank", "empty"),
    # Deferred to #37: each needs a reference the manifest does not carry yet, so the layout
    # reserves the box rather than emitting a zone Tableau cannot resolve.
    ("image", "empty"), ("filter", "empty"), ("legend", "empty"),
    ("button", "empty"), ("parameter", "empty"),
    ("nonsense", "empty"),
])
def test_object_kinds_map_to_their_zone_type(kind, type_v2):
    """Every manifest object kind gets a zone - none of them drops out of the layout."""
    container, _, embedded = _render(
        {"type": "vert", "children": [{"id": "obj", "size": 100}]},
        {"obj": zones.Leaf(kind=kind)},
    )
    zone = container.find("zone/zone/zone")

    assert zone.get("type-v2") == type_v2
    assert embedded == set()  # an object zone embeds no sheet


def test_every_object_kind_the_manifest_accepts_has_a_zone_type():
    """A kind validate_manifest allows but this module has never heard of would silently
    become a blank - so the two tables must cover the same set."""
    import manifest

    assert manifest.OBJECT_KINDS == set(zones.OBJECT_ZONE_TYPES) | set(
        zones.DEFERRED_ZONE_TYPES
    )


def test_a_text_object_carries_its_text():
    """A title/label zone with no text is an empty box on the analyst's dashboard."""
    container, _, _ = _render(
        {"type": "vert", "children": [{"id": "obj", "size": 100}]},
        {"obj": zones.Leaf(kind="text", text="Sales Performance")},
    )
    run = container.find("zone/zone/zone/formatted-text/run")

    assert run.text == "Sales Performance"


def test_an_unmapped_leaf_becomes_an_empty_zone_not_a_missing_one():
    """validate_manifest rejects an unfilled zone; if one still arrives, keep the geometry."""
    container, _, _ = _render({"type": "vert", "children": [{"id": "ghost", "size": 100}]})
    zone = container.find("zone/zone/zone")

    assert zone.get("type-v2") == "empty"
    assert zone.get("friendly-name") == "ghost"


# --- The zone tree inside a real workbook --------------------------------------

DATA_MODEL = """# Data Model

## Data source: `sales_orders.csv`

| Field | Type | Role | Sample values | Description |
|-------|------|------|---------------|-------------|
| region | string | dimension | West | Sales region |
| revenue | real | measure | 1200.5 | Order revenue |
"""

CSV_HEADERS = {"sales_orders.csv": ["region", "revenue"]}

TARGET_VERSION = "2026.1+"

#: A header/sidebar/main dashboard: nested containers three deep, a titled and colour-encoded
#: chart, and one zone of every object kind - the shapes a real spec produces.
LAYOUT_RICH_MANIFEST = {
    "target_tableau_version": TARGET_VERSION,
    "datasources": [{
        "name": "sales_orders",
        "csv": "sales_orders.csv",
        "fields": [{"name": "region", "type": "string"}, {"name": "revenue", "type": "real"}],
    }],
    "worksheets": [{
        "name": "Revenue by Region",
        "element_id": "chart-revenue",
        "chart_type": "bar",
        "datasource": "sales_orders",
        "title": "Revenue by region",
        "shelves": {"columns": ["region"], "rows": ["revenue"]},
        "encodings": {"color": "region"},
    }],
    "objects": [
        {"element_id": "txt-title", "kind": "text", "text": "Sales Performance"},
        {"element_id": "img-logo", "kind": "image"},
        {"element_id": "flt-region", "kind": "filter"},
        {"element_id": "prm-target", "kind": "parameter"},
        {"element_id": "leg-region", "kind": "legend"},
        {"element_id": "btn-reset", "kind": "button"},
        {"element_id": "spc-gap", "kind": "blank"},
    ],
    "layout": {
        "canvas": {"width": 1366, "height": 768},
        "root": {
            "type": "vert",
            "children": [
                {"type": "horz", "size": 10, "children": [
                    {"id": "img-logo", "size": 20},
                    {"id": "txt-title", "size": 80},
                ]},
                {"type": "horz", "size": 90, "children": [
                    {"id": "pnl-sidebar", "type": "vert", "size": 25, "children": [
                        {"id": "flt-region", "size": 25},
                        {"id": "prm-target", "size": 25},
                        {"id": "leg-region", "size": 20},
                        {"id": "btn-reset", "size": 20},
                        {"id": "spc-gap", "size": 10},
                    ]},
                    {"id": "chart-revenue", "size": 75},
                ]},
            ],
        },
    },
    "actions": [],
    "parameters": [],
}


@pytest.fixture(scope="module")
def layout_rich_workbook():
    """The rendered ``.twb`` XML of :data:`LAYOUT_RICH_MANIFEST` (text and parsed root)."""
    import twb

    xml_text = twb.render_workbook(LAYOUT_RICH_MANIFEST, DATA_MODEL, CSV_HEADERS)
    return xml_text, ET.fromstring(xml_text)


def test_a_layout_rich_manifest_validates():
    """The schema accepts the tree, so a failure below is the assembler's, not the manifest's."""
    import manifest

    assert manifest.validate_manifest(
        LAYOUT_RICH_MANIFEST, DATA_MODEL, TARGET_VERSION
    ) == []


def test_the_dashboard_is_fixed_size_at_the_canvas(layout_rich_workbook):
    """Without a fixed size Tableau re-flows the zones and the mock's geometry is lost."""
    _, root = layout_rich_workbook
    size = root.find("dashboards/dashboard/size")

    assert size.attrib == {
        "maxheight": "768", "maxwidth": "1366", "minheight": "768", "minwidth": "1366",
    }


def test_the_workbooks_zone_tree_mirrors_the_manifests(layout_rich_workbook):
    """End to end: element ids in, a nested zone tree out, with the wrappers where expected."""
    _, root = layout_rich_workbook
    dashboard_zones = root.find("dashboards/dashboard/zones")

    friendly_names = [zone.get("friendly-name") for zone in dashboard_zones.iter("zone")]
    assert friendly_names == [
        "Dashboard", "V-root",
        "H-root.1", "img-logo", "txt-title",
        "H-root.2",
        "V-pnl-sidebar", "flt-region", "prm-target", "leg-region", "btn-reset", "spc-gap",
        # The titled, colour-encoded chart is wrapped: title above, legend below.
        "V-chart-revenue", "chart-revenue title", "chart-revenue", "chart-revenue legend",
    ]


def test_the_layout_rich_workbook_passes_the_legacy_validators(layout_rich_workbook, tmp_path):
    """The cross-reference checks the legacy skill shipped, on a full zone tree."""
    import validate_twb

    xml_text, _ = layout_rich_workbook
    twb_path = tmp_path / "layout-rich.twb"
    twb_path.write_text(xml_text, encoding="utf-8")

    report = validate_twb.TwbValidator(str(twb_path)).validate()

    assert not [
        f"{result.name}: {result.details}" for result in report.results if not result.passed
    ]


def test_the_layout_rich_workbook_passes_the_xsd(layout_rich_workbook, tmp_path):
    """Zone attributes and nesting are schema-checked: Tableau will not repair a bad tree."""
    pytest.importorskip("lxml")
    import validate_twb_xsd

    xml_text, _ = layout_rich_workbook
    twb_path = tmp_path / "layout-rich.twb"
    twb_path.write_text(xml_text, encoding="utf-8")

    _, errors = validate_twb_xsd.validate(
        twb_path, validate_twb_xsd.load_schema(validate_twb_xsd.XSD_PATH)
    )

    assert not [f"line {error.line}: {error.message}" for error in errors]
