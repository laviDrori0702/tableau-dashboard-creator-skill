# Implementation Spec: Sales Performance

Maps the approved `mock.html` to concrete Tableau constructs so `tableau-build` never has to
guess.

**Mock version**: v_1
**Data sources**: `sales_orders.csv`, `monthly_targets.csv`, `customer_segments.csv`

## Element Mapping

| id | tableau construct | justification |
|----|-------------------|---------------|
| obj-title | Text object, "Sales Performance", in the top dashboard zone | - |
| flt-month | Filter card (dropdown of checkboxes) on the `Order Month` string field of `sales_orders` | - |
| flt-region | Filter card (dropdown of checkboxes) on `[region]` of `sales_orders` | - |
| flt-category | Filter card (dropdown of checkboxes) on `[product_category]` of `sales_orders` | - |
| int-region-crossfilter | Filter action (Use as Filter), chart-region-actual -> chart-trend, chart-category, run on select | - |
| int-category-crossfilter | Filter action (Use as Filter), chart-category -> chart-trend, chart-region-actual, run on select | - |
| int-region-highlight | Highlight action, chart-region-actual -> chart-region-target, run on select | - |
| kpi-revenue | Text mark, `SUM([revenue])`, format `$#,##0` | - |
| kpi-profit | Text mark, `SUM([profit])`, format `$#,##0` | - |
| kpi-orders | Text mark, `COUNTD([order_id])`, format `#,##0` | - |
| kpi-aov | Text mark, calculated field `Average Order Value`, format `$#,##0` | - |
| chart-trend | Line mark: continuous `MONTH([order_date])` x `SUM([revenue])`, `[region]` on Colour | - |
| chart-region-actual | Bar mark: `[region]` on Rows x `SUM([revenue])` on Columns (horizontal), sorted by revenue descending | - |
| chart-region-target | Bar mark: `[region]` on Rows x `SUM([revenue_target])` on Columns (horizontal), from `monthly_targets` | - |
| chart-category | Bar mark: `[product_category]` x `SUM([profit])`, sorted by profit descending | - |
| table-customers | Text table from `customer_segments`: customer / segment / industry / account manager / NPS on Rows, `SUM([lifetime_value])` on Text, sorted by lifetime value descending | - |

Every construct above is the simplest primitive that does the job: no Dynamic Zone
Visibility, no level-of-detail expression, no table calculation, and no parameter — the two
cross-filters are plain filter actions and the below-target comparison is a highlight action
(see Notes).

## Layout

A single vertical stack: the title bar, the filter-card row, the four KPI cards, then two
chart rows. The upper chart row is the revenue trend beside a vertical pair of region bar
charts (actual above target, so the two share a region axis and read as one comparison); the
lower row is the category breakdown beside the customer table. Percentages are each zone's
share of its parent, taken from the mock's pixel geometry at its 1366 x 900 canvas.

```json
{
  "canvas": {"width": 1366, "height": 900},
  "root": {
    "type": "vert",
    "children": [
      {"id": "obj-title", "size": 7},
      {
        "type": "horz",
        "size": 7,
        "children": [
          {"id": "flt-month", "size": 34},
          {"id": "flt-region", "size": 33},
          {"id": "flt-category", "size": 33}
        ]
      },
      {
        "type": "horz",
        "size": 14,
        "children": [
          {"id": "kpi-revenue", "size": 25},
          {"id": "kpi-profit", "size": 25},
          {"id": "kpi-orders", "size": 25},
          {"id": "kpi-aov", "size": 25}
        ]
      },
      {
        "type": "horz",
        "size": 40,
        "children": [
          {"id": "chart-trend", "size": 60},
          {
            "type": "vert",
            "size": 40,
            "children": [
              {"id": "chart-region-actual", "size": 50},
              {"id": "chart-region-target", "size": 50}
            ]
          }
        ]
      },
      {
        "type": "horz",
        "size": 32,
        "children": [
          {"id": "chart-category", "size": 40},
          {"id": "table-customers", "size": 60}
        ]
      }
    ]
  }
}
```

## Calculated Fields

| field name | formula | used by (element id) |
|------------|---------|----------------------|
| Average Order Value | `SUM([revenue]) / COUNTD([order_id])` | kpi-aov |
| Order Month | `STR(YEAR([order_date])) + "-" + IF MONTH([order_date]) < 10 THEN "0" ELSE "" END + STR(MONTH([order_date]))` | flt-month |

`Order Month` exists because a filter card lists a field's *members*, and `order_date` is a
date. Formatting the month as a `YYYY-MM` string gives the month selector the mock shows,
and matches how `monthly_targets.month` is already stored.

## Data Sources & Joins

| source (csv) | role | join / relationship |
|--------------|------|---------------------|
| `sales_orders.csv` | primary — KPIs, trend, regional revenue, category profit | - |
| `monthly_targets.csv` | independent source — the regional target bars | none (see Notes) |
| `customer_segments.csv` | independent source — the customer table | none (see Notes) |

Each CSV is one Tableau data source (CONTRACT.md §3.2) and none are joined, so every
worksheet reads exactly one source. Replace Data Source swaps each independently.

## Actions

| action name | type | source | target(s) | run on |
|-------------|------|--------|-----------|--------|
| Region cross-filter | Filter | chart-region-actual | chart-trend, chart-category | select |
| Category cross-filter | Filter | chart-category | chart-trend, chart-region-actual | select |
| Region highlight | Highlight | chart-region-actual | chart-region-target | select |

## Parameters

None.

## Notes

- **The two region charts are separate sheets on purpose.** Actual revenue and target
  revenue live in different CSVs, so a single grouped bar (or a below-target colour rule)
  would need the two joined — out of scope per `DASHBOARD-PLAN.md` → Data Gaps. The
  highlight action carries that comparison instead: selecting a region in the actual chart
  dims the other regions in the target chart, so the pair reads as one actual-vs-target view.
- **The filter cards are declared against `chart-trend`.** A card is the UI for one
  worksheet's filter; extending it to the other `sales_orders` sheets is *Apply to Worksheets
  → All Using This Data Source* in Desktop, which is not a manifest-level choice.
  `chart-region-target` and `table-customers` read other sources and are unaffected, exactly
  as the mock shows.
- **The KPI cards show one number each.** A text mark carries a single text encoding, so the
  mock's secondary lines (profit margin under Total profit, the in-scope order count under
  Total revenue) are demo affordances rather than workbook content, as are the coloured
  accent bars along the top of each card.
- **Only `chart-trend` has a dimension on Colour**, so it is the one view that walks the
  brand's `### Chart series colors` in order (region on Colour, with a generated legend). The
  three bar charts and the four KPI cards have nothing on Colour and therefore render flat in
  the brand's first colour, `#1b4f72` — which is what the mock draws too.
- **The customer table is not sortable by the viewer.** Column-header sorting in the mock is
  an HTML affordance; the Tableau table ships sorted by lifetime value descending, and a
  viewer re-sorts with Tableau's own column sort control.
