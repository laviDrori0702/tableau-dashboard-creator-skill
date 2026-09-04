# Handoff — end-to-end test of tableau-dashboard-plugin v0.1.3

**For:** an agent in a **fresh session**, with the plugin installed from the marketplace
(not from this clone).
**Goal:** prove the plugin's own documented commands run on a machine that only has the
installed plugin — then fix the one known gap this test cannot cover.

---

## Why this test exists

Every `SKILL.md` in this plugin used to invoke its script through `${CLAUDE_SKILL_DIR}`,
which is **not a Claude Code variable**. It expanded to nothing, so all 26 invocations
resolved to a bare `/scripts/<name>.py` and failed on every machine. Commit
`fix/plugin-root-paths` changed all 26 to `${CLAUDE_PLUGIN_ROOT}/skills/<skill>/scripts/`.

The 659-test suite never caught this and **still cannot**: the tests import the script
modules directly, while the broken variable lived only in the `SKILL.md` prose that Claude
executes. That gap is the whole reason for a manual end-to-end run.

`${CLAUDE_PLUGIN_ROOT}` was chosen because all nine third-party plugins installed on the
author's machine use it — but every one of those uses it in a **hook** command. Whether
Claude Code also injects it into a **skill's** Bash calls is **unverified**. Step 1 settles
that and nothing else matters until it passes.

---

## Step 1 — The probe (do this first; stop if it fails)

`tableau-route` is the cheapest possible probe: standard-library only, one script,
read-only, and it prints a useful result even on an empty directory.

```bash
mkdir -p /tmp/plugin-probe && cd /tmp/plugin-probe
```

Now invoke the skill **as a user would** — `/tableau-route` — and let it run its own
documented command. Do **not** hand-substitute the path; the point is to watch the
variable expand.

**Pass:**

```
[NEXT] tableau-init (fresh project)
No STATE.md found - this is a fresh project. Run 'tableau-init' to scaffold it and initialize STATE.md.
```

**Fail — variable is empty:** `can't open file '/scripts/route.py'` or
`'C:\Program Files\Git\scripts\route.py'`. The leading directory is missing, which means
`${CLAUDE_PLUGIN_ROOT}` did not expand in a skill context.

### If it fails

Do not paper over it by substituting an absolute path — that reintroduces the original
bug. Find the variable Claude Code actually injects into skill Bash calls, then:

```bash
# from a clone of this repo
for skill in skills/*/; do
  name=$(basename "${skill%/}")
  sed -i "s|\${CLAUDE_PLUGIN_ROOT}/skills/$name/scripts/|\${THE_REAL_VAR}/scripts/|g" "$skill/SKILL.md"
done
```

Confirm with `git grep -c CLAUDE_PLUGIN_ROOT -- 'skills/*/SKILL.md'` (expect 26 across 9
files: brand 3, build 5, data 4, init 1, intake 3, mock 3, plan 3, route 1, spec 3).

---

## Step 2 — Walk the workflow on the CSV route

Use **Route 1 (`data_mode: csv`)** — it is the zero-credential path, so this needs no
Tableau account, no `.env`, and no network. Route 2 (published datasource) requires
`TABLEAU_*` credentials and Tableau Cloud or Server 2025.1+; leave it for a separate test.

The repo's `demo/` directory is a complete worked example — `demo/data/*.csv`,
`DASHBOARD-REQUEST.md`, `PRD.md`, `branding/`, and a finished `mock-version/v_1/`. Copy
its **inputs** into a scratch project and let the skills regenerate the outputs, so you
can diff yours against `demo/`'s.

```bash
mkdir -p /tmp/plugin-e2e/data
cp <clone>/demo/data/*.csv                  /tmp/plugin-e2e/data/
cp <clone>/demo/DASHBOARD-REQUEST.md        /tmp/plugin-e2e/
cp -r <clone>/demo/branding                 /tmp/plugin-e2e/
cd /tmp/plugin-e2e
```

Run each skill **in its own fresh conversation** — the plugin's core design constraint,
stated in `skills/tableau-route/SKILL.md`. One session per skill; `/tableau-route` between
any two to confirm the state machine agrees with you.

| # | Skill | Watch for |
|---|-------|-----------|
| 1 | `/tableau-init` | Needs `--target-version` (`2024.2-2025.x` or `2026.1+`). Creates `STATE.md`. |
| 2 | `/tableau-intake` | Reads `DASHBOARD-REQUEST.md` → writes `PRD.md`. |
| 3 | `/tableau-brand` | Reads `branding/` → writes `DESIGN-TOKENS.md`. Can be legitimately *skipped*. |
| 4 | `/tableau-data` | Must pick **Route 1** and find `data/*.csv`. Writes `DATA-MODEL.md`. |
| 5 | `/tableau-plan` | Writes `DASHBOARD-PLAN.md`. |
| 6 | `/tableau-mock` | Writes `mock-version/v_1/`. |
| 7 | `/tableau-spec` | Writes `IMPLEMENTATION-SPEC.md`. |
| 8 | `/tableau-build` | Writes the `.twbx`. **`lxml` must be installed** or the gate fails by design (issue #68) — that failure is correct behaviour, not a bug. |

For every skill, record: did its `precheck` command run at all, and did the path in the
command it printed contain a real directory before `/scripts/`?

**Note:** each skill's first documented action is a `precheck` subcommand. If a `precheck`
runs and reports, that skill's path resolution is proven — you do not have to reach step 8
to learn whether step 1's fix held.

---

## Step 3 — Dependencies

Seven of the nine skills are **standard-library only**. Only two need anything:

| Skill | Needs | Guard |
|---|---|---|
| `tableau-build` | `lxml` | `build.py:650` refuses to run a partial gate; `validate_twb_xsd.py:34` guards the import |
| `tableau-data` (Route 2 only) | `requests`, `python-dotenv` | `vds.py:46` and `vds.py:170` — added in this branch |

```bash
pip install -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"
```

`pandas` was removed from `requirements.txt` in this branch — nothing in the nine skills
imports it (only the legacy `skill/tableau-dashboard-creator/scripts/query_postgresql.py`
did, and that also needs `psycopg2`, which was never listed).

To verify a guard fires cleanly rather than traceback-ing, use a venv that genuinely
lacks the package — an in-process `sys.modules` trick proves nothing about a subprocess:

```bash
python -m venv /tmp/bare-venv          # no requests, no dotenv, no lxml
/tmp/bare-venv/bin/python "${CLAUDE_PLUGIN_ROOT}/skills/tableau-data/scripts/vds.py"
# expect on stderr, exit 2:
#   ERROR: requests is required by the published-ds route. Install with: pip install -r ...
# NOT a bare ModuleNotFoundError traceback.
```

On Windows the interpreter is `/tmp/bare-venv/Scripts/python.exe`.

---

## Step 4 — The remaining task: validate env vars at precheck

**This is not yet done and is the reason this handoff includes a task, not just a test.**

`tableau-data` Route 2 reads `TABLEAU_*` from `.env` via `vds.py:load_connection`. A
missing or blank variable raises `VdsError` with a clear message — but only **mid-run**,
deep inside the published-datasource path, after the analyst has already committed to the
route.

Move that check earlier, into `data.py precheck`, which is step 1 of that skill's
documented flow. A missing credential should surface before any network call.

- The variables are `TABLEAU_SERVER`, `TABLEAU_SITE`, `TABLEAU_API_VERSION`,
  `TABLEAU_PAT_NAME`, `TABLEAU_PAT_SECRET` — confirm against `CONTRACT.md` §3.2 and
  `demo/scaffold/.env.example` rather than trusting this list. Note `TABLEAU_SITE` is
  legitimately blank for a default site, and `TABLEAU_API_VERSION` has a code default, so
  only `TABLEAU_SERVER` + the two PAT values are genuinely required.
- Only validate Route 2's variables, and only when Route 2 is the selected route
  (`datasources.json` present, no production CSVs in `data/`). Route 1 must stay
  zero-credential.
- Add a test alongside `tests/test_data.py`; keep the suite at green.

Related: no env var is currently **documented** as a plugin requirement in `README.md`'s
prerequisites table. Add a row naming those five and pointing at
`demo/scaffold/.env.example`.

---

## Ground truth

- **Branch:** `fix/plugin-root-paths`, merged to `main`. Version `0.1.3`.
- **Suite:** `python -m pytest -q` from the repo root — 659 passed before handoff.
- **Contract:** `CONTRACT.md` is the inter-skill API. `route.py` is its executable mirror.
  If prose and script disagree, the script wins and the prose is the bug.
- **Do not edit** `~/.claude/plugins/marketplaces/dashboard-creation-tool/` — it is a
  cache that `/plugin update` overwrites. Fixes belong in a clone and go through `main`.
- After merging, the installed copy is stale until `/plugin update`. Run it before testing,
  and confirm the version reads 0.1.3.
