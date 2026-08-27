# Table-calculation instance-name prefixes

A table calculation does not get a `<column>` of its own. It lives on the
`<column-instance>` as a `<table-calc>` child, and Tableau puts an extra prefix on the
instance `name`. `worksheet.TABLE_CALC_PREFIXES` holds that prefix per `TCType-ST` value.

The prefix is cosmetic: a wrong one makes Desktop rewrite the name on open, it does not
refuse the workbook. That is why a guess survives both validators - only a Desktop-saved
workbook settles it. The rows below are read out of real Desktop output.

## Attested

Each fragment is copied verbatim from the named workbook. `source-build` is that workbook's
`<workbook source-build='...'>`; all four are `version='18.1'`.

### `CumTotal` -> `cum`

`FINANCIAL SERVICES - Trading.twbx`, source-build 2023.3.0, win.

```xml
<column-instance column='[LinPack_215363864742895389]' derivation='User' name='[cum:usr:LinPack_215363864742895389:qk]' pivot='key' type='quantitative'>
  <table-calc aggregation='Sum' ordering-type='Rows' type='CumTotal' />
</column-instance>
```

### `PctDiff` -> `pcdf`

`20250818-appsfortableau_HierarchyFilter demo workbook-demo-workbook.twbx`, source-build
2024.3.0, mac. Note the prefix is `pcdf`, not `pctdiff` - it does not follow the
`pct` + verb shape the other percent calcs suggest.

```xml
<column-instance column='[Sales]' derivation='Sum' name='[pcdf:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc diff-options='Relative' level-address='[Sample - Superstore].[yr:Order Date:ok]' ordering-field='[Sample - Superstore].[Order Date]' ordering-type='Field' type='PctDiff'>
    <address>
      <value>-1</value>
    </address>
  </table-calc>
</column-instance>
```

### `PctTotal` -> `pcto`

`lavi_webpage_test.twbx`, source-build 2025.2.0. Also the one Desktop 2025.1 renamed on
save when the builder wrote its own guess - the original attestation.

```xml
<column-instance column='[Sales]' derivation='Sum' name='[pcto:sum:Sales:qk]' pivot='key' type='quantitative'>
```

A percent-of-total *of* a running total nests both prefixes, outermost first:
`name='[pcto:cum:usr:LinPack_215363864742895389:qk]'` - which is exactly what
`FieldRef.instance_name` builds by prepending one prefix to `self.prefix`.

### `Rank` -> `rank`

`Embedded Filters Test.twbx`, source-build 2024.2.10, win.

```xml
<column-instance column='[Sales]' derivation='Sum' name='[rank:sum:Sales:qk]' pivot='key' type='quantitative'>
  <table-calc ordering-type='Columns' rank-options='Competition,Descending' type='Rank' />
</column-instance>
```

## Still inferred

`WindowTotal` (`wnd`), `Difference` (`diff`), `PctValue` (`pctval`) and `PctRank`
(`pctrank`) do not appear in any Desktop workbook checked so far - a 195-workbook sweep of
the local corpus (`~/Downloads`, `~/Documents/My Tableau Repository/Workbooks`, this
repo) turned up only the four above. They stay in the table as guesses. Issue #50 has the
sheet-by-sheet steps for authoring the workbook that would settle them.

## How to attest a new one

The pairing is `<table-calc type='X'>` inside a `<column-instance name='[prefix:...]'>`, so
one sweep reads them all:

```python
BLOCK = re.compile(
    r"<column-instance\b[^>]*name='\[([^']*)'[^>]*>\s*"
    r"<table-calc\b((?:[^>]*?\s)?)type='([^']*)'", re.S)
# group(1).split(":")[0] is the prefix, group(3) is the TCType.
```

Match `type=` with a lookbehind or a leading-space group - a bare `\btype=` also matches
`ordering-type=`, which reports `Rows`/`Columns`/`Field` as calculation types.
