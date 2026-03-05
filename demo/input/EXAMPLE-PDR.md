# Sales Performance Dashboard - Product Dashboard Request (Demo)

## Overview
A regional sales performance dashboard designed for the VP of Sales and regional managers. The dashboard provides a monthly overview of revenue, profit, and order KPIs across three regions (North America, Europe, Asia Pacific), with the ability to drill down by region, product category, and customer segment. Data is updated monthly and sourced from `sales_orders.csv`, `customer_segments.csv`, and `monthly_targets.csv`.

## KPIs
1. **Total Revenue** - Sum of `revenue` from `sales_orders.csv`, displayed alongside the `revenue_target` from `monthly_targets.csv` with a delta indicator (above/below target)
2. **Total Profit** - Sum of `profit` from `sales_orders.csv`, with profit margin percentage calculated as `SUM(profit) / SUM(revenue) * 100`
3. **Total Orders** - Count of `order_id` from `sales_orders.csv`, compared to `orders_target` from `monthly_targets.csv`
4. **Average Order Value** - Calculated as `SUM(revenue) / COUNT(order_id)` from `sales_orders.csv`
5. **Top Customer by Revenue** - Customer with highest total `revenue`, showing their `segment` and `nps_score` from `customer_segments.csv`

## Visualizations
1. **Revenue Trend** - Line chart showing monthly `revenue` (aggregated from `sales_orders.csv` by `order_date` month) over time, with separate lines per `region`. Overlay `revenue_target` from `monthly_targets.csv` as a dashed reference line per region.
2. **Revenue vs Target by Region** - Grouped bar chart comparing actual `SUM(revenue)` against `revenue_target` per `region`. Bars for regions below target should be highlighted in a warning color.
3. **Profit by Product Category** - Horizontal bar chart showing `SUM(profit)` from `sales_orders.csv` grouped by `product_category` (Electronics, Software, Furniture). Sorted descending by profit.
4. **Top Customers Table** - Table listing each `customer_name` with columns: total `revenue`, total `profit`, `segment`, `industry`, and `nps_score` (joined from `customer_segments.csv`). Sortable by any column, top 10 by default.

## Filters
- **Date range** - Month selector filtering `order_date` from `sales_orders.csv` and `month` from `monthly_targets.csv`
- **Region** - Dropdown filtering `region` across all three data sources
- **Product Category** - Dropdown filtering `product_category` from `sales_orders.csv`
- **Customer Segment** - Dropdown filtering `segment` from `customer_segments.csv` (Enterprise, Mid-Market)

## Additional Notes
- Highlight regions below their `revenue_target` using a contrasting color (e.g., red/orange bar vs. the primary blue)
- The Top Customers table should be sortable by clicking column headers
- KPI cards should display a delta arrow (up/down) and percentage change when comparing actual vs. target
- Keep the design clean and professional, using the branding colors defined in `branding/branding.md`
- All monetary values should be formatted with currency symbol and thousand separators (e.g., $12,345.67)
