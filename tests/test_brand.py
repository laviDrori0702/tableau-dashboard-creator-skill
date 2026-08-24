"""Contract test for tableau-brand (CONTRACT.md step 4, §3.1, §4.1, §4.2).

``tableau-brand`` is the skippable design-token step. Its token *content* is
model-authored, so this test pins the parts ``brand.py`` must mechanically
guarantee:

1. the entry gate (brand refuses until ``init`` is ``approved``, §4.1);
2. the precheck signals the skill branches on (existing tokens, branding source
   mode, logo/icon assets, and whether skip is allowed);
3. the DESIGN-TOKENS schema - a required core (Colors, Typography, Spacing,
   Fallback Decisions) is enforced while Logo/Icons/Sizing stay optional, and a
   refined file that keeps custom sections still validates;
4. the **skip precondition** - skipping is refused until ``branding/branding.md``
   exists, because branding is too important to skip blank;
5. the STATE.md transition + staleness propagation (§4.2), cross-checked by routing
   the resulting manifest through the *router's* own ``compute_next_step``.
"""

from pathlib import Path

import pytest

import brand   # the skill under test (on sys.path via conftest.py)
import init    # builds a realistic STATE.md the same way a real project would
import route   # the router parses/routes the STATE.md brand writes

TARGET_VERSION = "2024.2-2025.x"

# A schema-complete token file (required core present) plus optional sections.
FULL_TOKENS = (
    "# Design Tokens\n\n"
    "## Source\nbranding/branding.md\n\n"
    "## Typography\nFont: Tableau Medium.\n\n"
    "## Colors\nBlue #2e75b6.\n\n"
    "### Chart series colors\n1. #2e75b6\n2. #f39c12\n\n"
    "## Spacing\nCard padding 8px.\n\n"
    "## Dashboard Sizing\nRange, min 1100x800, max Flex.\n\n"
    "## Fallback Decisions\nNone — all values came from the brand source.\n"
)

# Only the required core - valid even with no logo, icons, or explicit sizing.
CORE_ONLY_TOKENS = (
    "# Design Tokens\n\n"
    "## Colors\nBlue #2e75b6.\n\n"
    "### Chart series colors\n1. #2e75b6\n2. #f39c12\n\n"
    "## Typography\nFont: Tableau Medium.\n\n"
    "## Spacing\nCard padding 8px.\n\n"
    "## Fallback Decisions\nNone.\n"
)

# Missing required sections (has Colors but no series colours/Typography/Spacing/Fallbacks).
INCOMPLETE_TOKENS = "# Design Tokens\n\n## Colors\nBlue #2e75b6.\n"


def _write_state(project_dir: Path, **status_overrides: str) -> None:
    """Write a canonical STATE.md, optionally overriding some step statuses.

    Args:
        project_dir: Directory to write ``STATE.md`` into.
        **status_overrides: ``step=status`` pairs to apply on top of a fresh
            manifest (e.g. ``brand="approved"``).
    """
    text = init.render_state_md(TARGET_VERSION)
    if status_overrides:
        text = brand.apply_status_updates(text, {k: v for k, v in status_overrides.items()})
    (project_dir / "STATE.md").write_text(text, encoding="utf-8")


def _write_branding_spec(project_dir: Path, body: str = "# Branding\nBlue brand.\n") -> None:
    """Create a production ``branding/branding.md`` in the project.

    Args:
        project_dir: The project directory.
        body: Spec contents (the brand step doesn't parse it here).
    """
    branding_dir = project_dir / "branding"
    branding_dir.mkdir(exist_ok=True)
    (branding_dir / "branding.md").write_text(body, encoding="utf-8")


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def test_precheck_blocks_when_no_state(tmp_path):
    """No STATE.md ⇒ brand cannot run; the blocker points at tableau-init."""
    result = brand.precheck(tmp_path)

    assert result.can_run is False
    assert "tableau-init" in result.blocker


def test_precheck_blocks_when_init_not_approved(tmp_path):
    """init must be approved before brand runs (§4.1)."""
    _write_state(tmp_path, init="pending")

    result = brand.precheck(tmp_path)

    assert result.can_run is False
    assert "init" in result.blocker and "pending" in result.blocker


def test_commit_refuses_when_init_not_approved(tmp_path):
    """The same gate guards commit, not just precheck."""
    _write_state(tmp_path, init="pending")
    (tmp_path / "DESIGN-TOKENS.md").write_text(FULL_TOKENS, encoding="utf-8")

    result = brand.commit(tmp_path, status="approved")

    assert result.ok is False
    assert "init" in result.message
    # STATE.md must be untouched: brand stays pending.
    assert route.parse_state(tmp_path / "STATE.md").statuses["brand"] == "pending"


# --- Precheck signals --------------------------------------------------------

def test_precheck_detects_existing_tokens(tmp_path):
    """precheck reports whether DESIGN-TOKENS.md exists (refine-vs-overwrite signal)."""
    _write_state(tmp_path)
    assert brand.precheck(tmp_path).tokens_exist is False

    (tmp_path / "DESIGN-TOKENS.md").write_text(FULL_TOKENS, encoding="utf-8")
    assert brand.precheck(tmp_path).tokens_exist is True


def test_source_mode_prefers_production_spec_over_scaffold(tmp_path):
    """branding/branding.md > scaffold/ demo example > none (§3.1)."""
    # A scaffolded project has only the scaffold/ demo example.
    init.scaffold_project(tmp_path, target_version=TARGET_VERSION)
    assert brand.precheck(tmp_path).source_mode == brand.SOURCE_SCAFFOLD

    # Adding a production branding.md flips the source to the spec.
    _write_branding_spec(tmp_path)
    assert brand.precheck(tmp_path).source_mode == brand.SOURCE_SPEC


def test_source_mode_none_when_nothing_present(tmp_path):
    """With neither production branding/ nor the scaffold example, the source is none."""
    _write_state(tmp_path)  # bare project: STATE.md only, no scaffold/ files

    assert brand.precheck(tmp_path).source_mode == brand.SOURCE_NONE


def test_source_mode_twb_only(tmp_path):
    """An org .twb with no branding.md ⇒ 'twb' mode (scrape it, write branding.md)."""
    _write_state(tmp_path)
    branding_dir = tmp_path / "branding"
    branding_dir.mkdir()
    (branding_dir / "template.twb").write_text("<workbook/>", encoding="utf-8")

    result = brand.precheck(tmp_path)

    assert result.source_mode == brand.SOURCE_TWB
    assert result.twb_files == ("branding/template.twb",)


def test_source_mode_spec_plus_twb(tmp_path):
    """branding.md + an org .twb ⇒ 'spec+twb' mode (enrich the spec from the .twb)."""
    _write_state(tmp_path)
    _write_branding_spec(tmp_path)
    (tmp_path / "branding" / "org.twb").write_text("<workbook/>", encoding="utf-8")

    result = brand.precheck(tmp_path)

    assert result.source_mode == brand.SOURCE_SPEC_AND_TWB
    assert result.twb_files == ("branding/org.twb",)


def test_precheck_reports_logo_and_icon_assets(tmp_path):
    """Logo files and a populated icons/ dir are surfaced for the model to integrate."""
    _write_state(tmp_path)
    _write_branding_spec(tmp_path)
    (tmp_path / "branding" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    icons_dir = tmp_path / "branding" / "icons"
    icons_dir.mkdir()
    (icons_dir / "bar-chart.svg").write_text("<svg/>", encoding="utf-8")

    assets = brand.precheck(tmp_path).assets

    assert "branding/logo.svg" in assets
    assert "branding/icons/" in assets


# --- DESIGN-TOKENS schema ----------------------------------------------------

def test_validate_tokens_required_core():
    """A token file missing the required core is invalid and names what's missing."""
    ok, missing_required, _ = brand.validate_design_tokens(INCOMPLETE_TOKENS)

    assert ok is False
    assert set(missing_required) == {
        "Chart series colors", "Typography", "Spacing", "Fallback Decisions",
    }


def test_validate_tokens_rejects_a_renamed_chart_series_heading():
    """``tableau-build`` reads ``### Chart series colors`` by name to build every worksheet's
    palette, so a file that renames it must fail here rather than silently un-style the
    workbook (CONTRACT.md §1)."""
    renamed = FULL_TOKENS.replace("### Chart series colors", "### Series palette")

    ok, missing_required, _ = brand.validate_design_tokens(renamed)

    assert ok is False and missing_required == ["Chart series colors"]


def test_validate_tokens_recommended_are_optional():
    """Core-only token file is valid; absent recommended sections are merely reported."""
    ok, missing_required, missing_recommended = brand.validate_design_tokens(CORE_ONLY_TOKENS)

    assert ok is True
    assert missing_required == []
    # Source/Dashboard Sizing/Logo/Icons absent — reported, but not a failure.
    assert set(missing_recommended) == set(brand.DESIGN_TOKENS_RECOMMENDED_SECTIONS)


def test_validate_tokens_allows_custom_sections_when_refining():
    """A refined token file that keeps the core plus custom sections still validates."""
    refined = CORE_ONLY_TOKENS + "\n## Constraints\nNo rounded corners.\n"

    ok, missing_required, _ = brand.validate_design_tokens(refined)

    assert ok is True and missing_required == []


def test_font_family_prose_value_is_named():
    """Issue #66: an annotated font value reaches ``tableau-build`` as a machine identifier
    and Desktop cannot resolve it. Catch it where it is written, with a fix-it."""
    problem = brand.font_family_problem(
        "## Typography\n\n- **Font family**: Tableau (Medium / Light — native)\n"
    )

    assert problem is not None
    assert "Font family" in problem


def test_a_plain_font_family_has_no_problem():
    """The normal case, the unfilled template placeholder, and a file with no typography
    bullet at all must all pass."""
    assert brand.font_family_problem("- **Font family**: Segoe UI\n") is None
    assert brand.font_family_problem("- **Font family**: [font]\n") is None
    assert brand.font_family_problem("no typography section here") is None


def test_shipped_reference_font_family_is_machine_usable():
    """The bundled fallback reference is what the model copies from - issue #66's bad value
    came straight out of it."""
    assert brand.font_family_problem(brand.render_fallback_reference()) is None


def test_shipped_template_is_schema_complete():
    """The bundled DESIGN-TOKENS-TEMPLATE.md contains the required core."""
    ok, missing_required, _ = brand.validate_design_tokens(brand.render_design_tokens_template())

    assert ok is True and missing_required == []


def test_fallback_reference_is_readable():
    """The owned fallback-defaults reference ships and is non-empty."""
    text = brand.render_fallback_reference()

    assert "Fallback Design Tokens" in text


# --- Commit: approve, skip, refuse -------------------------------------------

def test_commit_approved_with_complete_tokens(tmp_path):
    """A schema-complete token file commits: brand → approved in STATE.md."""
    _write_state(tmp_path)
    (tmp_path / "DESIGN-TOKENS.md").write_text(FULL_TOKENS, encoding="utf-8")

    result = brand.commit(tmp_path, status="approved")

    assert result.ok is True
    assert route.parse_state(tmp_path / "STATE.md").statuses["brand"] == "approved"


def test_commit_approved_refuses_incomplete_tokens(tmp_path):
    """commit refuses to approve tokens missing the required core; STATE is untouched."""
    _write_state(tmp_path)
    (tmp_path / "DESIGN-TOKENS.md").write_text(INCOMPLETE_TOKENS, encoding="utf-8")

    result = brand.commit(tmp_path, status="approved")

    assert result.ok is False
    assert "Typography" in result.message and "Fallback Decisions" in result.message
    assert route.parse_state(tmp_path / "STATE.md").statuses["brand"] == "pending"


def test_commit_approved_refuses_when_tokens_absent(tmp_path):
    """Approving without a DESIGN-TOKENS.md on disk is refused (nothing to approve)."""
    _write_state(tmp_path)

    result = brand.commit(tmp_path, status="approved")

    assert result.ok is False
    assert "DESIGN-TOKENS.md" in result.message


def test_commit_core_only_tokens_succeeds_and_notes_recommended(tmp_path):
    """A core-only token file commits; missing recommended is just a note."""
    _write_state(tmp_path)
    (tmp_path / "DESIGN-TOKENS.md").write_text(CORE_ONLY_TOKENS, encoding="utf-8")

    result = brand.commit(tmp_path, status="approved")

    assert result.ok is True
    assert set(result.missing_recommended) == set(brand.DESIGN_TOKENS_RECOMMENDED_SECTIONS)


def test_commit_rejects_unknown_status(tmp_path):
    """An out-of-vocabulary status is a programming error, not a silent no-op."""
    _write_state(tmp_path)

    with pytest.raises(ValueError, match="brand status"):
        brand.commit(tmp_path, status="approve")  # typo: not in COMMIT_STATUSES


# --- Skip precondition (brand-specific) --------------------------------------

def test_skip_refused_without_branding_spec(tmp_path):
    """Skip is refused when branding/branding.md does not exist (too important)."""
    _write_state(tmp_path)

    result = brand.commit(tmp_path, status="skipped")

    assert result.ok is False
    assert "branding/branding.md" in result.message
    # STATE.md untouched: brand stays pending, not skipped.
    assert route.parse_state(tmp_path / "STATE.md").statuses["brand"] == "pending"


def test_precheck_can_skip_tracks_branding_spec(tmp_path):
    """precheck.can_skip is False without branding.md, True once it exists."""
    _write_state(tmp_path)
    assert brand.precheck(tmp_path).can_skip is False

    _write_branding_spec(tmp_path)
    assert brand.precheck(tmp_path).can_skip is True


def test_commit_skipped_succeeds_with_branding_spec_and_does_not_block(tmp_path):
    """With branding.md present, skipping records 'skipped' and the pipeline continues."""
    _write_state(tmp_path, intake="approved", data="approved")
    _write_branding_spec(tmp_path)
    # plan's ordering gate needs DATA-MODEL.md on disk (data is the producer).
    (tmp_path / "DATA-MODEL.md").write_text("# Data Model\n", encoding="utf-8")

    result = brand.commit(tmp_path, status="skipped")

    assert result.ok is True
    statuses = route.parse_state(tmp_path / "STATE.md").statuses
    assert statuses["brand"] == "skipped"
    # 'skipped' is resolved; the router advances past brand to plan.
    assert route.compute_next_step(tmp_path).next_step == "plan"


# --- Staleness propagation (CONTRACT.md §4.2) --------------------------------

def test_rerun_marks_downstream_approved_steps_stale(tmp_path):
    """Re-running brand flips downstream approved steps to stale; upstream untouched."""
    # Mid-pipeline: upstream (intake, data) resolved; brand+plan+mock approved.
    _write_state(
        tmp_path, intake="approved", data="approved",
        brand="approved", plan="approved", mock="approved",
    )
    (tmp_path / "DESIGN-TOKENS.md").write_text(FULL_TOKENS, encoding="utf-8")
    # plan's ordering gate needs DATA-MODEL.md on disk for the router cross-check.
    (tmp_path / "DATA-MODEL.md").write_text("# Data Model\n", encoding="utf-8")

    result = brand.commit(tmp_path, status="approved")

    assert result.ok is True
    # plan and mock (downstream, approved) flip to stale, in canonical order.
    assert result.staled_steps == ["plan", "mock"]

    statuses = route.parse_state(tmp_path / "STATE.md").statuses
    assert statuses["plan"] == "stale" and statuses["mock"] == "stale"
    assert statuses["data"] == "approved"   # upstream — left as-is
    assert statuses["brand"] == "approved"  # its own step re-approved

    # Cross-check through the router: the first unresolved step is now stale 'plan'.
    routed = route.compute_next_step(tmp_path)
    assert routed.next_step == "plan" and "stale" in routed.reason.lower()


def test_first_run_marks_nothing_stale(tmp_path):
    """On a first approval there is nothing downstream approved, so nothing goes stale."""
    _write_state(tmp_path)
    (tmp_path / "DESIGN-TOKENS.md").write_text(FULL_TOKENS, encoding="utf-8")

    result = brand.commit(tmp_path, status="approved")

    assert result.ok is True and result.staled_steps == []
