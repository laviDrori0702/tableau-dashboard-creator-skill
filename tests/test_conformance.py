"""Contract test for the build's third validator (issue #38).

:mod:`validate_conformance` is the only check that compares the *manifest* to the built
XML. The other two validators read the workbook alone: the semantic one asks whether the
XML is internally consistent, the XSD one whether it matches the schema - so a workbook
that silently dropped a whole worksheet, or a zone the analyst approved, passes both. That
is the gap these checks close, plus the unsupported-construct policy: a construct the
builder has no template for must be *named*, not silently reduced to an empty box.

Every check is driven off a real built workbook (``twb.render_workbook``) rather than a
hand-written fixture, so the checks stay coupled to what the assembler actually emits.
"""

import xml.etree.ElementTree as ET

import pytest

import twb  # the pure assembler that produces the XML under test
import validate_conformance as conformance

TARGET_VERSION = "2024.2-2025.x"

DATA_MODEL = """# Data Model

## Data source: `sales_orders.csv`

| Field | Type | Role | Sample values | Description |
|-------|------|------|---------------|-------------|
| order_date | date | dimension | 2024-01-05 | Order date |
| region | string | dimension | West | Sales region |
| revenue | real | measure | 1200.5 | Order revenue |
"""

CSV_HEADERS = {"sales_orders.csv": ["order_date", "region", "revenue"]}


def _manifest() -> dict:
    """A valid two-worksheet manifest for the data model above."""
    return {
        "target_tableau_version": TARGET_VERSION,
        "datasources": [{
            "name": "sales_orders",
            "csv": "sales_orders.csv",
            "fields": [
                {"name": "order_date", "type": "date"},
                {"name": "region", "type": "string"},
                {"name": "revenue", "type": "real"},
            ],
        }],
        "worksheets": [
            {
                "name": "Revenue KPI", "element_id": "kpi-revenue", "chart_type": "text",
                "datasource": "sales_orders",
                "shelves": {"rows": [], "columns": []},
                "encodings": {"text": "revenue"},
            },
            {
                "name": "Revenue Trend", "element_id": "chart-trend", "chart_type": "line",
                "datasource": "sales_orders",
                "shelves": {"columns": ["order_date"], "rows": ["revenue"]},
                "encodings": {"color": "region"},
            },
        ],
        "layout": {
            "canvas": {"width": 1366, "height": 768},
            "root": {"type": "vert", "children": [
                {"id": "kpi-revenue", "size": 20},
                {"id": "chart-trend", "size": 80},
            ]},
        },
        "actions": [],
        "parameters": [],
    }


def _built(document=None) -> ET.Element:
    """Render a manifest into a parsed ``<workbook>`` element."""
    return ET.fromstring(
        twb.render_workbook(document or _manifest(), DATA_MODEL, CSV_HEADERS, "")
    )


def _errors(document=None, root=None) -> list[str]:
    """Run the conformance checks over a built workbook."""
    document = document or _manifest()
    return conformance.conformance_errors(root if root is not None else _built(document),
                                          document)


# --- The happy path -----------------------------------------------------------

def test_a_built_workbook_conforms_to_its_manifest():
    assert _errors() == []


def test_objects_and_titles_still_conform():
    """Titled elements and object zones nest extra zones - the ids must still resolve."""
    document = _manifest()
    document["worksheets"][0]["title"] = "Revenue"
    document["layout"]["root"]["children"].append({"id": "note", "size": 10})
    document["objects"] = [{"element_id": "note", "kind": "text", "text": "Hello"}]
    assert _errors(document) == []


# --- Check 1: every element the manifest places became a zone -----------------

def test_element_with_no_zone_is_caught():
    """The manifest's own layout is the authority: a zone the assembler dropped is a bug."""
    root = _built()
    # 'kpi-revenue' is an unwrapped leaf, so its friendly-name is the only carrier of the id.
    dropped = next(
        zone for zone in root.iter("zone") if zone.get("friendly-name") == "kpi-revenue"
    )
    dropped.set("friendly-name", "something-else")

    errors = _errors(root=root)
    assert any("kpi-revenue" in error for error in errors), errors


def test_unembedded_view_element_is_caught():
    """A wrapped leaf keeps its friendly-name even if the sheet zone inside it is gone."""
    root = _built()
    wrapper = next(
        zone for zone in root.iter("zone")
        if zone.get("friendly-name") == "V-chart-trend"
    )
    wrapper.remove(wrapper.find("zone[@name='Revenue Trend']"))

    errors = _errors(root=root)
    assert any(
        "Revenue Trend" in error and "chart-trend" in error for error in errors
    ), errors


# --- Check 2: every zone references a real sheet ------------------------------

def test_zone_naming_a_nonexistent_sheet_is_caught():
    root = _built()
    zone = next(
        zone for zone in root.iter("zone") if zone.get("name") == "Revenue Trend"
    )
    zone.set("name", "Ghost Sheet")

    errors = _errors(root=root)
    assert any("Ghost Sheet" in error for error in errors), errors


# --- Check 3: every manifest worksheet is a sheet with a window ---------------

def test_manifest_worksheet_missing_from_the_workbook_is_caught():
    root = _built()
    worksheets = root.find("worksheets")
    worksheets.remove(worksheets.find("worksheet[@name='Revenue KPI']"))

    errors = _errors(root=root)
    assert any("Revenue KPI" in error for error in errors), errors


def test_sheet_with_no_window_is_caught():
    root = _built()
    windows = root.find("windows")
    windows.remove(windows.find("window[@name='Revenue KPI']"))

    errors = _errors(root=root)
    assert any(
        "Revenue KPI" in error and "window" in error for error in errors
    ), errors


# --- Check 4: the dashboard window's viewpoints match its sheets --------------

def test_embedded_sheet_with_no_viewpoint_is_caught():
    root = _built()
    viewpoints = root.find("windows/window[@class='dashboard']/viewpoints")
    viewpoints.remove(viewpoints.find("viewpoint[@name='Revenue Trend']"))

    errors = _errors(root=root)
    assert any(
        "Revenue Trend" in error and "viewpoint" in error for error in errors
    ), errors


def test_stale_viewpoint_naming_no_sheet_is_caught():
    """The other direction: a hand-written block can leave a viewpoint behind."""
    root = _built()
    ET.SubElement(
        root.find("windows/window[@class='dashboard']/viewpoints"),
        "viewpoint", {"name": "Deleted Sheet"},
    )

    errors = _errors(root=root)
    assert any(
        "Deleted Sheet" in error and "viewpoint" in error for error in errors
    ), errors


def test_dashboard_with_no_window_is_caught():
    root = _built()
    windows = root.find("windows")
    windows.remove(windows.find("window[@class='dashboard']"))

    errors = _errors(root=root)
    assert any("no dashboard tab" in error for error in errors), errors


# --- The unsupported-construct policy ----------------------------------------

@pytest.mark.parametrize("kind", ["image", "button", "legend"])
def test_a_deferred_object_kind_names_the_gap(kind):
    """A construct with no template is refused *by name*, not silently emptied."""
    document = _manifest()
    document["layout"]["root"]["children"].append({"id": "logo", "size": 10})
    document["objects"] = [{"element_id": "logo", "kind": kind}]

    notes = conformance.unsupported_notes(document)
    assert len(notes) == 1
    note = notes[0]
    assert kind in note and "logo" in note
    # Both move-on options the policy owes the analyst (issue #38).
    assert ".twb" in note and "hand-write" in note


def test_a_supported_object_kind_is_not_reported():
    document = _manifest()
    document["layout"]["root"]["children"].append({"id": "note", "size": 10})
    document["objects"] = [{"element_id": "note", "kind": "text", "text": "Hi"}]
    assert conformance.unsupported_notes(document) == []


def test_the_rest_of_the_workbook_still_builds_around_a_gap():
    """Refusing one construct must not cost the analyst the other zones."""
    document = _manifest()
    document["layout"]["root"]["children"].append({"id": "logo", "size": 10})
    document["objects"] = [{"element_id": "logo", "kind": "image"}]

    root = _built(document)
    assert root.find("worksheets/worksheet[@name='Revenue Trend']") is not None
    assert conformance.conformance_errors(root, document) == []
