"""Contract test for tableau-build (CONTRACT.md step 8, §4.1, §4.3).

``tableau-build`` turns the approved ``IMPLEMENTATION-SPEC.md`` + ``DATA-MODEL.md`` into a
Tableau workbook, across three modules:

* :mod:`manifest` (pure core) - the **build manifest**: the machine-readable JSON an agent
  derives from the spec + data model that the deterministic builder consumes. Validation is
  fail-fast and names the offending entry, so a bad spec-to-manifest translation is caught
  before any XML is generated.
* :mod:`twb` (pure assembler) - manifest -> ``.twb`` XML. What is pinned here is everything
  Tableau is strict about and an LLM checklist got wrong: the order of ``<workbook>``'s
  children, unique generated ids, the four places every column must appear, the live-only
  relation, and the version-dependent ``<explain-data>``.
* :mod:`build` (orchestration) - the entry gate (§4.1: ``spec`` and ``data`` resolved with
  their artifacts on disk), the assembly + packaging step, and the STATE.md transition
  (``build`` -> ``approved``, never touching ``current_version``, overwriting in the
  current ``v_N``, §4.3).
"""

import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

import build  # the orchestration under test
import init  # builds a realistic STATE.md the same way a real project would
import manifest  # the pure manifest-schema core (on sys.path via conftest.py)
import route  # the router parses/routes the STATE.md build writes
import twb  # the pure .twb assembler
import worksheet  # caption_for, the shared caption rule

TARGET_VERSION = "2024.2-2025.x"

DATA_MODEL = """# Data Model

## Acquisition

- tier: csv (provided in data/)

## Data source: `sales_orders.csv`

| Field | Type | Role | Sample values | Description |
|-------|------|------|---------------|-------------|
| order_date | date | dimension | 2024-01-05 | Order date |
| region | string | dimension | West | Sales region |
| revenue | real | measure | 1200.5 | Order revenue |
"""

def _layout(children=None) -> dict:
    """The spec's container tree, as the manifest carries it."""
    return {
        "canvas": {"width": 1366, "height": 768},
        "root": {
            "type": "vert",
            "children": children or [
                {"id": "kpi-revenue", "size": 20},
                {"id": "chart-trend", "size": 80},
            ],
        },
    }


def _spec_md(layout=None) -> str:
    """An IMPLEMENTATION-SPEC.md whose '## Layout' tree the manifest must carry (§1.1)."""
    return (
        "# Implementation Spec: Sales\n\n"
        "## Element Mapping\n\n"
        "| id | tableau construct | justification |\n"
        "|----|-------------------|---------------|\n"
        "| kpi-revenue | Text mark, SUM([revenue]) | - |\n"
        "| chart-trend | Line mark: MONTH([order_date]) x SUM([revenue]) | - |\n\n"
        "## Layout\n\nA vert stack.\n\n"
        f"```json\n{json.dumps(layout or _layout(), indent=2)}\n```\n"
    )


SPEC = _spec_md()


def _manifest(**overrides) -> dict:
    """A valid build manifest for the DATA_MODEL / SPEC pair above."""
    document = {
        "target_tableau_version": TARGET_VERSION,
        "datasources": [
            {
                "name": "sales_orders",
                "csv": "sales_orders.csv",
                "fields": [
                    {"name": "order_date", "type": "date"},
                    {"name": "region", "type": "string"},
                    {"name": "revenue", "type": "real"},
                ],
            }
        ],
        "worksheets": [
            {
                "name": "Revenue KPI",
                "element_id": "kpi-revenue",
                "chart_type": "text",
                "datasource": "sales_orders",
                "shelves": {"rows": [], "columns": []},
                "encodings": {"text": "revenue"},
            },
            {
                "name": "Revenue Trend",
                "element_id": "chart-trend",
                "chart_type": "line",
                "datasource": "sales_orders",
                "shelves": {"columns": ["order_date"], "rows": ["revenue"]},
                "encodings": {"color": "region"},
            },
        ],
        "layout": _layout(),
        "actions": [],
        "parameters": [],
    }
    document.update(overrides)
    return document


def _errors(document=None, **overrides) -> list[str]:
    """Validate a manifest against the DATA_MODEL and return the error messages."""
    return manifest.validate_manifest(
        _manifest(**overrides) if document is None else document,
        DATA_MODEL,
        TARGET_VERSION,
    )


def _state_with(
    project_dir: Path, target: str = TARGET_VERSION, **status_overrides: str
) -> None:
    """Write a canonical STATE.md, optionally overriding some step statuses."""
    text = init.render_state_md(target)
    if status_overrides:
        text = build.apply_status_updates(text, dict(status_overrides))
    (project_dir / "STATE.md").write_text(text, encoding="utf-8")


def _write_spec(project_dir: Path, version: str = "v_1", text: str = SPEC) -> None:
    """Write ``text`` to ``mock-version/<version>/IMPLEMENTATION-SPEC.md``."""
    version_dir = project_dir / build.VERSION_DIR / version
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / build.SPEC_FILENAME).write_text(text, encoding="utf-8")


def _write_manifest(project_dir: Path, document: dict, version: str = "v_1") -> None:
    """Write ``document`` to ``mock-version/<version>/build-manifest.json``."""
    version_dir = project_dir / build.VERSION_DIR / version
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / build.MANIFEST_FILENAME).write_text(
        json.dumps(document, indent=2), encoding="utf-8"
    )


def _ready_project(
    project_dir: Path, target: str = TARGET_VERSION, **status_overrides: str
) -> None:
    """Open build's entry gate: spec+data resolved with all three artifacts on disk."""
    _state_with(
        project_dir, target, **{"spec": "approved", "data": "approved", **status_overrides}
    )
    (project_dir / "DATA-MODEL.md").write_text(DATA_MODEL, encoding="utf-8")
    data_dir = project_dir / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "sales_orders.csv").write_text(
        "order_date,region,revenue\n2024-01-05,West,1200.5\n", encoding="utf-8"
    )
    _write_spec(project_dir)


def _built_project(project_dir: Path, **status_overrides: str) -> build.BuildResult:
    """A ready project with the manifest authored and the workbook built + packaged."""
    _ready_project(project_dir, **status_overrides)
    _write_manifest(project_dir, _manifest())
    return build.build_workbook(project_dir)


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def test_precheck_blocks_when_no_state(tmp_path):
    """No STATE.md => build cannot run; the blocker points at tableau-init."""
    result = build.precheck(tmp_path)

    assert result.can_run is False
    assert "tableau-init" in result.blocker


def test_precheck_blocks_when_spec_not_resolved(tmp_path):
    """spec must be resolved before build runs, and the blocker names it (§4.1)."""
    _ready_project(tmp_path, spec="pending")

    result = build.precheck(tmp_path)

    assert result.can_run is False
    assert "spec" in result.blocker and "pending" in result.blocker
    assert "tableau-spec" in result.blocker


def test_precheck_blocks_when_spec_file_missing(tmp_path):
    """Even with spec approved, a missing IMPLEMENTATION-SPEC.md at current_version blocks."""
    _ready_project(tmp_path)
    (tmp_path / build.VERSION_DIR / "v_1" / build.SPEC_FILENAME).unlink()

    result = build.precheck(tmp_path)

    assert result.can_run is False
    assert build.SPEC_FILENAME in result.blocker


def test_precheck_blocks_when_data_not_resolved(tmp_path):
    """data must be resolved too - the manifest's fields come from DATA-MODEL.md (§4.1)."""
    _ready_project(tmp_path, data="pending")

    result = build.precheck(tmp_path)

    assert result.can_run is False
    assert "data" in result.blocker and "tableau-data" in result.blocker


def test_precheck_blocks_when_data_model_missing(tmp_path):
    """A missing DATA-MODEL.md blocks even when data is approved (§4.1)."""
    _ready_project(tmp_path)
    (tmp_path / "DATA-MODEL.md").unlink()

    result = build.precheck(tmp_path)

    assert result.can_run is False
    assert "DATA-MODEL.md" in result.blocker


def test_precheck_accepts_scaffold_sample_data(tmp_path):
    """The csv read is satisfied by scaffold/sample-data/ too (CONTRACT.md §3.1)."""
    _ready_project(tmp_path)
    (tmp_path / "data" / "sales_orders.csv").unlink()
    scaffold = tmp_path / "scaffold" / "sample-data"
    scaffold.mkdir(parents=True)
    (scaffold / "sales_orders.csv").write_text("region\nWest\n", encoding="utf-8")

    assert build.precheck(tmp_path).can_run is True


def test_precheck_reports_target_version_dir(tmp_path):
    """precheck surfaces the v_N it builds into and the paths it reads/writes."""
    _state_with(tmp_path, spec="approved", data="approved")
    (tmp_path / "STATE.md").write_text(
        (tmp_path / "STATE.md").read_text(encoding="utf-8").replace(
            "current_version: v_1", "current_version: v_3"
        ),
        encoding="utf-8",
    )
    (tmp_path / "DATA-MODEL.md").write_text(DATA_MODEL, encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sales_orders.csv").write_text("region\nWest\n", encoding="utf-8")
    _write_spec(tmp_path, "v_3")

    result = build.precheck(tmp_path)

    assert result.can_run is True
    assert result.version == "v_3"
    assert result.spec_path == "mock-version/v_3/IMPLEMENTATION-SPEC.md"
    assert result.manifest_path == "mock-version/v_3/build-manifest.json"
    assert result.workbook_path == "mock-version/v_3/dashboard.twbx"
    assert result.target_tableau_version == TARGET_VERSION
    assert result.manifest_exists is False


# --- Manifest schema (manifest.py) -------------------------------------------

def test_valid_manifest_has_no_errors():
    """The happy path: a manifest consistent with the data model and layout validates."""
    assert _errors() == []


def test_missing_top_level_key_is_rejected():
    """Every required section must be present - the message names the missing key."""
    document = _manifest()
    del document["worksheets"]

    errors = _errors(document)

    assert any("worksheets" in error for error in errors)


def test_target_version_must_match_state():
    """The manifest's target version is STATE.md's; a mismatch is a translation bug."""
    errors = _errors(target_tableau_version="2026.1+")

    assert any("2026.1+" in error and "2024.2-2025.x" in error for error in errors)


def test_unknown_chart_type_names_the_worksheet():
    """An unknown chart type is rejected with the offending worksheet named."""
    document = _manifest()
    document["worksheets"][1]["chart_type"] = "donut"

    errors = _errors(document)

    assert any("Revenue Trend" in error and "donut" in error for error in errors)


def test_element_id_not_in_layout_tree_is_rejected():
    """Every worksheet must occupy a zone in the layout tree, named when it does not."""
    document = _manifest()
    document["worksheets"][1]["element_id"] = "chart-ghost"

    errors = _errors(document)

    assert any("chart-ghost" in error and "layout" in error.lower() for error in errors)


def test_layout_zone_without_a_worksheet_is_rejected():
    """A zone in the tree that no worksheet fills would build an empty container."""
    document = _manifest()
    document["layout"]["root"]["children"].append({"id": "chart-orphan", "size": 0.1})

    errors = _errors(document)

    assert any("chart-orphan" in error for error in errors)


def test_object_zone_fills_a_non_worksheet_zone():
    """Not every zone is a view - a filter card / text zone is declared under 'objects'."""
    document = _manifest()
    document["layout"]["root"]["children"].insert(0, {"id": "flt-region", "size": 10})
    document["objects"] = [{
        "element_id": "flt-region", "kind": "filter",
        "field": "region", "worksheet": "Revenue Trend",
    }]

    assert _errors(document) == []


def test_unknown_object_kind_is_rejected():
    """An object kind the builder cannot emit is named with its zone."""
    document = _manifest()
    document["layout"]["root"]["children"].insert(0, {"id": "flt-region", "size": 10})
    document["objects"] = [{"element_id": "flt-region", "kind": "hologram"}]

    errors = _errors(document)

    assert any("hologram" in error and "flt-region" in error for error in errors)


def test_field_not_in_the_data_model_is_rejected():
    """A datasource field absent from DATA-MODEL.md is named with its source."""
    document = _manifest()
    document["datasources"][0]["fields"].append({"name": "margin", "type": "real"})

    errors = _errors(document)

    assert any("margin" in error and "sales_orders.csv" in error for error in errors)


def test_shelf_field_not_declared_is_rejected():
    """A shelf/encoding referencing an undeclared field names the worksheet and field."""
    document = _manifest()
    document["worksheets"][1]["shelves"]["rows"] = ["profit"]

    errors = _errors(document)

    assert any("profit" in error and "Revenue Trend" in error for error in errors)


def test_calculated_field_may_back_a_shelf():
    """Calculated fields are legitimately absent from the data model - declare and use."""
    document = _manifest()
    document["calculated_fields"] = [
        {"name": "Profit Ratio", "formula": "SUM([revenue]) / 2", "datasource": "sales_orders"}
    ]
    document["worksheets"][1]["shelves"]["rows"] = ["Profit Ratio"]

    assert _errors(document) == []


def test_re_aggregating_an_aggregate_calculated_field_is_rejected():
    """Issue #62: Tableau refuses SUM(SUM([revenue]) / 2) at load, and the builder emits the
    un-aggregated instance whatever the key says - so an aggregation asked for here would be
    silently dropped. Caught at validation instead, where the author can fix the manifest."""
    document = _manifest()
    document["calculated_fields"] = [
        {"name": "Profit Ratio", "formula": "SUM([revenue]) / 2", "datasource": "sales_orders"}
    ]
    document["worksheets"][1]["shelves"]["rows"] = [
        {"field": "Profit Ratio", "aggregation": "sum"}
    ]

    errors = _errors(document)

    assert any(
        "Profit Ratio" in error and "already aggregates" in error for error in errors
    ), errors


def test_none_on_an_aggregate_calculated_field_is_accepted():
    """The scope guard: "none" on such a field is the documented way to say "do not
    re-aggregate" (BUILD-MANIFEST-TEMPLATE.md), not a second aggregation."""
    document = _manifest()
    document["calculated_fields"] = [
        {"name": "Profit Ratio", "formula": "SUM([revenue]) / 2", "datasource": "sales_orders"}
    ]
    document["worksheets"][1]["shelves"]["rows"] = [
        {"field": "Profit Ratio", "aggregation": "none"}
    ]

    assert _errors(document) == []


def test_re_aggregating_a_transitively_aggregate_calc_is_rejected():
    """The closure, through validation: 'Doubled' calls no aggregate function itself, but it
    references one that does, so Tableau treats it as an aggregate too."""
    document = _manifest()
    document["calculated_fields"] = [
        {"name": "Profit Ratio", "formula": "SUM([revenue]) / 2",
         "datasource": "sales_orders"},
        {"name": "Doubled", "formula": "[Profit Ratio] * 2", "datasource": "sales_orders"},
    ]
    document["worksheets"][1]["shelves"]["rows"] = [
        {"field": "Doubled", "aggregation": "avg"}
    ]

    errors = _errors(document)

    assert any("Doubled" in error and "already aggregates" in error for error in errors), errors


@pytest.mark.parametrize("modifier,described", [
    ({"date_part": "year"}, "date_part"),
    ({"bin": 0.1}, "bin"),
])
def test_deriving_a_row_level_value_from_an_aggregate_calc_is_rejected(modifier, described):
    """Review of #62: a date part and a bin are both derived from a row-level value, which an
    aggregate calculated field has none of - the same red pill the aggregation rule catches.
    MIN([order_date]) with a 'year' date part validated clean and shipped as [tyr:...]."""
    document = _manifest()
    document["calculated_fields"] = [
        {"name": "First Order", "formula": "MIN([order_date])",
         "datasource": "sales_orders", "type": "date"}
    ]
    document["worksheets"][1]["shelves"]["rows"] = [{"field": "First Order", **modifier}]

    errors = _errors(document)

    assert any(
        "First Order" in error and described in error and "already aggregates" in error
        for error in errors
    ), errors


def test_unknown_datasource_reference_is_rejected():
    """A worksheet pointing at an undeclared datasource is named."""
    document = _manifest()
    document["worksheets"][0]["datasource"] = "inventory"

    errors = _errors(document)

    assert any("inventory" in error and "Revenue KPI" in error for error in errors)


def test_csv_not_in_the_data_model_is_rejected():
    """A datasource whose csv the data model does not document is named."""
    document = _manifest()
    document["datasources"][0]["csv"] = "orders.csv"

    errors = _errors(document)

    assert any("orders.csv" in error for error in errors)


def test_duplicate_worksheet_names_are_rejected():
    """Worksheet names are the workbook's keys - duplicates are ambiguous."""
    document = _manifest()
    document["worksheets"][1]["name"] = "Revenue KPI"

    errors = _errors(document)

    assert any("Revenue KPI" in error and "duplicate" in error.lower() for error in errors)


def test_action_endpoints_must_be_known_elements():
    """An action whose source/target is not a mapped zone is named."""
    document = _manifest()
    document["actions"] = [
        {"name": "Cross-filter", "type": "filter", "source": "chart-nope",
         "targets": ["chart-trend"]}
    ]

    errors = _errors(document)

    assert any("chart-nope" in error and "Cross-filter" in error for error in errors)


def test_unknown_action_type_is_rejected():
    """Action types are the vocabulary of CONTRACT.md §6 - anything else is a typo."""
    document = _manifest()
    document["actions"] = [
        {"name": "Weird", "type": "teleport", "source": "chart-trend",
         "targets": ["kpi-revenue"]}
    ]

    errors = _errors(document)

    assert any("teleport" in error and "Weird" in error for error in errors)


def test_parameter_needs_a_name_and_data_type():
    """A parameter without a data type cannot be emitted - it is named by index."""
    document = _manifest()
    document["parameters"] = [{"name": "Measure"}]

    errors = _errors(document)

    assert any("Measure" in error and "data_type" in error for error in errors)


def test_malformed_layout_tree_is_rejected():
    """The layout tree must be containers and id leaves - a bad node is located."""
    document = _manifest()
    document["layout"]["root"]["type"] = "diagonal"

    errors = _errors(document)

    assert any("diagonal" in error for error in errors)


def test_mapped_container_is_filled_by_its_children():
    """A mapped container (a DZV panel holding zones) needs no view of its own (§1.1)."""
    document = _manifest()
    document["layout"] = _layout([
        {"id": "kpi-revenue", "size": 20},
        {"id": "pnl-detail", "type": "horz", "size": 80,
         "children": [{"id": "chart-trend", "size": 100}]},
    ])

    assert _errors(document) == []


def test_worksheet_claiming_a_mapped_container_is_rejected():
    """A container id is filled by its children - a worksheet cannot claim it too (§1.1)."""
    document = _manifest()
    document["layout"] = _layout([
        {"id": "kpi-revenue", "size": 20},
        {"id": "pnl-detail", "type": "horz", "size": 80,
         "children": [{"id": "chart-trend", "size": 100}]},
    ])
    document["worksheets"].append({
        "name": "Detail Panel", "element_id": "pnl-detail", "chart_type": "text",
        "datasource": "sales_orders", "shelves": {"rows": ["revenue"]},
    })

    errors = _errors(document)

    assert any(
        "pnl-detail" in error and "filled by its children" in error for error in errors
    )
    assert not any("not a zone in the layout tree" in error and "pnl-detail" in error
                   for error in errors)


def test_object_claiming_a_mapped_container_is_rejected():
    """Same rule for an 'objects' entry - a container needs no object of its own (§1.1)."""
    document = _manifest()
    document["layout"] = _layout([
        {"id": "kpi-revenue", "size": 20},
        {"id": "pnl-detail", "type": "horz", "size": 80,
         "children": [{"id": "chart-trend", "size": 100}]},
    ])
    document["objects"] = [{"element_id": "pnl-detail", "kind": "legend"}]

    errors = _errors(document)

    assert any(
        "pnl-detail" in error and "filled by its children" in error for error in errors
    )


def test_null_date_part_is_not_silently_rejected():
    """A JSON null must not coerce to the string 'none' - which is not a valid date_part,
    so it would otherwise fail with the confusing "unknown date_part 'none'" (the same
    ``str(None).lower()`` coercion bug affects 'aggregation', where it goes unnoticed only
    because 'none' happens to already be a valid aggregation value)."""
    document = _manifest()
    document["worksheets"][1]["shelves"]["columns"] = [
        {"field": "order_date", "date_part": None}
    ]

    assert _errors(document) == []


def test_interaction_id_in_the_layout_tree_is_rejected():
    """Actions occupy no zone, so an int-* id in the tree is a translation bug (§1.1)."""
    document = _manifest()
    document["layout"]["root"]["children"].append({"id": "int-cross-filter", "size": 0.1})

    errors = _errors(document)

    assert any("int-cross-filter" in error for error in errors)


def test_parameter_action_targets_a_parameter_not_a_zone():
    """A parameter action targets a declared parameter - not every action targets a zone."""
    document = _manifest()
    document["parameters"] = [
        {"name": "Measure", "data_type": "string", "current_value": "Revenue",
         "values": ["Revenue"]}
    ]
    document["actions"] = [
        {"name": "Pick measure", "type": "parameter", "source": "chart-trend",
         "targets": ["Measure"], "field": "region"}
    ]

    assert _errors(document) == []


def test_parameter_action_with_an_undeclared_target_is_rejected():
    """The parameter it drives must exist, or the action cannot be emitted."""
    document = _manifest()
    document["actions"] = [
        {"name": "Pick measure", "type": "parameter", "source": "chart-trend",
         "targets": ["Measure"]}
    ]

    errors = _errors(document)

    assert any("Measure" in error and "parameter" in error for error in errors)


def test_missing_target_version_is_rejected():
    """The builder needs the workbook version attribute - blank is not "matches"."""
    errors = _errors(target_tableau_version="")

    assert any("target_tableau_version" in error for error in errors)


def test_shelf_entry_may_carry_an_aggregation():
    """Shelves take a bare field or a {field, aggregation, date_part} entry."""
    document = _manifest()
    document["worksheets"][1]["shelves"] = {
        "columns": [{"field": "order_date", "date_part": "month"}],
        "rows": [{"field": "revenue", "aggregation": "sum"}],
    }

    assert _errors(document) == []


def test_aggregated_expression_on_a_shelf_is_rejected_with_the_fix():
    """A construct copied verbatim ('SUM([revenue])') is named, with the entry to use."""
    document = _manifest()
    document["worksheets"][1]["shelves"]["rows"] = ["SUM([revenue])"]

    errors = _errors(document)

    assert any("SUM([revenue])" in error and "aggregation" in error for error in errors)


def test_unknown_aggregation_is_rejected():
    """An aggregation the builder cannot emit is named with its field."""
    document = _manifest()
    document["worksheets"][1]["shelves"]["rows"] = [
        {"field": "revenue", "aggregation": "geomean"}
    ]

    errors = _errors(document)

    assert any("geomean" in error and "revenue" in error for error in errors)


def test_duplicate_datasource_keeps_the_first_fields():
    """A duplicate name is reported once, without corrupting field resolution."""
    document = _manifest()
    document["datasources"].append({
        "name": "sales_orders", "csv": "sales_orders.csv",
        "fields": [{"name": "region", "type": "string"}],
    })

    errors = _errors(document)

    assert any("duplicate" in error.lower() for error in errors)
    # The first entry's fields still resolve: no spurious "not a field" on good worksheets.
    assert not any("not a field" in error for error in errors)


def test_unnamed_datasource_does_not_swallow_bad_references():
    """An unnamed datasource must not register as '' and absorb worksheet references."""
    document = _manifest()
    document["datasources"][0]["name"] = ""
    document["worksheets"][0]["datasource"] = ""

    errors = _errors(document)

    assert any("needs a 'name'" in error for error in errors)
    assert any("Revenue KPI" in error and "unknown datasource ''" in error for error in errors)


def test_documented_fields_reads_a_rendered_data_model():
    """Pin the coupling to tableau-data's renderer, not just a hand-written fixture."""
    import datamodel  # tableau-data's stdlib-only core (on sys.path via conftest.py)

    profile = datamodel.CsvProfile(
        filename="sales_orders.csv",
        row_count=1,
        fields=[
            datamodel.FieldProfile("region", "string", "dimension", ["West"], ""),
            datamodel.FieldProfile("revenue", "real", "measure", ["10.5"], ""),
        ],
    )
    rendered = datamodel.render_data_model([profile], "csv (provided in data/)")

    assert manifest.documented_fields(rendered) == {
        "sales_orders.csv": {"region", "revenue"}
    }


def test_a_stray_table_does_not_donate_field_names():
    """Only the "| Field | Type |" table counts - a sample-rows preview must not."""
    with_preview = DATA_MODEL + (
        "\n| order_date | region |\n|---|---|\n| 2024-01-05 | West |\n"
    )

    assert "2024-01-05" not in manifest.documented_fields(with_preview)["sales_orders.csv"]


def test_unknown_fit_is_rejected():
    """'Entire View' is the label, not the value - a fit Tableau has no zoom for would
    silently leave the sheet on Standard."""
    document = _manifest()
    document["worksheets"][0]["fit"] = "Entire View"

    errors = _errors(document)

    assert any("unknown fit 'Entire View'" in error for error in errors)
    assert any("entire-view" in error for error in errors)


@pytest.mark.parametrize("block,expected", [
    ({"shading": "#FFFFFF"}, None),
    ({"borders": "none", "gridlines": "#EEEEEE"}, None),
    ({"align": "center", "vertical_align": "top"}, None),
    ({"shading": "white"}, "format.shading 'white' is not a '#rrggbb' colour"),
    ({"shading": "none"}, "format.shading 'none' is not a '#rrggbb' colour"),
    ({"gridlines": "off"}, "format.gridlines 'off' is not a '#rrggbb' colour nor 'none'"),
    ({"align": "middle"}, "format.align 'middle' is not an alignment"),
    ({"borders": ""}, "format.borders needs a value"),
    ({"border": "#DDDDDD"}, "unknown format key 'border'"),
])
def test_format_block_values_are_checked(block, expected):
    """A misspelled format key or an unparseable colour is the worst formatting bug: the
    workbook opens, nothing complains, and the sheet simply is not formatted."""
    document = _manifest()
    document["worksheets"][0]["format"] = block

    errors = _errors(document)

    if expected is None:
        assert errors == []
    else:
        assert any(expected in error for error in errors), errors


def test_format_must_be_an_object():
    """A bare colour string on 'format' names no pane to apply it to."""
    document = _manifest()
    document["worksheets"][0]["format"] = "#FFFFFF"

    assert any("'format' must be an object" in error for error in _errors(document))


def test_load_manifest_reports_bad_json(tmp_path):
    """Unparseable JSON fails fast with the file named, not a stack trace."""
    path = tmp_path / build.MANIFEST_FILENAME
    path.write_text("{not json", encoding="utf-8")

    document, error = manifest.load_manifest(path)

    assert document is None and build.MANIFEST_FILENAME in error


# --- Reconciliation with the approved spec (CONTRACT.md §1.1) ----------------

def test_validate_accepts_the_specs_layout_tree(tmp_path):
    """The happy path through the CLI-level validate: manifest carries the spec's tree."""
    _ready_project(tmp_path)
    _write_manifest(tmp_path, _manifest())

    result = build.validate(tmp_path)

    assert result.ok is True and result.errors == []
    assert result.manifest_path == "mock-version/v_1/build-manifest.json"


def test_validate_rejects_a_layout_that_drops_a_spec_zone(tmp_path):
    """A re-derived tree that loses a zone would build a dashboard the analyst never saw."""
    _ready_project(tmp_path)
    document = _manifest()
    document["layout"] = _layout([{"id": "chart-trend", "size": 100}])
    document["worksheets"] = [document["worksheets"][1]]
    _write_manifest(tmp_path, document)

    result = build.validate(tmp_path)

    assert result.ok is False
    assert any("kpi-revenue" in error and "drops" in error for error in result.errors)


def test_validate_rejects_a_layout_that_invents_a_zone(tmp_path):
    """A zone the spec never placed is equally a translation bug."""
    _ready_project(tmp_path)
    document = _manifest()
    document["layout"]["root"]["children"].append({"id": "chart-extra", "size": 10})
    document["worksheets"].append({
        "name": "Extra", "element_id": "chart-extra", "chart_type": "bar",
        "datasource": "sales_orders", "shelves": {"rows": ["revenue"]},
    })
    _write_manifest(tmp_path, document)

    result = build.validate(tmp_path)

    assert result.ok is False
    assert any("chart-extra" in error for error in result.errors)


def test_validate_rejects_a_layout_with_matching_zones_but_different_geometry(tmp_path):
    """Same zone ids as the spec, but a sibling's 'size' differs - the geometry must match too."""
    _ready_project(tmp_path)
    document = _manifest()
    document["layout"] = _layout([
        {"id": "kpi-revenue", "size": 50},  # spec has 20/80, not 50/50
        {"id": "chart-trend", "size": 50},
    ])
    _write_manifest(tmp_path, document)

    result = build.validate(tmp_path)

    assert result.ok is False
    assert any("geometry differs" in error for error in result.errors)


def test_validate_blocked_gate_reports_the_blocker(tmp_path):
    """validate on a closed gate reports the upstream step, not a manifest complaint."""
    result = build.validate(tmp_path)

    assert result.ok is False and "tableau-init" in result.errors[0]


# --- The .twb assembler (twb.py) ---------------------------------------------

CSV_HEADERS = {"sales_orders.csv": ["order_date", "region", "revenue"]}


def _render(document=None, **overrides) -> ET.Element:
    """Assemble a manifest into a .twb and return the parsed ``<workbook>`` root."""
    return ET.fromstring(
        twb.render_workbook(
            _manifest(**overrides) if document is None else document,
            DATA_MODEL,
            CSV_HEADERS,
        )
    )


def test_minimal_manifest_builds_a_well_formed_twb():
    """The floor: the assembler emits parseable XML rooted at <workbook> (AC #1)."""
    root = _render()

    assert root.tag == "workbook"
    assert root.get("source-build") == twb.SOURCE_BUILD


def test_workbook_child_order_is_canonical():
    """Tableau rejects out-of-order children - the sequence is the XSD's, in code."""
    assert [child.tag for child in _render()] == [
        "document-format-change-manifest", "datasources", "worksheets", "dashboards",
        "windows",
    ]


def test_every_column_appears_in_all_four_locations():
    """Missing any one of the four causes silent corruption or a load failure (AC #4)."""
    root = _render()
    expected = set(CSV_HEADERS["sales_orders.csv"])

    relation = {
        column.get("name")
        for column in root.findall(
            "datasources/datasource/connection/relation/columns/column"
        )
    }
    metadata = {
        record.findtext("remote-name")
        for record in root.findall(
            "datasources/datasource/connection/metadata-records/metadata-record"
        )
        if record.get("class") == "column"
    }
    object_graph = {
        column.get("name")
        for column in root.findall(
            "datasources/datasource/object-graph/objects/object/properties/relation/"
            "columns/column"
        )
    }
    ui_columns = {
        column.get("name").strip("[]")
        for column in root.findall("datasources/datasource/column")
        if column.get("datatype") != "table"
    }

    assert relation == metadata == object_graph == ui_columns == expected


def test_the_physical_schema_comes_from_the_csv_not_the_manifest():
    """The manifest lists only the fields the dashboard uses; a partial relation is a
    schema mismatch at load, so the CSV header is what the physical schema follows."""
    document = _manifest()
    document["datasources"][0]["fields"] = [{"name": "revenue", "type": "real"}]
    document["worksheets"][1]["encodings"] = {}
    document["worksheets"][1]["shelves"] = {"rows": ["revenue"]}

    relation = _render(document).findall(
        "datasources/datasource/connection/relation/columns/column"
    )

    assert [column.get("name") for column in relation] == CSV_HEADERS["sales_orders.csv"]


def test_column_types_come_from_the_data_model():
    """DATA-MODEL.md is the field authority (§3): type drives datatype, role, remote-type."""
    root = _render()
    by_name = {
        column.get("name"): column
        for column in root.findall("datasources/datasource/column")
    }

    assert by_name["[revenue]"].get("datatype") == "real"
    assert by_name["[revenue]"].get("role") == "measure"
    assert by_name["[region]"].get("role") == "dimension"
    assert by_name["[order_date]"].get("datatype") == "date"
    assert by_name["[revenue]"].get("caption") == "Revenue"


@pytest.mark.parametrize("field_name, caption", [
    ("order_date", "Order Date"),                     # raw snake_case: title-cased as before
    ("ACV - Current", "ACV - Current"),               # acronym survives
    ("YoY Direction - ACV", "YoY Direction - ACV"),   # mixed case survives
    ("In KPI Window", "In KPI Window"),               # acronym mid-name survives
])
def test_captions_only_title_case_uncased_names(field_name, caption):
    """A name the author already cased is a display name (issue #69): ``.title()`` would
    turn ``ACV`` into ``Acv`` in the Data pane, on pills and in Desktop error messages."""
    assert worksheet.caption_for(field_name) == caption
    assert twb.Column(name=field_name, ordinal=0, datatype="string").caption == caption


def test_a_cased_calculated_field_keeps_its_authored_caption():
    """The path the analyst hit (issue #69): Desktop reported "Acv - Current" - a name
    nobody wrote - because the rendered column caption had been .title()-ed."""
    document = _manifest()
    document["calculated_fields"] = [
        {"name": "ACV - Current", "formula": "SUM([revenue])", "datasource": "sales_orders"}
    ]
    document["worksheets"][1]["shelves"]["rows"] = [
        {"field": "ACV - Current", "aggregation": "none"}
    ]
    root = _render(document)

    column = root.find("datasources/datasource/column[@name='[ACV - Current]']")
    assert column is not None and column.get("caption") == "ACV - Current"


def test_every_documented_type_renders_a_complete_column():
    """All six DATA-MODEL types must map - datetime and boolean are the least attested,
    and a type the map misses would silently fall back to string."""
    data_model = (
        "# Data Model\n\n## Data source: `types.csv`\n\n"
        "| Field | Type |\n|---|---|\n"
        + "".join(f"| f_{name} | {name} |\n" for name in twb.TYPE_FACTS)
    )
    document = _manifest(
        datasources=[{
            "name": "types", "csv": "types.csv",
            "fields": [{"name": f"f_{name}", "type": name} for name in twb.TYPE_FACTS],
        }],
        worksheets=[],
        objects=[
            {"element_id": "kpi-revenue", "kind": "text"},
            {"element_id": "chart-trend", "kind": "image"},
        ],
    )
    header = [f"f_{name}" for name in twb.TYPE_FACTS]

    root = ET.fromstring(twb.render_workbook(document, data_model, {"types.csv": header}))
    records = {
        record.findtext("remote-name"): record
        for record in root.iter("metadata-record")
        if record.get("class") == "column"
    }

    assert set(records) == set(header)
    for type_name, facts in twb.TYPE_FACTS.items():
        record = records[f"f_{type_name}"]
        assert record.findtext("local-type") == type_name
        assert record.findtext("remote-type") == str(facts.remote_type)
        assert record.findtext("aggregation") == facts.aggregation
        # The string trio rides along with text-like types only.
        assert (record.find("collation") is not None) == facts.text_like


def test_an_undocumented_csv_column_still_reaches_the_physical_schema():
    """A column the data model missed must not vanish: an incomplete relation is a schema
    mismatch at load. It falls back to string rather than being dropped."""
    root = ET.fromstring(
        twb.render_workbook(
            _manifest(), DATA_MODEL,
            {"sales_orders.csv": ["order_date", "region", "revenue", "surprise"]},
        )
    )
    columns = root.findall("datasources/datasource/connection/relation/columns/column")

    assert [column.get("name") for column in columns][-1] == "surprise"
    assert columns[-1].get("datatype") == twb.FALLBACK_TYPE


def test_generated_ids_are_unique():
    """Every id in the workbook must be unique or Tableau cross-references the wrong thing."""
    root = _render()
    ids = (
        [element.get("uuid") for element in root.iter("simple-id")]
        + [element.get("name") for element in root.findall("datasources/datasource")]
        + [
            element.get("name")
            for element in root.iter("named-connection")
        ]
    )

    assert len(ids) == len(set(ids))


def test_two_datasources_over_one_csv_still_get_unique_ids():
    """Ids are seeded from the datasource *and* the csv: sharing a CSV must not collide."""
    document = _manifest()
    document["datasources"].append({
        "name": "sales_orders_secondary",
        "csv": "sales_orders.csv",
        "fields": [{"name": "revenue", "type": "real"}],
    })

    root = _render(document)
    connections = [
        element.get("name") for element in root.iter("named-connection")
    ]
    objects = [element.get("id") for element in root.iter("object")]

    assert len(connections) == len(set(connections)) == 2
    assert len(objects) == len(set(objects)) == 2


def test_no_snippet_ids_leak():
    """Ids are generated, never copied from the reference snippets (AC #4)."""
    xml = twb.render_workbook(_manifest(), DATA_MODEL, CSV_HEADERS)

    for snippet_id in (
        "1hckotw0bte0i51b8k3sd1ffpnqc",             # scaffold datasource
        "16xkalt18d1a7p1cjzge51xf66r6",             # scaffold named connection
        "09EB5EA8C4E1488681646EA8C7C1C3B0",         # scaffold object id
        "{8ED4AD55-A43F-4C33-B8C1-A6484D0F1985}",   # scaffold worksheet simple-id
        "{0CB252DB-8C32-4E23-87BF-F5520667C3F4}",   # scaffold dashboard simple-id
        "{5072827F-AB68-407A-8A5C-209EC187C960}",   # scaffold worksheet window
        "{3FC88B5F-B055-44CF-B059-FB779136E3D0}",   # scaffold dashboard window
    ):
        assert snippet_id not in xml


def test_the_same_manifest_rebuilds_byte_identical():
    """Ids are hash-derived, not random: a re-run is a no-op diff."""
    first = twb.render_workbook(_manifest(), DATA_MODEL, CSV_HEADERS)
    second = twb.render_workbook(_manifest(), DATA_MODEL, CSV_HEADERS)

    assert first == second


def test_2025_target_omits_explain_data_and_uses_18_1():
    """The 2024.2-2025.x document format is 18.1 and has no <explain-data> (AC #5)."""
    root = _render(target_tableau_version="2024.2-2025.x")

    assert root.get("version") == "18.1" and root.get("original-version") == "18.1"
    assert root.find("explain-data") is None


def test_2026_target_emits_explain_data_and_26_1():
    """Switching STATE.md's target flips both the version attribute and the element (AC #5)."""
    root = _render(target_tableau_version="2026.1+")

    assert root.get("version") == "26.1" and root.get("original-version") == "26.1"
    explain_data = root.find("explain-data")
    assert explain_data is not None
    assert explain_data.get("enabled-for-viewer") == "false"
    assert [child.tag for child in root][-1] == "explain-data"  # always the last child


def test_relation_is_live_and_local():
    """Never an extract, and the CSV is read from beside the .twb inside the .twbx."""
    root = _render()
    connection = root.find(
        "datasources/datasource/connection/named-connections/named-connection/connection"
    )

    assert list(root.iter("extract")) == []
    assert connection.get("class") == "textscan"
    assert connection.get("directory") == "."
    assert connection.get("filename") == "sales_orders.csv"
    assert root.find("datasources/datasource/connection/relation").get("table") == (
        "[sales_orders#csv]"
    )


def test_every_viewpoint_has_entire_view_zoom():
    """A self-closing viewpoint defaults to 'standard' zoom - the sheet then under-fills."""
    viewpoints = _render().findall("windows/window/viewpoints/viewpoint")

    assert [viewpoint.get("name") for viewpoint in viewpoints] == [
        "Revenue KPI", "Revenue Trend",
    ]
    for viewpoint in viewpoints:
        assert viewpoint.find("zoom").get("type") == "entire-view"


def test_each_worksheet_window_carries_its_own_fit():
    """The sheet's own tab needs the fit too, or the analyst reviews it on Standard (#44).

    The dashboard's viewpoints only govern the embedded copy; a reviewer opening the tab sees
    the worksheet window, and without a zoom there Tableau fits it to nothing.
    """
    fits = {
        window.get("name"): window.find("viewpoint/zoom").get("type")
        for window in _render().findall("windows/window")
        if window.get("class") == "worksheet"
    }

    assert fits == {"Revenue KPI": "entire-view", "Revenue Trend": "entire-view"}


def test_every_worksheet_has_a_matching_window():
    """validate_twb's worksheet<->window check is bidirectional; both sides come from here.

    A window is hidden only when a dashboard zone embeds its sheet: Tableau renders no tab
    for hidden='true', so hiding a sheet nothing shows leaves the analyst a workbook with
    nothing to see."""
    root = _render()
    worksheets = [sheet.get("name") for sheet in root.findall("worksheets/worksheet")]
    windows = [
        window.get("name") for window in root.findall("windows/window")
        if window.get("class") == "worksheet"
    ]

    assert worksheets == windows == ["Revenue KPI", "Revenue Trend"]
    assert [
        window.get("name") for window in root.findall("windows/window")
        if window.get("class") == "worksheet" and window.get("hidden") == "true"
    ] == ["Revenue KPI", "Revenue Trend"]  # both fill a zone in the layout tree


def test_a_sheet_no_dashboard_zone_shows_keeps_its_tab():
    """The other half of the rule: an un-embedded sheet stays reachable through its tab."""
    document = _manifest()
    # A second sheet on the same zone: only one zone exists, so one of them is embedded.
    document["worksheets"].append({
        "name": "Revenue Detail",
        "element_id": "chart-trend",
        "chart_type": "table",
        "datasource": "sales_orders",
        "shelves": {"columns": ["region"], "rows": ["revenue"]},
    })

    hidden = [
        window.get("name") for window in _render(document).findall("windows/window")
        if window.get("class") == "worksheet" and window.get("hidden") == "true"
    ]

    assert "Revenue Detail" not in hidden


def test_dashboard_is_range_sized_above_the_standard_floor():
    """The zone proportions hold at any window size, so the dashboard gets the standard
    minimum and no maximum - not the mock's canvas, which is a design surface: pinning the
    minimum to a 1366px-wide mock would make it unopenable on a smaller laptop."""
    size = _render().find("dashboards/dashboard/size")

    assert size.attrib == {
        "minheight": str(twb.MIN_DASHBOARD_HEIGHT),
        "minwidth": str(twb.MIN_DASHBOARD_WIDTH),
        "sizing-mode": "range",
    }


def test_zero_worksheets_omits_the_worksheets_element():
    """The XSD requires >=1 <worksheet>, so an empty <worksheets> is invalid - omit it."""
    document = _manifest()
    document["worksheets"] = []
    document["objects"] = [
        {"element_id": "kpi-revenue", "kind": "text"},
        {"element_id": "chart-trend", "kind": "image"},
    ]

    root = _render(document)

    assert root.find("worksheets") is None
    assert [child.tag for child in root] == [
        "document-format-change-manifest", "datasources", "dashboards", "windows",
    ]


def test_thumbnails_are_never_emitted():
    """The XSD requires >=1 <thumbnail>; Tableau regenerates them, so omit the element."""
    assert _render().find("thumbnails") is None


def test_worksheets_may_be_empty_when_objects_fill_every_zone():
    """The manifest relaxation behind the zero-worksheet case: no views is a legal
    dashboard as long as every leaf zone is still filled."""
    document = _manifest()
    document["worksheets"] = []
    document["objects"] = [
        {"element_id": "kpi-revenue", "kind": "text"},
        {"element_id": "chart-trend", "kind": "image"},
    ]

    assert _errors(document) == []


def test_an_unfilled_zone_is_still_rejected_with_no_worksheets():
    """The relaxation must not open a hole: a zone nothing fills is still an error."""
    document = _manifest()
    document["worksheets"] = []
    document["objects"] = [{"element_id": "kpi-revenue", "kind": "text"}]

    assert any("chart-trend" in error for error in _errors(document))


# --- Assembly, validation, packaging (build.build_workbook) -------------------

def test_build_writes_a_validated_twb_and_twbx(tmp_path):
    """The end-to-end deliverable: a minimal manifest builds both files (AC #1)."""
    result = _built_project(tmp_path)

    assert result.ok is True, result.errors
    assert (tmp_path / build.VERSION_DIR / "v_1" / build.TWB_FILENAME).exists()
    assert (tmp_path / build.VERSION_DIR / "v_1" / build.WORKBOOK_FILENAME).exists()


def test_a_zero_worksheet_manifest_builds_and_packages(tmp_path):
    """AC #1 literally: one CSV datasource, zero worksheets, one empty dashboard."""
    _ready_project(tmp_path)
    document = _manifest()
    document["worksheets"] = []
    document["objects"] = [
        {"element_id": "kpi-revenue", "kind": "text"},
        {"element_id": "chart-trend", "kind": "image"},
    ]
    _write_manifest(tmp_path, document)

    result = build.build_workbook(tmp_path)

    assert result.ok is True, result.errors
    assert (tmp_path / build.VERSION_DIR / "v_1" / build.WORKBOOK_FILENAME).exists()


def test_twbx_contains_the_twb_and_every_csv(tmp_path):
    """Packaging is flat, so directory='.' resolves each CSV beside the .twb (AC #1)."""
    _built_project(tmp_path)

    with zipfile.ZipFile(
        tmp_path / build.VERSION_DIR / "v_1" / build.WORKBOOK_FILENAME
    ) as archive:
        assert sorted(archive.namelist()) == ["dashboard.twb", "sales_orders.csv"]


def test_semantic_validator_passes_on_the_generated_twb(tmp_path):
    """The migrated breakage-only validator is green on what the assembler emits (AC #2)."""
    import validate_twb  # migrated into the skill by this ticket

    _built_project(tmp_path)

    report = validate_twb.TwbValidator(
        str(tmp_path / build.VERSION_DIR / "v_1" / build.TWB_FILENAME)
    ).validate()

    assert report.passed is True, [
        (result.name, result.details) for result in report.results if not result.passed
    ]


def test_xsd_reports_at_most_the_documented_version_shift(tmp_path):
    """2026.1 validates clean; 2025.x differs only by the required <explain-data> (AC #2)."""
    pytest.importorskip("lxml")
    import validate_twb_xsd  # migrated into the skill by this ticket

    schema = validate_twb_xsd.load_schema(validate_twb_xsd.XSD_PATH)

    _built_project(tmp_path)
    _, errors_2025 = validate_twb_xsd.validate(
        tmp_path / build.VERSION_DIR / "v_1" / build.TWB_FILENAME, schema
    )
    assert len(errors_2025) <= 1
    assert all("explain-data" in error.message for error in errors_2025)

    newer = tmp_path / "newer"
    newer.mkdir()
    _ready_project(newer, target="2026.1+")
    _write_manifest(newer, _manifest(target_tableau_version="2026.1+"))
    assert build.build_workbook(newer).ok is True

    passed, errors_2026 = validate_twb_xsd.validate(
        newer / build.VERSION_DIR / "v_1" / build.TWB_FILENAME, schema
    )
    assert passed is True and errors_2026 == []


def test_build_reports_the_version_shift_as_a_warning_not_an_error(tmp_path):
    """The 2025.x XSD complaint is expected - it must not read as a broken workbook."""
    pytest.importorskip("lxml")

    result = _built_project(tmp_path)

    assert result.ok is True
    assert any("explain-data" in warning for warning in result.warnings)


def test_build_refuses_an_invalid_manifest(tmp_path):
    """No XML is generated from a manifest that does not validate."""
    _ready_project(tmp_path)
    document = _manifest()
    document["worksheets"][1]["chart_type"] = "donut"
    _write_manifest(tmp_path, document)

    result = build.build_workbook(tmp_path)

    assert result.ok is False
    assert any("donut" in error for error in result.errors)
    assert not (tmp_path / build.VERSION_DIR / "v_1" / build.TWB_FILENAME).exists()


def test_build_refuses_a_csv_that_is_not_on_disk(tmp_path):
    """A datasource with no CSV would build a workbook bound to nothing."""
    _ready_project(tmp_path)
    _write_manifest(tmp_path, _manifest())
    (tmp_path / "data" / "sales_orders.csv").rename(tmp_path / "data" / "orders.csv")

    result = build.build_workbook(tmp_path)

    assert result.ok is False
    assert any("sales_orders.csv" in error for error in result.errors)


def test_a_failed_build_removes_the_previous_package(tmp_path):
    """commit approves on the .twbx's existence, so a failed rebuild must not leave the
    last good package behind for it to approve."""
    _built_project(tmp_path)
    (tmp_path / "data" / "sales_orders.csv").rename(tmp_path / "data" / "orders.csv")

    assert build.build_workbook(tmp_path).ok is False
    assert not (tmp_path / build.VERSION_DIR / "v_1" / build.WORKBOOK_FILENAME).exists()
    assert build.commit(tmp_path).ok is False


def test_only_the_referenced_csvs_are_packaged(tmp_path):
    """An unrelated CSV in data/ has no business inside the analyst's deliverable."""
    _ready_project(tmp_path)
    (tmp_path / "data" / "unrelated.csv").write_text("a\n1\n", encoding="utf-8")
    _write_manifest(tmp_path, _manifest())

    assert build.build_workbook(tmp_path).ok is True

    with zipfile.ZipFile(
        tmp_path / build.VERSION_DIR / "v_1" / build.WORKBOOK_FILENAME
    ) as archive:
        assert "unrelated.csv" not in archive.namelist()


# --- The validation gate (issue #38) ------------------------------------------

def test_the_gate_runs_all_three_validators(tmp_path):
    """One gate, one verdict, and the conformance validator is part of it."""
    _built_project(tmp_path)
    version_dir = tmp_path / build.VERSION_DIR / "v_1"
    document = json.loads(
        (version_dir / build.MANIFEST_FILENAME).read_text(encoding="utf-8")
    )

    report = build.run_gate(
        version_dir / build.TWB_FILENAME, document, TARGET_VERSION
    )

    assert set(report.results) == set(build.GATE_VALIDATORS)
    assert report.ok is True, report.errors


def test_a_nonconforming_workbook_is_never_packaged(tmp_path, monkeypatch):
    """AC #2: the gate catches it before the .twbx exists, and names the validator."""
    import validate_conformance

    monkeypatch.setattr(
        validate_conformance, "conformance_errors",
        lambda root, document: ["layout element 'kpi-revenue' has no zone"],
    )

    result = _built_project(tmp_path)

    assert result.ok is False
    assert any("conformance" in error for error in result.errors)
    assert (tmp_path / build.VERSION_DIR / "v_1" / build.TWB_FILENAME).exists()
    assert not (tmp_path / build.VERSION_DIR / "v_1" / build.WORKBOOK_FILENAME).exists()


def test_gate_revalidates_and_repackages_the_workbook_on_disk(tmp_path):
    """The revalidate half of the fix loop, after a hand-written block is added."""
    _built_project(tmp_path)
    package = tmp_path / build.VERSION_DIR / "v_1" / build.WORKBOOK_FILENAME
    package.unlink()

    result = build.gate(tmp_path)

    assert result.ok is True, result.errors
    assert package.exists()


def test_gate_refuses_a_hand_edited_workbook_that_breaks_conformance(tmp_path):
    """Hand-writing XML is offered, but only the gate decides whether it ships.

    Renaming a sheet everywhere it appears leaves the XML *internally* consistent, so the
    other two validators pass it - only the manifest knows the sheet is meant to be there.
    """
    _built_project(tmp_path)
    twb_path = tmp_path / build.VERSION_DIR / "v_1" / build.TWB_FILENAME
    twb_path.write_text(
        twb_path.read_text(encoding="utf-8").replace(
            '"Revenue Trend"', '"Ghost Sheet"'
        ),
        encoding="utf-8",
    )

    result = build.gate(tmp_path)

    assert result.ok is False
    assert any("conformance" in error for error in result.errors), result.errors
    assert not (tmp_path / build.VERSION_DIR / "v_1" / build.WORKBOOK_FILENAME).exists()


def test_gate_refuses_a_csv_that_is_no_longer_on_disk(tmp_path):
    """The gate packages too, so it owes the same guard: a .twbx short a CSV opens bound to
    nothing, and commit would approve it on the package's existence."""
    _built_project(tmp_path)
    (tmp_path / "data" / "sales_orders.csv").rename(tmp_path / "data" / "orders.csv")

    result = build.gate(tmp_path)

    assert result.ok is False
    assert any("sales_orders.csv" in error for error in result.errors)
    assert not (tmp_path / build.VERSION_DIR / "v_1" / build.WORKBOOK_FILENAME).exists()
    assert build.commit(tmp_path).ok is False


def test_a_failure_before_the_gate_is_not_reported_as_a_failed_gate(tmp_path):
    """Sending the analyst to the XML for a manifest problem costs them the real fix."""
    _ready_project(tmp_path)
    document = _manifest()
    document["worksheets"][1]["chart_type"] = "donut"
    _write_manifest(tmp_path, document)

    result = build.build_workbook(tmp_path)

    assert result.gated is False
    assert "validation gate failed" not in build.format_build(result)


def test_a_gate_failure_says_so(tmp_path):
    _built_project(tmp_path)
    twb_path = tmp_path / build.VERSION_DIR / "v_1" / build.TWB_FILENAME
    twb_path.write_text(
        twb_path.read_text(encoding="utf-8").replace('"Revenue KPI"', '"Ghost"'),
        encoding="utf-8",
    )

    result = build.gate(tmp_path)

    assert result.gated is True
    assert "validation gate failed" in build.format_build(result)


def test_gate_refuses_when_no_workbook_has_been_built(tmp_path):
    _ready_project(tmp_path)
    _write_manifest(tmp_path, _manifest())

    result = build.gate(tmp_path)

    assert result.ok is False
    assert any(build.TWB_FILENAME in error for error in result.errors)


def test_an_unsupported_construct_is_named_and_the_rest_still_builds(tmp_path):
    """AC #3: the build refuses the piece, not the workbook, and offers the way forward."""
    _ready_project(tmp_path)
    document = _manifest()
    document["layout"]["root"]["children"].append({"id": "logo", "size": 10})
    document["objects"] = [{"element_id": "logo", "kind": "image"}]
    _write_spec(tmp_path, text=_spec_md(document["layout"]))
    _write_manifest(tmp_path, document)

    result = build.build_workbook(tmp_path)

    assert result.ok is True, result.errors
    gap = next(warning for warning in result.warnings if "logo" in warning)
    assert "image" in gap and ".twb" in gap and "hand-write" in gap
    # The rest of the deliverable is untouched by the refused piece.
    assert (tmp_path / build.VERSION_DIR / "v_1" / build.WORKBOOK_FILENAME).exists()


def test_the_gate_refuses_to_run_without_lxml(tmp_path, monkeypatch):
    """Issue #68: a validator that can silently not run is not a validator. Without lxml
    the XSD check cannot execute, so the gate fails instead of reporting green degraded."""
    real_find_spec = build.importlib.util.find_spec
    monkeypatch.setattr(
        build.importlib.util, "find_spec",
        lambda name, *args: None if name == "lxml" else real_find_spec(name, *args),
    )

    result = _built_project(tmp_path)

    assert result.ok is False
    assert any("lxml" in error for error in result.errors)
    assert any("pip install lxml" in error for error in result.errors)
    # The absence is never a mere warning, and nothing is packaged for commit to approve.
    assert not any("lxml" in warning for warning in result.warnings)
    assert "[BUILT]" not in build.format_build(result)
    assert build.commit(tmp_path).ok is False


# --- STATE.md transition (CONTRACT.md §4.3) ----------------------------------

def test_commit_refuses_without_a_workbook(tmp_path):
    """The deliverable is the workbook: a validated manifest alone does not approve it."""
    _ready_project(tmp_path)
    _write_manifest(tmp_path, _manifest())

    result = build.commit(tmp_path)

    assert result.ok is False
    assert build.WORKBOOK_FILENAME in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["build"] == "pending"


def test_commit_refuses_without_a_manifest(tmp_path):
    """No manifest => nothing to build; STATE.md is untouched."""
    _ready_project(tmp_path)

    result = build.commit(tmp_path)

    assert result.ok is False
    assert build.MANIFEST_FILENAME in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["build"] == "pending"


def test_commit_refuses_an_invalid_manifest(tmp_path):
    """An invalid manifest blocks approval and reports the offending entry."""
    _ready_project(tmp_path)
    document = _manifest()
    document["worksheets"][1]["chart_type"] = "donut"
    _write_manifest(tmp_path, document)

    result = build.commit(tmp_path)

    assert result.ok is False
    assert any("donut" in error for error in result.errors)
    assert route.parse_state(tmp_path / "STATE.md").statuses["build"] == "pending"


def test_commit_refuses_when_the_entry_gate_is_closed(tmp_path):
    """Commit is gated like every other entrypoint: an unresolved producer refuses it."""
    _built_project(tmp_path)
    _state_with(tmp_path, spec="pending", data="approved")

    result = build.commit(tmp_path)

    assert result.ok is False
    assert "spec" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["build"] == "pending"


def test_commit_approves_and_leaves_current_version_alone(tmp_path):
    """A valid manifest approves build in place - current_version is never bumped (§4.3)."""
    _built_project(tmp_path)

    result = build.commit(tmp_path)

    assert result.ok is True and result.version == "v_1"
    state = route.parse_state(tmp_path / "STATE.md")
    assert state.statuses["build"] == "approved"
    assert state.current_version == "v_1"


def test_commit_touches_no_other_step(tmp_path):
    """Build is last, so nothing goes stale: every other status survives the commit (§4.2)."""
    _built_project(tmp_path, intake="approved", brand="skipped", plan="approved",
                   mock="approved")
    before = route.parse_state(tmp_path / "STATE.md").statuses

    assert build.commit(tmp_path).ok is True

    after = route.parse_state(tmp_path / "STATE.md").statuses
    assert after["build"] == "approved"
    assert (
        {step: status for step, status in after.items() if step != "build"}
        == {step: status for step, status in before.items() if step != "build"}
    )


def test_recommit_overwrites_in_place(tmp_path):
    """Re-running build after approval overwrites the same v_N; no new version dir (§4.3)."""
    _built_project(tmp_path)
    build.commit(tmp_path)

    result = build.commit(tmp_path)

    assert result.ok is True and result.version == "v_1"
    assert route.parse_state(tmp_path / "STATE.md").current_version == "v_1"
    assert sorted(p.name for p in (tmp_path / build.VERSION_DIR).iterdir()) == ["v_1"]


def test_commit_from_stale_reapproves_the_same_version(tmp_path):
    """The realistic re-run: mock bumped and staled build, which rebuilds into that v_N."""
    _built_project(tmp_path, build="stale")

    result = build.commit(tmp_path)

    assert result.ok is True and result.version == "v_1"
    assert route.parse_state(tmp_path / "STATE.md").statuses["build"] == "approved"


def test_router_reports_done_after_build_is_approved(tmp_path):
    """Cross-check through the router: build approved completes the pipeline."""
    _built_project(tmp_path, intake="approved", brand="skipped", plan="approved",
                   mock="approved")
    (tmp_path / "DASHBOARD-PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / build.VERSION_DIR / "v_1" / "mock.html").write_text(
        "<html></html>", encoding="utf-8"
    )

    assert build.commit(tmp_path).ok is True

    assert route.compute_next_step(tmp_path).is_done is True


def test_a_non_boolean_legend_key_is_rejected():
    """"legend": "false" is a truthy string - reading it as opt-in would silently keep the
    zone the analyst asked to drop (issue #65)."""
    document = _manifest()
    document["worksheets"][0]["legend"] = "false"

    assert any("'legend' must be true or false" in error for error in _errors(document))


def test_a_boolean_legend_key_is_accepted():
    """Both values are legal; false is the opt-out, true is the documented default."""
    for value in (True, False):
        document = _manifest()
        document["worksheets"][0]["legend"] = value

        assert _errors(document) == []
