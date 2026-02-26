# Tableau Implementation: Sales Performance

**Template**: Frame With Main KPI (2*2)
**Datasources**: sales_orders.csv, customer_segments.csv, monthly_targets.csv
**Mock version**: v_1

---

## Section 1: Container Hierarchy

### Container Tree

```
Root Container (layout-basic, 100% x 100%)
├── Content (Vertical, flex)
│   ├── Top Banner (Tiled, fixed-height: 65)
│   │   ├── Logo (Image, 195x50, padding: 7, padding-left: 12) — placeholder
│   │   ├── Spacer (Blank, flex)
│   │   ├── Update Time (Text, fixed-width: 305)
│   │   │   └── "Last updated: [month] [year]" (Open Sans, 11px, #5D6D7E)
│   │   └── Info Icon (Text, fixed-width: 34, margin: 4)
│   ├── Dashboard Title (Horizontal, fixed-height: 70)
│   │   └── "Sales Performance" (Open Sans, 36px, bold, #1C2833, bg: #F4F6F7)
│   ├── Filter Bar (Horizontal, fixed-height: 53, margin-top: 11, margin-bottom: 11)
│   │   ├── "Filters" Label (Text, fixed-width: 185, margin: 4)
│   │   │   └── "Filters" (Open Sans, 12px, bold, #5D6D7E)
│   │   ├── Date Filter (Filter, type: relative-date, margin: 4)
│   │   ├── Region Filter (Filter, type: single-value-dropdown, margin: 4)
│   │   ├── Category Filter (Filter, type: single-value-dropdown, margin: 4)
│   │   └── Expand Button (Button, type: toggle, fixed-width: 38, margin: 4)
│   └── Main Area (Horizontal, flex)
│       ├── Charts Area (Vertical, ~86% width)
│       │   ├── KPI Row (Horizontal, distribute-evenly, fixed-height: 94)
│       │   │   ├── KPI 1: Total Revenue (Vertical, bg: #FFFFFF)
│       │   │   │   ├── Accent Bar (Blank, 3px, bg: #1B4F72)
│       │   │   │   └── Sheet: KPI_TotalRevenue
│       │   │   ├── KPI 2: Total Profit (Vertical, bg: #FFFFFF)
│       │   │   │   ├── Accent Bar (Blank, 3px, bg: #2E86C1)
│       │   │   │   └── Sheet: KPI_TotalProfit
│       │   │   ├── KPI 3: Total Orders (Vertical, bg: #FFFFFF)
│       │   │   │   ├── Accent Bar (Blank, 3px, bg: #48C9B0)
│       │   │   │   └── Sheet: KPI_TotalOrders
│       │   │   └── KPI 4: Avg Order Value (Vertical, bg: #FFFFFF)
│       │   │       ├── Accent Bar (Blank, 3px, bg: #F39C12)
│       │   │       └── Sheet: KPI_AvgOrderValue
│       │   ├── Chart Row 1 (Horizontal, distribute-evenly, margin-top: 11, flex)
│       │   │   ├── Chart 1 Container (Vertical, padding: 8, bg: #FFFFFF)
│       │   │   │   ├── Title Bar (Horizontal, fixed-height: 46)
│       │   │   │   │   ├── Icon (Image, fixed-width: 47)
│       │   │   │   │   ├── "Revenue Trend" (Text, 15px, #1C2833, margin-left: 10)
│       │   │   │   │   └── Info Icon (Text, fixed-width: 38, margin: 4)
│       │   │   │   ├── Separator (Blank, 3px, bg: #E5E8E8, margin: 0 10px)
│       │   │   │   └── Sheet: Chart_RevenueTrend (flex)
│       │   │   └── Chart 2 Container (Vertical, padding: 8, bg: #FFFFFF)
│       │   │       ├── Title Bar (Horizontal, fixed-height: 46)
│       │   │       │   ├── Icon (Image, fixed-width: 47)
│       │   │       │   ├── "Revenue vs Target by Region" (Text, 15px, #1C2833, margin-left: 10)
│       │   │       │   └── Info Icon (Text, fixed-width: 38, margin: 4)
│       │   │       ├── Separator (Blank, 3px, bg: #E5E8E8, margin: 0 10px)
│       │   │       └── Sheet: Chart_RevenueVsTarget (flex)
│       │   └── Chart Row 2 (Horizontal, distribute-evenly, margin-top: 11, flex)
│       │       ├── Chart 3 Container (Vertical, padding: 8, bg: #FFFFFF)
│       │       │   ├── Title Bar (Horizontal, fixed-height: 46)
│       │       │   │   ├── Icon (Image, fixed-width: 47)
│       │       │   │   ├── "Profit by Product Category" (Text, 15px, #1C2833, margin-left: 10)
│       │       │   │   └── Info Icon (Text, fixed-width: 38, margin: 4)
│       │       │   ├── Separator (Blank, 3px, bg: #E5E8E8, margin: 0 10px)
│       │       │   └── Sheet: Chart_ProfitByCategory (flex)
│       │       └── Chart 4 Container (Vertical, padding: 8, bg: #FFFFFF)
│       │           ├── Title Bar (Horizontal, fixed-height: 46)
│       │           │   ├── Icon (Image, fixed-width: 47)
│       │           │   ├── "Top Customers" (Text, 15px, #1C2833, margin-left: 10)
│       │           │   └── Info Icon (Text, fixed-width: 38, margin: 4)
│       │           ├── Separator (Blank, 3px, bg: #E5E8E8, margin: 0 10px)
│       │           └── Sheet: Chart_TopCustomers (flex)
│       └── Hidden Filters Panel (Vertical, ~14% width, collapsible via DZV)
│           ├── Sheet: Filter_CustomerSegment
│           └── Sheet: Filter_Country
```

### Container Details

| Container Name | Type | Direction | Size | Background | Padding | Margin |
|---------------|------|-----------|------|------------|---------|--------|
| Root Container | layout-basic | — | 100% x 100% | #F4F6F7 | 0 | 0 |
| Content | layout-flow | Vertical | flex | transparent | 0 | ~1.25% sides |
| Top Banner | Tiled | Horizontal | fixed 65px | #F4F6F7 | 7px (left:12) | 0 |
| Dashboard Title | layout-flow | Horizontal | fixed 70px | #F4F6F7 | 4px (bottom:0) | bottom:1 |
| Filter Bar | layout-flow | Horizontal | fixed 53px | transparent | 0 4px | top:11, bottom:11 |
| Main Area | layout-flow | Horizontal | flex | transparent | 0 | 0 |
| Charts Area | layout-flow | Vertical | ~86% | transparent | 0 | 0 |
| KPI Row | layout-flow | Horizontal | fixed 94px | transparent | 0 | 0 |
| KPI Card | layout-flow | Vertical | distribute-evenly | #FFFFFF | 0 | right:16 |
| Chart Row 1 | layout-flow | Horizontal | flex | transparent | 0 | top:11 |
| Chart Row 2 | layout-flow | Horizontal | flex | transparent | 0 | top:11 |
| Chart Container | layout-flow | Vertical | distribute-evenly | #FFFFFF | 8px | 0 |
| Hidden Filters | layout-flow | Vertical | ~14% | #FFFFFF | 12px | 0 |

---

## Section 2: Sheets

### Sheet: KPI_TotalRevenue

**Container**: KPI Row > KPI 1
**Size**: flex (distribute-evenly within KPI row)
**Title**: hidden

#### Marks
- **Mark type**: Text
- **Columns shelf**: (empty)
- **Rows shelf**: (empty)
- **Label**: AGG([Total Revenue]) formatted as `$#,##0`
- **Detail**: AGG([Revenue % of Target])
- **Tooltip**: "Total Revenue: <SUM(revenue)>\n<Revenue % of Target>% of target"

#### Calculated Fields

| Field Name | Formula | Purpose |
|-----------|---------|---------|
| Total Revenue | `SUM([revenue])` | Primary KPI metric |
| Total Revenue Target | `SUM([revenue_target])` | From monthly_targets datasource |
| Revenue % of Target | `SUM([revenue]) / SUM([revenue_target]) * 100` | Comparison percentage |
| Revenue Target Delta | `IF [Revenue % of Target] >= 100 THEN "▲" ELSE "▼" END + " " + STR(ROUND([Revenue % of Target], 1)) + "% of target"` | Display string |

#### Formatting
- **Font**: Open Sans, 26px bold for value, 11px for label and comparison
- **Number format**: $#,##0
- **Axis**: hidden
- **Grid lines**: hidden

---

### Sheet: KPI_TotalProfit

**Container**: KPI Row > KPI 2
**Size**: flex
**Title**: hidden

#### Marks
- **Mark type**: Text
- **Label**: AGG([Total Profit]) formatted as `$#,##0`
- **Detail**: AGG([Profit Margin %])

#### Calculated Fields

| Field Name | Formula | Purpose |
|-----------|---------|---------|
| Total Profit | `SUM([profit])` | Primary KPI metric |
| Profit Margin % | `SUM([profit]) / SUM([revenue]) * 100` | Margin percentage |

#### Formatting
- **Font**: Open Sans, 26px bold for value, 11px for comparison
- **Number format**: $#,##0 for profit, 0.0% for margin

---

### Sheet: KPI_TotalOrders

**Container**: KPI Row > KPI 3
**Size**: flex
**Title**: hidden

#### Marks
- **Mark type**: Text
- **Label**: AGG([Total Orders])
- **Detail**: AGG([Orders % of Target])

#### Calculated Fields

| Field Name | Formula | Purpose |
|-----------|---------|---------|
| Total Orders | `COUNTD([order_id])` | Primary KPI metric |
| Total Orders Target | `SUM([orders_target])` | From monthly_targets |
| Orders % of Target | `COUNTD([order_id]) / SUM([orders_target]) * 100` | Comparison percentage |

#### Formatting
- **Font**: Open Sans, 26px bold for value
- **Number format**: #,##0

---

### Sheet: KPI_AvgOrderValue

**Container**: KPI Row > KPI 4
**Size**: flex
**Title**: hidden

#### Marks
- **Mark type**: Text
- **Label**: AGG([Avg Order Value])

#### Calculated Fields

| Field Name | Formula | Purpose |
|-----------|---------|---------|
| Avg Order Value | `SUM([revenue]) / COUNTD([order_id])` | Revenue per order |

#### Formatting
- **Font**: Open Sans, 26px bold for value
- **Number format**: $#,##0.00

---

### Sheet: Chart_RevenueTrend

**Container**: Chart Row 1 > Chart 1 Container
**Size**: flex
**Title**: hidden (title is in the container text zone)

#### Marks
- **Mark type**: Line
- **Columns shelf**: MONTH([order_date])
- **Rows shelf**: SUM([revenue])
- **Color**: [region] — 3 values:
  - North America: #1B4F72
  - Europe: #2E86C1
  - Asia Pacific: #48C9B0
- **Label**: (none)
- **Detail**: (none)
- **Tooltip**: "Region: <region>\nMonth: <MONTH(order_date)>\nRevenue: <SUM(revenue)>"

#### Formatting
- **Font**: Open Sans, 11px
- **Number format**: $#,##0 for y-axis
- **Axis**: x-axis visible (month labels), y-axis visible ($k format)
- **Grid lines**: y-axis only, color #E5E8E8
- **Line width**: 2px
- **Point markers**: visible, radius 4

---

### Sheet: Chart_RevenueVsTarget

**Container**: Chart Row 1 > Chart 2 Container
**Size**: flex
**Title**: hidden

#### Marks
- **Mark type**: Bar (horizontal)
- **Columns shelf**: Measure Values (SUM([revenue]), SUM([revenue_target]))
- **Rows shelf**: [region]
- **Color**: Measure Names
  - SUM([revenue]): #1B4F72 (or #E74C3C if below target)
  - SUM([revenue_target]): #E5E8E8
- **Label**: SUM values on bars
- **Tooltip**: "Region: <region>\nActual: <SUM(revenue)>\nTarget: <SUM(revenue_target)>"

#### Calculated Fields

| Field Name | Formula | Purpose |
|-----------|---------|---------|
| Below Target Flag | `IF SUM([revenue]) < SUM([revenue_target]) THEN "Below" ELSE "On Track" END` | Conditional coloring |

#### Formatting
- **Font**: Open Sans, 11px
- **Number format**: $#,##0
- **Axis**: x-axis visible ($k format), y-axis (region labels)
- **Grid lines**: x-axis only, #E5E8E8

---

### Sheet: Chart_ProfitByCategory

**Container**: Chart Row 2 > Chart 3 Container
**Size**: flex
**Title**: hidden

#### Marks
- **Mark type**: Bar (horizontal)
- **Columns shelf**: SUM([profit])
- **Rows shelf**: [product_category]
- **Color**: [product_category]
  - Electronics: #1B4F72
  - Software: #2E86C1
  - Furniture: #48C9B0
- **Label**: SUM([profit]) on bars
- **Tooltip**: "Category: <product_category>\nProfit: <SUM(profit)>"

#### Formatting
- **Font**: Open Sans, 11px
- **Number format**: $#,##0
- **Axis**: x-axis visible ($k format), y-axis (category labels)
- **Grid lines**: x-axis only, #E5E8E8

---

### Sheet: Chart_TopCustomers

**Container**: Chart Row 2 > Chart 4 Container
**Size**: flex
**Title**: hidden

#### Marks
- **Mark type**: Text (crosstab/table)
- **Columns shelf**: (none — use Measure Values)
- **Rows shelf**: [customer_name]
- **Label**: [customer_name], [segment], SUM([revenue]), AVG([nps_score])
- **Detail**: [segment], [nps_score]
- **Tooltip**: "Customer: <customer_name>\nSegment: <segment>\nRevenue: <SUM(revenue)>\nNPS: <nps_score>"

#### Data Source
- Requires join: `sales_orders.customer_name = customer_segments.customer_name`
- Pull [segment] and [nps_score] from customer_segments

#### Formatting
- **Font**: Open Sans, 12px
- **Number format**: $#,##0 for revenue, #,##0 for NPS
- **Sort**: [revenue] descending (default)
- **Header alignment**: left
- **Row banding**: alternate row shading #F4F6F7

---

### Sheet: Filter_CustomerSegment

**Container**: Hidden Filters Panel
**Title**: "Customer Segment"
**Filter type**: Single-value dropdown on [segment] from customer_segments

### Sheet: Filter_Country

**Container**: Hidden Filters Panel
**Title**: "Country"
**Filter type**: Single-value dropdown on [country] from sales_orders

---

## Section 3: Parameters

No parameters required for this dashboard.

---

## Dashboard Actions

| Action Name | Type | Source Sheet | Target Sheet(s) | Run On | Fields |
|------------|------|-------------|-----------------|--------|--------|
| Region Filter Action | Filter | Chart_RevenueVsTarget | Chart_RevenueTrend, Chart_ProfitByCategory, Chart_TopCustomers | Select | region = region |
| Category Filter Action | Filter | Chart_ProfitByCategory | Chart_RevenueTrend, Chart_TopCustomers | Select | product_category = product_category |
| Region Highlight | Highlight | Chart_RevenueTrend | Chart_RevenueVsTarget | Hover | region = region |

---

## Datasource Definitions

### Datasource: Sales Orders
- **File**: `sample-data/sales_orders.csv`
- **Connection**: textscan (CSV)
- **Fields used**: order_id, order_date, customer_name, region, country, product_category, product_name, quantity, unit_price, discount, revenue, cost, profit

### Datasource: Customer Segments
- **File**: `sample-data/customer_segments.csv`
- **Connection**: textscan (CSV)
- **Join**: customer_name = customer_name (to Sales Orders)
- **Fields used**: customer_name, segment, nps_score

### Datasource: Monthly Targets
- **File**: `sample-data/monthly_targets.csv`
- **Connection**: textscan (CSV)
- **Join**: region = region AND month = DATETRUNC('month', [order_date])
- **Fields used**: month, region, revenue_target, orders_target

---

## Notes

- The date filter should use relative-date or month-level filtering on [order_date]
- The "Below Target" conditional coloring on Chart_RevenueVsTarget requires a calculated field that compares actual vs target at the region level
- The hidden filters panel uses Tableau's DZV (Dashboard Zone Visibility) toggle pattern — the expand button shows/hides the panel
- KPI comparison values require calculated fields with conditional logic
- The monthly_targets `month` field (YYYY-MM string) needs a date conversion or string-based join with DATETRUNC'd order_date