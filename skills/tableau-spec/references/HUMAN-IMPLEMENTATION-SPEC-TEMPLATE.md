# IMPLEMENTATION-SPEC.md (human route) — Desktop build guide template

> Used when `spec_mode: human`. This is a **Tableau Desktop build guide**, not a machine
> spec. `tableau-build` is skipped. Keep every `Element: <id>` line — coverage is checked
> mechanically against `DASHBOARD-PLAN.md`.

**Plan**: DASHBOARD-PLAN.md
**Mock (reference only)**: mock-version/<current>/mock.html

## Decision register

| decision | choice | why |
|----------|--------|-----|
| <decision> | <choice> | <reason> |

## Seams

- Data: <extract / live / hybrid>
- Brand: <tokens source>
- Desktop version: <version>

## Build order

1. ...
2. ...

## Verification checklist

- [ ] Every Element: id matches the plan
- [ ] Filters behave as planned
- [ ] Layout matches the mock per view

## Open items

- ...

---

# spec/<view>.md — one page per declared view

## View: <view-name>

### Tile layout

| tile | sheet / object | notes |
|------|----------------|-------|
| ... | ... | ... |

### Sidebar

| block | control | notes |
|-------|---------|-------|
| ... | ... | ... |

### Sizing

- Canvas: <W x H>
- Tiles use range sizing; no floating containers.

### Elements

#### <title>

Element: <plan-id>
Pattern: <optional-shared-shape-name>

| slot | value |
|------|-------|
| Text | ... |
| Colour | ... |
| Rows | ... |
| Columns | ... |
| Filter | ... |
| Tooltip | ... |

Justification (only when using Dynamic Zone Visibility, LOD, table calc, or parameter action):
because <why the simpler primitive is not enough>.

---

# spec/calculations.md

## <calc-name>

- Formula: `...`
- Used by: Element: <id>

# spec/parameters.md

## <parameter-name>

- Type / allow list: ...
- Used by: Element: <id>

# spec/fields.md

## [Field Name]

- Role: dimension / measure
- Notes: ...

# spec/patterns.md

> Include **only** when two or more sheets share a shape (same `Pattern: <name>` on
> multiple Element sections). Otherwise omit this file.

## Pattern: <name>

Shared slot table and the Element: ids that instantiate it.
