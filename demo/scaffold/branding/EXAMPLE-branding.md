# Branding Specification — EXAMPLE

> **This is a demo example under `scaffold/`.** To apply your own brand, create
> **`branding/branding.md` at the project root** (copy this file) and optionally
> add `branding/logo.svg` and `branding/icons/*.svg`. `tableau-brand` prefers the
> root production `branding/` over this example. Branding is optional — skip it
> for neutral styling.

## Color Palette
- Primary: #YOUR_PRIMARY_COLOR
- Secondary: #YOUR_SECONDARY_COLOR
- Accent colors: #ACCENT_1, #ACCENT_2
- Background: #F6F7F9
- Card background: #FFFFFF
- Text dark: #1C2833
- Text medium: #5D6D7E

## Fonts
- Primary font: Open Sans
- Title weight: Bold
- Body weight: Regular

## Padding & Spacing
- Card padding: 8px
- Section spacing: 11px
- Container margin: 4px

## Icons
Place 40x40 `.svg` files in `branding/icons/` for chart header enrichment (e.g., `bar-chart.svg`, `trend.svg`).
If omitted, `tableau-mock` will generate simple inline SVG icons.

## Dashboard Sizing
- Mode: Range
- Minimum height: 800
- Minimum width: 1100
- Maximum: Flexible

## Fallback Disclosure
If you leave any branding choices unspecified, the agent should tell you which Tableau default it used and why.
