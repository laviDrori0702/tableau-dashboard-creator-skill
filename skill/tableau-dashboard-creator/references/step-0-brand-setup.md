# Step 0: Brand Setup

**Identity**: You are a Tableau design systems engineer. Your goal is to extract or build design tokens from the customer's branding assets so all subsequent steps use the correct look and feel.

## Process

1. **Detect branding source** — check the user's project root for one of:
   - `template.twb` — the organization's Tableau template workbook (preferred)
   - `branding/` directory — containing logo file(s) and a color palette file

2. **Extract or build design tokens** depending on which source is found
3. **Generate `design-tokens.md`** in the project root
4. **Present design-tokens.md to the user for approval** before proceeding to Step A

---

## Path A: Extract from `.twb` Template

If a `template.twb` (or any `.twb` file) is found in the project root:

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

## Path B: Build from Logo + Palette

If no `.twb` is found, look for `branding/` directory containing:
- **Logo**: `logo.png`, `logo.svg`, or any image file
- **Palette**: `palette.json` or `palette.pdf`

### Palette JSON format (if provided):
```json
{
  "primary": "#1a2b3c",
  "secondary": "#4d5e6f",
  "accent_colors": ["#13c636", "#e96e14", "#f7b42c", "#f887cc"],
  "background": "#f6f7f9",
  "card_background": "#ffffff",
  "text_dark": "#000021",
  "text_medium": "#5f5f71"
}
```

### Palette PDF:
If the user provides a PDF palette, visually inspect it to identify:
- Primary brand color
- Secondary/accent colors
- Use these as KPI accent bars and chart series colors

### Build tokens from branding:
When only logo + palette are available (no `.twb` template), use Tableau's default layout conventions:
- **Font family**: Open Sans (Tableau default)
- **Dashboard sizing**: Range, min 1100x800, no max
- **Container hierarchy**: Use the standard hierarchy from the fallback design tokens
- **Colors**: Map from the provided palette
- **Template layouts**: Use generic layout names (e.g., "2x2 Grid", "KPI Row + 2 Charts")

---

## Path C: No Branding Provided

If neither `template.twb` nor `branding/` directory exists:
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