---
name: tableau-build
description: Builds the Tableau workbook for the tableau-dashboard-plugin workflow from the approved IMPLEMENTATION-SPEC.md and DATA-MODEL.md, producing a Replace-Data-Source-ready dashboard.twbx beside the mock it was specced from. Derives a machine-readable build manifest (datasources, worksheets with chart type and shelves/encodings, the spec's layout container tree, actions, parameters, and the project's target Tableau version) and schema-validates it fail-fast, so an unknown chart type, an element id missing from the layout, or a field that is not in the data model is caught with the offending entry named before any XML is generated. Reads the approved IMPLEMENTATION-SPEC.md at the current version, DATA-MODEL.md, and the CSVs under data/. Use when the user wants to build the workbook, generate the twbx, or when tableau-route reports build is next. Step 8 of 8 in the workflow.
disable-model-invocation: true
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(python *), Bash(python3 *)
---

# tableau-build

Step 8 of 8, the last step, and **non-skippable**. It turns the approved
`IMPLEMENTATION-SPEC.md` into the deliverable workbook. Between the prose spec and the XML
sits one artifact: the **build manifest** (`build-manifest.json`) — the machine-readable
translation of the spec that the deterministic builder consumes. Validating the manifest is
what makes a bad translation fail *before* any XML exists, with the offending worksheet,
field, or element id named.

| | |
|---|---|
| **Reads** | **Required:** `mock-version/<v_N>/IMPLEMENTATION-SPEC.md` (from `spec`) — the construct mapping and the layout container tree; `DATA-MODEL.md` + `data/*.csv` (from `data`) — the fields the workbook binds to. **Optional:** `DESIGN-TOKENS.md` (styling; absent ⇒ neutral). |
| **Writes** | `mock-version/<v_N>/build-manifest.json` (build-internal) and `mock-version/<v_N>/dashboard.twbx` — standalone deliverable copies (CONTRACT.md §4.3). |
| **STATE.md update** | Sets `build` = `approved`. Nothing is downstream, so nothing goes stale. Does **not** touch `current_version` — only `tableau-mock` bumps it (§4.3). |
| **Entry gate** | Refuses to run until `spec` is resolved **and** `IMPLEMENTATION-SPEC.md` exists at `current_version`, **and** `data` is resolved **and** `DATA-MODEL.md` plus at least one CSV exist (CONTRACT.md §4.1). |
| **Next step** | None — the pipeline is complete (`tableau-route` confirms). |

The mechanical guarantees live in Python: the entry gate, the manifest schema validation and
the STATE.md transition in `build.py` / `manifest.py`, the workbook shell in `twb.py`, and
every chart template in `worksheet.py` — the element order, the generated ids, the four
places every column must appear, the mark class and shelves per chart type, the live-only
connection and the version targeting are **code, not a checklist**, so a validated manifest
builds a workbook that is correct by construction. Your job is the judgment part: translating
each spec row into the right manifest entry. Run the script at the points below; do not
hand-edit `STATE.md` or the generated XML.

## Chart templates

Every chart type is built from manifest fields — nothing is copied from a snippet, so no
stray field name or datasource id can leak into the analyst's workbook. Pick the
`chart_type` from `references/BUILD-MANIFEST-TEMPLATE.md`'s table (`bar`, `line`, `area`,
`pie`, `scatter`, `map`, `text` = KPI card, `table` = text table, `histogram`, `dual-axis`,
`combo`, plus `heatmap` / `treemap` / `bullet` / `gantt` / `boxplot`).

Four things the spec may ask for are **not** chart types — they are optional keys on any
worksheet: `sort`, `filters`, `tooltip`, and `axis_titles` / `number_formats`. A *stacked*
bar is a `bar` with a `color` encoding. Reach for a modifier before reaching for a new
chart type.

Styling comes from `DESIGN-TOKENS.md` when the analyst ran `tableau-brand`: its font family
and chart-title size/colour are applied to every worksheet automatically. When it is absent,
Tableau's own defaults apply and nothing is invented. The manifest never carries fonts or
colours.

## The build manifest

`references/BUILD-MANIFEST-TEMPLATE.md` is the annotated schema and a worked example. In
short, the manifest carries `target_tableau_version` (copied from `STATE.md`), `datasources`
(one per `data/` CSV), `worksheets` (chart type + shelves/encodings + the `element_id` each
fills), `layout` (the spec's container tree, copied as-is), `actions`, and `parameters`;
plus optional `objects` (zones no view fills — a filter card, title, logo) and
`calculated_fields`.

`validate` rejects, naming the entry: an unknown `chart_type`, an `element_id` that is not a
zone in the layout tree, a leaf zone nothing fills, a field that `DATA-MODEL.md` does not
document for that CSV, a shelf referencing an undeclared field or an unknown aggregation, a
duplicate worksheet name, an action endpoint that is not a zone (or, for a parameter action,
not a declared parameter), and a target version that disagrees with `STATE.md`. It also
**diffs the manifest's layout tree against the spec's** (CONTRACT.md §1.1), so a dropped or
invented zone is caught rather than silently built.

## How to run

1. **Precheck.** From the project directory, run:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/build.py" precheck "<project-dir>"
   ```

   (Use `python3` if `python` is unavailable.) If it prints `[BLOCKED]`, relay the reason and
   **stop** — the analyst must resolve the named upstream step (`tableau-spec` for the spec,
   `tableau-data` for the model and CSVs) first. Otherwise note its signals: the **spec path**
   to build from, the **CSVs**, the **target Tableau version**, and the **manifest and
   workbook paths** in the mock's `current_version` (build writes beside the spec it builds
   from and never bumps the version).

2. **Read the inputs.** Read `IMPLEMENTATION-SPEC.md` at the reported path — its Element
   Mapping rows are the worksheets/objects to create and its `## Layout` JSON is the
   container tree to copy. Read `DATA-MODEL.md` for the exact field names and types.

3. **Author `build-manifest.json`** at the precheck's manifest path, following
   `references/BUILD-MANIFEST-TEMPLATE.md`. Every mapping row becomes either a worksheet
   (a view), an `objects` entry (a filter card / text / image zone), or an action (`int-*`
   ids). When **refining** an existing manifest at this version, `Edit` it in place.

4. **Validate, then present:**

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/build.py" validate "<project-dir>"
   ```

   If it prints `[INVALID]`, fix the named entries and re-run — do not generate a workbook
   from a manifest that does not validate. When it prints `[OK]`, present the manifest
   summary (worksheets, layout zones, actions) for approval.

5. **Build the workbook:**

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/build.py" build "<project-dir>"
   ```

   This assembles `dashboard.twb`, runs both validators over it, and packages
   `dashboard.twbx` with the CSVs. `[BUILT]` means both validators are green — a `[WARN]`
   line about a missing `explain-data` element is the expected version shift when the target
   is `2024.2-2025.x` (the 2026.1 schema requires an element that older Tableau must not
   carry), not a problem. `[INVALID]` leaves the `.twb` on disk unpackaged: read the named
   errors, fix the manifest, and re-run. Never hand-patch the generated XML — the assembler
   is the fix's home.

6. **Commit** — only after the analyst approves:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/build.py" commit "<project-dir>"
   ```

   Commit re-validates the manifest, refuses unless `dashboard.twbx` is on disk (run step 5
   first), and records `build` = `approved`. If it prints `[REFUSED]`, fix what it names and
   re-run. On success, tell the analyst the pipeline is complete and where the deliverable
   lives.

## Notes

- **Non-skippable.** The workbook is the deliverable; `commit` only ever sets `approved`.
- **Versioned deliverable.** The manifest and workbook live under `mock-version/<v_N>/`
  beside the `mock.html` / `IMPLEMENTATION-SPEC.md` they were built from (CONTRACT.md §4.3).
  Build **overwrites in place** on a re-run and never bumps `current_version`; a new build
  version is created by re-running `tableau-mock` (which bumps and stales spec and build).
- **Live connection, always.** The workbook never carries an extract: the `.twbx` embeds the
  CSVs, and the analyst points it at the real database with Data → Replace Data Source.
- **The zones are computed and the canvas is the dashboard's minimum size.** The `layout`
  tree becomes the dashboard's zone hierarchy one-to-one: sibling `size` values are
  proportions of the parent along its flow axis, mapped into Tableau's 0–100000 space at the
  canvas dimensions. Because that space is normalised, the approved proportions hold at any
  window size, so the dashboard is `sizing-mode='range'` with the canvas as `minwidth` /
  `minheight` and **no maximum** — the analyst can lower the minimum in Desktop.
- **Every view zone's header is a text object.** A sheet's *own* title is always off: Tableau
  draws it inside the zone out of the sheet's own height, so a short zone (a KPI card) loses
  its number to it. Give every `worksheets[]` entry a `title` — it becomes a text zone above
  the sheet zone — or have the layout place a `text` object beside it; a view with neither
  gets no header at all. A colour-encoded chart also gets a legend zone below it. Both
  generated zones stack *inside* the element's own box, so they never disturb its siblings.
- **Field labels are off on every sheet.** They repeat what the zone's header already says
  and cost the chart a whole band of the sheet.
- **A filter / parameter / image / button / legend object reserves its box, empty.** Each of
  those zone types needs a reference the manifest does not carry yet (a field plus the
  dashboard's own `<datasource-dependencies>`, a parameter, a filename, an action, a sheet +
  colour field), and Tableau does not treat those as optional — so the layout keeps the
  geometry and the features-and-actions ticket turns each into its real zone. `text` and
  `blank` objects render fully today.

> The full `STATE.md` schema and the ordering / staleness / versioning rules live in
> `CONTRACT.md` at the repo root. This skill restates only its own slice; `build.py`,
> `manifest.py`, `twb.py` and `worksheet.py` are the executable mirror of the contract it
> enforces.
