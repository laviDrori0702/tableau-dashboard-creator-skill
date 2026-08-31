# Worksheet formatting: which element Desktop hangs each format off

What Desktop-saved workbooks write for the palette encoding and the four Format panes
(Shading / Borders / Lines / Alignment). Backs `worksheet._add_palette`,
`worksheet._add_sheet_format` and `twb._lift_palettes` (issue #52, introduced by #44).

Attested by scanning 40+ Desktop-saved workbooks from the analyst's local corpus (the
downloads folder, the Tableau repository's `Workbooks/`, and sibling project directories),
document versions 18.1 through 2026.2, this repo's own output excluded. No Desktop session
was needed: the corpus settled five of the six constructs outright. The sixth - the
categorical palette - it settled only negatively, and the gap is spelled out below.

| construct | what Desktop writes | occurrences | verdict on #44's guess |
| --- | --- | --- | --- |
| categorical palette | `<encoding … palette='X' type='palette'>` naming a palette, with `<map>` children pinning each member | 17 named, 0 inline `regular` palettes anywhere | **wrong** - the inline form is ruled out; the replacement is partly inferred (below) |
| continuous ramp | `<encoding … palette='X' type='interpolated'/>` with no children, or `type='custom-interpolated'` with an inline `ordered-sequential`/`ordered-diverging` palette whose `name` is `""` | 320 named / 69 inline | **wrong** - `interpolated` was paired with an inline palette, which is neither shape |
| Shading > Pane | `<style-rule element='pane'><format attr='background-color' …/>` | 72, 6 files | correct |
| Borders > Cell | `<style-rule element='cell'>` + `border-color` / `border-style` (`solid`/`none`) / `border-width` (`0`/`1`/`5`) | 43 / 67 / 50 | correct |
| Lines > Grid Lines | `<style-rule element='gridline'><format attr='stroke-color' …/>` | 173, 14 files | correct |
| Lines > *off* | `<format attr='line-visibility' value='off'/>` | gridline 316, zeroline 293 | **wrong** - `display='false'` appears **0 times** on either element |
| Alignment > Cell | `<style-rule element='cell'>` + `text-align` / `vertical-align` | 597 / 426 | correct (already attested by the KPI card) |

Three things the samples settle:

1. **A custom palette is defined once and referenced by name.** The definition goes in
   `<workbook><preferences>`; the encoding that uses it carries `palette='<name>'`. This is
   what keeps `#44`'s brand colours reachable *without* a `Preferences.tps` on the analyst's
   machine - the definition travels inside the workbook.
2. **An inline `<color-palette>` is a continuous-only shape.** All 69 inline palettes are
   `ordered-sequential` or `ordered-diverging` under `type='custom-interpolated'`, and every
   one has `name=""` and no `palette=` attribute on the encoding. Zero are `type='regular'`.
3. **`display` is not how a line is turned off.** `line-visibility='off'` is, in 609 samples
   across 27 files. `display='false'` is legal per `StyleAttribute-ST` and would have shown
   nothing - the silent-rewrite class of bug the issue was filed for.

## What is still inferred: the categorical palette

The builder emits `<encoding attr='color' type='palette' palette='Brand'/>` with **no**
`<map>` children, resolving against a `type='regular'` palette in `<preferences>`. Both
halves are attested, but not this composition:

| step | attested? | evidence |
| --- | --- | --- |
| `palette='<name>'` is read on a `type='palette'` encoding | **yes** | 17 encodings, 4 names (`tableau10_10_0`, `tableau-10`, `nuriel_stone_10_0`, `cyclic_10_0`), builds 2018.1 - 2025.1.10 |
| a custom palette lives in `<preferences>` and is referenced by name | **yes** | `FINANCIAL SERVICES - Trading.twbx`, `type='ordered-diverging'` |
| `type='regular'` is a legal palette type | **yes** | `PaletteType-ST` in the 2026.1 XSD; it is also `Preferences.tps`'s categorical form |
| a `type='regular'` palette in `<preferences>`, referenced by a categorical encoding **with no `<map>` children** | **no** | not one sample - every attested categorical encoding also enumerates its members as `<map>` |

So the composition is an inference, and the honest reading of the corpus is that Desktop,
having assigned a categorical palette, *materialises* the assignment as one `<map>` per
member - `Region` in `BankCustomers.twbx` has four members and four maps, not a partial
override. The builder cannot write maps: it never sees the data's members, which is
`#44`'s founding constraint.

What this change buys is that the *ruled-out* half is gone. The inline
`type='regular'` palette had zero support in 40+ workbooks; the named reference has 17
positive samples for the attribute and an attested definition site for the name. If Desktop
still drops the colours on open, the remaining fallback is a named **built-in** palette
(`tableau10_10_0`), which loses the brand hexes and reopens `#44`'s palette question.
Confirming this needs the one thing the corpus cannot supply: a Desktop save of a workbook
whose categorical palette was picked from a `Preferences.tps` custom entry.

The source workbooks are not vendored here (several are customer dashboards). The fragments
below are the whole of what was read.

## Categorical palette + named reference - `FINANCIAL SERVICES - Trading.twbx` (build 2023.3.0)

The palette lives at workbook level, immediately after `<document-format-change-manifest>`,
which is where `WorkbookFile-CT` puts `Workbook-Preferences-G`:

```xml
<preferences>
  <preference name='ui.encoding.shelf.height' value='24' />
  <preference name='ui.shelf.height' value='26' />
  <color-palette custom='true' name='Tableau Accelerators Diverging' type='ordered-diverging'>
    <color>#f5b574</color>
    <color>#ebbc8d</color>
    <color>#e0c2a5</color>
    <color>#ddcebf</color>
    <color>#d9d9d9</color>
    <color>#9db8ce</color>
    <color>#6196c3</color>
    <color>#4679a7</color>
    <color>#2b5c8a</color>
  </color-palette>
</preferences>
```

and each worksheet references it by name, with no children of its own:

```xml
<style-rule element='mark'>
  <encoding attr='color' field='[federated.…].[usr:Calculation_…:qk]'
            palette='Tableau Accelerators Diverging' type='interpolated' />
</style-rule>
```

The same name-reference shape covers the built-ins: `palette='blue_10_0'` (95),
`palette='red_black_10_0'` (23), `palette='orange_blue_diverging_10_0'` (6),
`palette='tableau-orange-blue'` (15), and a `Preferences.tps` custom
`palette='rapaport-ordered-diverging'` (16).

## Categorical encodings - `Superstore - Dashboard Publishing.twbx` (build 2025.1.10), `BankCustomers.twbx` (build 2022.4.0)

`type='palette'` names its base palette **and** pins every member. This is Desktop's
*assign specific colours* form; it needs the data members, which is why the builder cannot
write it:

```xml
<encoding attr='color' field='[none:Calculation_6401103171259723:nk]'
          palette='tableau10_10_0' type='palette'>
  <map to='#4e79a7'><bucket>&quot;Shipped Early&quot;</bucket></map>
  <map to='#9c755f'><bucket>&quot;Shipped Late&quot;</bucket></map>
  <map to='#bab0ac'><bucket>&quot;Shipped On Time&quot;</bucket></map>
</encoding>
```

`BankCustomers.twbx` shows the same on `[none:Region:nk]` with `palette='tableau-10'` and
four maps for four members. A `type='palette'` encoding carrying **only** a `palette=` name
appears nowhere - see the inference table above.

## Inline continuous ramp - `Vehicle Registration Data.twbx` (build 2024.2.10)

Note `name=""`, `custom='true'`, `type='custom-interpolated'` on the encoding, and no
`palette=` attribute:

```xml
<encoding attr='color' field='[federated.…].[sum:car_num:qk]' type='custom-interpolated'>
  <color-palette custom='true' name='' type='ordered-sequential'>
    <color>#f1f1f1</color>
    <color>#e7ecdc</color>
    <color>#dee8c7</color>
    <!-- … 11 stops total … -->
    <color>#9dc43c</color>
  </color-palette>
</encoding>
```

## Lines off - `Churns Analysis.twbx` (build 2025.1.10), `Rapaport Subscriptions Waterfall.twbx` (build 2026.1.0)

```xml
<style-rule element='zeroline'>
  <format attr='stroke-size' scope='cols' value='0' />
  <format attr='line-visibility' scope='cols' value='off' />
  <format attr='stroke-size' scope='rows' value='0' />
  <format attr='line-visibility' scope='rows' value='off' />
</style-rule>
<style-rule element='gridline'>
  <format attr='stroke-size' scope='cols' value='0' />
  <format attr='line-visibility' scope='cols' value='off' />
</style-rule>
```

The `scope='rows'`/`scope='cols'` split is Desktop's per-axis form. The unscoped rule is
equally attested - `Rapaport Subscriptions Waterfall.twbx` carries
`<format attr='line-visibility' value='off'/>` with an unscoped `stroke-size='0'`, and
`FINANCIAL SERVICES - Trading.twbx` (build 2023.3.0) carries the unscoped
`line-visibility='off'` on its own. The builder emits the unscoped form alone, because no
manifest asks for a per-axis line.

## Shading - `Vehicle Registration Data.twbx` (build 2024.2.10)

```xml
<style-rule element='pane'>
  <format attr='background-color' value='#ffffff' />
</style-rule>
```

## Borders and gridline colour - `High Activity Accounts.twbx` (build 2025.1.10)

The one file that carries all three unscoped, in exactly the shape the builder emits:

```xml
<style-rule element='cell'>
  <format attr='border-color' value='#e9eaeb' />
  <format attr='border-style' value='solid' />
</style-rule>
<style-rule element='gridline'>
  <format attr='line-visibility' value='on' />
  <format attr='stroke-color' value='#e9eaeb' />
</style-rule>
```

One value is *not* attested unscoped: `cell` / `border-width='1'`, which
`worksheet.BORDER_WIDTH` emits beside a `solid` border. Unscoped, the corpus only ever
carries `border-width='0'` (36) and `'5'` (11); `'1'` appears only as
`scope='cols'` (`Superstore.twbx`, build 2025.1.2). The element/attribute pairing is
attested and the value is in range, so this is a value question, not a slot question -
a wrong width draws a thinner or thicker line, never nothing.
