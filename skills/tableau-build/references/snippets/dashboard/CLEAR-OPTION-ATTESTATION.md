# `<clear-option>` value serialization, per parameter data type

What a Desktop-saved workbook writes in an `<edit-parameter-action>`'s
`<clear-option value>` when the action's **"Clearing the selection will: Set value to"**
is set. Backs `features.CLEAR_VALUE_TAGS` / `features.serialize_clear_value` and
`manifest.PARAMETER_ACTION_TARGET_TYPES` (issue #49).

| parameter `datatype` | tag | example `value` | attested from |
| --- | --- | --- | --- |
| `string` | `s:LROOT:` | `s:LROOT:All` | `dashboard_dzv_clear_unselected.twbx`, `parameter-action.twb` (this dir) |
| `integer` | `i:` | `i:1` | `Logistics Dashboard_v2025.1.twbx`, `Clear All Button_v2023.3.twbx` (`i:0`), `Art of HR - Compensation Overview Makeover_v2024.2.twbx` (`i:1`/`i:2`/`i:3`) |
| `boolean` | `b:` | `b:false` | `SalesMRR.twbx`, `Churns Analysis.twbx`, `US Penetration Overview.twbx`, `Rapaport Subscriptions Waterfall.twbx` |
| `real` | **unattested** | — | — |
| `date` / `datetime` | **unattested** | — | — |

Three things the samples settle:

1. The leading letter **is** a data-type tag, and only a string carries the extra `LROOT:`
   segment. `i:` and `b:` have no second segment.
2. The value after the tag is **undelimited** — `All`, not `"All"`; `false`, not `"false"`.
   It is the bare literal, matching `features.parameter_literal` for a number and a boolean
   and the *unquoted* text for a string.
3. Every boolean sample resets to `b:false`; `b:true` is inferred, not read. The
   inference is safe because the parameter's own `value=` and `<member value=>` use the
   same bare lowercase `true`/`false` literal that `b:false` does.
4. A `do-nothing` clear-option's `value` is not read. Desktop usually leaves `s:LROOT:`
   there whatever the target's type (`features.CLEAR_VALUE_UNUSED`), but not always —
   `Logistics Dashboard_v2025.1.twbx` carries `value='r:::1'` on a `do-nothing` targeting an
   **integer** parameter, so that slot holds stale junk and is no evidence of a `real` tag.

The source workbooks are not vendored here: the boolean samples are customer dashboards
(one has a live Salesforce account URL as a parameter's current value). The fragments below
are the whole of what was read.

## `string` — `dashboard_dzv_clear_unselected.twbx` (document version 18.1)

```xml
<column caption='Selected Region' datatype='string' name='[Selected Region]'
        param-domain-type='list' role='measure' type='nominal' value='&quot;All&quot;'>
  <calculation class='tableau' formula='&quot;All&quot;' />
  <members>
    <member value='&quot;All&quot;' />
    <member value='&quot;East&quot;' />
    <member value='&quot;North&quot;' />
    <member value='&quot;West&quot;' />
  </members>
</column>
...
<edit-parameter-action caption='Pick region' name='[Action4_94ABA201C771E732BB68894FD33BD1E9]'>
  <activation type='on-select' />
  <source dashboard='Dashboard 1' type='sheet' worksheet='Detail Table' />
  <agg-type type='attr' />
  <clear-option type='assign-fixed-value' value='s:LROOT:All' />
  <params>
    <param name='source-field' value='[federated.b2993893834e8f0306a282d0a717d7a9].[none:region:nk]' />
    <param name='target-parameter' value='[Parameters].[Selected Region]' />
  </params>
</edit-parameter-action>
```

## `integer` — `Logistics Dashboard_v2025.1.twbx` (document version 18.1)

```xml
<column caption='Driver ID Parameter' datatype='integer' datatype-customized='true'
        name='[Parameter 4]' param-domain-type='list' role='measure'
        type='quantitative' value='3'>
  <calculation class='tableau' formula='3' />
  <members>
    <member value='1' />
    <member value='2' />
    <!-- ... through 11 -->
  </members>
</column>
...
<edit-parameter-action caption='Drivers Name' name='[Action7_CB637FA5404F43848AB1A401D31911D0]'>
  <activation type='on-select' />
  <source dashboard='Drivers' type='sheet'>
    <exclude-sheet name='Rank ' />
  </source>
  <agg-type type='attr' />
  <clear-option type='assign-fixed-value' value='i:1' />
  <params>
    <param name='source-field' value='[federated.1mwylet0mo9yzg15b8cu21pchupa].[none:DriverID (Drivers.csv):ok]' />
    <param name='target-parameter' value='[Parameters].[Parameter 4]' />
  </params>
</edit-parameter-action>
```

Note the reset value (`1`) is what was typed into "Set value to", not the parameter's
current value (`3`) — the two are independent. This builder resets to the parameter's
opening value, which is the case that keeps a Dynamic-Zone-Visibility panel closable.

## `boolean` — `SalesMRR.twbx` (document version 18.1)

```xml
<column caption='DZV: Sales MRR' datatype='boolean' name='[Parameter 1]'
        param-domain-type='list' role='measure' type='nominal' value='true'>
  <calculation class='tableau' formula='true' />
  <members>
    <member value='true' />
    <member value='false' />
  </members>
</column>
...
<edit-parameter-action caption='DZV action' name='[Action1_55A3760AE05A4DB7B2F5D5576E0928D2]'>
  <activation type='on-select' />
  <source dashboard='Sales Dashboard' type='sheet' worksheet='Sales MRR by Month' />
  <agg-type type='attr' />
  <clear-option type='assign-fixed-value' value='b:false' />
  <params>
    <param name='source-field' value='[sqlproxy...].[none:Calculation_1446781410472054788:nk]' />
    <param name='target-parameter' value='[Parameters].[Parameter 1]' />
  </params>
</edit-parameter-action>
```

## Reproducing / extending this

To attest `real`, `date` or `datetime`, save a Desktop workbook with a parameter of that
type and a Change Parameter action whose **"Clearing the selection will:"** is **Set value
to**, then read the `<clear-option>` out of the `.twb`:

```bash
python -c "import re,sys; print(re.findall(r'<clear-option[^>]*>', open(sys.argv[1],encoding='utf-8').read()))" workbook.twb
```

If Desktop greys out "Set value to" for a type, that type genuinely has no reset and
belongs out of `PARAMETER_ACTION_TARGET_TYPES` permanently.
