# Implementation Spec: Sales Performance (human route)

> Worked Desktop build guide for the Sales Performance demo. Not a machine spec —
> `tableau-build` is skipped on this route.

**Plan**: DASHBOARD-PLAN.md (copied beside this guide)
**Mock (reference)**: ../mock-version/v_1/mock.html

## Decision register

| decision | choice | why |
|----------|--------|-----|
| Spec route | human | Analyst will build in Tableau Desktop; no generated .twbx |
| Cross-filter | Filter actions | Simplest construct for region/category brush |
| Region + target | dual-axis style sheets | Actual vs target comparison without a fixed-grain expression |

## Seams

- Data: CSV extracts under `demo/data/`
- Brand: `demo/DESIGN-TOKENS.md` / branding/
- Desktop version: 2024.2–2025.x

## Build order

1. Create parameters (none required for this demo beyond filter cards).
2. Create calculated fields listed in `spec/calculations.md`.
3. Build pattern sheets from `spec/patterns.md` (KPI card).
4. Build remaining sheets for the Overview view.
5. Assemble the Overview dashboard from the tile table; set range sizing.
6. Wire filter and highlight actions from the Interactions plan ids.
7. Run the verification checklist below.

## Verification checklist

- [ ] Every Element: id from the plan appears in the guide
- [ ] KPI row matches mock proportions
- [ ] Region cross-filter updates trend, category, and table
- [ ] Category highlight works without breaking filters
- [ ] No floating containers; tiles use percentage range sizing

## Open items

- Confirm AOV definition with the stakeholder (order grain vs line grain).
