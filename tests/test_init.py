"""Contract test for tableau-init (CONTRACT.md §1 step 1, §2 schema, §3.1, §4.1).

``tableau-init`` is the only step with no upstream dependency: it bootstraps the
project and the ``STATE.md`` manifest every other skill reads. This test pins that
contract by exercising the scaffolder's observable output — the files it produces
and the schema-validity of the manifest — rather than the prose of SKILL.md.

Three seams matter most and are deliberately cross-checked here:

1. init writes its templates under ``scaffold/`` as **demo examples** and creates
   **no** production input files at the project root (CONTRACT.md §3.1) — their
   absence is the signal of what the analyst still owes.
2. The ``STATE.md`` init writes must be parseable and routable by the *router's*
   own parser (``route.parse_state`` / ``route.compute_next_step``). Asserting
   init's output through route's input proves the handoff actually works.
3. Re-running init must be non-destructive: an established project keeps its edits
   and pipeline progress (the idempotency guarantee in the acceptance criteria).
"""

from pathlib import Path

import pytest

import init  # provided on sys.path by conftest.py
import route  # the router parses/routes the STATE.md that init writes

# The full set of demo examples init must lay down under scaffold/.
EXPECTED_SCAFFOLD_FILES = (
    "scaffold/EXAMPLE-DASHBOARD-REQUEST.md",
    "scaffold/EXAMPLE-datasources.json",
    "scaffold/.env.example",
    "scaffold/branding/EXAMPLE-branding.md",
    "scaffold/sample-data/sales_orders.csv",
    "scaffold/sample-data/customer_segments.csv",
    "scaffold/sample-data/monthly_targets.csv",
)

# Production inputs init must NOT create — their absence is the signal (§3.1).
PRODUCTION_INPUTS = (
    "DASHBOARD-REQUEST.md",
    "datasources.json",
    "branding/branding.md",
    "data",
)


@pytest.fixture
def fresh_project(tmp_path):
    """An empty project directory scaffolded with a known target version."""
    result = init.scaffold_project(tmp_path, target_version="2024.2-2025.x")
    return tmp_path, result


def test_scaffolds_demo_examples_under_scaffold(fresh_project):
    """An empty dir gains the full scaffold/ demo set plus STATE.md."""
    project_dir, result = fresh_project

    for relative in EXPECTED_SCAFFOLD_FILES:
        assert (project_dir / relative).is_file(), f"missing scaffold file: {relative}"
    assert (project_dir / "STATE.md").is_file()

    # Everything was newly created on a fresh project; nothing pre-existing.
    assert result.state_created is True
    assert set(EXPECTED_SCAFFOLD_FILES).issubset(set(result.created))
    assert result.skipped == []


def test_creates_no_production_inputs(fresh_project):
    """init writes only demo examples; production inputs are the analyst's to add (§3.1)."""
    project_dir, _ = fresh_project

    for relative in PRODUCTION_INPUTS:
        assert not (project_dir / relative).exists(), (
            f"init should not create production input '{relative}' — its absence "
            f"is what tells the analyst what is still owed."
        )


def test_state_is_schema_valid_via_route_parser(fresh_project):
    """The STATE.md init writes parses cleanly through the router's own parser."""
    project_dir, _ = fresh_project

    state = route.parse_state(project_dir / "STATE.md")

    # Metadata recorded at init (CONTRACT.md §2).
    assert state.metadata["target_tableau_version"] == "2024.2-2025.x"
    assert state.metadata["data_mode"] == "csv"
    assert state.current_version == "v_1"

    # init is approved so the pipeline can advance; steps 2-8 start pending.
    assert state.statuses["init"] == "approved"
    downstream = [name for name in state.statuses if name != "init"]
    assert downstream, "expected steps 2-8 to be present"
    assert all(state.statuses[name] == "pending" for name in downstream)


def test_route_reports_intake_next_after_init(fresh_project):
    """Acceptance: after init, the router points at the next step (tableau-intake)."""
    project_dir, _ = fresh_project

    result = route.compute_next_step(project_dir)

    assert result.kind == "ready"
    assert result.next_step == "intake"
    assert result.next_skill == "tableau-intake"


@pytest.mark.parametrize("target_version", init.ALLOWED_TARGET_VERSIONS)
def test_records_each_allowed_target_version(tmp_path, target_version):
    """Both allowed target versions round-trip into STATE.md verbatim."""
    init.scaffold_project(tmp_path, target_version=target_version)

    state = route.parse_state(tmp_path / "STATE.md")
    assert state.metadata["target_tableau_version"] == target_version


def test_rejects_invalid_target_version(tmp_path):
    """An unsupported version is refused before any files are written."""
    with pytest.raises(ValueError, match="target_tableau_version"):
        init.scaffold_project(tmp_path, target_version="2099.9")

    # Nothing should have been scaffolded on a validation failure.
    assert not (tmp_path / "STATE.md").exists()
    assert not (tmp_path / "scaffold").exists()


def test_rerun_preserves_user_content(fresh_project):
    """Re-running init never clobbers user edits or recorded pipeline progress."""
    project_dir, _ = fresh_project

    # The analyst customizes a scaffold example and advances the pipeline by hand.
    example = project_dir / "scaffold" / "EXAMPLE-DASHBOARD-REQUEST.md"
    example.write_text("My edited example.", encoding="utf-8")
    advanced_state = init.render_state_md("2024.2-2025.x").replace(
        "| 2     | intake | tableau-intake | pending  |",
        "| 2     | intake | tableau-intake | approved |",
    )
    (project_dir / "STATE.md").write_text(advanced_state, encoding="utf-8")

    # Re-running init on the established project (even with a different version).
    result = init.scaffold_project(project_dir, target_version="2026.1+")

    # User edits survive untouched...
    assert example.read_text(encoding="utf-8") == "My edited example."
    # ...and the existing STATE.md (with intake approved) was preserved, not reset.
    assert result.state_created is False
    assert "STATE.md" in result.skipped
    state = route.parse_state(project_dir / "STATE.md")
    assert state.statuses["intake"] == "approved"
    assert state.metadata["target_tableau_version"] == "2024.2-2025.x"


def test_adds_only_missing_files_on_partial_project(tmp_path):
    """A project that already holds some scaffold files keeps them and gains the rest."""
    # Analyst pre-seeds one scaffold CSV before re-running init.
    sample_dir = tmp_path / "scaffold" / "sample-data"
    sample_dir.mkdir(parents=True)
    user_csv = sample_dir / "sales_orders.csv"
    user_csv.write_text("my,own,headers\n1,2,3\n", encoding="utf-8")

    result = init.scaffold_project(tmp_path, target_version="2024.2-2025.x")

    # The pre-existing file is preserved; the other scaffold files are added.
    assert user_csv.read_text(encoding="utf-8") == "my,own,headers\n1,2,3\n"
    assert "scaffold/sample-data/sales_orders.csv" in result.skipped
    assert (tmp_path / "scaffold/sample-data/customer_segments.csv").is_file()
    assert "scaffold/sample-data/customer_segments.csv" in result.created


def test_state_text_matches_canonical_schema():
    """render_state_md emits the canonical Metadata + 8-row Steps table."""
    text = init.render_state_md("2026.1+")

    assert "# Project State" in text
    assert "- target_tableau_version: 2026.1+" in text
    assert "## Steps" in text
    # All 8 steps present, in order, with init approved.
    for step in init.WORKFLOW_STEPS:
        assert step.skill in text
    assert "| 1     | init   | tableau-init   | approved |" in text


def test_workflow_steps_mirror_the_eight_contract_steps():
    """init.WORKFLOW_STEPS stays in lock-step with the 8-step contract (§2)."""
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
    actual = [(step.order, step.name, step.skill) for step in init.WORKFLOW_STEPS]
    assert actual == expected
