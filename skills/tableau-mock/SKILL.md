---
name: tableau-mock
description: Renders the interactive HTML demo (mock.html) for the tableau-dashboard-plugin workflow at the plan's screen size, populated from the real sample CSVs, so the analyst can share a realistic demo and validate direction before any Tableau work. Builds strictly from DASHBOARD-PLAN.md — every KPI, chart, filter, and interaction — and ends with a coverage checklist proving each plan element (screen size, every filter, every interaction) was rendered, so omissions are visible before the stakeholder sees it. Enforces readable slot sizing (no compressed, out-of-bounds, or empty-space-heavy charts). Reads DASHBOARD-PLAN.md and the sample CSVs, and DESIGN-TOKENS.md if present. Use when the user wants to build the mock, render the demo, or when tableau-route reports mock is next. Step 6 of 8 in the workflow.
disable-model-invocation: true
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(python *), Bash(python3 *)
---

# tableau-mock

Step 6 of 8, and **non-skippable**. It turns the strict `DASHBOARD-PLAN.md` into an
interactive `mock.html` demo — rendered at the plan's screen size and populated from the
**real sample CSVs** — that the analyst shares so the stakeholder can validate the
dashboard direction *before any Tableau work begins*. The mock ends with a **coverage
checklist** proving every plan element was rendered, so nothing is silently dropped
before the demo goes out.

| | |
|---|---|
| **Reads** | **Required:** `DASHBOARD-PLAN.md` (from `plan`) — the blueprint, including the screen size and every element/filter/interaction id; and the sample CSVs (`data/*.csv`, demo fallback `scaffold/sample-data/*.csv`) — the real rows the demo shows. **Optional (enrich, never block):** `DESIGN-TOKENS.md` (from `brand`; absent ⇒ neutral styling). |
| **Writes** | `mock-version/<v_N>/mock.html` — a full standalone deliverable copy (CONTRACT.md §4.3). |
| **STATE.md update** | Sets `mock` = `approved` and `current_version` to the target `v_N`; flips every downstream `approved` step (`spec`/`build`) to `stale` on a re-run (CONTRACT.md §4.2). |
| **Entry gate** | Refuses to run until `plan` is resolved **and** `DASHBOARD-PLAN.md` exists, **and** `data` is resolved **and** a sample CSV exists (CONTRACT.md §4.1). |
| **Next step** | `tableau-spec` (or `tableau-route` to confirm). |

The mechanical guarantees — the entry gate, the **coverage checklist** (every plan
element rendered), the **slot-sizing guard** (no compressed / out-of-bounds /
empty-space-heavy layouts), the version bump, and the STATE.md transition — live in
`mock.py` (CLI) and `coverage.py` (the checklist + guard core). Your job is the judgment
part: designing a faithful, attractive demo that renders the plan at its screen size with
real data. Run the script at the points below; do not hand-edit `STATE.md`.

## The two conventions the coverage check depends on

`coverage.py` decides "rendered" mechanically, so the mock **must** carry two machine-readable
markers (see `references/MOCK-SKELETON.html` for a working example):

1. **`data-plan-id="<id>"`** on every rendered KPI, chart, filter control, and interaction
   trigger. The `<id>` must match the plan's Elements / Filters / Interactions tables
   **exactly**. A plan id with no matching `data-plan-id` is a **coverage gap** that blocks
   approval — this is what makes "every element rendered" a guarantee, not a hope.
2. **An embedded JSON layout manifest** — one `<script type="application/json" id="mock-layout">`
   block giving the `canvas` size and a pixel box `{id, x, y, width, height}` for every
   **element** id (KPIs/charts; filters and interactions need no box). The slot-sizing guard
   reads it; the `canvas` width/height **must equal** the plan's Screen Size dimensions.

## How to run

1. **Precheck.** From the project directory, run:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-mock/scripts/mock.py" precheck "<project-dir>"
   ```

   (Use `python3` if `python` is unavailable.) If it prints `[BLOCKED]`, relay the reason
   and **stop** — the analyst must resolve the named upstream step (`tableau-plan` for the
   blueprint, `tableau-data` for the samples) first. Otherwise note its signals: the
   **canvas size** to render at, the **coverage targets** (element/filter/interaction
   counts), the **target version path** to write `mock.html` into (and whether this run
   **bumps the version** because the mock was already approved), and whether
   `DESIGN-TOKENS.md` is present.

2. **Read the inputs.** Read `DASHBOARD-PLAN.md` in full — its Screen Size, Layout Grid,
   Elements, Filters, and Interactions tables are your build list; use the ids verbatim.
   Read the **sample CSVs** (`data/*.csv`, or `scaffold/sample-data/*.csv` — if you fall
   back to the demo samples, **say so**) and populate every chart/KPI from the real rows —
   do not invent numbers. If `DESIGN-TOKENS.md` is present, apply its colors/typography;
   if absent, use neutral styling.

3. **Author `mock.html`** at the precheck's **target version path**
   (`mock-version/<v_N>/mock.html`). Render at the plan's screen size, lay out every
   element in its planned slot, wire the filters and interactions, and tag everything with
   `data-plan-id` + embed the `mock-layout` manifest (see the two conventions above and
   `references/MOCK-SKELETON.html`). When **refining** an existing mock at this version,
   `Edit` it in place. Keep the file **self-contained** (inline CSS/JS; no external fetches)
   so the analyst can open and share it directly.

4. **Slot sizing.** Give each element a readable box inside the canvas — not so small it
   compresses (the guard rejects boxes under ~80×56px), not overflowing the canvas
   (out-of-bounds), and collectively filling the canvas (the guard rejects layouts that
   leave it mostly empty). Make the manifest boxes match what you actually render.

5. **Self-check, then present.** Validate the draft before showing it:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-mock/scripts/mock.py" validate "<project-dir>"
   ```

   It prints the **coverage checklist** (one line per screen size / element / filter /
   interaction, each `[x]` rendered or `[ ] <- MISSING`) and the **slot-sizing guard**
   result. If it prints `[INVALID]`, fix the flagged gaps/violations and re-run. When it
   prints `[OK]`, **show the checklist to the analyst** (it's the proof nothing was
   dropped) and present the mock for approval.

6. **Commit** — only after the analyst approves:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-mock/scripts/mock.py" commit "<project-dir>"
   ```

   Commit re-runs the full coverage + guard check (mock is non-skippable, so it only ever
   sets `approved`). If it prints `[REFUSED]`, fix what the checklist names and re-run. On
   success it records `mock` = `approved`, sets `current_version` to the target `v_N`, and
   reports any downstream steps it marked `stale`. Relay the summary and tell the analyst
   to open a fresh conversation and run the next step (`tableau-spec`, or `tableau-route`
   to confirm).

## Filter control types

The plan's Filters table names a `control type` per filter. Render each as the Tableau
control it stands for, not as a bare browser widget. Never use a native `<select multiple>`
list box: it needs Ctrl+click and applies on every change.

| control type | render in the mock as |
|--------------|------------------------|
| `dropdown (multi)` / `multi-select` | a button showing the selection summary ("All", "2 of 5") that opens a checkbox list with an **(All)** row and an **Apply** button; the filter applies on Apply, not on each tick. |
| `dropdown (single)` / `single-select` | a native `<select>` (one value, applies on change). |
| `date range` | two `<input type="month">` (or `date`) pickers, from and to. |
| `slider` | a native `<input type="range">` with the current value shown. |

When the plan puts filters in a sidebar slot, render it as a **collapsible** panel with a
Show/Hide control (see `references/MOCK-SKELETON.html`), unless the plan says otherwise.

## Shared interactions vocabulary (CONTRACT.md §6)

The plan's interactions use these intent-level terms; render each so its intent is
demonstrable in the demo (the Tableau construct is chosen later, in `spec`):

| term | render in the mock as |
|------|------------------------|
| `toggle panel` | a control that shows/hides a region. |
| `swap view` | a control that replaces one chart with an alternative in the same slot. |
| `drill` | click-through between hierarchy levels (year → quarter → month) in place. |
| `cross-filter` | clicking marks in one chart filters the others. |
| `highlight` | clicking/hovering marks highlights related marks elsewhere (no filtering). |
| `parameter swap` | a control that changes a measure/dimension/threshold across views. |

## Notes

- **Non-skippable.** The workflow has no demo without the mock; `commit` only ever sets
  `approved`.
- **Versioned deliverable.** `mock.html` lives under `mock-version/<v_N>/` (CONTRACT.md
  §4.3): re-running **after approval** bumps `current_version` to a fresh `v_N` and writes
  a full standalone copy there, preserving prior versions; re-running **before** approval
  overwrites the current `v_N`. The target path is reported by `precheck`.
- **Real data, real field names.** Populate from the sample CSVs and reference the same
  field names the plan uses — that's how the demo lines up with the data model and, later,
  the workbook.

> The full `STATE.md` schema and the ordering / staleness / versioning rules live in
> `CONTRACT.md` at the repo root. This skill restates only its own slice; `mock.py` /
> `coverage.py` are the executable mirror of the contract it enforces.
