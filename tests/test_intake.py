"""Contract test for tableau-intake (CONTRACT.md step 2, §3.1, §4.1, §4.2).

``tableau-intake`` is the first skill that *mutates* an existing ``STATE.md`` (init
only creates it, route only reads it), and the only optional/idempotent step besides
``brand``. The PRD prose is model-authored, so this test pins the parts that must be
mechanically guaranteed - exactly the seams ``intake.py`` owns:

1. the entry gate (intake refuses until ``init`` is ``approved``, §4.1);
2. the precheck signals the skill branches on (existing PRD, request source);
3. the PRD schema - a required core (Overview, Visualizations) is enforced while
   KPIs/Filters stay optional, and a refined PRD that keeps custom sections still
   validates;
4. the STATE.md transition + staleness propagation (§4.2), cross-checked by routing
   the resulting manifest through the *router's* own ``compute_next_step``.
"""

from pathlib import Path

import pytest

import init    # builds a realistic STATE.md the same way a real project would
import intake  # the skill under test (on sys.path via conftest.py)
import route   # the router parses/routes the STATE.md intake writes

TARGET_VERSION = "2024.2-2025.x"

# A schema-complete PRD (required core present) plus optional sections.
FULL_PRD = (
    "# Sales Dashboard\n\n"
    "## Overview\nFor the VP of Sales.\n\n"
    "## Visualizations\n1. Revenue trend line chart.\n\n"
    "## KPIs\n1. Total Revenue.\n\n"
    "## Filters\n- Region.\n"
)

# Only the required core - a legitimate dashboard with no KPIs and no filters.
CORE_ONLY_PRD = (
    "# Status Board\n\n"
    "## Overview\nA live status wall.\n\n"
    "## Visualizations\n1. A single status table.\n"
)

# Missing the required core entirely (has only an optional section).
INCOMPLETE_PRD = "# Half-baked\n\n## KPIs\n1. Some number.\n"


def _write_state(project_dir: Path, **status_overrides: str) -> None:
    """Write a canonical STATE.md, optionally overriding some step statuses.

    Args:
        project_dir: Directory to write ``STATE.md`` into.
        **status_overrides: ``step=status`` pairs to apply on top of a fresh
            manifest (e.g. ``intake="approved"``).
    """
    text = init.render_state_md(TARGET_VERSION)
    if status_overrides:
        text = intake.apply_status_updates(text, {k: v for k, v in status_overrides.items()})
    (project_dir / "STATE.md").write_text(text, encoding="utf-8")


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def test_precheck_blocks_when_no_state(tmp_path):
    """No STATE.md ⇒ intake cannot run; the blocker points at tableau-init."""
    result = intake.precheck(tmp_path)

    assert result.can_run is False
    assert "tableau-init" in result.blocker


def test_precheck_blocks_when_init_not_approved(tmp_path):
    """init must be approved before intake runs (§4.1)."""
    _write_state(tmp_path, init="pending")

    result = intake.precheck(tmp_path)

    assert result.can_run is False
    assert "init" in result.blocker and "pending" in result.blocker


def test_commit_refuses_when_init_not_approved(tmp_path):
    """The same gate guards commit, not just precheck."""
    _write_state(tmp_path, init="pending")
    (tmp_path / "PRD.md").write_text(FULL_PRD, encoding="utf-8")

    result = intake.commit(tmp_path, status="approved")

    assert result.ok is False
    assert "init" in result.message
    # STATE.md must be untouched: intake stays pending.
    assert route.parse_state(tmp_path / "STATE.md").statuses["intake"] == "pending"


# --- Precheck signals --------------------------------------------------------

def test_precheck_detects_existing_prd(tmp_path):
    """precheck reports whether a PRD.md already exists (the refine-vs-overwrite signal)."""
    _write_state(tmp_path)
    assert intake.precheck(tmp_path).prd_exists is False

    (tmp_path / "PRD.md").write_text(FULL_PRD, encoding="utf-8")
    assert intake.precheck(tmp_path).prd_exists is True


def test_request_source_prefers_production_over_scaffold(tmp_path):
    """Root DASHBOARD-REQUEST.md > scaffold/ demo example > none (§3.1)."""
    # A scaffolded project has only the scaffold/ demo example.
    init.scaffold_project(tmp_path, target_version=TARGET_VERSION)
    assert intake.precheck(tmp_path).request_source == intake.SCAFFOLD_REQUEST

    # Adding the production request flips the preference to it.
    (tmp_path / "DASHBOARD-REQUEST.md").write_text("my request", encoding="utf-8")
    assert intake.precheck(tmp_path).request_source == "DASHBOARD-REQUEST.md"


def test_request_source_none_when_nothing_present(tmp_path):
    """With neither a root request nor the scaffold example, the analyst will paste."""
    _write_state(tmp_path)  # bare project: STATE.md only, no scaffold/ files

    assert intake.precheck(tmp_path).request_source == "none"


# --- PRD schema --------------------------------------------------------------

def test_validate_prd_required_core():
    """A PRD missing the required core is invalid and names what's missing."""
    ok, missing_required, _ = intake.validate_prd(INCOMPLETE_PRD)

    assert ok is False
    assert set(missing_required) == {"Overview", "Visualizations"}


def test_validate_prd_recommended_are_optional():
    """Core-only PRD is valid; the absent recommended sections are merely reported."""
    ok, missing_required, missing_recommended = intake.validate_prd(CORE_ONLY_PRD)

    assert ok is True
    assert missing_required == []
    # KPIs/Filters/Additional Notes absent — reported, but not a failure.
    assert set(missing_recommended) == set(intake.PRD_RECOMMENDED_SECTIONS)


def test_validate_prd_allows_custom_sections_when_refining():
    """A refined PRD that keeps the core plus its own custom sections still validates."""
    refined = CORE_ONLY_PRD + "\n## Data Sources\nsales.csv\n\n## Open Questions\nTBD\n"

    ok, missing_required, _ = intake.validate_prd(refined)

    assert ok is True and missing_required == []


def test_shipped_template_is_schema_complete():
    """The bundled PRD-TEMPLATE.md contains the required core (validator's anchor)."""
    ok, missing_required, _ = intake.validate_prd(intake.render_prd_template())

    assert ok is True and missing_required == []


# --- Commit: approve, skip, refuse -------------------------------------------

def test_commit_approved_with_complete_prd(tmp_path):
    """A schema-complete PRD commits: intake → approved in STATE.md."""
    _write_state(tmp_path)
    (tmp_path / "PRD.md").write_text(FULL_PRD, encoding="utf-8")

    result = intake.commit(tmp_path, status="approved")

    assert result.ok is True
    assert route.parse_state(tmp_path / "STATE.md").statuses["intake"] == "approved"


def test_commit_approved_refuses_incomplete_prd(tmp_path):
    """commit refuses to approve a PRD missing the required core, and STATE is untouched."""
    _write_state(tmp_path)
    (tmp_path / "PRD.md").write_text(INCOMPLETE_PRD, encoding="utf-8")

    result = intake.commit(tmp_path, status="approved")

    assert result.ok is False
    assert "Overview" in result.message and "Visualizations" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["intake"] == "pending"


def test_commit_approved_refuses_when_prd_absent(tmp_path):
    """Approving without a PRD.md on disk is refused (nothing to approve)."""
    _write_state(tmp_path)

    result = intake.commit(tmp_path, status="approved")

    assert result.ok is False
    assert "PRD.md" in result.message


def test_commit_core_only_prd_succeeds_and_notes_recommended(tmp_path):
    """A core-only PRD (no KPIs/filters) commits; missing recommended is just a note."""
    _write_state(tmp_path)
    (tmp_path / "PRD.md").write_text(CORE_ONLY_PRD, encoding="utf-8")

    result = intake.commit(tmp_path, status="approved")

    assert result.ok is True
    assert set(result.missing_recommended) == set(intake.PRD_RECOMMENDED_SECTIONS)


def test_commit_skipped_needs_no_prd_and_does_not_block(tmp_path):
    """Skipping records 'skipped' without any PRD and lets the pipeline continue."""
    _write_state(tmp_path)

    result = intake.commit(tmp_path, status="skipped")

    assert result.ok is True
    statuses = route.parse_state(tmp_path / "STATE.md").statuses
    assert statuses["intake"] == "skipped"
    # 'skipped' is resolved, so the router advances past intake (to data).
    assert route.compute_next_step(tmp_path).next_step == "data"


def test_commit_rejects_unknown_status(tmp_path):
    """An out-of-vocabulary status is a programming error, not a silent no-op."""
    _write_state(tmp_path)

    with pytest.raises(ValueError, match="intake status"):
        intake.commit(tmp_path, status="approve")  # typo: not in COMMIT_STATUSES


# --- Staleness propagation (CONTRACT.md §4.2) --------------------------------

def test_rerun_marks_downstream_approved_steps_stale(tmp_path):
    """Re-running intake flips downstream approved steps to stale; others untouched."""
    # A mid-pipeline project: intake already approved, data+brand approved, plan pending.
    _write_state(tmp_path, intake="approved", data="approved", brand="approved")
    (tmp_path / "PRD.md").write_text(FULL_PRD, encoding="utf-8")

    result = intake.commit(tmp_path, status="approved")

    assert result.ok is True
    # data and brand (downstream, approved) flip to stale, in canonical order.
    assert result.staled_steps == ["data", "brand"]

    statuses = route.parse_state(tmp_path / "STATE.md").statuses
    assert statuses["data"] == "stale" and statuses["brand"] == "stale"
    assert statuses["plan"] == "pending"      # was pending → left as-is
    assert statuses["intake"] == "approved"   # its own step re-approved

    # Cross-check through the router: the first unresolved step is now stale 'data'.
    routed = route.compute_next_step(tmp_path)
    assert routed.next_step == "data" and "stale" in routed.reason.lower()


def test_first_run_marks_nothing_stale(tmp_path):
    """On a first approval there is nothing downstream approved, so nothing goes stale."""
    _write_state(tmp_path)
    (tmp_path / "PRD.md").write_text(FULL_PRD, encoding="utf-8")

    result = intake.commit(tmp_path, status="approved")

    assert result.ok is True and result.staled_steps == []
