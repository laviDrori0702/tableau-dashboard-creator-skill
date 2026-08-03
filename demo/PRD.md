# Sales Performance Dashboard — PRD

## Overview

A regional sales performance dashboard for the **VP of Sales and regional managers**.
It gives a quick read on revenue, profit, and order volume, then lets a manager drill
into the detail by region, product category, and customer segment to see who is
carrying the number and who is behind. Data is **refreshed monthly**, so the dashboard
is a monthly review instrument rather than an operational live view.

Decisions it supports: where to redirect sales effort next month, which regions need
intervention against target, which product categories carry margin, and which accounts
are worth protecting.

## KPIs

1. **Total Revenue** — `SUM(revenue)` across the filtered scope, shown against the
   summed `revenue_target` for the same scope (variance to target in % as the secondary
   figure).
2. **Total Profit** — `SUM(profit)`, with **profit margin** `SUM(profit) / SUM(revenue)`
   as the secondary figure.
3. **Total Orders** — `COUNTD(order_id)`, shown against the summed `orders_target`.
4. **Average Order Value** — `SUM(revenue) / COUNTD(order_id)`.

All four respond to the dashboard filters.

## Visualizations

1. **Revenue Trend** — line chart of `SUM(revenue)` by month (`order_date` truncated to
   month), one line per **region**; chronological x-axis, region on colour.
2. **Revenue vs Target by Region** — bar chart of `SUM(revenue)` by **region** with the
   region's `revenue_target` as a reference marker; regions **below target are coloured
   distinctly** so a shortfall is visible without reading the axis. Sorted by revenue
   descending.
3. **Profit by Product Category** — bar chart of `SUM(profit)` by **product_category**,
   sorted descending, so the margin contributors rank themselves.
4. **Top Customers** — table of the highest-revenue customers with `SUM(revenue)`,
   their **segment** and **nps_score**; **sortable** by any column, default sort revenue
   descending.

## Filters

- **Date range (month)** — month selector over `order_date`; the primary time slice.
- **Region** — dropdown over `region`.
- **Product category** — dropdown over `product_category`.

## Additional Notes

- **Below-target highlighting.** Regions under their revenue target must be visually
  distinct in the Revenue vs Target view (a different colour, not just a shorter bar).
- **Cross-filtering.** Selecting a region, category, or month in one view should filter
  the rest of the dashboard, so the KPI row always reflects the current selection.
- **Sortable customer table.** The Top Customers table sorts on any column.
- **Clean, professional design** — restrained palette, no decorative chrome; the
  organisation's branding tokens govern colour and typography.
