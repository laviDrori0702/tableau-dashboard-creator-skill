# Step D: Tableau Implementation Spec

Create `TABLEAU-IMPLEMENTATION.md` that serves as a technical blueprint for building the dashboard in Tableau Desktop. A Tableau developer should be able to implement the dashboard solely from this document.

## Process

1. **Review the approved mock** (`mock-version/v_N/mock.html`)
2. **Review DASHBOARD-PLAN.md** for data mappings
3. **Review DS-ARCHITECTURE.md** for field references
4. **Review design-tokens.md** for styling values and template layout
5. **Create TABLEAU-IMPLEMENTATION.md** with all sections below
6. **Save to** `mock-version/v_N/TABLEAU-IMPLEMENTATION.md`

## TABLEAU-IMPLEMENTATION.md Template

```markdown
# Tableau Implementation: [Dashboard Name]

**Template**: [Which template layout to use from design-tokens.md]
**Datasources**: [List of datasources from DS-ARCHITECTURE.md]
**Mock version**: v_N

---

## Section 1: Container Hierarchy

[Full container tree with all properties. Use indentation to show nesting.]

### Container Tree

```
Root Container (layout-basic)
├── Content (Vertical, fixed-height: [px])
│   ├── Top Banner (Tiled, fixed-height: [px])
│   │   ├── Logo (Image, [w]x[h], padding: [values])
│   │   ├── Spacer (Blank, flex)
│   │   ├── Update Time (Sheet, [w]x[h])
│   │   └── Info Icon (Sheet, [w]x[h])
│   ├── Dashboard Title (Text, fixed-height: [px])
│   │   └── "[Title Text]" ([font], [size], bold, [color], bg: [color])
│   ├── Filter Bar (Horizontal, fixed-height: [px], margin-top: 11, margin-bottom: 11)
│   │   ├── "Filters" Label (Text, fixed-width: [px])
│   │   ├── [Filter 1] (Filter, type: [dropdown/slider/date])
│   │   ├── [Filter 2] (Filter, type: [type])
│   │   └── Expand Button (Button, fixed-width: [px])
│   └── Main Area (Horizontal, flex)
│       ├── Charts Area (Vertical, ~86% width)
│       │   ├── KPI Row (Horizontal, distribute-evenly, fixed-height: [px])
│       │   │   ├── KPI 1 Container (Vertical, bg: [card bg color])
│       │   │   │   ├── Accent Bar (Blank, 3px, bg: [accent color])
│       │   │   │   └── [KPI Sheet Name] (Sheet)
│       │   │   └── [... more KPIs]
│       │   ├── Chart Row 1 (Horizontal, distribute-evenly, margin-top: 11)
│       │   │   └── Chart 1 Container (Vertical, padding: 8, bg: [card bg color])
│       │   │       ├── Title Bar (Horizontal, fixed-height: 46)
│       │   │       │   ├── Icon (Image)
│       │   │       │   ├── "[Chart Title]" (Text, [size], [color])
│       │   │       │   └── Info Icon (Sheet)
│       │   │       ├── Separator (Blank, 3px, bg: [separator color], margin: 0 10px)
│       │   │       └── [Chart Sheet Name] (Sheet, flex)
│       │   └── [... more chart rows]
│       └── Hidden Filters Panel (Vertical, ~14% width, collapsible)
│           └── [Hidden filter sheets]
```

### Container Details

| Container Name | Type | Direction | Size | Background | Padding | Margin |
|---------------|------|-----------|------|------------|---------|--------|
| [Name] | [Horizontal/Vertical/Tiled] | [H/V] | [fixed px or flex] | [color] | [values] | [values] |

---

## Section 2: Sheets

### Sheet: [Sheet Name]

**Container**: [Parent container name from hierarchy]
**Size**: [Width x Height or flex]
**Title**: [Visible title text, or "hidden"]

#### Marks
- **Mark type**: [Bar / Line / Circle / Square / Text / etc.]
- **Columns shelf**: [field(s)]
- **Rows shelf**: [field(s)]
- **Color**: [field or fixed color]
- **Size**: [field or fixed]
- **Label**: [field(s) and format]
- **Detail**: [field(s)]
- **Tooltip**: [custom tooltip text with field references]

#### Calculated Fields

| Field Name | Formula | Purpose |
|-----------|---------|---------|
| [Name] | `[Tableau calc syntax]` | [What it computes] |

#### Filters
| Filter | Type | Values | Scope |
|--------|------|--------|-------|
| [Field] | [Dimension/Measure/Date] | [All/specific/relative] | [This sheet / All using datasource] |

#### Formatting
- **Font**: [size, weight, color]
- **Number format**: [e.g., #,##0 / $#,##0.00 / 0.0%]
- **Axis**: [show/hide, title, range]
- **Grid lines**: [show/hide]
- **Reference lines**: [if any]

---

[Repeat "Sheet: [Name]" section for every sheet in the dashboard]

---

## Section 3: Parameters

| Parameter Name | Data Type | Default | Allowable Values | Used In |
|---------------|-----------|---------|-----------------|---------|
| [Name] | [String/Integer/Float/Date/Boolean] | [default] | [All/List/Range] | [Which calc fields or filters use it] |

[If no parameters are needed, state: "No parameters required for this dashboard."]

---

## Dashboard Actions

| Action Name | Type | Source Sheet | Target Sheet(s) | Run On | Fields |
|------------|------|-------------|-----------------|--------|--------|
| [Name] | [Filter/Highlight/URL/Go to Sheet] | [sheet] | [sheet(s)] | [Select/Hover/Menu] | [field mappings] |

---

## Notes
[Any additional implementation notes, edge cases, or considerations]
```

## Guidelines

- Be explicit about every calculated field formula - a developer should not need to guess
- Use Tableau formula syntax for calculated fields (not SQL)
- Reference field names exactly as they appear in DS-ARCHITECTURE.md
- Use colors and fonts from design-tokens.md for all styling values
- Include number formats for all measures
- Specify tooltip content including field references in square brackets
- Document all dashboard actions including field mappings
- This document will be used as input for Step E (TWB generation) — ensure all details are machine-parseable