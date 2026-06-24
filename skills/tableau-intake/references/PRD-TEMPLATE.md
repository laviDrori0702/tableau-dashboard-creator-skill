# <Dashboard name> — PRD

> Canonical `PRD.md` structure produced by `tableau-intake`. Fill each section
> from the analyst's request. **`## Overview` and `## Visualizations` are the
> required core** — every dashboard PRD has them. `## KPIs`, `## Filters`, and
> `## Additional Notes` are **optional**: propose them while refining, but omit a
> section the analyst says they don't need rather than padding the PRD. You may
> add your own sections too — the validator only checks the required core is present.

## Overview

_Purpose, audience, and update frequency. Who is this for, what decisions does it
support, and how often is the data refreshed?_

## Visualizations

_The charts and what each shows. One numbered entry per view — chart type, the
measures/dimensions it plots, and any grouping, sorting, or reference lines._

1. **<Chart name>** — <chart type> of <measure> by <dimension>; <sort / breakdown / notes>.

## KPIs

> _Optional — include if the dashboard has headline numbers. Remove this section
> if the analyst says KPIs aren't needed._

_The headline metrics to track, with a formula where it's specific._

1. **<KPI name>** — <definition / formula>, <comparison to target if any>.

## Filters

> _Optional — include if users need to slice the data. Remove this section if the
> analyst says filters aren't needed._

_The filtering controls users need._

- **<Filter name>** — <field it filters and control type (dropdown, date range, …)>.

## Additional Notes

> _Optional — branding, conditional formatting, sort orders, number formats, or any
> other must-haves and constraints. Remove if there are none._

- <constraint or styling note>
