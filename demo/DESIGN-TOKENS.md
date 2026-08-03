# Design Tokens

> Authored by `tableau-brand` (step 4) from `branding/branding.md`. The single
> source of truth for palette, type, and spacing consumed by `tableau-mock` (the
> HTML demo) and `tableau-build` (the `.twb`).

## Source

- **Brand source**: `branding/branding.md` (production brand spec)
- **Logo / icons**: `branding/logo.svg` (no `branding/icons/` provided)

## Typography

- **Font family**: Open Sans
- **Dashboard title**: 28px, Bold, `#1c2833`
- **Chart title**: 15px, Bold, `#1c2833`   *(a Text object, not the worksheet's built-in header — see Constraints)*
- **Filter / section labels**: 12px, Regular, `#5d6d7e`
- **Worksheet default**: 12px
- **Tooltip**: 12px

## Colors

### Backgrounds
- **Dashboard background**: `#f4f6f7`
- **Top banner / title area**: `#ffffff`
- **Chart card background**: `#ffffff`
- **Separator line**: `#eef1f4`

### Accent colors (KPI top border bars)
- Accent 1: `#1b4f72`
- Accent 2: `#2e86c1`
- Accent 3: `#48c9b0`
- Accent 4: `#f39c12`

### Chart series colors

`#1b4f72` (primary deep blue), `#2e86c1` (secondary blue), `#48c9b0` (teal), `#f39c12` (amber)

> Four colors, taken verbatim from the brand's accent list and kept in brand order. A chart
> with a dimension on Colour walks this list in order; a chart with nothing on Colour takes
> the first color, `#1b4f72`, as its flat mark color.

### Text
- Dark (titles): `#1c2833`
- Medium (labels): `#5d6d7e`

## Logo

- **File**: `branding/logo.svg`
- **Placement**: Top-left header container (alongside the dashboard-title Text object)

## Dashboard Sizing

- **Sizing mode**: Range
- **Minimum width**: 1100
- **Minimum height**: 800
- **Maximum**: Flex (no max)

## Icons

No `branding/icons/` folder was provided — `tableau-mock` generates simple inline
SVG icons matching each chart type.

## Spacing

| Element | Property | Value |
|---------|----------|-------|
| Chart card | padding | 8px |
| Section | spacing | 11px |
| Container | margin | 4px |
| KPI accent bar | height | 4px |

## Fallback Decisions

| Token / decision | Fallback value used | Why it was needed |
|------------------|---------------------|-------------------|
| Dashboard title size/weight | 28px, Bold | Brand names fonts and weights but no sizes |
| Chart title size | 15px | Not specified by the brand |
| Filter / section label size | 12px, `#5d6d7e` | Not specified by the brand |
| Worksheet default font size | 12px | Not specified by the brand |
| Tooltip font size | 12px | Not specified by the brand |
| Top banner / title area | `#ffffff` | Brand gives dashboard and card backgrounds only; the banner reuses the card white |
| Separator line | `#eef1f4` | Not specified by the brand |
| KPI accent bar height | 4px | Brand gives padding/spacing but no accent-bar height |
| Border style | none (width 0) | Not specified; Tableau default keeps cards flat |

## Constraints

- **Sheet headers are hidden; the visible header is a Text object.** Every
  worksheet's built-in title is turned off; the visible chart title is a separate
  Text object so it can sit inside a header container and be positioned freely.
- **Collapsible filter panel via a Show/Hide button** (not Dynamic Zone Visibility).
- **Tiled layout by default** — tiled horizontal/vertical containers for spacing
  control; floating reserved for overlays.
- **Insight-style titles** — titles state the takeaway, not just the chart name.
- Tableau-fidelity: no rounded corners on the `2024.2-2025.x` target; border-style
  none; white chart cards on the `#f4f6f7` dashboard background.
