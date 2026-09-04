---
name: tableau-init
description: Scaffolds a new tableau-dashboard-plugin project. Creates a scaffold/ folder of demo examples (EXAMPLE-DASHBOARD-REQUEST.md, EXAMPLE-datasources.json, .env.example, branding/EXAMPLE-branding.md, starter sample-data/) and initializes the STATE.md manifest, recording the target Tableau Desktop version so the build step never has to re-ask. Use when the user wants to start a new Tableau dashboard project, scaffold the workflow, or when tableau-route reports a fresh project. Step 1 of 8 in the workflow.
disable-model-invocation: true
allowed-tools: Read, Bash(python *), Bash(python3 *)
---

# tableau-init

The first step of the `tableau-dashboard-plugin` workflow. Running it in a project
directory scaffolds a complete starting skeleton and writes the `STATE.md` manifest
that every downstream skill reads to know where the analyst is.

This skill is **step 1 of 8**. It has no upstream dependencies — it is what creates
the workflow state in the first place.

| | |
|---|---|
| **Reads** | — (nothing; this step bootstraps the project) |
| **Writes** | `STATE.md` + a `scaffold/` folder of demo examples: `scaffold/EXAMPLE-DASHBOARD-REQUEST.md`, `scaffold/EXAMPLE-datasources.json`, `scaffold/.env.example`, `scaffold/branding/EXAMPLE-branding.md`, `scaffold/sample-data/*.csv` |
| **STATE.md update** | Creates the manifest with `init` = `approved` and steps 2–8 = `pending`; records `target_tableau_version`, `data_mode: csv`, `current_version: v_1` |
| **Next step** | `tableau-intake` (or `tableau-data` if the analyst skips intake) |

### scaffold/ examples vs. production inputs

Everything `init` writes under `scaffold/` is a **demo example** so the analyst
can trial the whole workflow before owning any real inputs. Every downstream skill
**prefers the production input at the project root** and only falls back to the
`scaffold/` example when the production file is absent:

| production input (preferred) | scaffold/ demo fallback |
|------------------------------|-------------------------|
| `DASHBOARD-REQUEST.md`       | `scaffold/EXAMPLE-DASHBOARD-REQUEST.md` |
| `datasources.json` + `.env` *(published-ds route)* | `scaffold/EXAMPLE-datasources.json` + `scaffold/.env.example` |
| `branding/branding.md`       | `scaffold/branding/EXAMPLE-branding.md` |
| `data/*.csv` *(csv route)*   | `scaffold/sample-data/*.csv` |

`init` deliberately creates **only** the `scaffold/` examples (and `STATE.md`),
never the production files — their absence is exactly what tells the analyst (and
the model) which real inputs are still owed. See CONTRACT.md §3 for the canonical
rule.

## How to run

1. **Determine the project directory.** Default to the current working directory.
   This is where the skeleton and `STATE.md` will be written.

2. **Ask for the target Tableau Desktop version** — unless the user already stated
   it. This is recorded once, here, and drives `tableau-build` later (it is never
   re-asked downstream). Offer exactly the two allowed values:

   - `2024.2-2025.x` — the broad-compatibility target (no rounded corners, no
     `<explain-data>` in the workbook).
   - `2026.1+` — the newest target (enables `border-radius` and newer XML).

   If unsure, recommend `2024.2-2025.x` for the widest compatibility.

3. **Run the scaffolder**, passing the chosen version:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-init/scripts/init.py" --target-version "<version>" "<project-dir>"
   ```

   (Use `python3` if `python` is unavailable.) The script copies this skill's
   `skeleton/` templates into the project's `scaffold/` folder and writes a
   schema-valid `STATE.md`. It is **non-destructive**: any file that already
   exists — including an existing `STATE.md` — is preserved, never overwritten, so
   re-running on an established project keeps the analyst's edits and pipeline
   progress.

4. **Relay the scaffolder's summary** to the analyst: what was created, what was
   preserved, and the one input to act on now — create their real
   `DASHBOARD-REQUEST.md` at the project root (copy `scaffold/`'s example, or just
   paste the request when running `tableau-intake`). Then tell them to open a
   fresh conversation and run the next step (`tableau-intake` to structure the
   request, or skip straight to `tableau-data` if they prefer). Suggest
   `tableau-route` if they want the router to confirm the next step.

   **Don't walk the analyst through data acquisition or branding here.** Those
   decisions belong to the skills that own them — `tableau-data` explains the two
   data routes (real CSVs in `data/`, or a published data source queried via the
   VizQL Data Service, with the `scaffold/sample-data/` demo as fallback) when the
   analyst reaches that step, and `tableau-brand` handles branding. Init only lays
   the examples down; it does not pre-explain how to fill them.

## Notes

- The `scaffold/` examples (including `scaffold/sample-data/*.csv`) let the project
  run a full demo immediately. How the analyst swaps in real data — into `data/` —
  is **`tableau-data`'s** topic, not init's; don't describe acquisition modes here.
- `target_tableau_version` is the one decision captured at init time. Everything
  else (the request, the data, branding) is filled in by the later steps that
  own those concerns.

> The full `STATE.md` schema and the ordering / staleness / versioning rules live
> in `CONTRACT.md` at the repo root. This skill restates only its own slice;
> `init.py` is the executable mirror of the schema it writes.
