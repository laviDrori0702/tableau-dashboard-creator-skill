"""Gate, validate, and commit the ``brand`` step of the tableau-dashboard-plugin.

This is the executable core of the ``tableau-brand`` skill (CONTRACT.md step 4):
the skippable step that extracts design tokens (palette, type, spacing) from the
analyst's ``branding/`` into a ``DESIGN-TOKENS.md`` so the mock and the workbook
match brand. The token *content* is authored by the model (reading ``branding.md``,
scraping an org ``*.twb``, or running a short brand interview); this script owns the
three things that must be **mechanically guaranteed** rather than left to model prose:

1. **Entry gate** - brand refuses to run until ``init`` is ``approved`` in
   ``STATE.md`` (and ``STATE.md`` exists at all). This mirrors the ordering rule in
   CONTRACT.md §4.1. Brand has **no producer-gated required reads** (its input is the
   analyst-owned ``branding/`` directory, CONTRACT.md §1/§3.1), so init being
   approved - i.e. the project is initialized - is the only precondition.
2. **DESIGN-TOKENS schema** - on approval the produced ``DESIGN-TOKENS.md`` must
   contain the required core (``Colors``, ``Typography``, ``Spacing``) plus a
   ``Fallback Decisions`` section. The first three are the palette/type/spacing the
   step exists to capture; the fourth is the mechanical anchor for the rule that
   *every value filled from a fallback default is explicitly flagged as guessed* -
   the section must be present (even if it records "none").
3. **Skip precondition + STATE.md transition** - the step is *skippable*, but only
   once ``branding/branding.md`` exists. Branding drives how good the mock and the
   Tableau spec can be, so the analyst may not skip into neutral styling with no
   brand intent captured at all: ``--status skipped`` is **refused** unless
   ``branding/branding.md`` is on disk. Committing either status flips every
   downstream ``approved`` step to ``stale`` (CONTRACT.md §4.2) so the pipeline can
   never silently disagree with a changed brand.

The module is intentionally pure and stdlib-only (it does **not** import the router)
so the contract test can call its functions directly, exactly like ``intake.py`` /
``init.py`` / ``route.py``. The CLI exposes two subcommands the skill runs at two
moments - ``precheck`` (before authoring) and ``commit`` (after approval). What the
CLI prints to stdout is the program's *output*; diagnostics go through ``logging``.

Keep ``STEP_ORDER`` below in lock-step with the ordered step list in CONTRACT.md §1.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Canonical constants (mirror of CONTRACT.md §1 / §2 / §3) ----------------

STATE_FILENAME = "STATE.md"
DESIGN_TOKENS_FILENAME = "DESIGN-TOKENS.md"

#: The analyst-owned branding input directory (CONTRACT.md §3 lowercase = input).
BRANDING_DIR = "branding"
#: The brand spec inside it (preferred input) and its scaffold/ demo fallback (§3.1).
BRANDING_SPEC = "branding/branding.md"
SCAFFOLD_BRANDING_SPEC = "scaffold/branding/EXAMPLE-branding.md"

#: Logo / icon assets that may live alongside the spec and get integrated.
LOGO_STEMS = ("logo",)
LOGO_SUFFIXES = (".svg", ".png", ".jpg", ".jpeg")
ICONS_SUBDIR = "branding/icons"

#: This step, and the upstream step whose approval gates it (CONTRACT.md §4.1).
BRAND_STEP = "brand"
INIT_STEP = "init"

#: The 8 step names in canonical order (mirror of CONTRACT.md §1). Used to decide
#: which steps are "downstream of brand" for staleness propagation (§4.2).
STEP_ORDER: tuple[str, ...] = (
    "init", "intake", "data", "brand", "plan", "mock", "spec", "build",
)

#: Statuses the commit subcommand may write for the brand step.
COMMIT_STATUSES: tuple[str, ...] = ("approved", "skipped")

#: DESIGN-TOKENS sections every approved token file must have: the palette, the
#: type, the spacing the step exists to capture, plus the fallback-disclosure
#: section that flags every guessed value (CONTRACT acceptance criteria).
#: ``Chart series colors`` is required by name because ``tableau-build`` reads that
#: heading to build every worksheet's colour palette (CONTRACT.md §8) - a file that
#: renames it still validates as "has Colors" and silently loses the brand's palette.
DESIGN_TOKENS_REQUIRED_SECTIONS: tuple[str, ...] = (
    "Colors", "Chart series colors", "Typography", "Spacing", "Fallback Decisions",
)

#: Common but genuinely optional sections. Proposed while authoring, never required
#: - not every brand ships a logo or custom icons or a fixed canvas size.
DESIGN_TOKENS_RECOMMENDED_SECTIONS: tuple[str, ...] = (
    "Source", "Dashboard Sizing", "Logo", "Icons",
)

#: The branding-source modes precheck reports, to drive the skill's branch.
SOURCE_SPEC_AND_TWB = "spec+twb"   # branding.md AND an org .twb (enrich the spec)
SOURCE_SPEC = "spec"               # branding.md only (extract from it)
SOURCE_TWB = "twb"                 # an org .twb only (scrape it -> write branding.md)
SOURCE_SCAFFOLD = "scaffold"       # only the scaffold/ demo example exists
SOURCE_NONE = "none"               # nothing -> run the brand interview (<=10 Qs)


# --- STATE.md reading --------------------------------------------------------

# Matches a markdown ATX heading line, capturing its text (drops leading "#"s and
# any trailing "#"s): "## Colors" -> "Colors".
_HEADING_LINE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")

#: The ``- **Font family**:`` bullet ``tableau-build`` reads to style every text run.
_FONT_FAMILY_BULLET = re.compile(r"^-\s*\**\s*font family\s*\**\s*:\s*(.+)$", re.IGNORECASE)

#: What a font family Windows can resolve looks like - letters, digits, and the punctuation
#: real family names use. A parenthetical, an em-dash or a slash means the value is prose.
_FONT_FAMILY = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .'&+-]*")

#: The Tableau families that actually ship with Desktop (mirror of
#: ``tableau-build``'s ``worksheet.TABLEAU_FONTS``; the two skills are self-contained,
#: CONTRACT.md §7). There is no font called plain "Tableau" - the weight is part of the
#: family name - so a value like "Tableau" or "Tableau Sans" resolves to nothing and
#: Desktop falls back silently on every run.
TABLEAU_FONTS: tuple[str, ...] = (
    "Tableau Bold", "Tableau Book", "Tableau Light",
    "Tableau Medium", "Tableau Regular", "Tableau Semibold",
)


def parse_statuses(text: str) -> dict[str, str]:
    """Parse the per-step statuses out of a STATE.md manifest.

    Parsing is tolerant (matching the router's parser): only genuine
    ``| order | step | skill | status |`` rows whose step name is known
    contribute; header, separator, and stray rows are ignored.

    Args:
        text: The full contents of a ``STATE.md`` file.

    Returns:
        A ``{step_name: status}`` mapping (both lower-cased).
    """
    statuses: dict[str, str] = {}
    in_steps = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Section headers toggle which parser applies.
        if line.lower().startswith("## steps"):
            in_steps = True
            continue
        if line.startswith("## "):  # any other section ends the Steps table
            in_steps = False

        if in_steps and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) < 4:
                continue
            step_name, status = cells[1].lower(), cells[3].lower()
            if step_name in STEP_ORDER:
                statuses[step_name] = status
    return statuses


# --- DESIGN-TOKENS schema ----------------------------------------------------

def _markdown_headings(text: str) -> list[str]:
    """Extract the text of every markdown heading in a document.

    Args:
        text: Markdown document contents.

    Returns:
        The heading texts, in document order (e.g. ``["Colors", "Typography"]``).
    """
    return [
        match.group(1).strip()
        for raw_line in text.splitlines()
        if (match := _HEADING_LINE.match(raw_line.strip()))
    ]


def validate_design_tokens(text: str) -> tuple[bool, list[str], list[str]]:
    """Check a DESIGN-TOKENS file's section coverage against the canonical schema.

    A token file is *valid* when it contains every **required** section; recommended
    sections are reported when absent but never make it invalid (not every brand has
    a logo, custom icons, or a fixed canvas). Section names are matched
    case-insensitively as substrings of the document's headings, so a heading like
    ``## Spacing Reference`` satisfies ``Spacing`` and extra custom sections are
    always allowed.

    Args:
        text: The contents of a ``DESIGN-TOKENS.md`` file.

    Returns:
        A ``(ok, missing_required, missing_recommended)`` tuple. ``ok`` is True iff
        ``missing_required`` is empty.
    """
    headings_blob = "\n".join(_markdown_headings(text)).lower()
    missing_required = [
        section for section in DESIGN_TOKENS_REQUIRED_SECTIONS
        if section.lower() not in headings_blob
    ]
    missing_recommended = [
        section for section in DESIGN_TOKENS_RECOMMENDED_SECTIONS
        if section.lower() not in headings_blob
    ]
    return (not missing_required, missing_required, missing_recommended)


def font_family_problem(text: str) -> Optional[str]:
    """Report a ``Font family`` token value that is prose rather than a font name.

    ``tableau-build`` puts this value straight into every text run's ``fontname=`` and into
    the worksheet's ``font-family`` format, so an annotated value like ``Tableau (Medium /
    Light - native, no webfont)`` names no font Windows can resolve and Desktop silently
    falls back on every title, label and tooltip (issue #66). A bare ``Tableau`` fails the
    same way: the weight is part of the family name, so only the six
    :data:`TABLEAU_FONTS` exist. Build sanitizes what it can; this catches it where it is
    authored, while the analyst can still say which family they meant. An unfilled
    ``[font]`` placeholder and a file with no such bullet are both fine - other checks own
    those.

    Args:
        text: The contents of a ``DESIGN-TOKENS.md`` file.

    Returns:
        A fix-it message, or None when every ``Font family`` bullet names a font.
    """
    for raw_line in text.splitlines():
        match = _FONT_FAMILY_BULLET.match(raw_line.strip())
        if not match:
            continue
        value = match.group(1).strip().strip("`*")
        if not value or value.startswith("["):  # an unfilled template placeholder
            continue
        tableau_font = value.lower().startswith("tableau")
        if not _FONT_FAMILY.fullmatch(value) or (tableau_font and value not in TABLEAU_FONTS):
            return (
                f"'Font family' is {value!r}, which is not a font family Desktop can "
                f"resolve - it becomes every text run's fontname= verbatim. Name one "
                f"family, weight included ({' | '.join(TABLEAU_FONTS)}), and put "
                f"availability notes in prose."
            )
    return None


def render_design_tokens_template() -> str:
    """Return the canonical DESIGN-TOKENS template text this skill ships.

    Reads ``references/DESIGN-TOKENS-TEMPLATE.md`` so there is a single source of
    truth for the schema-complete starting token file - the same file the skill
    hands the model to fill in. It is, by construction, schema-complete (contains
    the required core).

    Returns:
        The template file contents.

    Raises:
        FileNotFoundError: If the bundled template is missing.
    """
    # This script lives in ``<skill>/scripts/``; references/ sits at the skill root.
    template_path = (
        Path(__file__).resolve().parent.parent / "references" / "DESIGN-TOKENS-TEMPLATE.md"
    )
    if not template_path.is_file():
        raise FileNotFoundError(f"DESIGN-TOKENS template not found: {template_path}")
    return template_path.read_text(encoding="utf-8-sig")


def render_fallback_reference() -> str:
    """Return the owned fallback-defaults reference this skill ships.

    Reads ``references/tableau-design-tokens.md`` - the Tableau-default palette,
    type, spacing, sizing, and constraints the model pulls from when a brand value
    is unspecified. Every value taken from here MUST be disclosed under the token
    file's ``## Fallback Decisions`` section.

    Returns:
        The reference file contents.

    Raises:
        FileNotFoundError: If the bundled reference is missing.
    """
    reference_path = (
        Path(__file__).resolve().parent.parent / "references" / "tableau-design-tokens.md"
    )
    if not reference_path.is_file():
        raise FileNotFoundError(f"Fallback token reference not found: {reference_path}")
    return reference_path.read_text(encoding="utf-8-sig")


# --- STATE.md rewriting ------------------------------------------------------

def _format_step_row(cells: list[str]) -> str:
    """Render a Steps-table row with the canonical column widths.

    Matches the alignment ``init.render_state_md`` produces (``order<5 | step<6 |
    skill<14 | status<8``) so a rewritten row stays visually consistent with the
    rest of the table.

    Args:
        cells: The four cell values ``[order, step, skill, status]``.

    Returns:
        The formatted ``| ... |`` table row (no trailing newline).
    """
    order, step, skill, status = cells[0], cells[1], cells[2], cells[3]
    return f"| {order:<5} | {step:<6} | {skill:<14} | {status:<8} |"


def apply_status_updates(text: str, updates: dict[str, str]) -> str:
    """Rewrite the status cell of one or more Steps-table rows.

    Only rows inside the ``## Steps`` table whose step name is a key in ``updates``
    are touched; every other line (metadata, prose, untouched rows) is preserved
    byte-for-byte. The trailing newline of the input is preserved.

    Args:
        text: The full ``STATE.md`` contents.
        updates: ``{step_name: new_status}`` for the rows to rewrite (step names
            lower-cased).

    Returns:
        The updated ``STATE.md`` contents.
    """
    out_lines: list[str] = []
    in_steps = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()

        if stripped.lower().startswith("## steps"):
            in_steps = True
            out_lines.append(raw_line)
            continue
        if stripped.startswith("## "):  # any other section ends the Steps table
            in_steps = False

        if in_steps and stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 4 and cells[1].lower() in updates:
                cells[3] = updates[cells[1].lower()]
                out_lines.append(_format_step_row(cells))
                continue

        out_lines.append(raw_line)

    result = "\n".join(out_lines)
    if text.endswith("\n"):
        result += "\n"
    return result


def _downstream_stale_updates(statuses: dict[str, str]) -> dict[str, str]:
    """Compute which downstream steps must flip to ``stale`` (CONTRACT.md §4.2).

    Every step ordered after ``brand`` that is currently ``approved`` becomes
    ``stale``; steps already ``pending`` / ``skipped`` / ``stale`` are left as-is.

    Args:
        statuses: The current ``{step_name: status}`` mapping.

    Returns:
        ``{step_name: "stale"}`` for each downstream step that was ``approved``.
    """
    brand_index = STEP_ORDER.index(BRAND_STEP)
    return {
        step: "stale"
        for step in STEP_ORDER[brand_index + 1:]
        if statuses.get(step) == "approved"
    }


# --- Entry gate (CONTRACT.md §4.1) -------------------------------------------

def entry_gate_blocker(project_root: Path) -> Optional[str]:
    """Return why brand may not run yet, or ``None`` if it may.

    Brand refuses to run unless ``STATE.md`` exists and ``init`` is ``approved``.
    (Brand has no producer-gated required reads, so this is its only gate.)

    Args:
        project_root: The analyst's project directory.

    Returns:
        A human-readable blocker message, or ``None`` when the gate is open.
    """
    state_path = project_root / STATE_FILENAME
    if not state_path.exists():
        return (
            "No STATE.md found. Run 'tableau-init' first to scaffold the project "
            "and initialize STATE.md before running 'tableau-brand'."
        )

    init_status = parse_statuses(state_path.read_text(encoding="utf-8-sig")).get(
        INIT_STEP, "pending"
    )
    if init_status != "approved":
        return (
            f"Step 'init' is '{init_status}', not 'approved'. Run 'tableau-init' "
            f"first; 'tableau-brand' cannot run until init is approved."
        )
    return None


# --- Branding-source detection -----------------------------------------------

def _find_twb_files(project_root: Path) -> tuple[str, ...]:
    """List org template workbooks (``*.twb``) under the production ``branding/``.

    Args:
        project_root: The analyst's project directory.

    Returns:
        Project-relative paths of any ``branding/*.twb`` files, sorted for stable
        output (empty when none).
    """
    branding_path = project_root / BRANDING_DIR
    if not branding_path.is_dir():
        return ()
    return tuple(
        sorted(f"{BRANDING_DIR}/{twb.name}" for twb in branding_path.glob("*.twb"))
    )


def _find_brand_assets(project_root: Path) -> tuple[str, ...]:
    """List logo / icon assets under the production ``branding/`` to integrate.

    Args:
        project_root: The analyst's project directory.

    Returns:
        Project-relative paths of any logo files (``branding/logo.*``) and the
        ``branding/icons/`` directory when it holds at least one file. Sorted for
        stable output (empty when none).
    """
    branding_path = project_root / BRANDING_DIR
    if not branding_path.is_dir():
        return ()

    assets: list[str] = []
    for asset in branding_path.iterdir():
        if (
            asset.is_file()
            and asset.stem.lower() in LOGO_STEMS
            and asset.suffix.lower() in LOGO_SUFFIXES
        ):
            assets.append(f"{BRANDING_DIR}/{asset.name}")

    icons_path = project_root / ICONS_SUBDIR
    if icons_path.is_dir() and any(icons_path.iterdir()):
        assets.append(f"{ICONS_SUBDIR}/")

    return tuple(sorted(assets))


def _resolve_source_mode(
    has_spec: bool, twb_files: tuple[str, ...], has_scaffold: bool
) -> str:
    """Pick which branding-source branch the skill should take (§3.1).

    Production ``branding/`` always wins over the ``scaffold/`` demo example; within
    production, an org ``.twb`` enriches an existing spec rather than replacing it.

    Args:
        has_spec: Whether production ``branding/branding.md`` exists.
        twb_files: Production ``branding/*.twb`` files found.
        has_scaffold: Whether the ``scaffold/`` demo example exists.

    Returns:
        One of :data:`SOURCE_SPEC_AND_TWB`, :data:`SOURCE_SPEC`, :data:`SOURCE_TWB`,
        :data:`SOURCE_SCAFFOLD`, :data:`SOURCE_NONE`.
    """
    if has_spec and twb_files:
        return SOURCE_SPEC_AND_TWB
    if has_spec:
        return SOURCE_SPEC
    if twb_files:
        return SOURCE_TWB
    if has_scaffold:
        return SOURCE_SCAFFOLD
    return SOURCE_NONE


# --- precheck ----------------------------------------------------------------

@dataclass(frozen=True)
class PrecheckResult:
    """The state brand needs to know before authoring design tokens.

    Attributes:
        can_run: True when the entry gate is open (init approved, STATE.md present).
        blocker: Why brand cannot run, when ``can_run`` is False; else ``None``.
        tokens_exist: Whether a ``DESIGN-TOKENS.md`` already exists at the project
            root. When True the skill must offer refine-vs-overwrite, never silently
            overwrite.
        source_mode: Which branding source to use - one of the ``SOURCE_*`` modes.
        has_branding_spec: Whether production ``branding/branding.md`` exists. This
            is also the **skip precondition**: skipping is only allowed when True.
        twb_files: Production ``branding/*.twb`` org templates found (to scrape).
        assets: Logo / icon assets under ``branding/`` to integrate.
        brand_status: The brand step's current status (to detect a re-run).
    """

    can_run: bool
    blocker: Optional[str]
    tokens_exist: bool
    source_mode: str
    has_branding_spec: bool
    twb_files: tuple[str, ...]
    assets: tuple[str, ...]
    brand_status: str

    @property
    def can_skip(self) -> bool:
        """bool: Whether the analyst may skip this step (branding.md must exist)."""
        return self.has_branding_spec


def precheck(project_dir: Path | str) -> PrecheckResult:
    """Report whether brand may run, what it should read, and whether it may skip.

    Args:
        project_dir: The analyst's project directory.

    Returns:
        A :class:`PrecheckResult`. When the entry gate is closed, ``can_run`` is
        False and ``blocker`` explains why; the other fields are placeholders.
    """
    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return PrecheckResult(
            can_run=False,
            blocker=blocker,
            tokens_exist=False,
            source_mode=SOURCE_NONE,
            has_branding_spec=False,
            twb_files=(),
            assets=(),
            brand_status="unknown",
        )

    has_spec = (project_root / BRANDING_SPEC).exists()
    twb_files = _find_twb_files(project_root)
    has_scaffold = (project_root / SCAFFOLD_BRANDING_SPEC).exists()
    statuses = parse_statuses((project_root / STATE_FILENAME).read_text(encoding="utf-8-sig"))

    return PrecheckResult(
        can_run=True,
        blocker=None,
        tokens_exist=(project_root / DESIGN_TOKENS_FILENAME).exists(),
        source_mode=_resolve_source_mode(has_spec, twb_files, has_scaffold),
        has_branding_spec=has_spec,
        twb_files=twb_files,
        assets=_find_brand_assets(project_root),
        brand_status=statuses.get(BRAND_STEP, "pending"),
    )


# --- commit ------------------------------------------------------------------

@dataclass
class CommitResult:
    """Outcome of committing the brand step's result to STATE.md.

    Attributes:
        ok: True when STATE.md was updated; False when the commit was refused.
        message: Human-readable explanation (the refusal reason when not ``ok``).
        status_set: The status written for ``brand`` (``approved``/``skipped``), or
            ``None`` when refused.
        staled_steps: Downstream steps flipped to ``stale`` by this commit.
        missing_required: Required token sections that were absent (only populated
            on an approval refusal).
        missing_recommended: Recommended token sections that were absent (reported
            as an informational note; never blocks an approval).
    """

    ok: bool
    message: str
    status_set: Optional[str] = None
    staled_steps: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_recommended: list[str] = field(default_factory=list)


def commit(project_dir: Path | str, status: str) -> CommitResult:
    """Record the brand step's result in STATE.md and propagate staleness.

    On ``approved`` the project's ``DESIGN-TOKENS.md`` must exist and contain the
    required core sections, or the commit is refused (so the model fixes it and
    re-commits). On ``skipped`` the **skip precondition** applies: branding is too
    important to skip blank, so ``branding/branding.md`` must exist first, else the
    commit is refused. On either accepted status, every downstream ``approved`` step
    is flipped to ``stale`` (CONTRACT.md §4.2) - a no-op on a first run, but the
    guard that keeps a re-run from silently disagreeing with the rest of the pipeline.

    Args:
        project_dir: The analyst's project directory.
        status: The status to record for ``brand`` - one of :data:`COMMIT_STATUSES`.

    Returns:
        A :class:`CommitResult`. ``ok`` is False (and STATE.md is left untouched)
        when the entry gate is closed, an approval's token file is incomplete, or a
        skip lacks ``branding/branding.md``.

    Raises:
        ValueError: If ``status`` is not an allowed commit status.
    """
    if status not in COMMIT_STATUSES:
        allowed = " | ".join(COMMIT_STATUSES)
        raise ValueError(f"Invalid brand status '{status}'. Allowed: {allowed}.")

    project_root = Path(project_dir)
    blocker = entry_gate_blocker(project_root)
    if blocker is not None:
        return CommitResult(False, blocker)

    missing_recommended: list[str] = []
    if status == "approved":
        tokens_path = project_root / DESIGN_TOKENS_FILENAME
        if not tokens_path.exists():
            return CommitResult(
                False,
                f"Cannot approve brand: '{DESIGN_TOKENS_FILENAME}' does not exist. "
                f"Author it first, or commit '--status skipped' to skip this step "
                f"(only possible once 'branding/branding.md' exists).",
            )
        tokens_text = tokens_path.read_text(encoding="utf-8-sig")
        ok, missing_required, missing_recommended = validate_design_tokens(tokens_text)
        if not ok:
            return CommitResult(
                False,
                f"'{DESIGN_TOKENS_FILENAME}' is missing required section(s): "
                f"{', '.join(missing_required)}. Add them and re-run commit.",
                missing_required=missing_required,
                missing_recommended=missing_recommended,
            )
        font_problem = font_family_problem(tokens_text)
        if font_problem is not None:
            return CommitResult(
                False,
                f"Cannot approve brand: {font_problem} Fix it and re-run commit.",
                missing_recommended=missing_recommended,
            )
    elif status == "skipped" and not (project_root / BRANDING_SPEC).exists():
        # Skip precondition: branding is too important to skip with no brand intent
        # on file. Force engagement (a spec, an org .twb, or the brand interview).
        return CommitResult(
            False,
            f"Cannot skip brand: '{BRANDING_SPEC}' does not exist. Branding drives "
            f"how well the mock and the Tableau spec turn out, so this step can't be "
            f"skipped blank. Provide '{BRANDING_SPEC}', drop an org '*.twb' in "
            f"'branding/', or answer the brand interview to generate it - then skip "
            f"if you still want neutral tokens.",
        )

    state_path = project_root / STATE_FILENAME
    text = state_path.read_text(encoding="utf-8-sig")
    statuses = parse_statuses(text)

    stale_updates = _downstream_stale_updates(statuses)
    updates = {BRAND_STEP: status, **stale_updates}
    state_path.write_text(apply_status_updates(text, updates), encoding="utf-8")

    staled_steps = sorted(stale_updates, key=STEP_ORDER.index)
    logger.info(f"Set brand -> {status}; marked stale: {staled_steps or 'none'}.")
    return CommitResult(
        ok=True,
        message=f"brand -> {status}",
        status_set=status,
        staled_steps=staled_steps,
        missing_recommended=missing_recommended,
    )


# --- CLI ---------------------------------------------------------------------

def format_precheck(result: PrecheckResult) -> str:
    """Render a :class:`PrecheckResult` as a human-readable block.

    Args:
        result: The precheck result to render.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    # Plain ASCII only: this prints to the console, which on Windows is cp1252
    # and would raise UnicodeEncodeError on emoji/box-drawing glyphs.
    if not result.can_run:
        return f"[BLOCKED] tableau-brand cannot run.\n{result.blocker}"

    lines = ["[BRAND] precheck OK - tableau-brand can run."]
    lines.append(f"  DESIGN-TOKENS.md exists : {'yes' if result.tokens_exist else 'no'}")
    if result.tokens_exist:
        lines.append(
            "    -> tokens already exist; offer REFINE vs OVERWRITE - never silently "
            "overwrite the analyst's work."
        )

    source_notes = {
        SOURCE_SPEC_AND_TWB: (
            f"branding/branding.md + org .twb ({', '.join(result.twb_files)}) - "
            "scrape the .twb to ENRICH the existing branding.md, then build tokens."
        ),
        SOURCE_SPEC: "branding/branding.md (production spec) - extract tokens from it.",
        SOURCE_TWB: (
            f"org .twb only ({', '.join(result.twb_files)}) - scrape it, WRITE "
            "branding/branding.md, then build tokens. Ask the analyst to confirm the "
            ".twb is as THIN as possible so context does not explode."
        ),
        SOURCE_SCAFFOLD: (
            f"{SCAFFOLD_BRANDING_SPEC} (DEMO fallback - say so; there is no production "
            "branding/branding.md)."
        ),
        SOURCE_NONE: (
            "none - run the brand interview (<=10 questions) and WRITE "
            "branding/branding.md from the answers, then build tokens."
        ),
    }
    lines.append(f"  source                  : {source_notes[result.source_mode]}")

    if result.assets:
        lines.append(f"  assets to integrate     : {', '.join(result.assets)}")

    skip_note = (
        "allowed (branding/branding.md exists)"
        if result.can_skip
        else "NOT allowed yet - branding/branding.md must exist first"
    )
    lines.append(f"  skip                    : {skip_note}")

    rerun_note = (
        " (re-run; downstream approved steps will be marked stale on commit)"
        if result.brand_status in ("approved", "stale")
        else ""
    )
    lines.append(f"  brand status            : {result.brand_status}{rerun_note}")
    return "\n".join(lines)


def format_commit(result: CommitResult) -> str:
    """Render a :class:`CommitResult` as a human-readable block.

    Args:
        result: The commit result to render.

    Returns:
        A multi-line, plain-ASCII string suitable for printing to the analyst.
    """
    # Plain ASCII only (see format_precheck).
    if not result.ok:
        return f"[REFUSED] {result.message}"

    lines = [f"[BRAND] {result.message}."]
    if result.missing_recommended:
        lines.append(
            f"  note: tokens have no {', '.join(result.missing_recommended)} "
            f"section(s) - optional, left as-is."
        )
    if result.staled_steps:
        lines.append(
            f"  downstream marked stale: {', '.join(result.staled_steps)} "
            f"(re-run these in order)."
        )
    lines.append(
        "  next: open a fresh conversation and run 'tableau-route' (or 'tableau-plan')."
    )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point: run ``precheck`` or ``commit`` and print the result.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``2`` when brand is blocked/refused or
        on a usage error.
    """
    parser = argparse.ArgumentParser(
        description="Gate, validate, and commit the tableau-brand step.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    precheck_parser = subparsers.add_parser(
        "precheck", help="Report whether brand may run, what to read, and skip-ability."
    )
    precheck_parser.add_argument(
        "project_dir", nargs="?", default=".", help="Project directory (default: cwd)."
    )

    commit_parser = subparsers.add_parser(
        "commit", help="Record brand's result in STATE.md and propagate staleness."
    )
    commit_parser.add_argument(
        "project_dir", nargs="?", default=".", help="Project directory (default: cwd)."
    )
    commit_parser.add_argument(
        "--status", required=True, choices=COMMIT_STATUSES,
        help="Status to record for the brand step.",
    )

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "precheck":
        precheck_result = precheck(args.project_dir)
        print(format_precheck(precheck_result))
        return 0 if precheck_result.can_run else 2

    commit_result = commit(args.project_dir, args.status)
    print(format_commit(commit_result))
    return 0 if commit_result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
