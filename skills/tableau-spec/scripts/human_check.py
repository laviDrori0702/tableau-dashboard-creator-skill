"""Pure checks for the human (Desktop build guide) route of tableau-spec.

Issue #101: on ``spec_mode: human`` the skill writes a Desktop guide at the project
root instead of a machine-readable IMPLEMENTATION-SPEC.md under mock-version/. The
guide keeps the two guarantees the agent route has - every plan id is covered, and
any escalation to an advanced Tableau construct carries a written justification -
but the carriers are prose conventions, not the Element Mapping table + Layout JSON.

This module is stdlib-only and never touches STATE.md. ``reconcile.py`` is untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# --- Carriers / markers -------------------------------------------------------

ELEMENT_LINE = re.compile(r"(?im)^\s*Element:\s*(\S+)\s*$")
PATTERN_LINE = re.compile(r"(?im)^\s*Pattern:\s*(\S+)\s*$")

ADVANCED_TERMS: tuple[str, ...] = (
    "Dynamic Zone Visibility",
    "DZV",
    "LOD",
    "{FIXED",
    "{INCLUDE",
    "{EXCLUDE",
    "table calculation",
    "table calc",
    "WINDOW_",
    "RUNNING_",
    "LOOKUP(",
    "parameter action",
    "Parameter Action",
)

JUSTIFICATION_HINTS = re.compile(
    r"(?i)\b(because|justification|why|reason|needed|requires?|cannot|can't|"
    r"instead of|rather than|rejected|no simpler)\b"
)

OPTIONAL_SUPPORT_PAGES = frozenset(
    {"calculations.md", "parameters.md", "fields.md", "patterns.md"}
)
OPTIONAL_DASHBOARD_PAGES = frozenset({"shared-sidebar.md"})
ROOT_GUIDE_NAME = "IMPLEMENTATION-SPEC.md"
SPEC_DIR_NAME = "spec"
DASHBOARDS_SUBDIR = "dashboards"


# --- Plan id extraction -------------------------------------------------------

def _markdown_tables(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            current.append(cells)
        else:
            if current:
                tables.append(current)
                current = []
    if current:
        tables.append(current)
    return tables


def _is_separator_row(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells)


def plan_ids(plan_text: str) -> list[str]:
    """Return every stable id from Elements / Filters / Interactions tables."""
    seen: set[str] = set()
    ordered: list[str] = []
    for table in _markdown_tables(plan_text):
        rows = [r for r in table if not _is_separator_row(r)]
        if len(rows) < 2:
            continue
        header = [c.lower() for c in rows[0]]
        if not header or header[0] != "id":
            continue
        for row in rows[1:]:
            if not row:
                continue
            rid = row[0].strip()
            if rid and rid not in seen:
                seen.add(rid)
                ordered.append(rid)
    return ordered


def view_page_filename(view: str) -> str:
    """Map a declared view name to its ``spec/dashboards/<file>.md`` basename."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", view.strip()).strip("-").lower()
    return f"{slug or 'default'}.md"


def view_page_relpath(view: str) -> str:
    """Return ``spec/dashboards/<slug>.md`` for a declared view."""
    return f"{SPEC_DIR_NAME}/{DASHBOARDS_SUBDIR}/{view_page_filename(view)}"


# --- Coverage -----------------------------------------------------------------

@dataclass(frozen=True)
class CoverageResult:
    """Outcome of scanning Element: carriers against the plan's ids."""

    covered: list[str]
    missing: list[str]
    extras: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing


def element_ids_in(texts: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for text in texts:
        found.update(m.group(1) for m in ELEMENT_LINE.finditer(text))
    return found


def check_coverage(plan_id_list: list[str], page_texts: Iterable[str]) -> CoverageResult:
    carried = element_ids_in(page_texts)
    required = list(plan_id_list)
    missing = [i for i in required if i not in carried]
    covered = [i for i in required if i in carried]
    extras = sorted(carried - set(required))
    checklist = [
        (f"[x] {i}" if i in carried else f"[ ] {i}  <- MISSING Element: line")
        for i in required
    ]
    return CoverageResult(
        covered=covered, missing=missing, extras=extras, checklist=checklist
    )


def format_coverage_checklist(result: CoverageResult) -> str:
    lines = ["Coverage checklist (plan -> human guide):", *result.checklist]
    if result.missing:
        lines.append(
            "[REFUSED] missing Element: line(s) for: " + ", ".join(result.missing)
        )
    else:
        lines.append("[OK] every plan id has an Element: line.")
    return "\n".join(lines)


# --- Simplest-primitive guard -------------------------------------------------

def _window_around(text: str, index: int, radius: int = 240) -> str:
    return text[max(0, index - radius): min(len(text), index + radius)]


def check_primitive_guard(page_texts: Iterable[str]) -> list[str]:
    problems: list[str] = []
    for text in page_texts:
        lower = text.lower()
        for term in ADVANCED_TERMS:
            start = 0
            term_lower = term.lower()
            while True:
                idx = lower.find(term_lower, start)
                if idx < 0:
                    break
                window = _window_around(text, idx)
                if not JUSTIFICATION_HINTS.search(window):
                    problems.append(
                        f"advanced construct '{term}' needs a written justification nearby"
                    )
                start = idx + len(term)
    seen: set[str] = set()
    unique: list[str] = []
    for p in problems:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


# --- Page-set rules -----------------------------------------------------------

def shared_patterns(page_texts: Iterable[str]) -> set[str]:
    counts: dict[str, int] = {}
    for text in page_texts:
        for match in PATTERN_LINE.finditer(text):
            name = match.group(1)
            counts[name] = counts.get(name, 0) + 1
    return {name for name, n in counts.items() if n >= 2}


def check_page_set(
    views: list[str],
    relative_paths: Iterable[str],
    page_texts_by_path: dict[str, str],
) -> list[str]:
    """Validate the human-route page set under IMPLEMENTATION-SPEC.md + spec/.

    View pages live under ``spec/dashboards/<view>.md`` (Polaris layout). Support
    pages stay as siblings of ``dashboards/``. ``shared-sidebar.md`` under
    dashboards/ is optional shared chrome.
    """
    problems: list[str] = []
    norm = {p.replace("\\", "/") for p in relative_paths}
    texts = {k.replace("\\", "/"): v for k, v in page_texts_by_path.items()}

    if ROOT_GUIDE_NAME not in norm:
        problems.append(f"missing root guide '{ROOT_GUIDE_NAME}'")

    expected_views = {view_page_relpath(v) for v in views}
    for path in sorted(expected_views):
        if path not in norm:
            problems.append(f"missing view page '{path}'")
        elif not texts.get(path, "").strip():
            problems.append(
                f"view page '{path}' is empty - omit empty pages or fill them"
            )

    dash_prefix = f"{SPEC_DIR_NAME}/{DASHBOARDS_SUBDIR}/"
    under_dashboards = {
        p for p in norm if p.startswith(dash_prefix) and p.count("/") == 2
    }
    allowed_dashboard_extras = {
        f"{dash_prefix}{n}" for n in OPTIONAL_DASHBOARD_PAGES
    }
    for path in sorted(under_dashboards - expected_views - allowed_dashboard_extras):
        problems.append(f"unexpected page '{path}' under {dash_prefix}")

    under_spec = {
        p for p in norm if p.startswith(f"{SPEC_DIR_NAME}/") and p.count("/") == 1
    }
    support_present = under_spec
    allowed_support = {f"{SPEC_DIR_NAME}/{n}" for n in OPTIONAL_SUPPORT_PAGES}
    for path in sorted(support_present - allowed_support):
        problems.append(f"unexpected page '{path}' under {SPEC_DIR_NAME}/")

    scanned_paths = expected_views | (
        support_present - {f"{SPEC_DIR_NAME}/patterns.md"}
    )
    scanned_paths |= under_dashboards & allowed_dashboard_extras
    scanned_for_patterns = [texts[p] for p in sorted(scanned_paths) if p in texts]
    shared = shared_patterns(scanned_for_patterns)
    patterns_path = f"{SPEC_DIR_NAME}/patterns.md"
    if shared and patterns_path not in norm:
        problems.append(
            "patterns.md required - Pattern names used on two or more sheets: "
            + ", ".join(sorted(shared))
        )
    if not shared and patterns_path in norm:
        problems.append(
            "patterns.md must be omitted when no sheet shape repeats "
            "(no Pattern: name appears on two or more elements)"
        )

    for name in OPTIONAL_SUPPORT_PAGES:
        path = f"{SPEC_DIR_NAME}/{name}"
        if path in norm and not texts.get(path, "").strip():
            problems.append(
                f"'{path}' is empty - omit empty pages rather than stubbing them"
            )
    for name in OPTIONAL_DASHBOARD_PAGES:
        path = f"{dash_prefix}{name}"
        if path in norm and not texts.get(path, "").strip():
            problems.append(
                f"'{path}' is empty - omit empty pages rather than stubbing them"
            )

    return problems

