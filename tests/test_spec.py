"""Contract test for tableau-spec (CONTRACT.md step 7, §4.1, §4.2, §4.3).

``tableau-spec`` turns the approved ``mock.html`` into an ``IMPLEMENTATION-SPEC.md`` that
maps every mock element to a Tableau construct. The spec prose is model-authored, so this
test pins the parts the scripts must guarantee mechanically, split across the two modules:

* :mod:`reconcile` (pure core) - parsing the mock's ``data-plan-id`` elements and the
  spec's Element Mapping table, the coverage reconciliation (nothing unmapped), and the
  simplest-primitive guard (an advanced feature needs a justification);
* :mod:`spec` (orchestration) - the entry gate (§4.1), deliverable versioning (spec writes
  into the mock's ``current_version`` and never bumps it, §4.3), and the STATE.md
  ``approved`` + downstream-``stale`` transition (§4.2), cross-checked through the *router*
  so the STATE.md spec writes routes on.
"""

from pathlib import Path

import init  # builds a realistic STATE.md the same way a real project would
import reconcile  # the pure reconciliation/guard core (on sys.path via conftest.py)
import route  # the router parses/routes the STATE.md spec writes
import spec  # the orchestration under test

TARGET_VERSION = "2024.2-2025.x"

# A minimal plan (spec reads it for context; the mock's data-plan-ids drive reconciliation).
PLAN = "# Dashboard Plan: Sales\n\n## Screen Size\n- dimensions: 1366 x 768 px\n"

# The mock renders four elements, each tagged with a data-plan-id.
MOCK_IDS = ("kpi-revenue", "chart-trend", "flt-region", "int-region-filter")


def _mock_html(ids=MOCK_IDS) -> str:
    """Render a mock.html tagging each id with data-plan-id (the reconciliation source)."""
    tagged = "\n".join(f'<div data-plan-id="{plan_id}"></div>' for plan_id in ids)
    return f"<!doctype html><html><body>\n{tagged}\n</body></html>"


def _layout_json(ids) -> str:
    """Render a minimal valid Layout JSON: a vert root placing each zone id once.

    Interaction ids (``int-*``) are actions, not zones, so they are never placed.
    """
    zone_ids = [i for i in ids if not i.startswith("int-")]
    share = round(100 / len(zone_ids), 2) if zone_ids else 100
    children = ", ".join(f'{{"id": "{i}", "size": {share}}}' for i in zone_ids)
    return (
        '{"canvas": {"width": 1366, "height": 768}, '
        f'"root": {{"type": "vert", "children": [{children}]}}}}'
    )


def _spec_md(rows=None, layout=None) -> str:
    """Render an IMPLEMENTATION-SPEC.md with an Element Mapping table and Layout section.

    Args:
        rows: Iterable of ``(id, construct, justification)`` tuples. Defaults to a full,
            all-simple mapping of every ``MOCK_IDS`` element.
        layout: The Layout section's fenced JSON text. Defaults to a valid tree placing
            each mapped zone id once; pass ``""`` to omit the Layout section entirely.

    Returns:
        A spec markdown string.
    """
    if rows is None:
        rows = [
            ("kpi-revenue", "Text mark, SUM([revenue])", "-"),
            ("chart-trend", "Line mark: MONTH([order_date]) x SUM([revenue])", "-"),
            ("flt-region", "Filter card on [region]", "-"),
            ("int-region-filter", "Filter action (Use as Filter)", "-"),
        ]
    if layout is None:
        layout = _layout_json([i for i, _, _ in rows])
    body = "\n".join(f"| {i} | {c} | {j} |" for i, c, j in rows)
    layout_section = (
        f"\n## Layout\n\nA vert stack of the mapped zones.\n\n```json\n{layout}\n```\n"
        if layout else ""
    )
    return (
        "# Implementation Spec: Sales\n\n## Element Mapping\n"
        "| id | tableau construct | justification |\n"
        "|----|-------------------|---------------|\n"
        f"{body}\n{layout_section}"
    )


def _state_with(project_dir: Path, **status_overrides: str) -> None:
    """Write a canonical STATE.md, optionally overriding some step statuses."""
    text = init.render_state_md(TARGET_VERSION)
    if status_overrides:
        text = spec.apply_status_updates(text, dict(status_overrides))
    (project_dir / "STATE.md").write_text(text, encoding="utf-8")


def _write_mock(project_dir: Path, version: str, html: str) -> None:
    """Write ``html`` to ``mock-version/<version>/mock.html``."""
    version_dir = project_dir / spec.VERSION_DIR / version
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / spec.MOCK_FILENAME).write_text(html, encoding="utf-8")


def _write_spec(project_dir: Path, version: str, text: str) -> None:
    """Write ``text`` to ``mock-version/<version>/IMPLEMENTATION-SPEC.md``."""
    version_dir = project_dir / spec.VERSION_DIR / version
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / spec.SPEC_FILENAME).write_text(text, encoding="utf-8")


def _ready_project(project_dir: Path, **status_overrides: str) -> None:
    """Open spec's entry gate: plan+mock resolved, DASHBOARD-PLAN.md and mock.html on disk."""
    _state_with(project_dir, plan="approved", mock="approved", **status_overrides)
    (project_dir / "DASHBOARD-PLAN.md").write_text(PLAN, encoding="utf-8")
    _write_mock(project_dir, "v_1", _mock_html())


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def test_precheck_blocks_when_no_state(tmp_path):
    """No STATE.md => spec cannot run; the blocker points at tableau-init."""
    result = spec.precheck(tmp_path)

    assert result.can_run is False
    assert "tableau-init" in result.blocker


def test_precheck_blocks_when_plan_not_resolved(tmp_path):
    """plan must be resolved before spec runs (§4.1)."""
    _state_with(tmp_path, plan="pending", mock="approved")
    (tmp_path / "DASHBOARD-PLAN.md").write_text(PLAN, encoding="utf-8")
    _write_mock(tmp_path, "v_1", _mock_html())

    result = spec.precheck(tmp_path)

    assert result.can_run is False
    assert "plan" in result.blocker and "pending" in result.blocker


def test_precheck_blocks_when_mock_not_resolved(tmp_path):
    """mock must be resolved too - spec maps the elements the mock rendered (§4.1)."""
    _state_with(tmp_path, plan="approved", mock="pending")
    (tmp_path / "DASHBOARD-PLAN.md").write_text(PLAN, encoding="utf-8")
    _write_mock(tmp_path, "v_1", _mock_html())

    result = spec.precheck(tmp_path)

    assert result.can_run is False
    assert "mock" in result.blocker and "pending" in result.blocker


def test_precheck_blocks_when_mock_file_missing(tmp_path):
    """Even with mock approved, a missing mock.html at current_version blocks spec (§4.1)."""
    _state_with(tmp_path, plan="approved", mock="approved")
    (tmp_path / "DASHBOARD-PLAN.md").write_text(PLAN, encoding="utf-8")  # no mock.html

    result = spec.precheck(tmp_path)

    assert result.can_run is False
    assert "mock.html" in result.blocker


def test_precheck_reports_elements_and_target(tmp_path):
    """precheck surfaces the mock element ids to map and where to write the spec."""
    _ready_project(tmp_path)

    result = spec.precheck(tmp_path)

    assert result.can_run is True
    assert result.element_ids == list(MOCK_IDS)
    assert result.version == "v_1"
    assert result.mock_path == "mock-version/v_1/mock.html"
    assert result.target_path == "mock-version/v_1/IMPLEMENTATION-SPEC.md"


# --- Reconciliation parsing (reconcile.py) -----------------------------------

def test_mock_element_ids_reads_data_plan_ids():
    """Every data-plan-id in the mock, de-duplicated in first-seen order, must be mapped."""
    assert reconcile.mock_element_ids(_mock_html()) == list(MOCK_IDS)


def test_parse_spec_mappings_reads_the_element_mapping_table():
    """The Element Mapping table (header id + construct) is parsed into rows."""
    mappings = reconcile.parse_spec_mappings(_spec_md())

    assert [m.id for m in mappings] == list(MOCK_IDS)
    assert mappings[0].construct == "Text mark, SUM([revenue])"


def test_advanced_features_detected_by_keyword():
    """The guard recognises DZV / LOD / table calc / parameter action in a construct."""
    assert reconcile.advanced_features_in("Dynamic Zone Visibility toggle") == [
        "Dynamic Zone Visibility"
    ]
    assert reconcile.advanced_features_in("{FIXED [region]: SUM([sales])}") == [
        "LOD expression"
    ]
    assert reconcile.advanced_features_in("WINDOW_SUM(SUM([x]))") == ["table calculation"]
    assert reconcile.advanced_features_in("a parameter action swaps the measure") == [
        "parameter action"
    ]
    assert reconcile.advanced_features_in("Text mark, SUM([revenue])") == []


def test_justification_present_rejects_placeholders():
    """A blank / dash / template-placeholder justification cell reads as absent."""
    assert reconcile.justification_present("show/hide can't collapse a zone") is True
    assert reconcile.justification_present("-") is False
    assert reconcile.justification_present("") is False
    assert reconcile.justification_present("<why...>") is False


# --- reconcile: coverage + guard ---------------------------------------------

def test_reconcile_full_spec_passes():
    """A spec mapping every mock element with simple primitives reconciles cleanly."""
    validation = reconcile.reconcile(_mock_html(), _spec_md())

    assert validation.ok is True
    assert validation.unmapped == []
    assert validation.unjustified == []


def test_reconcile_flags_unmapped_element():
    """A mock element with no mapping row is unmapped and fails reconciliation (AC)."""
    # Drop the interaction row from the spec.
    rows = [
        ("kpi-revenue", "Text mark, SUM([revenue])", "-"),
        ("chart-trend", "Line mark", "-"),
        ("flt-region", "Filter card on [region]", "-"),
    ]
    validation = reconcile.reconcile(_mock_html(), _spec_md(rows))

    assert validation.ok is False
    assert validation.unmapped == ["int-region-filter"]


def test_reconcile_flags_unjustified_advanced_feature():
    """An advanced feature with a blank justification is flagged as over-engineering (AC)."""
    rows = [
        ("kpi-revenue", "Text mark, SUM([revenue])", "-"),
        ("chart-trend", "Line mark", "-"),
        ("flt-region", "Filter card on [region]", "-"),
        ("int-region-filter", "parameter action swaps region", "-"),  # advanced, no reason
    ]
    validation = reconcile.reconcile(_mock_html(), _spec_md(rows))

    assert validation.ok is False
    assert [item.id for item in validation.unjustified] == ["int-region-filter"]
    assert validation.unmapped == []


def test_reconcile_accepts_justified_advanced_feature():
    """The same advanced feature passes once a real justification is written."""
    rows = [
        ("kpi-revenue", "Text mark, SUM([revenue])", "-"),
        ("chart-trend", "Line mark", "-"),
        ("flt-region", "Filter card on [region]", "-"),
        (
            "int-region-filter",
            "parameter action swaps region",
            "a filter action can't rewrite the measure used across sheets",
        ),
    ]
    validation = reconcile.reconcile(_mock_html(), _spec_md(rows))

    assert validation.ok is True


def test_reconcile_notes_extra_mapped_ids():
    """A spec row for an id not in the mock is a non-fatal note, not a failure."""
    rows = list(
        [
            ("kpi-revenue", "Text mark", "-"),
            ("chart-trend", "Line mark", "-"),
            ("flt-region", "Filter card", "-"),
            ("int-region-filter", "Filter action", "-"),
            ("ghost-id", "Bar mark", "-"),  # not in the mock
        ]
    )
    validation = reconcile.reconcile(_mock_html(), _spec_md(rows))

    assert validation.ok is True
    assert validation.extra_ids == ["ghost-id"]


# --- Layout container tree (issue #32) ----------------------------------------

def test_reconcile_flags_missing_layout_section():
    """A spec with no ## Layout section fails with an actionable message (AC)."""
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout=""))

    assert validation.ok is False
    assert any("Layout section missing" in error for error in validation.layout_errors)


def test_reconcile_flags_unparseable_layout_json():
    """A Layout block that is not valid JSON fails with a parse error."""
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout="{not json"))

    assert validation.ok is False
    assert any("does not parse" in error for error in validation.layout_errors)


def test_reconcile_flags_mapped_id_absent_from_tree():
    """A mapped zone id with no leaf in the tree blocks approval (AC)."""
    layout = (
        '{"canvas": {"width": 1366, "height": 768}, "root": {"type": "vert", "children": '
        '[{"id": "kpi-revenue", "size": 50}, {"id": "chart-trend", "size": 50}]}}'
    )  # flt-region unplaced
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout=layout))

    assert validation.ok is False
    assert any(
        "missing from the layout tree" in error and "flt-region" in error
        for error in validation.layout_errors
    )


def test_reconcile_flags_id_placed_more_than_once():
    """A zone id placed twice in the tree blocks approval (AC)."""
    layout = (
        '{"canvas": {"width": 1366, "height": 768}, "root": {"type": "vert", "children": '
        '[{"id": "kpi-revenue", "size": 25}, {"id": "kpi-revenue", "size": 25}, '
        '{"id": "chart-trend", "size": 25}, {"id": "flt-region", "size": 25}]}}'
    )
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout=layout))

    assert validation.ok is False
    assert any(
        "more than once" in error and "kpi-revenue" in error
        for error in validation.layout_errors
    )


def test_reconcile_flags_tree_id_with_no_mapping_row():
    """An id in the tree that has no Element Mapping row blocks approval (AC)."""
    layout = (
        '{"canvas": {"width": 1366, "height": 768}, "root": {"type": "vert", "children": '
        '[{"id": "kpi-revenue", "size": 25}, {"id": "chart-trend", "size": 25}, '
        '{"id": "flt-region", "size": 25}, {"id": "ghost-zone", "size": 25}]}}'
    )
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout=layout))

    assert validation.ok is False
    assert any(
        "no Element Mapping row" in error and "ghost-zone" in error
        for error in validation.layout_errors
    )


def test_reconcile_flags_insane_sibling_sizes():
    """Sibling sizes far from 100% block approval (AC)."""
    layout = (
        '{"canvas": {"width": 1366, "height": 768}, "root": {"type": "vert", "children": '
        '[{"id": "kpi-revenue", "size": 10}, {"id": "chart-trend", "size": 10}, '
        '{"id": "flt-region", "size": 10}]}}'
    )
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout=layout))

    assert validation.ok is False
    assert any("sum to 30" in error for error in validation.layout_errors)


def test_reconcile_flags_an_equal_group_stranded_with_a_smaller_sibling():
    """Two equal cards above a 3% legend strip cannot be distributed evenly: the build pins
    every child but the biggest, so the cards drift apart as the dashboard grows. The author
    is told to wrap the equal group in its own container (issue #63)."""
    layout = (
        '{"canvas": {"width": 1366, "height": 768}, "root": {"type": "vert", "children": '
        '[{"id": "kpi-revenue", "size": 48.5}, {"id": "chart-trend", "size": 48.5}, '
        '{"id": "flt-region", "size": 3}]}}'
    )
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout=layout))

    assert validation.ok is False
    assert any(
        "wrap the equal group in its own container" in error
        for error in validation.layout_errors
    )


def test_reconcile_accepts_equal_siblings_under_a_bigger_one():
    """Equal siblings *below* the biggest child are all pinned at equal sizes and stay
    equal - they just do not grow. Only a stranded *largest* group is a problem."""
    layout = (
        '{"canvas": {"width": 1366, "height": 768}, "root": {"type": "vert", "children": '
        '[{"id": "kpi-revenue", "size": 60}, {"id": "chart-trend", "size": 20}, '
        '{"id": "flt-region", "size": 20}]}}'
    )
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout=layout))

    assert validation.layout_errors == []


def test_reconcile_flags_missing_canvas():
    """The canvas dimensions are required (the mock's design size drives the build)."""
    layout = (
        '{"root": {"type": "vert", "children": [{"id": "kpi-revenue", "size": 34}, '
        '{"id": "chart-trend", "size": 33}, {"id": "flt-region", "size": 33}]}}'
    )
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout=layout))

    assert validation.ok is False
    assert any("'canvas'" in error for error in validation.layout_errors)


def test_reconcile_accepts_nested_containers():
    """A well-formed nested vert/horz tree with sane sizes validates [OK] (AC)."""
    layout = (
        '{"canvas": {"width": 1366, "height": 768}, "root": {"type": "vert", "children": '
        '[{"id": "flt-region", "size": 10}, '
        '{"type": "horz", "size": 90, "children": '
        '[{"id": "kpi-revenue", "size": 40}, {"id": "chart-trend", "size": 60}]}]}}'
    )
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout=layout))

    assert validation.ok is True
    assert validation.layout_errors == []


def test_reconcile_accepts_mapped_container():
    """A container may itself carry a mapped id (e.g. a DZV panel holding zones)."""
    ids = ("pnl-details", "kpi-revenue", "chart-trend")
    rows = [
        ("pnl-details", "Dynamic Zone Visibility on the details container", "no simpler toggle"),
        ("kpi-revenue", "Text mark, SUM([revenue])", "-"),
        ("chart-trend", "Line mark", "-"),
    ]
    layout = (
        '{"canvas": {"width": 1366, "height": 768}, "root": {"type": "vert", "children": '
        '[{"id": "chart-trend", "size": 60}, '
        '{"id": "pnl-details", "type": "horz", "size": 40, "children": '
        '[{"id": "kpi-revenue", "size": 100}]}]}}'
    )
    validation = reconcile.reconcile(_mock_html(ids), _spec_md(rows, layout=layout))

    assert validation.ok is True
    assert validation.layout_errors == []


def test_reconcile_interaction_ids_stay_out_of_the_tree():
    """int-* ids are actions, not zones: not required in the tree, and rejected if placed."""
    placed_action = (
        '{"canvas": {"width": 1366, "height": 768}, "root": {"type": "vert", "children": '
        '[{"id": "kpi-revenue", "size": 25}, {"id": "chart-trend", "size": 25}, '
        '{"id": "flt-region", "size": 25}, {"id": "int-region-filter", "size": 25}]}}'
    )
    validation = reconcile.reconcile(_mock_html(), _spec_md(layout=placed_action))

    assert validation.ok is False
    assert any("occupy no zone" in error for error in validation.layout_errors)
    # The default helper layout omits int-region-filter and is valid (see other tests).


def test_commit_refuses_spec_without_layout(tmp_path):
    """commit is gated on the Layout section too, STATE.md untouched (AC)."""
    _ready_project(tmp_path)
    _write_spec(tmp_path, "v_1", _spec_md(layout=""))

    result = spec.commit(tmp_path)

    assert result.ok is False
    assert "layout" in result.message.lower()
    assert route.parse_state(tmp_path / "STATE.md").statuses["spec"] == "pending"


# --- Versioning (CONTRACT.md §4.3: spec never bumps current_version) ----------

def test_spec_reads_and_writes_at_current_version():
    """Spec always targets current_version (the mock's dir); it never bumps (§4.3)."""
    text = init.render_state_md(TARGET_VERSION)
    text = spec.apply_status_updates(text, {"mock": "approved"})
    text = text.replace("current_version: v_1", "current_version: v_3")
    assert spec.read_current_version(text) == "v_3"


# --- Commit: approve, refuse (CONTRACT.md §4.1, §4.3) ------------------------

def test_commit_refuses_when_gate_closed(tmp_path):
    """The entry gate guards commit, not just precheck; STATE.md stays untouched."""
    _state_with(tmp_path, plan="pending", mock="approved")
    (tmp_path / "DASHBOARD-PLAN.md").write_text(PLAN, encoding="utf-8")
    _write_mock(tmp_path, "v_1", _mock_html())

    result = spec.commit(tmp_path)

    assert result.ok is False
    assert "plan" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["spec"] == "pending"


def test_commit_refuses_when_spec_absent(tmp_path):
    """Approving without an IMPLEMENTATION-SPEC.md on disk is refused."""
    _ready_project(tmp_path)

    result = spec.commit(tmp_path)

    assert result.ok is False
    assert "IMPLEMENTATION-SPEC.md" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["spec"] == "pending"


def test_commit_refuses_spec_with_unmapped_element(tmp_path):
    """commit re-runs reconciliation; an unmapped element is refused, STATE.md untouched."""
    _ready_project(tmp_path)
    rows = [
        ("kpi-revenue", "Text mark", "-"),
        ("chart-trend", "Line mark", "-"),
        ("flt-region", "Filter card", "-"),
    ]  # int-region-filter unmapped
    _write_spec(tmp_path, "v_1", _spec_md(rows))

    result = spec.commit(tmp_path)

    assert result.ok is False
    assert "unmapped" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["spec"] == "pending"


def test_commit_approves_valid_spec(tmp_path):
    """A spec mapping every element commits: spec -> approved, current_version set."""
    _ready_project(tmp_path)
    _write_spec(tmp_path, "v_1", _spec_md())

    result = spec.commit(tmp_path)

    assert result.ok is True
    assert result.version == "v_1"
    assert route.parse_state(tmp_path / "STATE.md").statuses["spec"] == "approved"


# --- Staleness propagation + versioning (CONTRACT.md §4.2, §4.3) -------------

def test_rerun_after_approval_overwrites_in_place_and_stales_build(tmp_path):
    """A post-approval re-run overwrites the spec at current_version (no bump) and stales build."""
    _ready_project(
        tmp_path,
        intake="approved", brand="approved", data="approved",
        spec="approved", build="approved",
    )
    # build's own required reads must exist so the router reaches the stale 'build' rather
    # than blocking on data (CONTRACT.md §1: build reads DATA-MODEL.md + data/*.csv).
    (tmp_path / "DATA-MODEL.md").write_text("# Data Model\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "x.csv").write_text("region,revenue\nWest,10\n", encoding="utf-8")
    # Spec never bumps: it re-writes into the mock's current_version (still v_1).
    _write_spec(tmp_path, "v_1", _spec_md())

    result = spec.commit(tmp_path)

    assert result.ok is True
    assert result.version == "v_1"  # no bump - spec overwrites in place (§4.3)
    assert result.staled_steps == ["build"]

    state = route.parse_state(tmp_path / "STATE.md")
    assert state.statuses["spec"] == "approved" and state.statuses["build"] == "stale"
    assert state.current_version == "v_1"  # only tableau-mock bumps current_version

    # Cross-check through the router: the first unresolved step is now stale 'build'.
    routed = route.compute_next_step(tmp_path)
    assert routed.next_step == "build" and "stale" in routed.reason.lower()


def test_first_run_marks_nothing_stale(tmp_path):
    """On a first approval there is nothing downstream approved, so nothing goes stale."""
    _ready_project(tmp_path)
    _write_spec(tmp_path, "v_1", _spec_md())

    result = spec.commit(tmp_path)

    assert result.ok is True and result.staled_steps == []


# --- spec_mode gate (issue #98) ----------------------------------------------

def test_read_spec_mode_absent_means_unset():
    """Projects that predate spec_mode have no line; read returns None."""
    text = init.render_state_md(TARGET_VERSION)
    assert spec.read_spec_mode(text) is None
    assert spec.effective_spec_mode(text) == spec.SPEC_MODE_AGENT


def test_set_spec_mode_inserts_beside_data_mode_preserving_comments():
    """set-mode writes the bullet after data_mode and keeps inline comments."""
    text = init.render_state_md(TARGET_VERSION)
    updated = spec.set_spec_mode(text, "human")

    assert spec.read_spec_mode(updated) == "human"
    assert "- spec_mode: human" in updated
    assert "# agent | human" in updated
    # data_mode line and its comment survive byte-for-byte beside the new bullet.
    assert "- data_mode: csv" in updated
    assert updated.count("spec_mode:") == 1


def test_set_spec_mode_rewrites_existing_value_only():
    """A second set-mode call rewrites the value without duplicating the bullet."""
    text = spec.set_spec_mode(init.render_state_md(TARGET_VERSION), "human")
    again = spec.set_spec_mode(text, "agent")

    assert spec.read_spec_mode(again) == "agent"
    assert again.count("spec_mode:") == 1


def test_precheck_reports_unset_spec_mode(tmp_path):
    """precheck reports that spec_mode is unset and must be chosen; agent route still runs."""
    _ready_project(tmp_path)

    result = spec.precheck(tmp_path)
    rendered = spec.format_precheck(result)

    assert result.can_run is True
    assert result.spec_mode is None
    assert result.effective_spec_mode == "agent"
    assert "unset" in rendered.lower() and "must be chosen" in rendered.lower()


def test_precheck_reports_recorded_spec_mode(tmp_path):
    """Once recorded, precheck surfaces the stored mode."""
    _ready_project(tmp_path)
    state_path = tmp_path / "STATE.md"
    state_path.write_text(
        spec.set_spec_mode(state_path.read_text(encoding="utf-8"), "human"),
        encoding="utf-8",
    )

    result = spec.precheck(tmp_path)
    rendered = spec.format_precheck(result)

    assert result.spec_mode == "human"
    assert result.effective_spec_mode == "human"
    assert "spec_mode      : human" in rendered


def test_unset_spec_mode_keeps_agent_route_commit(tmp_path):
    """A STATE.md with no spec_mode still commits the agent-route spec exactly as before."""
    _ready_project(tmp_path)
    _write_spec(tmp_path, "v_1", _spec_md())

    result = spec.commit(tmp_path)

    assert result.ok is True
    assert "spec_mode" not in (tmp_path / "STATE.md").read_text(encoding="utf-8")


def test_cli_set_mode_writes_state(tmp_path):
    """The set-mode subcommand records spec_mode in STATE.md."""
    _ready_project(tmp_path)

    code = spec.main(["set-mode", str(tmp_path), "--mode", "agent"])

    assert code == 0
    assert spec.read_spec_mode((tmp_path / "STATE.md").read_text(encoding="utf-8")) == "agent"


# --- Agent-route multi-view block (issue #100) --------------------------------

_MULTI_VIEW_PLAN = """# Dashboard Plan: Multi

## Layout Grid
| slot | position | size | view |
|------|----------|------|------|
| a | top | 100% | Overview |
| b | bottom | 100% | Detail |

## Elements
| id | type | columns | slot | size | view |
|----|------|---------|------|------|------|
| e1 | kpi | x | a | 1 | Overview |
| e2 | kpi | y | b | 1 | Detail |
"""

_SINGLE_NAMED_VIEW_PLAN = """# Dashboard Plan: Single Named

## Layout Grid
| slot | position | size | view |
|------|----------|------|------|
| a | top | 100% | Overview |

## Elements
| id | type | columns | slot | size | view |
|----|------|---------|------|------|------|
| e1 | kpi | x | a | 1 | Overview |
"""


def _write_plan(project_dir: Path, plan_text: str) -> None:
    """Overwrite DASHBOARD-PLAN.md with ``plan_text``."""
    (project_dir / spec.PLAN_FILENAME).write_text(plan_text, encoding="utf-8")


def test_agent_precheck_blocks_multi_view_plan(tmp_path):
    """Agent-route precheck returns [BLOCKED] when the plan declares 2+ views."""
    _ready_project(tmp_path)
    _write_plan(tmp_path, _MULTI_VIEW_PLAN)
    # Unset spec_mode => agent route (effective).
    assert spec.read_spec_mode((tmp_path / "STATE.md").read_text(encoding="utf-8")) is None

    result = spec.precheck(tmp_path)
    rendered = spec.format_precheck(result)

    assert result.can_run is False
    assert result.blocker is not None
    assert "single dashboard" in result.blocker.lower() or "emits a single dashboard" in result.blocker
    assert "human" in result.blocker.lower()
    assert "multi-dashboard" in result.blocker.lower()
    assert rendered.startswith("[BLOCKED]")
    assert "human" in rendered.lower()
    assert "multi-dashboard" in rendered.lower()


def test_agent_commit_blocks_multi_view_plan(tmp_path):
    """Agent-route commit returns [BLOCKED] on a multi-view plan; STATE.md untouched."""
    _ready_project(tmp_path)
    _write_plan(tmp_path, _MULTI_VIEW_PLAN)
    _write_spec(tmp_path, "v_1", _spec_md())
    before = (tmp_path / "STATE.md").read_text(encoding="utf-8")

    result = spec.commit(tmp_path)
    rendered = spec.format_commit(result)

    assert result.ok is False
    assert result.blocked is True
    assert "human" in result.message.lower()
    assert "multi-dashboard" in result.message.lower()
    assert rendered.startswith("[BLOCKED]")
    assert (tmp_path / "STATE.md").read_text(encoding="utf-8") == before


def test_agent_single_view_plan_is_unaffected(tmp_path):
    """A single-view plan (named or default) still prechecks and commits on the agent route."""
    _ready_project(tmp_path)
    _write_plan(tmp_path, _SINGLE_NAMED_VIEW_PLAN)
    _write_spec(tmp_path, "v_1", _spec_md())

    pre = spec.precheck(tmp_path)
    assert pre.can_run is True
    assert pre.effective_spec_mode == spec.SPEC_MODE_AGENT

    committed = spec.commit(tmp_path)
    assert committed.ok is True
    assert committed.blocked is False


def test_human_route_not_blocked_by_view_count(tmp_path):
    """Human route precheck is not gated by the number of declared views."""
    _ready_project(tmp_path)
    _write_plan(tmp_path, _MULTI_VIEW_PLAN)
    state_path = tmp_path / "STATE.md"
    state_path.write_text(
        spec.set_spec_mode(state_path.read_text(encoding="utf-8"), "human"),
        encoding="utf-8",
    )

    pre = spec.precheck(tmp_path)
    assert pre.can_run is True
    assert pre.effective_spec_mode == "human"


def _write_human_guide(
    project_dir: Path, element_ids: list[str], views: list[str] | None = None
) -> None:
    """Write a minimal human-route guide covering ``element_ids``."""
    import human_check

    views = views or ["default"]
    (project_dir / "IMPLEMENTATION-SPEC.md").write_text(
        "# Implementation Spec\n\n## Decisions\n\n- none\n",
        encoding="utf-8",
    )
    spec_dir = project_dir / "spec"
    spec_dir.mkdir(exist_ok=True)
    per_view = {v: [] for v in views}
    for i, eid in enumerate(element_ids):
        per_view[views[i % len(views)]].append(eid)
    for view in views:
        body = [f"# View: {view}", ""]
        for eid in per_view[view]:
            body += [
                f"## {eid}",
                f"Element: {eid}",
                "",
                "| slot | value |",
                "|------|-------|",
                "| Text | x |",
                "",
            ]
        if not per_view[view]:
            body += ["(no elements on this view)", ""]
        fname = human_check.view_page_filename(view)
        (spec_dir / fname).write_text("\n".join(body), encoding="utf-8")


def test_human_commit_approves_and_skips_build(tmp_path):
    """Human commit approves spec and sets build=skipped when the guide covers the plan."""
    _ready_project(tmp_path)
    plan = """# Dashboard Plan

## Elements
| id | type | view |
|----|------|------|
| kpi-revenue | kpi | Overview |

## Filters
| id | field | view |
|----|-------|------|
| flt-region | region | Overview |

## Interactions
| id | interaction |
|----|-------------|
| int-region-filter | filter |
"""
    _write_plan(tmp_path, plan)
    state_path = tmp_path / "STATE.md"
    state_path.write_text(
        spec.set_spec_mode(state_path.read_text(encoding="utf-8"), "human"),
        encoding="utf-8",
    )
    _write_human_guide(
        tmp_path,
        ["kpi-revenue", "flt-region", "int-region-filter"],
        views=["Overview"],
    )

    result = spec.commit(tmp_path)
    assert result.ok is True, result.message
    statuses = spec.parse_statuses((tmp_path / "STATE.md").read_text(encoding="utf-8"))
    assert statuses["spec"] == "approved"
    assert statuses["build"] == "skipped"


def test_human_commit_refuses_missing_element_lines(tmp_path):
    """Human commit refuses when a plan id has no Element: carrier."""
    _ready_project(tmp_path)
    plan = """# Dashboard Plan

## Elements
| id | type | view |
|----|------|------|
| kpi-revenue | kpi | Overview |
| chart-trend | chart | Overview |
"""
    _write_plan(tmp_path, plan)
    state_path = tmp_path / "STATE.md"
    state_path.write_text(
        spec.set_spec_mode(state_path.read_text(encoding="utf-8"), "human"),
        encoding="utf-8",
    )
    _write_human_guide(tmp_path, ["kpi-revenue"], views=["Overview"])

    result = spec.commit(tmp_path)
    assert result.ok is False
    assert "chart-trend" in result.message
    statuses = spec.parse_statuses((tmp_path / "STATE.md").read_text(encoding="utf-8"))
    assert statuses.get("spec") != "approved"
