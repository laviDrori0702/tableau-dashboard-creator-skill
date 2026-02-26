# TWB XML Reference

Complete XML schema reference for generating valid `.twb` Tableau workbook files. Derived from real-world workbook analysis and cross-referenced with the [ranvithm/tableau.xml](https://github.com/ranvithm/tableau.xml) community documentation.

This document is the primary reference for **Step E: TWB Workbook Generation**.

---

## 1. Top-Level Structure

Element ordering within `<workbook>` is **strict** — follow this order exactly:

```xml
<?xml version='1.0' encoding='utf-8' ?>

<workbook original-version='18.1'
          source-build='2024.2.0 (20242.24.0705.1443)'
          source-platform='win'
          version='18.1'
          xmlns:user='http://www.tableausoftware.com/xml/user'>

  <!-- 1 --> <document-format-change-manifest> ... </document-format-change-manifest>
  <!-- 2 --> <preferences> ... </preferences>
  <!-- 3 --> <datasources> ... </datasources>
  <!-- 4 --> <worksheets> ... </worksheets>
  <!-- 5 --> <dashboards> ... </dashboards>
  <!-- 6 --> <windows> ... </windows>

</workbook>
```

> ⚠️ **Version compatibility**: Use `source-build='2024.2.0 (20242.24.0705.1443)'` as the default. Targeting too-new versions (e.g., 2025.1) will cause "Incompatible Document" errors on older Tableau Desktop installs. Do NOT include `locale` on the `<workbook>` tag.

### `<document-format-change-manifest>`

Feature flags — empty self-closing tags that signal format capabilities. Include the ones relevant to features used.

> ⚠️ **CRITICAL**: Use ONLY simple tag names (e.g., `<SheetIdentifierTracking />`). NEVER use FCP-prefixed names like `<_.fcp.AnimationOnByDefault.true...just />` — these are internal Tableau serialization artifacts that cause "Unrecognized Format Changes" errors on most Tableau Desktop versions.

For maximum compatibility, include only these minimal flags:

```xml
<document-format-change-manifest>
  <SheetIdentifierTracking />
  <WindowsPersistSimpleIdentifiers />
</document-format-change-manifest>
```

If the dashboard requires advanced features, you may also include these (all using simple names, never FCP-prefixed):

```xml
<document-format-change-manifest>
  <!-- Minimal baseline (always include) -->
  <SheetIdentifierTracking />
  <WindowsPersistSimpleIdentifiers />

  <!-- Include if using buttons -->
  <BasicButtonObject />
  <BasicButtonObjectTextSupport />

  <!-- Include if using collapsible panels -->
  <CollapsiblePane />

  <!-- Include if using DZV (zone visibility) -->
  <DatagraphCoreV1 />
  <DatagraphNodeDashboardZoneVisibilityV1 />
  <DatagraphNodeSingleValueFieldV1 />
  <ZoneBackgroundTransparency />
  <ZoneVisibilityControl />

  <!-- Include if using parameter actions -->
  <ParameterAction />
  <ParameterActionClearSelection />

  <!-- Include if using set membership controls -->
  <SetMembershipControl />
</document-format-change-manifest>
```

### `<preferences>`

```xml
<preferences>
  <preference name='ui.encoding.shelf.height' value='24' />
  <preference name='ui.shelf.height' value='26' />
</preferences>
```

---

## 2. Datasources

### 2.1 Parameters Datasource

Must be named exactly `'Parameters'` with `hasconnection='false'`:

```xml
<datasource hasconnection='false' inline='true' name='Parameters' version='18.1'>
  <aliases enabled='yes' />

  <!-- List-based parameter -->
  <column alias='DefaultAlias'
          caption='Parameter Display Name'
          datatype='string'
          datatype-customized='true'
          name='[Parameter 1]'
          param-domain-type='list'
          role='measure'
          type='nominal'
          value='&quot;DefaultValue&quot;'>
    <calculation class='tableau' formula='&quot;DefaultValue&quot;' />
    <aliases>
      <alias key='&quot;InternalVal1&quot;' value='Display Name 1' />
      <alias key='&quot;InternalVal2&quot;' value='Display Name 2' />
    </aliases>
    <members>
      <member alias='Display Name 1' value='&quot;InternalVal1&quot;' />
      <member alias='Display Name 2' value='&quot;InternalVal2&quot;' />
      <member value='&quot;ValueWithNoAlias&quot;' />
    </members>
  </column>

  <!-- Range-based parameter -->
  <column caption='Numeric Param'
          datatype='integer'
          name='[Parameter 2]'
          param-domain-type='range'
          role='measure'
          type='quantitative'
          value='10'>
    <calculation class='tableau' formula='10' />
    <range granularity='1' max='100' min='0' />
  </column>
</datasource>
```

**`param-domain-type` values**: `list` | `range` | `all`

### 2.2 CSV/Text Datasource

Uses a `federated` connection wrapping a `textscan` connection:

```xml
<datasource caption='Human Readable Name'
            inline='true'
            name='federated.{unique_id}'
            version='18.1'>

  <connection class='federated'>
    <named-connections>
      <named-connection caption='Human Readable Name'
                        name='textscan.{unique_id}'>
        <connection class='textscan'
                    directory='.'
                    filename='data.csv'
                    password=''
                    server='' />
      </named-connection>
    </named-connections>

    <relation connection='textscan.{unique_id}'
              name='data.csv'
              table='[data#csv]'
              type='table'>
      <columns character-set='UTF-8' header='yes' locale='en_US' separator=','>
        <column datatype='string'  name='Country'   ordinal='0' />
        <column datatype='integer' name='Sales'     ordinal='1' />
        <column datatype='real'    name='Revenue'   ordinal='2' />
        <column datatype='date'    name='OrderDate' ordinal='3' />
        <column datatype='boolean' name='IsActive'  ordinal='4' />
      </columns>
    </relation>

    <metadata-records>
      <!-- Always first: capability record -->
      <metadata-record class='capability'>
        <remote-name />
        <remote-type>0</remote-type>
        <parent-name>[data.csv]</parent-name>
        <remote-alias />
        <aggregation>Count</aggregation>
        <contains-null>true</contains-null>
        <attributes>
          <attribute datatype='string' name='character-set'>&quot;UTF-8&quot;</attribute>
          <attribute datatype='string' name='collation'>&quot;en_US&quot;</attribute>
          <attribute datatype='string' name='field-delimiter'>&quot;,&quot;</attribute>
          <attribute datatype='string' name='header-row'>&quot;true&quot;</attribute>
          <attribute datatype='string' name='locale'>&quot;en_US&quot;</attribute>
          <attribute datatype='string' name='single-char'>&quot;&quot;</attribute>
        </attributes>
      </metadata-record>

      <!-- One per column -->
      <metadata-record class='column'>
        <remote-name>Country</remote-name>
        <remote-type>129</remote-type>
        <local-name>[Country]</local-name>
        <parent-name>[data.csv]</parent-name>
        <remote-alias>Country</remote-alias>
        <ordinal>0</ordinal>
        <local-type>string</local-type>
        <aggregation>Count</aggregation>
        <scale>1</scale>
        <width>1073741823</width>
        <contains-null>true</contains-null>
        <collation flag='0' name='LEN_RUS' />
      </metadata-record>
    </metadata-records>
  </connection>

  <!-- Logical column overrides and calculated fields go here -->
  <aliases enabled='yes' />

  <!-- Override a physical column's role/type -->
  <column datatype='string' name='[Country]' role='dimension' type='nominal' />

  <!-- Calculated field -->
  <column caption='Revenue Per Unit'
          datatype='real'
          default-format='#,##0.00'
          name='[Calculation_1001]'
          role='measure'
          type='quantitative'>
    <calculation class='tableau' formula='[Revenue] / [Sales]' scope-isolation='false' />
  </column>

  <!-- Calculated field with percentage format -->
  <column caption='Profit Ratio'
          datatype='real'
          default-format='p0%'
          name='[Calculation_1002]'
          role='measure'
          type='quantitative'>
    <calculation class='tableau' formula='SUM([Profit])/SUM([Sales])' />
  </column>

  <layout dim-ordering='alphabetic' dim-percentage='0.5' measure-ordering='alphabetic' measure-percentage='0.4' show-structure='true' />
</datasource>
```

> ⚠️ **CRITICAL — `<layout>` required attributes**: The `<layout>` element inside each datasource MUST include `dim-percentage` and `measure-percentage` attributes. Omitting them causes "missing required attribute" errors in Tableau Desktop. Always use `dim-percentage='0.5' measure-percentage='0.4'` as defaults.

> ⚠️ **CRITICAL — `<relation>` must be plain**: Never wrap `<relation>` elements with FCP-prefixed names like `<_.fcp.ObjectModelEncapsulateLegacy.false...relation>`. Always use plain `<relation>` directly.

### Remote-Type Mapping

| remote-type | local-type | datatype  | Default aggregation | Extra elements        |
|-------------|------------|-----------|--------------------|-----------------------|
| 129         | string     | string    | Count              | `<scale>1</scale>`, `<width>1073741823</width>`, `<collation>` |
| 20          | integer    | integer   | Sum                | (none)                |
| 5           | real       | real      | Sum                | (none)                |
| 133         | date       | date      | Year               | (none)                |
| 11          | boolean    | boolean   | Count              | (none)                |

### Unique ID Generation

- Datasource name: `federated.{13_char_alphanum}` — e.g., `federated.1d4lmek1t0hsj`
- Named connection: `textscan.{13_char_alphanum}` — e.g., `textscan.15wlv6v15g9q1`
- Table reference: filename with `.` replaced by `#` in brackets — `[data#csv]`
- Calculation names: `[Calculation_{18_digit_number}]` — e.g., `[Calculation_2494149810928013364]`

---

## 3. Column Instances

Column instances define how fields are used in views. They appear inside `<datasource-dependencies>` in worksheets:

```xml
<!-- Dimension (no aggregation) -->
<column-instance column='[Country]'
                 derivation='None'
                 name='[none:Country:nk]'
                 pivot='key'
                 type='nominal' />

<!-- Measure (sum aggregation) -->
<column-instance column='[Sales]'
                 derivation='Sum'
                 name='[sum:Sales:qk]'
                 pivot='key'
                 type='quantitative' />
```

### Naming Convention: `[derivation:FieldName:typeCode]`

| Derivation | Meaning                |
|-----------|------------------------|
| `none`    | No aggregation (dimension) |
| `sum`     | SUM aggregation        |
| `avg`     | AVG aggregation        |
| `min`     | MIN aggregation        |
| `max`     | MAX aggregation        |
| `cnt`     | COUNT aggregation      |
| `usr`     | User/table calc        |
| `yr`      | Year date part         |
| `mn`      | Month date part        |

| Type Code | Meaning              |
|-----------|---------------------|
| `nk`      | Nominal key (dimension) |
| `ok`      | Ordinal key (ordered dimension) |
| `qk`      | Quantitative key (measure) |

---

## 4. Worksheets

### 4.1 Standard Chart Worksheet

```xml
<worksheet name='Sales by Country'>
  <table>
    <view>
      <datasources>
        <datasource caption='Data' name='federated.{id}' />
      </datasources>

      <datasource-dependencies datasource='federated.{id}'>
        <!-- Declare columns used in this sheet -->
        <column datatype='string' name='[Country]' role='dimension' type='nominal' />
        <column datatype='integer' name='[Sales]' role='measure' type='quantitative' />
        <!-- Declare column instances -->
        <column-instance column='[Country]' derivation='None' name='[none:Country:nk]' pivot='key' type='nominal' />
        <column-instance column='[Sales]' derivation='Sum' name='[sum:Sales:qk]' pivot='key' type='quantitative' />
      </datasource-dependencies>

      <!-- Optional: filters -->
      <filter class='categorical' column='[federated.{id}].[none:Country:nk]'>
        <groupfilter function='union' user:ui-domain='database' user:ui-enumeration='inclusive' user:ui-marker='enumerate'>
          <groupfilter function='member' level='[none:Country:nk]' member='&quot;USA&quot;' />
          <groupfilter function='member' level='[none:Country:nk]' member='&quot;UK&quot;' />
        </groupfilter>
      </filter>

      <!-- Optional: sort -->
      <computed-sort column='[federated.{id}].[none:Country:nk]'
                     direction='DESC'
                     using='[federated.{id}].[sum:Sales:qk]' />

      <slices>
        <column>[federated.{id}].[none:Country:nk]</column>
      </slices>

      <aggregation value='true' />
    </view>

    <style>
      <!-- Axis formatting -->
      <style-rule element='axis'>
        <format attr='tick-color' value='#00000000' />
      </style-rule>
      <!-- Grid lines -->
      <style-rule element='gridline'>
        <format attr='stroke-size' scope='cols' value='0' />
        <format attr='line-visibility' scope='cols' value='off' />
      </style-rule>
      <!-- Number format -->
      <style-rule element='cell'>
        <format attr='text-format' field='[federated.{id}].[sum:Sales:qk]' value='#,##0' />
      </style-rule>
    </style>

    <panes>
      <pane selection-relaxation-option='selection-relaxation-allow'>
        <view>
          <breakdown value='auto' />
        </view>
        <mark class='Bar' />
        <encodings>
          <color column='[federated.{id}].[none:Country:nk]' />
          <text column='[federated.{id}].[sum:Sales:qk]' />
        </encodings>
        <style>
          <style-rule element='mark'>
            <format attr='mark-labels-show' value='true' />
          </style-rule>
        </style>
      </pane>
    </panes>

    <!-- Shelf assignments (fully-qualified field references) -->
    <rows>[federated.{id}].[none:Country:nk]</rows>
    <cols>[federated.{id}].[sum:Sales:qk]</cols>
  </table>
  <simple-id uuid='{GUID}' />
</worksheet>
```

### 4.2 KPI Card Worksheet (Text-Only)

KPI sheets use empty shelves and `<customized-label>` for big-number display:

```xml
<worksheet name='KPI Total Sales'>
  <table>
    <view>
      <datasources>
        <datasource caption='Data' name='federated.{id}' />
      </datasources>
      <datasource-dependencies datasource='federated.{id}'>
        <column datatype='integer' name='[Sales]' role='measure' type='quantitative' />
        <column-instance column='[Sales]' derivation='Sum' name='[sum:Sales:qk]' pivot='key' type='quantitative' />
      </datasource-dependencies>
      <aggregation value='true' />
    </view>

    <style>
      <style-rule element='worksheet'>
        <format attr='display-field-labels' scope='rows' value='false' />
      </style-rule>
    </style>

    <panes>
      <pane selection-relaxation-option='selection-relaxation-allow'>
        <view>
          <breakdown value='auto' />
        </view>
        <mark class='Automatic' />
        <encodings>
          <text column='[federated.{id}].[sum:Sales:qk]' />
        </encodings>
        <customized-label>
          <formatted-text>
            <run bold='true' fontcolor='#181d27' fontname='Tableau Medium' fontsize='26'>&lt;</run>
            <run bold='true' fontcolor='#181d27' fontname='Tableau Medium' fontsize='26'>[federated.{id}].[sum:Sales:qk]</run>
            <run bold='true' fontcolor='#181d27' fontname='Tableau Medium' fontsize='26'>&gt;</run>
          </formatted-text>
        </customized-label>
      </pane>
    </panes>

    <rows />
    <cols />

    <!-- Suppress default tooltip on KPI cards -->
    <tooltip-style tooltip-mode='none' />
  </table>
  <simple-id uuid='{GUID}' />
</worksheet>
```

**Important**: The `&lt;` and `&gt;` around the field reference tell Tableau to substitute the actual value. The three `<run>` elements (open bracket, field ref, close bracket) are always needed.

> ⚠️ **Valid `<run>` attributes**: Only these attributes are allowed on `<run>` elements: `bold`, `fontcolor`, `fontname`, `fontsize`, `italic`, `underline`. Do NOT use `fontstyle` — it is not a valid attribute and will cause XML validation errors.

> ⚠️ **Color encoding placement**: Color assignments go inside `<pane><encodings><color column='...' />` — NEVER place `<color>` as a direct child of `<table>`. That is not part of the `<table>` content model.

> ⚠️ **Sorting**: Use `<computed-sort>` inside `<view>` (as shown in Section 4.1). Do NOT use a `<sort>` element — it does not exist in the TWB schema.

> ⚠️ **Calculated field formatting**: Use the `default-format` attribute on the `<column>` element (e.g., `default-format='$#,##0'`). Do NOT nest `<format>` elements inside `<column>` definitions — that causes "no declaration found for element 'format'" errors. Number formatting in worksheets goes in `<style><style-rule element='cell'>` instead.

### 4.3 KPI Delta Worksheet (Conditional Color)

```xml
<worksheet name='KPI Sales Delta'>
  <table>
    <view>
      <datasource-dependencies datasource='federated.{id}'>
        <!-- "Good"/"Bad" color field -->
        <column caption='Color Sales Delta' datatype='string' name='[Calculation_2001]' role='dimension' type='nominal'>
          <calculation class='tableau' formula='IF [KPI Sales Delta] &gt; 0 THEN &quot;Good&quot; ELSE &quot;Bad&quot; END' />
        </column>
        <!-- Delta percentage field -->
        <column caption='KPI Sales Delta' datatype='real' name='[Calculation_2002]' role='measure' type='quantitative'>
          <calculation class='tableau' formula='([This Period] - [Last Period]) / [Last Period]' />
        </column>
      </datasource-dependencies>
      <aggregation value='true' />
    </view>

    <style>
      <style-rule element='cell'>
        <!-- Triangle indicators in number format -->
        <format attr='text-format' field='[federated.{id}].[sum:Calculation_2002:qk]'
                value='*&#9650; +0.0%; &#9660; -0.0%' />
      </style-rule>
    </style>

    <panes>
      <pane>
        <mark class='Automatic' />
        <encodings>
          <color column='[federated.{id}].[none:Calculation_2001:nk]' />
          <text column='[federated.{id}].[sum:Calculation_2002:qk]' />
        </encodings>
      </pane>
    </panes>

    <rows />
    <cols />
    <tooltip-style tooltip-mode='none' />
  </table>
</worksheet>
```

### 4.4 Dual-Axis Line Chart (Multi-Pane)

Dual axis is expressed by repeating the measure in `<rows>` with `(A + A)` syntax:

```xml
<worksheet name='Trend Chart'>
  <table>
    <view> ... </view>
    <panes>
      <!-- Pane 0: primary axis (Line) -->
      <pane selection-relaxation-option='selection-relaxation-allow'>
        <mark class='Line' />
        <encodings>
          <color column='[federated.{id}].[none:Country:nk]' />
        </encodings>
      </pane>
      <!-- Pane 1: secondary axis (Circle for endpoints) -->
      <pane id='1' y-axis-name='[federated.{id}].[sum:Value:qk]'>
        <mark class='Circle' />
        <mark-sizing mark-sizing-setting='marks-scaling-off' />
        <encodings>
          <color column='[federated.{id}].[none:Country:nk]' />
          <size column='[federated.{id}].[none:TierSize:ok]' />
        </encodings>
      </pane>
    </panes>

    <!-- Dual-axis syntax: (measure + measure) -->
    <rows>([federated.{id}].[sum:Value:qk] + [federated.{id}].[sum:Value:qk])</rows>
    <cols>[federated.{id}].[none:Date:ok]</cols>
  </table>
</worksheet>
```

---

## 5. Dashboard

### 5.1 Dashboard Shell

```xml
<dashboard name='Dashboard Name'>
  <style />

  <size maxheight='800'
        maxwidth='1100'
        minheight='800'
        minwidth='1100' />

  <zones>
    <!-- Root zone -->
    <zone h='100000' id='4' type-v2='layout-basic' w='100000' x='0' y='0'>
      <!-- All content nested here -->
      <zone-style>
        <format attr='border-color' value='#000000' />
        <format attr='border-style' value='none' />
        <format attr='border-width' value='0' />
        <format attr='margin' value='0' />
      </zone-style>
    </zone>
  </zones>

  <simple-id uuid='{GUID}' />
</dashboard>
```

### 5.2 Zone Types Reference

| `type-v2` | Purpose | Key attributes |
|-----------|---------|---------------|
| `layout-basic` | Root container (absolute positioning) | Always outermost; `h='100000' w='100000' x='0' y='0'` |
| `layout-flow` | Flex container (vertical or horizontal) | `param='vert'` or `param='horz'` |
| *(absent)* | Worksheet reference | `name='Sheet Name'` (must match worksheet name) |
| `text` | Text label | Contains `<formatted-text>`, set `forceUpdate='true'` |
| `bitmap` | Image | `param='path/to/image'`, `is-scaled='1'` |
| `empty` | Spacer/filler | No `name` attribute |
| `color` | Color legend | `name='Sheet'`, `param='[field]'`, `leg-item-layout='horz'` |
| `filter` | Filter control | `mode='checkdropdown'`, `show-apply='true'` |
| `dashboard-object` | Button | Contains `<button>` element (see Section 5.5) |

### 5.3 Zone Attributes

| Attribute | Description | Example |
|-----------|-------------|---------|
| `id` | Unique integer ID within dashboard | `id='25'` |
| `h`, `w` | Height/width in internal units (100000 = full) | `h='98000' w='98400'` |
| `x`, `y` | Position offset | `x='800' y='1000'` |
| `fixed-size` | Fixed pixel size | `fixed-size='60'` |
| `is-fixed` | Boolean: size is fixed | `is-fixed='true'` |
| `param` | Direction (`horz`/`vert`), image path, or field ref | `param='horz'` |
| `friendly-name` | Human-readable label | `friendly-name='H-Header'` |
| `name` | Worksheet name (for sheet zones) | `name='KPI Sales'` |
| `layout-strategy-id` | `distribute-evenly` for equal-width children | On `layout-flow` zones |
| `show-title` | Show/hide zone title | `show-title='false'` |
| `hidden-by-user` | Zone starts hidden | `hidden-by-user='true'` |

### 5.4 Zone-Style

> ⚠️ **CRITICAL**: `<zone-style>` MUST ALWAYS be the **last child** of any zone element. If a zone contains other children (e.g., `<formatted-text>`, nested zones), `<zone-style>` comes after all of them. Incorrect ordering causes XML validation errors.

Every zone should have a `<zone-style>` child:

```xml
<zone-style>
  <format attr='border-color' value='#000000' />
  <format attr='border-style' value='none' />
  <format attr='border-width' value='0' />
  <format attr='margin' value='4' />
  <!-- Optional: -->
  <format attr='padding' value='0' />
  <format attr='padding-top' value='8' />
  <format attr='padding-right' value='24' />
  <format attr='padding-bottom' value='8' />
  <format attr='padding-left' value='24' />
  <format attr='background-color' value='#ffffff' />
</zone-style>
```

### 5.5 Buttons

**Navigation button** (Go to Sheet):

```xml
<zone type-v2='dashboard-object'>
  <button action='tabdoc:goto-sheet window-id=&quot;{GUID}&quot;' button-type='text'>
    <button-visual-state>
      <caption>Detail View --&gt;</caption>
      <button-caption-font-style fontname='Tableau Medium' fontsize='12' />
      <format attr='border-style' value='solid' />
      <format attr='border-width' value='1' />
      <format attr='border-color' value='#e9eaeb' />
    </button-visual-state>
  </button>
</zone>
```

**Toggle button** (show/hide a zone):

```xml
<zone type-v2='dashboard-object'>
  <button action='' active-visual-state-index='1'>
    <toggle-action>tabdoc:toggle-button-click-action
      window-id=&quot;{GUID}&quot; zone-id=&quot;76&quot; zone-ids=[22]</toggle-action>
    <button-visual-state />
    <button-visual-state>
      <image-path>path/to/icon.png</image-path>
    </button-visual-state>
  </button>
</zone>
```

### 5.6 Complete Zone Hierarchy Example

```xml
<zones>
  <zone h='100000' id='4' type-v2='layout-basic' w='100000' x='0' y='0'>
    <zone h='98000' id='3' param='vert' type-v2='layout-flow' w='98400' x='800' y='1000'>

      <!-- Header row (fixed height) -->
      <zone fixed-size='60' friendly-name='H-Header' h='8500' id='13'
            is-fixed='true' param='horz' type-v2='layout-flow' w='98400' x='800' y='1000'>
        <zone fixed-size='192' h='5500' id='9' is-fixed='true' is-scaled='1'
              param='path/to/logo.svg' type-v2='bitmap' w='20000' x='5600' y='2500'>
          <zone-style>
            <format attr='border-color' value='#000000' />
            <format attr='border-style' value='none' />
            <format attr='border-width' value='0' />
            <format attr='margin' value='0' />
          </zone-style>
        </zone>
        <zone fixed-size='342' forceUpdate='true' h='5500' id='11'
              is-fixed='true' type-v2='text' w='35000' x='26600' y='2500'>
          <formatted-text>
            <run bold='true' fontcolor='#000021' fontname='Open Sans' fontsize='36'>Dashboard Title</run>
          </formatted-text>
          <zone-style> ... </zone-style>
        </zone>
        <zone-style> ... </zone-style>
      </zone>

      <!-- KPI row (fixed height, evenly distributed) -->
      <zone fixed-size='120' friendly-name='H-KPIs' h='15000' id='14'
            is-fixed='true' layout-strategy-id='distribute-evenly'
            param='horz' type-v2='layout-flow' w='98400' x='800' y='9500'>

        <!-- KPI card: vertical container with accent bar + sheet -->
        <zone h='15000' id='34' param='vert' type-v2='layout-flow' w='32800' x='800' y='9500'>
          <!-- Accent bar (text zone, 3px, colored) -->
          <zone fixed-size='3' h='500' id='39' is-fixed='true'
                type-v2='text' w='32800' x='800' y='9500'>
            <formatted-text><run> </run></formatted-text>
            <zone-style>
              <format attr='border-style' value='none' />
              <format attr='border-width' value='0' />
              <format attr='margin' value='0' />
              <format attr='background-color' value='#4e79a7' />
            </zone-style>
          </zone>
          <!-- KPI sheet -->
          <zone h='14500' id='36' name='KPI Total Sales' w='32800' x='800' y='10000'>
            <zone-style>
              <format attr='border-style' value='none' />
              <format attr='border-width' value='0' />
              <format attr='margin' value='0' />
              <format attr='background-color' value='#ffffff' />
            </zone-style>
          </zone>
          <zone-style>
            <format attr='border-style' value='none' />
            <format attr='border-width' value='0' />
            <format attr='margin' value='4' />
            <format attr='background-color' value='#ffffff' />
          </zone-style>
        </zone>

        <!-- More KPI cards follow same pattern -->
        <zone-style> ... </zone-style>
      </zone>

      <!-- Chart row (flex height) -->
      <zone h='50000' id='60' layout-strategy-id='distribute-evenly'
            param='horz' type-v2='layout-flow' w='98400' x='800' y='25000'>
        <!-- Chart card -->
        <zone h='50000' id='61' param='vert' type-v2='layout-flow' w='49000' x='800' y='25000'>
          <!-- Title bar -->
          <zone fixed-size='46' h='5000' id='62' is-fixed='true'
                param='horz' type-v2='layout-flow' w='49000' x='800' y='25000'>
            <zone fixed-size='342' forceUpdate='true' h='5000' id='63'
                  is-fixed='true' type-v2='text' w='40000' x='800' y='25000'>
              <formatted-text>
                <run fontcolor='#000021' fontname='Open Sans' fontsize='15'>Chart Title</run>
              </formatted-text>
              <zone-style> ... </zone-style>
            </zone>
            <zone-style> ... </zone-style>
          </zone>
          <!-- Separator line -->
          <zone fixed-size='3' h='300' id='64' is-fixed='true' type-v2='text' w='49000'>
            <formatted-text><run> </run></formatted-text>
            <zone-style>
              <format attr='background-color' value='#f0f3f5' />
              <format attr='margin' value='0' />
              <format attr='padding' value='0' />
            </zone-style>
          </zone>
          <!-- Chart sheet (flex) -->
          <zone h='44700' id='65' name='Sales by Country' w='49000' x='800' y='30300'>
            <zone-style>
              <format attr='border-style' value='none' />
              <format attr='border-width' value='0' />
              <format attr='margin' value='0' />
              <format attr='background-color' value='#ffffff' />
            </zone-style>
          </zone>
          <zone-style>
            <format attr='margin' value='6' />
            <format attr='padding' value='8' />
            <format attr='background-color' value='#ffffff' />
          </zone-style>
        </zone>
        <zone-style> ... </zone-style>
      </zone>

    </zone>
    <zone-style> ... </zone-style>
  </zone>
</zones>
```

---

## 6. Dashboard Actions

> ⚠️ **CRITICAL — Placement**: Dashboard actions (`<action>`, `<edit-parameter-action>`) are placed at the **`<workbook>` level**, NOT inside `<dashboard>`. They appear between `</dashboards>` and `<windows>`. Placing `<actions>` inside `<dashboard>` violates the content model and causes errors.

### 6.1 Filter Action

```xml
<action caption='Filter by Country'
        name='[Action1_{GUID}]'>
  <activation auto-clear='true' type='on-select' />
  <source dashboard='Dashboard Name' type='sheet' worksheet='Source Sheet' />
  <command command='tsc:tsl-filter'>
    <param name='target' value='Target Sheet' />
  </command>
</action>
```

### 6.2 Highlight Action

Defined in the `<windows>` section under `<viewpoints>`:

```xml
<window class='dashboard' name='Dashboard Name'>
  <viewpoints>
    <viewpoint name='Sheet Name'>
      <highlight>
        <color-one-way>
          <field>[federated.{id}].[none:Country:nk]</field>
        </color-one-way>
      </highlight>
    </viewpoint>
  </viewpoints>
</window>
```

### 6.3 Parameter Action

```xml
<edit-parameter-action caption='Set Country Param'
                       name='[Action2_{GUID}]'>
  <activation type='on-select' />
  <source dashboard='Dashboard Name' type='sheet' worksheet='Source Sheet' />
  <agg-type type='attr' />
  <clear-option type='do-nothing' value='s:LROOT:' />
  <params>
    <param name='source-field' value='[federated.{id}].[none:Country:nk]' />
    <param name='target-parameter' value='[Parameters].[Parameter 1]' />
  </params>
</edit-parameter-action>
```

---

## 7. Color Palettes and Encodings

### Datasource-Level Color Palette

Defined in `<style>` under the datasource, maps field values to hex colors:

```xml
<style>
  <style-rule element='mark'>
    <!-- Categorical palette -->
    <encoding attr='color' field='[none:Country:nk]' type='palette'>
      <map to='#4e79a7'><bucket>&quot;USA&quot;</bucket></map>
      <map to='#f28e2b'><bucket>&quot;UK&quot;</bucket></map>
      <map to='#e15759'><bucket>&quot;Germany&quot;</bucket></map>
    </encoding>

    <!-- Boolean palette (good/bad) -->
    <encoding attr='color' field='[none:IsGood:nk]' type='palette'>
      <map to='#079455'><bucket>&quot;Good&quot;</bucket></map>
      <map to='#D92D20'><bucket>&quot;Bad&quot;</bucket></map>
    </encoding>

    <!-- Measure Names palette (for stacked bars) -->
    <encoding attr='color' field='[:Measure Names]' type='palette'>
      <map to='#7f56d9'><bucket>&quot;[federated.{id}].[sum:Sales:qk]&quot;</bucket></map>
      <map to='#b692f6'><bucket>&quot;[federated.{id}].[sum:Returns:qk]&quot;</bucket></map>
    </encoding>

    <!-- Size encoding -->
    <encoding attr='size' field='[none:TierSize:ok]'
              field-type='ordinal' max-size='1' min-size='0.19' type='catsize' />
  </style-rule>
</style>
```

### Pane-Level Encodings

```xml
<encodings>
  <color column='[federated.{id}].[none:Country:nk]' />
  <size column='[federated.{id}].[none:TierSize:ok]' />
  <text column='[federated.{id}].[sum:Sales:qk]' />
  <lod column='[federated.{id}].[none:Detail:nk]' />  <!-- Detail shelf -->
</encodings>
```

---

## 8. Style Rules Reference

### Target Elements

| `element` value | What it styles |
|----------------|---------------|
| `axis` | Axis lines, ticks, labels |
| `cell` | Cell height, number format, text alignment |
| `header` | Row/column headers |
| `pane` | Pane borders, min/max height |
| `worksheet` | Field label visibility |
| `gridline` | Grid lines |
| `zeroline` | Zero reference line |
| `dropline` | Drop lines |
| `refline` | Reference line styling |
| `mark` | Mark labels, size |
| `table` | Background color (worksheet background) |
| `table-div` | Table divider lines |

### Common Format Attributes

| `attr` | Values | Notes |
|--------|--------|-------|
| `border-color` | `#hex` | |
| `border-style` | `none` / `solid` | |
| `border-width` | Integer | |
| `background-color` | `#hex` | `#00000000` = transparent |
| `margin` | Integer | Shorthand for all sides |
| `padding` | Integer | Shorthand for all sides |
| `stroke-size` | Integer | Line thickness; `0` = hidden |
| `line-visibility` | `on` / `off` | |
| `tick-color` | `#hex` | `#00000000` = hidden |
| `display` | `true` / `false` | Show/hide element |
| `text-format` | Format string | e.g., `#,##0`, `0.0%`, `*&#9650; +0.0%` |
| `text-align` | `left` / `center` / `right` | |
| `vertical-align` | `top` / `center` / `bottom` | |
| `mark-labels-show` | `true` / `false` | |
| `fill-above` / `fill-below` | `#hex` | Reference line fill |

### Number Format Strings

| Format | Display | Usage |
|--------|---------|-------|
| `#,##0` | 1,234 | Integers with comma separator |
| `$#,##0.00` | $1,234.56 | Currency |
| `0.0%` | 12.3% | Percentage |
| `*#,##0` | 1,234 (no negative sign prefix) | Absolute value display |
| `*&#9650; +0.0%; &#9660; -0.0%` | ▲ +12.3% / ▼ -5.6% | Delta with triangle indicators |

---

## 9. Windows Section

Stores UI state. Include for each sheet:

```xml
<windows source-height='30'>
  <!-- Dashboard window -->
  <window class='dashboard' name='Dashboard Name'>
    <viewpoints>
      <viewpoint name='Sheet Name'>
        <zoom type='entire-view' />
      </viewpoint>
    </viewpoints>
    <active id='-1' />
    <simple-id uuid='{GUID}' />
  </window>

  <!-- Optional: devicelayout for auto-sizing -->
  <!-- ⚠️ Do NOT add auto-generated='true' — this attribute is not declared in older Tableau versions -->
  <window class='dashboard' name='Dashboard Name'>
    <devicelayouts>
      <devicelayout name='Phone'>
        <size maxheight='800' minheight='800' />
      </devicelayout>
    </devicelayouts>
  </window>

  <!-- Hidden worksheet (used only in dashboard) -->
  <window class='worksheet' hidden='true' name='Sheet Name'>
    <cards>
      <edge name='left'>
        <strip size='160'>
          <card type='pages' />
          <card type='filters' />
          <card type='marks' />
        </strip>
      </edge>
      <edge name='top'>
        <strip size='2147483647'><card type='columns' /></strip>
        <strip size='2147483647'><card type='rows' /></strip>
        <strip size='31'><card type='title' /></strip>
      </edge>
    </cards>
    <simple-id uuid='{GUID}' />
  </window>
</windows>
```

---

## 10. DZV (Dashboard Zone Visibility) Pattern

Advanced pattern for toggle-button UIs. Requires manifest flags: `DatagraphCoreV1`, `DatagraphNodeDashboardZoneVisibilityV1`, `DatagraphNodeSingleValueFieldV1`, `ZoneVisibilityControl`.

### Architecture

A datagraph connects boolean field values to zone visibility:

```xml
<datagraph>
  <!-- Reads a boolean field value from the datasource -->
  <single-value-field-node
      fieldname='[federated.{id}].[DZV: Toggle Active]'
      fieldname-input-guid='{GUID_A}'
      node-guid='{GUID_B}'
      value-output-guid='{GUID_C}' />

  <!-- Controls zone visibility based on the boolean -->
  <dashboard-zone-visibility-node
      dashboard-identifier='{DASHBOARD_GUID}'
      node-guid='{GUID_D}'
      visibility-input-guid='{GUID_E}'
      zone-id='104' />

  <!-- Connects field output → visibility input -->
  <edges>
    <edge from='{GUID_C}' to='{GUID_E}' />
  </edges>
</datagraph>
```

### Toggle Button Pair Pattern

For each toggle, create two worksheet zones (Active + InActive) and two DZV nodes:

1. **InActive zone** (`hidden-by-user='false'`, grey styling) — shown when toggle is OFF
2. **Active zone** (`hidden-by-user='true'`, accent color) — shown when toggle is ON
3. **Parameter action** on InActive zone — sets parameter value on click
4. **Filter action** on InActive zone — "unhighlight" with `auto-clear='true'`
5. **DZV nodes** — boolean fields in data control which zone is visible

The data must contain pre-computed boolean columns (e.g., `DZV: Toggle X Is Active` = TRUE when parameter matches X).

---

## 11. Generation Checklist

When generating a `.twb` file, ensure:

### Core XML
1. **XML declaration**: `<?xml version='1.0' encoding='utf-8' ?>`
2. **Element ordering**: Strict order within `<workbook>` (manifest → preferences → datasources → worksheets → dashboards → windows)
3. **Close all tags**: Tableau Desktop will reject malformed XML
4. **String escaping**: Use `&quot;` for quotes, `&lt;` / `&gt;` for angle brackets, `&amp;` for ampersands
5. **Unique IDs**: All datasource names, connection names, calculation names, zone IDs, and GUIDs must be unique

### Version & Manifest
6. **Versioning**: Use `version='18.1'` and `source-build='2024.2.0 (20242.24.0705.1443)'` — do NOT target 2025.x versions as they cause "Incompatible Document" errors on most Tableau installs
7. **Manifest flags**: Use ONLY simple tag names (e.g., `<SheetIdentifierTracking />`). NEVER use FCP-prefixed names like `<_.fcp.AnimationOnByDefault.true...just />`

### Datasources
8. **CSV path**: Use `directory='.'` and bare `filename` — NEVER use subdirectory paths (critical for `.twbx` packaging)
9. **`<relation>` must be plain**: Never wrap in FCP elements like `<_.fcp.ObjectModelEncapsulateLegacy.false...relation>`
10. **`<layout>` required attributes**: Every `<layout>` inside a datasource MUST include `dim-percentage='0.5'` and `measure-percentage='0.4'`
11. **Connection classes**: Use `class='textscan'` for CSV. Step E always generates CSV-based workbooks

### Column & Field Definitions
12. **Field references**: Always fully-qualified in worksheets: `[datasource_name].[column_instance_name]`
13. **Column consistency**: Every field used in a worksheet's shelves/encodings must be declared in `<datasource-dependencies>`
14. **Calculated field formatting**: Use `default-format` attribute on `<column>` (e.g., `default-format='$#,##0'`). NEVER nest `<format>` elements inside `<column>` definitions
15. **Calculated fields**: Add `scope-isolation='false'` on `<calculation>` when using LOD expressions

### Worksheets
16. **Valid `<run>` attributes**: Only `bold`, `fontcolor`, `fontname`, `fontsize`, `italic`, `underline`. NEVER use `fontstyle`
17. **Color encoding**: Place inside `<pane><encodings><color>` — NEVER as direct child of `<table>`
18. **Sorting**: Use `<computed-sort>` inside `<view>`. NEVER use `<sort>` element (does not exist in schema)

### Dashboard
19. **Zone IDs**: Sequential integers starting from 3-4, must not collide
20. **Zone coordinates**: Use 100000-based coordinate system (100000 = 100% of dashboard). E.g., `x='800' y='1000' w='98400'` spans 0.8%–99.2% width
21. **`<zone-style>` ordering**: MUST ALWAYS be the LAST child of any zone element — after all nested zones, `<formatted-text>`, etc.
22. **Dashboard actions**: Place at `<workbook>` level (between `</dashboards>` and `<windows>`), NOT inside `<dashboard>`
23. **`<devicelayout>`**: Do NOT use `auto-generated='true'` attribute — it is not declared in older Tableau versions