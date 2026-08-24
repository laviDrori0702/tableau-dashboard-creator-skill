"""Contract test for tableau-build's dashboard layout engine (CONTRACT.md step 8).

The spec's ``## Layout`` container tree is what the analyst approved the mock at, so the
workbook's zone tree has to *be* that tree - not an approximation of it. What is pinned here
is everything the mock's geometry depends on and an LLM checklist got wrong:

* the zone hierarchy is the container tree one-to-one (a test walks both);
* the dashboard's minimum size is the mock's canvas, and sibling zones reproduce the declared
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
import twb
import worksheet
import zones

CANVAS = {"width": 1366, "height": 768}

#: The demo project's approved spec - a real 4-level tree with a mapped container in it.
DEMO_SPEC = (
    Path(__file__).resolve().parent.parent
    / "demo" / "mock-version" / "v_1" / "IMPLEMENTATION-SPEC.md"
)

#: The root margin, in zone units, at CANVAS - what every child of the root is inset by.
INSET_X = round(zones.ROOT_MARGIN_PX / CANVAS["width"] * zones.ZONE_SPACE)
INSET_Y = round(zones.ROOT_MARGIN_PX / CANVAS["height"] * zones.ZONE_SPACE)


def _render(root, leaves=None, canvas=None, tokens=None):
    """Render one layout tree and return ``(<zones> element, root zone id, embedded)``."""
    container = ET.Element("zones")
    rendered = zones.render_zones(
        container,
        root,
        canvas or CANVAS,
        leaves or {},
        tokens or worksheet.DesignTokens(),
    )
    return container, rendered.root_zone_id, rendered.embedded


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

    if not children:
        if not zone_children:
            return
        # A titled or colour-encoded leaf is wrapped, and the wrapper's children are the
        # generated header row / content / legend zones - never further layout nodes, so the
        # tree still maps one-to-one.
        label = zone.get("friendly-name", "")
        assert label.startswith("V-"), f"{path}: a leaf zone has children but is no wrapper"
        element = label[len("V-"):]
        names = [part.get("friendly-name") for part in zone_children]
        assert set(names) <= {element, f"H-{element} header", f"{element} legend"}, (
            f"{path}: unexpected zone(s) under a leaf: {names}"
        )
        assert element in names, f"{path}: the wrapper lost its content zone"
        return

    assert len(zone_children) == len(children), f"{path}: child count differs"
    assert zone.get("type-v2") == "layout-flow", f"{path}: container is not a flow zone"
    assert zone.get("param") == node.get("type"), f"{path}: orientation differs"
    for index, (child, child_zone) in enumerate(zip(children, zone_children), start=1):
        _walk_both(child, child_zone, f"{path}.{index}")


def _leaf_ids(node):
    """Return every element id the tree places on a leaf (in document order)."""
    children = node.get("children") or []
    if not children:
        return [node["id"]] if node.get("id") else []
    return [element_id for child in children for element_id in _leaf_ids(child)]


@pytest.mark.parametrize("decorated", [False, True], ids=["bare", "titles-and-legends"])
def test_the_demo_projects_layout_tree_maps_one_to_one(decorated):
    """AC #1, on the real thing: the demo's approved container tree, walked against the
    zone tree it produces. A dropped, added or re-oriented container fails here.

    Run twice, because the shape the build actually produces is the decorated one: with a
    title and a colour legend on every element, each leaf gains a wrapper, and the walk has
    to still hold - a wrapper is transparent to the hierarchy, not a new level of it.
    """
    layout = build.spec_layout(DEMO_SPEC.read_text(encoding="utf-8-sig"))
    assert layout is not None, f"the demo spec at {DEMO_SPEC} carries no layout tree"

    leaves = {} if not decorated else {
        element_id: zones.Leaf(
            worksheet=f"Sheet {index}",
            title=f"Title {index}",
            legend=zones.Legend("[ds].[none:region:nk]", "0"),
        )
        for index, element_id in enumerate(_leaf_ids(layout["root"]), start=1)
    }

    container, _, _ = _render(layout["root"], leaves, canvas=layout["canvas"])

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


# --- Layout metadata (issue #59) ------------------------------------------------

def test_every_sheet_zone_carries_a_layout_cache():
    """Issue #59 bug 1: Desktop 2025.1 rendered demo v_1 with every zone collapsed to its
    content's natural size. The only structural diff against the Desktop-authored example
    workbook was the missing ``<layout-cache>`` on each sheet zone - Desktop writes one on
    every one of them, and on nothing else."""
    root = {"type": "horz", "children": [
        {"id": "kpi", "size": 30},
        {"id": "chart", "size": 40},
        {"id": "note", "size": 30},
    ]}
    leaves = {
        "kpi": zones.Leaf(worksheet="Revenue KPI", single_cell=True),
        "chart": zones.Leaf(worksheet="Trend"),
        "note": zones.Leaf(kind="text", text="hello"),
    }

    container, _, _ = _render(root, leaves)
    by_name = {zone.get("name") or zone.get("type-v2"): zone
               for zone in container.iter("zone")}

    kpi_cache = by_name["Revenue KPI"].find("layout-cache")
    assert kpi_cache.attrib == {
        "cell-count-h": "1", "cell-count-w": "1", "type-h": "cell", "type-w": "cell",
    }
    assert by_name["Trend"].find("layout-cache").attrib == {
        "type-h": "scalable", "type-w": "scalable",
    }
    # Desktop writes no cache on a text / empty / filter / colour zone - only on sheets.
    assert by_name["text"].find("layout-cache") is None


def test_layout_cache_precedes_nested_zones_and_zone_style():
    """The XSD sequence is formatted-text, layout-cache, zones, ..., zone-style; Tableau
    reads both positionally, so a cache written after the style is a cache it ignores."""
    container, _, _ = _render(
        {"type": "vert", "children": [{"id": "chart", "size": 100}]},
        {"chart": zones.Leaf(worksheet="Trend", title="Trend")},
    )

    sheet_zone = container.find(".//zone[@name='Trend']")
    assert [child.tag for child in sheet_zone] == ["layout-cache", "zone-style"]


def test_an_evenly_split_flow_container_declares_the_strategy():
    """A KPI row is four equal cards. Desktop marks such a container
    ``layout-strategy-id='distribute-evenly'``; an unequal one carries no strategy."""
    container, _, _ = _render({"type": "vert", "children": [
        {"type": "horz", "size": 50, "children": [
            {"id": "a", "size": 25}, {"id": "b", "size": 25},
            {"id": "c", "size": 25}, {"id": "d", "size": 25},
        ]},
        {"type": "horz", "size": 50, "children": [
            {"id": "e", "size": 70}, {"id": "f", "size": 30},
        ]},
    ]})
    even_row, uneven_row = container.find("zone/zone").findall("zone")

    assert even_row.get("layout-strategy-id") == "distribute-evenly"
    assert uneven_row.get("layout-strategy-id") is None


def test_a_near_equal_split_still_counts_as_evenly_split():
    """Three siblings can only sum to 100 as ``33.34/33.33/33.33``. That row is even in
    every sense that matters, so it gets the strategy and no per-child pinning (issue #63)."""
    container, _, _ = _render({"type": "horz", "children": [
        {"id": "a", "size": 33.34}, {"id": "b", "size": 33.33}, {"id": "c", "size": 33.33},
    ]})
    row = container.find("zone/zone")

    assert row.get("layout-strategy-id") == "distribute-evenly"
    assert [child.get("is-fixed") for child in row.findall("zone")] == [None, None, None]


def test_a_proportioned_split_pins_every_child_but_the_flex_one():
    """Desktop keeps a hand-proportioned split by writing each child's size in px along the
    flow axis; without it the stored w/h is re-flowed away on open (issue #63)."""
    container, _, _ = _render({"type": "horz", "children": [
        {"id": "side", "size": 25}, {"id": "main", "size": 75},
    ]})
    row = container.find("zone/zone")
    side, main = row.findall("zone")

    assert row.get("layout-strategy-id") is None
    # 25% of the root's width, inset by the root margin, in px at CANVAS.
    assert side.get("is-fixed") == "true"
    assert side.get("fixed-size") == str(round(int(side.get("w")) / zones.ZONE_SPACE * 1366))
    # The last child flexes, absorbing the rounding remainder - as Desktop leaves it.
    assert main.get("is-fixed") is None
    assert main.get("fixed-size") is None


def test_a_vertical_split_pins_children_by_height():
    """The pin is the child's size along the *flow* axis, so a vertical container pins px
    of height (issue #63)."""
    container, _, _ = _render({"type": "vert", "children": [
        {"id": "top", "size": 30}, {"id": "bottom", "size": 70},
    ]})
    top = container.find("zone/zone/zone")

    assert top.get("fixed-size") == str(round(int(top.get("h")) / zones.ZONE_SPACE * 768))


def test_the_biggest_child_flexes_even_when_it_is_not_last():
    """A hand-built container pins every child but the biggest one - the reference workbook's
    trend section pins its 40px title row *and* its trailing 22px legend, leaving the chart
    row between them free. Flexing the trailing strip instead would hand it every pixel the
    dashboard gains over its minimum size, and the charts would never grow (issue #63)."""
    container, _, _ = _render({"type": "vert", "children": [
        {"id": "title", "size": 8}, {"id": "charts", "size": 89}, {"id": "legend", "size": 3},
    ]})
    title, charts, legend = container.find("zone/zone").findall("zone")

    assert charts.get("is-fixed") is None
    assert [title.get("is-fixed"), legend.get("is-fixed")] == ["true", "true"]


def test_a_show_hide_child_is_pinned_and_its_sibling_flexes():
    """A show/hide panel is exactly what must not be the flex child: hiding it would leave
    the container's slack blank. The uncontrolled sibling takes the reflow (issue #63)."""
    container, _, _ = _render({"type": "horz", "children": [
        {"id": "main", "size": 60}, {"id": "panel", "size": 40, "visibility": "Show Panel"},
    ]})
    main, panel = container.find("zone/zone").findall("zone")

    assert panel.get("is-fixed") == "true"
    assert main.get("is-fixed") is None


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


def test_a_titled_element_gets_a_header_row_above_its_sheet():
    """The spec's title becomes a styled text zone in a fixed-height header row; the sheet's
    own title is suppressed."""
    tokens = worksheet.DesignTokens(
        font_family="Inter", title_size=14, title_color="#1a2b3c", present=True
    )
    container, _, _ = _render(
        {"type": "vert", "children": [{"id": "chart", "size": 100}]},
        {"chart": zones.Leaf(worksheet="Trend", title="Revenue over time")},
        tokens=tokens,
    )
    wrapper = container.find("zone/zone/zone")
    header_row, sheet_zone = wrapper.findall("zone")

    assert wrapper.get("param") == "vert"  # the header stacks above the sheet
    assert header_row.get("param") == "horz"
    assert header_row.get("is-fixed") == "true"
    assert header_row.get("fixed-size") == str(zones.TITLE_HEIGHT_PX)

    title_zone, spacer = header_row.findall("zone")
    assert title_zone.get("type-v2") == "text"
    run = title_zone.find("formatted-text/run")
    assert run.text == "Revenue over time"
    assert run.attrib == {
        "bold": "true", "fontcolor": "#1a2b3c", "fontname": "Inter", "fontsize": "14",
    }
    # The spacer is what a filter or a button fills later, so the row must not be all text.
    assert spacer.get("type-v2") == "empty"
    assert int(title_zone.get("w")) + int(spacer.get("w")) == int(header_row.get("w"))
    assert int(title_zone.get("w")) > int(spacer.get("w"))
    assert sheet_zone.get("name") == "Trend" and sheet_zone.get("show-title") == "false"


def test_a_sheets_own_title_is_always_off_titled_or_not():
    """Tableau draws a sheet's own title inside the zone, out of the sheet's own height: a
    KPI card 30px shorter than its number is a card that shows no number. The header is a
    text zone's job, so an untitled element gets no header - and one zone less."""
    container, _, _ = _render(
        {"type": "vert", "children": [{"id": "chart", "size": 100}]},
        {"chart": zones.Leaf(worksheet="Trend")},
    )
    zone = container.find("zone/zone/zone")

    assert zone.get("name") == "Trend"
    assert zone.get("show-title") == "false"
    assert zone.findall("zone") == []


def test_a_colour_encoded_chart_gets_a_legend_zone_below_it():
    """A colour encoding with no key in the dashboard reads as a broken chart."""
    container, _, _ = _render(
        {"type": "vert", "children": [{"id": "chart", "size": 100}]},
        {"chart": zones.Leaf(
            worksheet="Trend",
            legend=zones.Legend("[federated.abc].[none:region:nk]", "0"),
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
        {"chart": zones.Leaf(worksheet="Trend", title="T", legend=zones.Legend("[c]", "1"))},
    )
    wrapper = container.find("zone/zone/zone")
    parts = wrapper.findall("zone")

    assert [zone.get("type-v2") or zone.get("name") for zone in parts] == [
        "layout-flow", "Trend", "color",  # the header row, the sheet, the legend
    ]
    assert sum(int(zone.get("h")) for zone in parts) == int(wrapper.get("h"))


def test_a_box_too_short_for_its_title_and_legend_is_squeezed_not_overflowed():
    """A zone written past its wrapper's bottom edge overlaps the sibling below it."""
    root = {"type": "vert", "children": [
        {"id": "sliver", "size": 3}, {"id": "rest", "size": 97},
    ]}

    container, _, _ = _render(
        root,
        {"sliver": zones.Leaf(worksheet="Tiny", title="T", legend=zones.Legend("[c]", "0"))},
    )
    wrapper, sibling = container.findall("zone/zone/zone")
    parts = wrapper.findall("zone")

    assert sum(int(zone.get("h")) for zone in parts) == int(wrapper.get("h"))
    assert all(int(zone.get("h")) >= 0 for zone in parts)
    bottom = int(parts[-1].get("y")) + int(parts[-1].get("h"))
    assert bottom == int(wrapper.get("y")) + int(wrapper.get("h")) == int(sibling.get("y"))


@pytest.mark.parametrize("kind, type_v2", [
    ("text", "text"), ("blank", "empty"),
    # An image / button / standalone legend needs a reference the manifest does not carry, so
    # the layout reserves the box rather than emitting a zone Tableau cannot resolve.
    ("image", "empty"), ("legend", "empty"), ("button", "empty"),
    # A filter card or a parameter control renders for real - but only once the leaf carries
    # the 'param' it controls (see test_features.py); without one it also reserves its box.
    ("filter", "empty"), ("parameter", "empty"),
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

    assert manifest.OBJECT_KINDS == set(zones.OBJECT_ZONE_TYPES) | zones.DEFERRED_KINDS


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
        {"element_id": "flt-region", "kind": "filter",
         "field": "region", "worksheet": "Revenue by Region"},
        {"element_id": "prm-target", "kind": "parameter", "parameter": "Target"},
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
    "parameters": [
        {"name": "Target", "data_type": "real", "current_value": 100000,
         "range": {"min": 0, "max": 500000, "step": 50000}},
    ],
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


def test_the_dashboard_is_range_sized_above_the_standard_floor(layout_rich_workbook):
    """Zone units are normalised, so the mock's proportions survive any window size: the
    dashboard gets the standard minimum and no maximum, and the 1366x768 canvas the tree was
    laid out against does not become the size the analyst is stuck with."""
    _, root = layout_rich_workbook
    size = root.find("dashboards/dashboard/size")

    assert size.attrib == {
        "minheight": str(twb.MIN_DASHBOARD_HEIGHT),
        "minwidth": str(twb.MIN_DASHBOARD_WIDTH),
        "sizing-mode": "range",
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
        # The titled, colour-encoded chart is wrapped: header row above, legend below.
        "V-chart-revenue",
        "H-chart-revenue header", "chart-revenue title", "chart-revenue header spacer",
        "chart-revenue", "chart-revenue legend",
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


def test_a_short_box_losing_most_of_itself_to_generated_zones_warns(caplog):
    """A 30px header + 22px legend inside a ~70px KPI box leaves ~17px of number (issue #65)."""
    root = {"type": "vert", "children": [
        {"id": "kpi-net-new-mrr", "size": 8}, {"id": "rest", "size": 92},
    ]}

    with caplog.at_level("WARNING"):
        _render(
            root,
            {"kpi-net-new-mrr": zones.Leaf(
                worksheet="Net New MRR", title="Net New MRR",
                legend=zones.Legend("[c]", "0"), single_cell=True,
            )},
            canvas={"width": 1366, "height": 900},
        )

    assert "kpi-net-new-mrr" in caplog.text
    assert "legend: false" in caplog.text


def test_a_tall_box_with_the_same_generated_zones_is_silent(caplog):
    """52px of header and legend out of a 400px chart box is normal, not a squeeze."""
    root = {"type": "vert", "children": [
        {"id": "chart-trend", "size": 50}, {"id": "rest", "size": 50},
    ]}

    with caplog.at_level("WARNING"):
        _render(
            root,
            {"chart-trend": zones.Leaf(
                worksheet="Trend", title="Trend", legend=zones.Legend("[c]", "0"),
            )},
            canvas={"width": 1366, "height": 900},
        )

    assert caplog.text == ""


def test_legend_false_suppresses_the_generated_legend_zone():
    """A KPI card coloured by a semantic up/down field needs the colour, not the key (#65)."""
    import copy
    import manifest
    import twb as twb_module

    document = copy.deepcopy(LAYOUT_RICH_MANIFEST)
    document["worksheets"][0]["legend"] = False

    assert manifest.validate_manifest(document, DATA_MODEL, TARGET_VERSION) == []

    root = ET.fromstring(twb_module.render_workbook(document, DATA_MODEL, CSV_HEADERS))
    wrapper = next(
        zone for zone in root.find("dashboards/dashboard/zones").iter("zone")
        if zone.get("friendly-name") == "V-chart-revenue"
    )
    parts = wrapper.findall("zone")

    assert [zone.get("friendly-name") for zone in parts] == [
        "H-chart-revenue header", "chart-revenue",  # no legend zone
    ]
    # The sheet reclaims the legend's height: header + view tile the wrapper exactly.
    assert sum(int(zone.get("h")) for zone in parts) == int(wrapper.get("h"))
    # The sheet's own right-edge legend card is untouched - only the dashboard zone is gone.
    right_edge = root.find(
        "windows/window[@name='Revenue by Region']/cards/edge[@name='right']"
    )
    assert right_edge is not None
