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
"""

from __future__ import annotations

import re
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
        ok: True iff every mock element is mapped and every advanced-feature escalation is
            justified.
        items: The reconciliation checklist (one item per mock element).
        extra_ids: Ids mapped in the spec that do not exist in the mock (non-fatal note).
        notes: Non-fatal observations.
    """

    ok: bool
    items: list[MappingItem]
    extra_ids: list[str]
    notes: list[str] = field(default_factory=list)

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
    parameter action) must carry a non-empty justification. The spec is valid iff there
    are no unmapped mock elements and no unjustified escalations.

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

    ok = all(item.mapped and item.justified for item in items)
    return SpecValidation(ok, items, extra_ids, notes)
