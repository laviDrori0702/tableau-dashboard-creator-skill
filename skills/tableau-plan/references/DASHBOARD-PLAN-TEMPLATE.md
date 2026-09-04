# Dashboard Plan: <dashboard name>

> Strict plan for the tableau-dashboard-plugin workflow. **Every required section below
> must be present and complete** — `tableau-mock` builds directly from this file, so an
> incomplete plan is rejected and blocks the mock. Every KPI, chart, filter, and
> interaction carries a **stable `id`** that later steps (mock, spec, build) reference;
> ids must be unique across the whole plan. Interactions use the shared vocabulary
> (CONTRACT.md §6). Replace every `<...>` and keep the table structures intact.

## Summary

<1–3 sentences: what this dashboard is for, who reads it, what decisions it drives, and
the update cadence. (Optional but strongly recommended.)>

## Screen Size

Screen size is decided **here**, not in `tableau-mock`, so slot sizing is correct
downstream.

- **mode**: fixed                 # fixed | range | automatic
- **dimensions**: 1366 x 768 px   # the design canvas; the slot sizes below are relative to this
- **rationale**: <primary viewing device / embed target that drives this size>

## Layout Grid

The named slots that elements are placed into, with position and size (relative to the
canvas above). Element rows reference these slot names.

| slot        | position             | size           |
|-------------|----------------------|----------------|
| filter-bar  | top, full width      | 100% x 56px    |
| kpi-row     | below filter-bar     | 100% x 120px   |
| chart-main  | middle-left          | 60% x 360px    |
| chart-side  | middle-right         | 40% x 360px    |

## Elements

Every KPI and chart. `id` is stable and unique; `type` is `kpi` or `chart` (or a chart
kind, e.g. `chart:line`); `columns` are the DATA-MODEL.md field(s) used; `slot` must be
one of the Layout Grid slots above; `size` is the footprint within that slot.

| id           | type        | columns                       | slot       | size       |
|--------------|-------------|-------------------------------|------------|------------|
| kpi-revenue  | kpi         | revenue                       | kpi-row    | 1/4 of row |
| kpi-orders   | kpi         | order_id (distinct count)     | kpi-row    | 1/4 of row |
| chart-trend  | chart:line  | order_date, revenue, region   | chart-main | fills slot |
| chart-region | chart:bar   | region, revenue               | chart-side | fills slot |

## Filters

Every filter. `scope` lists which elements (by id) it affects. If the dashboard has no
filters, keep this section and write a single row with `id` = `none`.
`control type` is one of `dropdown (multi)` (checkbox list + Apply button), `dropdown (single)`,
`date range`, `slider`. Default to `dropdown (multi)` for dimensions and `date range` for dates.

| id         | field      | control type     | scope                      | default    |
|------------|------------|------------------|----------------------------|------------|
| flt-date   | order_date | date range       | all                        | last 12 mo |
| flt-region | region     | dropdown (multi) | chart-trend, chart-region  | All        |

## Interactions

Every interaction (action). The `interaction` column **must** use a term from the shared
vocabulary (CONTRACT.md §6): `toggle panel`, `swap view`, `drill`, `cross-filter`,
`highlight`, `parameter swap`. `source` and `target` reference element ids. If there are
no interactions, keep this section with a single row `id` = `none`, `interaction` =
`toggle panel` (or remove the table and write "None").

| id                  | interaction  | source       | target                    | detail                              |
|---------------------|--------------|--------------|---------------------------|-------------------------------------|
| int-region-filter   | cross-filter | chart-region | chart-trend, kpi-revenue  | click a region to filter the others |
| int-trend-highlight | highlight    | chart-trend  | chart-region              | hover a month to highlight it       |

## Suggestions

Additional KPIs or patterns **beyond the literal request** — value the data supports that
the analyst did not ask for. Mark each clearly as a suggestion, not a requirement.

1. <a suggested KPI/chart and why it helps the audience>
2. <an interesting pattern the data supports, e.g. discount-impact or segment mix>

## Data Gaps

<Anything the request wants that the data cannot support — surfaced here, before the mock,
so it is visible rather than silently dropped. Write "None" if there are no gaps.>
