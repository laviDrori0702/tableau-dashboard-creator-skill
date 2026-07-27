"""Golden-output contract test for tableau-build's 15 worksheet chart templates (issue #35).

Every chart pattern the legacy skill shipped as a hand-written ``.twb`` snippet is produced
here from manifest fields alone. Two things are being protected:

* **No snippet leakage.** The old workflow copy-pasted a snippet and search-replaced the
  field names; anything it missed - a datasource hash, a UUID, a leftover ``profit`` -
  shipped to the analyst. :func:`test_no_snippet_values_leak` fails if any of those appear.
* **No silent template regression.** Each pattern's rendered ``<worksheet>`` is pinned to a
  golden file under ``fixtures/charts/``, so a change to the builder shows up as a diff on
  the exact chart types it affects rather than as a workbook that opens blank in Tableau.

Regenerate the goldens after an *intended* template change with::

    UPDATE_CHART_GOLDENS=1 python -m pytest tests/test_charts.py -q

and read the resulting diff before committing it - that diff is the review.

The 15 patterns are not 15 chart types: sorted / filtered / styled / custom-tooltip are
modifiers that apply to any chart, and a stacked bar is a bar with a colour encoding. The
cases below therefore cover all 15 *patterns* across 12 ``chart_type`` values plus the
modifier keys.
"""

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import build
import manifest
import twb
import worksheet

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "charts"
UPDATE_GOLDENS = os.environ.get("UPDATE_CHART_GOLDENS") == "1"

TARGET_VERSION = "2026.1+"

DATA_MODEL = """# Data Model

## Data source: `sales_orders.csv`

| Field | Type | Role | Sample values | Description |
|-------|------|------|---------------|-------------|
| order_date | date | dimension | 2024-01-05 | Order date |
| region | string | dimension | West | Sales region |
| country | string | dimension | France | Billing country |
| product_category | string | dimension | Technology | Product category |
| revenue | real | measure | 1200.5 | Order revenue |
| profit | real | measure | 120.5 | Order profit |
"""

CSV_HEADERS = {
    "sales_orders.csv": [
        "order_date", "region", "country", "product_category", "revenue", "profit",
    ]
}

DESIGN_TOKENS = """# Design Tokens

## Typography

- **Font family**: Open Sans
- **Chart title**: 14px, 600, #7F56D9
"""


def _sheet(name: str, chart_type: str, **extra) -> dict:
    """Build one manifest worksheet entry, filling in the boilerplate."""
    entry = {
        "name": name,
        "element_id": f"zone-{name.lower().replace(' ', '-')}",
        "chart_type": chart_type,
        "datasource": "sales_orders",
    }
    entry.update(extra)
    return entry


#: One case per legacy pattern: ``(golden slug, manifest worksheet)``. The sheet name is the
#: slug title-cased, which is what the golden filename and the element id are derived from.
CHART_CASES: list[tuple[str, dict]] = [
    ("bar", _sheet("Bar", "bar",
                   shelves={"columns": ["product_category"], "rows": ["revenue"]})),
    ("bar-sorted", _sheet(
        "Bar Sorted", "bar",
        shelves={"columns": ["product_category"], "rows": ["revenue"]},
        sort={"field": "product_category", "direction": "DESC",
              "by": {"field": "revenue", "aggregation": "sum"}},
    )),
    ("bar-filtered", _sheet(
        "Bar Filtered", "bar",
        shelves={"columns": ["product_category"], "rows": ["revenue"]},
        filters=[
            {"field": "region", "values": ["Europe", "Asia"], "context": True},
            {"field": "order_date", "min": "2025-03-03", "max": "2025-04-22"},
        ],
    )),
    ("bar-styled", _sheet(
        "Bar Styled", "bar",
        shelves={"columns": ["product_category"], "rows": ["revenue"]},
        axis_titles={"rows": "Revenue per category"},
        number_formats=[{"field": "revenue", "format": "$#,##0"}],
    )),
    ("bar-stacked", _sheet(
        "Bar Stacked", "bar",
        shelves={"columns": ["product_category"], "rows": ["revenue"]},
        encodings={"color": "region"},
    )),
    ("line", _sheet("Line", "line", shelves={
        "columns": [{"field": "order_date", "date_part": "month"}], "rows": ["revenue"],
    })),
    ("area", _sheet("Area", "area", shelves={
        "columns": [{"field": "order_date", "date_part": "week"}], "rows": ["revenue"],
    })),
    ("pie", _sheet("Pie", "pie", encodings={
        "color": "product_category", "wedge-size": "revenue", "size": "revenue",
        "text": "revenue",
    })),
    ("scatter", _sheet(
        "Scatter", "scatter",
        shelves={"columns": ["profit"], "rows": ["revenue"]},
        encodings={"lod": "product_category"},
    )),
    ("text-table", _sheet(
        "Text Table", "table",
        shelves={"columns": ["region"], "rows": ["product_category"]},
        encodings={"text": "revenue"},
    )),
    ("kpi-card", _sheet("Kpi Card", "text", encodings={"text": "revenue"})),
    ("histogram", _sheet("Histogram", "histogram", shelves={
        "columns": [{"field": "revenue", "bin": 500}],
        "rows": [{"field": "revenue", "aggregation": "count"}],
    })),
    ("map", _sheet(
        "Map", "map",
        encodings={"lod": "country", "color": "profit"},
        geo_role="[Country].[ISO3166_2]",
    )),
    ("dual-axis", _sheet("Dual Axis", "dual-axis", shelves={
        "columns": [{"field": "order_date", "date_part": "month"}],
        "rows": ["revenue", "profit"],
    })),
    ("combo", _sheet("Combo", "combo", shelves={
        "columns": [{"field": "order_date", "date_part": "month"}],
        "rows": ["revenue", "profit"],
    })),
    ("custom-tooltip", _sheet(
        "Custom Tooltip", "bar",
        shelves={"columns": ["product_category"], "rows": ["revenue"]},
        tooltip=[
            {"label": "Category", "field": "product_category", "aggregation": "attr"},
            {"label": "Revenue", "field": "revenue", "aggregation": "sum"},
        ],
    )),
    ("calculated-field", _sheet(
        "Calculated Field", "bar",
        shelves={"columns": ["region"], "rows": ["Margin Pct"]},
    )),
]

SHEET_NAMES = [entry["name"] for _, entry in CHART_CASES]


def _manifest(worksheets=None, **overrides) -> dict:
    """A manifest whose layout places one zone per worksheet."""
    entries = list(worksheets if worksheets is not None else [e for _, e in CHART_CASES])
    document = {
        "target_tableau_version": TARGET_VERSION,
        "datasources": [{
            "name": "sales_orders",
            "csv": "sales_orders.csv",
            "fields": [
                {"name": "order_date", "type": "date"},
                {"name": "region", "type": "string"},
                {"name": "country", "type": "string"},
                {"name": "product_category", "type": "string"},
                {"name": "revenue", "type": "real"},
                {"name": "profit", "type": "real"},
            ],
        }],
        "calculated_fields": [{
            "name": "Margin Pct",
            "formula": "SUM([profit]) / SUM([revenue])",
            "datasource": "sales_orders",
            "type": "real",
        }],
        "worksheets": entries,
        "layout": {
            "canvas": {"width": 1366, "height": 768},
            "root": {
                "type": "vert",
                "children": [{"id": entry["element_id"]} for entry in entries],
            },
        },
        "actions": [],
        "parameters": [],
    }
    document.update(overrides)
    return document


def _render(document=None, tokens: str = DESIGN_TOKENS) -> str:
    """Render a manifest to .twb XML."""
    return twb.render_workbook(
        document if document is not None else _manifest(), DATA_MODEL, CSV_HEADERS, tokens
    )


ALL_CHARTS_XML = _render()
ALL_CHARTS_ROOT = ET.fromstring(ALL_CHARTS_XML)


def _worksheet_element(name: str, root: ET.Element = ALL_CHARTS_ROOT) -> ET.Element:
    """Return the ``<worksheet>`` with the given name."""
    for element in root.findall("worksheets/worksheet"):
        if element.get("name") == name:
            return element
    raise AssertionError(f"no worksheet named {name!r} in the rendered workbook")


def _worksheet_xml(name: str, xml_text: str = ALL_CHARTS_XML) -> str:
    """Return one worksheet's raw serialised XML, sliced out of the workbook text.

    Sliced rather than re-serialised from the parsed tree on purpose: ``ET`` turns a CDATA
    section back into entity-escaped text, which is exactly the mistake the golden has to
    be able to catch (Tableau prints an entity-escaped field reference as literal text).
    """
    start = xml_text.index(f'<worksheet name="{name}">')
    end = xml_text.index("</worksheet>", start) + len("</worksheet>")
    return xml_text[start:end]


# --- Golden output (AC #4) ----------------------------------------------------

@pytest.mark.parametrize("slug,entry", CHART_CASES, ids=[slug for slug, _ in CHART_CASES])
def test_chart_template_matches_its_golden(slug, entry):
    """Each pattern's rendered worksheet is byte-identical to its golden file.

    This is the regression net the ticket asks for: change a mark class, a shelf, an
    encoding or a style rule and exactly the affected chart types fail, with the diff
    naming what moved.
    """
    rendered = _worksheet_xml(entry["name"])
    golden = GOLDEN_DIR / f"{slug}.xml"

    if UPDATE_GOLDENS:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden.write_text(rendered + "\n", encoding="utf-8")

    assert golden.exists(), (
        f"missing golden {golden.relative_to(GOLDEN_DIR.parents[2])} - regenerate with "
        f"UPDATE_CHART_GOLDENS=1"
    )
    assert rendered == golden.read_text(encoding="utf-8").strip()


#: The ticket's parity list, as ``slug -> what makes that pattern that pattern``. A golden
#: set that drops one of these has stopped covering a legacy chart.
REQUIRED_PATTERNS: dict[str, str] = {
    "bar": "chart_type",
    "bar-sorted": "sort",
    "bar-filtered": "filters",
    "bar-styled": "axis_titles",
    "bar-stacked": "encodings",
    "line": "chart_type",
    "area": "chart_type",
    "pie": "chart_type",
    "scatter": "chart_type",
    "text-table": "chart_type",
    "kpi-card": "chart_type",
    "histogram": "chart_type",
    "map": "chart_type",
    "dual-axis": "chart_type",
    "combo": "chart_type",
    "custom-tooltip": "tooltip",
}


def test_every_legacy_pattern_has_a_case():
    """All 15 legacy patterns are covered, and each really exercises what defines it.

    A bare count would pass with fifteen bar charts; this checks each named pattern is
    present *and* that its distinguishing manifest key is actually set.
    """
    cases = dict(CHART_CASES)
    assert len(cases) == len(CHART_CASES), "duplicate golden slug"
    missing = sorted(set(REQUIRED_PATTERNS) - set(cases))
    assert not missing, f"legacy pattern(s) with no golden case: {missing}"

    chart_types = set()
    for slug, key in REQUIRED_PATTERNS.items():
        assert key in cases[slug], f"{slug} does not exercise its '{key}'"
        chart_types.add(cases[slug]["chart_type"])

    # The 15 patterns span 12 distinct chart types; the rest are modifiers.
    assert chart_types == {
        "bar", "line", "area", "pie", "scatter", "table", "text", "histogram", "map",
        "dual-axis", "combo",
    }


def test_every_chart_type_is_buildable():
    """Every type manifest.CHART_TYPES accepts has a spec - otherwise validate lets through
    a chart the assembler renders as a bare Automatic mark."""
    assert manifest.CHART_TYPES <= set(worksheet.CHART_SPECS)


# --- Parameterisation, not copy-paste (AC #1) ---------------------------------

#: Values that only exist in the legacy snippets. Any of them in generated XML means a
#: template was copied rather than built.
SNIPPET_LEAKS = (
    "federated.1hckotw0bte0i51b8k3sd1ffpnqc",
    "federated.0oycml11kgsls31h6pvlk0w4f5yj",
    "8ED4AD55-A43F-4C33-B8C1-A6484D0F1985",
    "Calculation_1923881515222933504",
    "product_name",
    "[cost]",
)


def test_no_snippet_values_leak():
    """No snippet's datasource hash, UUID, calculation id or field name reaches the output."""
    for leak in SNIPPET_LEAKS:
        assert leak not in ALL_CHARTS_XML, f"snippet value {leak!r} leaked into the workbook"


def test_the_same_manifest_rebuilds_byte_identical():
    """Ids are hash-derived, so a re-run is diff-free (and the goldens are stable)."""
    assert _render() == ALL_CHARTS_XML


# --- Mark classes and shelves --------------------------------------------------

@pytest.mark.parametrize("sheet_name,mark_class", [
    ("Bar", "Automatic"),
    ("Line", "Automatic"),
    ("Area", "Area"),
    ("Pie", "Pie"),
    ("Scatter", "Automatic"),
    ("Text Table", "Automatic"),
    ("Kpi Card", "Automatic"),
    ("Histogram", "Automatic"),
    ("Map", "Automatic"),
])
def test_mark_class_per_chart_type(sheet_name, mark_class):
    """The mark class is what makes Tableau draw the right shape; Area and Pie must be
    explicit or Tableau falls back to a line."""
    pane = _worksheet_element(sheet_name).find("table/panes/pane")
    assert pane.find("mark").get("class") == mark_class


def test_pie_and_kpi_put_everything_on_encodings():
    """Both charts carry empty shelves - all their data flows through encodings."""
    for sheet_name in ("Pie", "Kpi Card"):
        table = _worksheet_element(sheet_name).find("table")
        assert not (table.findtext("rows") or "").strip()
        assert not (table.findtext("cols") or "").strip()


def test_pie_has_a_wedge_size_encoding():
    """wedge-size is what gives each slice its angle; without it the pie is one wedge."""
    encodings = _worksheet_element("Pie").find("table/panes/pane/encodings")
    assert encodings.find("wedge-size") is not None


def test_line_uses_a_continuous_truncated_date():
    """A discrete date draws bars; the Month-Trunc continuous instance is what makes a line."""
    element = _worksheet_element("Line")
    instance = element.find(
        "table/view/datasource-dependencies/"
        "column-instance[@name='[tmn:order_date:qk]']"
    )
    assert instance is not None
    assert instance.get("derivation") == "Month-Trunc"
    assert instance.get("type") == "quantitative"
    assert "[tmn:order_date:qk]" in element.findtext("table/cols")


def test_scatter_puts_measures_on_both_axes_and_a_dimension_on_detail():
    """Two continuous measures are what turn a view into a scatter plot."""
    element = _worksheet_element("Scatter")
    assert "[sum:profit:qk]" in element.findtext("table/cols")
    assert "[sum:revenue:qk]" in element.findtext("table/rows")
    assert element.find("table/panes/pane/encodings/lod") is not None


def test_text_table_puts_dimensions_on_both_axes_and_the_measure_on_text():
    """The measure appears only as a label - that is what makes it a table, not a chart."""
    element = _worksheet_element("Text Table")
    assert "[none:region:nk]" in element.findtext("table/cols")
    assert "[none:product_category:nk]" in element.findtext("table/rows")
    assert element.find("table/panes/pane/encodings/text") is not None


def test_labelled_charts_show_their_mark_labels():
    """A text table or pie whose labels are off renders as blank cells / unlabelled wedges."""
    for sheet_name in ("Text Table", "Pie", "Kpi Card"):
        rule = _worksheet_element(sheet_name).find(
            "table/panes/pane/style/style-rule[@element='mark']"
        )
        shown = {fmt.get("attr"): fmt.get("value") for fmt in rule.findall("format")}
        assert shown["mark-labels-show"] == "true"


def test_kpi_card_renders_its_value_as_a_big_cdata_label():
    """The KPI's number comes from a customized-label; the field reference must be CDATA,
    or Tableau prints the reference as literal text."""
    element = _worksheet_element("Kpi Card")
    run = element.find("table/panes/pane/customized-label/formatted-text/run")
    assert run.get("bold") == "true"
    assert int(run.get("fontsize")) >= 22
    assert "[sum:revenue:qk]" in run.text
    assert "<![CDATA[<[federated." in ALL_CHARTS_XML


def test_histogram_declares_a_bin_column_and_slices_it_ordinally():
    """A histogram's x axis is a bin *column* with its own calculation, not a derivation."""
    element = _worksheet_element("Histogram")
    column = element.find(
        "table/view/datasource-dependencies/column[@name='[Revenue (bin)]']"
    )
    assert column.get("role") == "dimension"
    calculation = column.find("calculation")
    assert calculation.get("class") == "bin"
    assert calculation.get("formula") == "[revenue]"
    assert calculation.get("size") == "500"
    assert "[none:Revenue (bin):ok]" in element.findtext("table/cols")


def test_a_bin_column_is_also_declared_on_the_datasource():
    """Tableau drops a calculation that exists only inside a worksheet when it saves."""
    column = ALL_CHARTS_ROOT.find(
        "datasources/datasource/column[@name='[Revenue (bin)]']"
    )
    assert column is not None and column.find("calculation") is not None


def test_a_calculated_field_reaches_the_datasource_and_the_shelf():
    """A shelf can only reference a calculated field the datasource actually declares."""
    column = ALL_CHARTS_ROOT.find(
        "datasources/datasource/column[@name='[Margin Pct]']"
    )
    assert column.find("calculation").get("formula") == "SUM([profit]) / SUM([revenue])"
    assert "[usr:Margin Pct:qk]" in _worksheet_element("Calculated Field").findtext(
        "table/rows"
    )


def test_an_already_aggregated_calculation_is_not_aggregated_again():
    """SUM(SUM([profit]) / SUM([revenue])) is an error Tableau refuses - an aggregate calc
    goes on the shelf as-is, which Tableau records as the 'User' derivation."""
    instance = _worksheet_element("Calculated Field").find(
        "table/view/datasource-dependencies/column-instance[@column='[Margin Pct]']"
    )
    assert instance.get("derivation") == "User"
    assert instance.get("name") == "[usr:Margin Pct:qk]"


def test_a_row_level_calculation_still_gets_the_default_sum():
    """Only *aggregate* formulas skip the default aggregation; a row-level calc is an
    ordinary measure and must still be summed."""
    document = _manifest(
        [_sheet("Line Total", "bar",
                shelves={"columns": ["region"], "rows": ["Line Total"]})],
        calculated_fields=[{
            "name": "Line Total", "formula": "[revenue] - [profit]",
            "datasource": "sales_orders", "type": "real",
        }],
    )
    element = _worksheet_element("Line Total", ET.fromstring(_render(document)))
    instance = element.find(
        "table/view/datasource-dependencies/column-instance[@column='[Line Total]']"
    )
    assert instance.get("derivation") == "Sum"


@pytest.mark.parametrize("formula,aggregate", [
    ("SUM([profit]) / SUM([revenue])", True),
    ("COUNTD([order_id])", True),
    ("WINDOW_AVG(SUM([revenue]))", True),
    ("[revenue] - [profit]", False),
    ("IF [revenue] > 0 THEN 1 ELSE 0 END", False),
])
def test_aggregate_formula_detection(formula, aggregate):
    """The rule that decides it, pinned directly."""
    assert worksheet.is_aggregate_formula(formula) is aggregate


def test_map_uses_the_generated_geographic_fields():
    """A map plots Tableau's generated lat/long, with the real dimension on Detail."""
    element = _worksheet_element("Map")
    assert "[Longitude (generated)]" in element.findtext("table/cols")
    assert "[Latitude (generated)]" in element.findtext("table/rows")
    assert element.find("table/view/mapsources/mapsource") is not None
    assert element.find("table/panes/pane/encodings/geometry") is not None
    column = element.find("table/view/datasource-dependencies/column[@name='[country]']")
    assert column.get("semantic-role") == "[Country].[ISO3166_2]"


def test_a_map_declares_its_basemap_at_the_workbook_level_too():
    """The <mapsources> in the view references a source the workbook must declare; one
    without the other renders no basemap."""
    assert ALL_CHARTS_ROOT.findtext("mapsources/mapsource") is not None or (
        ALL_CHARTS_ROOT.find("mapsources/mapsource") is not None
    )
    assert ALL_CHARTS_ROOT.find("mapsources/mapsource").get("name") == "Tableau"


def test_no_map_means_no_workbook_mapsources():
    """A workbook with no map has no basemap to declare."""
    document = _manifest([_sheet(
        "Bar", "bar", shelves={"columns": ["region"], "rows": ["revenue"]},
    )])
    assert ET.fromstring(_render(document)).find("mapsources") is None


def test_a_kpi_card_suppresses_its_tooltip():
    """A KPI card is one number, not a mark worth hovering."""
    style = _worksheet_element("Kpi Card").find("table/tooltip-style")
    assert style.get("tooltip-mode") == "none"
    assert _worksheet_element("Bar").find("table/tooltip-style") is None


def test_a_histogram_shows_its_empty_bins():
    """Without show-full-range an empty bin vanishes and the distribution misreads."""
    element = _worksheet_element("Histogram")
    columns = element.findall("table/show-full-range/column")
    assert [column.text.rsplit(".", 1)[-1] for column in columns] == ["[Revenue (bin)]"]
    assert _worksheet_element("Bar").find("table/show-full-range") is None


def test_dual_axis_overlays_two_measures_across_three_panes():
    """Pane 0 is shared; panes 1 and 2 each own one measure's axis, in shelf order."""
    element = _worksheet_element("Dual Axis")
    assert element.findtext("table/rows") == (
        "([{ds}].[sum:revenue:qk] + [{ds}].[sum:profit:qk])".format(
            ds=twb.datasource_id("sales_orders")
        )
    )
    panes = element.findall("table/panes/pane")
    assert len(panes) == 3
    assert [pane.get("id") for pane in panes] == [None, "1", "2"]
    assert panes[1].get("y-axis-name").endswith("[sum:revenue:qk]")
    assert panes[2].get("y-axis-name").endswith("[sum:profit:qk]")


def test_dual_axis_synchronises_and_hides_the_second_axis():
    """Unsynchronised axes make two unrelated scales look like one chart."""
    rule = _worksheet_element("Dual Axis").find("table/style/style-rule[@element='axis']")
    assert rule.find("encoding").get("synchronized") == "true"
    assert rule.find("format").get("value") == "false"


def test_combo_overrides_the_mark_class_per_pane():
    """The only structural difference from a dual axis: Bar on one axis, Line on the other."""
    panes = _worksheet_element("Combo").findall("table/panes/pane")
    assert [pane.find("mark").get("class") for pane in panes] == [
        "Automatic", "Bar", "Line",
    ]


def test_dual_charts_colour_their_measures_apart():
    """Without Measure Names on colour, both series draw in one colour."""
    for sheet_name in ("Dual Axis", "Combo"):
        for pane in _worksheet_element(sheet_name).findall("table/panes/pane"):
            assert pane.find("encodings/color").get("column").endswith("[:Measure Names]")


# --- Modifiers: sort, filters, tooltip ----------------------------------------

def test_computed_sort_names_the_dimension_and_the_measure():
    """A sorted bar orders its dimension *by* a measure - both references must resolve."""
    sort = _worksheet_element("Bar Sorted").find("table/view/computed-sort")
    assert sort.get("column").endswith("[none:product_category:nk]")
    assert sort.get("using").endswith("[sum:revenue:qk]")
    assert sort.get("direction") == "DESC"


def test_a_manual_sort_writes_the_member_order():
    """An explicit order becomes a <dictionary> of quoted buckets."""
    document = _manifest([_sheet(
        "Manual", "bar",
        shelves={"columns": ["region"], "rows": ["revenue"]},
        sort={"field": "region", "direction": "ASC", "order": ["West", "East"]},
    )])
    element = _worksheet_element("Manual", ET.fromstring(_render(document)))
    buckets = element.findall("table/view/sort/dictionary/bucket")
    assert [bucket.text for bucket in buckets] == ['"West"', '"East"']


def test_a_multi_value_filter_wraps_its_members_in_a_union():
    """One member is a bare groupfilter; several need a union, or only the last applies."""
    element = _worksheet_element("Bar Filtered")
    categorical = element.find("table/view/filter[@class='categorical']")
    assert categorical.get("context") == "true"
    union = categorical.find("groupfilter")
    assert union.get("function") == "union"
    assert [member.get("member") for member in union.findall("groupfilter")] == [
        '"Europe"', '"Asia"',
    ]


def test_a_date_range_filter_uses_hash_delimited_bounds_and_a_continuous_instance():
    """Tableau needs #...# literals, and a range needs the continuous date instance."""
    element = _worksheet_element("Bar Filtered")
    quantitative = element.find("table/view/filter[@class='quantitative']")
    assert quantitative.get("column").endswith("[none:order_date:qk]")
    assert quantitative.findtext("min") == "#2025-03-03#"
    assert quantitative.findtext("max") == "#2025-04-22#"


def test_every_filtered_field_is_declared_and_sliced():
    """A filter on an undeclared field, or one missing from <slices>, is silently ignored."""
    element = _worksheet_element("Bar Filtered")
    declared = {
        instance.get("name")
        for instance in element.findall("table/view/datasource-dependencies/column-instance")
    }
    for filter_element in element.findall("table/view/filter"):
        column = filter_element.get("column")
        assert column.rsplit(".", 1)[-1] in declared
    sliced = {column.text for column in element.findall("table/view/slices/column")}
    assert sliced == {
        filter_element.get("column")
        for filter_element in element.findall("table/view/filter")
    }


def test_a_custom_tooltip_registers_every_field_it_names():
    """The three-part chain: declared instance, tooltip encoding, template run."""
    element = _worksheet_element("Custom Tooltip")
    pane = element.find("table/panes/pane")
    registered = {
        tooltip.get("column").rsplit(".", 1)[-1]
        for tooltip in pane.findall("encodings/tooltip")
    }
    assert registered == {"[attr:product_category:nk]", "[sum:revenue:qk]"}

    runs = pane.findall("customized-tooltip/formatted-text/run")
    assert runs[0].text == "Category:"
    assert "[attr:product_category:nk]" in runs[2].text
    # The value runs must be CDATA - entity-escaped, Tableau prints the reference verbatim.
    assert "<![CDATA[<[federated" in _worksheet_xml("Custom Tooltip")


def test_a_tooltip_field_reaches_the_dependencies():
    """A tooltip-only field still has to be declared, or the tooltip renders empty."""
    declared = {
        instance.get("name")
        for instance in _worksheet_element("Custom Tooltip").findall(
            "table/view/datasource-dependencies/column-instance"
        )
    }
    assert "[attr:product_category:nk]" in declared


# --- Design tokens (AC #3) -----------------------------------------------------

def test_tokens_set_the_body_font_and_the_title_run():
    """Font family and title styling are the tokens a worksheet can genuinely carry."""
    element = _worksheet_element("Bar")
    run = element.find("layout-options/title/formatted-text/run")
    assert run.get("fontname") == "Open Sans"
    assert run.get("fontsize") == "14"
    assert run.get("fontcolor") == "#7f56d9"

    rule = element.find("table/style/style-rule[@element='worksheet']")
    assert rule.find("format").get("value") == "Open Sans"


def test_token_hex_colours_are_lower_cased():
    """DESIGN-TOKENS.md writes #7F56D9; Tableau writes lower case, so a save would diff."""
    assert "#7F56D9" not in ALL_CHARTS_XML


def test_without_tokens_no_styling_is_invented():
    """No branding step means Tableau's own defaults - not a made-up font or colour."""
    root = ET.fromstring(_render(tokens=""))
    element = _worksheet_element("Bar", root)
    assert element.find("layout-options") is None
    assert element.find("table/style/style-rule[@element='worksheet']") is None


def test_an_unfilled_token_template_is_ignored():
    """A DESIGN-TOKENS.md still carrying '[font]' placeholders must not style anything
    with the literal placeholder text."""
    tokens = worksheet.parse_design_tokens(
        "## Typography\n\n- **Font family**: [font]\n- **Chart title**: [size]px\n"
    )
    assert tokens.font_family == worksheet.DEFAULT_FONT


def test_style_rules_are_alphabetical_by_element():
    """Tableau Desktop rewrites them alphabetically on save; emitting any other order
    produces a diff the analyst did not make."""
    for element in ALL_CHARTS_ROOT.findall("worksheets/worksheet"):
        for style in element.findall(".//style"):
            elements = [rule.get("element") for rule in style.findall("style-rule")]
            assert elements == sorted(elements), element.get("name")


# --- Legends -------------------------------------------------------------------

def _worksheet_windows() -> dict:
    """``{sheet name: <window>}`` for the worksheet-class windows."""
    return {
        window.get("name"): window
        for window in ALL_CHARTS_ROOT.findall("windows/window")
        if window.get("class") == "worksheet"
    }


def test_colour_and_size_encoded_charts_get_legend_cards():
    """A chart encoded by colour with no legend renders, but reads as broken."""
    windows = _worksheet_windows()
    pie_cards = windows["Pie"].findall("cards/edge[@name='right']/strip/card")
    assert [card.get("type") for card in pie_cards] == ["color", "size"]
    assert all(card.get("param") for card in pie_cards)
    assert all(card.get("pane-specification-id") == "0" for card in pie_cards)

    assert windows["Bar"].find("cards/edge[@name='right']") is None


@pytest.mark.parametrize("sheet_name", ["Dual Axis", "Combo"])
def test_dual_charts_get_a_measure_names_legend_on_their_first_measure_pane(sheet_name):
    """Their colour comes from the built-in Measure Names, which the manifest never names -
    so the legend has to be derived from the chart type, and it belongs to pane 1."""
    card = _worksheet_windows()[sheet_name].find(
        "cards/edge[@name='right']/strip/card"
    )
    assert card.get("type") == "color"
    assert card.get("param").endswith("[:Measure Names]")
    assert card.get("pane-specification-id") == "1"


# --- The whole workbook still validates ----------------------------------------

def test_the_all_charts_workbook_passes_the_semantic_validator(tmp_path):
    """Every migrated breakage check, over a workbook holding every chart type (AC #2)."""
    import validate_twb

    twb_path = tmp_path / "all-charts.twb"
    twb_path.write_text(ALL_CHARTS_XML, encoding="utf-8")
    report = validate_twb.TwbValidator(str(twb_path)).validate()
    failures = [
        f"{result.name}: {result.details}" for result in report.results if not result.passed
    ]
    assert not failures


def test_the_all_charts_workbook_passes_the_xsd(tmp_path):
    """Schema-valid against the 2026.1 XSD - the element order inside a worksheet is not
    something the assembler may get 'nearly' right."""
    lxml = pytest.importorskip("lxml")  # noqa: F841 - the validator imports it itself
    import validate_twb_xsd

    twb_path = tmp_path / "all-charts.twb"
    twb_path.write_text(ALL_CHARTS_XML, encoding="utf-8")
    _, errors = validate_twb_xsd.validate(
        twb_path, validate_twb_xsd.load_schema(validate_twb_xsd.XSD_PATH)
    )
    assert not [f"line {error.line}: {error.message}" for error in errors]


def test_an_all_charts_manifest_validates(tmp_path):
    """The schema accepts every chart type and modifier key the templates support - a
    manifest the builder can render must not be one validate rejects."""
    assert manifest.validate_manifest(_manifest(), DATA_MODEL, TARGET_VERSION) == []


def test_a_dual_axis_without_two_measures_is_rejected():
    """One measure on rows means no second axis - the chart type is the wrong choice."""
    document = _manifest([_sheet("Dual", "dual-axis", shelves={
        "columns": ["region"], "rows": ["revenue"],
    })])
    errors = manifest.validate_manifest(document, DATA_MODEL, TARGET_VERSION)
    assert any("exactly two measures" in error for error in errors)


def test_a_negative_bin_width_is_rejected():
    """A bin of 0 or less would divide the axis into nothing."""
    document = _manifest([_sheet("Hist", "histogram", shelves={
        "columns": [{"field": "revenue", "bin": 0}], "rows": ["revenue"],
    })])
    errors = manifest.validate_manifest(document, DATA_MODEL, TARGET_VERSION)
    assert any("bin width must be a positive number" in error for error in errors)


def test_a_filter_with_nothing_to_filter_on_is_rejected():
    """The builder would drop it and Tableau would show no sign - name the row instead."""
    document = _manifest([_sheet(
        "Empty Filter", "bar",
        shelves={"columns": ["region"], "rows": ["revenue"]},
        filters=[{"field": "region", "context": True}],
    )])
    errors = manifest.validate_manifest(document, DATA_MODEL, TARGET_VERSION)
    assert any("nothing to filter on" in error for error in errors)


def test_a_sort_with_neither_by_nor_order_is_rejected():
    """Same failure mode: an incomplete sort silently does nothing."""
    document = _manifest([_sheet(
        "Empty Sort", "bar",
        shelves={"columns": ["region"], "rows": ["revenue"]},
        sort={"field": "region", "direction": "ASC"},
    )])
    errors = manifest.validate_manifest(document, DATA_MODEL, TARGET_VERSION)
    assert any("neither 'by'" in error for error in errors)


def test_a_filter_on_an_undocumented_field_is_rejected():
    """Modifier fields are resolved as strictly as shelf fields."""
    document = _manifest([_sheet(
        "Bad Filter", "bar",
        shelves={"columns": ["region"], "rows": ["revenue"]},
        filters=[{"field": "no_such_field", "values": ["x"]}],
    )])
    errors = manifest.validate_manifest(document, DATA_MODEL, TARGET_VERSION)
    assert any("no_such_field" in error for error in errors)


# --- End to end through build.py -----------------------------------------------

def test_build_packages_a_workbook_holding_every_chart_type(tmp_path):
    """The ticket's headline: a workbook with one sheet per chart type builds, passes both
    validators, and is packaged (AC #2 - the Tableau Desktop open is manual)."""
    import init
    import json

    project = tmp_path / "project"
    project.mkdir()
    (project / "STATE.md").write_text(
        build.apply_status_updates(
            init.render_state_md(TARGET_VERSION),
            {"data": "approved", "spec": "approved"},
        ),
        encoding="utf-8",
    )
    (project / "DATA-MODEL.md").write_text(DATA_MODEL, encoding="utf-8")
    (project / "DESIGN-TOKENS.md").write_text(DESIGN_TOKENS, encoding="utf-8")
    data_dir = project / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "sales_orders.csv").write_text(
        ",".join(CSV_HEADERS["sales_orders.csv"]) + "\n", encoding="utf-8"
    )

    document = _manifest()
    version_dir = project / "mock-version" / "v_1"
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "IMPLEMENTATION-SPEC.md").write_text(
        "# Spec\n\n## Layout\n\n```json\n"
        + json.dumps(document["layout"], indent=2)
        + "\n```\n",
        encoding="utf-8",
    )
    (version_dir / "build-manifest.json").write_text(
        json.dumps(document, indent=2), encoding="utf-8"
    )

    result = build.build_workbook(project)
    assert result.ok, result.errors
    assert (project / result.twbx_path).exists()
    assert len(
        ET.fromstring(
            (project / result.twb_path).read_text(encoding="utf-8")
        ).findall("worksheets/worksheet")
    ) == len(CHART_CASES)
