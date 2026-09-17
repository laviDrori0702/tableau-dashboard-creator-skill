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

#: Name for the single default view — a blank / absent ``view`` cell (issue #97/#99).
DEFAULT_VIEW = "default"

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
        views: Declared workbook tabs (``DEFAULT_VIEW`` first when used). A plan with no
            ``view`` column is ``[DEFAULT_VIEW]``.
        id_views: ``{plan_id: view_name}`` for every element/filter/interaction id.
    """

    canvas: Optional[tuple[int, int]]
    element_ids: list[str]
    filter_ids: list[str]
    interaction_ids: list[str]
    views: list[str] = field(default_factory=lambda: [DEFAULT_VIEW])
    id_views: dict[str, str] = field(default_factory=dict)

    @property
    def all_ids(self) -> list[str]:
        """list[str]: Every plan id that must appear as a ``data-plan-id`` in the mock."""
        return [*self.element_ids, *self.filter_ids, *self.interaction_ids]

    @property
    def is_multi_view(self) -> bool:
        """True when the plan declares more than one workbook tab."""
        return len(self.views) > 1


def _header_index(header: list[str], name: str) -> Optional[int]:
    """Return the index of ``name`` in a lower-cased header row, or ``None``."""
    try:
        return header.index(name)
    except ValueError:
        return None


def _cell(row: list[str], index: Optional[int]) -> str:
    """Return a trimmed cell, or ``""`` when the column is absent / short row."""
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def parse_plan_coverage(text: str) -> PlanCoverage:
    """Extract the canvas size and every element/filter/interaction id from a plan.

    The plan's id-tables (first column header ``id``) are categorised by their other
    headers: a ``slot`` column marks the Elements table, an ``interaction`` column the
    Interactions table, everything else (a ``field``/``control`` table) the Filters
    table. The ``none`` sentinel row is dropped. An optional ``view`` column (issue #97)
    assigns each id to a workbook tab; blank / absent means :data:`DEFAULT_VIEW`.

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
    id_views: dict[str, str] = {}
    views_seen: set[str] = set()

    # Layout Grid (header starts with ``slot``) maps slot -> view for Elements rows.
    slot_views: dict[str, str] = {}
    for table in _markdown_tables(text):
        rows = [row for row in table if not _is_separator_row(row)]
        if len(rows) < 2:
            continue
        header = [cell.lower() for cell in rows[0]]
        if not header or header[0] != "slot":
            continue
        view_i = _header_index(header, "view")
        for row in rows[1:]:
            if not row or not row[0].strip():
                continue
            view = _cell(row, view_i) or DEFAULT_VIEW
            slot_views[row[0].strip()] = view
            views_seen.add(view)

    for table in _markdown_tables(text):
        rows = [row for row in table if not _is_separator_row(row)]
        if len(rows) < 2:
            continue
        header = [cell.lower() for cell in rows[0]]
        if not header or header[0] != "id":
            continue

        view_i = _header_index(header, "view")
        slot_i = _header_index(header, "slot")
        for row in rows[1:]:
            if not row or not row[0].strip():
                continue
            row_id = row[0].strip()
            if row_id.lower() == NONE_ID:
                continue
            # Prefer the explicit view cell; else inherit the slot's view; else default.
            view = _cell(row, view_i)
            if not view and slot_i is not None:
                view = slot_views.get(_cell(row, slot_i), "")
            view = view or DEFAULT_VIEW
            id_views[row_id] = view
            views_seen.add(view)
            if "slot" in header:
                element_ids.append(row_id)
            elif "interaction" in header:
                interaction_ids.append(row_id)
            else:
                filter_ids.append(row_id)

    if not views_seen:
        views_seen.add(DEFAULT_VIEW)
    default_first = [DEFAULT_VIEW] if DEFAULT_VIEW in views_seen else []
    views = default_first + sorted(views_seen - {DEFAULT_VIEW})
    return PlanCoverage(
        canvas, element_ids, filter_ids, interaction_ids, views, id_views,
    )


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




def _view_canvas_html(html: str, view: str) -> Optional[str]:
    """Return the HTML of the canvas tagged data-view="<view>", if any.

    Looks for an element carrying data-view="<view>" (typically .view-canvas)
    and returns the slice from that opening tag through its matching close. Nested
    same-tag depth is tracked so a view canvas may contain child divs.

    Args:
        html: Full mock.html contents.
        view: The view name to find.

    Returns:
        The matching element HTML (inclusive), or None when absent.
    """
    attr = re.escape(view)
    open_re = re.compile(
        r"<([a-zA-Z0-9]+)([^>]*\bdata-view\s*=\s*[\"']" + attr + r"[\"'][^>]*)>",
        re.IGNORECASE,
    )
    match = open_re.search(html)
    if not match:
        return None
    tag = match.group(1)
    start = match.start()
    if match.group(0).rstrip().endswith("/>") or tag.lower() in {
        "img", "input", "br", "hr", "meta", "link",
    }:
        return match.group(0)

    open_tag = re.compile(rf"<{re.escape(tag)}\b[^>]*>", re.IGNORECASE)
    close_tag = re.compile(rf"</{re.escape(tag)}\s*>", re.IGNORECASE)
    pos = match.end()
    depth = 1
    while depth > 0:
        next_open = open_tag.search(html, pos)
        next_close = close_tag.search(html, pos)
        if next_close is None:
            return html[start:]
        if next_open is not None and next_open.start() < next_close.start():
            depth += 1
            pos = next_open.end()
        else:
            depth -= 1
            pos = next_close.end()
    return html[start:pos]


def rendered_plan_ids_for_view(html: str, view: str, *, multi_view: bool) -> set[str]:
    """Return data-plan-id values visible for a given view.

    On a single-view plan/mock, the whole document is searched (today's behaviour).
    On a multi-view mock, only ids inside the data-view="<view>" canvas count;
    if that canvas is missing the set is empty (every planned id is a gap).

    Args:
        html: mock.html contents.
        view: View name.
        multi_view: Whether the plan declared more than one view.

    Returns:
        The set of rendered plan ids for that view.
    """
    if not multi_view:
        return rendered_plan_ids(html)
    canvas_html = _view_canvas_html(html, view)
    if canvas_html is None:
        return set()
    return rendered_plan_ids(canvas_html)



# --- Coverage checklist + slot-sizing guard ----------------------------------

@dataclass(frozen=True)
class CoverageItem:
    """One row of the coverage checklist.

    Attributes:
        kind: ``"screen size"``, ``"element"``, ``"filter"``, or ``"interaction"``.
        label: The id (or canvas size) being checked.
        rendered: Whether the mock renders it.
        view: Workbook tab the id belongs to (multi-view plans); ``None`` for screen size
            or a single-view plan.
    """

    kind: str
    label: str
    rendered: bool
    view: Optional[str] = None


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
    layout manifest. On a multi-view plan (issue #99), each id is checked against the
    canvas tagged ``data-view="<view>"`` for its declared view — a miss is a gap that
    names the view. A single-view plan checks the whole document exactly as before.
    Guard: every element box must be in-bounds, readable, and the layout must not be
    empty-space-heavy. The mock is valid iff there are no coverage gaps, no missing
    boxes, and no guard violations.

    Args:
        plan_text: The contents of ``DASHBOARD-PLAN.md``.
        html: The contents of the ``mock.html`` under validation.

    Returns:
        A :class:`MockValidation`.
    """
    spec = parse_plan_coverage(plan_text)
    multi = spec.is_multi_view
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
        if multi and isinstance((manifest or {}).get("views"), list):
            # Each view entry may carry its own canvas; all must match the plan size.
            view_ok = True
            for entry in manifest["views"]:
                if not isinstance(entry, dict):
                    view_ok = False
                    break
                canvas = entry.get("canvas") or {}
                if (canvas.get("width"), canvas.get("height")) != (plan_w, plan_h):
                    view_ok = False
                    break
            if not manifest["views"]:
                view_ok = False
            items.append(CoverageItem(
                "screen size", f"{plan_w}x{plan_h}px", view_ok,
            ))
        else:
            canvas = (manifest or {}).get("canvas") or {}
            rendered_size = (canvas.get("width"), canvas.get("height"))
            items.append(CoverageItem(
                "screen size", f"{plan_w}x{plan_h}px", rendered_size == (plan_w, plan_h),
            ))

    # Per-view rendered sets (single-view uses the whole document).
    rendered_by_view = {
        view: rendered_plan_ids_for_view(html, view, multi_view=multi)
        for view in spec.views
    }
    # Fallback for ids somehow missing from id_views.
    whole_rendered = rendered_plan_ids(html)

    def _item(kind: str, plan_id: str) -> CoverageItem:
        view = spec.id_views.get(plan_id, DEFAULT_VIEW)
        rendered_set = rendered_by_view.get(view, set()) if multi else whole_rendered
        return CoverageItem(
            kind,
            plan_id,
            plan_id in rendered_set,
            view if multi else None,
        )

    for element_id in spec.element_ids:
        items.append(_item("element", element_id))
    for filter_id in spec.filter_ids:
        items.append(_item("filter", filter_id))
    for interaction_id in spec.interaction_ids:
        items.append(_item("interaction", interaction_id))

    # Slot-sizing: single manifest (today) or per-view manifests under ``views``.
    guard_violations: list[str] = []
    missing_boxes: list[str] = []
    if multi and isinstance((manifest or {}).get("views"), list):
        elements_by_view: dict[str, list[str]] = {v: [] for v in spec.views}
        for eid in spec.element_ids:
            elements_by_view.setdefault(
                spec.id_views.get(eid, DEFAULT_VIEW), []
            ).append(eid)
        by_name = {
            str(entry.get("name")): entry
            for entry in manifest["views"]
            if isinstance(entry, dict) and entry.get("name") is not None
        }
        for view, eids in elements_by_view.items():
            if not eids:
                continue
            entry = by_name.get(view)
            if entry is None:
                missing_boxes.extend(eids)
                guard_violations.append(
                    f"view '{view}': no layout manifest entry (need views[].name)"
                )
                continue
            # Reuse the single-canvas guard against a synthetic manifest for this view.
            view_manifest = {
                "canvas": entry.get("canvas") or (manifest or {}).get("canvas"),
                "elements": entry.get("elements") or [],
            }
            v_violations, v_missing = slot_sizing_violations(view_manifest, eids)
            guard_violations.extend(
                f"view '{view}': {msg}" for msg in v_violations
            )
            missing_boxes.extend(v_missing)
    else:
        guard_violations, missing_boxes = slot_sizing_violations(
            manifest, spec.element_ids
        )

    has_gap = any(not item.rendered for item in items)
    ok = not has_gap and not guard_violations and not missing_boxes
    return MockValidation(ok, items, guard_violations, missing_boxes, notes)
