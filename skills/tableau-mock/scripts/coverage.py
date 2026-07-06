"""The coverage + slot-sizing core of the tableau-mock skill (CONTRACT.md step 6).

This is the testable heart of ``tableau-mock``, kept pure (stdlib-only, no STATE.md, no
filesystem) so the contract test can drive it directly. It owns two jobs:

1. **Coverage** - parse the strict ``DASHBOARD-PLAN.md`` into the set of things the demo
   must render (the screen size, every KPI/chart, every filter, every interaction), parse
   the produced ``mock.html`` for what it actually rendered, and build a **coverage
   checklist** matching the two. A plan element is "rendered" iff its plan ``id`` appears
   as a ``data-plan-id="<id>"`` attribute in the markup; any plan id with no match is a
   coverage **gap** that blocks approval. This is the guarantee that the mock can never
   silently drop a requirement.
2. **Slot-sizing guard** - read the embedded JSON layout manifest (each element's pixel
   box) and reject boxes that fall outside the canvas (**out-of-bounds**), are too small
   to read (**compressed**), or that collectively leave the canvas mostly empty
   (**empty-space-heavy**), so the demo looks professional.

``mock.py`` owns the STATE.md / versioning / entry-gate plumbing and the CLI; it imports
:func:`validate_mock` and :func:`parse_plan_coverage` from here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

#: The id-string the plan uses for a "no filters" / "no interactions" sentinel row
#: (DASHBOARD-PLAN-TEMPLATE.md). It is not a real element, so coverage skips it.
NONE_ID = "none"

#: The <script type="application/json" id="..."> block the mock embeds for the guard.
LAYOUT_MANIFEST_ID = "mock-layout"

# --- Slot-sizing guard thresholds --------------------------------------------
# ponytail: hand-tuned readability heuristics, not physics. A box narrower/shorter
# than these reads as unusably compressed; a canvas filled below MIN_FILL_RATIO reads
# as empty-space-heavy. The fill check sums box areas and so undercounts overlap - good
# enough to catch a sparse layout. Tune here if they misfire on real plans.
MIN_ELEMENT_WIDTH_PX = 80
MIN_ELEMENT_HEIGHT_PX = 56
MIN_FILL_RATIO = 0.45  # element boxes must cover >= 45% of the canvas area


# --- Plan parsing: the expected coverage -------------------------------------

# A markdown table separator cell: "---", ":---", "---:", ":---:".
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")

# The design canvas in the plan's Screen Size section: "1366 x 768", "1366x768px".
_DIMENSIONS = re.compile(r"(\d{2,5})\s*[x×]\s*(\d{2,5})", re.IGNORECASE)


def _markdown_tables(text: str) -> list[list[list[str]]]:
    """Split a markdown document into its pipe tables.

    Contiguous runs of lines beginning with ``|`` are grouped into one table; each
    table is a list of rows, each row a list of trimmed cell strings.

    Args:
        text: Markdown document contents.

    Returns:
        A list of tables (each a list of cell-rows).
    """
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("|"):
            current.append([cell.strip() for cell in line.strip("|").split("|")])
            continue
        if current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _is_separator_row(cells: list[str]) -> bool:
    """Return True if a table row is the ``|---|---|`` header/body separator."""
    non_empty = [cell for cell in cells if cell]
    return bool(non_empty) and all(_SEPARATOR_CELL.match(cell) for cell in non_empty)


def _screen_size_block(text: str) -> str:
    """Return the text between the ``## Screen Size`` heading and the next heading.

    Args:
        text: The contents of a ``DASHBOARD-PLAN.md`` file.

    Returns:
        The Screen Size section body, or the whole document if the heading is absent
        (so dimension parsing still gets a chance on a non-standard plan).
    """
    out: list[str] = []
    capturing = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## screen size"):
            capturing = True
            continue
        if capturing and stripped.startswith("## "):
            break
        if capturing:
            out.append(line)
    return "\n".join(out) if out else text


@dataclass(frozen=True)
class PlanCoverage:
    """The set of things a mock.html must render to fully cover its plan.

    Attributes:
        canvas: The planned design canvas ``(width, height)`` in px, or ``None`` if the
            Screen Size dimensions could not be parsed.
        element_ids: KPI/chart ids (the id-table that has a ``slot`` column). These also
            need a geometry box in the layout manifest.
        filter_ids: Filter ids (excludes the ``none`` sentinel).
        interaction_ids: Interaction ids (excludes the ``none`` sentinel).
    """

    canvas: Optional[tuple[int, int]]
    element_ids: list[str]
    filter_ids: list[str]
    interaction_ids: list[str]

    @property
    def all_ids(self) -> list[str]:
        """list[str]: Every plan id that must appear as a ``data-plan-id`` in the mock."""
        return [*self.element_ids, *self.filter_ids, *self.interaction_ids]


def parse_plan_coverage(text: str) -> PlanCoverage:
    """Extract the canvas size and every element/filter/interaction id from a plan.

    The plan's id-tables (first column header ``id``) are categorised by their other
    headers: a ``slot`` column marks the Elements table, an ``interaction`` column the
    Interactions table, everything else (a ``field``/``control`` table) the Filters
    table. The ``none`` sentinel row is dropped.

    Args:
        text: The contents of a ``DASHBOARD-PLAN.md`` file.

    Returns:
        A :class:`PlanCoverage`.
    """
    dims_match = _DIMENSIONS.search(_screen_size_block(text))
    canvas = (
        (int(dims_match.group(1)), int(dims_match.group(2))) if dims_match else None
    )

    element_ids: list[str] = []
    filter_ids: list[str] = []
    interaction_ids: list[str] = []

    for table in _markdown_tables(text):
        rows = [row for row in table if not _is_separator_row(row)]
        if len(rows) < 2:
            continue
        header = [cell.lower() for cell in rows[0]]
        if not header or header[0] != "id":
            continue

        ids = [
            row[0].strip()
            for row in rows[1:]
            if row and row[0].strip() and row[0].strip().lower() != NONE_ID
        ]
        if "slot" in header:
            element_ids.extend(ids)
        elif "interaction" in header:
            interaction_ids.extend(ids)
        else:
            filter_ids.extend(ids)

    return PlanCoverage(canvas, element_ids, filter_ids, interaction_ids)


# --- Mock parsing: what the HTML actually rendered ---------------------------

_DATA_PLAN_ID = re.compile(r"""data-plan-id\s*=\s*["']([^"']+)["']""")
_LAYOUT_SCRIPT = re.compile(
    r"""<script[^>]*\bid\s*=\s*["']"""
    + re.escape(LAYOUT_MANIFEST_ID)
    + r"""["'][^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)


def rendered_plan_ids(html: str) -> set[str]:
    """Return every ``data-plan-id`` value present in the mock HTML.

    A plan element is "rendered" iff its id appears as a ``data-plan-id`` attribute,
    so this set is what the coverage checklist matches the plan against.

    Args:
        html: The contents of a ``mock.html`` file.

    Returns:
        The set of rendered plan ids.
    """
    return {match.group(1).strip() for match in _DATA_PLAN_ID.finditer(html)}


def parse_layout_manifest(html: str) -> Optional[dict]:
    """Parse the embedded JSON layout manifest from the mock HTML.

    Args:
        html: The contents of a ``mock.html`` file.

    Returns:
        The parsed manifest dict, or ``None`` if the script block is absent or its body
        is not a valid JSON object.
    """
    match = _LAYOUT_SCRIPT.search(html)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


# --- Coverage checklist + slot-sizing guard ----------------------------------

@dataclass(frozen=True)
class CoverageItem:
    """One row of the coverage checklist.

    Attributes:
        kind: ``"screen size"``, ``"element"``, ``"filter"``, or ``"interaction"``.
        label: The id (or canvas size) being checked.
        rendered: Whether the mock renders it.
    """

    kind: str
    label: str
    rendered: bool


@dataclass(frozen=True)
class MockValidation:
    """Result of validating a ``mock.html`` against its plan.

    Attributes:
        ok: True iff every plan element is rendered and the slot-sizing guard passes.
        coverage: The coverage checklist (one item per plan element + screen size).
        guard_violations: Slot-sizing problems (out-of-bounds / compressed / empty).
        missing_boxes: Element ids with no geometry box in the layout manifest.
        notes: Non-fatal observations (e.g. plan had no parseable canvas).
    """

    ok: bool
    coverage: list[CoverageItem]
    guard_violations: list[str]
    missing_boxes: list[str]
    notes: list[str] = field(default_factory=list)

    @property
    def gaps(self) -> list[CoverageItem]:
        """list[CoverageItem]: Coverage rows the mock failed to render."""
        return [item for item in self.coverage if not item.rendered]


def slot_sizing_violations(
    manifest: Optional[dict], element_ids: list[str]
) -> tuple[list[str], list[str]]:
    """Run the slot-sizing guard against the layout manifest.

    Rejects element boxes that are out-of-bounds (outside the canvas), compressed
    (below the minimum readable width/height), or that collectively leave the canvas
    mostly empty (empty-space-heavy). A box is expected for every plan element id.

    Args:
        manifest: The parsed layout manifest, or ``None`` if absent/invalid.
        element_ids: The plan's element ids (each needs a box).

    Returns:
        ``(violations, missing_boxes)`` - guard problem strings, and element ids with
        no declared box.
    """
    if not element_ids:
        return [], []  # nothing visual to size (e.g. an all-filter plan)
    if manifest is None:
        return (
            [f"no '{LAYOUT_MANIFEST_ID}' JSON layout manifest found (or invalid JSON) "
             "- the slot-sizing guard cannot run"],
            list(element_ids),
        )

    canvas = manifest.get("canvas") or {}
    try:
        canvas_w = float(canvas.get("width"))
        canvas_h = float(canvas.get("height"))
    except (TypeError, ValueError):
        return ["layout manifest has no numeric canvas {width, height}"], list(element_ids)

    boxes = {
        str(box.get("id")): box
        for box in manifest.get("elements", [])
        if isinstance(box, dict) and box.get("id") is not None
    }
    violations: list[str] = []
    missing_boxes: list[str] = []
    covered_area = 0.0

    for element_id in element_ids:
        box = boxes.get(element_id)
        if box is None:
            missing_boxes.append(element_id)
            continue
        try:
            x, y = float(box["x"]), float(box["y"])
            width, height = float(box["width"]), float(box["height"])
        except (KeyError, TypeError, ValueError):
            violations.append(
                f"element '{element_id}' has a malformed box (need x/y/width/height)"
            )
            continue

        if x < 0 or y < 0 or x + width > canvas_w or y + height > canvas_h:
            violations.append(
                f"element '{element_id}' is out-of-bounds: box "
                f"({x:g},{y:g},{width:g}x{height:g}) escapes the {canvas_w:g}x{canvas_h:g} canvas"
            )
        if width < MIN_ELEMENT_WIDTH_PX or height < MIN_ELEMENT_HEIGHT_PX:
            violations.append(
                f"element '{element_id}' is compressed: {width:g}x{height:g}px is below the "
                f"{MIN_ELEMENT_WIDTH_PX}x{MIN_ELEMENT_HEIGHT_PX}px readable minimum"
            )
        covered_area += max(width, 0) * max(height, 0)

    canvas_area = canvas_w * canvas_h
    if canvas_area > 0 and covered_area / canvas_area < MIN_FILL_RATIO:
        violations.append(
            f"layout is empty-space-heavy: elements cover {covered_area / canvas_area:.0%} of "
            f"the canvas, below the {MIN_FILL_RATIO:.0%} minimum"
        )
    return violations, missing_boxes


def validate_mock(plan_text: str, html: str) -> MockValidation:
    """Build the coverage checklist and run the slot-sizing guard for a mock.

    Coverage: every plan element/filter/interaction id must appear as a
    ``data-plan-id`` in the HTML, and the planned canvas size must be declared in the
    layout manifest. Guard: every element box must be in-bounds, readable, and the
    layout must not be empty-space-heavy. The mock is valid iff there are no coverage
    gaps, no missing boxes, and no guard violations.

    Args:
        plan_text: The contents of ``DASHBOARD-PLAN.md``.
        html: The contents of the ``mock.html`` under validation.

    Returns:
        A :class:`MockValidation`.
    """
    spec = parse_plan_coverage(plan_text)
    rendered = rendered_plan_ids(html)
    manifest = parse_layout_manifest(html)
    notes: list[str] = []
    items: list[CoverageItem] = []

    # Screen size: the manifest's canvas must match the plan's design dimensions.
    if spec.canvas is None:
        notes.append(
            "plan has no parseable Screen Size dimensions - screen-size coverage skipped"
        )
    else:
        plan_w, plan_h = spec.canvas
        canvas = (manifest or {}).get("canvas") or {}
        rendered_size = (canvas.get("width"), canvas.get("height"))
        items.append(CoverageItem(
            "screen size", f"{plan_w}x{plan_h}px", rendered_size == (plan_w, plan_h),
        ))

    for element_id in spec.element_ids:
        items.append(CoverageItem("element", element_id, element_id in rendered))
    for filter_id in spec.filter_ids:
        items.append(CoverageItem("filter", filter_id, filter_id in rendered))
    for interaction_id in spec.interaction_ids:
        items.append(CoverageItem("interaction", interaction_id, interaction_id in rendered))

    guard_violations, missing_boxes = slot_sizing_violations(manifest, spec.element_ids)

    has_gap = any(not item.rendered for item in items)
    ok = not has_gap and not guard_violations and not missing_boxes
    return MockValidation(ok, items, guard_violations, missing_boxes, notes)
