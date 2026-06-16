# Fallback Design Tokens (Tableau Defaults)

These are default Tableau design tokens used as a **fallback** when the brand
leaves a value unspecified — whether `tableau-brand` is working from a
`branding/branding.md`, a scraped org `*.twb`, or the brand interview. `tableau-brand`
produces a project-specific `DESIGN-TOKENS.md` that overrides these values and
**must disclose every fallback-driven value under its `## Fallback Decisions`
section** so the analyst knows which visual decisions were guessed.

This reference is owned by `tableau-brand` (self-containment, CONTRACT.md §7) and is
read by the skill at authoring time only; it is never a handoff artifact.

## Typography

Use the native **Tableau** font family (it ships with Tableau Desktop, so the
workbook renders identically without installing anything):

- **Font family**: Tableau (Tableau Bold / Tableau Medium / Tableau Light)
- **Dashboard title**: 28px, Tableau Bold, `#1c2833`
- **Chart title**: 15px, Tableau Medium, `#1c2833`
- **Filter / section labels**: 12px, Tableau Medium, `#5d6d7e`
- **Worksheet default font size**: 12px, Tableau Light
- **Tooltip font size**: 12px, Tableau Light

> Titles are rendered as **Text objects**, not the worksheet's built-in header
> (see Constraints). Prefer **insight-style** title copy that states the takeaway
> (e.g. "Revenue up 12% YoY") over a bare field name.

## Colors

A dashboard should **speak one color language** — a cohesive, analogous palette
(not a rainbow). Default to **at most 5** series colors drawn from the same
blue-teal family, so categories stay distinguishable without clashing.

### Backgrounds
- **Dashboard background**: `#f6f7f9`
- **Top banner / title area**: `#f5f7f9`
- **Chart card background**: `#ffffff`
- **Separator line**: `#eef1f4`

### KPI Accent Colors (top border bar — same family as the series)
- Accent 1: `#2e75b6` (primary blue)
- Accent 2: `#41a5a5` (teal)
- Accent 3: `#5b9bd5` (light blue)
- Accent 4: `#6f8faf` (slate blue)

### Chart Series Colors (cohesive, max 5)
`#2e75b6` (blue), `#41a5a5` (teal), `#5b9bd5` (light blue), `#6f8faf` (slate), `#9b8bbd` (muted purple)

### Text
- Dark (titles): `#1c2833`
- Medium (labels): `#5d6d7e`

### Borders
- Default border-style: `none` (border-width: 0)

## Dashboard Sizing

- **Sizing mode**: Range
- **Minimum width**: 1100
- **Minimum height**: 800
- **Maximum**: Flex (no max) — the default, unless the brand requests a fixed cap

> **Avoid Automatic sizing.** Automatic distorts aspect ratios on resize (wide
> charts go tall-and-thin, fonts become unreadable). Range with a minimum set and
> no maximum keeps charts legible while letting the dashboard expand on larger
> screens. Use Fixed only when a specific device/canvas is the target.

## Standard Container Hierarchy

```
layout-basic (root, 100% x 100%)
└── Content (vertical flow, centered)
    ├── Header container (horizontal flow, fixed-size 65)
    │   ├── Logo area (fixed-size 195, padding: 7, padding-left: 12)
    │   ├── Title (Text object — NOT the worksheet header)
    │   ├── Spacer (Blank, flex)
    │   └── Right section (update time, info icon)
    ├── Top Filters (horizontal flow, fixed-size 53)
    │   ├── Label (fixed-size 185, margin: 4)
    │   ├── Filter placeholder (margin: 4)
    │   ├── Spacer (Blank, flex)
    │   └── Show/Hide button (fixed-size ~38, margin: 4)
    │   └── [margin-top: 11, margin-bottom: 11]
    └── Charts & Hidden Filters (horizontal flow, flex)
        ├── KPI & Charts (vertical flow, ~86% width)
        │   ├── Main KPI row (horizontal, distribute-evenly, fixed-size 94)
        │   ├── Chart rows (per layout)
        │   └── Spacer (Blank, flex)
        └── Hidden Filters panel (vertical flow, collapsible via Show/Hide button)
            └── Spacer (Blank, flex)
```

## KPI Card Pattern

```
KPI container (horizontal flow, margin-right: 16)
└── Inner wrapper (vertical flow, bg: card background)
    ├── Accent bar (3px height, margin: 0, bg: accent color)
    ├── KPI content area (sheet, inner-padding: 8)
    └── Spacer (Blank, flex)
```

## Chart Card Pattern

```
Chart wrapper (horizontal flow, margin-top: 11)
└── Chart outer (horizontal flow)
    └── Chart inner (vertical flow, padding: 8, bg: card background)
        ├── Title bar (horizontal flow, fixed-size 46)
        │   ├── Icon image (40x40, fixed-size 47, from branding/icons/)
        │   ├── Chart title (Text object, margin: 4, margin-left: 10)
        │   ├── Spacer (Blank, flex)
        │   └── Info icon (fixed-size 38, margin: 4)
        ├── Separator line (3px, margin-lr: 10, bg: separator color)
        ├── Chart sheet area (flex, inner-padding: 8, worksheet header hidden)
        └── Spacer (Blank, flex)
```

## Spacing Reference

| Element | Property | Value |
|---------|----------|-------|
| Content container | outer margin | ~1.25% from edges |
| Logo | padding | 7 (padding-left: 12) |
| Dashboard title | margin | 4 (margin-bottom: 1) |
| Filter bar | margin-top/bottom | 11 |
| Filter label | margin | 4 |
| KPI cards | margin-right | 16 (between cards) |
| KPI accent bar | margin | 0, height 3 |
| Chart card outer | margin-top | 11 |
| Chart card inner | padding | 8 |
| Chart title text | margin | 4 (margin-left: 10) |
| Separator line | margin-right/left | 10 |
| Sheet zone | inner padding | 8 |

## Constraints

- **Worksheet headers are hidden; titles are Text objects.** Turn off each
  worksheet's built-in title (`Show Header`) and render the visible chart/dashboard
  title as a separate **Text object**, so it can live in a header container with the
  logo/icons and be placed freely. Tableau's built-in title is always pinned to the
  top and can't be moved — the Text object gives full layout control.
- **Collapsible filter panel via a Show/Hide button** (not Dynamic Zone Visibility):
  a container revealed/hidden by a Show/Hide button.
- **Tiled layout by default** — tiled horizontal/vertical containers for spacing
  control; reserve floating for overlays (e.g. the Show/Hide filter panel).
- **One color language, max 5 series colors** — cohesive analogous palette; don't
  reach for a rainbow.
- **No rounded corners** on `2024.2-2025.x` unless the analyst explicitly accepts a
  non-Tableau-faithful mock.
- **border-style: none** on all containers.
- Charts have a white background; the dashboard has a light-gray background.
- Use distribute-evenly layout for KPI rows and multi-chart rows.
- Use `fixed-size` on structural elements (header bar, KPI rows, filter bars,
  accent/separator bars, icons, logo) — only chart areas and main content flex.
- Every `layout-flow` container must include at least one Spacer (Blank, flex) to
  prevent layout collapse.
- Any value taken from this reference MUST be listed under `## Fallback Decisions`
  in the generated `DESIGN-TOKENS.md`.
