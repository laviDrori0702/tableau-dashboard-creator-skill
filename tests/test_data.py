"""Contract test for tableau-data (CONTRACT.md step 3, §3.1, §3.2, §4.1, §4.2).

``tableau-data`` is the non-skippable step that produces ``DATA-MODEL.md`` and makes
the ``data/`` CSVs the single source of truth for the field names downstream steps
build against. The field *descriptions* are model-authored, so this test pins the
parts that must be mechanically guaranteed - exactly the seams ``data.py`` owns:

1. the entry gate (data refuses until ``init`` is ``approved``, §4.1);
2. acquisition-route detection (production CSV > scaffold demo > none; Route 2
   inputs detected, §3.1/§3.2);
3. type inference and the DATA-MODEL.md render <-> parse round-trip;
4. the header<->model validator - the guarantee that documented field names match
   the real CSV headers exactly, typo/casing included (§3.2);
5. the STATE.md transition + staleness propagation (§4.2), cross-checked by routing
   the resulting manifest through the *router's* own ``compute_next_step``.

There is deliberately **no** synthesized-data path to test: when no real data exists
the floor is the labelled ``scaffold/sample-data/`` demo (§3.1), never invented rows.
"""

from pathlib import Path

import pytest

import data   # the skill under test (on sys.path via conftest.py)
import init   # builds a realistic STATE.md the same way a real project would
import route  # the router parses/routes the STATE.md data writes

TARGET_VERSION = "2024.2-2025.x"

# A small CSV exercising every inferable type plus a casing-sensitive header.
SAMPLE_CSV = (
    "order_id,Region,revenue,quantity,order_date,is_paid\n"
    "ORD-001,North America,971.89,12,2025-01-03,true\n"
    "ORD-002,Europe,1499.95,5,2025-01-05,false\n"
    "ORD-003,Asia Pacific,1147.50,30,2025-01-07,true\n"
)


def _write_state(project_dir: Path, **status_overrides: str) -> None:
    """Write a canonical STATE.md, optionally overriding some step statuses.

    Args:
        project_dir: Directory to write ``STATE.md`` into.
        **status_overrides: ``step=status`` pairs to apply on top of a fresh
            manifest (e.g. ``data="approved"``).
    """
    text = init.render_state_md(TARGET_VERSION)
    if status_overrides:
        text = data.apply_status_updates(text, {k: v for k, v in status_overrides.items()})
    (project_dir / "STATE.md").write_text(text, encoding="utf-8")


def _write_csv(project_dir: Path, relative: str, content: str = SAMPLE_CSV) -> Path:
    """Write a CSV under ``project_dir`` (creating parent dirs) and return its path."""
    path = project_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def test_precheck_blocks_when_no_state(tmp_path):
    """No STATE.md => data cannot run; the blocker points at tableau-init."""
    result = data.precheck(tmp_path)

    assert result.can_run is False
    assert "tableau-init" in result.blocker


def test_precheck_blocks_when_init_not_approved(tmp_path):
    """init must be approved before data runs (§4.1)."""
    _write_state(tmp_path, init="pending")

    result = data.precheck(tmp_path)

    assert result.can_run is False
    assert "init" in result.blocker and "pending" in result.blocker


def test_commit_refuses_when_init_not_approved(tmp_path):
    """The same gate guards commit, not just precheck; STATE stays untouched."""
    _write_state(tmp_path, init="pending")

    result = data.commit(tmp_path)

    assert result.ok is False
    assert "init" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["data"] == "pending"


# --- Acquisition-route detection (CONTRACT.md §3.1 / §3.2) -------------------

def test_precheck_prefers_production_data_over_scaffold(tmp_path):
    """Root data/*.csv wins over the scaffold/sample-data/ demo (§3.1)."""
    _write_state(tmp_path)
    _write_csv(tmp_path, "scaffold/sample-data/demo.csv")
    assert data.precheck(tmp_path).csv_source == "scaffold"

    _write_csv(tmp_path, "data/sales.csv")
    result = data.precheck(tmp_path)
    assert result.csv_source == "data"
    assert result.csv_files == ["sales.csv"]


def test_precheck_detects_route2_inputs(tmp_path):
    """datasources.json + .env are detected (published-ds route), not queried."""
    _write_state(tmp_path)
    (tmp_path / "datasources.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".env").write_text("TABLEAU_TOKEN=x", encoding="utf-8")

    result = data.precheck(tmp_path)

    assert result.csv_source == "none"
    assert result.has_datasources_json is True and result.has_env is True


def test_precheck_none_when_no_data_anywhere(tmp_path):
    """With neither CSVs nor Route 2 inputs, precheck reports an empty situation."""
    _write_state(tmp_path)

    result = data.precheck(tmp_path)

    assert result.csv_source == "none"
    assert result.has_datasources_json is False and result.has_env is False


# --- Type inference ----------------------------------------------------------

@pytest.mark.parametrize("values, expected", [
    (["true", "false", "TRUE"], data.TYPE_BOOLEAN),
    (["1", "0", "1"], data.TYPE_INTEGER),         # 0/1 are integers, not booleans
    (["12", "-5", "30"], data.TYPE_INTEGER),
    (["971.89", "1499.95", "45"], data.TYPE_REAL),
    (["2025-01-03", "2025/01/05"], data.TYPE_DATE),
    (["2025-01-03 14:30:00", "2025-01-05T09:00:00"], data.TYPE_DATETIME),
    (["North America", "Europe"], data.TYPE_STRING),
    (["", "  ", ""], data.TYPE_STRING),           # all-empty has no narrow evidence
    (["10", "", "20"], data.TYPE_INTEGER),        # blanks don't veto a real type
])
def test_infer_type(values, expected):
    """Type inference is conservative and narrowest-first."""
    assert data.infer_type(values) == expected


# --- DATA-MODEL.md render <-> parse round-trip -------------------------------

def test_render_then_parse_recovers_fields(tmp_path):
    """render_data_model -> parse_data_model recovers the field names + types exactly."""
    csv_path = _write_csv(tmp_path, "data/orders.csv")
    profile = data.profile_csv(csv_path)
    text = data.render_data_model([profile], data.TIER_CSV_PROVIDED)

    documented = data.parse_data_model(text)
    assert list(documented.keys()) == ["orders.csv"]
    recovered = documented["orders.csv"]
    assert [name for name, _ in recovered] == [
        "order_id", "Region", "revenue", "quantity", "order_date", "is_paid",
    ]
    by_name = dict(recovered)
    assert by_name["revenue"] == data.TYPE_REAL
    assert by_name["quantity"] == data.TYPE_INTEGER
    assert by_name["order_date"] == data.TYPE_DATE
    assert by_name["is_paid"] == data.TYPE_BOOLEAN
    assert data.parse_acquisition_tier(text) == data.TIER_CSV_PROVIDED


# --- Header <-> model validator (AC: match + mismatch) -----------------------

def test_validate_headers_exact_match():
    """Identical field sets validate, regardless of order."""
    check = data.validate_headers(["a", "b", "c"], ["c", "a", "b"])
    assert check.ok is True and check.missing == [] and check.extra == []


def test_validate_headers_reports_typo():
    """A documented typo surfaces as a missing/extra pair, not a silent pass."""
    check = data.validate_headers(["order_id", "region"], ["order_id", "regoin"])
    assert check.ok is False
    assert check.missing == ["regoin"]   # documented but not in CSV
    assert check.extra == ["region"]     # in CSV but not documented


def test_validate_headers_casing_is_significant():
    """'Region' != 'region' - a casing drift is reported (§3.2)."""
    check = data.validate_headers(["Region"], ["region"])
    assert check.ok is False
    assert check.missing == ["region"] and check.extra == ["Region"]


def test_validate_headers_reports_missing_and_extra():
    """A dropped column and an undocumented column are both reported."""
    check = data.validate_headers(["a", "b", "x"], ["a", "b", "c"])
    assert check.ok is False
    assert check.missing == ["c"] and check.extra == ["x"]


# --- profile -----------------------------------------------------------------

def test_profile_writes_data_model_and_is_non_destructive(tmp_path):
    """profile writes DATA-MODEL.md once; a second run refuses without --force."""
    _write_state(tmp_path)
    _write_csv(tmp_path, "data/sales.csv")

    first = data.profile(tmp_path)
    assert first.ok is True
    assert (tmp_path / "DATA-MODEL.md").exists()
    assert first.tier == data.TIER_CSV_PROVIDED and first.is_demo is False

    # Simulate the model enriching a description, then a careless re-profile.
    model_path = tmp_path / "DATA-MODEL.md"
    enriched = model_path.read_text(encoding="utf-8").replace(
        "| order_id | string | Dimension | ORD-001, ORD-002, ORD-003 |  |",
        "| order_id | string | Dimension | ORD-001, ORD-002, ORD-003 | Unique order id |",
    )
    assert "Unique order id" in enriched  # guard: the replace target must exist
    model_path.write_text(enriched, encoding="utf-8")

    second = data.profile(tmp_path)
    assert second.ok is False and "already exists" in second.message
    # The enrichment survived (non-destructive).
    assert "Unique order id" in model_path.read_text(encoding="utf-8")

    # --force regenerates from the CSVs.
    forced = data.profile(tmp_path, force=True)
    assert forced.ok is True


def test_profile_demo_source_is_flagged(tmp_path):
    """Profiling the scaffold/sample-data/ fallback records the demo tier."""
    _write_state(tmp_path)
    _write_csv(tmp_path, "scaffold/sample-data/demo.csv")

    result = data.profile(tmp_path)

    assert result.ok is True
    assert result.is_demo is True and result.tier == data.TIER_CSV_DEMO


def test_profile_refuses_when_no_data(tmp_path):
    """No CSVs anywhere => profile refuses (no synthesized-data path)."""
    _write_state(tmp_path)

    result = data.profile(tmp_path)

    assert result.ok is False
    assert "No CSVs" in result.message


# --- commit: approve, refuse, staleness --------------------------------------

def test_commit_approves_when_headers_match(tmp_path):
    """A profiled, matching DATA-MODEL.md commits: data -> approved."""
    _write_state(tmp_path)
    _write_csv(tmp_path, "data/sales.csv")
    data.profile(tmp_path)

    result = data.commit(tmp_path)

    assert result.ok is True
    assert result.validated == ["sales.csv"]
    statuses = route.parse_state(tmp_path / "STATE.md").statuses
    assert statuses["data"] == "approved"
    # The router advances past data (intake is still pending, but data is resolved).
    assert route.compute_next_step(tmp_path).next_step == "intake"


def test_commit_refuses_on_header_mismatch(tmp_path):
    """A documented field that drifts from the CSV header refuses; STATE untouched."""
    _write_state(tmp_path)
    _write_csv(tmp_path, "data/sales.csv")
    data.profile(tmp_path)

    # The model corrupts a field name (casing drift) during enrichment.
    model_path = tmp_path / "DATA-MODEL.md"
    model_path.write_text(
        model_path.read_text(encoding="utf-8").replace("| Region |", "| region |"),
        encoding="utf-8",
    )

    result = data.commit(tmp_path)

    assert result.ok is False
    assert "sales.csv" in result.message
    assert "sales.csv" in result.mismatches
    assert route.parse_state(tmp_path / "STATE.md").statuses["data"] == "pending"


def test_commit_refuses_when_data_model_absent(tmp_path):
    """Approving without a DATA-MODEL.md on disk is refused (nothing to validate)."""
    _write_state(tmp_path)
    _write_csv(tmp_path, "data/sales.csv")

    result = data.commit(tmp_path)

    assert result.ok is False and "DATA-MODEL.md" in result.message


def test_commit_validates_against_scaffold_demo(tmp_path):
    """A documented CSV that lives only in the scaffold demo still validates (§3.1)."""
    _write_state(tmp_path)
    _write_csv(tmp_path, "scaffold/sample-data/demo.csv")
    data.profile(tmp_path)

    result = data.commit(tmp_path)

    assert result.ok is True and result.validated == ["demo.csv"]


def test_rerun_marks_downstream_approved_steps_stale(tmp_path):
    """Re-running data flips downstream approved steps to stale; others untouched."""
    # Mid-pipeline: data approved, brand+plan approved, mock pending.
    _write_state(tmp_path, data="approved", brand="approved", plan="approved")
    _write_csv(tmp_path, "data/sales.csv")
    data.profile(tmp_path)

    result = data.commit(tmp_path)

    assert result.ok is True
    # brand and plan (downstream, approved) flip to stale, in canonical order.
    assert result.staled_steps == ["brand", "plan"]

    statuses = route.parse_state(tmp_path / "STATE.md").statuses
    assert statuses["brand"] == "stale" and statuses["plan"] == "stale"
    assert statuses["mock"] == "pending"     # was pending -> left as-is
    assert statuses["data"] == "approved"    # its own step re-approved

    # Cross-check through the router: intake is pending, so it routes there first;
    # but brand is now stale too. Confirm the staleness landed in STATE.md above.


def test_first_run_marks_nothing_stale(tmp_path):
    """On a first approval there is nothing downstream approved, so nothing goes stale."""
    _write_state(tmp_path)
    _write_csv(tmp_path, "data/sales.csv")
    data.profile(tmp_path)

    result = data.commit(tmp_path)

    assert result.ok is True and result.staled_steps == []
