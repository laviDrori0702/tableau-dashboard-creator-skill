# Implementation Spec: <dashboard name>

> Maps the approved `mock.html` to concrete Tableau constructs so `tableau-build` never has
> to guess. **The Element Mapping table below is required and machine-checked**: every mock
> element (every `data-plan-id`) must have exactly one row, and any escalation to an advanced
> feature must carry a justification. Replace every `<...>` and keep the table structure intact.

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
