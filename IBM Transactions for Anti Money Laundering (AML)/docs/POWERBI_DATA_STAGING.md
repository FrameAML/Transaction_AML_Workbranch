# IBM AML dataset staging

The staging pipeline reads all transaction, account, and pattern files from
`archive/`, preserves the source data, and writes reusable staged outputs to
`powerbi_staging/`.

## Source contents and staged outputs

The archive contains six dataset variants (`HI-Small`, `HI-Medium`, `HI-Large`,
`LI-Small`, `LI-Medium`, `LI-Large`), each with:

| Source file | Staged output | Contents |
|---|---|---|
| `<variant>_Trans.csv` | `<variant>_Trans.parquet` | Every source transaction column plus the documented derived columns below. |
| `<variant>_accounts.csv` | `accounts/<variant>_accounts.parquet` | Every source account/party column plus `Dataset Variant` and the `Account` join alias. |
| `<variant>_Patterns.txt` | `patterns/<variant>_Patterns.txt` | Byte-for-byte copy of the original IBM laundering-pattern description. |

`manifest.json` inventories the staged variants, row totals, source files, and
field definitions. Each CSV also has a per-file `_quality.json` report. The
original files remain untouched in `archive/`.

Run the complete staging pipeline from the project folder:

```powershell
& ".\.venv\Scripts\python.exe" ".\analysis\stage_aml_for_powerbi.py"
```

To stage a different source folder or output location:

```powershell
& ".\.venv\Scripts\python.exe" ".\analysis\stage_aml_for_powerbi.py" `
  --source-dir "C:\path\to\archive" `
  --output-dir "C:\path\to\powerbi_staging"
```

The script requires all three expected files for every variant. It fails on
missing or unrecognized files rather than silently reporting an incomplete
stage. Source columns and order are validated. Transaction and account CSVs
are processed in chunks, so the large variants do not need to fit in memory.
The manifest is written only after the full run succeeds.

## Transaction fields

The source transaction CSV contains these 11 fields:

| Source field | Meaning |
|---|---|
| `Timestamp` | Transaction date and time (`yyyy/MM/dd HH:mm`). |
| `From Bank` | Sender's bank identifier. |
| `Account` | Sender's account identifier. |
| `To Bank` | Receiver's bank identifier. |
| `Account.1` | Receiver's account identifier. The CSV repeats the header `Account`; pandas names the second occurrence `Account.1`. |
| `Amount Received` | Amount credited to the receiver, in `Receiving Currency`. |
| `Receiving Currency` | Currency of the received amount. |
| `Amount Paid` | Amount debited from the sender, in `Payment Currency`. |
| `Payment Currency` | Currency of the paid amount. |
| `Payment Format` | Transfer format, such as Wire, ACH, Cheque, or Cash. |
| `Is Laundering` | IBM simulator ground-truth label (0 or 1). |

The Parquet fact tables keep all 11 source fields and add:

| Derived field | Meaning |
|---|---|
| `Dataset Variant` | Which of the six IBM data variants the row came from. |
| `Transaction Key` | Generated text key composed from dataset variant and one-based source row number. Unique across staged variants; **not an IBM-provided transaction ID**. |
| `Sender Account` | Alias of `Account`. |
| `Receiver Account` | Alias of `Account.1`. |
| `Debit Amount` | Alias of `Amount Paid`; the sender-side debit. |
| `Credit Amount` | Alias of `Amount Received`; the receiver-side credit. |

There is no single mutually exclusive debit/credit flag: a transfer has both a
debit and a credit, and their currencies may differ. Bank and account
identifiers are stored as text to preserve leading zeroes.

## Account ownership and joins

The account CSVs contain `Bank Name`, `Bank ID`, `Account Number`, `Entity ID`,
and `Entity Name`. `Entity Name` identifies the party/account owner provided by
the IBM data. The staging pipeline retains every source field and adds
`Dataset Variant` and `Account` (an alias of `Account Number`).

The account number alone is **not** a safe join key: the source reuses some
account numbers at different banks, where they can belong to different
entities. Validation found repeated account numbers in every variant, but the
combination of bank ID and account number is unique in each account dimension.

Join the sender to the account dimension using all of:

- `Dataset Variant` = `Dataset Variant`
- `From Bank` = `Bank ID` (compare their numeric values; transaction bank IDs
  can be zero-padded, such as `010` versus `10`)
- `Account` = `Account Number`

For the receiver, use `To Bank` and `Account.1` with the same dimension fields.
Preserve the source bank IDs as text in the staged data; normalize them only
when creating the join key. For Power Query, create the sender key in the
transaction query with:

```powerquery-m
Text.Combine({
    [Dataset Variant],
    Number.ToText(Number.FromText([From Bank], "en-US"), "0", "en-US"),
    [Account]
}, "|")
```

Create the receiver key similarly, using `[To Bank]` and `[Account.1]`. In the
account query, create the matching key with:

```powerquery-m
Text.Combine({
    [Dataset Variant],
    Number.ToText(Number.FromText([Bank ID], "en-US"), "0", "en-US"),
    [Account Number]
}, "|")
```

Use separate sender and receiver joins/role-playing account queries if both
party names need to be shown on one transaction row. The owner value is the
account dimension's `Entity Name`; the staged dimension keeps it separate to
avoid repeating it on hundreds of millions of transactions.

## Pattern files and AML evaluation

The pattern files are retained unchanged as text so the complete IBM scenario
annotations are preserved, including their original attempt headings and
transaction lines. They complement `Is Laundering`, which provides the
transaction-level label in the transaction tables.

The laundering label is highly imbalanced. AML testing should emphasize
minority-class precision, recall, and F1 rather than plain accuracy. Amounts
must be interpreted with their corresponding currency field; they are not
converted to a common currency.

## Power BI and monitoring-system use

The staged outputs are the full six-variant dataset, not a single combined
Power BI import. Connect Power BI to the desired Parquet tables and retain the
`Dataset Variant` relationship when joining account owners. The Large variants
are particularly big; importing all six transaction tables into a standard
Power BI Desktop model may exceed available memory. For system-level
transaction-monitoring tests, use the Parquet outputs as source data for a
database or lakehouse and load only the needed subset into Power BI.
