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
| nps_score | integer | measure | 82 | Net promoter score |
| lifetime_value | real | measure | 45200.0 | Lifetime value |
| profit | real | measure | 120.5 | Order profit |
"""

CSV_HEADERS = {
    "customers.csv": [
        "customer_name", "product_category", "nps_score", "lifetime_value", "profit",
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
]

MANIFEST = {
    "target_tableau_version": TARGET_VERSION,
    "datasources": [{
        "name": "customers",
        "csv": "customers.csv",
        "fields": [
            {"name": "customer_name", "type": "string"},
            {"name": "product_category", "type": "string"},
            {"name": "nps_score", "type": "integer"},
            {"name": "lifetime_value", "type": "real"},
            {"name": "profit", "type": "real"},
        ],
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

ROOT = ET.fromstring(twb.render_workbook(MANIFEST, DATA_MODEL, CSV_HEADERS))
DATASOURCE_ID = twb.datasource_id("customers")


def _worksheet_element(name: str) -> ET.Element:
    """Return the ``<worksheet>`` with the given name."""
    for element in ROOT.findall("worksheets/worksheet"):
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


# --- Bug 3: number_formats reach the datasource column --------------------------

def test_a_worksheets_number_format_lands_on_the_datasource_column():
    """Mark labels and axis ticks read the column's ``default-format``; the cell rule only
    formats cells, which is why labelled bars rendered ``19,241.21`` under a ``$#,##0`` spec."""
    column = ROOT.find(
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
    column = ROOT.find(
        f"datasources/datasource[@name='{DATASOURCE_ID}']/column[@name='[lifetime_value]']"
    )

    assert "default-format" not in column.attrib
