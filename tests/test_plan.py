"""Contract test for tableau-plan (CONTRACT.md step 5, §4.1, §4.2, §6).

``tableau-plan`` produces the strict ``DASHBOARD-PLAN.md`` the mock builds against. The
plan prose is model-authored, so this test pins the parts ``plan.py`` must guarantee
mechanically:

1. the entry gate (plan refuses until ``data`` is resolved AND ``DATA-MODEL.md`` exists,
   §4.1 — note that ``intake``/``brand`` are optional reads and never gate plan);
2. the precheck signals the skill branches on (existing plan, requirements source);
3. the strict schema — every required section present, every KPI/chart/filter/action with
   a stable, unique id, and interactions drawn from the shared vocabulary (§6);
4. the STATE.md transition + staleness propagation (§4.2), cross-checked by routing the
   resulting manifest through the *router's* own ``compute_next_step``.
"""

from pathlib import Path

import pytest

import init  # builds a realistic STATE.md the same way a real project would
import plan  # the skill under test (on sys.path via conftest.py)
import route  # the router parses/routes the STATE.md plan writes

TARGET_VERSION = "2024.2-2025.x"

# A schema-complete plan: all required sections, unique ids, valid interaction terms.
FULL_PLAN = (
    "# Dashboard Plan: Sales\n\n"
    "## Summary\nFor the VP of Sales.\n\n"
    "## Screen Size\n- mode: fixed\n- dimensions: 1366 x 768 px\n\n"
    "## Layout Grid\n"
    "| slot     | position | size         |\n"
    "|----------|----------|--------------|\n"
    "| kpi-row  | top      | 100% x 120px |\n"
    "| chart-a  | middle   | 100% x 360px |\n\n"
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
    "| int-region-filter | cross-filter | chart-trend | kpi-revenue | click filters |\n\n"
    "## Suggestions\n1. A discount-impact view.\n"
)


def _state_with(project_dir: Path, **status_overrides: str) -> None:
    """Write a canonical STATE.md, optionally overriding some step statuses.

    Args:
        project_dir: Directory to write ``STATE.md`` into.
        **status_overrides: ``step=status`` pairs to apply on top of a fresh manifest.
    """
    text = init.render_state_md(TARGET_VERSION)
    if status_overrides:
        text = plan.apply_status_updates(text, dict(status_overrides))
    (project_dir / "STATE.md").write_text(text, encoding="utf-8")


def _ready_project(project_dir: Path, **status_overrides: str) -> None:
    """Set up a project where plan's entry gate is open (data approved + DATA-MODEL.md).

    Args:
        project_dir: The project directory to populate.
        **status_overrides: Extra ``step=status`` overrides (merged after ``data``).
    """
    _state_with(project_dir, data="approved", **status_overrides)
    (project_dir / "DATA-MODEL.md").write_text("## Data source: `x.csv`\n", encoding="utf-8")
    # A real CSV on disk too, so downstream gates (e.g. mock) see the data artifact.
    data_dir = project_dir / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "x.csv").write_text("region,revenue\nWest,10\n", encoding="utf-8")


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def test_precheck_blocks_when_no_state(tmp_path):
    """No STATE.md ⇒ plan cannot run; the blocker points at tableau-init."""
    result = plan.precheck(tmp_path)

    assert result.can_run is False
    assert "tableau-init" in result.blocker


def test_precheck_blocks_when_data_not_resolved(tmp_path):
    """data must be resolved before plan runs (§4.1)."""
    _state_with(tmp_path, data="pending")
    (tmp_path / "DATA-MODEL.md").write_text("x", encoding="utf-8")

    result = plan.precheck(tmp_path)

    assert result.can_run is False
    assert "data" in result.blocker and "pending" in result.blocker


def test_precheck_blocks_when_data_model_missing(tmp_path):
    """Even with data approved, a missing DATA-MODEL.md on disk blocks plan (§4.1)."""
    _state_with(tmp_path, data="approved")  # no DATA-MODEL.md written

    result = plan.precheck(tmp_path)

    assert result.can_run is False
    assert "DATA-MODEL.md" in result.blocker


def test_intake_and_brand_do_not_gate_plan(tmp_path):
    """intake/brand are optional reads: plan runs with them pending (§1)."""
    _ready_project(tmp_path, intake="pending", brand="pending")

    assert plan.precheck(tmp_path).can_run is True


def test_commit_refuses_when_data_not_resolved(tmp_path):
    """The same gate guards commit, not just precheck; STATE.md stays untouched."""
    _state_with(tmp_path, data="pending")
    (tmp_path / "DATA-MODEL.md").write_text("x", encoding="utf-8")
    (tmp_path / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")

    result = plan.commit(tmp_path)

    assert result.ok is False
    assert "data" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["plan"] == "pending"


# --- Precheck signals --------------------------------------------------------

def test_precheck_detects_existing_plan(tmp_path):
    """precheck reports whether a DASHBOARD-PLAN.md already exists (refine-vs-overwrite)."""
    _ready_project(tmp_path)
    assert plan.precheck(tmp_path).plan_exists is False

    (tmp_path / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")
    assert plan.precheck(tmp_path).plan_exists is True


def test_requirements_source_prefers_prd_then_request(tmp_path):
    """PRD.md > root DASHBOARD-REQUEST.md > scaffold demo > none (§3.1)."""
    _ready_project(tmp_path)
    assert plan.precheck(tmp_path).requirements_source == "none"

    (tmp_path / "scaffold").mkdir()
    (tmp_path / "scaffold" / "EXAMPLE-DASHBOARD-REQUEST.md").write_text("demo", encoding="utf-8")
    assert plan.precheck(tmp_path).requirements_source == plan.SCAFFOLD_REQUEST

    (tmp_path / "DASHBOARD-REQUEST.md").write_text("req", encoding="utf-8")
    assert plan.precheck(tmp_path).requirements_source == "DASHBOARD-REQUEST.md"

    (tmp_path / "PRD.md").write_text("prd", encoding="utf-8")
    assert plan.precheck(tmp_path).requirements_source == "PRD.md"


def test_precheck_reports_design_tokens_presence(tmp_path):
    """precheck surfaces whether DESIGN-TOKENS.md is available (optional read)."""
    _ready_project(tmp_path)
    assert plan.precheck(tmp_path).tokens_present is False

    (tmp_path / "DESIGN-TOKENS.md").write_text("## Colors\n", encoding="utf-8")
    assert plan.precheck(tmp_path).tokens_present is True


# --- Plan schema -------------------------------------------------------------

def test_validate_full_plan_passes():
    """A schema-complete plan validates clean."""
    validation = plan.validate_plan(FULL_PLAN)

    assert validation.ok is True
    assert validation.missing_required == []
    assert validation.problems == []


def test_validate_rejects_missing_required_section():
    """A plan missing a required section is invalid and names it (blocks the mock)."""
    without_interactions = FULL_PLAN.split("## Interactions")[0]

    validation = plan.validate_plan(without_interactions)

    assert validation.ok is False
    assert "Interactions" in validation.missing_required


def test_validate_rejects_duplicate_ids():
    """Stable ids must be unique across the plan — a collision is rejected."""
    dup = FULL_PLAN.replace("int-region-filter", "kpi-revenue")  # clashes with the KPI id

    validation = plan.validate_plan(dup)

    assert validation.ok is False
    assert any("duplicate id 'kpi-revenue'" in problem for problem in validation.problems)


def test_validate_rejects_empty_id():
    """A row in an id-table with a blank id is rejected (every element needs an id)."""
    blank = FULL_PLAN.replace(
        "| kpi-revenue | kpi        | revenue      | kpi-row | 1/4 of row |",
        "|             | kpi        | revenue      | kpi-row | 1/4 of row |",
    )

    validation = plan.validate_plan(blank)

    assert validation.ok is False
    assert any("empty 'id'" in problem for problem in validation.problems)


def test_validate_rejects_unknown_interaction_term():
    """Interactions must use the shared vocabulary (§6); an off-list term is rejected."""
    bad = FULL_PLAN.replace("cross-filter", "zoom-and-pan")

    validation = plan.validate_plan(bad)

    assert validation.ok is False
    assert any("zoom-and-pan" in problem for problem in validation.problems)


def test_validate_accepts_all_vocabulary_terms():
    """Every shared-vocabulary term is accepted in the interaction column (§6)."""
    for term in plan.INTERACTION_VOCABULARY:
        text = FULL_PLAN.replace("cross-filter", term)
        assert plan.validate_plan(text).ok is True, term


def test_validate_reports_missing_recommended_without_failing():
    """An absent recommended section is reported but never makes a plan invalid."""
    no_summary = FULL_PLAN.replace("## Summary\nFor the VP of Sales.\n\n", "")

    validation = plan.validate_plan(no_summary)

    assert validation.ok is True
    assert "Summary" in validation.missing_recommended


def test_shipped_template_is_schema_complete():
    """The bundled DASHBOARD-PLAN-TEMPLATE.md passes its own validator (the anchor)."""
    validation = plan.validate_plan(plan.render_plan_template())

    assert validation.ok is True, (validation.missing_required, validation.problems)


# --- Commit: approve, refuse -------------------------------------------------

def test_commit_approves_complete_plan(tmp_path):
    """A schema-complete plan commits: plan → approved in STATE.md."""
    _ready_project(tmp_path)
    (tmp_path / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")

    result = plan.commit(tmp_path)

    assert result.ok is True
    assert route.parse_state(tmp_path / "STATE.md").statuses["plan"] == "approved"


def test_commit_refuses_incomplete_plan(tmp_path):
    """commit refuses a plan missing a required section, and STATE.md is untouched."""
    _ready_project(tmp_path)
    (tmp_path / "DASHBOARD-PLAN.md").write_text(
        FULL_PLAN.split("## Filters")[0], encoding="utf-8"
    )

    result = plan.commit(tmp_path)

    assert result.ok is False
    assert "Filters" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["plan"] == "pending"


def test_commit_refuses_when_plan_absent(tmp_path):
    """Approving without a DASHBOARD-PLAN.md on disk is refused (nothing to approve)."""
    _ready_project(tmp_path)

    result = plan.commit(tmp_path)

    assert result.ok is False
    assert "DASHBOARD-PLAN.md" in result.message


# --- Staleness propagation (CONTRACT.md §4.2) --------------------------------

def test_rerun_marks_downstream_approved_steps_stale(tmp_path):
    """Re-running plan flips downstream approved steps (mock/spec/build) to stale."""
    # Upstream steps resolved too, so the router's cross-check reaches the stale 'mock'.
    _ready_project(
        tmp_path,
        intake="approved", brand="approved",
        plan="approved", mock="approved", spec="approved",
    )
    (tmp_path / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")

    result = plan.commit(tmp_path)

    assert result.ok is True
    assert result.staled_steps == ["mock", "spec"]

    statuses = route.parse_state(tmp_path / "STATE.md").statuses
    assert statuses["mock"] == "stale" and statuses["spec"] == "stale"
    assert statuses["plan"] == "approved"  # its own step re-approved

    # Cross-check through the router: the first unresolved step is now stale 'mock'.
    routed = route.compute_next_step(tmp_path)
    assert routed.next_step == "mock" and "stale" in routed.reason.lower()


def test_first_run_marks_nothing_stale(tmp_path):
    """On a first approval there is nothing downstream approved, so nothing goes stale."""
    _ready_project(tmp_path)
    (tmp_path / "DASHBOARD-PLAN.md").write_text(FULL_PLAN, encoding="utf-8")

    result = plan.commit(tmp_path)

    assert result.ok is True and result.staled_steps == []
