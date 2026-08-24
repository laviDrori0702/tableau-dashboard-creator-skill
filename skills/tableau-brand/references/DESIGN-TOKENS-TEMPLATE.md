# Design Tokens

> Authored by `tableau-brand` (step 4). The single source of truth for palette,
> type, and spacing consumed by `tableau-mock` (the HTML demo) and `tableau-build`
> (the `.twb`). Fill every bracketed value from `branding/` (the spec, a scraped
> org `.twb`, or the brand interview); for anything the brand didn't specify, pull
> the Tableau default from `references/tableau-design-tokens.md` **and list it under
> `## Fallback Decisions`**.

## Source

- **Brand source**: [branding/branding.md | branding/<name>.twb | brand interview | scaffold demo]
- **Logo / icons**: [paths under branding/, or "none provided"]

## Typography

- **Font family**: [one installed family, weight included - e.g. Tableau Book]
- **Dashboard title**: [size]px, [weight], [hex]
- **Chart title**: [size]px, [weight], [hex]   *(a Text object, not the worksheet's built-in header — see Constraints)*
- **Filter / section labels**: [size]px, [weight], [hex]
- **Worksheet default**: [size]px
- **Tooltip**: [size]px

## Colors

### Backgrounds
- **Dashboard background**: [hex]
- **Top banner / title area**: [hex]
- **Chart card background**: [hex]
- **Separator line**: [hex]

### Accent colors (KPI top border bars)
- Accent 1: [hex]
- Accent 2: [hex]
- Accent 3: [hex]
- Accent 4: [hex]

### Chart series colors
[Ordered list of hex colors for chart data series.]

[When the dashboard colors by more than one dimension, write one table per colored field
instead, each introduced by its field name in backticks — `tableau-build` binds each color
encoding to the table named for its field, and a field no table names falls back to the flat
list above. Row order is the domain walk order: Tableau pairs the first color with the first
domain member, so order the rows the way the members will appear (the chart's sort order).]

**`[field_name]`**

| Member | Token | Hex |
|--------|-------|-----|
| [member] | [Indigo 700] | `[hex]` |

### Text
- Dark (titles): [hex]
- Medium (labels): [hex]

## Logo

- **File**: [path under branding/, or "none provided"]
- **Placement**: Top-left header container (alongside the dashboard-title Text object)

## Dashboard Sizing

- **Sizing mode**: [Range | Fixed]   *(default Range; avoid Automatic — it distorts aspect ratios)*
- **Minimum width**: [px]
- **Minimum height**: [px]
- **Maximum**: [Flex (no max) by default, unless the brand requests a fixed cap]

## Icons

[If `branding/icons/` exists, map icon name -> file. Otherwise note that
`tableau-mock` will generate simple inline SVG icons matching chart types.]

| Icon name | File | Size |
|-----------|------|------|
| [name] | [branding/icons/<file>.svg] | 40x40 |

## Spacing

| Element | Property | Value |
|---------|----------|-------|
| Chart card | padding | [px] |
| Section | spacing | [px] |
| Container | margin | [px] |
| KPI accent bar | height | [px] |

## Fallback Decisions

Every value below was **not** specified by the brand and was filled from the
Tableau defaults in `references/tableau-design-tokens.md`. If the brand specified
everything, write `None — all values came from the brand source.`

| Token / decision | Fallback value used | Why it was needed |
|------------------|---------------------|-------------------|
| [token] | [value] | [missing from brand input] |

## Constraints

- **Sheet headers are hidden; the visible header is a Text object.** Every
  worksheet's built-in title is turned off (`Show Header` unchecked); the visible
  chart/dashboard title is a separate **Text object**, so it can sit inside a header
  container alongside the logo and icons and be positioned freely. (Tableau's
  built-in title is always pinned to the top and can't be moved.)
- **Collapsible filter panel via a Show/Hide button** (not Dynamic Zone Visibility):
  a container revealed/hidden by a Show/Hide button.
- **Tiled layout by default** — use tiled horizontal/vertical layout containers for
  spacing control; reserve floating for overlays (e.g. a Show/Hide filter panel).
- **Insight-style titles** — prefer titles that state the takeaway, not just the
  chart name (e.g. "Revenue up 12% YoY", not "Revenue").
- Tableau-fidelity: no rounded corners on `2024.2-2025.x`; border-style none; white
  chart cards on a light dashboard background.
