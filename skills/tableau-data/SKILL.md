---
name: tableau-data
description: Acquires the data for a tableau-dashboard-plugin project and writes the DATA-MODEL.md handoff artifact. CSV mode (the default, zero-credential path) profiles the analyst's data/*.csv into documented field names + types and validates that headers match exactly; the scaffold/sample-data/ demo CSVs are the fallback. Detects the published-ds route (datasources.json + .env) and defers its VizQL Data Service extraction. Use when the user wants to acquire or model dashboard data, build DATA-MODEL.md, or when tableau-route reports data is next. Step 3 of 8 in the workflow.
disable-model-invocation: true
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(python *), Bash(python3 *)
---

# tableau-data

Step 3 of 8, and **non-skippable**. It turns the analyst's data into `DATA-MODEL.md`
— the documented field names and types that `tableau-mock` and `tableau-build`
build against. The CSVs under `data/` are the single source of truth for those field
names, so this step also **validates that the documented names match the real CSV
headers exactly** before approving.

| | |
|---|---|
| **Reads** | The data: production `data/*.csv` (preferred) **or** the `scaffold/sample-data/*.csv` demo fallback. Also *detects* the published-ds route inputs (`datasources.json` + `.env`). None is a *required read* — `data` has no producer-gated inputs (CONTRACT.md §1). |
| **Writes** | `DATA-MODEL.md` at the project root (latest approved truth; overwritten in place). The CSVs themselves are analyst-provided in `data/` (or the demo `scaffold/sample-data/`). |
| **STATE.md update** | Sets `data` = `approved`; flips every downstream `approved` step to `stale` on a re-run (CONTRACT.md §4.2). |
| **Entry gate** | Refuses to run until `init` is `approved` in `STATE.md` (CONTRACT.md §4.1). |
| **Next step** | `tableau-brand` (or `tableau-plan`, or `tableau-route` to confirm). |

## The two acquisition routes (and no third)

There are exactly **two** ways to get the mimicking CSVs (CONTRACT.md §3.2). There is
**no** synthesized/random-data path — when no real data exists, the floor is the
clearly-labelled `scaffold/sample-data/` demo, never invented rows.

- **Route 1 — `data_mode: csv` (default, zero-credential).** The analyst drops CSV
  file(s) in `data/`. **Each CSV is one data source.** This skill implements Route 1
  fully: profile → document → validate.
- **Route 2 — `data_mode: published-ds` (VizQL Data Service).** The analyst lists
  published sources in `datasources.json` and supplies Tableau creds in `.env`. This
  skill **detects** those inputs and reports them, but the VDS query itself is part of
  the published-ds work — it is **not** run here.

The mechanical guarantees — the entry gate, CSV profiling/type inference, the
header↔model validation, and the STATE.md transition — live in `data.py`, this
skill's executable mirror of the contract. Your job is the judgment part: confirming
which route applies and enriching each field's **Description** in `DATA-MODEL.md`.
Run the script at the three points below; do not hand-edit `STATE.md` yourself.

## How to run

1. **Precheck.** From the project directory, run:

   ```bash
   python "${CLAUDE_SKILL_DIR}/data.py" precheck "<project-dir>"
   ```

   (Use `python3` if `python` is unavailable.) If it prints `[BLOCKED]`, relay the
   reason and **stop** — the analyst must run `tableau-init` first. Otherwise note its
   signals: the csv source (production `data/`, demo `scaffold/sample-data/`, or
   `none`), whether Route 2 inputs are present, whether a `DATA-MODEL.md` already
   exists, and the current `data` status (a re-run).

2. **Branch on the situation** precheck reported:
   - **CSV available** (`data/` or the demo) → go to step 3 (profile).
   - **Route 2 only** (`datasources.json` + `.env`, no CSVs) → tell the analyst the
     published-ds route is detected and its VDS extraction is handled separately; to
     proceed now under CSV mode they can drop CSV(s) in `data/`. **Stop.**
   - **Nothing** → tell the analyst to either drop CSV(s) in `data/`, or add
     `datasources.json` + `.env` for the published-ds route. To just demo the
     workflow they can re-run `tableau-init` to lay down the `scaffold/sample-data/`
     examples. **Stop.**

3. **Profile.** Generate the field tables from the resolved CSVs:

   ```bash
   python "${CLAUDE_SKILL_DIR}/data.py" profile "<project-dir>"
   ```

   This infers a type per column and writes a schema-complete `DATA-MODEL.md`. It is
   **non-destructive**: if `DATA-MODEL.md` already exists it refuses (so prior
   descriptions aren't clobbered) — `Edit` it in place to refine, or re-run with
   `--force` to regenerate from the CSVs (e.g. after the data changed). If precheck
   said the source was the **demo** fallback, **tell the analyst** you're profiling
   demo data, not their real source.

4. **Enrich `DATA-MODEL.md`.** `Edit` each data source's field table to fill the
   **Description** column (and refine **Role** — `Dimension`/`Measure` — where the
   numeric heuristic guessed wrong). **Do not rename fields** — the documented field
   names must stay identical to the CSV headers (commit enforces this). Present the
   `DATA-MODEL.md` for approval.

5. **Commit** — only after the analyst approves:

   ```bash
   python "${CLAUDE_SKILL_DIR}/data.py" commit "<project-dir>"
   ```

   The script validates every documented field name against the real CSV header
   (exact match, case included). If it prints `[REFUSED]` naming missing/extra
   fields, fix the drift in `DATA-MODEL.md` (or the CSV) and re-run. On success it
   records `data` = `approved` and reports any downstream steps it marked `stale`.
   Relay the summary and tell the analyst to open a fresh conversation and run the
   next step (`tableau-route` to confirm).

## The DATA-MODEL.md schema

`profile` generates this; the model enriches only the Description (and Role) cells.

```markdown
## Acquisition
- tier: csv (provided in data/)        # or: csv (demo - scaffold/sample-data/)

## Data source: `sales_orders.csv`
- rows profiled: 40

| Field      | Type    | Role      | Sample values        | Description    |
|------------|---------|-----------|----------------------|----------------|
| order_id   | string  | Dimension | ORD-001, ORD-002     | <model fills>  |
| revenue    | real    | Measure   | 971.89, 1499.95      | <model fills>  |
```

- **One `## Data source:` section per CSV** (CONTRACT.md §3.2 — "csv = datasource").
- **Type** is one of `string`, `integer`, `real`, `date`, `datetime`, `boolean`.
- **Acquisition tier** is recorded so downstream steps know whether this is the
  analyst's real data or the demo fallback.
- `commit` re-parses the **Field** column and checks it against the CSV headers, so
  keep the table structure intact when enriching.

## Notes

- **Non-skippable.** Unlike `intake`/`brand`, `data` cannot be skipped — the pipeline
  has no field names to build against without it. `commit` only ever sets `approved`.
- **Latest-truth file.** `DATA-MODEL.md` lives at the project root and is overwritten
  in place; re-running flips downstream `approved` steps to `stale` (CONTRACT.md
  §4.2/§4.3). It does **not** create a version directory.
- **CSV-only here.** This step reads `*.csv`. Excel (`.xlsx`) is not profiled — export
  to CSV, or use the published-ds route.

> The full `STATE.md` schema and the ordering / staleness / versioning rules live in
> `CONTRACT.md` at the repo root. This skill restates only its own slice; `data.py`
> is the executable mirror of the contract it enforces.
