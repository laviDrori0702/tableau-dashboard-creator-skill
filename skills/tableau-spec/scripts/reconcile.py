"""The coverage-reconciliation + simplest-primitive guard core of tableau-spec (CONTRACT.md step 7).

This is the testable heart of ``tableau-spec``, kept pure (stdlib-only, no STATE.md, no
filesystem) so the contract test can drive it directly. ``spec.py`` owns the STATE.md /
versioning / entry-gate plumbing and imports :func:`reconcile` from here. It owns two jobs:

1. **Coverage reconciliation** - the mirror of the mock's coverage checklist. Every mock
   element (every ``data-plan-id`` in ``mock.html``) MUST be mapped to a Tableau construct
   in ``IMPLEMENTATION-SPEC.md``'s Element Mapping table. A mock id with no mapping row is
   an **unmapped** element that blocks approval - this is what makes "every mock element
   maps to a construct, nothing unmapped" a guarantee rather than a hope.
2. **Simplest-primitive guard** - each element should default to the simplest sufficient
   Tableau primitive. Any escalation to an advanced feature (Dynamic Zone Visibility, LOD
   expression, table calculation, parameter action) is detected by keyword in the mapping's
   construct cell and MUST carry a non-empty justification (why the advanced feature over
   the simpler alternative). An advanced feature with no justification is **flagged**, so
   the workbook is not silently over-engineered.

The concrete "what is the simplest primitive for X" knowledge lives in the validated snippet
library and the SKILL.md's decision guidance; this module enforces only the mechanical rule
"advanced feature present => a justification must be written".

3. **Layout reconciliation** (issue #32) - the spec MUST carry a ``## Layout`` section
   holding a fenced JSON container tree (canvas dimensions + nested ``vert``/``horz``
   containers + element-id leaves with percentage sizes). This is how the mock's geometry
   reaches ``tableau-build``; without it the workbook's layout is guesswork. The tree must
   place every mapped *zone* id exactly once (interaction ids - ``int-*`` - are dashboard
   actions, not zones, so they never appear in the tree), must not place unmapped ids, and
   sibling sizes must sum to ~100%.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

#: The id-string the plan uses for a "no filters" / "no interactions" sentinel row; if it
#: ever leaks into the mock as a data-plan-id it is not a real element, so we skip it.
NONE_ID = "none"

# --- Advanced-feature detection (the simplest-primitive guard) ----------------
# ponytail: keyword heuristics, not a Tableau parser. Each pattern names one advanced
# construct whose use must be justified against the simpler alternative (CONTRACT.md §6 /
# issue #13). Matched case-insensitively against a mapping's construct cell. Add a row here
# only when a new recurring over-engineering pattern appears.
ADVANCED_FEATURE_PATTERNS: dict[str, str] = {
    "Dynamic Zone Visibility": r"dynamic\s+zone\s+visibility|\bDZV\b",
    "LOD expression": r"\bLOD\b|level\s+of\s+detail|\{\s*(fixed|include|exclude)\b",
    "table calculation": (
        r"table\s+calc|WINDOW_\w+|RUNNING_\w+|LOOKUP\s*\(|\bINDEX\s*\(\s*\)"
        r"|\bRANK\b|\bTOTAL\s*\("
    ),
    "parameter action": r"parameter\s+action",
}

_ADVANCED_COMPILED = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in ADVANCED_FEATURE_PATTERNS.items()
}

#: Cell values that mean "no justification given" (a blank / placeholder / dash cell).
_ABSENT_JUSTIFICATION = frozenset({"", "-", "--", "n/a", "na", "none", "todo", "tbd"})


def advanced_features_in(construct: str) -> list[str]:
    """Return the advanced Tableau features a construct description mentions.

    Args:
        construct: The Tableau-construct text from an Element Mapping row.

    Returns:
        The names of matched advanced features (empty when the construct is a simple
        primitive that needs no justification).
    """
    return [name for name, rx in _ADVANCED_COMPILED.items() if rx.search(construct)]


def justification_present(justification: str) -> bool:
    """Return True if a justification cell holds a real reason (not blank/placeholder).

    Args:
        justification: The justification cell text from an Element Mapping row.

    Returns:
        True when the cell is a non-placeholder, non-``<...>`` string.
    """
    stripped = justification.strip()
    if stripped.startswith("<"):  # an unfilled "<why...>" template placeholder
        return False
    return stripped.lower() not in _ABSENT_JUSTIFICATION


# --- Mock parsing: the elements that MUST be mapped --------------------------

_DATA_PLAN_ID = re.compile(r"""data-plan-id\s*=\s*["']([^"']+)["']""")


def mock_element_ids(html: str) -> list[str]:
    """Return every ``data-plan-id`` value in the mock HTML, in first-seen order.

    These are the mock elements the spec must reconcile - every one needs a mapping row.
    The ``none`` sentinel (should it ever appear) and duplicates are dropped.

    Args:
        html: The contents of a ``mock.html`` file.

    Returns:
        The ordered, de-duplicated list of mock element ids.
    """
    seen: dict[str, None] = {}
    for match in _DATA_PLAN_ID.finditer(html):
        plan_id = match.group(1).strip()
        if plan_id and plan_id.lower() != NONE_ID:
            seen.setdefault(plan_id, None)
    return list(seen)


# --- Spec parsing: the Element Mapping table ---------------------------------

_SEPARATOR_CELL = re.compile(r"^:?-+:?$")


def _markdown_tables(text: str) -> list[list[list[str]]]:
    """Split a markdown document into its pipe tables (rows of trimmed cells)."""
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


@dataclass(frozen=True)
class Mapping:
    """One row of the spec's Element Mapping table.

    Attributes:
        id: The mock element id this row maps (matches a ``data-plan-id``).
        construct: The Tableau construct the element becomes.
        justification: The reason for any advanced-feature escalation (blank when the
            construct is a simple primitive).
    """

    id: str
    construct: str
    justification: str


def parse_spec_mappings(spec_text: str) -> list[Mapping]:
    """Extract the Element Mapping rows from an ``IMPLEMENTATION-SPEC.md``.

    The mapping table is the one whose header's first column is ``id`` and that has a
    column whose header contains ``construct``. The ``justification`` column is located by
    header name (any column containing ``justif``); if absent, justifications read as blank.

    Args:
        spec_text: The contents of an ``IMPLEMENTATION-SPEC.md`` file.

    Returns:
        The parsed mapping rows (empty when no Element Mapping table is present).
    """
    for table in _markdown_tables(spec_text):
        rows = [row for row in table if not _is_separator_row(row)]
        if len(rows) < 2:
            continue
        header = [cell.lower() for cell in rows[0]]
        if not header or header[0] != "id":
            continue
        construct_col = next(
            (i for i, cell in enumerate(header) if "construct" in cell), None
        )
        if construct_col is None:
            continue
        justif_col = next((i for i, cell in enumerate(header) if "justif" in cell), None)

        mappings: list[Mapping] = []
        for row in rows[1:]:
            element_id = row[0].strip() if row else ""
            if not element_id or element_id.lower() == NONE_ID:
                continue
            construct = row[construct_col].strip() if construct_col < len(row) else ""
            justification = (
                row[justif_col].strip()
                if justif_col is not None and justif_col < len(row)
                else ""
            )
            mappings.append(Mapping(element_id, construct, justification))
        return mappings
    return []


# --- Layout container tree (issue #32) ----------------------------------------

#: Valid container orientations in the layout tree.
CONTAINER_TYPES = frozenset({"vert", "horz"})

#: Mapped ids with this prefix are dashboard *actions* (plan convention: ``int-region-filter``)
#: - they occupy no zone, so the layout tree neither requires nor allows placing them.
INTERACTION_PREFIX = "int-"

#: How far sibling sizes may drift from 100% before they "don't sum sanely" (rounding slack).
SIBLING_SIZE_TOLERANCE = 2.0

# Exactly '## Layout' (the level the contract/template mandate) so the section reliably
# ends at the next '## ' heading.
_LAYOUT_HEADING = re.compile(r"^##\s+Layout\b.*$", re.MULTILINE)
_NEXT_SECTION = re.compile(r"^##\s", re.MULTILINE)
_FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def extract_layout_block(spec_text: str) -> Optional[str]:
    """Return the fenced JSON text under the spec's ``## Layout`` heading, or ``None``.

    Args:
        spec_text: The contents of an ``IMPLEMENTATION-SPEC.md`` file.

    Returns:
        The inner text of the first fenced code block between the ``## Layout`` heading
        and the next ``## `` section, or ``None`` when the heading or block is absent.
    """
    heading = _LAYOUT_HEADING.search(spec_text)
    if heading is None:
        return None
    section = spec_text[heading.end():]
    next_section = _NEXT_SECTION.search(section)
    if next_section is not None:
        section = section[: next_section.start()]
    block = _FENCED_BLOCK.search(section)
    return block.group(1) if block else None


def _is_number(value: object) -> bool:
    """Return True for a real (non-bool) JSON number."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _walk_layout_node(
    node: object, path: str, is_root: bool, placed: list[str], errors: list[str]
) -> None:
    """Validate one layout node (and recurse), appending placed ids and errors.

    A node is a **container** (``type`` in vert/horz + non-empty ``children``), a **leaf**
    (``id`` naming a mapped element), or both - a *mapped container* (e.g. a DZV panel
    that is itself an element and holds further zones). Every non-root node needs a
    positive numeric ``size`` (percent of its parent), and each container's children must
    sum to ~100.

    Args:
        node: The JSON value at this position in the tree.
        path: Human-readable position (e.g. ``root.children[1]``) for error messages.
        is_root: True for the top-level container (which needs no ``size``).
        placed: Accumulator for every placed id encountered.
        errors: Accumulator for validation errors.
    """
    if not isinstance(node, dict):
        errors.append(f"{path}: every layout node must be a JSON object")
        return

    has_id, is_container = "id" in node, "children" in node
    if not has_id and not is_container:
        errors.append(
            f"{path}: a node needs an 'id' (leaf), 'children' (container), or both "
            f"(a mapped container, e.g. a DZV panel)"
        )
        return

    if not is_root and not (_is_number(node.get("size")) and node["size"] > 0):
        errors.append(f"{path}: missing a positive numeric 'size' (percent of parent)")

    if has_id:
        element_id = node["id"]
        if isinstance(element_id, str) and element_id.strip():
            placed.append(element_id.strip())
        else:
            errors.append(f"{path}: 'id' must be a non-empty string")
    if not is_container:
        return

    if node.get("type") not in CONTAINER_TYPES:
        errors.append(
            f"{path}: container 'type' must be 'vert' or 'horz' (got {node.get('type')!r})"
        )
    children = node["children"]
    if not isinstance(children, list) or not children:
        errors.append(f"{path}: 'children' must be a non-empty list")
        return
    for index, child in enumerate(children):
        _walk_layout_node(child, f"{path}.children[{index}]", False, placed, errors)

    sizes = [child.get("size") for child in children if isinstance(child, dict)]
    if len(sizes) == len(children) and all(_is_number(size) for size in sizes):
        total = sum(sizes)
        if abs(total - 100) > SIBLING_SIZE_TOLERANCE:
            errors.append(
                f"{path}: sibling sizes sum to {total:g}, expected ~100 "
                f"(each child's share of its parent)"
            )


def validate_layout(spec_text: str, mapped_ids: list[str]) -> list[str]:
    """Validate the spec's Layout container tree against its Element Mapping ids.

    The Layout section is **required** (it is how the mock's geometry reaches the build):
    a missing section/block, unparseable JSON, a malformed tree, a mapped zone id absent or
    placed more than once, an id in the tree with no mapping row, or siblings whose sizes
    don't sum to ~100% each produce an actionable error.

    Args:
        spec_text: The contents of an ``IMPLEMENTATION-SPEC.md`` file.
        mapped_ids: The ids of the spec's Element Mapping rows. Ids prefixed ``int-``
            (interactions/actions) are exempt from placement - they occupy no zone.

    Returns:
        A list of error messages; empty when the layout is present and consistent.
    """
    block = extract_layout_block(spec_text)
    if block is None:
        return [
            "Layout section missing: add a '## Layout' section with a fenced JSON "
            "container tree (canvas + nested vert/horz containers + element-id leaves "
            "with % sizes) - see IMPLEMENTATION-SPEC-TEMPLATE.md"
        ]
    try:
        layout = json.loads(block)
    except json.JSONDecodeError as exc:
        return [f"Layout JSON does not parse: {exc}"]
    if not isinstance(layout, dict):
        return ["Layout JSON must be an object with 'canvas' and 'root' keys"]

    errors: list[str] = []
    canvas = layout.get("canvas")
    if not (
        isinstance(canvas, dict)
        and _is_number(canvas.get("width")) and canvas["width"] > 0
        and _is_number(canvas.get("height")) and canvas["height"] > 0
    ):
        errors.append(
            "Layout 'canvas' must carry positive numeric 'width' and 'height' "
            "(the mock's design dimensions in px)"
        )

    root = layout.get("root")
    placed: list[str] = []
    if isinstance(root, dict):
        _walk_layout_node(root, "root", True, placed, errors)
    else:
        errors.append("Layout 'root' must be a container object ('type' + 'children')")

    placement_counts = Counter(placed)
    duplicates = sorted(pid for pid, count in placement_counts.items() if count > 1)
    if duplicates:
        errors.append(
            "element id(s) placed more than once in the layout tree: "
            + ", ".join(duplicates)
        )

    mapped_set = set(mapped_ids)
    unknown = sorted(pid for pid in placement_counts if pid not in mapped_set)
    if unknown:
        errors.append(
            "layout tree places id(s) with no Element Mapping row: " + ", ".join(unknown)
        )

    zone_ids = [pid for pid in mapped_ids if not pid.startswith(INTERACTION_PREFIX)]
    unplaced = [pid for pid in zone_ids if pid not in placement_counts]
    if unplaced:
        errors.append(
            "mapped element id(s) missing from the layout tree: " + ", ".join(unplaced)
        )
    misplaced_actions = sorted(
        pid for pid in placement_counts if pid.startswith(INTERACTION_PREFIX)
    )
    if misplaced_actions:
        errors.append(
            "interaction id(s) placed in the layout tree (actions occupy no zone): "
            + ", ".join(misplaced_actions)
        )
    return errors


# --- Reconciliation result ---------------------------------------------------

@dataclass(frozen=True)
class MappingItem:
    """One row of the reconciliation checklist (one per mock element).

    Attributes:
        id: The mock element id.
        mapped: Whether a mapping row for it exists in the spec.
        construct: The mapped Tableau construct (empty when unmapped).
        advanced_features: Advanced features the construct uses (empty when simplest).
        justified: True when the mapping needs no justification (simple primitive) or
            carries one; False only for an unjustified advanced-feature escalation.
    """

    id: str
    mapped: bool
    construct: str
    advanced_features: list[str]
    justified: bool


@dataclass(frozen=True)
class SpecValidation:
    """Result of reconciling an ``IMPLEMENTATION-SPEC.md`` against its ``mock.html``.

    Attributes:
        ok: True iff every mock element is mapped, every advanced-feature escalation is
            justified, and the Layout container tree is present and consistent.
        items: The reconciliation checklist (one item per mock element).
        extra_ids: Ids mapped in the spec that do not exist in the mock (non-fatal note).
        notes: Non-fatal observations.
        layout_errors: Layout-section problems (each blocks approval; empty when the
            container tree is present and consistent with the Element Mapping).
    """

    ok: bool
    items: list[MappingItem]
    extra_ids: list[str]
    notes: list[str] = field(default_factory=list)
    layout_errors: list[str] = field(default_factory=list)

    @property
    def unmapped(self) -> list[str]:
        """list[str]: Mock elements with no mapping row (blocks approval)."""
        return [item.id for item in self.items if not item.mapped]

    @property
    def unjustified(self) -> list[MappingItem]:
        """list[MappingItem]: Advanced-feature escalations with no justification."""
        return [
            item for item in self.items
            if item.mapped and item.advanced_features and not item.justified
        ]


def reconcile(mock_html: str, spec_text: str) -> SpecValidation:
    """Reconcile a spec against its mock: coverage + simplest-primitive guard.

    Coverage: every ``data-plan-id`` in the mock must have an Element Mapping row.
    Guard: any mapping whose construct uses an advanced feature (DZV / LOD / table calc /
    parameter action) must carry a non-empty justification. Layout: the spec must carry a
    ``## Layout`` container tree consistent with the mapping (:func:`validate_layout`).
    The spec is valid iff there are no unmapped mock elements, no unjustified escalations,
    and no layout errors.

    Args:
        mock_html: The contents of the approved ``mock.html``.
        spec_text: The contents of the ``IMPLEMENTATION-SPEC.md`` under validation.

    Returns:
        A :class:`SpecValidation`.
    """
    mock_ids = mock_element_ids(mock_html)
    mappings = {m.id: m for m in parse_spec_mappings(spec_text)}

    items: list[MappingItem] = []
    for element_id in mock_ids:
        mapping = mappings.get(element_id)
        if mapping is None:
            items.append(MappingItem(element_id, False, "", [], False))
            continue
        features = advanced_features_in(mapping.construct)
        justified = not features or justification_present(mapping.justification)
        items.append(
            MappingItem(element_id, True, mapping.construct, features, justified)
        )

    mock_id_set = set(mock_ids)
    extra_ids = sorted(mapped_id for mapped_id in mappings if mapped_id not in mock_id_set)

    notes: list[str] = []
    if not mock_ids:
        notes.append("mock.html declares no data-plan-id elements - nothing to reconcile")
    if extra_ids:
        notes.append(
            "spec maps id(s) not present in the mock (typo or stale mapping?): "
            + ", ".join(extra_ids)
        )

    layout_errors = validate_layout(spec_text, list(mappings.keys()))
    ok = all(item.mapped and item.justified for item in items) and not layout_errors
    return SpecValidation(ok, items, extra_ids, notes, layout_errors)
