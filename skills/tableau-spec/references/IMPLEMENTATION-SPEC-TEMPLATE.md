# Implementation Spec: <dashboard name>

> Maps the approved `mock.html` to concrete Tableau constructs so `tableau-build` never has
> to guess. **The Element Mapping table and the Layout section below are required and
> machine-checked**: every mock element (every `data-plan-id`) must have exactly one mapping
> row, any escalation to an advanced feature must carry a justification, and the Layout's
> container tree must place every mapped zone exactly once. Replace every `<...>` and keep
> the table structure and the fenced JSON intact.

**Mock version**: <v_N>
**Data sources**: <csv file(s) from DATA-MODEL.md>

## Element Mapping

One row per mock element. `id` matches a `data-plan-id` from `mock.html` **exactly**.
`tableau construct` names the construct; default to the **simplest sufficient** primitive.
Leave `justification` blank (`-`) for a simple primitive; for any advanced feature (Dynamic
Zone Visibility, LOD, table calculation, parameter action) write **why the simpler
alternative was rejected**.

| id                | tableau construct                                   | justification                                              |
|-------------------|-----------------------------------------------------|------------------------------------------------------------|
| kpi-revenue       | Text mark, `SUM([revenue])`, `$#,##0`               | -                                                          |
| chart-trend       | Line mark: MONTH([order_date]) x SUM([revenue])     | -                                                          |
| chart-region      | Bar mark: [region] x SUM([revenue])                 | -                                                          |
| flt-region        | Filter card on [region] (multi-select dropdown)     | -                                                          |
| int-region-filter | Filter action (Use as Filter) chart-region -> rest  | -                                                          |
| pnl-details       | Dynamic Zone Visibility on the details container     | show/hide button can't collapse a whole zone here: <why>   |

## Layout

The dashboard's container tree, derived from the approved `mock.html` — this is how the
mock's geometry reaches `tableau-build`, so the workbook's layout matches the mock instead
of being guessed. Start with a short human-readable summary of the layout, then the
**required fenced JSON block**:

- `canvas` — the mock's design dimensions in px (from the plan's Screen Size).
- `root` — a **container**: `type` is `vert` (children stack top-to-bottom) or `horz`
  (left-to-right), `children` is a non-empty list.
- Each child is another container or a **leaf** `{"id": ..., "size": ...}` whose `id`
  matches an Element Mapping row exactly. A container may also carry an `id` when the
  container itself is a mapped element (e.g. a DZV panel holding further zones).
- `size` is the child's **percentage of its parent** along the parent's flow axis;
  siblings must sum to ~100. The root itself needs no size.
- Every mapped **zone** id appears **exactly once**. Interaction ids (`int-*`) are
  dashboard actions, not zones — never place them in the tree.

<Example: a filter bar over a KPI row, a chart row, and a collapsible details panel.>

```json
{
  "canvas": {"width": 1366, "height": 768},
  "root": {
    "type": "vert",
    "children": [
      {"id": "flt-region", "size": 8},
      {"id": "kpi-revenue", "size": 14},
      {
        "type": "horz",
        "size": 58,
        "children": [
          {"id": "chart-trend", "size": 60},
          {"id": "chart-region", "size": 40}
        ]
      },
      {"id": "pnl-details", "size": 20}
    ]
  }
}
```

## Calculated Fields

| field name | formula | used by (element id) |
|------------|---------|----------------------|
| <Name>     | `<formula>` | <id(s)>          |

## Data Sources & Joins

| source (csv)     | role                | join / relationship                          |
|------------------|---------------------|----------------------------------------------|
| <sales.csv>      | primary             | -                                            |
| <segments.csv>   | lookup              | `sales.customer = segments.customer` (left)  |

## Actions

The dashboard actions that back the mock's interactions (source/target are element ids).

| action name | type       | source        | target(s)              | run on |
|-------------|------------|---------------|------------------------|--------|
| <Name>      | Filter     | chart-region  | chart-trend, kpi-revenue | select |

## Parameters

| name | data type | allowed values | drives |
|------|-----------|----------------|--------|
| <Name> | <type>  | <list/range>   | <what it changes> |

_Write "None" under any section that does not apply._
