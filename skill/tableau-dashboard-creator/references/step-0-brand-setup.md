# Step 0: Brand Setup

**Identity**: You are a Tableau design systems engineer. Your goal is to extract or build design tokens from the customer's branding assets so all subsequent steps use the correct look and feel.

## Process

1. **Detect branding source** — check the user's project root for one of:
   - `branding/` directory — containing logo file and a `branding.md` spec (preferred)
   - `template.twb` — the organization's Tableau template workbook (fallback)

2. **Extract or build design tokens** depending on which source is found
3. **Generate `design-tokens.md`** in the project root
4. **Present design-tokens.md to the user for approval** before proceeding to Step A

---

## Path A: Extract from `.twb` Template

If no `branding/` directory is found, but a `template.twb` (or any `.twb` file) exists in the project root:

1. **Read the TWB XML** — it is standard XML. Parse it to extract:
   - **Typography**: font family, sizes for titles/labels/tooltips, weights, colors
   - **Color palette**: background colors, accent colors, text colors, chart series colors
   - **Dashboard sizing**: min/max dimensions, sizing mode
   - **Container hierarchy**: the standard layout structure (zones, flow directions, fixed sizes)
   - **Template layouts**: available dashboard layouts (e.g., "Frame 2*2", "Frame With Main KPI 1+2")
   - **Spacing**: margins, padding values for containers, KPI cards, chart cards
   - **Constraints**: border styles, border-radius rules, shadow usage

2. **Identify the logo** — look for `<zone type='bitmap'>` elements in the TWB XML to find embedded or referenced logo images. Note the path or embedded data.

3. **Map TWB XML elements to design tokens** using this mapping:

   | TWB XML Element | Design Token |
   |----------------|-------------|
   | `<formatted-text><run fontname="...">` | Font family |
   | `<run fontsize="...">` | Font sizes per context |
   | `<run fontcolor="...">` | Text colors |
   | `<format attr='fill' value='...'/>` | Background colors |
   | `<zone ... fixed-size='N'>` | Container sizes |
   | `<zone type='layout-flow' flow='horizontal/vertical'>` | Layout direction |
   | `<zone style='margin:...; padding:...'>` | Spacing values |
   | `<color-palette>` | Chart series colors |

---

## Path B: Build from Logo + Branding Spec

If a `branding/` directory is found (checked **before** `template.twb`), look for:
- **Logo**: `.svg` or `.jpg` file (e.g., `logo.svg`, `logo.jpg`)
- **Branding spec**: `branding.md` — a markdown file describing the desired palette, fonts, padding, and dashboard sizing

### Expected `branding.md` format:

```markdown
# Branding Specification

## Color Palette
- Primary: #1a2b3c
- Secondary: #4d5e6f
- Accent colors: #13c636, #e96e14, #f7b42c, #f887cc
- Background: #f6f7f9
- Card background: #ffffff
- Text dark: #000021
- Text medium: #5f5f71

## Fonts
- Primary font: Open Sans
- Title weight: Bold
- Body weight: Regular

## Padding & Spacing
- Card padding: 8px
- Section gap: 11px
- Container margin: 4px

## Dashboard Sizing
- Mode: Range
- Minimum: 1100 x 800
- Maximum: Flexible
```

> Any section the user omits will use Tableau defaults from `references/tableau-design-tokens.md`.

### Build tokens from branding:
When logo + `branding.md` are available (no `.twb` template), use Tableau's default layout conventions for any values not specified in the branding spec:
- **Font family**: Open Sans (Tableau default)
- **Dashboard sizing**: Range, min 1100x800, no max
- **Container hierarchy**: Use the standard hierarchy from the fallback design tokens
- **Colors**: Map from the provided palette
- **Template layouts**: Use generic layout names (e.g., "2x2 Grid", "KPI Row + 2 Charts")

---

## Path C: No Branding Provided

If neither `branding/` directory nor `template.twb` exists:
1. **Ask the user** to provide one of them
2. If the user explicitly wants to proceed without branding, use the fallback tokens from `references/tableau-design-tokens.md` as a starting point
3. Warn the user that the mock will use generic Tableau defaults

---

## design-tokens.md Output Template

Generate this file in the project root:

```markdown
# Design Tokens

**Source**: [template.twb / branding directory / fallback defaults]

## Typography
- **Font family**: [extracted font]
- **Dashboard title**: [size]px, [weight], [color hex]
- **Chart title**: [size]px, [weight], [color hex]
- **Filter/section labels**: [size]px, [weight], [color hex]
- **Worksheet default font size**: [size]px
- **Tooltip font size**: [size]px

## Colors

### Backgrounds
- **Dashboard background**: [hex]
- **Top banner / title area**: [hex]
- **Chart card background**: [hex]
- **Separator line**: [hex]

### Accent Colors (KPI top border bars)
- Accent 1: [hex]
- Accent 2: [hex]
- Accent 3: [hex]
- Accent 4: [hex]

### Chart Series Colors
[Ordered list of hex colors for chart data series]

### Text
- Dark (titles): [hex]
- Medium (labels): [hex]

### Borders
- Default border-style: [value]
- Exceptions: [any special border zones]

## Logo
- **File**: [path to logo file]
- **Dimensions**: [w x h if known]
- **Placement**: Top-left banner area

## Dashboard Sizing
- **Sizing mode**: [Range / Fixed / Automatic]
- **Minimum**: [w] x [h]
- **Maximum**: [w] x [h] or Flex

## Standard Container Hierarchy
[Container tree extracted from .twb or built from defaults]

## Available Template Layouts
[List of available layouts with descriptions]

## KPI Card Pattern
[Structure of KPI cards]

## Chart Card Pattern
[Structure of chart cards]

## Spacing Reference
| Element | Property | Value |
|---------|----------|-------|
| [element] | [property] | [value] |

## Constraints
- [List of Tableau rendering constraints: no rounded corners, border rules, etc.]
```

## Guidelines

- If extracting from `.twb`, be thorough — capture every styling detail so Steps C and D don't need to reference the TWB directly
- If building from palette, clearly mark which values are "assumed defaults" vs "extracted from branding"
- The generated `design-tokens.md` becomes the **single source of truth** for all subsequent steps
- Present to the user and **wait for approval** before proceeding to Step A