"""Contract test for tableau-build's interactive layer (issue #37, CONTRACT.md step 8, §6).

A dashboard that renders but does nothing when you click it is the failure this file exists to
prevent. Four groups of guarantees, one test per guarantee:

* **datasource level** - a calculated field carries its ``default-format``, an aggregate
  formula is *not* re-aggregated while an LOD *is*, a table calc lives on the column-instance,
  a reference line sits in the pane, and every parameter is a column of the inline
  ``Parameters`` datasource;
* **workbook level** - filter / highlight / parameter actions in ``<actions>``, bound to real
  sheet names, one action per target, with deterministic unique ids;
* **dashboard level** - a quick-filter card and a parameter control render as real zones, the
  dashboard declares the datasources and fields they read, and the filtered column reaches the
  owning worksheet's ``<slices>``;
* **Dynamic Zone Visibility** - a ``visibility`` field becomes a ``<datagraph>`` wired to the
  controlled zone, plus the four document-format flags that go with it.

Everything is driven through :func:`twb.render_workbook` (pure), with one end-to-end
:func:`build.build_workbook` case so the whole path - manifest on disk to packaged ``.twbx`` -
is covered too.
"""

import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

import build
import features
import init
import manifest
import twb
import worksheet

TARGET_VERSION = "2024.2-2025.x"

DATA_MODEL = """# Data Model

## Acquisition

- tier: csv (provided in data/)

## Data source: `sales_orders.csv`

| Field | Type | Role | Sample values | Description |
|-------|------|------|---------------|-------------|
| order_date | date | dimension | 2024-01-05 | Order date |
| region | string | dimension | West | Sales region |
| product_category | string | dimension | Technology | Product category |
| revenue | real | measure | 1200.5 | Order revenue |
| profit | real | measure | 120.5 | Order profit |
"""

CSV_HEADER = ["order_date", "region", "product_category", "revenue", "profit"]
CSV_HEADERS = {"sales_orders.csv": CSV_HEADER}

#: One CSV datasource, three calculated fields (aggregate / LOD / boolean-for-DZV) and two
#: parameters (a member list and a range) - the datasource-level surface of the ticket.
DATASOURCES = [{
    "name": "sales_orders",
    "csv": "sales_orders.csv",
    "fields": [{"name": name, "type": datatype} for name, datatype in (
        ("order_date", "date"), ("region", "string"), ("product_category", "string"),
        ("revenue", "real"), ("profit", "real"),
    )],
}]

CALCULATED_FIELDS = [
    {"name": "Margin Pct", "formula": "SUM([profit]) / SUM([revenue])",
     "datasource": "sales_orders", "type": "real", "format": "p0.0%"},
    {"name": "Regional Revenue", "formula": "{FIXED [region]: SUM([revenue])}",
     "datasource": "sales_orders", "type": "real"},
    # The DZV switch: no region picked yet means there is nothing to break down, so the
    # category panel stays hidden until the parameter action below writes a region.
    {"name": "Show Breakdown", "formula": '[Parameters].[Selected Region] <> "All"',
     "datasource": "sales_orders", "type": "boolean"},
]

PARAMETERS = [
    {"name": "Selected Region", "data_type": "string", "current_value": "All",
     "values": ["All", "East", "North", "West"]},
    {"name": "Top N", "data_type": "integer", "current_value": 10,
     "range": {"min": 5, "max": 50, "step": 5}, "format": "#,##0"},
]

WORKSHEETS = [
    {"name": "Revenue KPI", "element_id": "kpi-revenue", "chart_type": "text",
     "datasource": "sales_orders", "encodings": {"text": "Margin Pct"}},
    {"name": "Revenue Trend", "element_id": "chart-trend", "chart_type": "line",
     "datasource": "sales_orders",
     "shelves": {"columns": [{"field": "order_date", "date_part": "month"}],
                 "rows": ["revenue"]},
     "reference_lines": [{"field": "revenue", "aggregation": "sum", "formula": "average",
                          "scope": "per-table", "label": "<Computation>: <Value>"}]},
    {"name": "Detail Table", "element_id": "chart-detail", "chart_type": "table",
     "datasource": "sales_orders",
     "shelves": {"rows": ["region"],
                 "columns": [{"field": "revenue", "aggregation": "sum",
                              "table_calc": "PctTotal"}]},
     "encodings": {"text": "Regional Revenue"}},
    {"name": "Category Mix", "element_id": "chart-category", "chart_type": "bar",
     "datasource": "sales_orders",
     "shelves": {"rows": ["product_category"],
                 "columns": [{"field": "revenue", "aggregation": "sum"}]}},
]

OBJECTS = [
    {"element_id": "flt-region", "kind": "filter", "field": "region",
     "worksheet": "Revenue Trend"},
    # 'Selected Region' gets no control: the parameter action sets it and clearing the
    # selection resets it, so a control would only be a second way to break the chain.
    {"element_id": "prm-topn", "kind": "parameter", "parameter": "Top N"},
]

#: One coherent interaction chain: click a point on the trend to filter and highlight the
#: detail table, then click a region there to write it into 'Selected Region' - which reveals
#: the category panel (DZV) *and* filters it to that region.
ACTIONS = [
    {"name": "Trend cross-filter", "type": "filter", "source": "chart-trend",
     "targets": ["chart-detail"]},
    {"name": "Highlight region", "type": "highlight", "source": "chart-trend",
     "targets": ["chart-detail"], "run_on": "hover"},
    {"name": "Breakdown filter", "type": "filter", "source": "chart-detail",
     "targets": ["chart-category"]},
    {"name": "Pick region", "type": "parameter", "source": "chart-detail",
     "targets": ["Selected Region"], "field": "region"},
]

LAYOUT = {
    "canvas": {"width": 1366, "height": 768},
    "root": {"type": "vert", "children": [
        {"type": "horz", "size": 12, "children": [
            {"id": "flt-region", "size": 50},
            {"id": "prm-topn", "size": 50},
        ]},
        {"id": "kpi-revenue", "size": 13},
        {"id": "chart-trend", "size": 30},
        {"id": "chart-detail", "size": 25},
        # The last child is shown or hidden by the boolean calc - Dynamic Zone Visibility.
        {"id": "chart-category", "size": 20, "visibility": "Show Breakdown"},
    ]},
}


def _manifest(**overrides) -> dict:
    """The features-rich manifest every test below reads, with optional overrides."""
    document = {
        "target_tableau_version": TARGET_VERSION,
        "datasources": DATASOURCES,
        "calculated_fields": CALCULATED_FIELDS,
        "parameters": PARAMETERS,
        "worksheets": WORKSHEETS,
        "objects": OBJECTS,
        "actions": ACTIONS,
        "layout": LAYOUT,
    }
    document.update(overrides)
    return document


def _render(document=None) -> str:
    """Render a manifest to ``.twb`` XML."""
    return twb.render_workbook(
        document if document is not None else _manifest(), DATA_MODEL, CSV_HEADERS
    )


def _errors(**overrides) -> list[str]:
    """Validate a manifest against the data model and return the messages."""
    return manifest.validate_manifest(_manifest(**overrides), DATA_MODEL, TARGET_VERSION)


FEATURES_XML = _render()
FEATURES_ROOT = ET.fromstring(FEATURES_XML)

DATASOURCE_ID = twb.datasource_id("sales_orders")


def _worksheet_element(name: str, root: ET.Element = FEATURES_ROOT) -> ET.Element:
    """Return the ``<worksheet>`` with the given name."""
    for element in root.findall("worksheets/worksheet"):
        if element.get("name") == name:
            return element
    raise AssertionError(f"no worksheet named {name!r} in the rendered workbook")


def _zone(friendly_name: str, root: ET.Element = FEATURES_ROOT) -> ET.Element:
    """Return the dashboard zone with the given friendly name."""
    for zone in root.find("dashboards/dashboard/zones").iter("zone"):
        if zone.get("friendly-name") == friendly_name:
            return zone
    raise AssertionError(f"no zone named {friendly_name!r} in the dashboard")


def test_the_features_manifest_validates():
    """The schema accepts every new key, so a failure below is the assembler's."""
    assert _errors() == []


def test_the_same_manifest_rebuilds_byte_identical():
    """Action names and datagraph guids are hash-derived, so a re-run is diff-free."""
    assert _render() == FEATURES_XML


# --- Calculated fields, LOD, formats (AC #1) ----------------------------------

def test_a_calculated_fields_format_lands_on_its_column():
    """A ratio without a format renders as 0.0834; the pattern belongs to the field, so it
    is on the column rather than in one sheet's style rules."""
    column = FEATURES_ROOT.find(
        f"datasources/datasource[@name='{DATASOURCE_ID}']/column[@name='[Margin Pct]']"
    )

    assert column.get("default-format") == "p0.0%"
    assert column.findtext(".") is not None  # the column exists with its calculation
    assert column.find("calculation").get("formula") == "SUM([profit]) / SUM([revenue])"


def test_a_calculated_field_without_a_format_carries_none():
    """No format asked for, no attribute invented."""
    column = FEATURES_ROOT.find(
        f"datasources/datasource[@name='{DATASOURCE_ID}']/column[@name='[Regional Revenue]']"
    )

    assert "default-format" not in column.attrib


def test_each_calculated_field_is_declared_exactly_once_per_datasource():
    """A field declared twice is a field Tableau resolves twice - and renames on save."""
    columns = FEATURES_ROOT.findall(
        f"datasources/datasource[@name='{DATASOURCE_ID}']/column"
    )
    names = [column.get("name") for column in columns]

    for name in ("[Margin Pct]", "[Regional Revenue]", "[Show Breakdown]"):
        assert names.count(name) == 1, f"{name} declared {names.count(name)} times"


@pytest.mark.parametrize("formula, aggregates", [
    ("SUM([profit]) / SUM([revenue])", True),
    ("AVG([revenue])", True),
    ("[quantity] * [unit_price]", False),
    ("{FIXED [region]: SUM([revenue])}", False),
    ("{ INCLUDE [region] : AVG([revenue]) }", False),
    ("{FIXED [region]: SUM([revenue])} / 2", False),
    ("SUM([revenue]) / {FIXED : SUM([revenue])}", True),
    ("{FIXED : SUM([revenue])} - SUM([cost])", True),
    ("{FIXED [a]: MAX({FIXED [b]: SUM([revenue])})}", False),
    ("", False),
])
def test_is_aggregate_formula_reads_lods_as_row_level(formula, aggregates):
    """An LOD contains SUM( but produces a row-level value, so it must be re-aggregated on
    the shelf. Matching the SUM inside it is what put the User derivation on an LOD - but an
    aggregate *outside* the braces still aggregates, so only the braced span is discounted."""
    assert worksheet.is_aggregate_formula(formula) is aggregates


def test_an_aggregate_calculation_reaches_the_shelf_un_aggregated():
    """SUM(SUM(profit)/SUM(revenue)) is an error Tableau refuses at load."""
    instance = _worksheet_element("Revenue KPI").find(
        "table/view/datasource-dependencies/column-instance[@column='[Margin Pct]']"
    )

    assert instance.get("derivation") == "User"
    assert instance.get("name") == "[usr:Margin Pct:qk]"


def test_an_lod_expression_is_re_aggregated_on_the_shelf():
    """The bug this ticket fixes: an LOD is one value per its own grain, so the view sums it."""
    instance = _worksheet_element("Detail Table").find(
        "table/view/datasource-dependencies/column-instance[@column='[Regional Revenue]']"
    )

    assert instance.get("derivation") == "Sum"
    assert instance.get("name") == "[sum:Regional Revenue:qk]"


# --- Table calculations (AC #1) ------------------------------------------------

def test_a_table_calc_lives_on_the_column_instance():
    """The calc is a property of the instance: the same measure can be plain on one shelf and
    a percent-of-total on another, so it introduces no column of its own."""
    dependencies = _worksheet_element("Detail Table").find(
        "table/view/datasource-dependencies"
    )
    instance = dependencies.find("column-instance[@name='[pcto:sum:revenue:qk]']")

    # No 'aggregation': Desktop 2025.1 strips it, and the prefix is 'pcto' - both read off
    # a Desktop-saved copy of this very workbook, which had rewritten ours.
    assert instance.find("table-calc").attrib == {
        "ordering-type": "Rows", "type": "PctTotal",
    }
    assert dependencies.find("column[@name='[pcto:sum:revenue:qk]']") is None
    assert instance.get("column") == "[revenue]"


def test_a_table_calcs_shelf_points_at_the_calculated_instance():
    """A shelf naming the plain instance would draw the raw measure, not the running one."""
    columns = _worksheet_element("Detail Table").findtext("table/cols")

    assert columns == f"[{DATASOURCE_ID}].[pcto:sum:revenue:qk]"


def test_the_datasource_also_declares_the_table_calcs_instance():
    """A table calc the datasource does not carry is a field Tableau drops from the data pane
    on save - the worksheet keeps working, the analyst's field list does not."""
    instance = FEATURES_ROOT.find(
        f"datasources/datasource[@name='{DATASOURCE_ID}']"
        f"/column-instance[@name='[pcto:sum:revenue:qk]']"
    )

    assert instance.find("table-calc").get("type") == "PctTotal"


def test_an_unknown_table_calc_is_rejected():
    """The XSD enumerates the types; 'CumAvg' is one the legacy docs invented."""
    sheets = json.loads(json.dumps(WORKSHEETS))
    sheets[2]["shelves"]["columns"][0]["table_calc"] = "CumAvg"

    assert any("CumAvg" in error for error in _errors(worksheets=sheets))


#: The Desktop-authored workbook that settles four of the eight prefixes - see
#: skills/tableau-build/references/snippets/worksheets/TABLE-CALCS.md.
_ATTESTATION_TWB = (
    Path(__file__).resolve().parent.parent
    / "skills/tableau-build/references/snippets/worksheets"
    / "table-calculations-attestation.twb"
)


def _attested_sheets():
    """dict: sheet name -> (``<table-calc>`` type or None, instance name, the <rows> shelf).

    Reads the vendored Desktop workbook. Sheet names are asserted here so a re-authored
    reference fails loudly instead of silently checking fewer types than it looks like.
    """
    workbook = ET.parse(_ATTESTATION_TWB).getroot()
    sheets = workbook.findall("worksheets/worksheet")
    assert [sheet.get("name") for sheet in sheets] == [
        "1a-total", "1b-running-total", "2-difference", "3-percent-from", "4-percentile",
        "5-moving-average",
    ], "the reference workbook changed - re-read TABLE-CALCS.md before touching this test"

    read = {}
    for sheet in sheets:
        instances = [
            instance
            for instance in sheet.findall("table/view/datasource-dependencies/column-instance")
            if instance.find("table-calc") is not None
        ]
        # One calculation per sheet is the whole design of the reference workbook; two would
        # mean the sheet no longer isolates a single type.
        assert len(instances) == 1, f"{sheet.get('name')} carries {len(instances)} table calcs"
        read[sheet.get("name")] = (
            instances[0].find("table-calc").get("type"),
            instances[0].get("name"),
            sheet.findtext("table/rows"),
        )
    return read


def _field_ref(table_calc, field_name="revenue"):
    """A minimal measure FieldRef carrying ``table_calc``, for naming assertions."""
    return worksheet.FieldRef(
        field_name=field_name, datatype="real", role="measure",
        column_type="quantitative", instance_type="quantitative",
        prefix="sum", derivation="Sum", table_calc=table_calc,
    )


# One row per TCType-ST value. Do NOT derive the expected prefix from TABLE_CALC_PREFIXES -
# a test that reads its expectation out of the table under test cannot fail, and a wrong
# prefix in that table is exactly the defect: four of the seven originally guessed were wrong
# ('pctdiff', 'pctval', 'pctrank', 'wnd'). Every row below is read off Desktop output.
@pytest.mark.parametrize("table_calc,prefix", [
    ("CumTotal", "cum"),        # attested: table-calculations-attestation.twb, 2025.1.10
    ("Difference", "diff"),     # attested: table-calculations-attestation.twb, 2025.1.10
    ("PctValue", "pcva"),       # attested: table-calculations-attestation.twb, 2025.1.10
    ("PctRank", "pcrk"),        # attested: table-calculations-attestation.twb, 2025.1.10
    ("PctDiff", "pcdf"),        # attested: appsfortableau HierarchyFilter demo, 2024.3.0
    ("PctTotal", "pcto"),       # attested: lavi_webpage_test.twbx, 2025.2.0
    ("Rank", "rank"),           # attested: Embedded Filters Test.twbx, 2024.2.10
    ("WindowTotal", "win"),     # attested: table-calculations-attestation.twb, 2025.1.10
])
def test_table_calc_instance_name_prefix(table_calc, prefix):
    """The prefix Tableau puts on the instance name, per calculation type.

    Rendered through :class:`worksheet.FieldRef` rather than asserted on the table so the
    nesting rule is covered too: the calc prefix goes *outside* the aggregation prefix.
    """
    assert _field_ref(table_calc).instance_name == f"[{prefix}:sum:revenue:qk]"


def test_the_attested_prefixes_match_desktops_own_output():
    """The attestation, enforced: rebuild each sheet's shelf and compare to what Desktop
    wrote.

    The parametrize list above is hand-transcribed, so it pins the table but not the truth -
    a typo there and in TABLE_CALC_PREFIXES would agree with each other. This reads the
    reference workbook instead, so the four types it covers cannot drift silently.
    """
    checked = dict(_attested_sheets())
    # '1a-total' is excluded on purpose - see
    # test_a_total_table_calc_is_a_calculated_field_with_no_type.
    checked.pop("1a-total")

    for sheet_name, (table_calc, instance_name, rows) in checked.items():
        # The field is Sales here, and <rows> qualifies the name with the datasource id, so
        # compare the bracketed tail rather than the whole shelf.
        expected = _field_ref(table_calc, "Sales").instance_name
        assert instance_name == expected, f"{sheet_name}: Desktop wrote {instance_name}"
        assert rows.endswith(expected), (
            f"{sheet_name}: Desktop wrote {rows}, we build {expected}"
        )

    assert {name: pair[:2] for name, pair in checked.items()} == {
        "1b-running-total": ("CumTotal", "[cum:sum:Sales:qk]"),
        "2-difference": ("Difference", "[diff:sum:Sales:qk]"),
        "3-percent-from": ("PctValue", "[pcva:sum:Sales:qk]"),
        "4-percentile": ("PctRank", "[pcrk:sum:Sales:qk]"),
        "5-moving-average": ("WindowTotal", "[win:sum:Sales:qk]"),
    }


def test_a_total_table_calc_is_a_calculated_field_with_no_type():
    """A formula-authored table calc writes no ``type`` and takes no prefix.

    Sheet '1a-total' is ``TOTAL(sum([Sales]))``. ``TOTAL`` *is* a table calculation - it is
    simply not offered by the Add Table Calculation dialog, so it is written as a formula, and
    the same is true of ``WINDOW_SUM`` and friends. On that path Desktop authors a
    **calculated field**: the ``<table-calc>`` carries addressing only, with no ``type``, and
    the instance keeps the ordinary ``usr`` prefix instead of gaining a table-calc one.

    So :data:`worksheet.TABLE_CALC_PREFIXES` governs the dialog-driven calculations only; a
    ``TOTAL(...)`` belongs in a manifest as a calculated field's ``formula``, not a
    ``table_calc``. This test is the guard on that boundary.
    """
    table_calc, instance_name, rows = _attested_sheets()["1a-total"]

    assert table_calc is None, f"Desktop grew a type for TOTAL(): {table_calc}"
    # Not WindowTotal in particular: that type exists, and Moving Calculation writes it.
    assert instance_name.startswith("[usr:"), instance_name
    assert rows.endswith(instance_name)
    # No prefix from our table appears in a name Desktop wrote for a window total.
    assert not any(
        instance_name.startswith(f"[{prefix}:")
        for prefix in worksheet.TABLE_CALC_PREFIXES.values()
    )


def test_the_prefix_table_holds_exactly_the_documented_types():
    """Adding a ninth type must not skip the attestation.

    The parametrize list above covers today's eight keys, but nothing makes a *new* key grow
    a row - it would ship un-pinned and un-attested. This is the guard that notices.
    """
    assert set(worksheet.TABLE_CALC_PREFIXES) == {
        "CumTotal", "WindowTotal", "Difference", "PctDiff",
        "PctValue", "PctTotal", "Rank", "PctRank",
    }


# --- Reference lines (AC #1) ---------------------------------------------------

def test_a_reference_line_is_a_pane_child_with_qualified_columns():
    """The legacy FEATURES.md puts it in <view> with unqualified refs; the XSD and Tableau's
    own output both put it in <pane> with fully qualified ones."""
    pane = _worksheet_element("Revenue Trend").find("table/panes/pane")
    line = pane.find("reference-line")

    assert line is not None
    assert line.get("axis-column") == f"[{DATASOURCE_ID}].[sum:revenue:qk]"
    assert line.get("value-column") == line.get("axis-column")
    assert line.get("formula") == "average"
    assert line.get("scope") == "per-table"
    assert line.get("id") == "refline0"
    assert line.get("z-order") == "1"
    assert line.get("enable-instant-analytics") == "true"


def test_a_labelled_reference_line_is_label_type_custom():
    """A label template only renders when the line says its labelling is custom."""
    line = _worksheet_element("Revenue Trend").find("table/panes/pane/reference-line")

    assert line.get("label-type") == "custom"
    assert line.get("label") == "<Computation>: <Value>"


def test_a_reference_line_comes_after_the_encodings_in_its_pane():
    """PaneSpecification-G's sequence: a line before <encodings> fails the XSD."""
    pane = _worksheet_element("Revenue Trend").find("table/panes/pane")
    tags = [child.tag for child in pane]

    assert tags == ["view", "mark", "reference-line"] or tags.index(
        "reference-line"
    ) > tags.index("mark")


def test_a_reference_lines_field_is_declared_by_the_worksheet():
    """A line drawn against an instance the view never declares draws nothing."""
    dependencies = _worksheet_element("Revenue Trend").find(
        "table/view/datasource-dependencies"
    )

    assert dependencies.find("column-instance[@name='[sum:revenue:qk]']") is not None


@pytest.mark.parametrize("key, value", [
    ("formula", "mean"), ("scope", "per-sheet"), ("label_type", "shouty"),
])
def test_a_reference_line_outside_the_schemas_enums_is_rejected(key, value):
    """Each of the three is an XSD enumeration - a wrong value is a file Tableau refuses."""
    sheets = json.loads(json.dumps(WORKSHEETS))
    sheets[1]["reference_lines"][0][key] = value

    assert any(value in error for error in _errors(worksheets=sheets))


# --- Parameters (AC #1) --------------------------------------------------------

def test_the_parameters_datasource_is_inline_connectionless_and_last():
    """Tableau resolves [Parameters].[...] from a datasource that has no connection, after
    the real ones."""
    datasources = FEATURES_ROOT.findall("datasources/datasource")

    assert datasources[-1].get("name") == features.PARAMETERS_DATASOURCE
    assert datasources[-1].get("hasconnection") == "false"
    assert datasources[-1].get("inline") == "true"
    assert datasources[-1].find("aliases").get("enabled") == "yes"


def test_a_list_parameter_declares_its_members_as_literals():
    """A string member is quoted; an unquoted 'on' reads as a field reference."""
    column = FEATURES_ROOT.find(
        f"datasources/datasource[@name='{features.PARAMETERS_DATASOURCE}']"
        f"/column[@name='[Selected Region]']"
    )

    assert column.get("param-domain-type") == "list"
    assert column.get("value") == '"All"'
    assert column.find("calculation").get("formula") == '"All"'
    assert [member.get("value") for member in column.find("members")] == [
        '"All"', '"East"', '"North"', '"West"',
    ]


def test_a_range_parameter_declares_its_bounds_and_granularity():
    """Without a granularity the slider has no step, and Tableau rewrites the parameter."""
    column = FEATURES_ROOT.find(
        f"datasources/datasource[@name='{features.PARAMETERS_DATASOURCE}']"
        f"/column[@name='[Top N]']"
    )

    assert column.get("param-domain-type") == "range"
    assert column.get("default-format") == "#,##0"
    assert column.attrib["datatype"] == "integer"
    assert column.attrib["type"] == "quantitative"
    assert column.attrib["role"] == features.PARAMETER_ROLE
    assert column.find("range").attrib == {"granularity": "5", "max": "50", "min": "5"}


@pytest.mark.parametrize("value, data_type, literal", [
    ("on", "string", '"on"'),
    (10, "integer", "10"),
    (2.5, "real", "2.5"),
    (True, "boolean", "true"),
    ("2024-01-01", "date", "#2024-01-01#"),
])
def test_a_parameter_value_is_rendered_as_its_tableau_literal(value, data_type, literal):
    """Each datatype has its own delimiters; the wrong ones make the value unparseable."""
    assert features.parameter_literal(value, data_type) == literal


def test_a_worksheet_declares_the_parameter_its_calculation_reads():
    """A calculation reading a parameter the view does not declare is unresolvable, and
    Tableau opens the sheet with the field greyed out."""
    sheets = json.loads(json.dumps(WORKSHEETS))
    sheets[0]["encodings"] = {"text": "Show Breakdown"}
    view = _worksheet_element(
        "Revenue KPI", ET.fromstring(_render(_manifest(worksheets=sheets)))
    ).find("table/view")

    assert view.find(
        f"datasources/datasource[@name='{features.PARAMETERS_DATASOURCE}']"
    ) is not None
    declared = view.find(
        f"datasource-dependencies[@datasource='{features.PARAMETERS_DATASOURCE}']"
    )
    assert declared.find("column[@name='[Selected Region]']") is not None
    # No domain at the worksheet level: the parameter's members are the datasource's business.
    assert declared.find("column[@name='[Selected Region]']/members") is None


def test_the_datasource_declares_the_parameters_its_calculations_read():
    """Desktop adds this dependency on save: a calculation in the datasource reads
    [Parameters].[x], so the datasource declares the link - domain-less, between <layout> and
    <object-graph>."""
    datasource = FEATURES_ROOT.find(f"datasources/datasource[@name='{DATASOURCE_ID}']")
    dependencies = datasource.find(
        f"datasource-dependencies[@datasource='{features.PARAMETERS_DATASOURCE}']"
    )

    assert [column.get("name") for column in dependencies] == ["[Selected Region]"]
    assert dependencies.find("column/members") is None
    tags = [child.tag for child in datasource]
    assert tags.index("layout") < tags.index("datasource-dependencies") < \
        tags.index("object-graph")


def test_a_worksheet_that_reads_no_parameter_declares_none():
    """Every sheet declaring every parameter is noise Tableau rewrites on save."""
    view = _worksheet_element("Revenue Trend").find("table/view")

    assert view.find(
        f"datasources/datasource[@name='{features.PARAMETERS_DATASOURCE}']"
    ) is None
    assert view.find(
        f"datasource-dependencies[@datasource='{features.PARAMETERS_DATASOURCE}']"
    ) is None


def test_no_parameters_means_no_parameters_datasource():
    """An empty inline datasource is a workbook the schema refuses."""
    root = ET.fromstring(_render(_manifest(
        parameters=[], objects=[OBJECTS[0]], actions=ACTIONS[:2],
        layout=_layout_without_parameter_control(),
        calculated_fields=CALCULATED_FIELDS[:2],
        worksheets=_worksheets_without_dzv(),
    )))

    assert root.find(
        f"datasources/datasource[@name='{features.PARAMETERS_DATASOURCE}']"
    ) is None


def _layout_without_parameter_control() -> dict:
    """The layout with the parameter-control zone (and the DZV binding) dropped."""
    layout = json.loads(json.dumps(LAYOUT))
    layout["root"]["children"][0]["children"] = [{"id": "flt-region", "size": 100}]
    layout["root"]["children"][-1].pop("visibility")
    return layout


def _worksheets_without_dzv() -> list[dict]:
    """The worksheets, with nothing referencing the boolean DZV calculation."""
    return json.loads(json.dumps(WORKSHEETS))


@pytest.mark.parametrize("override, expected", [
    ({"name": "Bad", "data_type": "money", "current_value": 1}, "money"),
    ({"name": "Bad", "data_type": "string", "current_value": "x", "values": []}, "values"),
    ({"name": "Bad", "data_type": "integer", "current_value": 1, "range": {"min": 1}},
     "range"),
    ({"name": "Bad", "data_type": "integer", "current_value": 1, "values": [1],
      "range": {"min": 1, "max": 2}}, "both"),
    ({"name": "Bad", "data_type": "string", "values": ["x"]}, "current_value"),
])
def test_a_malformed_parameter_is_rejected(override, expected):
    """Each message names the parameter and what is wrong with it."""
    assert any(expected in error for error in _errors(parameters=[*PARAMETERS, override]))


def test_a_parameter_without_a_current_value_is_never_guessed_at():
    """The value *is* the column's calculation, so a guessed one silently changes what every
    calc reading the parameter computes. Validation demands it and the builder skips it."""
    entry = {"name": "Bad", "data_type": "string", "values": ["x"]}

    assert features.plan_parameters([entry]) == []


# --- Actions (AC #2) -----------------------------------------------------------

def test_actions_sit_between_the_datasources_and_the_worksheets():
    """WorkbookFile-CT's order: datasources, mapsources, actions, worksheets, dashboards."""
    tags = [child.tag for child in FEATURES_ROOT]

    assert tags.index("datasources") < tags.index("actions") < tags.index("worksheets")
    assert tags.index("worksheets") < tags.index("dashboards") < tags.index("windows")


def test_a_filter_action_targets_one_real_sheet_and_excludes_its_source():
    """Targeting the dashboard instead would cross-filter every sheet on it, including the
    ones the manifest never listed."""
    action = FEATURES_ROOT.find("actions/action[@caption='Trend cross-filter']")
    params = {param.get("name"): param.get("value") for param in action.iter("param")}

    assert action.find("command").get("command") == "tsc:tsl-filter"
    assert action.find("source").attrib == {
        "dashboard": twb.DASHBOARD_NAME, "type": "sheet", "worksheet": "Revenue Trend",
    }
    assert action.find("activation").attrib == {"auto-clear": "true", "type": "on-select"}
    assert params == {
        "exclude": "Revenue Trend", "special-fields": "all", "target": "Detail Table",
    }


def test_a_highlight_action_brushes_on_hover():
    """A highlight is tsc:brush over the shared field captions - a filter command would
    remove the unselected marks instead of dimming them."""
    action = FEATURES_ROOT.find("actions/action[@caption='Highlight region']")
    params = {param.get("name"): param.get("value") for param in action.iter("param")}

    assert action.find("command").get("command") == "tsc:brush"
    assert action.find("activation").get("type") == "on-hover"
    assert params == {
        "exclude": "Revenue Trend", "field-captions": "all", "target": "Detail Table",
    }


def test_a_parameter_action_writes_a_field_into_a_parameter():
    """The source field is a qualified column-instance and the target a qualified parameter;
    an unqualified either end is an action Tableau drops on load."""
    action = FEATURES_ROOT.find("actions/edit-parameter-action[@caption='Pick region']")
    params = {param.get("name"): param.get("value") for param in action.find("params")}

    assert action.find("source").get("worksheet") == "Detail Table"
    assert action.find("agg-type").get("type") == "attr"
    # Clearing the selection resets the parameter, so the panel it reveals hides itself again.
    # The value is Tableau's own serialization: the prefix token, then *undelimited* text.
    assert action.find("clear-option").attrib == {
        "type": "assign-fixed-value", "value": "s:LROOT:All",
    }
    assert params == {
        "source-field": f"[{DATASOURCE_ID}].[none:region:nk]",
        "target-parameter": "[Parameters].[Selected Region]",
    }


def test_a_parameter_actions_source_field_is_on_its_sheets_detail_shelf():
    """The action reads the field off the clicked mark, so it has to be *on* the source
    sheet's Detail shelf - <lod> in the pane encodings. Declaring it in the sheet's
    dependencies is not enough: Desktop 2025.1.10 opens the workbook and the action then
    silently never fires."""
    sheet = _worksheet_element("Detail Table")
    dependencies = sheet.find("table/view/datasource-dependencies")
    details = [
        element.get("column")
        for element in sheet.findall("table/panes/pane/encodings/lod")
    ]

    assert dependencies.find("column-instance[@name='[none:region:nk]']") is not None
    assert f"[{DATASOURCE_ID}].[none:region:nk]" in details


def test_parameter_actions_come_after_the_sheet_actions():
    """The XSD's <actions> sequence puts every <edit-parameter-action> last."""
    tags = [child.tag for child in FEATURES_ROOT.find("actions")]

    assert tags == ["action", "action", "action", "edit-parameter-action"]


def test_action_names_are_unique_and_deterministic():
    """Two actions sharing a name is a workbook Tableau repairs by dropping one."""
    names = [
        element.get("name") for element in FEATURES_ROOT.find("actions")
    ]

    assert len(set(names)) == len(names)
    assert names == [
        element.get("name")
        for element in ET.fromstring(_render()).find("actions")
    ]
    assert all(name.startswith("[Action") and name.endswith("]") for name in names)


def test_one_action_is_emitted_per_target():
    """A two-target cross-filter is two actions, each excluding the same source."""
    actions = json.loads(json.dumps(ACTIONS))
    actions[0]["targets"] = ["chart-detail", "kpi-revenue"]
    root = ET.fromstring(_render(_manifest(actions=actions)))

    emitted = root.findall("actions/action[@caption='Trend cross-filter']")
    targets = [
        param.get("value") for action in emitted for param in action.iter("param")
        if param.get("name") == "target"
    ]
    assert targets == ["Detail Table", "Revenue KPI"]


def test_no_actions_means_no_actions_element():
    """The schema requires at least one child, so an empty <actions/> is a file Tableau
    refuses - not a workbook without interactivity."""
    root = ET.fromstring(_render(_manifest(actions=[])))

    assert root.find("actions") is None


def test_an_action_whose_endpoint_is_not_a_view_is_rejected():
    """An action runs off a worksheet's marks: a text or filter zone has none."""
    actions = json.loads(json.dumps(ACTIONS))
    actions[0]["targets"] = ["flt-region"]

    assert any("not a view" in error for error in _errors(actions=actions))


def test_an_action_with_an_unknown_run_on_is_rejected():
    """'click' is not one of Tableau's two activations."""
    actions = json.loads(json.dumps(ACTIONS))
    actions[0]["run_on"] = "click"

    assert any("run_on" in error for error in _errors(actions=actions))


def test_a_parameter_action_without_a_field_is_rejected():
    """Without a source field there is no value to write into the parameter."""
    actions = json.loads(json.dumps(ACTIONS))
    actions[-1].pop("field")

    assert any("'field'" in error for error in _errors(actions=actions))


@pytest.mark.parametrize("action_type", sorted(manifest.UNBUILDABLE_ACTION_TYPES))
def test_an_action_this_builder_cannot_emit_is_rejected(action_type):
    """A 'set' or 'url' action that validated would build a dashboard whose interaction is
    silently absent - worse than a manifest that fails and names the alternative."""
    actions = json.loads(json.dumps(ACTIONS))
    actions[0]["type"] = action_type

    assert any(action_type in error for error in _errors(actions=actions))


def test_a_parameter_action_targeting_an_unattested_type_is_rejected():
    """A 'real' clear value has no attested serialization, so the action would never reset -
    and a DZV panel it reveals would never hide again. Rejected rather than built."""
    parameters = json.loads(json.dumps(PARAMETERS))
    parameters.append({"name": "Threshold", "data_type": "real", "current_value": 0.5})
    actions = json.loads(json.dumps(ACTIONS))
    actions[-1]["targets"] = ["Threshold"]
    errors = _errors(parameters=parameters, actions=actions)

    assert any("Threshold" in error and "real" in error for error in errors)


def test_a_parameter_action_targeting_an_attested_type_is_accepted():
    """The string-only narrowing is lifted: integer and boolean resets are attested too
    (references/snippets/dashboard/CLEAR-OPTION-ATTESTATION.md, issue #49)."""
    actions = json.loads(json.dumps(ACTIONS))
    actions[-1]["targets"] = ["Top N"]
    actions[-1]["field"] = "revenue"

    assert not [error for error in _errors(actions=actions) if "Top N" in error]


def test_a_parameter_action_whose_field_and_parameter_types_disagree_is_rejected():
    """The action writes the mark's value straight in, so Desktop only offers fields of the
    parameter's own type - a string field into an integer parameter is one it refuses."""
    actions = json.loads(json.dumps(ACTIONS))
    actions[-1]["targets"] = ["Top N"]
    actions[-1]["field"] = "region"
    errors = _errors(actions=actions)

    assert any("region" in error and "Top N" in error for error in errors)


def test_the_parameter_action_type_tables_cover_the_same_types():
    """The field-compatibility lookup is indexed by the target's type without a default, so
    a type allowed as a target but missing a field rule would raise instead of validate."""
    assert (
        frozenset(manifest.PARAMETER_ACTION_FIELD_TYPES)
        == manifest.PARAMETER_ACTION_TARGET_TYPES
    )


# --- Quick filters and parameter controls (AC #3) ------------------------------

def test_a_filter_card_zone_names_its_sheet_field_and_mode():
    """A card is the UI for one sheet's filter, so it carries both ends."""
    zone = _zone("flt-region")

    assert zone.get("type-v2") == "filter"
    assert zone.get("name") == "Revenue Trend"
    assert zone.get("param") == f"[{DATASOURCE_ID}].[none:region:nk]"
    assert zone.get("mode") == "checkdropdown"


def test_a_filter_card_injects_the_filter_it_controls():
    """Without a worksheet filter the card has nothing to control; an enumerated member list
    would freeze today's domain into the workbook, so it is the all-members form."""
    view = _worksheet_element("Revenue Trend").find("table/view")
    filter_element = view.find(f"filter[@column='[{DATASOURCE_ID}].[none:region:nk]']")

    assert filter_element.get("class") == "categorical"
    assert filter_element.find("groupfilter").get("function") == "level-members"
    assert filter_element.find("groupfilter").get("level") == "[none:region:nk]"


def test_the_quick_filtered_column_reaches_the_slices():
    """A filtered field missing from <slices> is a filter Tableau silently ignores (AC #3)."""
    slices = _worksheet_element("Revenue Trend").find("table/view/slices")

    assert [column.text for column in slices] == [f"[{DATASOURCE_ID}].[none:region:nk]"]


def test_a_card_over_an_already_filtered_field_adds_no_second_filter():
    """An explicit member list is a narrower filter than 'all members', not a duplicate -
    two filters on one column is a worksheet Tableau cannot resolve."""
    sheets = json.loads(json.dumps(WORKSHEETS))
    sheets[1]["filters"] = [{"field": "region", "values": ["West"]}]
    view = _worksheet_element(
        "Revenue Trend", ET.fromstring(_render(_manifest(worksheets=sheets)))
    ).find("table/view")

    filters = view.findall(f"filter[@column='[{DATASOURCE_ID}].[none:region:nk]']")
    assert len(filters) == 1
    assert filters[0].find("groupfilter").get("function") == "member"


def test_the_dashboard_declares_what_its_control_zones_read():
    """A card whose field only the worksheet declares crashes Tableau on open (AC #3)."""
    dashboard = FEATURES_ROOT.find("dashboards/dashboard")
    declared = [
        element.get("name") for element in dashboard.find("datasources")
    ]
    dependencies = dashboard.find(
        f"datasource-dependencies[@datasource='{DATASOURCE_ID}']"
    )

    assert declared == [DATASOURCE_ID, features.PARAMETERS_DATASOURCE]
    assert dependencies.find("column[@name='[region]']") is not None
    assert dependencies.find("column-instance[@name='[none:region:nk]']") is not None
    parameters = dashboard.find(
        f"datasource-dependencies[@datasource='{features.PARAMETERS_DATASOURCE}']"
    )
    assert parameters.find("column[@name='[Top N]']/range") is not None
    # Only the parameters a *control zone* puts on the dashboard: one a sheet's calculation
    # reads is that sheet's declaration, and Desktop drops it from here on save.
    assert [column.get("name") for column in parameters] == ["[Top N]"]


def test_the_dashboards_declarations_come_before_its_zones():
    """Dashboard-CT's sequence: style, size, datasources, datasource-dependencies, zones."""
    tags = [child.tag for child in FEATURES_ROOT.find("dashboards/dashboard")]

    assert tags == [
        "style", "size", "datasources", "datasource-dependencies",
        "datasource-dependencies", "zones", "simple-id",
    ]


def test_a_parameter_control_zone_names_its_parameter():
    """The control is addressed by the qualified parameter, and nothing else."""
    zone = _zone("prm-topn")

    assert zone.get("type-v2") == "paramctrl"
    assert zone.get("param") == "[Parameters].[Top N]"
    # A range parameter's widget is a slider; Desktop rewrites 'compact' on save.
    assert zone.get("mode") == "slider"
    assert zone.get("name") is None  # a parameter is not a sheet's


def test_a_control_kind_with_nothing_to_control_still_reserves_its_box():
    """An image or a button needs a reference the manifest does not carry - the geometry the
    analyst approved survives as an empty zone rather than a crash."""
    document = _manifest(
        objects=[*OBJECTS, {"element_id": "img-logo", "kind": "image"}],
        layout=_layout_with_logo(),
    )
    root = ET.fromstring(_render(document))

    assert _zone("img-logo", root).get("type-v2") == "empty"


def _layout_with_logo() -> dict:
    """The layout with an extra image zone in the control row."""
    layout = json.loads(json.dumps(LAYOUT))
    layout["root"]["children"][0]["children"].append({"id": "img-logo", "size": 20})
    return layout


@pytest.mark.parametrize("override, expected", [
    ({"kind": "filter", "worksheet": "Revenue Trend"}, "'field'"),
    ({"kind": "filter", "field": "region"}, "'worksheet'"),
    ({"kind": "filter", "field": "region", "worksheet": "Nowhere"}, "Nowhere"),
    ({"kind": "filter", "field": "nope", "worksheet": "Revenue Trend"}, "nope"),
    ({"kind": "filter", "field": "revenue", "worksheet": "Revenue Trend"}, "real"),
    ({"kind": "filter", "field": "region", "worksheet": "Revenue Trend",
      "mode": "slider"}, "slider"),
    ({"kind": "parameter"}, "parameter"),
    ({"kind": "parameter", "parameter": "Nope"}, "Nope"),
])
def test_a_malformed_control_object_is_rejected(override, expected):
    """Every message names the zone and the missing or wrong reference."""
    objects = [{"element_id": "flt-region", **override}, OBJECTS[1]]

    assert any(expected in error for error in _errors(objects=objects))


# --- Dynamic Zone Visibility ---------------------------------------------------

def test_the_datagraph_wires_the_boolean_field_to_the_controlled_zone():
    """Two nodes and one edge per zone: the field's value output feeds the zone's visibility
    input, and both nodes are listed against the execution subgraph or neither runs."""
    graph = FEATURES_ROOT.find("datagraph/graph")
    field_node = graph.find("nodes/single-value-field-node")
    zone_node = graph.find("nodes/dashboard-zone-visibility-node")
    edge = graph.find("edges/edge")

    assert field_node.get("fieldname") == f"[{DATASOURCE_ID}].[Show Breakdown]"
    assert edge.get("from") == field_node.get("value-output-guid")
    assert edge.get("to") == zone_node.get("visibility-input-guid")

    subgraph = graph.find("properties/default-execution-subgraph-guid").get("value")
    pairs = graph.findall("node-execution-subgraphs/pair")
    assert {pair.get("node-guid") for pair in pairs} == {
        field_node.get("node-guid"), zone_node.get("node-guid"),
    }
    assert {pair.get("execution-subgraph-guid") for pair in pairs} == {subgraph}


def test_the_visibility_field_is_on_the_controlled_sheets_detail_shelf():
    """Tableau reads a DZV field off the *view*: with the datagraph alone the field is
    unevaluable and the zone never toggles (the workbook opens, it just does nothing). A
    Desktop-saved DZV workbook puts it on the controlled sheet's Detail shelf."""
    view = _worksheet_element("Category Mix").find("table/view")
    pane = _worksheet_element("Category Mix").find("table/panes/pane")
    instance = f"[{DATASOURCE_ID}].[none:Show Breakdown:nk]"

    assert view.find(
        f"datasource-dependencies[@datasource='{DATASOURCE_ID}']"
        f"/column-instance[@column='[Show Breakdown]']"
    ) is not None
    assert [lod.get("column") for lod in pane.findall("encodings/lod")] == [instance]
    # ... and only there: every other sheet is left alone.
    assert _worksheet_element("Revenue Trend").find("table/panes/pane/encodings/lod") is None
    # The field's formula reads a parameter, so the sheet it landed on must declare it too -
    # placing the field before the parameters are attached is what gets this wrong.
    assert view.find(
        f"datasource-dependencies[@datasource='{features.PARAMETERS_DATASOURCE}']"
        f"/column[@name='[Selected Region]']"
    ) is not None


def test_the_visibility_node_points_at_the_dashboard_and_the_right_zone():
    """The zone id is the *wrapper* of a titled element - hiding only the content zone would
    leave its title and legend behind."""
    zone_node = FEATURES_ROOT.find("datagraph/graph/nodes/dashboard-zone-visibility-node")

    assert zone_node.get("dashboard-identifier") == FEATURES_ROOT.find(
        "dashboards/dashboard/simple-id"
    ).get("uuid")
    assert zone_node.get("zone-id") == _zone("chart-category").get("id")


def test_a_titled_element_is_controlled_at_its_wrapper():
    """The wrapper holds the header row, the sheet and the legend; the datagraph must name it
    rather than the sheet zone inside it."""
    sheets = json.loads(json.dumps(WORKSHEETS))
    sheets[3]["title"] = "Category mix"
    root = ET.fromstring(_render(_manifest(worksheets=sheets)))
    zone_node = root.find("datagraph/graph/nodes/dashboard-zone-visibility-node")

    assert zone_node.get("zone-id") == _zone("V-chart-category", root).get("id")


def test_the_dzv_format_flags_are_present_and_alphabetical():
    """Tableau writes the format manifest alphabetically; out of order it rewrites the file."""
    flags = [child.tag for child in FEATURES_ROOT.find("document-format-change-manifest")]

    assert set(features.DZV_FORMAT_FLAGS) <= set(flags)
    assert flags == sorted(flags)


@pytest.mark.parametrize("data_type, current, expected", [
    ("string", "All", "s:LROOT:All"),
    ("integer", 10, "i:10"),
    ("boolean", True, "b:true"),
    ("boolean", False, "b:false"),
])
def test_the_clear_value_serialization_is_pinned_per_type(data_type, current, expected):
    """The tag is the data type's own letter and only a string adds LROOT:; the value after
    it is undelimited. Read off Desktop-saved workbooks - see the attestation note beside
    references/snippets/dashboard/parameter-action.twb (issue #49)."""
    assert features.serialize_clear_value(current, data_type) == expected


@pytest.mark.parametrize("data_type", ["real", "date", "datetime"])
def test_an_unattested_type_gets_no_clear_value(data_type):
    """Guessing a tag Desktop does not write is what makes it refuse the action editor, so
    an unattested type serializes to nothing and the caller falls back to do-nothing."""
    assert features.serialize_clear_value(1, data_type) == ""


def test_a_parameter_action_on_an_attested_non_string_type_still_resets():
    """An integer target now carries a real reset, not a do-nothing - which is what keeps a
    zone the parameter reveals closable."""
    actions = json.loads(json.dumps(ACTIONS))
    actions[-1]["targets"] = ["Top N"]
    actions[-1]["field"] = "revenue"
    root = ET.fromstring(_render(_manifest(actions=actions)))

    assert root.find("actions/edit-parameter-action/clear-option").attrib == {
        "type": "assign-fixed-value", "value": "i:10",
    }


def test_a_parameter_action_brings_its_format_flags():
    """Desktop 2025.1 loads <edit-parameter-action> against a schema that does not declare it
    unless the format manifest announces ParameterAction - the workbook is refused outright,
    which is how this was found."""
    flags = [child.tag for child in FEATURES_ROOT.find("document-format-change-manifest")]

    assert set(features.PARAMETER_ACTION_FORMAT_FLAGS) <= set(flags)


def test_no_parameter_action_means_no_parameter_action_flags():
    """A flag for a feature the file does not use makes Tableau migrate a workbook that needs
    no migration."""
    actions = [entry for entry in ACTIONS if entry["type"] != "parameter"]
    root = ET.fromstring(_render(_manifest(actions=actions)))
    flags = [child.tag for child in root.find("document-format-change-manifest")]

    assert root.find("actions/edit-parameter-action") is None
    assert not set(features.PARAMETER_ACTION_FORMAT_FLAGS) & set(flags)


def test_no_visibility_means_no_datagraph_and_no_extra_flags():
    """The flags announce a document feature: claiming one the file does not use makes
    Tableau migrate a workbook that needs no migration."""
    layout = json.loads(json.dumps(LAYOUT))
    layout["root"]["children"][-1].pop("visibility")
    root = ET.fromstring(_render(_manifest(layout=layout)))
    flags = [child.tag for child in root.find("document-format-change-manifest")]

    assert root.find("datagraph") is None
    assert not set(features.DZV_FORMAT_FLAGS) & set(flags)


def test_the_datagraph_comes_after_the_windows():
    """WorkbookFile-CT puts <datagraph> after <windows>; anywhere else fails the XSD."""
    tags = [child.tag for child in FEATURES_ROOT]

    assert tags.index("windows") < tags.index("datagraph")


@pytest.mark.parametrize("field_name, expected", [
    ("Margin Pct", "boolean"),   # declared, but not a boolean
    ("Nonexistent", "Nonexistent"),
])
def test_a_visibility_field_that_is_not_a_boolean_calc_is_rejected(field_name, expected):
    """Tableau shows or hides a zone on one boolean value, and the builder qualifies the
    field against the datasource that declares it."""
    layout = json.loads(json.dumps(LAYOUT))
    layout["root"]["children"][-1]["visibility"] = field_name

    assert any(expected in error for error in _errors(layout=layout))


# --- The validators, and the whole path ----------------------------------------

def test_the_features_workbook_passes_the_semantic_validator(tmp_path):
    """The legacy cross-reference checks, on a workbook with every feature switched on."""
    import validate_twb

    twb_path = tmp_path / "features.twb"
    twb_path.write_text(FEATURES_XML, encoding="utf-8")

    report = validate_twb.TwbValidator(str(twb_path)).validate()

    assert not [
        f"{result.name}: {result.details}" for result in report.results if not result.passed
    ]


def test_the_features_workbook_passes_the_xsd(tmp_path):
    """Actions, parameters, reference lines and the datagraph are all schema-checked.

    The 2024.2-2025.x target legitimately omits ``<explain-data>``, which the 2026.1 schema
    requires - that one error is the documented version shift, not breakage.
    """
    pytest.importorskip("lxml")
    import validate_twb_xsd

    twb_path = tmp_path / "features.twb"
    twb_path.write_text(FEATURES_XML, encoding="utf-8")

    _, errors = validate_twb_xsd.validate(
        twb_path, validate_twb_xsd.load_schema(validate_twb_xsd.XSD_PATH)
    )

    assert [error.message for error in errors if "explain-data" not in error.message] == []


def test_the_whole_build_produces_a_twbx_that_keeps_its_interactivity(tmp_path):
    """End to end (AC #4): a manifest on disk with a cross-filter, a highlight and a filter
    card becomes a packaged .twbx whose .twb still carries them."""
    (tmp_path / "STATE.md").write_text(
        build.apply_status_updates(
            init.render_state_md(TARGET_VERSION),
            {"spec": "approved", "data": "approved"},
        ),
        encoding="utf-8",
    )
    (tmp_path / "DATA-MODEL.md").write_text(DATA_MODEL, encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sales_orders.csv").write_text(
        ",".join(CSV_HEADER) + "\n2024-01-05,West,Technology,1200.5,120.5\n",
        encoding="utf-8",
    )
    version_dir = tmp_path / build.VERSION_DIR / "v_1"
    version_dir.mkdir(parents=True)
    (version_dir / build.SPEC_FILENAME).write_text(
        "# Implementation Spec: Sales\n\n"
        "## Element Mapping\n\n"
        "| id | tableau construct | justification |\n"
        "|----|-------------------|---------------|\n"
        "| kpi-revenue | Text mark, [Margin Pct] | - |\n\n"
        "## Layout\n\nThe approved tree.\n\n"
        f"```json\n{json.dumps(LAYOUT, indent=2)}\n```\n",
        encoding="utf-8",
    )
    (version_dir / build.MANIFEST_FILENAME).write_text(
        json.dumps(_manifest(), indent=2), encoding="utf-8"
    )

    result = build.build_workbook(tmp_path)

    assert result.ok is True, result.message
    packaged = next(version_dir.glob("*.twbx"))
    with zipfile.ZipFile(packaged) as archive:
        name = next(item for item in archive.namelist() if item.endswith(".twb"))
        root = ET.fromstring(archive.read(name).decode("utf-8"))

    assert [element.tag for element in root.find("actions")] == [
        "action", "action", "action", "edit-parameter-action",
    ]
    zone_types = {zone.get("type-v2") for zone in root.iter("zone")}
    assert {"filter", "paramctrl"} <= zone_types
    assert root.find("datagraph") is not None


def test_the_demo_manifest_template_documents_every_new_key():
    """The template is what an agent authors a manifest from - a key it does not mention is
    a feature no dashboard will ever use."""
    template = (
        Path(twb.__file__).resolve().parent.parent
        / "references" / "BUILD-MANIFEST-TEMPLATE.md"
    ).read_text(encoding="utf-8")

    for key in (
        "table_calc", "reference_lines", "visibility", "run_on", "data_type",
        "current_value", "checkdropdown",
    ):
        assert key in template, f"BUILD-MANIFEST-TEMPLATE.md never mentions '{key}'"


def test_the_manifest_template_example_validates():
    """The template's worked example is what an agent copies from, so it has to be a manifest
    the validator accepts - a documented key with a wrong shape is worse than no docs."""
    template = (
        Path(twb.__file__).resolve().parent.parent
        / "references" / "BUILD-MANIFEST-TEMPLATE.md"
    ).read_text(encoding="utf-8")

    example = template.split("## Example", 1)[1].split("```json", 1)[1].split("```", 1)[0]

    assert manifest.validate_manifest(
        json.loads(example), DATA_MODEL, TARGET_VERSION
    ) == []


# --- Dynamic Zone Visibility driven by a boolean parameter (issue #49) ---------

def _boolean_parameter_manifest() -> dict:
    """A manifest whose DZV zone is driven by a boolean parameter, with no comparison calc.

    This is the shape Desktop writes when a parameter action drives the zone: the datagraph
    binds straight to ``[Parameters].[<name>]`` (attested in ``SalesMRR.twbx``).
    """
    parameters = json.loads(json.dumps(PARAMETERS))
    parameters.append({"name": "Show Panel", "data_type": "boolean",
                       "current_value": False, "values": [True, False]})
    calculated = json.loads(json.dumps(CALCULATED_FIELDS))
    calculated.append({"name": "Panel Toggle", "formula": "TRUE",
                       "datasource": "sales_orders", "type": "boolean"})
    actions = json.loads(json.dumps(ACTIONS))
    actions[-1]["targets"] = ["Show Panel"]
    actions[-1]["field"] = "Panel Toggle"
    layout = json.loads(json.dumps(LAYOUT))

    def _mark(node):
        if node.get("id") == "chart-category":
            node["visibility"] = "Show Panel"
        for child in node.get("children", []):
            _mark(child)

    _mark(layout["root"])
    return _manifest(parameters=parameters, calculated_fields=calculated,
                     actions=actions, layout=layout)


def test_a_boolean_parameter_can_drive_zone_visibility_directly():
    """Desktop binds the visibility node straight to the parameter - the comparison calc a
    string parameter needs (`<> "All"`) is dead weight when the parameter is already boolean."""
    document = _boolean_parameter_manifest()

    assert not manifest.validate_manifest(document, DATA_MODEL, TARGET_VERSION)

    root = ET.fromstring(_render(document))
    node = root.find("datagraph/graph/nodes/single-value-field-node")

    assert node.get("fieldname") == "[Parameters].[Show Panel]"


def test_a_parameter_driven_zone_puts_nothing_on_a_detail_shelf():
    """A parameter is view-independent, so unlike a boolean calc it needs no sheet to carry
    it - attaching it to one would add a field the view never uses."""
    root = ET.fromstring(_render(_boolean_parameter_manifest()))
    details = [
        element.get("column")
        for element in root.findall("worksheets/worksheet/table/panes/pane/encodings/lod")
    ]

    assert not [column for column in details if "Show Panel" in column]
