---
name: tableau-intake
description: Turns a free-form dashboard request into a structured PRD.md for the tableau-dashboard-plugin workflow. Reads the analyst's DASHBOARD-REQUEST.md (or text pasted into the terminal, or the scaffold/ demo example) and authors a schema-complete PRD.md. Optional and idempotent — if a PRD.md already exists it offers to refine rather than overwrite. Use when the user wants to structure a dashboard request, write a PRD, or when tableau-route reports intake is next. Step 2 of 8 in the workflow.
disable-model-invocation: true
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(python *), Bash(python3 *)
---

# tableau-intake

Step 2 of 8. Converts a free-form request into a structured `PRD.md` so the
downstream planning steps work from an unambiguous spec. The step is **optional**
(it can be skipped) and **idempotent** (re-running refines an existing PRD instead
of clobbering it).

| | |
|---|---|
| **Reads** | The request: production `DASHBOARD-REQUEST.md` (preferred) **or** text the analyst pastes **or** the `scaffold/EXAMPLE-DASHBOARD-REQUEST.md` demo example. None is a *required read* — the request can be pasted, so this step never refuses for a "missing" request file (CONTRACT.md §1). |
| **Writes** | `PRD.md` at the project root (latest approved truth; overwritten in place). |
| **STATE.md update** | Sets `intake` = `approved` (PRD authored) or `skipped`; flips every downstream `approved` step to `stale` on a re-run (CONTRACT.md §4.2). |
| **Entry gate** | Refuses to run until `init` is `approved` in `STATE.md` (CONTRACT.md §4.1). |
| **Next step** | `tableau-data` (or `tableau-route` to confirm). |

**Why optional?** `PRD.md` is a *structuring convenience*, not a hard dependency.
`tableau-plan` (step 5) reads it as an **optional** input and falls back to the raw
`DASHBOARD-REQUEST.md` when intake was skipped (CONTRACT.md §1), so skipping never
blocks the pipeline — it just means planning works from the unstructured request.

The mechanical guarantees — the entry gate, the PRD schema check, and the STATE.md
transition — live in `intake.py`, this skill's executable mirror of the contract.
Your job is the part that needs judgment: reading the free-form request and
authoring (or refining) the PRD prose. Run the script at the two points below; do
not hand-edit `STATE.md` yourself.

## How to run

1. **Precheck.** From the project directory, run:

   ```bash
   python "${CLAUDE_SKILL_DIR}/intake.py" precheck "<project-dir>"
   ```

   (Use `python3` if `python` is unavailable.) If it prints `[BLOCKED]`, relay the
   reason and **stop** — the analyst must run `tableau-init` first. Otherwise note
   its three signals: whether a `PRD.md` already exists, which request source to
   read, and the current `intake` status (a re-run).

2. **Refine vs. overwrite.** If precheck reports a `PRD.md` already exists, use
   **AskUserQuestion** to offer **Refine** (keep its structure, update the content),
   **Overwrite** (author fresh), or **Skip**. **Never silently overwrite** an
   existing `PRD.md`. If no PRD exists, author fresh (still offer Skip).

3. **Read the request.** Follow precheck's `request source`:
   - `DASHBOARD-REQUEST.md` — read the analyst's production request.
   - `scaffold/EXAMPLE-DASHBOARD-REQUEST.md` — the **demo** fallback; **tell the
     analyst** you're demoing the workflow, not using real input.
   - `none` — ask the analyst to paste their request text.

4. **Author `PRD.md`.** Write (or, when refining, `Edit`) `PRD.md` at the project
   root using `references/PRD-TEMPLATE.md` as the structure. Always include the
   required core — **Overview** and **Visualizations**. **Proactively propose**
   KPIs, Filters, and Additional Notes, since most dashboards want them — but if
   the analyst says a section isn't needed, omit it without friction. Don't
   complicate the PRD with empty sections. When **refining**, preserve the analyst's
   existing structure and custom sections; change only what the request updates.
   Present the PRD for approval.

5. **Commit** — only after the analyst approves (or chooses to skip):

   ```bash
   python "${CLAUDE_SKILL_DIR}/intake.py" commit "<project-dir>" --status approved
   # or, to skip the step:
   python "${CLAUDE_SKILL_DIR}/intake.py" commit "<project-dir>" --status skipped
   ```

   On `--status approved` the script validates `PRD.md` has the required core; if
   it prints `[REFUSED]` listing missing sections, add them and re-run. On success
   it records the status and reports any downstream steps it marked `stale`. Relay
   the summary and tell the analyst to open a fresh conversation and run the next
   step (`tableau-data`, or `tableau-route` to confirm).

## The PRD schema

`intake.py` enforces only the **required core**; the rest is recommended-but-optional.

| section | required? | what goes there |
|---------|-----------|-----------------|
| `## Overview` | **required** | Purpose, audience, update frequency. |
| `## Visualizations` | **required** | The charts and what each shows. |
| `## KPIs` | optional | Headline metrics with formulas; omit if the dashboard has none. |
| `## Filters` | optional | Slicing controls; omit if users don't need filtering. |
| `## Additional Notes` | optional | Branding, conditional formatting, sort orders, constraints. |

The validator matches headings as case-insensitive substrings and allows extra
custom sections, so a refined PRD that keeps its own structure still passes.

## Notes

- **Idempotent & non-blocking.** Skipping records `skipped` in `STATE.md` and never
  blocks the pipeline — downstream `plan` falls back to `DASHBOARD-REQUEST.md` and
  neutral handling (CONTRACT.md §1). Re-running refines the root `PRD.md` in place
  and flips downstream `approved` steps to `stale`; it does **not** create a new
  version directory (`PRD.md` is a root "latest truth" file, CONTRACT.md §4.3).
- **Do the data/branding work elsewhere.** Intake only structures the request.
  Data acquisition is `tableau-data`'s job and branding is `tableau-brand`'s.

> The full `STATE.md` schema and the ordering / staleness / versioning rules live in
> `CONTRACT.md` at the repo root. This skill restates only its own slice; `intake.py`
> is the executable mirror of the contract it enforces.
