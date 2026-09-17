# Dashboard Plan: Sales Performance

## Summary

A monthly sales review dashboard for the VP of Sales and regional managers: four headline
KPIs, the revenue trend by region, actual-vs-target by region, profit by product category,
and a ranked customer table. It answers "are we on plan, where are we off, and which
categories and accounts are carrying it" in one screen, refreshed monthly.

## Screen Size

- **mode**: range
- **dimensions**: 1366 x 900 px
- **rationale**: The primary audience opens this on a 1366-wide laptop; the brand sets a
  1100 x 800 minimum with no maximum (`DESIGN-TOKENS.md` → Dashboard Sizing), so 1366 x 900
  is the design canvas and the dashboard is free to grow on larger screens.

## Layout Grid

| slot             | position                          | size          |
|------------------|-----------------------------------|---------------|
| header           | top, full width                   | 100% x 63px   |
| filter-bar       | below header, full width          | 100% x 63px   |
| kpi-row          | below filter-bar, full width      | 100% x 126px  |
| chart-main       | middle-left                       | 60% x 360px   |
| chart-stack      | middle-right, two charts stacked  | 40% x 360px   |
| chart-lower-left | bottom-left                       | 40% x 288px   |
| chart-lower-right| bottom-right                      | 60% x 288px   |

The `header` slot holds the dashboard-title text object (`obj-title`) and the brand logo;
it carries no KPI or chart, so it has no Elements row.

## Elements

| id                  | type        | columns                                                            | slot              | size            |
|---------------------|-------------|--------------------------------------------------------------------|-------------------|-----------------|
| kpi-revenue         | kpi         | revenue                                                             | kpi-row           | 1/4 of row      |
| kpi-profit          | kpi         | profit, revenue                                                     | kpi-row           | 1/4 of row      |
| kpi-orders          | kpi         | order_id (distinct count)                                           | kpi-row           | 1/4 of row      |
| kpi-aov             | kpi         | revenue, order_id                                                   | kpi-row           | 1/4 of row      |
| chart-trend         | chart:line  | order_date, revenue, region                                         | chart-main        | fills slot      |
| chart-region-actual | chart:bar   | region, revenue                                                     | chart-stack       | top half of slot|
| chart-region-target | chart:bar   | region, revenue_target                                              | chart-stack       | lower half      |
| chart-category      | chart:bar   | product_category, profit                                            | chart-lower-left  | fills slot      |
| table-customers     | chart:table | customer_name, segment, industry, lifetime_value, nps_score         | chart-lower-right | fills slot      |

Per-element detail:

- **kpi-revenue** — `SUM(revenue)` from `sales_orders.csv`, currency, 0 dp.
- **kpi-profit** — `SUM(profit)`, with profit margin `SUM(profit)/SUM(revenue)` as the
  secondary line.
- **kpi-orders** — `COUNTD(order_id)`.
- **kpi-aov** — `SUM(revenue)/COUNTD(order_id)`, currency.
- **chart-trend** — `SUM(revenue)` by `MONTH(order_date)`, one line per `region`
  (region on colour, brand series palette in order).
- **chart-region-actual** — `SUM(revenue)` by `region`, sorted descending.
- **chart-region-target** — `SUM(revenue_target)` by `region` from `monthly_targets.csv`,
  same region order as `chart-region-actual` so the two read as one comparison.
- **chart-category** — `SUM(profit)` by `product_category`, sorted descending.
- **table-customers** — text table from `customer_segments.csv`, sorted by
  `lifetime_value` descending; `nps_score` averaged (never summed).

## Filters

| id           | field            | control type     | scope                                                                    | default |
|--------------|------------------|------------------|--------------------------------------------------------------------------|---------|
| flt-month    | order_date       | date range (month) | chart-trend, chart-region-actual, chart-category, kpi-revenue, kpi-profit, kpi-orders, kpi-aov | All months |
| flt-region   | region           | dropdown (multi) | chart-trend, chart-region-actual, chart-category, kpi-revenue, kpi-profit, kpi-orders, kpi-aov | All     |
| flt-category | product_category | dropdown (multi) | chart-trend, chart-region-actual, chart-category, kpi-revenue, kpi-profit, kpi-orders, kpi-aov | All     |

All three filter `sales_orders.csv`. `chart-region-target` (targets) and `table-customers`
(customer master) come from other CSVs — separate Tableau data sources — so the filter cards
do not reach them; see Data Gaps.

## Interactions

| id                     | interaction  | source              | target                                       | detail                                                                             |
|------------------------|--------------|---------------------|----------------------------------------------|------------------------------------------------------------------------------------|
| int-region-crossfilter | cross-filter | chart-region-actual | chart-trend, chart-category                  | click a region bar to filter the trend and the category breakdown to that region   |
| int-category-crossfilter | cross-filter | chart-category    | chart-trend, chart-region-actual             | click a category bar to filter the trend and the regional revenue to that category |
| int-region-highlight   | highlight    | chart-region-actual | chart-region-target                          | selecting a region highlights the same region in the target bar, so the shortfall against target reads at a glance |

## Suggestions

1. **Discount impact** — `discount` is captured per order but unused. A scatter of
   `AVG(discount)` against `SUM(profit)` by `product_category` would show whether discounting
   is buying revenue at the cost of margin.
2. **Order-count attainment** — `monthly_targets.orders_target` is loaded but only
   `revenue_target` is plotted. A second target bar for order count would round out the
   attainment picture.
3. **Account-manager view** — `customer_segments.account_manager` supports a book-of-business
   breakdown (lifetime value by manager), useful in the same monthly review.
4. **Tenure vs value** — `customer_since` against `lifetime_value` would show whether the
   long-standing accounts are still the valuable ones.

## Data Gaps

- **Actual-vs-target in a single chart is not available.** Each CSV is one Tableau data
  source and this project does not join them (CONTRACT.md §3.2 — composable data sources are
  Tableau 2026.2+ and out of scope). Actual revenue (`sales_orders.csv`) and target revenue
  (`monthly_targets.csv`) are therefore two adjacent bar charts sharing a region axis, linked
  by `int-region-highlight`, rather than one grouped bar with a variance colour. The same
  limit is why the KPI cards show no target delta.
- **Below-target conditional colour is not available** for the same reason: the comparison
  spans two data sources, so no per-region calculated field can decide "below target". The
  highlight action carries that intent instead.
- **Customer revenue comes from `lifetime_value`, not order revenue.** Segment and NPS live
  in `customer_segments.csv` while order revenue lives in `sales_orders.csv`; ranking
  customers by order revenue *and* showing their segment would need the two joined. The table
  ranks by `lifetime_value` (cumulative revenue per account) instead.
- **`monthly_targets.month` is text (`YYYY-MM`), not a date**, so the month filter cannot be
  applied to the target chart; the target bars show the full-period target.
