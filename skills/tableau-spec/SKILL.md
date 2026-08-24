---
name: tableau-spec
description: Translates the approved HTML mock (mock.html) into an IMPLEMENTATION-SPEC.md for the tableau-dashboard-plugin workflow, mapping every mock element to a concrete Tableau construct so the build step never has to guess. Reconciles coverage the mirror of the mock's checklist so nothing is left unmapped, and applies a simplest-primitive guard defaulting each element to the simplest sufficient Tableau primitive and forcing an explicit justification for any escalation to an advanced feature (Dynamic Zone Visibility, LOD, table calculation, parameter action), so the workbook is not over-engineered. Emits a required Layout section (a fenced JSON container tree with canvas dimensions, nested vert/horz containers, and percentage sizes) so the build reproduces the mock's geometry instead of guessing it. Reads the approved mock.html and DASHBOARD-PLAN.md. Use when the user wants to write the implementation spec, map the mock to Tableau, or when tableau-route reports spec is next. Step 7 of 8 in the workflow.
disable-model-invocation: true
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(python *), Bash(python3 *)
---

# tableau-spec

Step 7 of 8, and **non-skippable**. It turns the approved `mock.html` into an
`IMPLEMENTATION-SPEC.md` that maps **every** mock element to a concrete Tableau construct,
so `tableau-build` builds from an explicit spec instead of guessing. It ends with a
**coverage reconciliation** — the mirror of the mock's checklist — proving every mock
element is mapped, and applies a **simplest-primitive guard** so the workbook uses the
simplest construct that does the job.

| | |
|---|---|
| **Reads** | **Required:** `mock-version/<v_N>/mock.html` (from `mock`) — the elements to map, tagged with `data-plan-id`; and `DASHBOARD-PLAN.md` (from `plan`) — the shared element/filter/interaction ids and the interaction intents (CONTRACT.md §6). |
| **Writes** | `mock-version/<v_N>/IMPLEMENTATION-SPEC.md` — a standalone deliverable copy (CONTRACT.md §4.3). |
| **STATE.md update** | Sets `spec` = `approved`; flips a downstream `approved` `build` step to `stale` on a re-run (CONTRACT.md §4.2). Does **not** touch `current_version` — only `tableau-mock` bumps it (§4.3). |
| **Entry gate** | Refuses to run until `plan` is resolved **and** `DASHBOARD-PLAN.md` exists, **and** `mock` is resolved **and** `mock.html` exists at `current_version` (CONTRACT.md §4.1). |
| **Next step** | `tableau-build` (or `tableau-route` to confirm). |

The mechanical guarantees — the entry gate, the **coverage reconciliation** (every mock
element mapped, nothing unmapped), the **simplest-primitive guard** (any advanced feature
carries a justification), the version bump, and the STATE.md transition — live in
`spec.py` (CLI) and `reconcile.py` (the reconciliation + guard core). Your job is the
judgment part: choosing the right, simplest Tableau construct for each element and
justifying any escalation. Run the script at the points below; do not hand-edit `STATE.md`.

## The two conventions the reconciliation depends on

`reconcile.py` decides "mapped" and "laid out" mechanically, so the spec **must** carry a
single **Element Mapping table** and a **Layout section** (see
`references/IMPLEMENTATION-SPEC-TEMPLATE.md`):

- The table's first column header is **`id`** and it has a column whose header contains
  **`construct`** and one whose header contains **`justif`**.
- **One row per mock element**, with the `id` matching a `data-plan-id` from `mock.html`
  **exactly**. A mock id with no row is an **unmapped element** that blocks approval — this
  is what makes "nothing unmapped" a guarantee.
- The **construct** cell names the Tableau construct; the **justification** cell explains
  any escalation (leave it blank / `-` for a simple primitive).
- The **`## Layout` section** holds a short summary and a fenced JSON **container tree**
  derived from the approved mock: the mock's `canvas` dimensions, nested `vert`/`horz`
  containers, and element-id leaves with percentage `size`s (siblings sum to ~100). Every
  mapped **zone** id appears **exactly once**; interaction ids (`int-*`) are actions, not
  zones, and never appear. This is how the mock's geometry reaches `tableau-build` — a
  missing or inconsistent Layout blocks approval exactly like an unmapped element.
- **Siblings meant to stay equal must be a container's only children.** Tableau holds a row
  of equal cards equal by distributing the *container* evenly, so an equal group stranded
  beside a smaller sibling (three chart cards above a 3% legend strip) cannot be held — the
  build pins every child but the biggest, and the group drifts apart on any dashboard taller
  than its minimum. Wrap the group in its own container; the validator flags this.

## The simplest-primitive guard

Default every element to the **simplest sufficient** Tableau primitive. Only escalate to an
advanced feature when the simpler option genuinely cannot do the job — and when you do,
**write why in the justification cell** (what simpler alternative you rejected and the
concrete reason). The guard flags these advanced features when their justification is blank:

| advanced feature | simpler default to justify against |
|------------------|-------------------------------------|
| **Dynamic Zone Visibility (DZV)** | a show/hide button container |
| **LOD expression** (`{FIXED ...}`) | a plain aggregate (`SUM`, `AVG`) or the view's default grain |
| **table calculation** (`WINDOW_*`, `RUNNING_*`, `INDEX()`, `RANK`) | a native aggregate or quick table calc on the view |
| **parameter action** | a filter action / highlight action, or a static parameter |

The concrete "what's simplest for this interaction" knowledge lives in the validated
snippet library. Map the shared interaction terms to their **simplest** construct first
(CONTRACT.md §6): `cross-filter` → a **Filter action** (`Use as Filter`); `highlight` → a
**Highlight action**; `drill` → a **hierarchy** expand/collapse; `swap view` / `toggle
panel` / `parameter swap` → these legitimately need DZV / parameter — justify them.

## How to run

1. **Precheck.** From the project directory, run:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/spec.py" precheck "<project-dir>"
   ```

   (Use `python3` if `python` is unavailable.) If it prints `[BLOCKED]`, relay the reason
   and **stop** — the analyst must resolve the named upstream step (`tableau-mock` for the
   approved mock, `tableau-plan` for the blueprint) first. Otherwise note its signals: the
   **mock path** to map from, the **list of element ids** every one of which needs a
   mapping row, and the **target path** to write `IMPLEMENTATION-SPEC.md` into (the mock's
   `current_version` — spec writes beside the `mock.html` it maps and never bumps the version).

2. **Read the inputs.** Read `mock.html` at the reported mock path — its `data-plan-id`
   attributes are your exact mapping list. Read `DASHBOARD-PLAN.md` for what each id *is*
   (KPI / chart kind / filter / interaction) and the interaction intents, so you map to the
   right construct.

3. **Author `IMPLEMENTATION-SPEC.md`** at the precheck's **target path**. Fill the Element
   Mapping table with one row per mock element, defaulting to the simplest primitive and
   justifying every escalation. Write the **`## Layout` section** by reading the mock's
   actual geometry (its rows, columns, and nesting) into the JSON container tree — sizes
   as percentages of the parent, canvas from the plan's Screen Size. Add the supporting
   detail the build needs (calculated fields, data source / joins, actions, parameters)
   following the template. When **refining** an existing spec at this version, `Edit` it
   in place.

4. **Self-check, then present.** Validate the draft before showing it:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/spec.py" validate "<project-dir>"
   ```

   It prints the **reconciliation checklist** (one line per mock element, each `[x]` mapped
   or `[ ]` unmapped / unjustified-escalation), the guard result, and the **layout check**
   (the container tree present and consistent with the mapping). If it prints `[INVALID]`,
   map the missing element(s) / add the missing justification(s) / fix the named layout
   problem(s) and re-run.
   When it prints `[OK]`, **show the checklist to the analyst** (it's the proof nothing was
   dropped and nothing is over-engineered) and present the spec for approval.

5. **Commit** — only after the analyst approves:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/spec.py" commit "<project-dir>"
   ```

   Commit re-runs the full reconciliation + guard (spec is non-skippable, so it only ever
   sets `approved`). If it prints `[REFUSED]`, fix what the checklist names and re-run. On
   success it records `spec` = `approved`, sets `current_version` to the target `v_N`, and
   reports any downstream step it marked `stale`. Relay the summary and tell the analyst to
   open a fresh conversation and run the next step (`tableau-build`, or `tableau-route` to
   confirm).

## Notes

- **Non-skippable.** The build needs an explicit spec; `commit` only ever sets `approved`.
- **Versioned deliverable.** `IMPLEMENTATION-SPEC.md` lives under `mock-version/<v_N>/`
  beside its `mock.html` (CONTRACT.md §4.3). Spec writes into the mock's `current_version`
  and **overwrites in place** on a re-run; it never bumps `current_version`. A new spec
  version is created by re-running `tableau-mock` (which bumps and stales spec). The target
  path is reported by `precheck`.

> The full `STATE.md` schema and the ordering / staleness / versioning rules live in
> `CONTRACT.md` at the repo root. This skill restates only its own slice; `spec.py` /
> `reconcile.py` are the executable mirror of the contract it enforces.
