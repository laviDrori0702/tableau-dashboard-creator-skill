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
from tests.test_vds import FakeSession, _datasources_payload  # shared fake VDS transport

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


def test_render_sanitizes_newlines_and_pipes_in_cells():
    """Newlines/pipes in samples or descriptions stay in one table row/cell.

    Real VDS data can carry both (e.g. a multi-line field description, or pipe-laden
    category values). They must not corrupt the markdown table, and the field name +
    type must still round-trip through parse_data_model.
    """
    messy = data.CsvProfile(filename="messy.csv", row_count=1, fields=[
        data.FieldProfile(
            name="Category", type="string", role="Dimension",
            samples=["Electronics|Audio|Headphones", "Home|Kitchen"],
            description="The product's rating (e.g. 4.2).\nNote: aggregate, not per-row.",
        ),
    ])

    text = data.render_data_model([messy], data.TIER_PUBLISHED_DS)

    # Exactly one physical line carries the Category row (no split on the newline).
    category_rows = [ln for ln in text.splitlines() if ln.startswith("| Category |")]
    assert len(category_rows) == 1
    row = category_rows[0]
    assert "\n" not in row and "Note: aggregate" in row   # description folded onto one line
    assert "Electronics\\|Audio" in row                    # data pipes escaped, not bare

    # Field name + type still recover cleanly for header validation.
    recovered = data.parse_data_model(text)["messy.csv"]
    assert recovered == [("Category", "string")]


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


# --- pull: published-ds route via VDS (CONTRACT.md §3.2) ---------------------

def _write_env(project_dir: Path) -> None:
    """Write a full, valid Tableau connection .env at the project root."""
    (project_dir / ".env").write_text(
        "TABLEAU_SERVER=https://pod.online.tableau.com\n"
        "TABLEAU_SITE=mysite\n"
        "TABLEAU_PAT_NAME=tok\n"
        "TABLEAU_PAT_SECRET=secret\n",
        encoding="utf-8",
    )


def _write_datasources(project_dir: Path, *entries: tuple[str, str]) -> None:
    """Write datasources.json from (ds_name, project_name) pairs (keyed ds_1, ds_2…)."""
    import json
    body = {"_comment": "test"}
    for index, (ds_name, project_name) in enumerate(entries, start=1):
        body[f"ds_{index}"] = {"ds_name": ds_name, "project_name": project_name}
    (project_dir / "datasources.json").write_text(json.dumps(body), encoding="utf-8")


def _vds_session(*, luid="luid-1", ds_name="Superstore", project="Samples",
                 metadata=None, rows=None) -> FakeSession:
    """A FakeSession primed for one source's full sign-in -> metadata -> query flow."""
    metadata = metadata if metadata is not None else {"data": [
        {"fieldCaption": "Order ID", "dataType": "STRING", "description": "Unique order id"},
        {"fieldCaption": "Revenue", "dataType": "REAL"},
        {"fieldCaption": "Paid", "dataType": "BOOLEAN"},
    ]}
    rows = rows if rows is not None else {"data": [
        {"Order ID": "ORD-1", "Revenue": 10.5, "Paid": True},
        {"Order ID": "ORD-2", "Revenue": 20.0, "Paid": False},
    ]}
    return FakeSession(
        signin={"credentials": {"token": "auth-tok", "site": {"id": "site-1"}}},
        datasources=_datasources_payload((luid, ds_name, project)),
        metadata=metadata,
        query=rows,
    )


def test_pull_writes_csv_and_data_model_with_published_tier(tmp_path):
    """A successful pull writes data/<slug>.csv + DATA-MODEL.md and flips data_mode."""
    _write_state(tmp_path)
    _write_datasources(tmp_path, ("Superstore", "Samples"))
    _write_env(tmp_path)

    result = data.pull(tmp_path, session=_vds_session())

    assert result.ok is True
    assert result.tier == data.TIER_PUBLISHED_DS
    assert result.written == ["superstore.csv"]
    assert result.row_counts == {"superstore.csv": 2}

    # The CSV header mirrors the field captions; booleans are lower-cased.
    csv_text = (tmp_path / "data" / "superstore.csv").read_text(encoding="utf-8")
    assert csv_text.splitlines()[0] == "Order ID,Revenue,Paid"
    assert "ORD-1,10.5,true" in csv_text

    # DATA-MODEL.md carries the published-ds tier, the metadata types, and the
    # metadata-provided description (pre-filled, not blank).
    model_text = (tmp_path / "DATA-MODEL.md").read_text(encoding="utf-8")
    assert data.parse_acquisition_tier(model_text) == data.TIER_PUBLISHED_DS
    documented = data.parse_data_model(model_text)["superstore.csv"]
    assert dict(documented) == {"Order ID": "string", "Revenue": "real", "Paid": "boolean"}
    assert "Unique order id" in model_text

    # data_mode flipped in STATE.md.
    assert "data_mode: published-ds" in (tmp_path / "STATE.md").read_text(encoding="utf-8")


def test_pull_then_commit_validates_cleanly(tmp_path):
    """The pulled CSV headers match the documented fields, so commit approves."""
    _write_state(tmp_path)
    _write_datasources(tmp_path, ("Superstore", "Samples"))
    _write_env(tmp_path)
    data.pull(tmp_path, session=_vds_session())

    result = data.commit(tmp_path)

    assert result.ok is True and result.validated == ["superstore.csv"]
    assert route.parse_state(tmp_path / "STATE.md").statuses["data"] == "approved"


def test_pull_refuses_when_production_csv_exists(tmp_path):
    """Real CSVs in data/ win (Route 1); pull refuses rather than overwrite them."""
    _write_state(tmp_path)
    _write_datasources(tmp_path, ("Superstore", "Samples"))
    _write_env(tmp_path)
    _write_csv(tmp_path, "data/sales.csv")

    result = data.pull(tmp_path, session=_vds_session())

    assert result.ok is False and "Route 1" in result.message


def test_pull_force_does_not_clobber_analyst_csvs(tmp_path):
    """--force must NOT overwrite the analyst's own dropped CSVs (csv-tier model)."""
    _write_state(tmp_path)
    _write_datasources(tmp_path, ("Superstore", "Samples"))
    _write_env(tmp_path)
    _write_csv(tmp_path, "data/sales.csv")
    data.profile(tmp_path)   # writes a csv-tier DATA-MODEL.md for the dropped CSV

    result = data.pull(tmp_path, force=True, session=_vds_session())

    assert result.ok is False and "Route 1" in result.message
    assert (tmp_path / "data" / "sales.csv").exists()   # untouched


def test_pull_force_repulls_prior_published_output(tmp_path):
    """--force re-pulls over data/ that a prior published-ds pull wrote (its own output)."""
    _write_state(tmp_path)
    _write_datasources(tmp_path, ("Superstore", "Samples"))
    _write_env(tmp_path)
    first = data.pull(tmp_path, session=_vds_session())
    assert first.ok is True   # data/superstore.csv + published-ds-tier DATA-MODEL.md

    # A plain re-pull is refused (DATA-MODEL.md exists); --force re-pulls cleanly.
    assert data.pull(tmp_path, session=_vds_session()).ok is False
    forced = data.pull(tmp_path, force=True, session=_vds_session())

    assert forced.ok is True and forced.written == ["superstore.csv"]


def test_pull_refuses_when_data_model_exists_without_force(tmp_path):
    """An existing DATA-MODEL.md is preserved unless --force (like profile)."""
    _write_state(tmp_path)
    _write_datasources(tmp_path, ("Superstore", "Samples"))
    _write_env(tmp_path)
    (tmp_path / "DATA-MODEL.md").write_text("# pre-existing\n", encoding="utf-8")

    result = data.pull(tmp_path, session=_vds_session())

    assert result.ok is False and "already exists" in result.message


def test_pull_refuses_when_no_datasources_json(tmp_path):
    """The published-ds route needs datasources.json; absent => actionable refusal."""
    _write_state(tmp_path)
    _write_env(tmp_path)

    result = data.pull(tmp_path, session=_vds_session())

    assert result.ok is False and "datasources.json" in result.message


def test_pull_refuses_large_row_limit_until_confirmed(tmp_path):
    """row_limit > 1000 is refused unless confirm_large is set (CONTRACT.md §3.2)."""
    _write_state(tmp_path)
    _write_datasources(tmp_path, ("Superstore", "Samples"))
    _write_env(tmp_path)

    refused = data.pull(tmp_path, row_limit=2000, session=_vds_session())
    assert refused.ok is False and "confirm" in refused.message.lower()
    assert not (tmp_path / "DATA-MODEL.md").exists()   # nothing written

    confirmed = data.pull(
        tmp_path, row_limit=2000, confirm_large=True, session=_vds_session()
    )
    assert confirmed.ok is True and confirmed.row_limit == 2000


def test_pull_is_atomic_on_vds_failure(tmp_path):
    """A VDS failure writes no artifact and leaves STATE.md untouched (§3.2)."""
    _write_state(tmp_path)
    _write_datasources(tmp_path, ("Superstore", "Samples"))
    _write_env(tmp_path)
    # Zero-row query is a hard failure (no synthesized fallback).
    failing = _vds_session(rows={"data": []})

    result = data.pull(tmp_path, session=failing)

    assert result.ok is False and "zero rows" in result.message
    assert not (tmp_path / "DATA-MODEL.md").exists()
    assert not (tmp_path / "data").exists()
    state_text = (tmp_path / "STATE.md").read_text(encoding="utf-8")
    assert "data_mode: csv" in state_text   # not flipped
    assert route.parse_state(tmp_path / "STATE.md").statuses["data"] == "pending"


def test_pull_refuses_when_init_not_approved(tmp_path):
    """The entry gate guards pull too (§4.1)."""
    _write_state(tmp_path, init="pending")
    _write_datasources(tmp_path, ("Superstore", "Samples"))
    _write_env(tmp_path)

    result = data.pull(tmp_path, session=_vds_session())

    assert result.ok is False and "init" in result.message


def test_pull_multiple_sources_one_csv_each(tmp_path):
    """Each listed source becomes its own data/<slug>.csv ('csv = datasource')."""
    _write_state(tmp_path)
    _write_datasources(tmp_path, ("Superstore", "Samples"), ("Regional Sales", "Samples"))
    _write_env(tmp_path)
    # The fake's datasources payload must resolve BOTH names in project 'Samples'.
    session = FakeSession(
        signin={"credentials": {"token": "auth-tok", "site": {"id": "site-1"}}},
        datasources=_datasources_payload(
            ("luid-1", "Superstore", "Samples"),
            ("luid-2", "Regional Sales", "Samples"),
        ),
        metadata={"data": [{"fieldCaption": "Order ID", "dataType": "STRING"}]},
        query={"data": [{"Order ID": "ORD-1"}]},
    )

    result = data.pull(tmp_path, session=session)

    assert result.ok is True
    assert sorted(result.written) == ["regional_sales.csv", "superstore.csv"]
    assert (tmp_path / "data" / "regional_sales.csv").exists()
    assert (tmp_path / "data" / "superstore.csv").exists()
