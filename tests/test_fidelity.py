"""Contract test for the two worksheet-level Desktop fidelity bugs of issue #59.

Both were found by opening ``demo/mock-version/v_1/dashboard.twbx`` in Tableau Desktop 2025.1
after all three validators had passed it - the repo's standing lesson that the validators are
not a Desktop oracle. Each guarantee below is pinned from the *smallest* manifest that
reproduced the symptom, so a regression names the bug rather than a golden diff:

* **an explicit ``aggregation: "none"`` on a measure is a discrete pill.** The demo's customer
  table asked for ``nps_score`` as a row header and got a continuous Nps Score *axis* per
  customer, because ``"none"`` fell through to the measure's ``quantitative`` column type.
* **a worksheet's ``number_formats`` reach the datasource column.** The cell style rule
  formats a text table's cells but is not what Desktop consults for mark labels or axis ticks;
  a field's default format is an attribute on its ``<column>``.
"""

import xml.etree.ElementTree as ET

import manifest
import twb

TARGET_VERSION = "2024.2-2025.x"

DATA_MODEL = """# Data Model

## Data source: `customers.csv`

| Field | Type | Role | Sample values | Description |
|-------|------|------|---------------|-------------|
| customer_name | string | dimension | Acme Ltd | Customer |
| product_category | string | dimension | Software | Product category |
| order_id | string | dimension | SO-1001 | Order id |
| nps_score | integer | measure | 82 | Net promoter score |
| lifetime_value | real | measure | 45200.0 | Lifetime value |
| revenue | real | measure | 1200.5 | Order revenue |
| profit | real | measure | 120.5 | Order profit |
"""

CSV_HEADERS = {
    "customers.csv": [
        "customer_name", "product_category", "order_id", "nps_score", "lifetime_value",
        "revenue", "profit",
    ]
}

#: One table sheet reproducing bug 2 (``nps_score`` on Rows, un-aggregated) and one bar sheet
#: reproducing bug 3 (a plain CSV measure with a ``number_formats`` entry).
WORKSHEETS = [
    {
        "name": "Top Customers",
        "element_id": "table-customers",
        "chart_type": "table",
        "datasource": "customers",
        "shelves": {
            "rows": ["customer_name", {"field": "nps_score", "aggregation": "none"}],
        },
        "encodings": {"text": {"field": "lifetime_value", "aggregation": "sum"}},
    },
    {
        "name": "Profit by Category",
        "element_id": "chart-category",
        "chart_type": "bar",
        "datasource": "customers",
        "shelves": {"columns": ["product_category"], "rows": ["profit"]},
        "encodings": {"text": {"field": "profit", "aggregation": "sum"}},
        "number_formats": [{"field": "profit", "format": "$#,##0"}],
    },
    # The scope guard for bug 2: "none" on an aggregate calculated field means "do not
    # re-aggregate", not "make it discrete" - the demo's AOV KPI card is exactly this shape.
    {
        "name": "AOV KPI",
        "element_id": "kpi-aov",
        "chart_type": "text",
        "datasource": "customers",
        "encodings": {"text": {"field": "Average Order Value", "aggregation": "none"}},
    },
]

MANIFEST = {
    "target_tableau_version": TARGET_VERSION,
    "datasources": [{
        "name": "customers",
        "csv": "customers.csv",
        "fields": [
            {"name": "customer_name", "type": "string"},
            {"name": "product_category", "type": "string"},
            {"name": "order_id", "type": "string"},
            {"name": "nps_score", "type": "integer"},
            {"name": "lifetime_value", "type": "real"},
            {"name": "revenue", "type": "real"},
            {"name": "profit", "type": "real"},
        ],
    }],
    "calculated_fields": [{
        "name": "Average Order Value",
        "formula": "SUM([revenue]) / COUNTD([order_id])",
        "datasource": "customers",
        "type": "real",
        "format": "$#,##0",
    }],
    "worksheets": WORKSHEETS,
    "layout": {
        "canvas": {"width": 1366, "height": 768},
        "root": {
            "type": "vert",
            "children": [{"id": entry["element_id"]} for entry in WORKSHEETS],
        },
    },
    "actions": [],
    "parameters": [],
}

FIDELITY_ROOT = ET.fromstring(twb.render_workbook(MANIFEST, DATA_MODEL, CSV_HEADERS))
DATASOURCE_ID = twb.datasource_id("customers")


def _worksheet_element(name: str) -> ET.Element:
    """Return the ``<worksheet>`` with the given name."""
    for element in FIDELITY_ROOT.findall("worksheets/worksheet"):
        if element.get("name") == name:
            return element
    raise AssertionError(f"no worksheet named {name!r} in the rendered workbook")


def test_the_fidelity_manifest_validates():
    """The schema accepts it, so a failure below is the builder's and not the manifest's."""
    assert manifest.validate_manifest(MANIFEST, DATA_MODEL, TARGET_VERSION) == []


# --- Bug 2: an explicit "none" on a measure is discrete -------------------------

def test_an_unaggregated_measure_on_rows_is_a_discrete_pill():
    """A continuous pill on Rows draws an axis - which is why every customer row grew its own
    90/80/70 Nps Score axis. Discrete (``:ok``, ``type='ordinal'``) is what Desktop writes for
    a measure dragged to Rows and set to Discrete with no aggregation."""
    element = _worksheet_element("Top Customers")
    rows = element.findtext("table/rows")

    assert ":ok]" in rows and ":qk]" not in rows, rows
    instance = element.find(
        f"table/view/datasource-dependencies[@datasource='{DATASOURCE_ID}']"
        "/column-instance[@name='[none:nps_score:ok]']"
    )
    assert instance is not None, ET.tostring(element.find("table/view"))
    assert instance.get("type") == "ordinal"
    assert instance.get("derivation") == "None"


def test_a_bare_measure_on_rows_is_still_continuous_sum():
    """The scope guard: only an *explicit* ``"none"`` turns discrete. A bare measure is
    Tableau's own SUM default, and every bar chart depends on it staying continuous."""
    rows = _worksheet_element("Profit by Category").findtext("table/rows")

    assert "[sum:profit:qk]" in rows, rows


def test_none_on_an_aggregate_calculated_field_stays_continuous():
    """The other scope guard. On ``SUM([revenue]) / COUNTD([order_id])``, ``"none"`` means
    "do not re-aggregate" - it is still one continuous number. Making it discrete turned the
    demo's AOV KPI card into a row header, so this pins the KPI card's text pill.

    Issue #62 corrected the *prefix* this originally pinned: "do not re-aggregate" is the
    ``usr:`` (User) instance, not ``none:``. ``none:`` asks for a row-level value the calc
    does not have, and Desktop refused the pill outright. Continuous (``:qk``,
    ``type='quantitative'``) is what this test is actually guarding, and that is unchanged.
    """
    element = _worksheet_element("AOV KPI")
    text = element.find(
        "table/panes/pane/encodings/text"
    )

    assert text.get("column").endswith("[usr:Average Order Value:qk]"), text.attrib
    instance = element.find(
        f"table/view/datasource-dependencies[@datasource='{DATASOURCE_ID}']"
        "/column-instance[@name='[usr:Average Order Value:qk]']"
    )
    assert instance is not None
    assert instance.get("type") == "quantitative"
    assert instance.get("derivation") == "User"


# --- Bug 3: number_formats reach the datasource column --------------------------

def test_a_worksheets_number_format_lands_on_the_datasource_column():
    """Mark labels and axis ticks read the column's ``default-format``; the cell rule only
    formats cells, which is why labelled bars rendered ``19,241.21`` under a ``$#,##0`` spec."""
    column = FIDELITY_ROOT.find(
        f"datasources/datasource[@name='{DATASOURCE_ID}']/column[@name='[profit]']"
    )

    assert column is not None
    assert column.get("default-format") == "$#,##0"


def test_the_cell_style_rule_survives_alongside_it():
    """Desktop writes both; a text table's cells are formatted by the rule, not the column."""
    cell = _worksheet_element("Profit by Category").find(
        "table/style/style-rule[@element='cell']/format"
    )

    assert cell.get("attr") == "text-format"
    assert cell.get("value") == "$#,##0"


def test_an_unformatted_column_carries_no_attribute():
    """No format asked for, no attribute invented."""
    column = FIDELITY_ROOT.find(
        f"datasources/datasource[@name='{DATASOURCE_ID}']/column[@name='[lifetime_value]']"
    )

    assert "default-format" not in column.attrib
