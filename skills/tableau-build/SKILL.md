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

The mechanical guarantees — the entry gate, the manifest schema validation, and the STATE.md
transition — live in `build.py` (CLI) and `manifest.py` (the schema core). Your job is the
judgment part: translating each spec row into the right manifest entry. Run the script at
the points below; do not hand-edit `STATE.md`.

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

5. **Commit** — only after the analyst approves:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/build.py" commit "<project-dir>"
   ```

   Commit re-validates the manifest and records `build` = `approved`. If it prints
   `[REFUSED]`, fix what it names and re-run. On success, tell the analyst the pipeline is
   complete and where the deliverable lives.

## Notes

- **Non-skippable.** The workbook is the deliverable; `commit` only ever sets `approved`.
- **Versioned deliverable.** The manifest and workbook live under `mock-version/<v_N>/`
  beside the `mock.html` / `IMPLEMENTATION-SPEC.md` they were built from (CONTRACT.md §4.3).
  Build **overwrites in place** on a re-run and never bumps `current_version`; a new build
  version is created by re-running `tableau-mock` (which bumps and stales spec and build).
- **In development.** The deterministic workbook generator (spec → `dashboard.twbx`) is the
  next ticket; today the skill gates the step and produces the validated manifest it will
  consume.

> The full `STATE.md` schema and the ordering / staleness / versioning rules live in
> `CONTRACT.md` at the repo root. This skill restates only its own slice; `build.py` /
> `manifest.py` are the executable mirror of the contract it enforces.
