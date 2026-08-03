# Data Model

> Managed by tableau-dashboard-plugin (tableau-data). See CONTRACT.md before hand-editing.

## Acquisition

- tier: csv (provided in data/)
- Each CSV under `data/` is one data source (CONTRACT.md §3.2). Documented field names below must match the CSV headers exactly so **Replace Data Source** can swap in live data later.

## Data source: `customer_segments.csv`

- rows profiled: 7

| Field | Type | Role | Sample values | Description |
|-------|------|------|---------------|-------------|
| customer_name | string | Dimension | Acme Corp, GlobalTech Ltd, Sakura Inc | Customer account name. Joins to `sales_orders.customer_name`; the grain of this table (one row per customer). |
| segment | string | Dimension | Enterprise, Mid-Market | Commercial segment the account is managed under. |
| industry | string | Dimension | Technology, Manufacturing, Consulting | Industry vertical of the customer. |
| company_size | string | Dimension | 500-1000, 1000+, 200-500 | Employee-count band, as a text bucket (not numeric). |
| account_manager | string | Dimension | Sarah Chen, James Miller, Lisa Wang | Salesperson who owns the relationship. |
| customer_since | date | Dimension | 2021-03-15, 2020-07-22, 2022-01-10 | Date the account was first won; use for tenure. |
| lifetime_value | real | Measure | 45200.00, 38500.00, 28900.00 | Cumulative revenue attributed to the account to date, in USD. |
| nps_score | integer | Measure | 72, 65, 81 | Latest Net Promoter Score for the account (0-100). Average it across customers; never sum. |

## Data source: `monthly_targets.csv`

- rows profiled: 12

| Field | Type | Role | Sample values | Description |
|-------|------|------|---------------|-------------|
| month | string | Dimension | 2025-01, 2025-02, 2025-03 | Target month as `YYYY-MM` text, not a date. Align with `MONTH(order_date)` when comparing to actuals. |
| region | string | Dimension | North America, Europe, Asia Pacific | Sales region the target applies to. Shares its domain with `sales_orders.region`. |
| revenue_target | integer | Measure | 8000, 9000, 7000 | Revenue goal for the region-month, in USD. Denominator of the below-target test. |
| orders_target | integer | Measure | 10, 12, 8 | Order-count goal for the region-month. |
| new_customers_target | integer | Measure | 2, 3, 1 | New-logo goal for the region-month. Not used by this dashboard. |

## Data source: `sales_orders.csv`

- rows profiled: 40

| Field | Type | Role | Sample values | Description |
|-------|------|------|---------------|-------------|
| order_id | string | Dimension | ORD-001, ORD-002, ORD-003 | Unique order identifier and the grain of this table. `COUNTD(order_id)` is the order count KPI. |
| order_date | date | Dimension | 2025-01-03, 2025-01-05, 2025-01-07 | Date the order was placed; the dashboard's time axis (truncate to month for the trend). |
| customer_name | string | Dimension | Acme Corp, GlobalTech Ltd, Sakura Inc | Ordering account. Joins to `customer_segments.customer_name` for segment and NPS. |
| region | string | Dimension | North America, Europe, Asia Pacific | Sales region credited with the order. Matches `monthly_targets.region`. |
| country | string | Dimension | United States, Germany, Japan | Country of the customer; nests under region. |
| product_category | string | Dimension | Electronics, Software, Furniture | Product line the item belongs to; the profit-breakdown dimension. |
| product_name | string | Dimension | Wireless Headphones, Cloud License Annual, USB-C Hub | Specific product ordered; nests under category. |
| quantity | integer | Measure | 12, 5, 30 | Units ordered. |
| unit_price | real | Measure | 89.99, 299.99, 45.00 | List price per unit, in USD, before discount. Average it; never sum. |
| discount | real | Measure | 0.10, 0.00, 0.15 | Discount applied to the order, as a fraction of list (0.10 = 10%). Average it; never sum. |
| revenue | real | Measure | 971.89, 1499.95, 1147.50 | Net revenue for the order after discount, in USD. The dashboard's headline measure. |
| cost | real | Measure | 480.00, 750.00, 600.00 | Cost of goods for the order, in USD. |
| profit | real | Measure | 491.89, 749.95, 547.50 | `revenue - cost`, in USD. Margin is `SUM(profit) / SUM(revenue)`. |
