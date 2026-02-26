# Dashboard Plan: Sales Performance

## Dashboard Summary

A regional sales performance dashboard for the VP of Sales and regional managers. Provides a quick overview of revenue, profit, and order KPIs at the top, with drill-down capabilities by region, product category, and customer segment. Updated monthly. Designed for operational review meetings and executive reporting.

## Recommended Layout

**Frame With Main KPI (2*2)**: KPI row (4 cards) + 2 rows of 2 charts each.

Justification: The PDR requests 4 KPIs and 4 visualizations — this maps perfectly to a KPI row + 2×2 chart grid. The layout provides equal visual weight to each chart while keeping KPIs prominent at the top.

---

## KPIs

### KPI 1: Total Revenue
- **Metric**: SUM([revenue]) from sales_orders
- **Source columns**: sales_orders.revenue
- **Comparison**: vs SUM([revenue_target]) from monthly_targets — show % of target
- **Format**: $#,##0 (currency, no decimals)
- **Accent color**: #1B4F72 (Accent 1)

### KPI 2: Total Profit
- **Metric**: SUM([profit]) from sales_orders
- **Source columns**: sales_orders.profit
- **Comparison**: Profit margin % = SUM([profit]) / SUM([revenue])
- **Format**: $#,##0 for profit, 0.0% for margin
- **Accent color**: #2E86C1 (Accent 2)

### KPI 3: Total Orders
- **Metric**: COUNTD([order_id]) from sales_orders
- **Source columns**: sales_orders.order_id
- **Comparison**: vs SUM([orders_target]) from monthly_targets — show % of target
- **Format**: #,##0
- **Accent color**: #48C9B0 (Accent 3)

### KPI 4: Average Order Value
- **Metric**: SUM([revenue]) / COUNTD([order_id])
- **Source columns**: sales_orders.revenue, sales_orders.order_id
- **Comparison**: none (standalone metric)
- **Format**: $#,##0.00
- **Accent color**: #F39C12 (Accent 4)

---

## Charts

### Chart 1: Revenue Trend (Top-left)
- **Type**: Line chart
- **Purpose**: How is revenue trending month-over-month? Are there seasonal patterns or growth?
- **Dimensions**: MONTH([order_date]) on Columns
- **Measures**: SUM([revenue]) on Rows
- **Color**: [region] — one line per region using chart series colors (#1B4F72, #2E86C1, #48C9B0)
- **Source datasource**: sales_orders.csv
- **Suggested filters**: Date range, Region
- **Icon suggestion**: trending-up line icon

### Chart 2: Revenue vs Target by Region (Top-right)
- **Type**: Side-by-side bar chart
- **Purpose**: Which regions are meeting their revenue targets and which are falling short?
- **Dimensions**: [region] on Rows
- **Measures**: SUM([revenue]) and SUM([revenue_target]) on Columns (dual measures, side-by-side)
- **Color**: Measure Names — actual revenue in #1B4F72, target in #E5E8E8 (lighter/muted)
- **Source datasource**: sales_orders.csv joined with monthly_targets.csv on region
- **Suggested filters**: Date range
- **Conditional formatting**: Highlight regions where actual < target in #E74C3C (red) — per PDR requirement
- **Icon suggestion**: bar chart comparison icon

### Chart 3: Profit by Product Category (Bottom-left)
- **Type**: Bar chart (horizontal)
- **Purpose**: Which product categories drive the most profit?
- **Dimensions**: [product_category] on Rows
- **Measures**: SUM([profit]) on Columns
- **Color**: [product_category] using chart series colors (#1B4F72, #2E86C1, #48C9B0)
- **Source datasource**: sales_orders.csv
- **Suggested filters**: Date range, Region
- **Icon suggestion**: stacked layers icon

### Chart 4: Top Customers (Bottom-right)
- **Type**: Table / crosstab
- **Purpose**: Who are the top customers by revenue, and what's their segment and satisfaction?
- **Dimensions**: [customer_name], [segment], [nps_score] — sorted by revenue descending
- **Measures**: SUM([revenue]) per customer
- **Source datasource**: sales_orders.csv joined with customer_segments.csv on customer_name
- **Suggested filters**: Date range, Region, Product Category
- **Sortable**: Yes — per PDR requirement, users should be able to sort by any column
- **Icon suggestion**: users/people icon

---

## Filters

### Global Filters (Top Filter Bar)

| Filter | Type | Source Column | Default Value |
|--------|------|--------------|---------------|
| Date Range | Date range (month selector) | sales_orders.order_date | All months (Jan–Apr 2025) |
| Region | Dropdown (multi-select) | sales_orders.region | All |
| Product Category | Dropdown (multi-select) | sales_orders.product_category | All |

### Dashboard Action Filters

| Action | Source Sheet | Target Sheet(s) | Field Mapping |
|--------|-------------|-----------------|---------------|
| Region Click | Revenue vs Target by Region | Revenue Trend, Profit by Category, Top Customers | region = region |
| Category Click | Profit by Product Category | Revenue Trend, Top Customers | product_category = product_category |

### Hidden Filters (Collapsible Panel)

| Filter | Type | Source Column | Default Value |
|--------|------|--------------|---------------|
| Customer Segment | Dropdown | customer_segments.segment | All |
| Country | Dropdown | sales_orders.country | All |

---

## Additional Suggestions

1. **Revenue vs Target % KPI delta**: Already included as comparison in KPI 1 and KPI 3. Could add conditional coloring — green when above target, red when below.

2. **Discount Impact Analysis**: The data has a `discount` field — a chart showing average discount by category or region could reveal margin pressure. Not requested in PDR, but valuable for the VP of Sales.

3. **Customer Segment Revenue Split**: A small donut or stacked bar showing Enterprise vs Mid-Market revenue contribution. Available from the data but not requested.

---

## Data Gaps

- **Time period**: Only 4 months of data — year-over-year comparisons are not possible
- **New customer tracking**: monthly_targets has `new_customers_target` but sales_orders doesn't have a "new customer" flag — this KPI cannot be tracked with current data
- **Individual sales rep performance**: No sales rep field in orders — only account_manager in customer_segments (not 1:1 with order ownership)