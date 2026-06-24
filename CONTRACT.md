# CONTRACT.md — the inter-skill API of `tableau-dashboard-plugin`

> **Audience: maintainers.** This is the single canonical source of truth for the file-based handoff
> API that the 8 skills use to talk to each other. It is **never loaded at runtime** — each
> `SKILL.md` restates only its own slice (its preconditions, reads, writes, `STATE.md` updates, and
> next step). When a skill's behavior and this document disagree, **this document wins** and the
> skill must be corrected.

The plugin replaces the monolithic `tableau-dashboard-creator` skill with 8 independent,
explicitly-invoked skills, each doing one job in its own fresh conversation. No skill loads the whole
workflow; all state and handoffs live in files on disk. This contract is what keeps the 8 skills
aligned without drift.

---

## 1. The ordered step list

The workflow is a fixed, ordered sequence of 8 steps. Each step is owned by exactly one skill, reads
a known set of artifacts, and writes exactly one primary artifact.

| # | step     | skill            | required reads (producer step)                                                   | primary write                                  | skippable |
|---|----------|------------------|----------------------------------------------------------------------------------|------------------------------------------------|-----------|
| 1 | `init`   | `tableau-init`   | —                                                                                | `STATE.md` + `scaffold/` demo examples (see §3.1)              | no  |
| 2 | `intake` | `tableau-intake` | — *(prefers root `DASHBOARD-REQUEST.md` or pasted text; demo fallback `scaffold/` — §3.1)* | `PRD.md`                    | yes |
| 3 | `data`   | `tableau-data`   | — *(csv route: `data/*.csv`; published-ds route: `datasources.json` + `.env` via VDS; demo fallback `scaffold/sample-data/` — §3.1/§3.2)* | `DATA-MODEL.md` + `data/*.csv` | no  |
| 4 | `brand`  | `tableau-brand`  | — *(prefers `branding/`; demo fallback `scaffold/branding/` — §3.1)*             | `DESIGN-TOKENS.md`                             | yes |
| 5 | `plan`   | `tableau-plan`   | `DATA-MODEL.md` (`data`)                                                          | `DASHBOARD-PLAN.md`                            | no  |
| 6 | `mock`   | `tableau-mock`   | `DASHBOARD-PLAN.md` (`plan`), `data/*.csv` (`data`)                               | `mock-version/v_N/mock.html`                   | no  |
| 7 | `spec`   | `tableau-spec`   | `DASHBOARD-PLAN.md` (`plan`), `mock-version/v_N/mock.html` (`mock`)               | `mock-version/v_N/IMPLEMENTATION-SPEC.md`      | no  |
| 8 | `build`  | `tableau-build`  | `IMPLEMENTATION-SPEC.md` (`spec`), `DATA-MODEL.md` (`data`), `data/*.csv` (`data`) | `mock-version/v_N/dashboard.twbx`     | no  |

### Required vs. optional reads

The **required reads** column lists only *producer-gated* artifacts — files written by an earlier
step. It is the only thing that gates ordering (§4.1). Steps whose input is an analyst-supplied
**input** (not produced by a step) have no required read and are never blocked on it:

- `intake` reads the `DASHBOARD-REQUEST.md` input file **or** free text the analyst pastes directly
  into the terminal — whichever is present (and, failing both, the `scaffold/` demo example, §3.1).
  Because the request can be pasted, `intake` has **no** required read and never refuses to run for a
  "missing" request file. (`data` and `brand` are likewise input-driven: `data/`/`datasources.json` and
  `branding/` respectively, each with a `scaffold/` demo fallback — §3.1.)

A step may also have *optional reads* that enrich its output but never block it:

- `plan` optionally reads `PRD.md` (from `intake`) and `DESIGN-TOKENS.md` (from `brand`). Because
  `intake` and `brand` are skippable, those artifacts may be absent; `plan` falls back to
  `DASHBOARD-REQUEST.md` and neutral styling respectively rather than refusing to run.
- `mock` optionally reads `DESIGN-TOKENS.md`; absent ⇒ neutral styling.
- `build` optionally reads `DESIGN-TOKENS.md` and the target version (always present, see §2).

> **Maintainer rule:** when you add or change a skill's dependencies, update **both** the table above
> and the `STEPS` definition in `skills/tableau-route/scripts/route.py`. They are the prose and the
> executable copy of the same graph and must stay identical.

---

## 2. The `STATE.md` schema

`STATE.md` is the project manifest: a human-readable markdown file at the project root, created by
`tableau-init` and updated by every subsequent skill. It is the single place that records where the
analyst is in the workflow.

```markdown
# Project State

> Managed by tableau-dashboard-plugin skills. See CONTRACT.md before hand-editing.

## Metadata
- target_tableau_version: 2024.2-2025.x   # 2024.2-2025.x | 2026.1+
- data_mode: csv                          # csv | published-ds
- current_version: v_1                    # v_1, v_2, ...

## Steps
| order | step   | skill          | status   |
|-------|--------|----------------|----------|
| 1     | init   | tableau-init   | approved |
| 2     | intake | tableau-intake | pending  |
| 3     | data   | tableau-data   | pending  |
| 4     | brand  | tableau-brand  | pending  |
| 5     | plan   | tableau-plan   | pending  |
| 6     | mock   | tableau-mock   | pending  |
| 7     | spec   | tableau-spec   | pending  |
| 8     | build  | tableau-build  | pending  |
```

### Fields

| field                   | location           | allowed values                                  | meaning |
|-------------------------|--------------------|-------------------------------------------------|---------|
| `target_tableau_version`| Metadata           | `2024.2-2025.x` \| `2026.1+`                     | Captured at `init`; drives `tableau-build`'s workbook `version` attribute and version-specific XML (e.g. `<explain-data>`). Never re-asked downstream. |
| `data_mode`             | Metadata           | `csv` \| `published-ds`                          | How `tableau-data` acquires rows (§3.2). `csv` is the default, zero-credential path (analyst-provided CSVs); `published-ds` queries a published source via the VizQL Data Service. There is **no** direct-database mode. |
| `current_version`       | Metadata           | `v_1`, `v_2`, …                                 | The active deliverable version directory under `mock-version/`. Bumped when a deliverable skill re-runs after approval (§4.3). |
| `status` (per step)     | Steps table, 1/row | `pending` \| `approved` \| `skipped` \| `stale` | Lifecycle of each step. |

### Per-step status vocabulary

| status     | set by                                  | meaning |
|------------|-----------------------------------------|---------|
| `pending`  | `init` (initial state of steps 2–8)     | Not yet run, or run but not approved. |
| `approved` | the owning skill, on explicit user OK   | The step's artifact exists and the analyst signed off on it. Satisfies the ordering gate for downstream steps. |
| `skipped`  | the owning skill (only steps 2 & 4)     | The analyst chose to skip an optional step. Satisfies the ordering gate **without** producing the artifact (downstream uses fallbacks). |
| `stale`    | an **upstream** skill, via §4.2         | The step was `approved`, but an upstream artifact changed afterward; its output may now disagree with the new upstream truth and must be re-run. |

> A step is **resolved** when its status is `approved` or `skipped`. Resolved is the condition the
> ordering gate (§4.1) checks for and the condition the router (§5) treats as "done."

> **Skip preconditions are skill-specific.** A skill may gate its own `skipped` transition behind a
> minimal precondition when skipping blank would degrade the whole pipeline. `tableau-brand` (step 4)
> only accepts `skipped` once `branding/branding.md` exists: branding drives how good the mock and the
> Tableau spec can be, so the analyst must capture at least some brand intent (a spec, a scraped org
> `.twb`, or the brand interview) before opting into neutral styling. `tableau-intake` (step 2) has no
> such precondition — its request can be skipped outright. This narrows *when* a step may be skipped;
> it never changes what `skipped` then means (a resolved step that produced no artifact).

---

## 3. The artifact-naming rule

The case of a filename encodes its role, so a skill can tell handoff artifacts from inputs at a glance.

- **`UPPER-KEBAB.md` ⇒ handoff artifact** produced by a step and consumed by later steps:
  `PRD.md`, `DATA-MODEL.md`, `DESIGN-TOKENS.md`, `DASHBOARD-PLAN.md`, `IMPLEMENTATION-SPEC.md`.
  (`STATE.md` is the manifest and also uses this casing.)
- **lowercase ⇒ input or config**, owned by the analyst, never produced as a handoff:
  `.env`, `branding/`, `data/`, `datasources.json`, `DASHBOARD-REQUEST.md`. Their demo counterparts live
  under `scaffold/` (see §3.1).

New artifacts MUST follow this rule. Do not introduce a lowercase handoff or an UPPER-KEBAB input.

### 3.1 Scaffold examples vs. production inputs

`tableau-init` does not create the analyst's input files directly. It writes a single `scaffold/`
folder of **demo examples** so the workflow can be trialed end-to-end before any real input exists:

| production input (preferred, project root) | `scaffold/` demo fallback                                    |
|---------------------------------------------|--------------------------------------------------------------|
| `DASHBOARD-REQUEST.md`                      | `scaffold/EXAMPLE-DASHBOARD-REQUEST.md`                      |
| `datasources.json` + `.env` *(published-ds)* | `scaffold/EXAMPLE-datasources.json` + `scaffold/.env.example` |
| `branding/` (e.g. `branding/branding.md`)   | `scaffold/branding/EXAMPLE-branding.md`                     |
| `data/*.csv` *(csv route)*                  | `scaffold/sample-data/*.csv`                                |

**Preference rule.** Every skill that consumes one of these inputs MUST prefer the production
file/folder at the project root and fall back to the matching `scaffold/` example **only** when the
production input is absent. A skill that falls back to a `scaffold/` example MUST say so — it is
demoing the workflow, not using real input.

This keeps "real vs. demo" unambiguous and makes the *absence* of a production file the signal of
what the analyst still owes. `init` creates only `scaffold/` (and `STATE.md`); the production files
are created by the analyst or written by the step that owns them — notably `tableau-data` writes the
real samples to `data/` (CONTRACT.md §1 step 3), never to `scaffold/`.

> **Ordering-gate note (§4.1):** for the `data`-produced sample that gates `mock` and `build`, the
> artifact-existence check is satisfied by **either** `data/*.csv` **or** `scaffold/sample-data/*.csv`,
> so a demo run is never wrongly blocked. The producer-status check still applies as normal.

### 3.2 Data acquisition routes (`tableau-data`)

There are exactly **two** routes by which `tableau-data` obtains the mimicking CSVs under `data/`.
There is deliberately **no** direct-database route: connecting to arbitrary databases would expose
credentials, run uncapped queries that cost money, and force a per-database connector to be built and
maintained. Both routes below avoid all three.

Whatever the route, the output is identical — `DATA-MODEL.md` plus mimicking CSVs in `data/` whose
headers and types match the real source exactly, so **Replace Data Source** swaps in live data later.
**A `data/` CSV stands in for its data source** ("csv = datasource").

**Route 1 — `data_mode: csv` (default, zero-credential).** The analyst drops CSV file(s) in `data/`.
**Each CSV file is one data source.** (Joining several into a single composed source relies on
Tableau's *composable data sources*, which is newer — Tableau 2026.2+ — and untested; it is opt-in and
out of scope until proven. Until then, multiple CSVs stay as multiple data sources.)

**Route 2 — `data_mode: published-ds` (VizQL Data Service).** The analyst lists published data
source(s) in `datasources.json` (one entry each, keyed by id, with `ds_name` + `project_name`) and
supplies a Tableau connection in `.env`. This route is a **pure pull** that *fills* an empty `data/`:
it fires only when `data/` holds no production CSVs (per §3.1, real CSVs always win — the analyst
already has the data). `tableau-data` samples each source through the **VizQL Data Service (VDS)** —
Tableau's official, governed query API — and **nothing else** (no synthesized rows, no `.tdsx`/`.hyper`
extract download, no embedded-source reading, no GraphQL). Two operations, in order:

1. **read-metadata** — the **queryable** fields (names, types, descriptions). These are
   **authoritative**: the pulled CSV schema and `DATA-MODEL.md` take their types and descriptions from
   here rather than inferring them; where a field has no description, the model fills it in (as for the
   csv route).
2. **query-datasource** — a capped sample of **all queryable** fields (hidden / non-queryable fields
   are skipped). **`rowLimit` caps the rows returned to us — default `100`, silent up to `1000`; above
   `1000` requires explicit analyst confirmation** before the pull. The cap bounds the response, not
   what VDS reads from the underlying source.

**Each listed source becomes one `data/<slug>.csv`** ("csv = datasource"), where `<slug>` is the
lowercased `ds_name` with non-alphanumeric runs collapsed to `_` (e.g. `Regional Sales` →
`regional_sales.csv`). The acquisition tier recorded in `DATA-MODEL.md` is **`published-ds (VDS query)`**.

The pull is **all-or-nothing**: if it cannot deliver rows for every source — sign-in/connection
failure, the source's **API Access** capability off, the named source not resolving as a *published*
source, or zero rows — `tableau-data` **fails with an actionable error and writes no artifact**
(`STATE.md` untouched). There is **no** silent fallback to demo or synthesized data: the analyst fixes
the credentials/permission and re-runs, or drops CSV(s) in `data/` to use Route 1.

Auth is a Tableau REST **Personal Access Token** sign-in (`.env`, discovered by walking up from the
project directory — nearest wins); the credentials token is reused for VDS, and the source must have
the **API Access** capability enabled. VDS requires **Tableau Cloud, or Tableau Server 2025.1+**.

> **Published only — not embedded.** VDS queries *published* data sources only; it cannot see
> **embedded** sources (bundled inside a workbook). A named source that is in fact embedded simply
> fails to resolve — so `tableau-data`'s not-found error MUST name this possibility and steer the
> analyst to **export the data to CSV from Tableau** and use Route 1 instead.

> **Executable spec.** The exact VDS endpoints, request/response JSON, `.env` variable names, and the
> slug/type mapping are implemented and tested in `skills/tableau-data/scripts/vds.py` (+ `tests/test_vds.py`).
> That code is the source of truth for the *mechanics*; this section is the source of truth for the
> *guarantees*. Keep them consistent.

---

## 4. The three cross-cutting rules

Every skill MUST honor all three. They are what make the file-based handoff trustworthy.

### 4.1 Ordering

A skill **refuses to run** unless, for every artifact in its **required reads** (§1):

1. the producer step's status is **resolved** (`approved` or `skipped`), **and**
2. the artifact file actually exists on disk.

A skill that cannot run reports which upstream step is the blocker and stops. It does not partially
run or fabricate missing inputs. (Optional reads never block — see §1.)

### 4.2 Staleness propagation

When a skill **re-runs and changes its output**, it flips **every downstream step** (every step with
a higher order number) from `approved` → `stale`. Steps already `pending`/`skipped`/`stale` are left
as-is; the skill's own step becomes `approved` again on re-approval.

This guarantees the demo and the workbook can never silently disagree with an updated plan: a changed
upstream forces a visible re-run of everything that depended on it. The analyst then re-runs the
stale steps in order (the router points the way).

### 4.3 Versioning

Two kinds of outputs, two storage strategies:

- **Root files = latest approved truth.** `PRD.md`, `DATA-MODEL.md`, `DESIGN-TOKENS.md`,
  `DASHBOARD-PLAN.md` live at the project root and are overwritten in place. There is exactly one
  current copy. Re-running one of these skills updates the root file and triggers staleness (§4.2);
  it does **not** create a new version directory.
- **Deliverables = standalone versioned copies.** `mock.html`, `IMPLEMENTATION-SPEC.md`, and
  `dashboard.twbx` are written under `mock-version/v_N/`. Re-running a deliverable skill **after its
  step was approved** bumps `current_version` to a new `v_N` and writes a full standalone copy there,
  preserving prior versions. (Re-running before approval overwrites the current `v_N`.)

---

## 5. The router (`tableau-route`)

`tableau-route` is a **skill** (not a slash command), explicit-invocation only. It is a thin wrapper
over `skills/tableau-route/scripts/route.py`, which reads `STATE.md` and reports the single next skill the
analyst should run, honoring §4.1.

**It is a router only — it never invokes a skill inline.** Inline execution would share context and
defeat the whole point of the split (each skill must run in its own fresh conversation). The router's
job ends at telling the analyst what to run next.

### Next-step algorithm (`compute_next_step`)

1. If `STATE.md` is **absent** ⇒ next is `tableau-init` (fresh project).
2. Parse the Steps table in canonical order.
3. The first step whose status is **not resolved** (i.e. `pending` or `stale`) is the candidate.
4. **Gate check** the candidate against §4.1: for each required read, the producer must be resolved
   and its artifact must exist. If a required upstream is unresolved, the **upstream** step is
   returned as next (it is the real blocker), not the candidate.
5. If no step is unresolved ⇒ the pipeline is **done**; point the analyst at the deliverable.

Because staleness only ever flips `approved` → `stale` (§4.2), scanning in order and taking the first
unresolved step naturally respects the dependency graph; the explicit gate check in step 4 is a
defensive guard against a hand-edited or inconsistent `STATE.md`.

---

## 6. Shared interactions vocabulary

To stop "interaction" meaning three different things across `plan` → `mock` → `spec`, the three
skills use this fixed vocabulary. Each term means the same thing in the plan's interactions table,
the mock's behavior, and the spec's Tableau mapping.

| term            | meaning (intent level)                                                              | typical Tableau construct (decided in `spec`) |
|-----------------|-------------------------------------------------------------------------------------|-----------------------------------------------|
| `toggle panel`  | Show/hide a region of the dashboard on demand.                                      | Dynamic Zone Visibility, or a show/hide button container. |
| `swap view`     | Replace one chart with an alternative in the same slot (e.g. bar ⇄ line).           | Parameter + Dynamic Zone Visibility, or sheet swap. |
| `drill`         | Move between levels of a hierarchy (e.g. year → quarter → month) in place.          | Hierarchy expand/collapse, or set/parameter action. |
| `cross-filter`  | Selecting marks in one chart filters the others.                                    | Filter action (`Use as Filter`). |
| `highlight`     | Selecting marks in one chart highlights related marks elsewhere without filtering.  | Highlight action. |
| `parameter swap`| A control changes a measure/dimension/threshold used across the dashboard.          | Parameter + parameter action / calculated field. |

When a requested interaction does not fit one of these terms, add the term **here first** (with its
intent and Tableau construct) before using it in any skill — that keeps plan, mock, and spec aligned.

---

## 7. Self-containment

Each skill owns only the resources it uses; there is no shared resource pool. For example
`tableau-build` owns its snippet library, `xsd/`, validators, and `examples/`; `tableau-data` owns its
VizQL Data Service client (the published-ds route, §3.2); `tableau-init` owns the `skeleton/`
templates. A skill can be edited and reasoned about independently as long as it continues to honor
this contract.
