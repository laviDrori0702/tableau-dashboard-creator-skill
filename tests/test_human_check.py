"""Unit tests for the human-route checks (issue #101)."""

from __future__ import annotations

import human_check


PLAN = """# Dashboard Plan

## Elements
| id | type | view |
|----|------|------|
| kpi-a | kpi | Overview |
| chart-b | chart | Detail |

## Filters
| id | field | view |
|----|-------|------|
| flt-region | region | Overview |

## Interactions
| id | interaction |
|----|-------------|
| int-click | filter |
"""


def test_plan_ids_reads_all_id_tables():
    assert human_check.plan_ids(PLAN) == ["kpi-a", "chart-b", "flt-region", "int-click"]


def test_coverage_ok_when_every_id_has_element_line():
    pages = [
        "Element: kpi-a\nElement: flt-region\n",
        "Element: chart-b\nElement: int-click\n",
    ]
    result = human_check.check_coverage(human_check.plan_ids(PLAN), pages)
    assert result.ok
    assert result.missing == []
    assert "[x] kpi-a" in result.checklist[0]


def test_coverage_flags_missing_element_lines():
    result = human_check.check_coverage(
        human_check.plan_ids(PLAN), ["Element: kpi-a\n"]
    )
    assert not result.ok
    assert "chart-b" in result.missing
    assert "flt-region" in result.missing
    block = human_check.format_coverage_checklist(result)
    assert "[REFUSED]" in block or "missing" in block.lower()


def test_primitive_guard_requires_justification():
    bad = human_check.check_primitive_guard(["Use an LOD on sales"])
    assert bad
    good = human_check.check_primitive_guard(
        ["Use an LOD because the grain of the extract is too fine"]
    )
    assert good == []


def test_page_set_requires_one_page_per_view_and_patterns_rules():
    views = ["Overview", "Detail"]
    texts = {
        "IMPLEMENTATION-SPEC.md": "# guide\n",
        "spec/overview.md": "Element: kpi-a\nPattern: kpi-card\n",
        "spec/detail.md": "Element: chart-b\nPattern: kpi-card\n",
    }
    # Shared pattern without patterns.md -> problem
    problems = human_check.check_page_set(views, texts.keys(), texts)
    assert any("patterns.md" in p for p in problems)

    texts["spec/patterns.md"] = "## Pattern: kpi-card\nshared layout\n"
    assert human_check.check_page_set(views, texts.keys(), texts) == []

    # patterns.md without shared pattern -> problem
    texts["spec/overview.md"] = "Element: kpi-a\n"
    texts["spec/detail.md"] = "Element: chart-b\n"
    problems = human_check.check_page_set(views, texts.keys(), texts)
    assert any("omit" in p.lower() or "omitted" in p.lower() or "no sheet" in p.lower() for p in problems)


def test_view_page_filename_slug():
    assert human_check.view_page_filename("Overview") == "overview.md"
    assert human_check.view_page_filename("Q1 Review!") == "q1-review.md"
