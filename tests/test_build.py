"""Contract test for tableau-build (CONTRACT.md step 8, §4.1, §4.3).

``tableau-build`` turns the approved ``IMPLEMENTATION-SPEC.md`` + ``DATA-MODEL.md`` into a
Tableau workbook. The XML generation is the next ticket; what this test pins is the
skeleton the builder stands on, split across the two modules:

* :mod:`manifest` (pure core) - the **build manifest**: the machine-readable JSON an agent
  derives from the spec + data model that the deterministic builder consumes. Validation is
  fail-fast and names the offending entry, so a bad spec-to-manifest translation is caught
  before any XML is generated.
* :mod:`build` (orchestration) - the entry gate (§4.1: ``spec`` and ``data`` resolved with
  their artifacts on disk) and the STATE.md transition (``build`` -> ``approved``, never
  touching ``current_version``, overwriting in the current ``v_N``, §4.3).
"""

import json
from pathlib import Path

import build  # the orchestration under test
import init  # builds a realistic STATE.md the same way a real project would
import manifest  # the pure manifest-schema core (on sys.path via conftest.py)
import route  # the router parses/routes the STATE.md build writes

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


def _state_with(project_dir: Path, **status_overrides: str) -> None:
    """Write a canonical STATE.md, optionally overriding some step statuses."""
    text = init.render_state_md(TARGET_VERSION)
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


def _ready_project(project_dir: Path, **status_overrides: str) -> None:
    """Open build's entry gate: spec+data resolved with all three artifacts on disk."""
    _state_with(project_dir, **{"spec": "approved", "data": "approved", **status_overrides})
    (project_dir / "DATA-MODEL.md").write_text(DATA_MODEL, encoding="utf-8")
    data_dir = project_dir / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "sales_orders.csv").write_text(
        "order_date,region,revenue\n2024-01-05,West,1200.5\n", encoding="utf-8"
    )
    _write_spec(project_dir)


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
    document["objects"] = [{"element_id": "flt-region", "kind": "filter"}]

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
        {"name": "Measure", "data_type": "string", "allowed_values": ["Revenue"]}
    ]
    document["actions"] = [
        {"name": "Pick measure", "type": "parameter", "source": "chart-trend",
         "targets": ["Measure"]}
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


# --- STATE.md transition (CONTRACT.md §4.3) ----------------------------------

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


def test_commit_approves_and_leaves_current_version_alone(tmp_path):
    """A valid manifest approves build in place - current_version is never bumped (§4.3)."""
    _ready_project(tmp_path)
    _write_manifest(tmp_path, _manifest())

    result = build.commit(tmp_path)

    assert result.ok is True and result.version == "v_1"
    state = route.parse_state(tmp_path / "STATE.md")
    assert state.statuses["build"] == "approved"
    assert state.current_version == "v_1"


def test_recommit_overwrites_in_place(tmp_path):
    """Re-running build after approval overwrites the same v_N; no new version dir (§4.3)."""
    _ready_project(tmp_path)
    _write_manifest(tmp_path, _manifest())
    build.commit(tmp_path)

    result = build.commit(tmp_path)

    assert result.ok is True and result.version == "v_1"
    assert route.parse_state(tmp_path / "STATE.md").current_version == "v_1"
    assert sorted(p.name for p in (tmp_path / build.VERSION_DIR).iterdir()) == ["v_1"]


def test_commit_from_stale_reapproves_the_same_version(tmp_path):
    """The realistic re-run: mock bumped and staled build, which rebuilds into that v_N."""
    _ready_project(tmp_path, build="stale")
    _write_manifest(tmp_path, _manifest())

    result = build.commit(tmp_path)

    assert result.ok is True and result.version == "v_1"
    assert route.parse_state(tmp_path / "STATE.md").statuses["build"] == "approved"


def test_router_reports_done_after_build_is_approved(tmp_path):
    """Cross-check through the router: build approved completes the pipeline."""
    _ready_project(tmp_path, intake="approved", brand="skipped", plan="approved",
                   mock="approved")
    (tmp_path / "DASHBOARD-PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / build.VERSION_DIR / "v_1" / "mock.html").write_text(
        "<html></html>", encoding="utf-8"
    )
    _write_manifest(tmp_path, _manifest())

    assert build.commit(tmp_path).ok is True

    assert route.compute_next_step(tmp_path).is_done is True
