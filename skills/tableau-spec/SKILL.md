---
name: tableau-spec
description: Translates the approved HTML mock (mock.html) into an IMPLEMENTATION-SPEC.md for the tableau-dashboard-plugin workflow, mapping every mock element to a concrete Tableau construct so the build step never has to guess. Reconciles coverage the mirror of the mock's checklist so nothing is left unmapped, and applies a simplest-primitive guard defaulting each element to the simplest sufficient Tableau primitive and forcing an explicit justification for any escalation to an advanced feature (Dynamic Zone Visibility, LOD, table calculation, parameter action), so the workbook is not over-engineered. Emits a required Layout section (a fenced JSON container tree with canvas dimensions, nested vert/horz containers, and percentage sizes) so the build reproduces the mock's geometry instead of guessing it. Reads the approved mock.html and DASHBOARD-PLAN.md. Use when the user wants to write the implementation spec, map the mock to Tableau, or when tableau-route reports spec is next. Step 7 of 8 in the workflow.
disable-model-invocation: true
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(python *), Bash(python3 *)
---
# tableau-spec

Step 7 of 8, and **non-skippable**. It turns the approved `mock.html` into either a
machine-readable `IMPLEMENTATION-SPEC.md` for `tableau-build`, or a Desktop build guide
for an analyst who will author in Tableau Desktop. Choose the route once via `spec_mode`.

| | |
|---|---|
| **Reads** | **Required:** `mock-version/<v_N>/mock.html` (from `mock`); `DASHBOARD-PLAN.md` (from `plan`). |
| **Writes** | **Agent:** `mock-version/<v_N>/IMPLEMENTATION-SPEC.md`. **Human:** root `IMPLEMENTATION-SPEC.md` + `spec/` tree. |
| **STATE.md update** | Records `spec_mode` (`agent` \| `human`) on first run via `set-mode`; sets `spec` = `approved`; on human also sets `build` = `skipped`. On agent re-run, flips an `approved` `build` to `stale`. Does **not** bump `current_version`. |
| **Entry gate** | `plan` + `DASHBOARD-PLAN.md` resolved; `mock` + `mock.html` at `current_version`. Agent route also blocks multi-view plans (one-dashboard builder limit). |
| **Next step** | Agent → `tableau-build`. Human → pipeline complete (`build` skipped). |

## Spec mode gate (`spec_mode`) — do this first

| mode | Meaning |
|------|---------|
| `agent` | Machine spec (Element Mapping + `## Layout`) for `tableau-build`. **Default when unset.** |
| `human` | Desktop build guide (root guide + `spec/` tree). `tableau-build` is skipped. |

1. Run precheck — it reports the recorded `spec_mode`, or that it is unset:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-spec/scripts/spec.py" precheck "<project-dir>"
   ```

2. If unset, ask the analyst once (`agent` or `human`), then record it:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-spec/scripts/spec.py" set-mode "<project-dir>" --mode agent
   ```

   (or `--mode human`). Do **not** hand-edit `STATE.md`. Later runs do not ask again.

Detail for each route is below; templates hold the full shape.

## Agent route

Author `mock-version/<v_N>/IMPLEMENTATION-SPEC.md` from
`references/IMPLEMENTATION-SPEC-TEMPLATE.md`.

- One **Element Mapping** row per mock `data-plan-id` (exact id match).
- **`## Layout`** fenced JSON container tree (canvas, vert/horz, percentage sizes).
- Default to the **simplest** Tableau primitive; justify Dynamic Zone Visibility, LOD, table
  calc, or parameter action in the justification cell.
- Equal siblings must be a container's only children.

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-spec/scripts/spec.py" validate "<project-dir>"
python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-spec/scripts/spec.py" commit "<project-dir>"
```

Show the reconciliation checklist to the analyst before asking for approval. Commit only
after they approve.

## Human route

Author a Desktop guide from `references/HUMAN-IMPLEMENTATION-SPEC-TEMPLATE.md`:

1. **Sheet names (one pass).** Derive the full Tableau sheet-name list from the plan (and
   mock titles). Confirm the **entire list in one interaction** — never one sheet at a time.
2. **Root `IMPLEMENTATION-SPEC.md`:** decision register with rationale, seams, numbered
   **build order** (parameters before calculated fields that read them; patterns before the
   sheets that instantiate them), verification checklist, open-items register.
3. **`spec/dashboards/<view>.md` per declared view:** prose tile table, sidebar blocks, sizing rules
   (range sizing with a minimum; tiled percentage containers; **never floating**), and
   per-sheet slot tables (Text / Colour / Rows / Columns / Filter / Tooltip).
4. **Coverage carrier:** every plan id (Elements, Filters, Interactions) must appear on an
   `Element: <id>` line somewhere in the tree. Show the coverage checklist to the analyst
   **before** asking for approval.
5. **Simplest-primitive:** any DZV / LOD / table calc / parameter action needs a written
   justification nearby.
6. **`spec/dashboards/shared-sidebar.md`** (optional) when header/sidebar/nav is shared across views.
7. **`spec/patterns.md`** only when two or more sheets share a `Pattern: <name>`; omit
   empty support pages rather than stubbing them.
7. Supporting pages as needed: `calculations.md`, `parameters.md`, `fields.md`.

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-spec/scripts/spec.py" commit "<project-dir>"
```

On success: `spec` = `approved`, `build` = `skipped`. Re-running mock still stales `spec`;
re-running spec overwrites the root guide in place.

## Notes

- Scripts are the executable contract. Do not hand-edit `STATE.md`.
- A worked human-route example lives under `demo/human-route/` (Sales Performance).
- Full schema: `CONTRACT.md` at the repo root.
