---
name: tableau-plan
description: Produces the strict DASHBOARD-PLAN.md for the tableau-dashboard-plugin workflow — the complete blueprint tableau-mock and tableau-build build against. Decides screen size here (so slot sizing is correct downstream), lays out a grid of named slots, and assigns every KPI, chart, filter, and interaction a stable id that later steps reference. Reads DATA-MODEL.md (field names) and, if present, PRD.md and DESIGN-TOKENS.md; proposes extra KPIs and patterns beyond the literal request. Use when the user wants to plan the dashboard, lay out KPIs/charts/filters, or when tableau-route reports plan is next. Step 5 of 8 in the workflow.
disable-model-invocation: true
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(python *), Bash(python3 *)
---

# tableau-plan

Step 5 of 8, and **non-skippable**. It turns the request and the data model into a
strict, complete `DASHBOARD-PLAN.md` — the single blueprint `tableau-mock` (the HTML
demo) and, downstream, `tableau-build` (the `.twb`) build against. The plan is
deliberately strict so the **mock can never silently drop a requirement**: screen size,
the layout grid, and every element/filter/interaction are pinned down here, each with a
**stable id** that later steps reference.

| | |
|---|---|
| **Reads** | **Required:** `DATA-MODEL.md` (from `data`) — the real field names to plan against. **Optional (enrich, never block):** `PRD.md` (from `intake`; falls back to root `DASHBOARD-REQUEST.md`, then the `scaffold/` demo) and `DESIGN-TOKENS.md` (from `brand`; absent ⇒ neutral styling). |
| **Writes** | `DASHBOARD-PLAN.md` at the project root (latest approved truth; overwritten in place). |
| **STATE.md update** | Sets `plan` = `approved`; flips every downstream `approved` step (`mock`/`spec`/`build`) to `stale` on a re-run (CONTRACT.md §4.2). |
| **Entry gate** | Refuses to run until `data` is resolved **and** `DATA-MODEL.md` exists (CONTRACT.md §4.1). |
| **Next step** | `tableau-mock` (or `tableau-route` to confirm). |

The mechanical guarantees — the entry gate, the strict-schema check (required sections,
stable unique ids, shared interaction vocabulary), and the STATE.md transition — live in
`plan.py`, this skill's executable mirror of the contract. Your job is the judgment part:
designing a layout that fits the request and the screen, mapping elements to real fields,
and **proposing extra value** beyond what was asked. Run the script at the points below;
do not hand-edit `STATE.md`.

## How to run

1. **Precheck.** From the project directory, run:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/plan.py" precheck "<project-dir>"
   ```

   (Use `python3` if `python` is unavailable.) If it prints `[BLOCKED]`, relay the
   reason and **stop** — the analyst must run `tableau-data` first (plan needs
   `DATA-MODEL.md`). Otherwise note its signals: whether a `DASHBOARD-PLAN.md` already
   exists (refine vs overwrite), the **requirements source** (`PRD.md`, the raw request,
   the `scaffold/` demo, or none), and whether `DESIGN-TOKENS.md` is present.

2. **Refine vs overwrite.** If precheck reports a `DASHBOARD-PLAN.md` already exists, use
   **AskUserQuestion** to offer **Refine** (keep its ids/structure, update values) or
   **Overwrite** (author fresh). **Never silently overwrite** the analyst's plan — its
   ids are referenced by any mock/spec/build already built.

3. **Read the inputs.** Always read `DATA-MODEL.md` for the exact field names and types
   (use them verbatim in the `columns` cells). Read the **requirements source** precheck
   reported for what to build; if it was the `scaffold/` demo, **say so** (you're demoing,
   not using a real request). If `DESIGN-TOKENS.md` is present, let it inform layout
   density and sizing; if absent, plan for neutral styling.

4. **Decide the screen size first.** Pick `mode` (fixed / range / automatic) and the
   design `dimensions`, and record the rationale (primary device / embed target). Every
   slot size is relative to this canvas, so it is decided **here**, not in the mock.

5. **Author `DASHBOARD-PLAN.md`.** Write (or `Edit`, when refining) the plan at the
   project root using `references/DASHBOARD-PLAN-TEMPLATE.md` as the structure. It must
   contain every **required** section:

   | section | what goes there |
   |---------|-----------------|
   | `## Screen Size` | Mode + design dimensions + rationale. |
   | `## Layout Grid` | Named slots with position + size (the `slot` ids elements reference). |
   | `## Elements` | Per-element table `id \| type \| columns \| slot \| size` — every KPI and chart. |
   | `## Filters` | `id \| field \| control type \| scope \| default` — every filter (a single `none` row if there are none). |
   | `## Interactions` | `id \| interaction \| source \| target \| detail` — the `interaction` term **must** come from the shared vocabulary below. |

   Plus the recommended `## Summary` and `## Suggestions` (and optional `## Data Gaps`).

   **Stable ids are the contract.** Give every KPI, chart, filter, and interaction a
   short, stable, unique id (e.g. `kpi-revenue`, `chart-trend`, `flt-region`,
   `int-region-filter`). Later steps reference these — don't renumber them on a refine.

6. **Propose extra value.** Beyond the literal request, suggest additional KPIs and
   interesting patterns the data supports (a discount-impact view, a segment mix, a
   target-vs-actual delta) in `## Suggestions`. Surface anything the request wants that
   the data **cannot** support under `## Data Gaps` — better visible here than dropped in
   the mock.

7. **Self-check, then present.** Validate the draft before showing it:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/plan.py" validate "<project-dir>"
   ```

   If it prints `[INVALID]`, fix the named sections/ids/terms and re-run. When it prints
   `[OK]`, present the plan to the analyst for approval.

8. **Commit** — only after the analyst approves:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/plan.py" commit "<project-dir>"
   ```

   Commit re-validates the plan (plan is non-skippable, so it only ever sets `approved`).
   If it prints `[REFUSED]`, fix what it names and re-run. On success it records `plan` =
   `approved` and reports any downstream steps it marked `stale`. Relay the summary and
   tell the analyst to open a fresh conversation and run the next step (`tableau-mock`, or
   `tableau-route` to confirm).

## Shared interactions vocabulary (CONTRACT.md §6)

The `interaction` column must use one of these intent-level terms, so `plan` → `mock` →
`spec` all mean the same thing. The Tableau construct is chosen later, in `spec`.

| term | meaning |
|------|---------|
| `toggle panel` | Show/hide a region of the dashboard on demand. |
| `swap view` | Replace one chart with an alternative in the same slot (bar ⇄ line). |
| `drill` | Move between levels of a hierarchy (year → quarter → month) in place. |
| `cross-filter` | Selecting marks in one chart filters the others. |
| `highlight` | Selecting marks highlights related marks elsewhere without filtering. |
| `parameter swap` | A control changes a measure/dimension/threshold across the dashboard. |

If a requested interaction fits none of these, add the term to **CONTRACT.md §6 first**
(with its intent and Tableau construct), then use it here — keep plan, mock, and spec
aligned.

## Notes

- **Non-skippable.** Unlike `intake`/`brand`, `plan` cannot be skipped — the mock and
  workbook have no blueprint without it. `commit` only ever sets `approved`.
- **Root file, no version bump.** `DASHBOARD-PLAN.md` lives at the project root and is
  overwritten in place; re-running flips downstream `approved` steps to `stale`
  (CONTRACT.md §4.2/§4.3). It does **not** create a version directory.
- **Plan against real fields.** The `columns` cells must use the exact field names from
  `DATA-MODEL.md` — that's how the mock and the `.twb` line up with the data, and how
  **Replace Data Source** swaps in live data later.

> The full `STATE.md` schema and the ordering / staleness / versioning rules live in
> `CONTRACT.md` at the repo root. This skill restates only its own slice; `plan.py` is
> the executable mirror of the contract it enforces.
