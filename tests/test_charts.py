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

## Colors

### Chart series colors
`#1B4F72`, `#2E86C1`, `#48C9B0`, `#F39C12`

### Text
- Dark (titles): #1C2833
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
    # The text encoding is what makes the number_format visible: without labelled marks a
    # styled bar renders identically to a plain one, which is the #35 symptom.
    ("bar-styled", _sheet(
        "Bar Styled", "bar",
        shelves={"columns": ["product_category"], "rows": ["revenue"]},
        axis_titles={"rows": "Revenue per category"},
        number_formats=[{"field": "revenue", "format": "$#,##0"}],
        encodings={"text": {"field": "revenue", "aggregation": "sum"}},
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
    # Issue #44: a measure on Text is mark labels on any chart type, and it is what makes a
    # number format visible - the cell format only reaches a chart through its labels.
    ("bar-labelled", _sheet(
        "Bar Labelled", "bar",
        shelves={"columns": ["product_category"], "rows": ["revenue"]},
        encodings={"text": {"field": "revenue", "aggregation": "sum"}},
        number_formats=[{"field": "revenue", "format": "$#,##0"}],
    )),
    # Issue #44: Format Borders / Lines / Shading / Alignment, plus a non-default fit.
    ("bar-formatted", _sheet(
        "Bar Formatted", "bar",
        shelves={"columns": ["product_category"], "rows": ["revenue"]},
        fit="fit-width",
        format={
            "shading": "#FFFFFF", "borders": "none", "gridlines": "#E5E8E8",
            "zero_lines": "none", "align": "center", "vertical_align": "bottom",
        },
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
    ("Bar", "Bar"),
    ("Line", "Line"),
    ("Area", "Area"),
    ("Pie", "Pie"),
    ("Scatter", "Circle"),
    ("Text Table", "Text"),
    ("Kpi Card", "Text"),
    ("Histogram", "Bar"),
    ("Map", "Automatic"),
])
def test_mark_class_per_chart_type(sheet_name, mark_class):
    """The mark class is what makes Tableau draw the right shape; every single-pane type
    states it rather than letting Tableau's Automatic infer one from the shelves."""
    pane = _worksheet_element(sheet_name).find("table/panes/pane")
    assert pane.find("mark").get("class") == mark_class


def test_a_bar_over_a_continuous_date_is_still_a_bar():
    """Issue #64: the reproducing shape. Automatic reads a continuous date x measure as
    points, so a month-spine bar chart drew scattered dots instead of bars."""
    document = _manifest(worksheets=[_sheet("Monthly Bar", "bar", shelves={
        "columns": [{"field": "order_date", "date_part": "month"}], "rows": ["revenue"],
    })])
    element = _worksheet_element("Monthly Bar", ET.fromstring(_render(document)))
    assert element.find("table/panes/pane/mark").get("class") == "Bar"


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


# --- Issue #62: aggregate calcs reach the shelf as 'usr:' ---------------------------------

def test_the_aggregate_closure_does_not_depend_on_declaration_order():
    """The chain is walked to a fixpoint, not in one pass. Declared dependents-first here on
    purpose: a single sweep in declaration order would see 'Badge' before 'Label' had joined
    the set and leave both out, which is a 'none:' pill on every sheet that uses them."""
    declared = {
        "Badge": worksheet.CalculatedField("'(' + [Label] + ')'", "string"),
        "Label": worksheet.CalculatedField("STR([Ratio])", "string"),
        "Ratio": worksheet.CalculatedField("SUM([profit]) / SUM([revenue])", "real"),
        "Row Level": worksheet.CalculatedField("[revenue] - [profit]", "real"),
        # An LOD is row-level at its own grain, so neither it nor what references it is an
        # aggregate - the distinction is_aggregate_formula already draws.
        "Regional": worksheet.CalculatedField("{FIXED [region]: SUM([revenue])}", "real"),
        "Regional Share": worksheet.CalculatedField("[Regional] / 2", "real"),
    }

    assert worksheet.aggregate_calculated_fields(declared) == frozenset(
        {"Ratio", "Label", "Badge"}
    )


#: Three calcs one hop apart: ``Ratio`` aggregates directly, the other two only *reference*
#: it and so are aggregate too - Tableau's rule is transitive, and both are strings, which
#: is the case that used to fall through to ``none:``.
_TRANSITIVE_CALCS = [
    {"name": "Ratio", "formula": "SUM([profit]) / SUM([revenue])",
     "datasource": "sales_orders", "type": "real"},
    {"name": "Ratio Label", "formula": "STR([Ratio])",
     "datasource": "sales_orders", "type": "string"},
    {"name": "Ratio Dir", "formula": "IF [Ratio] >= 0 THEN 'up' ELSE 'down' END",
     "datasource": "sales_orders", "type": "string"},
    # Numeric and one hop out - the case that fell to the measure default 'sum:' and reached
    # Desktop as a double aggregation.
    {"name": "Ratio Scaled", "formula": "[Ratio] * 100",
     "datasource": "sales_orders", "type": "real"},
    # Two hops: aggregate only through 'Ratio Label', which is itself only aggregate through
    # 'Ratio'. One pass over the calcs would miss this - it is what the closure iterates for.
    {"name": "Ratio Badge", "formula": "'(' + [Ratio Label] + ')'",
     "datasource": "sales_orders", "type": "string"},
]


def _transitive_aggregate_xml() -> str:
    """Render a sheet placing all four calcs, the numeric one with ``aggregation: none``."""
    document = _manifest(
        [_sheet(
            "Ratio Card", "text",
            encodings={
                "text": {"field": "Ratio", "aggregation": "none"},
                "color": "Ratio Dir",
            },
            tooltip=[
                {"label": "Label", "field": "Ratio Label"},
                {"label": "Scaled", "field": "Ratio Scaled"},
                {"label": "Badge", "field": "Ratio Badge"},
            ],
        )],
        calculated_fields=_TRANSITIVE_CALCS,
    )
    return _render(document)


def test_a_literal_brace_does_not_hide_an_aggregate_reference():
    """Review of #62: an unclosed brace is a string literal, not an LOD. Stripping from it
    onwards swallowed the '[Ratio]' reference, so 'Badge' escaped the closure and went back
    to the 'none:' pill the fix exists to remove."""
    declared = {
        "Ratio": worksheet.CalculatedField("SUM([profit]) / SUM([revenue])", "real"),
        "Badge": worksheet.CalculatedField('"{" + STR([Ratio])', "string"),
    }

    assert worksheet.aggregate_calculated_fields(declared) == frozenset({"Ratio", "Badge"})


@pytest.mark.parametrize("field,instance,column_type", [
    ("Ratio", "[usr:Ratio:qk]", "quantitative"),
    ("Ratio Label", "[usr:Ratio Label:nk]", "nominal"),
    ("Ratio Dir", "[usr:Ratio Dir:nk]", "nominal"),
    ("Ratio Scaled", "[usr:Ratio Scaled:qk]", "quantitative"),
    ("Ratio Badge", "[usr:Ratio Badge:nk]", "nominal"),
])
def test_an_aggregate_calc_reaches_the_shelf_as_a_user_derivation(field, instance, column_type):
    """Issue #62: a calc that aggregates - directly or by referencing one that does - has no
    row-level value, so any instance but ``usr:`` is a pill Desktop refuses with "can't be
    applied to a user-defined aggregate".

    ``Ratio`` carries ``"aggregation": "none"``, which is how BUILD-MANIFEST-TEMPLATE.md tells
    authors to say "do not re-aggregate" - that key used to route around the ``usr`` branch
    entirely. The rest carry no aggregation and were mis-derived by role: the strings to
    ``none:``, the numeric one to the measure default ``sum:``.
    """
    element = _worksheet_element(
        "Ratio Card", ET.fromstring(_transitive_aggregate_xml())
    )
    column_instance = element.find(
        f"table/view/datasource-dependencies/column-instance[@column='[{field}]']"
    )
    assert column_instance is not None, f"no column-instance emitted for [{field}]"
    assert column_instance.get("derivation") == "User"
    assert column_instance.get("name") == instance

    # Issue #71: the *column* the 'usr:' instance derives from has to agree with it. A
    # role='dimension' column under a derivation='User' instance says "group by this" over a
    # field with no row-level value - the shape behind "cannot mix aggregate and
    # non-aggregate arguments". Desktop writes role='measure' for a string aggregate calc
    # (see the 'Color * Delta' calcs in the tracked reference workbook) and leaves 'type'
    # following the datatype, so the string ones stay nominal.
    column = element.find(
        f"table/view/datasource-dependencies/column[@name='[{field}]']"
    )
    assert column is not None, f"no column emitted for [{field}]"
    assert column.get("role") == "measure"
    assert column.get("type") == column_type


@pytest.mark.parametrize(
    "field", ["Ratio", "Ratio Label", "Ratio Dir", "Ratio Scaled", "Ratio Badge"]
)
def test_no_re_aggregating_instance_of_an_aggregate_calc_is_emitted_anywhere(field):
    """The wrong instance in *any* corner of the worksheet - a shelf, an encoding, a tooltip,
    a style rule - is one red pill, so the whole sheet is swept rather than the shelves."""
    sheet_xml = _worksheet_xml("Ratio Card", _transitive_aggregate_xml())
    for prefix in ("none", "sum", "avg", "attr"):
        assert f"[{prefix}:{field}:" not in sheet_xml, (
            f"[{field}] reaches Tableau as a '{prefix}:' instance; an aggregate calc has no "
            f"row-level value to derive one from"
        )


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


def test_every_chart_type_gets_a_dashboard_zone_and_a_hidden_tab():
    """Tableau renders no tab for hidden='true', so a sheet may only be hidden once a zone
    shows it. With the zone tree in place, every one of the 15 sheets is embedded - so every
    one is hidden, and the analyst opens on the dashboard rather than on a wall of tabs."""
    hidden = {
        window.get("name")
        for window in ALL_CHARTS_ROOT.find("windows")
        if window.get("class") == "worksheet" and window.get("hidden") == "true"
    }
    embedded = {
        zone.get("name")
        for zone in ALL_CHARTS_ROOT.findall("dashboards/dashboard/zones//zone")
        if zone.get("name")
    }
    sheets = {sheet.get("name") for sheet in ALL_CHARTS_ROOT.findall("worksheets/worksheet")}

    assert embedded == sheets == hidden


def test_a_sorted_workbook_carries_the_sort_format_flag():
    """WORKSHEETS.md:512 - a workbook holding a sorted worksheet adds <SortTagCleanup/> to
    the format-change manifest, and Tableau writes those flags alphabetically."""
    flags = [
        element.tag
        for element in ALL_CHARTS_ROOT.find("document-format-change-manifest")
    ]
    assert "SortTagCleanup" in flags
    assert flags == sorted(flags)

    unsorted_document = _manifest([_sheet(
        "Bar", "bar", shelves={"columns": ["region"], "rows": ["revenue"]},
    )])
    unsorted_root = ET.fromstring(_render(unsorted_document))
    unsorted_flags = [
        element.tag for element in unsorted_root.find("document-format-change-manifest")
    ]
    assert "SortTagCleanup" not in unsorted_flags


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
    assert element.find("table/view/sort") is None
    manual = element.find("table/view/manual-sort")
    assert manual.get("column").endswith("[none:region:nk]")
    assert manual.get("direction") == "ASC"
    buckets = manual.findall("dictionary/bucket")
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
    # Label and value share a line: no break between them, one break between pairs.
    assert runs[0].text == "Category: "
    assert "[attr:product_category:nk]" in runs[1].text
    assert runs[2].text == worksheet.TOOLTIP_BREAK
    assert runs[3].text == "Revenue: "
    # The value runs must be CDATA - entity-escaped, Tableau prints the reference verbatim.
    assert "<![CDATA[<[federated" in _worksheet_xml("Custom Tooltip")


def test_a_bare_dimension_on_tooltip_is_wrapped_in_attr():
    """Desktop-reported (#39): a dimension on Tooltip must reach the pane as ATTR().

    The Tooltip shelf does not add to the view's level of detail, so a field on it has to
    resolve to one value per mark. Tableau wraps a dimension in ``ATTR()`` and refuses the
    un-aggregated pill outright: *"The field Region can't be displayed in Tooltips because
    it can't be converted to a measure using ATTR()."* The manifest must not have to know
    that - ``{"label": "Region", "field": "region"}`` is a legal entry.
    """
    document = _manifest([_sheet(
        "Bare Tooltip", "bar",
        shelves={"columns": ["region"], "rows": ["revenue"]},
        tooltip=[{"label": "Region", "field": "region"}],
    )])
    pane = _worksheet_element(
        "Bare Tooltip", ET.fromstring(_render(document))
    ).find("table/panes/pane")

    registered = {
        tooltip.get("column").rsplit(".", 1)[-1]
        for tooltip in pane.findall("encodings/tooltip")
    }
    assert registered == {"[attr:region:nk]"}
    assert "[attr:region:nk]" in pane.find("customized-tooltip/formatted-text/run[2]").text


def test_an_explicit_tooltip_aggregation_is_left_alone():
    """The ATTR wrap is a default, not an override: an asked-for aggregation still wins."""
    document = _manifest([_sheet(
        "Counted Tooltip", "bar",
        shelves={"columns": ["region"], "rows": ["revenue"]},
        tooltip=[{"label": "Orders", "field": "region", "aggregation": "countd"}],
    )])
    pane = _worksheet_element(
        "Counted Tooltip", ET.fromstring(_render(document))
    ).find("table/panes/pane")

    assert {
        tooltip.get("column").rsplit(".", 1)[-1]
        for tooltip in pane.findall("encodings/tooltip")
    } == {"[ctd:region:qk]"}


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
    """No branding step means Tableau's own defaults - not a made-up font or colour. The
    worksheet rule still carries the field-label switches, which are layout, not branding."""
    root = ET.fromstring(_render(tokens=""))
    element = _worksheet_element("Bar", root)
    assert element.find("layout-options") is None
    rule = element.find("table/style/style-rule[@element='worksheet']")
    assert [format.get("attr") for format in rule] == ["display-field-labels"] * 2


def test_field_labels_are_off_on_every_sheet():
    """A field label repeats the zone's header and costs the chart a whole band of the
    sheet - and on a crosstab it is the band the analyst notices."""
    for element in ALL_CHARTS_ROOT.findall("worksheets/worksheet"):
        rule = element.find("table/style/style-rule[@element='worksheet']")
        scopes = {
            format.get("scope") for format in rule.findall("format")
            if format.get("attr") == "display-field-labels" and format.get("value") == "false"
        }
        assert scopes == {"cols", "rows"}, element.get("name")


def test_an_unfilled_token_template_is_ignored():
    """A DESIGN-TOKENS.md still carrying '[font]' placeholders must not style anything
    with the literal placeholder text."""
    tokens = worksheet.parse_design_tokens(
        "## Typography\n\n- **Font family**: [font]\n- **Chart title**: [size]px\n"
    )
    assert tokens.font_family == worksheet.DEFAULT_FONT


def test_a_prose_annotated_font_family_is_sanitized(caplog):
    """Issue #66: tableau-brand wrote 'Tableau (Medium / Light - native, no webfont)' into the
    token value. Taken verbatim it is not a font Windows can resolve, so Desktop silently
    falls back on every run. Stripping the annotation leaves a bare 'Tableau', which is not a
    family either, so the named default weight is what gets emitted."""
    with caplog.at_level("WARNING"):
        tokens = worksheet.parse_design_tokens(
            "## Typography\n\n"
            "- **Font family**: Tableau (Medium / Light — native, no webfont)\n"
        )

    assert tokens.font_family == worksheet.DEFAULT_FONT
    assert "[WARN]" in caplog.text


def test_a_bare_tableau_is_not_a_family(caplog):
    """Tableau ships six *families* - Bold, Book, Light, Medium, Regular, Semibold - and no
    font called plain 'Tableau'. The weight is part of the name, so a bare 'Tableau' resolves
    to nothing (verified against Desktop's own output in
    skill/tableau-dashboard-creator/examples/top-level-workbook-example.twb, which carries
    fontname='Tableau Medium' and fontname='Tableau Light')."""
    with caplog.at_level("WARNING"):
        tokens = worksheet.parse_design_tokens("## Typography\n\n- **Font family**: Tableau\n")

    assert tokens.font_family == worksheet.DEFAULT_FONT
    assert "[WARN]" in caplog.text


def test_a_real_tableau_family_passes(caplog):
    """The weight-bearing families are exactly what Desktop writes - never rewrite one."""
    for family in worksheet.TABLEAU_FONTS:
        with caplog.at_level("WARNING"):
            tokens = worksheet.parse_design_tokens(
                f"## Typography\n\n- **Font family**: {family}\n"
            )

        assert tokens.font_family == family
        assert caplog.text == ""


def test_an_unresolvable_font_family_falls_back(caplog):
    """Nothing a font can be named survives the strip, so emitting it would hand Desktop a
    fontname it cannot resolve - Tableau's own default is the honest answer, loudly."""
    with caplog.at_level("WARNING"):
        tokens = worksheet.parse_design_tokens(
            "## Typography\n\n- **Font family**: Helvetica / Arial — whichever\n"
        )

    assert tokens.font_family == worksheet.DEFAULT_FONT
    assert "[WARN]" in caplog.text


def test_a_clean_font_family_is_left_alone(caplog):
    """The sanitizer must be silent on the normal case, or the [WARN] means nothing."""
    with caplog.at_level("WARNING"):
        tokens = worksheet.parse_design_tokens(
            "## Typography\n\n- **Font family**: Segoe UI\n"
        )

    assert tokens.font_family == "Segoe UI"
    assert caplog.text == ""


def test_style_rules_are_alphabetical_by_element():
    """Tableau Desktop rewrites them alphabetically on save; emitting any other order
    produces a diff the analyst did not make."""
    for element in ALL_CHARTS_ROOT.findall("worksheets/worksheet"):
        for style in element.findall(".//style"):
            elements = [rule.get("element") for rule in style.findall("style-rule")]
            assert elements == sorted(elements), element.get("name")


# --- Worksheet formatting (issue #44) ------------------------------------------

def _mark_encoding(sheet_name: str) -> ET.Element:
    """Return a sheet's worksheet-level mark colour encoding (its palette)."""
    return _worksheet_element(sheet_name).find(
        "table/style/style-rule[@element='mark']/encoding"
    )


def test_the_brand_palette_needs_no_member_values():
    """The decision this ticket asked for: an inline <color-palette> orders the brand's hexes
    and Tableau walks the domain against them, so a palette needs no data members at all."""
    encoding = _mark_encoding("Bar Stacked")

    assert encoding.get("attr") == "color"
    assert encoding.get("type") == "palette"
    assert encoding.get("field").endswith("[none:region:nk]")
    palette = encoding.find("color-palette")
    assert palette.get("type") == "regular"
    assert [color.text for color in palette] == [
        "#1b4f72", "#2e86c1", "#48c9b0", "#f39c12",
    ]
    # Nothing binds a member: the encoding carries the palette and no <map to=.../> at all.
    assert encoding.find("map") is None


def test_a_measure_on_colour_gets_a_two_ended_ramp():
    """A continuous field cannot take a categorical palette - a ramp has a low and a high."""
    encoding = _mark_encoding("Map")

    assert encoding.get("type") == "interpolated"
    palette = encoding.find("color-palette")
    assert palette.get("type") == "ordered-sequential"
    assert [color.text for color in palette] == ["#1b4f72", "#f39c12"]


@pytest.mark.parametrize("sheet_name", ["Dual Axis", "Combo"])
def test_dual_charts_colour_their_measure_names_from_the_brand(sheet_name):
    """Their colour field is the built-in Measure Names, which is still a dimension."""
    encoding = _mark_encoding(sheet_name)
    assert encoding.get("field").endswith("[:Measure Names]")
    assert encoding.get("type") == "palette"


def test_an_uncoloured_chart_gets_the_brand_colour_and_no_palette():
    """A plain bar has no domain to walk, so a palette would style nothing - but its marks
    still have a colour, and Tableau's default blue is what makes a chart look generated."""
    assert _mark_encoding("Bar") is None

    rule = _worksheet_element("Bar").find("table/style/style-rule[@element='mark']")
    assert [(fmt.get("attr"), fmt.get("value")) for fmt in rule.findall("format")] == [
        ("mark-color", "#1b4f72"),
    ]


def test_no_series_colours_means_tableau_default_10():
    """Tokens that carry only typography must not invent a palette."""
    root = ET.fromstring(_render(tokens="## Typography\n\n- **Font family**: Open Sans\n"))
    assert _worksheet_element("Bar Stacked", root).find(
        "table/style/style-rule[@element='mark']"
    ) is None


# --- Per-field sub-palettes (issue #67) ----------------------------------------

#: A tokens file whose ``### Chart series colors`` section carries one table per coloured
#: field - the shape tableau-brand writes for a dashboard that colours by more than one
#: dimension. Order-walking the *concatenation* of these tables hands every encoding the
#: first table's colours, which is issue #67.
PER_FIELD_TOKENS = """# Design Tokens

## Typography

- **Font family**: Open Sans

## Colors

### Chart series colors

**`region`** - the primary split, ordered by revenue rank.

| Member | Token | Hex |
|--------|-------|-----|
| West | Purple 700 | `#6941C6` |
| East | Purple 500 | `#9E77ED` |

**`product_category`**

| Member | Token | Hex |
|--------|-------|-----|
| Technology | Indigo 700 | `#3538CD` |
| Furniture | Indigo 500 | `#6172F3` |
"""

#: One sheet per colour field, plus one coloured by a field no table names.
PER_FIELD_SHEETS = [
    _sheet("By Region", "bar", shelves={"columns": ["region"], "rows": ["revenue"]},
           encodings={"color": "region"}),
    _sheet("By Category", "bar", shelves={"columns": ["region"], "rows": ["revenue"]},
           encodings={"color": "product_category"}),
    _sheet("By Country", "bar", shelves={"columns": ["region"], "rows": ["revenue"]},
           encodings={"color": "country"}),
]


def _palette_of(sheet_name: str, root: ET.Element) -> list[str]:
    """Return the colours of a sheet's mark colour encoding, in order."""
    encoding = _worksheet_element(sheet_name, root).find(
        "table/style/style-rule[@element='mark']/encoding"
    )
    return [color.text for color in encoding.find("color-palette")]


def test_a_named_series_table_binds_to_its_own_field():
    """Issue #67: with a table per field, each colour encoding walks *its* table. Handing
    both sheets the concatenation coloured 'By Category' with the region purples."""
    root = ET.fromstring(_render(_manifest(PER_FIELD_SHEETS), tokens=PER_FIELD_TOKENS))

    assert _palette_of("By Region", root) == ["#6941c6", "#9e77ed"]
    assert _palette_of("By Category", root) == ["#3538cd", "#6172f3"]


def test_an_unnamed_colour_field_falls_back_to_the_whole_list():
    """No table names 'country', so the brand's full ordered list is still the best guess -
    which is also what keeps a single-table tokens file rendering exactly as before."""
    root = ET.fromstring(_render(_manifest(PER_FIELD_SHEETS), tokens=PER_FIELD_TOKENS))

    assert _palette_of("By Country", root) == [
        "#6941c6", "#9e77ed", "#3538cd", "#6172f3",
    ]


def test_the_palette_key_binds_a_table_the_encoding_field_does_not_name():
    """The escape hatch: a calculated encoding field ('Package Bucket') rarely carries the
    name of the tokens table that colours it, so the manifest says which one."""
    sheets = [_sheet(
        "By Country", "bar", shelves={"columns": ["region"], "rows": ["revenue"]},
        encodings={"color": "country"}, palette="product_category",
    )]
    root = ET.fromstring(_render(_manifest(sheets), tokens=PER_FIELD_TOKENS))

    assert _palette_of("By Country", root) == ["#3538cd", "#6172f3"]


def test_a_palette_name_no_tokens_table_carries_is_rejected():
    """A typo'd name would silently fall back to the flat list - the symptom #67 is about."""
    document = _manifest([_sheet(
        "By Country", "bar", shelves={"columns": ["region"], "rows": ["revenue"]},
        encodings={"color": "country"}, palette="no_such_table",
    )])
    errors = manifest.validate_manifest(
        document, DATA_MODEL, TARGET_VERSION, PER_FIELD_TOKENS
    )
    assert any("palette 'no_such_table'" in error for error in errors)


def test_the_palette_key_validates_against_the_tokens_file():
    """The same manifest with a name the tokens file does carry is buildable."""
    document = _manifest([_sheet(
        "By Country", "bar", shelves={"columns": ["region"], "rows": ["revenue"]},
        encodings={"color": "country"}, palette="product_category",
    )])
    assert manifest.validate_manifest(
        document, DATA_MODEL, TARGET_VERSION, PER_FIELD_TOKENS
    ) == []


def test_a_single_table_tokens_file_names_no_sub_palette():
    """The demo's prose-and-commas section has no per-field tables, so nothing changes."""
    tokens = worksheet.parse_design_tokens(DESIGN_TOKENS)

    assert tokens.field_palettes == {}
    assert tokens.series_colors == ("#1b4f72", "#2e86c1", "#48c9b0", "#f39c12")


def test_a_measure_on_text_labels_the_marks_of_any_chart_type():
    """A bar with SUM on Text is labelled bars - and the labels are what make its
    number_format visible, which is why a styled bar used to look identical to a plain one."""
    element = _worksheet_element("Bar Labelled")
    rule = element.find("table/panes/pane/style/style-rule[@element='mark']")
    shown = {fmt.get("attr"): fmt.get("value") for fmt in rule.findall("format")}

    assert shown["mark-labels-show"] == "true"
    assert element.find("table/panes/pane/encodings/text") is not None
    cell = element.find("table/style/style-rule[@element='cell']/format")
    assert cell.get("attr") == "text-format"
    assert cell.get("value") == "$#,##0"


def test_borders_lines_shading_and_alignment_come_off_the_manifest():
    """The four Desktop format panes, each a style rule the manifest asked for."""
    element = _worksheet_element("Bar Formatted")
    formats = {
        (rule.get("element"), fmt.get("attr")): fmt.get("value")
        for rule in element.findall("table/style/style-rule")
        for fmt in rule.findall("format")
    }

    assert formats[("pane", "background-color")] == "#ffffff"
    assert formats[("cell", "border-style")] == "none"
    assert formats[("cell", "border-width")] == "0"
    assert formats[("gridline", "stroke-color")] == "#e5e8e8"
    assert formats[("zeroline", "display")] == "false"
    assert formats[("cell", "text-align")] == "center"
    assert formats[("cell", "vertical-align")] == "bottom"


def test_a_colour_border_gets_a_width_and_a_style():
    """'borders: #hex' has to say how wide and how solid, or Tableau draws nothing."""
    document = _manifest([_sheet(
        "Bordered", "bar",
        shelves={"columns": ["region"], "rows": ["revenue"]},
        format={"borders": "#DDDDDD"},
    )])
    element = _worksheet_element("Bordered", ET.fromstring(_render(document)))
    formats = {
        fmt.get("attr"): fmt.get("value")
        for fmt in element.findall("table/style/style-rule[@element='cell']/format")
    }

    assert formats == {
        "border-color": "#dddddd", "border-style": "solid", "border-width": "1",
    }


def test_a_kpi_card_still_centres_itself_and_an_explicit_align_wins():
    """Centring is the KPI treatment, not a default worth losing - but the analyst overrules."""
    kpi_formats = {
        fmt.get("attr"): fmt.get("value")
        for fmt in _worksheet_element("Kpi Card").findall(
            "table/style/style-rule[@element='cell']/format"
        )
    }
    assert kpi_formats == {"text-align": "center", "vertical-align": "center"}

    document = _manifest([_sheet("Left KPI", "text", encodings={"text": "revenue"},
                                 format={"align": "left"})])
    element = _worksheet_element("Left KPI", ET.fromstring(_render(document)))
    overruled = {
        fmt.get("attr"): fmt.get("value")
        for fmt in element.findall("table/style/style-rule[@element='cell']/format")
    }
    assert overruled == {"text-align": "left", "vertical-align": "center"}


def test_every_sheet_fits_its_zone_and_a_text_table_still_scrolls():
    """Entire View by default (a zone is a fixed box), Standard where the sheet is meant to
    scroll, and the manifest overrides either."""
    zooms = {}
    for window in ALL_CHARTS_ROOT.findall("windows/window"):
        if window.get("class") != "worksheet":
            continue
        zoom = window.find("viewpoint/zoom")
        zooms[window.get("name")] = None if zoom is None else zoom.get("type")

    assert zooms["Bar"] == "entire-view"
    assert zooms["Text Table"] is None, "a text table keeps Standard fit"
    assert zooms["Bar Formatted"] == "fit-width", "the manifest's own fit"

    # The dashboard's copy of each sheet fits the same way.
    dashboard = {
        viewpoint.get("name"): viewpoint.find("zoom")
        for viewpoint in ALL_CHARTS_ROOT.findall("windows/window/viewpoints/viewpoint")
    }
    assert dashboard["Bar"].get("type") == "entire-view"
    assert dashboard["Text Table"] is None
    assert dashboard["Bar Formatted"].get("type") == "fit-width"


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


def test_an_unknown_encoding_name_is_rejected():
    """The builder only emits ENCODING_ORDER, so a typo'd 'colour' would vanish - the field
    behind it validates fine, which is exactly what makes the name worth checking."""
    document = _manifest([_sheet(
        "Typo", "bar",
        shelves={"columns": ["region"], "rows": ["revenue"]},
        encodings={"colour": "product_category"},
    )])
    errors = manifest.validate_manifest(document, DATA_MODEL, TARGET_VERSION)
    assert any("unknown encoding 'colour'" in error for error in errors)


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
