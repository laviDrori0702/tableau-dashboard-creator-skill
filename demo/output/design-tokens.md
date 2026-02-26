# Design Tokens

**Source**: branding directory (palette.json only — no logo provided)

## Typography
- **Font family**: Open Sans
- **Dashboard title**: 36px, bold, #1C2833
- **Chart title**: 15px, regular, #1C2833
- **Filter/section labels**: 12px, bold, #5D6D7E
- **Worksheet default font size**: 12px
- **Tooltip font size**: 12px

## Colors

### Backgrounds
- **Dashboard background**: #F4F6F7
- **Top banner / title area**: #F4F6F7
- **Chart card background**: #FFFFFF
- **Separator line**: #E5E8E8

### Accent Colors (KPI top border bars)
- Accent 1: #1B4F72
- Accent 2: #2E86C1
- Accent 3: #48C9B0
- Accent 4: #F39C12

### Chart Series Colors
`#1B4F72`, `#2E86C1`, `#48C9B0`, `#F39C12`, `#E74C3C`, `#8E44AD`, `#27AE60`, `#F1C40F`, `#95A5A6`, `#34495E`

### Text
- Dark (titles): #1C2833
- Medium (labels): #5D6D7E

### Borders
- Default border-style: none (border-width: 0)
- Exceptions: none

## Logo
- **File**: not provided
- **Placement**: Top-left banner area (placeholder space reserved)

## Dashboard Sizing
- **Sizing mode**: Range
- **Minimum**: 1100 x 800
- **Maximum**: Flex (no max set)

## Standard Container Hierarchy

```
layout-basic (root, 100% x 100%)
└── Content (vertical flow, centered)
    ├── Top Banner (layout-basic, fixed-size 65)
    │   ├── Logo area (fixed-size 195, padding: 7, padding-left: 12) — placeholder
    │   └── Right section
    │       ├── Update Time (fixed-size 305)
    │       └── Info icon (fixed-size 34, margin: 4)
    ├── Dashboard Title (horizontal flow, fixed-size 70)
    │   └── Text (margin: 4, margin-bottom: 1, bg: #F4F6F7)
    ├── Top Filters (horizontal flow, fixed-size 53)
    │   ├── Label (fixed-size 185, margin: 4)
    │   ├── Filter placeholder (margin: 4)
    │   └── Expand/collapse button (fixed-size ~38, margin: 4)
    │   └── [margin-top: 11, margin-bottom: 11]
    └── Charts & Hidden Filters (horizontal flow, flex)
        ├── KPI & Charts (vertical flow, ~86% width)
        │   ├── Main KPI row (horizontal, distribute-evenly, fixed-size 94)
        │   └── Chart rows (per layout)
        └── Hidden Filters panel (vertical flow, ~14% width, collapsible)
```

## Available Template Layouts

### With Main KPI Row
- **Frame With Main KPI (1+2)**: KPI row + 1 full-width chart + 1 row with 2 charts
- **Frame With Main KPI (2*2)**: KPI row + 2 rows of 2 charts each
- **Frame With Main KPI (2*2*1)**: KPI row + 2 rows of 2 charts + 1 full-width row

### Without Main KPI
- **Frame (2*2)**: 2 rows, 2 charts each
- **Frame (1+2)**: 1 full-width row + 1 row with 2 charts

## KPI Card Pattern

```
KPI container (horizontal flow, margin-right: 16)
└── Inner wrapper (vertical flow, bg: #FFFFFF)
    ├── Accent bar (3px height, margin: 0, bg: accent color)
    └── KPI content area (sheet placeholder)
```

## Chart Card Pattern

```
Chart wrapper (horizontal flow, margin-top: 11)
└── Chart outer (horizontal flow)
    └── Chart inner (vertical flow, padding: 8, bg: #FFFFFF)
        ├── Title bar (horizontal flow, fixed-size 46)
        │   ├── Icon image (fixed-size 47)
        │   ├── Chart title text (margin: 4, margin-left: 10)
        │   └── Info icon (fixed-size 38, margin: 4)
        ├── Separator line (3px, margin-lr: 10, bg: #E5E8E8)
        └── Chart sheet area (flex)
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

## Constraints

- **No rounded corners** (unsupported until Tableau 2026.1)
- **border-style: none** on all containers
- Charts have white background (#FFFFFF), dashboard has light gray background (#F4F6F7)
- Use distribute-evenly layout strategy for KPI rows and multi-chart rows
- Hidden filters panel uses collapsible toggle button (DZV pattern)