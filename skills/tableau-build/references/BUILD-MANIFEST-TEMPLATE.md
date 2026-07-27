# build-manifest.json — the builder's only input

Derived by the agent from `IMPLEMENTATION-SPEC.md` (constructs + layout tree) and
`DATA-MODEL.md` (the fields). It is **build-internal**, not a handoff artifact — hence the
lowercase name (CONTRACT.md §3) — and it lives beside the workbook in `mock-version/v_N/`.

`build.py validate` schema-checks it fail-fast: every message names the offending entry, so
a bad spec-to-manifest translation is fixed row-by-row before any XML is generated.

## Sections

| key | required | what it holds |
|-----|----------|---------------|
| `target_tableau_version` | yes | Copied verbatim from `STATE.md` — a mismatch is rejected. |
| `datasources` | yes | One per `data/` CSV ("csv = datasource", CONTRACT.md §3.2). Every field must be documented in `DATA-MODEL.md` for that CSV. |
| `worksheets` | yes | One per layout zone that is a **view**: unique `name`, a known `chart_type`, the `element_id` it fills, its `datasource`, and shelves/encodings whose fields resolve. |
| `layout` | yes | The spec's `## Layout` container tree, copied as-is (canvas + nested `vert`/`horz` + id leaves with `%` sizes). |
| `actions` | yes (`[]` if none) | Dashboard actions; `type` from `filter`/`highlight`/`parameter`/`set`/`url`. `source` is always a zone; a target is a zone for `filter`/`highlight`, a declared parameter for `parameter` (see below). |
| `parameters` | yes (`[]` if none) | Each needs a `name` and a `data_type`. |
| `objects` | optional | Layout zones no view fills — a filter card, title text, a logo, a legend. `kind` from `filter`/`parameter`/`text`/`image`/`legend`/`button`/`blank`. |
| `calculated_fields` | optional | Fields that legitimately are not in the data model; declaring one makes it usable on a shelf of its `datasource`. |

**Copy the `layout` tree from the spec verbatim** — `validate` diffs the two and rejects any
zone the manifest drops or invents, so the workbook cannot disagree with the approved mock.

**Every leaf zone must be filled** by exactly one worksheet or one `objects` entry — an
unfilled zone would build an empty container. A *mapped container* (a node with both an `id`
and `children`, e.g. a DZV panel) is filled by its children and needs no entry of its own.
Interaction ids (`int-*`) are actions, not zones, and never appear in the tree.

Chart types the builder emits: `bar`, `line`, `area`, `pie`, `scatter`, `map`, `text`,
`table`, `heatmap`, `histogram`, `treemap`, `bullet`, `gantt`, `boxplot`.

**Shelf and encoding entries** are either a bare field name (`"revenue"`) or an object
carrying what the builder must apply: `{"field": "revenue", "aggregation": "sum"}` or
`{"field": "order_date", "date_part": "month"}`. Aggregations: `sum`, `avg`, `min`, `max`,
`count`, `countd`, `median`, `attr`, `none`. Never write an expression like
`"SUM([revenue])"` — that is the spec's prose, not a field reference.

**Action targets depend on the type**: `filter` / `highlight` target layout zones, a
`parameter` action targets a declared parameter by name. The `source` is always a zone.

## Example

```json
{
  "target_tableau_version": "2024.2-2025.x",
  "datasources": [
    {
      "name": "sales_orders",
      "csv": "sales_orders.csv",
      "fields": [
        {"name": "order_date", "type": "date"},
        {"name": "region", "type": "string"},
        {"name": "revenue", "type": "real"}
      ]
    }
  ],
  "calculated_fields": [
    {"name": "Revenue per Order", "formula": "SUM([revenue]) / COUNTD([order_id])",
     "datasource": "sales_orders"}
  ],
  "worksheets": [
    {
      "name": "Revenue KPI",
      "element_id": "kpi-revenue",
      "chart_type": "text",
      "datasource": "sales_orders",
      "shelves": {"columns": [], "rows": []},
      "encodings": {"text": "revenue"}
    },
    {
      "name": "Revenue Trend",
      "element_id": "chart-trend",
      "chart_type": "line",
      "datasource": "sales_orders",
      "shelves": {
        "columns": [{"field": "order_date", "date_part": "month"}],
        "rows": [{"field": "revenue", "aggregation": "sum"}]
      },
      "encodings": {"color": "region"}
    }
  ],
  "objects": [
    {"element_id": "flt-region", "kind": "filter"}
  ],
  "layout": {
    "canvas": {"width": 1366, "height": 768},
    "root": {
      "type": "vert",
      "children": [
        {"id": "flt-region", "size": 8},
        {"id": "kpi-revenue", "size": 22},
        {"id": "chart-trend", "size": 70}
      ]
    }
  },
  "actions": [
    {"name": "Region cross-filter", "type": "filter", "source": "chart-trend",
     "targets": ["kpi-revenue"], "run_on": "select"}
  ],
  "parameters": []
}
```
