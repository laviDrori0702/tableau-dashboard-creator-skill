---
name: tableau-build
description: Builds the Tableau workbook for the tableau-dashboard-plugin workflow from the approved IMPLEMENTATION-SPEC.md and DATA-MODEL.md, producing a Replace-Data-Source-ready dashboard.twbx beside the mock it was specced from. Derives a machine-readable build manifest (datasources, worksheets with chart type and shelves/encodings, the spec's layout container tree, actions, parameters, and the project's target Tableau version) and schema-validates it fail-fast, so an unknown chart type, an element id missing from the layout, or a field that is not in the data model is caught with the offending entry named before any XML is generated. Reads the approved IMPLEMENTATION-SPEC.md at the current version, DATA-MODEL.md, and the CSVs under data/. Use when the user wants to build the workbook, generate the twbx, or when tableau-route reports build is next. Step 8 of 8 in the workflow.
disable-model-invocation: true
allowed-tools: Read, Write, Edit, Grep, AskUserQuestion, Bash(python *), Bash(python3 *)
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

Several things the spec may ask for are **not** chart types — they are optional keys on any
worksheet: `sort`, `filters`, `tooltip`, `reference_lines`, `fit`, `format`, and `axis_titles` /
`number_formats`; a running total or percent-of-total is a `table_calc` on the shelf entry. A
*stacked* bar is a `bar` with a `color` encoding, and a measure on the `text` encoding gives
any chart mark labels. Reach for a modifier before reaching for a new chart type.

Styling comes from `DESIGN-TOKENS.md` when the analyst ran `tableau-brand`: its font family,
chart-title size/colour and ordered `### Chart series colors` are applied to every worksheet
automatically — the series colours ride along as an inline palette, so no data member values
are needed. When it is absent, Tableau's own defaults apply and nothing is invented. The
manifest carries no fonts or brand colours; its per-sheet `format` block covers only sheet
furniture (borders, lines, shading, alignment).

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

   An `[INVALID]` naming an **unknown `chart_type`** is not a typo but a construct the
   builder has no template for. Unlike an object kind it cannot be reduced to an empty box —
   a worksheet with no template renders nothing — so validation refuses fail-fast, before any
   XML exists. Say so plainly and offer the same two ways forward as any other gap (see the
   unsupported-construct note below): the closest supported chart type for now, plus either a
   reference `.twb` from Desktop or a hand-written block. Never silently substitute a
   different chart type — the spec is what the analyst approved.

5. **Build the workbook:**

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/build.py" build "<project-dir>"
   ```

   This assembles `dashboard.twb`, runs the **validation gate** over it, and packages
   `dashboard.twbx` with the CSVs. `[BUILT]` means all three validators are green — a `[WARN]`
   line about a missing `explain-data` element is the expected version shift when the target
   is `2024.2-2025.x` (the 2026.1 schema requires an element that older Tableau must not
   carry), not a problem. `[INVALID]` leaves the `.twb` on disk unpackaged: read the named
   errors, fix the manifest, and re-run. Never hand-patch the generated XML — the assembler
   is the fix's home.

   **The gate is what the analyst's trust rests on**, so never present a workbook that has
   not passed it. It is three validators under one verdict, each error prefixed with the one
   that raised it: `[semantic]` (is the XML internally consistent?), `[schema]` (does it match
   the XSD?) and `[conformance]` (does the workbook agree with the manifest — every layout
   element became a zone, every zone names a real sheet, every declared worksheet is built,
   placed, and has a window). **The gate refuses to run partially** — if `lxml` is missing the
   `[schema]` validator cannot execute, so the gate fails rather than reporting green on two
   of three (`pip install -r requirements.txt`). Only the third can see something *missing*:
   what is absent is absent consistently, so the first two happily pass a workbook that
   dropped a chart. To re-run the gate over a `.twb` already on disk — the revalidate half of
   a fix — use:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/build.py" gate "<project-dir>"
   ```

   It repackages the `.twbx` when the gate passes and deletes it when it does not, so
   `commit` can never approve a workbook that failed.

6. **Commit** — only after the analyst approves:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/build.py" commit "<project-dir>"
   ```

   Commit re-validates the manifest, refuses unless `dashboard.twbx` is on disk (run step 5
   first), and records `build` = `approved`. If it prints `[REFUSED]`, fix what it names and
   re-run. On success, tell the analyst the pipeline is complete and where the deliverable
   lives — and ask them to open it (see below).

## When Desktop rejects it — the report-back repair

**The validators are not Tableau.** A workbook can pass all three and still be refused, or
silently rewritten, on open. So the last thing you say after `commit` is that Desktop is the
authority: *open the `.twbx`, and if Tableau reports an error or a view renders differently
from the mock it was specced from, paste the error text back here.* That report is not a
support ticket — it is the input to a permanent template fix, so the next workbook the builder
generates is right too.

When one arrives:

1. **Locate the construct.** Desktop names a sheet, a field or an element; find its entry in
   `build-manifest.json` at the precheck's manifest path. That entry is what was asked for —
   the bug is either the XML that came out of it, or a validation that should have refused it.
2. **Diagnose which template wrote the bad XML.** Each part of the workbook has one author —
   the file that *emits* it, which is where the fix goes:

   | what Desktop complains about | the template |
   |---|---|
   | a view's marks, shelves, encodings, sort / filter / tooltip / reference line, formatting | `worksheet.py` |
   | a zone's position or size, a generated header / legend wrapper, an object kind | `zones.py` |
   | an action, a parameter, dynamic zone visibility | `features.py` |
   | the workbook shell, a datasource, a column, the version attribute | `twb.py` |
   | the manifest was accepted when it should have been refused | `manifest.py` (a missing check) |

   For an action, a filter card or dynamic zone visibility, check `twb.py` too: it *plans*
   them (`_plan_actions`, `_plan_quick_filter`, `_plan_interactions`) and `features.py` only
   renders what it was handed, so a wrong target or a missing declaration is often decided
   there.

3. **Fix it in the repo, never in the generated XML.** Add the smallest manifest that
   reproduces the bad XML as a test in `tests/` (`test_build.py`, `test_charts.py`,
   `test_features.py`, `test_zones.py`), watch it fail, then fix the template until it passes
   and the suite is green. A patch to the `.twb` fixes one workbook; a patch to the template
   fixes every workbook built afterwards. When the correct XML is not obvious, get it from
   Desktop: build that one construct by hand there, save, and diff its `.twb` against the
   generated one — Desktop's own output is the only authority on fidelity.
4. **Rebuild and re-attest.** Re-run steps 5 **and** 6 — a rebuild alone leaves `build` sitting
   at the `approved` it earned from the pre-fix workbook, and only `commit` re-checks that a
   packaged deliverable is actually on disk. Then have the analyst open the new `.twbx` and
   confirm the error is gone. The second Desktop open is what closes the loop; the gate going
   green is not.
5. **When this repo is not to hand** — an analyst in their own project, without the plugin
   source — hand-patch that one `.twb`, prove it with `build.py gate`, and ask them to file the
   error text plus the `build-manifest.json` that produced it at
   <https://github.com/laviDrori0702/tableau-dashboard-creator-skill/issues/new>, so the fix
   still lands in the templates.

## Notes

- **Non-skippable.** The workbook is the deliverable; `commit` only ever sets `approved`.
- **Versioned deliverable.** The manifest and workbook live under `mock-version/<v_N>/`
  beside the `mock.html` / `IMPLEMENTATION-SPEC.md` they were built from (CONTRACT.md §4.3).
  Build **overwrites in place** on a re-run and never bumps `current_version`; a new build
  version is created by re-running `tableau-mock` (which bumps and stales spec and build).
- **Live connection, always.** The workbook never carries an extract: the `.twbx` embeds the
  CSVs, and the analyst points it at the real database with Data → Replace Data Source.
- **The zones are computed and the dashboard is range-sized.** The `layout` tree becomes the
  dashboard's zone hierarchy one-to-one: sibling `size` values are proportions of the parent
  along its flow axis, mapped into Tableau's 0–100000 space at the canvas dimensions. Because
  that space is normalised, the approved proportions hold at any window size, so the dashboard
  is `sizing-mode='range'` at a fixed **1100 × 800** minimum with **no maximum**. The canvas
  is the design surface the tree was laid out against, not the size the analyst is stuck
  with — who can change either bound in Desktop.
- **Every view zone's header is a text object.** A sheet's *own* title is always off: Tableau
  draws it inside the zone out of the sheet's own height, so a short zone (a KPI card) loses
  its number to it. Give every `worksheets[]` entry a `title` — it becomes a text zone above
  the sheet zone — or have the layout place a `text` object beside it; a view with neither
  gets no header at all. A colour-encoded chart also gets a legend zone below it. Both
  generated zones stack *inside* the element's own box, so they never disturb its siblings.
- **Field labels are off on every sheet.** They repeat what the zone's header already says
  and cost the chart a whole band of the sheet.
- **The dashboard is interactive** (CONTRACT.md §6). An `actions` entry of type `filter`
  cross-filters its target zones from the marks clicked in its source view, `highlight` brushes
  related marks, and `parameter` writes a clicked mark's `field` into a declared parameter;
  `run_on` chooses click (`select`) or `hover`, and one Tableau action is emitted per target. A
  `filter` object is a quick-filter card over one worksheet's field, and a `parameter` object is
  that parameter's control. Endpoints are validated: an action source and its filter/highlight
  targets must be **view** zones, a parameter action's target a declared parameter.
- **A `visibility` key on a layout node is Dynamic Zone Visibility**, and its boolean
  calculated field must resolve to **one value, independent of the view** — so the calc compares
  a parameter. Either shape is normal: a two-value parameter with a control (`= true` / `=
  "on"`, the collapse-this-panel toggle) or a parameter a parameter action writes into,
  compared against its opening value (`<> "All"`, the nothing-to-show-until-you-pick reveal,
  which needs no control because clearing the selection resets it). **Never write a *visibility*
  calc as an LOD expression** — `{FIXED : …}` is view-independent too and Tableau accepts it,
  but it is hard to reason about and hard to repair by hand. That is the only place LODs are
  ruled out; elsewhere they are ordinary `calculated_fields`. A row-level boolean is not a
  visibility field at all: it splits the marks and the zone stops toggling. The Detail-shelf
  placement the field needs, the parameter declarations and the format flags are the builder's
  job.
- **An unsupported construct is refused by name, never silently.** An image / button / legend
  object needs a reference the manifest does not carry (a filename, an action, a sheet + colour
  field) and Tableau does not treat those as optional, so the layout **reserves its box as an
  empty zone** — the approved geometry holds and everything else builds. `filter`, `parameter`,
  `text` and `blank` objects render fully today. The gate emits a `[WARN]` naming each gap:
  relay it to the analyst with both ways forward — **either** they build that one object in
  Tableau Desktop and save a reference `.twb` for the repo (the permanent fix: a snippet under
  `references/snippets/` and a template in the builder), **or** say the word and you hand-write
  that one block into the `.twb` and prove it with `build.py gate` (the move-on fix, good for
  this workbook only). Ask which; never pick silently, and never let the analyst discover the
  gap as a blank rectangle in Desktop.

> The full `STATE.md` schema and the ordering / staleness / versioning rules live in
> `CONTRACT.md` at the repo root. This skill restates only its own slice; `build.py`,
> `manifest.py`, `twb.py` and `worksheet.py` are the executable mirror of the contract it
> enforces.
