---
name: tableau-route
description: Router for the tableau-dashboard-plugin workflow. Reads the project's STATE.md and reports which single skill the analyst should run next, honoring the ordering rule. Use when the user asks "what's next", "where am I in the workflow", or "which tableau skill do I run now". It only recommends — it never runs another skill itself.
disable-model-invocation: true
allowed-tools: Read, Bash(python *), Bash(python3 *)
---

# tableau-route

A thin router over the file-based workflow contract. It reads `STATE.md` in the
analyst's project and tells them **which one skill to run next** — nothing more.

> **This skill is a router only. It NEVER invokes another skill inline.** Running
> the next skill here would share this conversation's context and defeat the
> entire point of the plugin (each skill must run in its own fresh conversation).
> Your job ends at *recommending* the next skill and telling the analyst to run
> it themselves.

## How to run

1. Determine the analyst's project directory (the directory that contains, or
   should contain, `STATE.md`). Default to the current working directory.

2. Run the router script and capture its output:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-route/scripts/route.py" "<project-dir>"
   ```

   (Use `python3` if `python` is unavailable.) The script reads `STATE.md`,
   applies the ordering rule from `CONTRACT.md`, and prints the recommended next
   skill plus a one-line reason. It is pure and read-only — it does not modify
   any files.

3. **Relay the script's recommendation to the analyst**, then stop. Tell them to
   open a fresh conversation and invoke the recommended skill themselves (e.g.
   "Run `tableau-data` next"). Do **not** call the recommended skill yourself.

## Interpreting the result

The script reports one of four situations:

- **fresh project** (no `STATE.md`) → recommends `tableau-init` to scaffold the
  project.
- **a step is ready** → recommends that step's skill (the first step whose status
  is `pending` or `stale`, with its required inputs satisfied).
- **blocked** → a required upstream artifact is unresolved or missing on disk;
  the script points at the *upstream* skill to run first.
- **done** → every step is `approved` or `skipped`; point the analyst at their
  deliverables under `mock-version/v_N/`.

If `STATE.md` looks malformed or the recommendation is surprising, show the
analyst the relevant part of `STATE.md` and refer them to `CONTRACT.md` (the
canonical schema) rather than guessing.

> The full schema and the ordering / staleness / versioning rules live in
> `CONTRACT.md` at the repo root. This skill deliberately restates only what it
> needs; `route.py` is the executable mirror of that contract.
