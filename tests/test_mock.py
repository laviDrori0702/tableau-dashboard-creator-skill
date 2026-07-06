"""Contract test for tableau-mock (CONTRACT.md step 6, §4.1, §4.2, §4.3).

``tableau-mock`` turns the strict ``DASHBOARD-PLAN.md`` into an interactive ``mock.html``
demo. The markup is model-authored, so this test pins the parts the scripts must guarantee
mechanically, split across the two modules:

* :mod:`coverage` (pure core) - parsing the plan into its coverage set, matching that
  against what the mock rendered (``data-plan-id``), the layout manifest, and the
  slot-sizing guard (out-of-bounds / compressed / empty-space-heavy);
* :mod:`mock` (orchestration) - the entry gate (§4.1), deliverable versioning (§4.3),
  and the STATE.md ``approved`` + downstream-``stale`` transition (§4.2), cross-checked
  through the *router* so the manifest mock writes actually routes onward.
"""

from pathlib import Path

import coverage  # the pure coverage/guard core (on sys.path via conftest.py)
import init  # builds a realistic STATE.md the same way a real project would
import mock  # the orchestration under test
import route  # the router parses/routes the STATE.md mock writes

TARGET_VERSION = "2024.2-2025.x"

# A schema-complete plan mirroring test_plan's: canvas 1366x768, 2 elements, 1 filter,
# 1 interaction. parse_plan_coverage categorises the id-tables by their headers.
FULL_PLAN = (
    "# Dashboard Plan: Sales\n\n"
    "## Screen Size\n- mode: fixed\n- dimensions: 1366 x 768 px\n\n"
    "## Elements\n"
    "| id          | type       | columns      | slot    | size       |\n"
    "|-------------|------------|--------------|---------|------------|\n"
    "| kpi-revenue | kpi        | revenue      | kpi-row | 1/4 of row |\n"
    "| chart-trend | chart:line | order_date   | chart-a | fills slot |\n\n"
    "## Filters\n"
    "| id         | field  | control type     | scope | default |\n"
    "|------------|--------|------------------|-------|---------|\n"
    "| flt-region | region | dropdown (multi) | all   | All     |\n\n"
    "## Interactions\n"
    "| id                | interaction  | source      | target      | detail        |\n"
    "|-------------------|--------------|-------------|-------------|---------------|\n"
    "| int-region-filter | cross-filter | chart-trend | kpi-revenue | click filters |\n"
)


def _mock_html(
    ids=("kpi-revenue", "chart-trend", "flt-region", "int-region-filter"),
    canvas=(1366, 768),
    boxes=(
        {"id": "kpi-revenue", "x": 0, "y": 0, "width": 1366, "height": 300},
        {"id": "chart-trend", "x": 0, "y": 300, "width": 1366, "height": 400},
    ),
) -> str:
    """Render a mock.html that covers ``FULL_PLAN`` (override args to break one thing).

    Args:
        ids: Plan ids to tag with ``data-plan-id`` (drop one to force a coverage gap).
        canvas: The manifest ``(width, height)`` (mismatch to fail screen-size coverage).
        boxes: Element geometry boxes for the layout manifest.

    Returns:
        A self-contained mock.html string.
    """
    tagged = "\n".join(f'<div data-plan-id="{plan_id}"></div>' for plan_id in ids)
    manifest = {"canvas": {"width": canvas[0], "height": canvas[1]}, "elements": list(boxes)}
    import json

    return (
        f"<!doctype html><html><body>\n{tagged}\n"
        f'<script type="application/json" id="{coverage.LAYOUT_MANIFEST_ID}">'
        f"{json.dumps(manifest)}</script>\n</body></html>"
    )


def _state_with(project_dir: Path, **status_overrides: str) -> None:
    """Write a canonical STATE.md, optionally overriding some step statuses.

    Args:
        project_dir: Directory to write ``STATE.md`` into.
        **status_overrides: ``step=status`` pairs to apply on top of a fresh manifest.
    """
    text = init.render_state_md(TARGET_VERSION)
    if status_overrides:
        text = mock.apply_status_updates(text, dict(status_overrides))
    (project_dir / "STATE.md").write_text(text, encoding="utf-8")


def _ready_project(project_dir: Path, **status_overrides: str) -> None:
    """Open mock's entry gate: plan+data resolved, DASHBOARD-PLAN.md and a CSV on disk.

    Args:
        project_dir: The project directory to populate.
        **status_overrides: Extra ``step=status`` overrides (merged after plan/data).
    """
    _state_with(project_dir, plan="approved", data="approved", **status_overrides)
    (project_dir / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")
    data_dir = project_dir / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "x.csv").write_text("region,revenue\nWest,10\n", encoding="utf-8")


def _write_mock(project_dir: Path, version: str, html: str) -> None:
    """Write ``html`` to ``mock-version/<version>/mock.html`` under the project."""
    version_dir = project_dir / mock.VERSION_DIR / version
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / mock.MOCK_FILENAME).write_text(html, encoding="utf-8")


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def test_precheck_blocks_when_no_state(tmp_path):
    """No STATE.md => mock cannot run; the blocker points at tableau-init."""
    result = mock.precheck(tmp_path)

    assert result.can_run is False
    assert "tableau-init" in result.blocker


def test_precheck_blocks_when_plan_not_resolved(tmp_path):
    """plan must be resolved before mock runs (§4.1)."""
    _state_with(tmp_path, plan="pending", data="approved")
    (tmp_path / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")

    result = mock.precheck(tmp_path)

    assert result.can_run is False
    assert "plan" in result.blocker and "pending" in result.blocker


def test_precheck_blocks_when_plan_file_missing(tmp_path):
    """Even with plan approved, a missing DASHBOARD-PLAN.md on disk blocks mock (§4.1)."""
    _state_with(tmp_path, plan="approved", data="approved")  # no plan file written

    result = mock.precheck(tmp_path)

    assert result.can_run is False
    assert "DASHBOARD-PLAN.md" in result.blocker


def test_precheck_blocks_when_data_not_resolved(tmp_path):
    """data must be resolved too - mock populates the demo from the sample CSVs (§4.1)."""
    _state_with(tmp_path, plan="approved", data="pending")
    (tmp_path / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")

    result = mock.precheck(tmp_path)

    assert result.can_run is False
    assert "data" in result.blocker and "pending" in result.blocker


def test_precheck_blocks_when_no_csv(tmp_path):
    """With data resolved but no CSV on disk, the gate stays closed (§4.1)."""
    _state_with(tmp_path, plan="approved", data="approved")
    (tmp_path / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")

    result = mock.precheck(tmp_path)

    assert result.can_run is False
    assert "CSV" in result.blocker


def test_scaffold_sample_csv_satisfies_the_data_gate(tmp_path):
    """The scaffold/sample-data/ demo CSV is an accepted fallback for the data gate (§3.1)."""
    _state_with(tmp_path, plan="approved", data="approved")
    (tmp_path / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")
    sample_dir = tmp_path / "scaffold" / "sample-data"
    sample_dir.mkdir(parents=True)
    (sample_dir / "demo.csv").write_text("region,revenue\nWest,10\n", encoding="utf-8")

    assert mock.precheck(tmp_path).can_run is True


def test_optional_steps_do_not_gate_mock(tmp_path):
    """intake/brand are optional reads: mock runs with them pending (§1)."""
    _ready_project(tmp_path, intake="pending", brand="pending")

    assert mock.precheck(tmp_path).can_run is True


# --- Precheck signals --------------------------------------------------------

def test_precheck_reports_canvas_and_coverage_targets(tmp_path):
    """precheck surfaces the canvas size and element/filter/interaction counts to render."""
    _ready_project(tmp_path)

    result = mock.precheck(tmp_path)

    assert result.can_run is True
    assert result.canvas == (1366, 768)
    assert result.element_count == 2
    assert result.filter_count == 1
    assert result.interaction_count == 1


def test_precheck_reports_target_version_path_and_rerun(tmp_path):
    """First run targets v_1 (no bump); a post-approval re-run bumps to v_2 (§4.3)."""
    _ready_project(tmp_path)
    first = mock.precheck(tmp_path)
    assert first.target_version == "v_1"
    assert first.target_path == "mock-version/v_1/mock.html"
    assert first.is_rerun_after_approval is False
    assert first.mock_exists is False

    # Simulate a prior approval: mock approved, v_1 on disk.
    _ready_project(tmp_path, mock="approved")
    _write_mock(tmp_path, "v_1", _mock_html())
    rerun = mock.precheck(tmp_path)
    assert rerun.target_version == "v_2"
    assert rerun.is_rerun_after_approval is True


def test_precheck_reports_design_tokens_presence(tmp_path):
    """precheck surfaces whether DESIGN-TOKENS.md is available (optional read)."""
    _ready_project(tmp_path)
    assert mock.precheck(tmp_path).tokens_present is False

    (tmp_path / "DESIGN-TOKENS.md").write_text("## Colors\n", encoding="utf-8")
    assert mock.precheck(tmp_path).tokens_present is True


# --- Plan coverage parsing (coverage.py) -------------------------------------

def test_parse_plan_coverage_categorises_id_tables():
    """The plan's id-tables are split into elements/filters/interactions by their headers."""
    spec = coverage.parse_plan_coverage(FULL_PLAN)

    assert spec.canvas == (1366, 768)
    assert spec.element_ids == ["kpi-revenue", "chart-trend"]
    assert spec.filter_ids == ["flt-region"]
    assert spec.interaction_ids == ["int-region-filter"]
    assert spec.all_ids == [
        "kpi-revenue", "chart-trend", "flt-region", "int-region-filter",
    ]


def test_parse_plan_coverage_drops_none_sentinel():
    """A ``none`` sentinel row (no-filters/no-interactions) is not counted as coverage."""
    no_filters = FULL_PLAN.replace(
        "| flt-region | region | dropdown (multi) | all   | All     |",
        "| none       | -      | -                | -     | -       |",
    )

    spec = coverage.parse_plan_coverage(no_filters)

    assert spec.filter_ids == []


def test_rendered_plan_ids_reads_data_plan_id_attrs():
    """Every ``data-plan-id`` value in the markup is what coverage matches against."""
    assert coverage.rendered_plan_ids(_mock_html()) == {
        "kpi-revenue", "chart-trend", "flt-region", "int-region-filter",
    }


def test_parse_layout_manifest_returns_none_on_missing_or_bad_json():
    """A missing or non-JSON manifest block parses to None (guard then reports it)."""
    assert coverage.parse_layout_manifest("<html></html>") is None
    bad = (
        f'<script type="application/json" id="{coverage.LAYOUT_MANIFEST_ID}">'
        "{not json}</script>"
    )
    assert coverage.parse_layout_manifest(bad) is None


# --- validate_mock: the coverage checklist + guard ---------------------------

def test_validate_full_mock_passes():
    """A mock rendering every id, matching canvas, with readable full-canvas boxes is OK."""
    validation = coverage.validate_mock(FULL_PLAN, _mock_html())

    assert validation.ok is True
    assert validation.gaps == []
    assert validation.guard_violations == []
    assert validation.missing_boxes == []


def test_validate_flags_coverage_gap_for_unrendered_id():
    """A plan id absent from the markup is a coverage gap that fails validation."""
    html = _mock_html(ids=("kpi-revenue", "chart-trend", "flt-region"))  # drops interaction

    validation = coverage.validate_mock(FULL_PLAN, html)

    assert validation.ok is False
    assert any(item.label == "int-region-filter" for item in validation.gaps)


def test_validate_flags_screen_size_mismatch():
    """A manifest canvas that differs from the plan's Screen Size fails screen-size coverage."""
    validation = coverage.validate_mock(FULL_PLAN, _mock_html(canvas=(1280, 720)))

    assert validation.ok is False
    assert any(
        item.kind == "screen size" and not item.rendered for item in validation.coverage
    )


def test_validate_flags_missing_element_box():
    """An element with a data-plan-id but no geometry box is a missing box (blocks approval)."""
    html = _mock_html(
        boxes=({"id": "kpi-revenue", "x": 0, "y": 0, "width": 1366, "height": 700},),
    )  # chart-trend has no box

    validation = coverage.validate_mock(FULL_PLAN, html)

    assert validation.ok is False
    assert "chart-trend" in validation.missing_boxes


def test_guard_flags_out_of_bounds_box():
    """A box escaping the canvas is an out-of-bounds guard violation."""
    html = _mock_html(
        boxes=(
            {"id": "kpi-revenue", "x": 0, "y": 0, "width": 1366, "height": 300},
            {"id": "chart-trend", "x": 0, "y": 300, "width": 1366, "height": 500},  # 800>768
        ),
    )

    validation = coverage.validate_mock(FULL_PLAN, html)

    assert validation.ok is False
    assert any("out-of-bounds" in v for v in validation.guard_violations)


def test_guard_flags_compressed_box():
    """A box below the readable minimum size is a 'compressed' guard violation."""
    html = _mock_html(
        boxes=(
            {"id": "kpi-revenue", "x": 0, "y": 0, "width": 1366, "height": 700},
            {"id": "chart-trend", "x": 0, "y": 700, "width": 40, "height": 20},  # tiny
        ),
    )

    validation = coverage.validate_mock(FULL_PLAN, html)

    assert validation.ok is False
    assert any("compressed" in v for v in validation.guard_violations)


def test_guard_flags_empty_space_heavy_layout():
    """Element boxes covering below the fill ratio flag an empty-space-heavy layout."""
    html = _mock_html(
        boxes=(
            {"id": "kpi-revenue", "x": 0, "y": 0, "width": 100, "height": 100},
            {"id": "chart-trend", "x": 0, "y": 120, "width": 100, "height": 100},
        ),
    )  # ~20k px of a 1.05M canvas => ~2%

    validation = coverage.validate_mock(FULL_PLAN, html)

    assert validation.ok is False
    assert any("empty-space-heavy" in v for v in validation.guard_violations)


# --- Versioning helpers (CONTRACT.md §4.3) -----------------------------------

def test_target_version_bumps_only_after_approval():
    """Re-running after approval bumps the version; before approval it overwrites."""
    assert mock.target_version("v_1", "pending") == "v_1"
    assert mock.target_version("v_1", "approved") == "v_2"
    assert mock.bump_version("v_3") == "v_4"


def test_set_and_read_current_version_round_trip():
    """set_current_version rewrites the metadata line read_current_version reads back."""
    text = init.render_state_md(TARGET_VERSION)
    assert mock.read_current_version(text) == "v_1"

    updated = mock.set_current_version(text, "v_2")
    assert mock.read_current_version(updated) == "v_2"
    assert "# v_1, v_2, ..." in updated  # trailing inline comment preserved


# --- Commit: approve, refuse (CONTRACT.md §4.1, §4.3) ------------------------

def test_commit_refuses_when_gate_closed(tmp_path):
    """The entry gate guards commit, not just precheck; STATE.md stays untouched."""
    _state_with(tmp_path, plan="pending", data="approved")
    (tmp_path / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")

    result = mock.commit(tmp_path)

    assert result.ok is False
    assert "plan" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["mock"] == "pending"


def test_commit_refuses_when_mock_absent(tmp_path):
    """Approving without a mock.html on disk is refused (nothing to approve)."""
    _ready_project(tmp_path)

    result = mock.commit(tmp_path)

    assert result.ok is False
    assert "mock.html" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["mock"] == "pending"


def test_commit_refuses_mock_with_coverage_gap(tmp_path):
    """commit re-runs the coverage check; a gap is refused and STATE.md is untouched."""
    _ready_project(tmp_path)
    _write_mock(tmp_path, "v_1", _mock_html(ids=("kpi-revenue", "chart-trend", "flt-region")))

    result = mock.commit(tmp_path)

    assert result.ok is False
    assert route.parse_state(tmp_path / "STATE.md").statuses["mock"] == "pending"


def test_commit_approves_valid_mock(tmp_path):
    """A mock that fully covers the plan commits: mock -> approved, current_version set."""
    _ready_project(tmp_path)
    _write_mock(tmp_path, "v_1", _mock_html())

    result = mock.commit(tmp_path)

    assert result.ok is True
    assert result.version == "v_1"
    statuses = route.parse_state(tmp_path / "STATE.md").statuses
    assert statuses["mock"] == "approved"


# --- Staleness propagation (CONTRACT.md §4.2) --------------------------------

def test_rerun_after_approval_bumps_version_and_stales_downstream(tmp_path):
    """A post-approval re-run writes v_2, and flips downstream approved steps to stale."""
    # Upstream steps resolved too, so the router's cross-check reaches the stale 'spec'.
    _ready_project(
        tmp_path,
        intake="approved", brand="approved",
        mock="approved", spec="approved", build="approved",
    )
    _write_mock(tmp_path, "v_2", _mock_html())  # target after approval is v_2 (§4.3)

    result = mock.commit(tmp_path)

    assert result.ok is True
    assert result.version == "v_2"
    assert result.staled_steps == ["spec", "build"]

    statuses = route.parse_state(tmp_path / "STATE.md").statuses
    assert statuses["mock"] == "approved"
    assert statuses["spec"] == "stale" and statuses["build"] == "stale"

    # Cross-check through the router: the first unresolved step is now stale 'spec'.
    routed = route.compute_next_step(tmp_path)
    assert routed.next_step == "spec" and "stale" in routed.reason.lower()


def test_first_run_marks_nothing_stale(tmp_path):
    """On a first approval there is nothing downstream approved, so nothing goes stale."""
    _ready_project(tmp_path)
    _write_mock(tmp_path, "v_1", _mock_html())

    result = mock.commit(tmp_path)

    assert result.ok is True and result.staled_steps == []
