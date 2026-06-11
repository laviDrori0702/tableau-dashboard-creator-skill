# Project State

> Managed by tableau-dashboard-plugin skills. See CONTRACT.md before hand-editing.
>
> Inconsistent on purpose: step 'data' is marked 'approved' but its artifact
> (DATA-MODEL.md) is absent from this directory. The router must catch this via
> the ordering gate's artifact-existence check and point back at tableau-data.

## Metadata
- target_tableau_version: 2024.2-2025.x
- data_mode: csv
- current_version: v_1

## Steps
| order | step   | skill          | status   |
|-------|--------|----------------|----------|
| 1     | init   | tableau-init   | approved |
| 2     | intake | tableau-intake | skipped  |
| 3     | data   | tableau-data   | approved |
| 4     | brand  | tableau-brand  | skipped  |
| 5     | plan   | tableau-plan   | pending  |
| 6     | mock   | tableau-mock   | pending  |
| 7     | spec   | tableau-spec   | pending  |
| 8     | build  | tableau-build  | pending  |
