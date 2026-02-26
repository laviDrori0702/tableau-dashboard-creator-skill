# Data Source Architecture

## Overview

Three local CSV datasources supporting a regional sales performance dashboard. The data covers January–April 2025 across 3 regions, 7 customers, and 3 product categories. The datasources relate through shared `region` (targets ↔ orders) and `customer_name` (orders ↔ segments) keys.

---

## Datasource 1: sales_orders.csv

**Source**: `sample-data/sales_orders.csv`
**Rows**: 40
**Description**: Individual sales orders with revenue, cost, and profit at the order-line level. Core transactional dataset.

### Fields

| Field | Type | Role | Description | Sample Values | Notes |
|-------|------|------|-------------|---------------|-------|
| order_id | STRING | Dimension | Unique order identifier | ORD-001, ORD-040 | Unique, no nulls |
| order_date | DATE | Dimension | Date the order was placed | 2025-01-03, 2025-04-22 | Range: Jan–Apr 2025 |
| customer_name | STRING | Dimension | Customer company name | Acme Corp, GlobalTech Ltd | 7 distinct values, FK to customer_segments |
| region | STRING | Dimension | Geographic sales region | North America, Europe, Asia Pacific | 3 distinct values, FK to monthly_targets |
| country | STRING | Dimension | Customer's country | United States, Germany, Japan, Sweden, Canada, Australia, United Kingdom | 7 distinct values |
| product_category | STRING | Dimension | Product category grouping | Electronics, Software, Furniture | 3 distinct values |
| product_name | STRING | Dimension | Specific product name | Wireless Headphones, Cloud License Annual, Standing Desk | 10 distinct values |
| quantity | INTEGER | Measure | Units ordered | 1–50 | No nulls |
| unit_price | REAL | Measure | Price per unit before discount | 45.00–1299.99 | USD |
| discount | REAL | Measure | Discount rate applied | 0.00–0.20 | Decimal (0.10 = 10%) |
| revenue | REAL | Measure | Total revenue after discount | 971.89–3799.91 | = quantity × unit_price × (1 - discount) |
| cost | REAL | Measure | Total cost of goods | 480.00–2200.00 | |
| profit | REAL | Measure | Profit = revenue - cost | 491.89–1999.96 | All positive in sample |

### Observations
- Revenue per order ranges from ~$970 to ~$3,800 — healthy spread
- All orders are profitable (no negative margins in sample)
- Discount rates: 0%, 5%, 10%, 15%, 20% — discrete tiers
- Even distribution across regions: ~13–14 orders each
- Monthly order count: 10 (Jan), 10 (Feb), 10 (Mar), 10 (Apr) — evenly distributed
- Derived month (`DATETRUNC('month', [order_date])`) needed for trend analysis

---

## Datasource 2: customer_segments.csv

**Source**: `sample-data/customer_segments.csv`
**Rows**: 7
**Description**: Customer master data with segmentation, industry, and relationship metrics.

### Fields

| Field | Type | Role | Description | Sample Values | Notes |
|-------|------|------|-------------|---------------|-------|
| customer_name | STRING | Dimension | Customer company name | Acme Corp, Thames Analytics | PK, matches sales_orders.customer_name |
| segment | STRING | Dimension | Customer segment tier | Enterprise, Mid-Market | 2 distinct values (4 Enterprise, 3 Mid-Market) |
| industry | STRING | Dimension | Customer industry vertical | Technology, Manufacturing, Finance, Consulting, Media | 5 distinct values |
| company_size | STRING | Dimension | Employee range bracket | 100-200, 200-500, 500-1000, 1000+ | 4 distinct brackets |
| account_manager | STRING | Dimension | Assigned account manager | Sarah Chen, James Miller, Lisa Wang | 3 distinct values |
| customer_since | DATE | Dimension | Account start date | 2020-07-22, 2023-02-28 | Range: mid-2020 to early-2023 |
| lifetime_value | REAL | Measure | Total lifetime revenue | 18200.00–45200.00 | USD |
| nps_score | INTEGER | Measure | Net Promoter Score | 65–85 | Scale 0-100 |

### Observations
- Small reference table — 7 customers, all appear in sales_orders
- NPS scores range 65–85 (all positive promoter range)
- Enterprise segment has higher lifetime values on average
- Could be used for customer detail tables and segment breakdowns

---

## Datasource 3: monthly_targets.csv

**Source**: `sample-data/monthly_targets.csv`
**Rows**: 12
**Description**: Monthly performance targets by region for revenue, orders, and new customer acquisition.

### Fields

| Field | Type | Role | Description | Sample Values | Notes |
|-------|------|------|-------------|---------------|-------|
| month | DATE | Dimension | Target month (first of month) | 2025-01, 2025-04 | Format: YYYY-MM, 4 months |
| region | STRING | Dimension | Geographic sales region | North America, Europe, Asia Pacific | Matches sales_orders.region |
| revenue_target | INTEGER | Measure | Revenue goal for the month/region | 7000–10500 | USD |
| orders_target | INTEGER | Measure | Order count goal | 8–15 | |
| new_customers_target | INTEGER | Measure | New customer acquisition goal | 1–3 | |

### Observations
- Targets increase month-over-month (growth trajectory)
- Europe has the highest revenue targets, Asia Pacific the lowest
- Granularity: month × region (12 rows = 4 months × 3 regions)
- `month` field format (YYYY-MM) will need to be parsed as a date — or joined as string to a derived month from sales_orders

---

## Relationships

```
sales_orders.customer_name ──────── customer_segments.customer_name
    (many-to-one)                       (PK)

sales_orders.region + MONTH(order_date) ──── monthly_targets.region + month
    (many-to-one)                                (composite key)
```

- **Orders ↔ Segments**: Join on `customer_name` — all 7 customers appear in both
- **Orders ↔ Targets**: Join on `region` + month — requires deriving month from `order_date` in sales_orders and matching to `month` in targets. The `month` field in targets is YYYY-MM string format.

## Data Quality Notes

- No null values detected across any datasource
- All revenue/cost/profit values are positive (no returns or credits in sample)
- Discount field is well-bounded (0.00–0.20)
- The `month` field in monthly_targets uses YYYY-MM format — will need type conversion or string matching when joining with order_date
- Sample is small (40 orders) but balanced across dimensions — suitable for mock/demo purposes