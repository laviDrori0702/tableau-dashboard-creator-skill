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
| `parameters` | yes (`[]` if none) | Each needs a `name`, a `data_type` (`string`/`integer`/`real`/`boolean`/`date`/`datetime`), and a `current_value`. Give it a domain — a `values` list **or** a `range` (`min`/`max`, optional `step`), never both — or it is a free-entry box. Optional `format`. |
| `objects` | optional | Layout zones no view fills — a filter card, a parameter control, title text, a logo, a legend. `kind` from `filter`/`parameter`/`text`/`image`/`legend`/`button`/`blank`. `filter`, `parameter`, `text` and `blank` render today; `image`, `button` and `legend` reserve their box as an empty zone until the wiring they need lands. |
| `calculated_fields` | optional | Fields that legitimately are not in the data model; declaring one makes it usable on a shelf of its `datasource`. Each takes a `name`, a `formula`, a `datasource`, an optional `type` (default `real`) and an optional `format`. Any Tableau formula, **LOD expressions included** (`{FIXED [region]: SUM([revenue])}`) — an LOD is row-level, so it is re-aggregated when placed on a shelf, while a formula that already aggregates is not. |

**Copy the `layout` tree from the spec verbatim** — `validate` diffs the two and rejects any
zone the manifest drops or invents, so the workbook cannot disagree with the approved mock.
The tree *is* the dashboard's zone hierarchy: each node becomes one zone, sibling `size`
values are proportions of the parent along its flow axis, and `canvas` px is what those
proportions are computed against — not the dashboard's size, which is `sizing-mode='range'` at
a 1100 × 800 minimum with no maximum. A child with no `size` shares whatever its siblings
leave.

**Give every view element a header.** A sheet's own title never renders on the dashboard, so
a `worksheets[]` entry without a `title` (or a `text` object placed beside it in the tree)
shows up with no header at all.

**Every leaf zone must be filled** by exactly one worksheet or one `objects` entry — an
unfilled zone would build an empty container. A *mapped container* (a node with both an `id`
and `children`, e.g. a DZV panel) is filled by its children and needs no entry of its own.
Interaction ids (`int-*`) are actions, not zones, and never appear in the tree.

## Chart types

| `chart_type` | what it emits |
|--------------|---------------|
| `bar` | Discrete dimension × aggregated measure. Add a `color` encoding for a **stacked** bar. |
| `line` | Same shape, but the dimension is a **continuous date** — give it a `date_part`. |
| `area` | Line with an explicit `Area` mark. |
| `pie` | Rows/Cols stay empty; everything is encodings (`color`, `wedge-size`, `size`, `text`). |
| `scatter` | A measure on each axis, a dimension on `lod` (Detail). |
| `map` | Tableau's generated lat/long; `lod` is the geographic dimension, `color` the measure. Optional `geo_role` (e.g. `"[Country].[ISO3166_2]"`). |
| `text` | **KPI card** — empty shelves, a single `text` encoding, rendered as a big number. |
| `table` | **Text table** — a dimension on each axis, the measure on `text`. |
| `histogram` | A binned dimension: `{"field": "revenue", "bin": 500}` on `columns`, a `count` on `rows`. |
| `dual-axis` | Exactly **two** measures on `rows`, overlaid on synchronised axes. Both panes keep the `Automatic` mark, so a continuous date on `columns` draws **two lines** — pick `combo` when the two series should look different. |
| `combo` | A dual axis with Bar marks on the first measure and Line on the second. |
| `heatmap`, `treemap`, `bullet`, `gantt`, `boxplot` | The corresponding mark class. |

Four of the legacy patterns are **modifiers**, not chart types — they apply to any of the
above via the optional worksheet keys below.

**Shelf and encoding entries** are either a bare field name (`"revenue"`) or an object
carrying what the builder must apply: `{"field": "revenue", "aggregation": "sum"}`,
`{"field": "order_date", "date_part": "month"}`, or `{"field": "revenue", "bin": 500}`.
Aggregations: `sum`, `avg`, `min`, `max`, `count`, `countd`, `median`, `attr`, `none` — a
measure with none of them defaults to `SUM`. Date parts: `year`, `quarter`, `month`, `week`,
`day`, `date`. Never write an expression like `"SUM([revenue])"` — that is the spec's prose,
not a field reference.

A shelf entry may also carry `table_calc` — a quick table calculation over the aggregate:
`{"field": "revenue", "aggregation": "sum", "table_calc": "CumTotal"}` for a running total.
One of `CumTotal`, `WindowTotal`, `Difference`, `PctDiff`, `PctValue`, `PctTotal`, `Rank`,
`PctRank`; it computes across the table (Tableau's "Table (across)" addressing).

**A histogram's `bin` is a width, not a bin count** — Tableau slices `0–500`, `500–1000`, …
Derive it from the field's range so the chart lands at roughly **10–25 bars**: a revenue that
runs 10k–50k wants `bin: 2000`, not the `500` from this document's example (which gives ~80
slivers). The `Sample values` column of `DATA-MODEL.md` is the only range signal available
today — nothing computes the width for you.

Encodings the builder emits: `color`, `size`, `shape`, `text`, `lod`, `wedge-size`,
`geometry`, `tooltip`. Any other name is a validation error, not a silent drop.

**A field on `text` is a request for mark labels**, on any chart type — `{"text": {"field":
"revenue", "aggregation": "sum"}}` on a bar gives labelled bars, and it is also what makes a
`number_formats` entry *visible*, since a cell format reaches a chart only through its labels.
On `chart_type: "text"` the same encoding is the KPI card's big number instead.

## Optional worksheet keys (the modifiers)

| key | shape | what it does |
|-----|-------|--------------|
| `sort` | `{"field": "region", "direction": "DESC", "by": {"field": "revenue", "aggregation": "sum"}}` | Computed sort. Swap `by` for `"order": ["West", "East"]` to sort manually. |
| `filters` | `[{"field": "region", "values": ["Europe"], "context": true}, {"field": "order_date", "min": "2025-01-01", "max": "2025-06-30"}]` | Categorical (member list) or in-range filter. `context: true` runs it before FIXED LODs. |
| `tooltip` | `[{"label": "Revenue", "field": "revenue", "aggregation": "sum"}]` | A custom tooltip template, one label/value pair per line. |
| `axis_titles` | `{"rows": "Revenue per category"}` | Overrides the generated axis title. |
| `number_formats` | `[{"field": "revenue", "format": "$#,##0"}]` | Cell number format for that field. |
| `reference_lines` | `[{"field": "revenue", "aggregation": "sum", "formula": "average", "scope": "per-table", "label": "<Computation>: <Value>"}]` | A line drawn at an aggregate of the measure. `formula` from `constant`/`total`/`sum`/`min`/`max`/`average`/`median`/`quantiles`/`percentile`/`stdev`/`confidence`/`medianconfidence` (default `average`); `scope` from `per-cell`/`per-pane`/`per-table` (default `per-table`). A `label` string is used verbatim; without one, `label_type` from `none`/`automatic`/`value`/`computation`/`custom` (default `computation`). |
| `title` | `"Revenue by region"` | Puts a styled text zone above the sheet's zone in the dashboard and suppresses the sheet's own title. Omit to keep Tableau's sheet title. |
| `fit` | `"entire-view"` | How the sheet fills its zone: `entire-view` (the default), `standard`, `fit-width`, `fit-height`. |
| `format` | `{"shading": "#FFFFFF", "borders": "none", "gridlines": "#E5E8E8", "zero_lines": "none", "align": "center", "vertical_align": "top"}` | Format Shading / Borders / Lines / Alignment. Every colour is a `#rrggbb`; `borders`, `gridlines` and `zero_lines` also take `"none"` to turn the border or line off. `align` is `left`/`center`/`right`, `vertical_align` is `top`/`center`/`bottom`. |

An `objects` entry of `kind: "text"` takes its content the same way: `{"element_id":
"txt-title", "kind": "text", "text": "Sales Performance"}`.

Every modifier's field is resolved as strictly as a shelf field — a filter on a field the
data model does not document is rejected, not silently dropped.

### Fit defaults to Entire View

A dashboard zone is a fixed box, and Tableau's own default (`standard`) leaves the chart at
its natural size floating in that box's whitespace. So every sheet is emitted at **Entire
View** unless it says otherwise — except `chart_type: "table"`, which defaults to `standard`
because a text table is meant to **scroll**: squeezing 200 rows into a zone renders them as
unreadable slivers. Any sheet with more rows than its zone can show wants `"fit": "standard"`
too. The fit is written on both the sheet's own tab and the dashboard's copy of it.

### Colour: typography *and* the palette come from `DESIGN-TOKENS.md`

**Nothing in the manifest sets fonts or colours** *except* the per-sheet `format` block above
(borders, lines, shading, alignment — sheet furniture, not brand identity). When
`DESIGN-TOKENS.md` exists, its font family, chart-title size/colour **and its ordered
`### Chart series colors`** are applied to every worksheet; when it does not, Tableau's own
defaults apply.

The palette needs **no data member values**, which is what previously blocked it. A palette
that binds hexes to concrete members (`<map to='#…'><bucket>"West"</bucket>`) is unbuildable
from a manifest — the builder never sees the data — but it does not have to be: the mark's
colour encoding carries an inline `<color-palette>` listing the brand's colours *in order*,
and Tableau walks the field's domain against them exactly as it does with its own default 10.
So:

- a **dimension** on `color` (a stacked bar, a pie, a treemap) takes the whole ordered palette;
- a **measure** on `color` (a filled map, a heatmap) is a continuous ramp, and gets the first
  and last brand colours as its low and high ends;
- a `dual-axis` / `combo` chart is coloured by the built-in Measure Names and takes the
  categorical palette too.

Tokens with no `### Chart series colors` section leave Tableau's default 10 in place.

## Interactions

**Actions.** `source` is always a **view** zone (the sheet whose marks are clicked), and so is
every `filter` / `highlight` target; a `parameter` action targets a declared **parameter** by
name and needs a `field` — the field read off the clicked mark and written into the parameter.
`run_on` is `select` (click a mark; the default) or `hover`. One Tableau action is emitted
**per target**, so a source fanning out to three zones is three actions.

```json
{"name": "Trend cross-filter", "type": "filter", "source": "chart-trend",
 "targets": ["chart-detail", "kpi-revenue"], "run_on": "select"}
{"name": "Pick region", "type": "parameter", "source": "chart-trend",
 "targets": ["Selected Region"], "field": "region"}
```

Clearing the selection **resets** a parameter action's parameter to its `current_value`, so
whatever the parameter drives returns to its opening state. That is what makes a parameter
action safe to use without a control: the viewer cannot get stuck in a state only a control
could undo. The reset is why **a parameter action's target must be a `string` parameter** —
that is the only type whose reset value has an attested serialization, and a target that
could not be reset is rejected rather than built without it. Give it a `values` domain
(`["All", "West", "East"]`) and open it on the neutral member.

`set` and `url` actions are **rejected**: the builder emits nothing for them, and a dashboard
that validated with one would open with the interaction silently missing. A `drill`
(CONTRACT.md §6) is built as a `parameter` action.

**A filter card** (`objects` entry, `kind: "filter"`) is the UI for **one** worksheet's filter,
so it names both the `worksheet` and the `field`: `{"element_id": "flt-region", "kind":
"filter", "field": "region", "worksheet": "Revenue Trend", "mode": "checkdropdown"}`. The card
lists the field's members, so the field must be a `string` or `boolean` field of that
worksheet's datasource — for a date or a numeric range use the worksheet's own `filters` with
`min`/`max` instead. `mode` is `checkdropdown` (a dropdown of checkboxes; the default) or
`typeinlist` (a search box over the list). "Apply to all sheets" is a Desktop-side choice, not
a manifest key.

**A parameter control** (`kind: "parameter"`) names one declared parameter:
`{"element_id": "prm-topn", "kind": "parameter", "parameter": "Top N"}`. Reference the
parameter from a calculated field's formula as `[Parameters].[Top N]` — every worksheet whose
calculations read it declares it automatically.

### Dynamic Zone Visibility

**Show/hide a zone** with `visibility` on a **layout** node: `{"id": "chart-category",
"visibility": "Show Breakdown"}`. The value must be the name of a declared **boolean**
calculated field, and the zone is shown when it is true. For a zone with a generated header or
legend, the whole wrapper is what shows and hides.

**The one rule the calc must obey: it resolves to a single value, independent of the view.**
Tableau reads *one* value per view, so a row-level boolean (`SUM([revenue]) > 1000`,
`[region] = "East"`) is not a visibility field — it splits the marks and the zone stops
toggling. In practice that means **the calc compares a parameter**, and which parameter shape
you reach for depends on who decides:

| the viewer decides, with a control | the viewer's *selection* decides |
|---|---|
| A two-value parameter and a control zone for it — the usual toggle. `boolean` (`true`/`false`) is the natural type; a `string` with `values: ["on", "off"]` works the same way and is the form this repo has round-tripped through Desktop. Calc: `[Parameters].[Show Detail] = true` / `= "on"`. | A `string` parameter a **parameter action** writes into, compared against its opening value: `[Parameters].[Selected Region] <> "All"`. The panel appears once a region is picked and hides itself when the selection clears (the action resets the parameter). No control zone needed. |

Both are ordinary DZV; pick by intent. A toggle is right for "let me collapse this"; a
selection-driven reveal is right for "there is nothing to show until you pick something", and
costs no dashboard real estate.

**Do not write the visibility calc as an LOD expression.** A `{FIXED : …}` or `{MAX(…)}` also
makes a field view-independent, and Tableau accepts it — but it is hard to reason about, hard
to fix by hand in Desktop, and silently changes what "single value" means when a filter moves.
A parameter comparison is the whole vocabulary needed here.

This restriction is **specific to visibility calcs**. LOD expressions are otherwise a normal,
supported part of a manifest — declare one as any other `calculated_fields` entry and place it
on a shelf like any measure.

Everything mechanical is the builder's job, not the manifest's: it puts the visibility field on
the controlled sheet's Detail shelf (Tableau evaluates it off the *view* — the `<datagraph>`
alone is not enough, and a workbook missing it opens fine and simply never toggles), declares
the parameter on that sheet and on its datasource, and emits the four document-format flags.
None of that is expressible in the manifest, and none of it needs to be.

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
     "datasource": "sales_orders", "type": "real", "format": "$#,##0"},
    {"name": "Show Breakdown", "formula": "[Parameters].[Selected Region] <> \"All\"",
     "datasource": "sales_orders", "type": "boolean"}
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
      "title": "Revenue over time",
      "shelves": {
        "columns": [{"field": "order_date", "date_part": "month"}],
        "rows": [{"field": "revenue", "aggregation": "sum"}]
      },
      "encodings": {"color": "region"},
      "fit": "entire-view",
      "format": {"shading": "#FFFFFF", "borders": "none", "gridlines": "#E5E8E8"},
      "filters": [{"field": "region", "values": ["West", "East"], "context": true}],
      "sort": {"field": "region", "direction": "DESC",
               "by": {"field": "revenue", "aggregation": "sum"}},
      "number_formats": [{"field": "revenue", "format": "$#,##0"}],
      "reference_lines": [{"field": "revenue", "aggregation": "sum", "formula": "average",
                           "scope": "per-table", "label": "<Computation>: <Value>"}]
    },
    {
      "name": "Running Revenue",
      "element_id": "chart-cumulative",
      "chart_type": "area",
      "datasource": "sales_orders",
      "title": "Revenue to date",
      "shelves": {
        "columns": [{"field": "order_date", "date_part": "month"}],
        "rows": [{"field": "revenue", "aggregation": "sum", "table_calc": "CumTotal"}]
      }
    }
  ],
  "objects": [
    {"element_id": "flt-region", "kind": "filter", "field": "region",
     "worksheet": "Revenue Trend", "mode": "checkdropdown"},
    {"element_id": "prm-topn", "kind": "parameter", "parameter": "Top N"}
  ],
  "layout": {
    "canvas": {"width": 1366, "height": 768},
    "root": {
      "type": "vert",
      "children": [
        {"id": "flt-region", "size": 8},
        {"id": "prm-topn", "size": 8},
        {"id": "kpi-revenue", "size": 20},
        {"id": "chart-trend", "size": 34},
        {"id": "chart-cumulative", "size": 30, "visibility": "Show Breakdown"}
      ]
    }
  },
  "actions": [
    {"name": "Region cross-filter", "type": "filter", "source": "chart-trend",
     "targets": ["kpi-revenue"], "run_on": "select"},
    {"name": "Region highlight", "type": "highlight", "source": "chart-trend",
     "targets": ["chart-cumulative"], "run_on": "hover"},
    {"name": "Pick region", "type": "parameter", "source": "chart-trend",
     "targets": ["Selected Region"], "field": "region"}
  ],
  "parameters": [
    {"name": "Selected Region", "data_type": "string", "current_value": "All",
     "values": ["All", "East", "North", "West"]},
    {"name": "Top N", "data_type": "integer", "current_value": 10,
     "range": {"min": 5, "max": 50, "step": 5}, "format": "#,##0"}
  ]
}
```
