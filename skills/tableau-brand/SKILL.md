---
name: tableau-brand
description: Extracts design tokens (palette, type, spacing) into DESIGN-TOKENS.md for the tableau-dashboard-plugin workflow, so the mock and the Tableau workbook match the brand. Reads the analyst's branding/ (a branding.md spec and/or an org template .twb, plus logo/icons), or runs a short brand interview when none exists. Every value guessed from a Tableau default is flagged. Skippable only once branding/branding.md exists. Use when the user wants to set up branding/design tokens, or when tableau-route reports brand is next. Step 4 of 8 in the workflow.
disable-model-invocation: true
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(python *), Bash(python3 *)
---

# tableau-brand

Step 4 of 8. Turns the analyst's branding into a `DESIGN-TOKENS.md` — the single
source of truth for **palette, type, and spacing** that `tableau-mock` (the HTML
demo) and `tableau-build` (the `.twb`) both consume.

| | |
|---|---|
| **Reads** | The analyst's `branding/` (lowercase input): `branding/branding.md` (spec, preferred), an org template `branding/*.twb`, and any `branding/logo.*` / `branding/icons/`. Falls back to the `scaffold/branding/EXAMPLE-branding.md` demo. **No required read** — branding is an input, so this step never refuses for a "missing" file (CONTRACT.md §1). |
| **Writes** | `DESIGN-TOKENS.md` at the project root (latest approved truth; overwritten in place). May also author `branding/branding.md` from a scraped `.twb` or the brand interview. |
| **STATE.md update** | Sets `brand` = `approved` (tokens authored) or `skipped`; flips every downstream `approved` step to `stale` on a re-run (CONTRACT.md §4.2). |
| **Entry gate** | Refuses to run until `init` is `approved` in `STATE.md` (CONTRACT.md §4.1). |
| **Next step** | `tableau-plan` (or `tableau-route` to confirm). |

> **Tell the analyst this step matters.** Branding is what lets the later steps
> know *how to build the mock* and *how to write a precise Tableau implementation
> spec*. Don't skimp on the palette, the fonts, the padding, the sizing — vague
> branding here produces a bland, generic dashboard downstream. This is why the step
> can only be **skipped once `branding/branding.md` exists** (see below): the analyst
> must capture at least some brand intent before opting into neutral styling.

The mechanical guarantees — the entry gate, the `DESIGN-TOKENS.md` schema check, the
skip precondition, and the STATE.md transition — live in `brand.py`, this skill's
executable mirror of the contract. Your job is the part that needs judgment: reading
the branding (or interviewing for it) and authoring the tokens. Run the script at the
two points below; do not hand-edit `STATE.md` yourself.

## How to run

1. **Precheck.** From the project directory, run:

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/brand.py" precheck "<project-dir>"
   ```

   (Use `python3` if `python` is unavailable.) If it prints `[BLOCKED]`, relay the
   reason and **stop** — the analyst must run `tableau-init` first. Otherwise note
   its signals: whether a `DESIGN-TOKENS.md` already exists (refine vs overwrite),
   the **source mode**, any logo/icon assets to integrate, and whether skip is
   allowed.

2. **Refine vs overwrite.** If precheck reports `DESIGN-TOKENS.md` already exists,
   use **AskUserQuestion** to offer **Refine** (keep its structure, update values),
   **Overwrite** (author fresh), or **Skip** (only if allowed). **Never silently
   overwrite** existing tokens.

3. **Follow the source mode** precheck reported:

   | source mode | what to do |
   |-------------|------------|
   | `spec` | Extract tokens from `branding/branding.md`. |
   | `spec+twb` | Scrape the org `branding/*.twb` and use it to **enrich** the existing `branding/branding.md` (don't discard the analyst's spec), then build tokens. |
   | `twb` | Scrape the org `branding/*.twb`, **write** `branding/branding.md` from what you find, then build tokens. |
   | `scaffold` | Only the `scaffold/` demo example exists — **say so** (you're demoing, not using real brand input). |
   | `none` | Run the **brand interview** (next section), write `branding/branding.md` from the answers, then build tokens. |

   **When a `.twb` is involved:** before reading it, **ask the analyst to confirm
   the workbook is as thin as possible** (a minimal template — ideally one styled
   sheet/dashboard, no large data extracts or dozens of sheets). A heavy `.twb`
   floods the context with irrelevant XML. Scrape only what feeds design tokens:
   fonts, colors (fills, text, series palette), sizing, container/spacing structure,
   and any embedded logo (`<zone type='bitmap'>`).

4. **Brand interview (source mode `none` only).** When there is no branding input at
   all, **do not** leave the brand blank — interview the analyst, borrowing the
   `grill-me` approach but **bounded to at most 10 questions**. Ask one at a time,
   propose a recommended default for each (from
   `references/tableau-design-tokens.md`), and choose the questions that recover the
   most token-relevant information: primary/secondary colors, a cohesive ≤5-color
   series palette, font family (default the Tableau family), title vs label sizing,
   card padding / section spacing, dashboard sizing mode, and whether a logo/icons
   exist. Prefer **AskUserQuestion** (multiple-choice with your recommendation
   first). Write the answers into `branding/branding.md`, then build tokens.

5. **Author `DESIGN-TOKENS.md`.** Write (or `Edit`, when refining) `DESIGN-TOKENS.md`
   at the project root using `references/DESIGN-TOKENS-TEMPLATE.md` as the structure.
   Fill palette/type/spacing from the branding source. **For every value the brand
   did not specify, pull the Tableau default from
   `references/tableau-design-tokens.md` and list it under `## Fallback Decisions`**
   — that section is how the analyst sees which visual choices were guessed (it is
   required; if nothing was guessed, write "None"). Integrate any logo/icons precheck
   found. Present the tokens for approval.

6. **Commit** — only after the analyst approves (or chooses to skip):

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/brand.py" commit "<project-dir>" --status approved
   # or, to skip the step (allowed only once branding/branding.md exists):
   python "${CLAUDE_SKILL_DIR}/scripts/brand.py" commit "<project-dir>" --status skipped
   ```

   On `--status approved` the script validates `DESIGN-TOKENS.md` has the required
   core; if it prints `[REFUSED]` listing missing sections, add them and re-run. On
   `--status skipped` it refuses unless `branding/branding.md` exists. On success it
   records the status and reports any downstream steps it marked `stale`. Relay the
   summary and tell the analyst to open a fresh conversation and run the next step
   (`tableau-plan`, or `tableau-route` to confirm).

## The DESIGN-TOKENS schema

`brand.py` enforces the **required core**; the rest is recommended-but-optional.

| section | required? | what goes there |
|---------|-----------|-----------------|
| `## Colors` | **required** | Backgrounds, accent bars, a cohesive ≤5-color series palette, text colors. |
| `## Typography` | **required** | Font family + sizes/weights for title, chart title, labels, tooltips. |
| `## Spacing` | **required** | Card padding, section spacing, container margins, accent-bar height. |
| `## Fallback Decisions` | **required** | Every value pulled from a Tableau default, flagged as guessed (or "None"). |
| `## Source` | optional | Which branding source produced these tokens. |
| `## Dashboard Sizing` | optional | Mode (default Range), min width/height, max (default Flex). |
| `## Logo` / `## Icons` | optional | Brand assets to integrate; omit when none. |

The validator matches headings as case-insensitive substrings and allows extra
custom sections, so a refined token file that keeps its own structure still passes.

## Notes

- **Skippable, but deliberately gated.** Skipping records `skipped` in `STATE.md`
  and yields neutral styling downstream (`mock`/`build` read `DESIGN-TOKENS.md` as an
  *optional* input — absent ⇒ neutral, CONTRACT.md §1). But skip is only offered once
  `branding/branding.md` exists, so the analyst can't bypass branding with zero brand
  intent on file.
- **Defaults worth keeping** (from `references/tableau-design-tokens.md`): the native
  **Tableau** font family (Bold/Medium/Light); a **cohesive ≤5-color** palette (a
  dashboard speaks one color language); **Range** sizing with no max (Flex), not
  Automatic; worksheet **headers hidden** with the visible title as a **Text object**;
  a collapsible filter panel via a **Show/Hide button**.
- **Root file, no version bump.** Re-running refines the root `DESIGN-TOKENS.md` in
  place and flips downstream `approved` steps to `stale`; it does **not** create a new
  version directory (`DESIGN-TOKENS.md` is a root "latest truth" file, CONTRACT.md §4.3).

> The full `STATE.md` schema and the ordering / staleness / versioning rules live in
> `CONTRACT.md` at the repo root. This skill restates only its own slice; `brand.py`
> is the executable mirror of the contract it enforces.
