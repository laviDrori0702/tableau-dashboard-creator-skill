# Step C: HTML Mock Creation

**Identity**: You are a Tableau developer creating an interactive HTML mock that stakeholders can preview in a browser. The mock must faithfully represent Tableau's layout constraints and the customer's design system as defined in `design-tokens.md`.

## Process

1. **Read the approved DASHBOARD-PLAN.md**
2. **Read design-tokens.md** (generated in Step 0) for all styling values
3. **Select the matching template layout** based on the recommended layout in the dashboard plan
4. **Build the HTML mock** following Tableau constraints
5. **Save to** `mock-version/v_N/mock.html`

## Technical Requirements

### HTML Structure
- Single self-contained HTML file (inline CSS + JS)
- Use Chart.js via CDN for interactive charts: `https://cdn.jsdelivr.net/npm/chart.js`
- Load the font family specified in design-tokens.md (default: Open Sans via Google Fonts)
- Responsive within the sizing range defined in design-tokens.md (default: min 1100px wide x 800px tall, no max)

### Tableau Fidelity Rules

**CRITICAL - these constraints must be followed:**

1. **No rounded corners** anywhere (Tableau does not support them until 2026.1)
   - `border-radius: 0` on all elements
2. **Container hierarchy** must match Tableau's zone model:
   - Outer: `layout-basic` (absolute positioning root)
   - Inner: `layout-flow` containers (vertical or horizontal flexbox)
   - Fixed-size containers use explicit pixel heights
   - Flex containers fill remaining space
3. **No box shadows** unless explicitly requested (Tableau does not have native shadows)
4. **Border-style: none** on all containers (except logo zone which uses a background-blending border)
5. **Fixed-size elements**: Use explicit `min-height` / `min-width` in CSS on structural elements (title bars, KPI rows, filter bars, accent bars, icons, logo) to prevent compression on smaller screens. Only chart areas and main content should flex.
6. **Tableau-native terminology**: Use Tableau spacing terms (`margin`, `padding`) in design documentation — avoid CSS-specific terms like `gap` which have no Tableau equivalent.

### Design Token Application

Apply all values from `design-tokens.md`. Map the tokens to CSS:

```css
/* Map these from design-tokens.md — values shown are examples */
body {
    font-family: /* from design-tokens: Typography > Font family */;
    background-color: /* from design-tokens: Colors > Dashboard background */;
    margin: 0;
    font-size: /* from design-tokens: Typography > Worksheet default font size */;
}

.dashboard-title {
    font-size: /* from design-tokens: Typography > Dashboard title size */;
    font-weight: /* from design-tokens: Typography > Dashboard title weight */;
    color: /* from design-tokens: Colors > Text > Dark */;
    background-color: /* from design-tokens: Colors > Top banner area */;
    padding: 4px;
    padding-bottom: 0;
    margin-bottom: 1px;
}

.chart-title {
    font-size: /* from design-tokens: Typography > Chart title size */;
    color: /* from design-tokens: Colors > Text > Dark */;
    margin: 4px;
    margin-left: 10px;
}

.filter-label {
    font-size: /* from design-tokens: Typography > Filter labels size */;
    font-weight: bold;
    color: /* from design-tokens: Colors > Text > Medium */;
}

.chart-card {
    background-color: /* from design-tokens: Colors > Chart card background */;
    padding: 8px;
    border: none;
    border-radius: 0;  /* CRITICAL */
}

.kpi-accent-bar {
    height: 3px;
    margin: 0;
    /* background-color: from design-tokens: Colors > Accent Colors */
}

.separator-line {
    height: 3px;
    background-color: /* from design-tokens: Colors > Separator line */;
    margin: 0 10px;
}

/* Inner padding for all worksheet/sheet zones — space between zone border and content */
.sheet-zone {
    padding: 8px;
}

/* Flexible spacer — every flow container should include one to prevent layout collapse */
.spacer {
    flex: 1;
}
```

### Container Layout Pattern

Follow this HTML structure mirroring Tableau's zone hierarchy (adapt based on design-tokens.md container hierarchy):

```html
<div class="dashboard-root">                    <!-- layout-basic -->
  <div class="content-wrapper">                 <!-- Content (vert flow) -->
    <div class="top-banner">                    <!-- Logo, Info, Last update -->
      <div class="logo-area">...</div>
      <div class="spacer"></div>                <!-- Flexible spacer -->
      <div class="update-info">...</div>
    </div>
    <div class="dashboard-title">Title</div>    <!-- Dashboard Title -->
    <div class="filter-bar">                    <!-- Top Filters -->
      <span class="filter-label">Filters</span>
      <div class="filter-controls">...</div>
      <div class="spacer"></div>                <!-- Flexible spacer -->
    </div>
    <div class="main-content">                  <!-- Charts & Hidden Filters -->
      <div class="charts-area">                 <!-- KPI & Charts -->
        <div class="kpi-row">...</div>          <!-- Main KPI (if applicable) -->
        <div class="chart-row">...</div>        <!-- Chart rows -->
        <div class="spacer"></div>              <!-- Flexible spacer -->
      </div>
      <div class="hidden-filters">...</div>     <!-- Hidden Filters panel -->
    </div>
  </div>
</div>
```

### Logo Integration

If a logo file was identified in design-tokens.md:
- Embed the logo in the top-banner area
- For SVG: inline the SVG or use `<img>` with a relative path
- For PNG: use `<img>` with a relative path or base64-encode for self-containment
- Match the dimensions and padding from the design tokens

### Icon Integration

Chart title bars should include a small icon for visual enrichment:
- If `branding/icons/` contains SVG files, use the matching icon for each chart (filenames should match the icon names suggested in DASHBOARD-PLAN.md, e.g., `bar-chart.svg`, `trend.svg`)
- If no icons are provided, generate simple monochrome 40x40 SVG icons inline, using the brand primary color and matching the chart type (e.g., a small bar-chart icon for bar charts, a line icon for trend charts)
- Icons are placed in the chart title bar at 40x40 pixels, before the chart title text

### Chart Implementation

Use Chart.js with dummy data that represents the expected data shape:
- Match chart types from DASHBOARD-PLAN.md
- Use realistic dummy values and labels
- Apply chart series colors from design-tokens.md
- Use accent colors from design-tokens.md for KPI cards
- Include tooltips showing the metric name and value

### Interactive Elements

Implement where applicable:
- **Filter dropdowns**: HTML `<select>` elements that filter chart data via JS
- **Dashboard action filters**: Click handlers on charts that highlight/filter other charts
- **Collapsible hidden filters panel**: Toggle button functionality (DZV pattern)
- **KPI cards**: Display values with optional comparison indicators

## Output

Save to `mock-version/v_N/mock.html` where N is the current version number (start with 1).

Present the mock to the user (tell them to open the HTML file in a browser) and **wait for approval** before proceeding to Step D. If the user requests changes, overwrite `mock.html` in the current `v_N` directory (do NOT create a new version directory — version increments only happen at Steps D or E).

When the user approves the mock, review whether the approved mock diverged from the current `DASHBOARD-PLAN.md` or `design-tokens.md` (e.g., added/removed KPIs, changed chart types, adjusted layout, new colors). If it did, update those root-level files to match the approved mock before proceeding to Step D.

> **Important — this is an iterative process.** The HTML mock is unlikely to be perfect on the first attempt. Expect multiple revision cycles — this is normal and by design. Encourage the user to share the mock with stakeholders for feedback before approving. A well-validated mock saves significant rework in later steps (D and E). Take your time here — it's better to iterate on the mock than to rebuild the implementation spec or workbook.