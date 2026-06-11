"""Contract test for the tableau-route router (CONTRACT.md §1, §4.1, §5).

Given fixture ``STATE.md`` files in known states, assert that
``compute_next_step`` recommends the next skill the ordering rule requires. This
is the highest-value seam in the plugin: the file-based contract is the whole
point of splitting the workflow into independent skills, and this test pins the
routing logic that every analyst relies on to know what to run next.

The fixtures under ``tests/fixtures/state/`` each represent one project state:
fresh, mid-pipeline, all-approved, a stale downstream step, and a deliberately
inconsistent state that the ordering gate must catch.
"""

from pathlib import Path

import pytest

import route  # provided on sys.path by conftest.py

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "state"


# Each case: fixture dir name -> (expected kind, expected next skill).
# next skill is None only when the pipeline is done.
ROUTING_CASES = [
    ("mid-pipeline", "ready", "tableau-brand"),
    ("stale-downstream", "ready", "tableau-mock"),
    ("all-approved", "done", None),
    ("gate-violation", "blocked", "tableau-data"),
]


@pytest.mark.parametrize(
    "fixture_name, expected_kind, expected_skill",
    ROUTING_CASES,
    ids=[case[0] for case in ROUTING_CASES],
)
def test_next_step_matches_ordering_rule(fixture_name, expected_kind, expected_skill):
    """Route's recommendation matches the ordering rule for each fixture state."""
    result = route.compute_next_step(FIXTURES / fixture_name)

    assert result.kind == expected_kind
    assert result.next_skill == expected_skill
    assert result.reason  # a human-readable explanation is always present


def test_fresh_project_recommends_init(tmp_path):
    """A directory with no STATE.md is a fresh project → tableau-init."""
    result = route.compute_next_step(tmp_path)

    assert result.kind == "fresh"
    assert result.next_skill == "tableau-init"
    assert result.is_done is False


def test_done_state_has_no_next_skill():
    """When every step is resolved, the router reports done with no next skill."""
    result = route.compute_next_step(FIXTURES / "all-approved")

    assert result.is_done is True
    assert result.next_skill is None


def test_stale_step_is_preferred_over_later_pending_steps():
    """A stale step is routed to before any later pending step (staleness wins)."""
    result = route.compute_next_step(FIXTURES / "stale-downstream")

    # mock is stale; spec and build are pending and come later — mock must win.
    assert result.next_step == "mock"
    assert "stale" in result.reason.lower()


def test_blocked_points_at_the_missing_artifacts_producer():
    """The gate catches an approved-but-missing artifact and points upstream."""
    result = route.compute_next_step(FIXTURES / "gate-violation")

    # 'data' is 'approved' but DATA-MODEL.md is absent, so 'plan' cannot run;
    # the router must send the analyst back to tableau-data, not to tableau-plan.
    assert result.kind == "blocked"
    assert result.next_step == "data"
    assert "DATA-MODEL.md" in result.reason


def _write_pre_mock_project(tmp_path, data_csv_dir):
    """Write a project where everything up to (not incl.) mock is approved.

    Args:
        tmp_path: The project directory to populate.
        data_csv_dir: Project-relative dir to drop a CSV in (``"data"`` or
            ``"scaffold/sample-data"``), or ``None`` to create no CSV at all.
    """
    state = (
        "# Project State\n\n"
        "## Metadata\n"
        "- target_tableau_version: 2024.2-2025.x\n"
        "- data_mode: csv\n"
        "- current_version: v_1\n\n"
        "## Steps\n"
        "| order | step   | skill          | status   |\n"
        "|-------|--------|----------------|----------|\n"
        "| 1     | init   | tableau-init   | approved |\n"
        "| 2     | intake | tableau-intake | approved |\n"
        "| 3     | data   | tableau-data   | approved |\n"
        "| 4     | brand  | tableau-brand  | approved |\n"
        "| 5     | plan   | tableau-plan   | approved |\n"
        "| 6     | mock   | tableau-mock   | pending  |\n"
        "| 7     | spec   | tableau-spec   | pending  |\n"
        "| 8     | build  | tableau-build  | pending  |\n"
    )
    (tmp_path / "STATE.md").write_text(state, encoding="utf-8")
    (tmp_path / "DASHBOARD-PLAN.md").write_text("plan", encoding="utf-8")
    if data_csv_dir is not None:
        csv_dir = tmp_path / data_csv_dir
        csv_dir.mkdir(parents=True)
        (csv_dir / "orders.csv").write_text("a,b\n1,2\n", encoding="utf-8")


def test_data_gate_accepts_production_data_dir(tmp_path):
    """The data gate is satisfied by production data/*.csv (CONTRACT.md §3.1)."""
    _write_pre_mock_project(tmp_path, data_csv_dir="data")

    result = route.compute_next_step(tmp_path)

    assert result.kind == "ready"
    assert result.next_step == "mock"


def test_data_gate_accepts_scaffold_sample_data_fallback(tmp_path):
    """The data gate falls back to scaffold/sample-data/*.csv so a demo never blocks (§3.1)."""
    _write_pre_mock_project(tmp_path, data_csv_dir="scaffold/sample-data")

    result = route.compute_next_step(tmp_path)

    assert result.kind == "ready"
    assert result.next_step == "mock"


def test_data_gate_blocks_when_no_csvs_anywhere(tmp_path):
    """With neither data/ nor scaffold/sample-data/ CSVs, mock is blocked on data."""
    _write_pre_mock_project(tmp_path, data_csv_dir=None)

    result = route.compute_next_step(tmp_path)

    assert result.kind == "blocked"
    assert result.next_step == "data"


def test_accepts_direct_state_file_path():
    """compute_next_step accepts a STATE.md path directly, not only a directory."""
    result = route.compute_next_step(FIXTURES / "mid-pipeline" / "STATE.md")

    assert result.next_skill == "tableau-brand"


def test_steps_definition_mirrors_the_eight_workflow_steps():
    """route.STEPS stays in lock-step with the 8-step contract (CONTRACT.md §1)."""
    expected = [
        (1, "init", "tableau-init"),
        (2, "intake", "tableau-intake"),
        (3, "data", "tableau-data"),
        (4, "brand", "tableau-brand"),
        (5, "plan", "tableau-plan"),
        (6, "mock", "tableau-mock"),
        (7, "spec", "tableau-spec"),
        (8, "build", "tableau-build"),
    ]
    actual = [(step.order, step.name, step.skill) for step in route.STEPS]
    assert actual == expected
