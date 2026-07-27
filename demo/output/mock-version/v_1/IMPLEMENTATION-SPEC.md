# Implementation Spec: Sales Performance

> Maps the approved `mock.html` (v_1) to concrete Tableau constructs so `tableau-build`
> never has to guess. The Element Mapping table and the Layout section are the two
> machine-checked handoffs (CONTRACT.md §1.1).
>
> **Note:** this demo mock predates the `data-plan-id` convention, so the ids below are
> the stable plan-style ids for its elements; in a live v2 project each id matches a
> `data-plan-id` attribute in `mock.html` exactly.

**Mock version**: v_1
**Data sources**: sales_orders.csv, monthly_targets.csv, customer_segments.csv

## Element Mapping

| id                      | tableau construct                                                                  | justification |
|-------------------------|------------------------------------------------------------------------------------|---------------|
| flt-date                | Filter card on [order_date] (month dropdown), applies to all sheets                 | -             |
| flt-region              | Filter card on [region] (multi-select dropdown), applies to all sheets              | -             |
| flt-category            | Filter card on [product_category] (multi-select dropdown), applies to all sheets    | -             |
| btn-more-filters        | Show/Hide button toggling pnl-hidden-filters                                        | -             |
| pnl-hidden-filters      | Floating container with show/hide button, holds the extra filter cards              | -             |
| flt-segment             | Filter card on [segment] (dropdown)                                                 | -             |
| flt-country             | Filter card on [country] (dropdown)                                                 | -             |
| kpi-revenue             | Text mark, SUM([revenue]) with [Revenue vs Target %] subtitle, `$#,##0`             | -             |
| kpi-profit              | Text mark, SUM([profit]) with [Profit Margin %] subtitle, `$#,##0` / `0.0%`         | -             |
| kpi-orders              | Text mark, COUNTD([order_id]) with [Orders vs Target %] subtitle, `#,##0`           | -             |
| kpi-aov                 | Text mark, [Avg Order Value], `$#,##0.00`                                           | -             |
| chart-revenue-trend     | Line mark: MONTH([order_date]) x SUM([revenue]), color by [region]                  | -             |
| chart-revenue-vs-target | Side-by-side bar: [region] x Measure Values (SUM([revenue]), SUM([revenue_target])) | -             |
| chart-profit-category   | Horizontal bar: [product_category] x SUM([profit]), color by [product_category]     | -             |
| tbl-top-customers       | Text table: [customer_name], [segment], [nps_score] x SUM([revenue]), sorted desc   | -             |
| int-region-click        | Filter action (Use as Filter): chart-revenue-vs-target -> trend, profit, customers  | -             |
| int-category-click      | Filter action (Use as Filter): chart-profit-category -> trend, customers            | -             |

## Layout

A vertical stack matching the mock: a filter bar on top (three global filter cards, the
"more filters" button, and the collapsible extra-filters container), a row of four KPI
cards, then two equal chart rows (trend + target comparison, category profit + top
customers). Sizes are percentages of the parent along its flow axis, on the mock's
1366 x 768 canvas.

```json
{
  "canvas": {"width": 1366, "height": 768},
  "root": {
    "type": "vert",
    "children": [
      {
        "type": "horz",
        "size": 8,
        "children": [
          {"id": "flt-date", "size": 20},
          {"id": "flt-region", "size": 20},
          {"id": "flt-category", "size": 20},
          {"id": "btn-more-filters", "size": 10},
          {
            "id": "pnl-hidden-filters",
            "type": "horz",
            "size": 30,
            "children": [
              {"id": "flt-segment", "size": 50},
              {"id": "flt-country", "size": 50}
            ]
          }
        ]
      },
      {
        "type": "horz",
        "size": 16,
        "children": [
          {"id": "kpi-revenue", "size": 25},
          {"id": "kpi-profit", "size": 25},
          {"id": "kpi-orders", "size": 25},
          {"id": "kpi-aov", "size": 25}
        ]
      },
      {
        "type": "horz",
        "size": 38,
        "children": [
          {"id": "chart-revenue-trend", "size": 50},
          {"id": "chart-revenue-vs-target", "size": 50}
        ]
      },
      {
        "type": "horz",
        "size": 38,
        "children": [
          {"id": "chart-profit-category", "size": 50},
          {"id": "tbl-top-customers", "size": 50}
        ]
      }
    ]
  }
}
```

## Calculated Fields

| field name           | formula                                                          | used by (element id)    |
|----------------------|------------------------------------------------------------------|-------------------------|
| Revenue vs Target %  | `SUM([revenue]) / SUM([revenue_target])`                          | kpi-revenue             |
| Profit Margin %      | `SUM([profit]) / SUM([revenue])`                                  | kpi-profit              |
| Orders vs Target %   | `COUNTD([order_id]) / SUM([orders_target])`                       | kpi-orders              |
| Avg Order Value      | `SUM([revenue]) / COUNTD([order_id])`                             | kpi-aov                 |
| Below Target         | `SUM([revenue]) < SUM([revenue_target])`                          | chart-revenue-vs-target |

## Data Sources & Joins

| source (csv)          | role    | join / relationship                                        |
|-----------------------|---------|-------------------------------------------------------------|
| sales_orders.csv      | primary | -                                                           |
| monthly_targets.csv   | lookup  | `sales_orders.region = monthly_targets.region` (left)       |
| customer_segments.csv | lookup  | `sales_orders.customer_name = customer_segments.customer_name` (left) |

## Actions

| action name    | type   | source                  | target(s)                                                    | run on |
|----------------|--------|-------------------------|--------------------------------------------------------------|--------|
| Region Click   | Filter | chart-revenue-vs-target | chart-revenue-trend, chart-profit-category, tbl-top-customers | select |
| Category Click | Filter | chart-profit-category   | chart-revenue-trend, tbl-top-customers                        | select |

## Parameters

None
