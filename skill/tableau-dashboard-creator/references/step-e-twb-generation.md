# Step E: TWB Workbook Generation (Experimental)

**Identity**: You are a Tableau XML engineer. Your goal is to generate a valid `.twbx` packaged workbook that can be opened directly in Tableau Desktop, fully implementing the dashboard from Step D's specification.

> **Experimental**: This step generates Tableau XML programmatically. The output should be a functional starting point, though minor adjustments in Tableau Desktop may be needed.

## Process

1. **Read the approved TABLEAU-IMPLEMENTATION.md** (`mock-version/v_N/TABLEAU-IMPLEMENTATION.md`)
2. **Read design-tokens.md** for all styling values
3. **Read DS-ARCHITECTURE.md** for field names and types
4. **Locate sample data CSVs** from Step A (in the project root or `sample-data/` directory)
5. **Read the TWB XML reference** from [twb-xml-reference.md](twb-xml-reference.md)
6. **Generate `dashboard.twb`** as valid Tableau XML (intermediate file)
7. **Package as `dashboard.twbx`** — a ZIP archive bundling the `.twb` with all sample CSV data files
8. **Save to** `mock-version/v_N/dashboard.twbx` (also keep `dashboard.twb` for reference)

---

## TWB XML Structure Overview

A `.twb` file is XML with this top-level structure:

```xml
<?xml version='1.0' encoding='utf-8' ?>
<workbook ...>
  <preferences />
  <datasources>
    <datasource name='...' caption='...' inline='true'>
      <connection class='textscan' directory='...' filename='...' />
      <column datatype='...' name='...' role='...' type='...' />
      ...
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name='...'>
      <table>
        <view> ... </view>
        <style> ... </style>
        <panes> ... </panes>
      </table>
    </worksheet>
  </worksheets>
  <dashboards>
    <dashboard name='...'>
      <zones>
        <zone type='layout-basic'> ... </zone>
      </zones>
    </dashboard>
  </dashboards>
</workbook>
```

**Refer to [twb-xml-reference.md](twb-xml-reference.md) for the complete XML schema, element ordering, required attributes, and examples.**

---

## Implementation Requirements

### 1. Datasources

For each datasource in DS-ARCHITECTURE.md:

- Use `class='textscan'` connection type (CSV files)
- **Always** set `directory='.'` (current directory) and `filename` to the bare CSV file name — this is critical for `.twbx` packaging where CSVs sit alongside the `.twb` inside the archive
- Define `<column>` elements for every field with correct:
  - `datatype`: `string`, `integer`, `real`, `date`, `datetime`, `boolean`
  - `role`: `dimension` or `measure`
  - `type`: `nominal`, `ordinal`, `quantitative`
  - `name`: field name enclosed in brackets `[field_name]`
  - `caption`: human-readable name from DS-ARCHITECTURE.md

### 2. Calculated Fields

For each calculated field in TABLEAU-IMPLEMENTATION.md:

```xml
<column caption='Calc Name' datatype='...' name='[Calculation_N]' role='...' type='...'>
  <calculation class='tableau' formula='...' />
</column>
```

- Use Tableau formula syntax (not SQL)
- Reference field names exactly as defined in the datasource columns

### 3. Parameters

For each parameter in TABLEAU-IMPLEMENTATION.md:

```xml
<datasource name='Parameters' caption='Parameters' inline='true'>
  <column caption='Param Name' datatype='...' name='[Parameter N]'
          param-domain-type='...' role='measure' type='quantitative' value='...'>
    <range granularity='...' max='...' min='...' />  <!-- for range type -->
    <members>  <!-- for list type -->
      <member value='...' />
    </members>
  </column>
</datasource>
```

### 4. Worksheets

For each sheet in TABLEAU-IMPLEMENTATION.md:

- **Mark type**: Set via `<mark class='...' />`
- **Shelves**: Map Columns/Rows/Color/Size/Label/Detail/Tooltip to the correct XML elements
- **Filters**: Define `<filter>` elements with correct field references and filter types
- **Formatting**: Apply font sizes, colors, number formats, axis visibility from design-tokens.md
- **Tooltips**: Use `<formatted-text>` with field references in `<run>` elements

### 5. Dashboard

Build the zone hierarchy matching the Container Tree from TABLEAU-IMPLEMENTATION.md:

```xml
<dashboard name='Dashboard Name'>
  <size maxheight='...' maxwidth='...' minheight='800' minwidth='1100' />
  <zones>
    <zone type='layout-basic' ...>
      <zone type='layout-flow' flow='vertical' ...>
        <!-- Nested zones matching the container hierarchy -->
      </zone>
    </zone>
  </zones>
</dashboard>
```

- Apply `fixed-size`, `margin`, `padding` attributes from the implementation spec
- Set background colors via `<format>` elements
- Reference worksheets via `<zone type='sheet' name='...'>`

### 6. Dashboard Actions

For each action in TABLEAU-IMPLEMENTATION.md:

```xml
<actions>
  <action name='...' caption='...'>
    <source type='sheet' worksheet='...' dashboard='...' />
    <target type='sheet' worksheet='...' dashboard='...' />
    <command command='...'> <!-- tsc:filter, tsc:highlight, tsc:url -->
      <param name='field' value='...' />
    </command>
  </action>
</actions>
```

---

## Sample Data as Datasource

The generated `.twb` will use CSV files as its datasource:

- If `sample-data/` contains the CSVs → use those files
- If Step A generated CSVs during query execution → use those
- The CSV column headers must match the field names in DS-ARCHITECTURE.md exactly
- **Inside the `.twb` XML, always use `directory='.'`** — never use `sample-data/` or any other subdirectory path, because in the `.twbx` package the CSVs are placed at the root alongside the `.twb`

**Important note for the user**: After opening the generated workbook in Tableau Desktop, use **Data → Replace Data Source** to swap the sample CSV datasources with your live database connections.

## Packaging as .twbx

After generating the `.twb`, **always** package it as a `.twbx` (Tableau Packaged Workbook). This bundles the data files with the workbook, eliminating path-resolution errors when the user opens the file.

### .twbx Structure

A `.twbx` is a standard ZIP archive with this internal layout:

```
dashboard.twbx (ZIP)
├── dashboard.twb          ← the workbook XML
├── sales_orders.csv       ← sample data files (flat, at root)
├── customer_segments.csv
└── monthly_targets.csv
```

### Packaging Script (Python)

Use the following Python snippet to create the `.twbx`:

```python
import zipfile
import os

def create_twbx(twb_path: str, csv_paths: list[str], twbx_path: str) -> None:
    """
    Package a .twb and its CSV datasources into a .twbx archive.

    Args:
        twb_path: Path to the generated .twb file
        csv_paths: List of paths to CSV data files
        twbx_path: Output path for the .twbx file
    """
    with zipfile.ZipFile(twbx_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add the .twb at the archive root
        zf.write(twb_path, os.path.basename(twb_path))
        # Add each CSV at the archive root (flat alongside the .twb)
        for csv_path in csv_paths:
            zf.write(csv_path, os.path.basename(csv_path))
```

Run this after saving the `.twb`:
1. Collect all CSV file paths from `sample-data/` (or wherever Step A placed them)
2. Call `create_twbx()` with the `.twb` path, CSV paths, and the desired `.twbx` output path
3. Save both `dashboard.twb` and `dashboard.twbx` to `mock-version/v_N/`

---

## CRITICAL RULES — Common Pitfalls to Avoid

These rules are derived from real Tableau Desktop validation failures. Violating any of them will cause the workbook to fail to open.

### Version & Manifest
1. **Use `source-build='2024.2.0 (20242.24.0705.1443)'`** — targeting 2025.x causes "Incompatible Document" errors on most installs
2. **Manifest flags must use simple names only** — e.g., `<SheetIdentifierTracking />`. NEVER use FCP-prefixed names like `<_.fcp.AnimationOnByDefault.true...just />` (causes "Unrecognized Format Changes" errors)
3. **Keep manifest minimal** — only `<SheetIdentifierTracking />` and `<WindowsPersistSimpleIdentifiers />` unless specific features require more

### Datasource Structure
4. **`<relation>` must be plain** — never wrap with `<_.fcp.ObjectModelEncapsulateLegacy.false...relation>` or any FCP wrapper
5. **`<layout>` requires `dim-percentage` and `measure-percentage`** — always include `dim-percentage='0.5' measure-percentage='0.4'`. Omitting these causes "missing required attribute" errors
6. **`directory='.'` always** — never use subdirectory paths like `sample-data/` in datasource connections

### Column & Formatting
7. **Calculated field formatting uses `default-format` attribute** — e.g., `<column default-format='$#,##0' ...>`. NEVER nest `<format>` elements inside `<column>` (causes "no declaration found for element 'format'" error)
8. **Valid `<run>` attributes**: `bold`, `fontcolor`, `fontname`, `fontsize`, `italic`, `underline` only. NEVER use `fontstyle` (not declared in schema)

### Worksheet Structure
9. **Color encoding goes in `<pane><encodings>`** — `<color column='...' />` inside `<encodings>`. NEVER place `<color>` as direct child of `<table>`
10. **Sorting uses `<computed-sort>`** — place inside `<view>`. There is NO `<sort>` element in the TWB schema
11. **Measure Names field reference**: Use `[:Measure Names]` (with colon prefix). NEVER use `:Measure Names` without brackets

### Dashboard Structure
12. **`<zone-style>` must be the LAST child** of any zone — after all nested zones, `<formatted-text>`, etc. Wrong ordering causes validation errors
13. **Dashboard actions go at `<workbook>` level** — between `</dashboards>` and `<windows>`. NEVER place `<actions>` inside `<dashboard>` (not part of content model)
14. **No `auto-generated` on `<devicelayout>`** — this attribute is not declared in older Tableau versions
15. **No `layout-flow-orientation` format attribute** — flow direction is controlled by `param='vert'` or `param='horz'` on the zone, not via format attributes

---

## Validation Checklist

Before saving the `.twb`, verify:

- [ ] XML is well-formed (all tags properly closed)
- [ ] `source-build` is `2024.2.0` (NOT 2025.x)
- [ ] Manifest uses only simple tag names (no FCP prefixes)
- [ ] All datasource connections use `directory='.'` (NOT `sample-data/` or any subdirectory)
- [ ] All `<layout>` elements include `dim-percentage` and `measure-percentage`
- [ ] All `<relation>` elements are plain (no FCP wrappers)
- [ ] All datasource field names match DS-ARCHITECTURE.md
- [ ] All calculated field formatting uses `default-format` attribute (no nested `<format>`)
- [ ] All `<run>` elements use only valid attributes (no `fontstyle`)
- [ ] Color encodings are inside `<pane><encodings>` (not on `<table>`)
- [ ] All sorting uses `<computed-sort>` (not `<sort>`)
- [ ] All `<zone-style>` elements are the last child of their parent zone
- [ ] Dashboard actions are at `<workbook>` level (not inside `<dashboard>`)
- [ ] No `auto-generated` attribute on `<devicelayout>`
- [ ] All worksheets reference valid datasource fields
- [ ] Dashboard zone hierarchy matches TABLEAU-IMPLEMENTATION.md container tree
- [ ] Design tokens (colors, fonts, spacing) are applied throughout
- [ ] The `.twbx` archive contains the `.twb` and all referenced CSV files

---

## Output

Save both files to `mock-version/v_N/`:
- `dashboard.twb` — the raw XML workbook (for reference/debugging)
- `dashboard.twbx` — the packaged workbook with embedded CSV data (**primary deliverable**)

Present to the user with these instructions:
1. Open **`dashboard.twbx`** in Tableau Desktop (this is the packaged file with data included)
2. The workbook uses sample CSV data from Step A — no path configuration needed
3. Go to **Data → Replace Data Source** to connect to your live database
4. Review and adjust any formatting or layout details as needed
5. The raw `dashboard.twb` is also available for manual inspection or debugging

---

## Limitations

- Complex Tableau features (e.g., Level of Detail expressions, table calculations with addressing) may require manual adjustment
- Custom shapes, map layers, and image-based marks are not generated
- The workbook targets Tableau Desktop 2023.1+ compatibility
- Datasource connection credentials are NOT embedded — the user must configure these after replacing datasources

> **Important — this is an iterative process.** The generated `.twbx` is unlikely to be perfect on the first attempt. Tableau's XML schema is complex and strict — minor issues (wrong field references, layout quirks, formatting mismatches) are expected. Encourage the user to open the workbook in Tableau Desktop, identify what needs fixing, and report back. Each iteration improves fidelity. This step is experimental by nature — treat it as a strong starting point, not a finished product.