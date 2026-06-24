---
name: tableau-data
description: Acquires the data for a tableau-dashboard-plugin project and writes the DATA-MODEL.md handoff artifact. CSV mode (the default, zero-credential path) profiles the analyst's data/*.csv into documented field names + types and validates that headers match exactly; the scaffold/sample-data/ demo CSVs are the fallback. The published-ds route (datasources.json + .env) samples published sources through the VizQL Data Service into data/*.csv. Use when the user wants to acquire or model dashboard data, build DATA-MODEL.md, or when tableau-route reports data is next. Step 3 of 8 in the workflow.
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
| **Reads** | The data: production `data/*.csv` (preferred) **or** the `scaffold/sample-data/*.csv` demo fallback. For the published-ds route, the inputs `datasources.json` + `.env` (the latter discovered by walking up — nearest wins). None is a *required read* — `data` has no producer-gated inputs (CONTRACT.md §1). |
| **Writes** | `DATA-MODEL.md` at the project root (latest approved truth; overwritten in place). The CSVs themselves are analyst-provided in `data/` (or the demo `scaffold/sample-data/`). |
| **STATE.md update** | Sets `data` = `approved`; flips every downstream `approved` step to `stale` on a re-run (CONTRACT.md §4.2). |
| **Entry gate** | Refuses to run until `init` is `approved` in `STATE.md` (CONTRACT.md §4.1). |
| **Next step** | `tableau-brand` (or `tableau-plan`, or `tableau-route` to confirm). |

## The two acquisition routes (and no third)

There are exactly **two** ways to get the mimicking CSVs (CONTRACT.md §3.2). There is
**no** synthesized/random-data path — when no real data exists, the floor is the
clearly-labelled `scaffold/sample-data/` demo, never invented rows.

- **Route 1 — `data_mode: csv` (default, zero-credential).** The analyst drops CSV
  file(s) in `data/`. **Each CSV is one data source.** `profile` → enrich → `commit`.
- **Route 2 — `data_mode: published-ds` (VizQL Data Service).** The analyst lists
  published sources in `datasources.json` (one entry each: `ds_name` + `project_name`)
  and supplies a Tableau connection in `.env`. `pull` signs in with the Personal Access
  Token and samples each source through the VDS — `read-metadata` (authoritative field
  names/types/descriptions) then `query-datasource` (a capped row sample) — writing one
  `data/<slug>.csv` per source plus `DATA-MODEL.md`. Then enrich → `commit`, exactly as
  Route 1. **Fires only when `data/` has no production CSVs** (real CSVs always win).

The mechanical guarantees — the entry gate, CSV profiling/type inference, the VDS pull,
the header↔model validation, and the STATE.md transition — live in `data.py` (with the
VDS client in `vds.py`), this skill's executable mirror of the contract. Your job is the
judgment part: confirming which route applies and enriching each field's **Description**
in `DATA-MODEL.md`. Run the script at the points below; do not hand-edit `STATE.md`.

## How to run

1. **Precheck.** From the project directory, run:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/data.py" precheck "<project-dir>"
   ```

   (Use `python3` if `python` is unavailable.) If it prints `[BLOCKED]`, relay the
   reason and **stop** — the analyst must run `tableau-init` first. Otherwise note its
   signals: the csv source (production `data/`, demo `scaffold/sample-data/`, or
   `none`), whether Route 2 inputs are present, whether a `DATA-MODEL.md` already
   exists, and the current `data` status (a re-run).

2. **Branch on the situation** precheck reported:
   - **CSV available** (`data/` or the demo) → go to step 3 (profile).
   - **Route 2** (`datasources.json` + `.env`, no production CSVs) → go to step 3a (pull).
     If `datasources.json` is present but `.env` is missing, tell the analyst to copy
     `scaffold/.env.example` to `.env` and fill in their Tableau connection first.
   - **Nothing** → tell the analyst to either drop CSV(s) in `data/`, or add
     `datasources.json` + `.env` for the published-ds route. To just demo the
     workflow they can re-run `tableau-init` to lay down the `scaffold/sample-data/`
     examples. **Stop.**

3a. **Pull (published-ds route only).** Sample the listed published sources via VDS:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/data.py" pull "<project-dir>"
   ```

   This signs in with the PAT, and for each source pulls metadata + a capped row sample,
   writing one `data/<slug>.csv` (slug = lowercased `ds_name`, e.g. `Regional Sales` →
   `regional_sales.csv`) and a schema-complete `DATA-MODEL.md` (tier
   `published-ds (VDS query)`, types/descriptions taken from VDS metadata). It also sets
   `data_mode: published-ds` in `STATE.md`. The row cap is `--row-limit` (default `100`,
   **silent up to `1000`**); a value **above `1000` is refused** until you confirm the
   larger sample with the analyst and re-run with `--confirm-large`. `pull` is
   **non-destructive** (refuses if `DATA-MODEL.md` exists; re-run with `--force` to
   re-sample). If it prints `[REFUSED]`, relay the actionable reason and **stop** — no
   artifact is written on failure (sign-in/connection error, the source's **API Access**
   capability is off, the named source is not a *published* source — it may be
   **embedded**, in which case export it to CSV and use Route 1 — or the query returned
   zero rows). On success, go to step 4 (enrich) — there is no separate `profile` step
   for this route.

3. **Profile (csv route only).** Generate the field tables from the resolved CSVs:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/data.py" profile "<project-dir>"
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
   python "${CLAUDE_SKILL_DIR}/scripts/data.py" commit "<project-dir>"
   ```

   The script validates every documented field name against the real CSV header
   (exact match, case included). If it prints `[REFUSED]` naming missing/extra
   fields, fix the drift in `DATA-MODEL.md` (or the CSV) and re-run. On success it
   records `data` = `approved` and reports any downstream steps it marked `stale`.
   Relay the summary and tell the analyst to open a fresh conversation and run the
   next step (`tableau-route` to confirm).

## The DATA-MODEL.md schema

`profile` (csv route) or `pull` (published-ds route) generates this; the model enriches
the Description (and refines Role) cells. For the published-ds route, Type and any
Description come pre-filled from authoritative VDS metadata.

```markdown
## Acquisition
- tier: csv (provided in data/)        # or: csv (demo - scaffold/sample-data/), or: published-ds (VDS query)

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
