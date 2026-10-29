# IBM AML Transaction Analysis Project

This project is set up to prepare the IBM synthetic AML dataset for transaction monitoring and investigation work. The goal is to make the data understandable in AML terms, traceable by transaction, and ready for analysis in Power BI or Python.

## What this project contains

| Folder/file | Purpose | Handling |
|---|---|---|
| `archive/` | Original IBM CSV and pattern source files for all six variants. | Preserve as source data; do not edit in place. |
| `powerbi_staging/` | Full Parquet populations, data-quality reports, manifest, and staged pattern copies. | Rebuildable outputs; use for complete analysis. |
| `analysis/` | Reproducible staging, analysis, and report-generation scripts. | Keep code separate from the large data it processes. |
| `docs/` | Human-facing guides, procedures, workbooks, summary cards, and references. | Project documentation and analysis deliverables. |
| `Reports/PowerBI/` | Power BI reports, dated backup, and the distinct review copy. | Keep report versions together; preserve the original report unchanged. |
| `Ryno Analysis(IBM)/` | Ryno's source files, notes, UDM scripts, and conversion manifests. | Keep source CSVs and original Ryno materials local; manifests and our descriptive comparison are versioned. |
| `test_data/` | Header-only Frame mapping templates and explicitly synthetic pipeline fixture. | Fixture labels remain separate from scoring input. |
| `System/` | Local HI-Small transaction lookup and test-file builder. | Application code, tests, and system instructions. |
| `Data/` | Local system database, transaction lookups, and generated test sets. | Machine-local output; excluded from Git. |

The local `.venv/`, `.kilo/`, and Python cache files are machine/editor state,
not project deliverables. `.gitignore` excludes these, the full source/staged
populations, generated Ryno messages, and PBIX binaries from ordinary Git
tracking. Scripts, notes, manifests, documentation, and small synthetic test
fixtures stay in the repository so the work can be continued from this folder.
Keep the large source files in this project folder locally/OneDrive or another
approved data store; do not add them to normal Git history.

The complete HI-Small transaction CSV (all **5,078,345 rows**, not a sample)
is kept locally under `archive/HI-Small_Trans.csv`; Ryno's local copy has the
same SHA-256 and is the workbook's raw-tab source. The corresponding account
dimension is kept in `archive/HI-Small_accounts.csv`. These files are
intentionally ignored by Git. The PBIX is a report, not the CSV export. The
full local descriptive workbook includes all HI-Small transaction rows across
five Excel tabs because Excel's worksheet limit is 1,048,576 rows.

The Power BI reports are grouped in `Reports/PowerBI/`. The main updated PBIX
is kept unchanged; the transaction walkthrough, dated backup, and distinct
review copy are stored alongside it so report versions are easy to find.
Supporting Word and Markdown guides are in `docs/`.

The local test system is documented in [`System/README.md`](System/README.md).
Its code stays in `System/`; building it creates `Data/HI-Small.sqlite3` with
the full HI-Small transactions and accounts. Transaction lookups and test-set
exports also stay under `Data/`. The source CSVs in `archive/` are not moved or
modified, and the local database and generated exports are excluded from Git.
Use `System/aml_store.py check` and `verify-fixture` to validate the local
store before using it.

GitHub blocks regular Git files larger than 100 MiB and recommends keeping
repositories below 5 GB. The six-variant raw CSVs and Parquet population are
far larger, so they remain local/OneDrive data assets rather than Git objects.
See GitHub's [large-file guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).

The GitHub-friendly workbook is
[`docs/FrameAML_IBM_Data_Preparation_and_Descriptive_Analysis_Summary.xlsx`](docs/FrameAML_IBM_Data_Preparation_and_Descriptive_Analysis_Summary.xlsx).
The full workbook with all five raw HI-Small tabs is generated locally at
`docs/FrameAML_IBM_Data_Preparation_and_Descriptive_Analysis.xlsx`; it is
ignored by Git because embedding more than five million rows can exceed
GitHub's regular file limit.

## Set up the local Python environment

The analysis scripts were validated with Python 3.14.7. Recreate the local
environment from the project folder:

```powershell
py -3.14 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -r ".\requirements.txt"
```

`.venv/` stays local and is ignored by Git. Keep the source CSVs and staged
Parquet tables in their documented folders; the scripts fail explicitly when
required source data is missing.

## Ryno UDM analysis

The `Ryno Analysis(IBM)/` folder contains two conversion scripts, Ryno's notes,
the required-attributes workbook and the UDM message-field specification.
`top20/manifest.json` and `sanitised_bank12/manifest.json` preserve conversion
parameters, input/output hashes and counts; generated JSONL messages and label
files are excluded from Git and can be rebuilt from the local source CSVs and
local Ryno scripts. The original shared scripts, notes, and specification are
not redistributed in this repository; see
[`docs/RYNO_UDM_ANALYSIS.md`](docs/RYNO_UDM_ANALYSIS.md) for the recorded
comparison and assumptions.

The workbook's **Ryno Comparison** sheet separates:

- the complete HI-Small source population (5,078,345 payments);
- the top-20-bank conversion (1,034,599 payments represented by 1,216,251
  debit/credit legs, with tail exclusion); and
- the Bank-12 sanitised conversion (113,744 payments represented by 121,626
  legs, with synthetic ZAR/FX/branch assumptions).

These are different populations and units, not like-for-like benchmark
datasets. The single-bank view has less counterparty and pattern evidence;
the synthetic account-currency, FX and branch fields must be treated as
assumptions, not observed facts. Review Ryno's notes and the conversion
manifests before building any evaluator or system logic from these outputs.
The **Ryno Field Mapping** sheet documents all 12 requested attributes in
order, with the original field source, transformation, and caveat for each.

When the full source CSVs are present beside the scripts in
`Ryno Analysis(IBM)/`, Ryno's conversions can be rebuilt with:

```powershell
Set-Location ".\Ryno Analysis(IBM)"
& "..\.venv\Scripts\python.exe" -I ".\hismall_to_udm_messages.py" --data "." --out ".\top20" --top 20 --exclude-tail --gzip
& "..\.venv\Scripts\python.exe" -I ".\hismall_to_udm_sanitised.py" --data "." --out ".\sanitised_bank12" --banks 12
```

The sanitised Bank-12 conversion assumes ZAR account currency, approximate
FX, fabricated branch IDs, and local timestamps without a source timezone.
These generated fields must be confirmed or replaced for any real-system
evaluation.

## Important interpretation

The transaction rows use synthetic bank and account identifiers rather than display names. The account dimension includes source-provided `Bank Name` and entity details where available; use the account dimension to look up names instead of guessing from IDs. IBM `Bank ID` values are benchmark-local identifiers, not real worldwide routing codes. Do not map them to BIC/SWIFT, LEI, or domestic bank codes.

Neither IBM source schema contains account product/type, operational account
role, general-ledger status, account balance, branch, country code, or real
bank-identification schemes. A clearing/settlement/correspondent classification
requires trusted account-master, GL, or payment-system evidence. Transaction
volume, many counterparties, or pass-through behaviour can motivate review but
cannot prove account type. See
[`docs/ACCOUNT_AND_BANK_IDENTIFIER_GUIDE.md`](docs/ACCOUNT_AND_BANK_IDENTIFIER_GUIDE.md)
and the workbook's **Account Type Guide** and **Bank Identifier Guide** sheets.

The transaction data is designed to support AML analysis with:

- sender and receiver information
- debit and credit amounts
- timestamps
- payment format
- currencies
- a transaction-level laundering label (`Is Laundering`)

The AML question is not only whether a single transaction is suspicious, but also whether the pattern looks like layering, smurfing, circular movement, rapid value movement, or account-to-account activity that is inconsistent with expected customer behavior.

## AML-ready data model

Each transaction should be interpreted as:

- A money movement event between two parties
- From one bank/account to another bank/account
- At a specific timestamp
- With a debit amount from the sender and a credit amount to the receiver
- In possibly different currencies

This means we can build AML logic around:

- account-to-account behavior
- customer/entity risk
- transaction frequency
- amount thresholds
- round-tripping or circular movement
- rapid movement across multiple accounts/banks
- unusual payment formats
- outlier activity compared with historical account behavior

## Data lineage and traceability

Every transaction is traceable through:

- `Dataset Variant`
- `Transaction Key`
- `Timestamp`
- `From Bank`
- `Account`
- `To Bank`
- `Account.1`
- `Amount Paid` / `Debit Amount`
- `Amount Received` / `Credit Amount`
- `Payment Currency` / `Receiving Currency`

This makes it possible to answer questions such as:

- Who sent the money?
- Who received the money?
- Which account was involved?
- What amount moved?
- What was the timing pattern?
- Was the transaction labeled as laundering by the IBM simulator?

## Project folders

```text
archive/              Original IBM source CSV/pattern files (immutable)
powerbi_staging/      Full Parquet tables, manifest, quality, pattern copies
analysis/             Staging, profile, and document/workbook builder scripts
docs/                 Guides, staging instructions, workbooks, and Word deliverables
Reports/PowerBI/      Main reports, dated backup, and distinct review copy
Ryno Analysis(IBM)/   Ryno source notes, conversion scripts, and manifests
test_data/            Frame templates and isolated synthetic fixture
README.md             Project entry point and run guidance
requirements.txt      Tested, pinned Python package versions
```

## Power BI transaction walkthrough

Open `Reports/PowerBI/IBM_AML_Analysis-updated-transaction-walkthrough.pbix` in Power BI Desktop and select the **Transaction Walkthrough** page. The page places a readable four-step interpretation guide above a transaction-level table. Use the report zoom controls to enlarge the page when needed.

The table includes the dataset variant, staged transaction key, raw timestamp, sender and receiver bank/account identifiers, debit and credit amounts, their separate currencies, payment format, and the IBM simulator label. Amounts and the label are not summed, and the timestamp is displayed as a raw timestamp rather than a year/quarter hierarchy. Use the table's column filters to isolate a transaction and clear filters before beginning a new review.

For a concise Frame-focused summary of the IBM synthetic data, proposed AML pattern framework, evaluation cautions, local export coverage, and source references, see `docs/FRAME_IBM_AML_Data_Use_Brief.docx`. The complete six-variant dataset is staged in `powerbi_staging/`; the PBIX is a report/model and is not the full data export.

For the GitHub-friendly descriptive analysis and FrameAML preparation steps,
see
[`docs/FrameAML_IBM_Data_Preparation_and_Descriptive_Analysis_Summary.xlsx`](docs/FrameAML_IBM_Data_Preparation_and_Descriptive_Analysis_Summary.xlsx).
It documents the transaction/account schemas, missing account-type and
account-currency fields, temporal-label caveats, population summaries, Ryno
comparison, test protocol, and transaction-to-account-event CSV mapping.
The full local workbook at
`docs/FrameAML_IBM_Data_Preparation_and_Descriptive_Analysis.xlsx` additionally
contains all 5,078,345 HI-Small source transactions across five raw-data tabs.
Full-population profiling is reproducible with
`analysis/build_frame_aml_descriptive_workbook.py`; the machine-readable
profile is `analysis/frame_aml_full_population_profile.json`.

The [account and bank identifier guide](docs/ACCOUNT_AND_BANK_IDENTIFIER_GUIDE.md) defines common account products/roles, including clearing, settlement, correspondent, suspense, escrow, and pooled accounts. It also documents the correct IBM account-dimension join and distinguishes synthetic IBM IDs from real-world BIC/LEI and national routing schemes.

The `test_data/` folder includes header-only Frame transaction and account templates, plus a balanced HI-Small synthetic development fixture and a separate ground-truth file. The fixture is for schema and pipeline checks only; it is not representative of population prevalence, and the ground-truth file must not enter model features or scoring inputs.

Important temporal caveat: Kaggle describes post-primary-period rows as laundering, but the full local scan finds 56,691 rows after the stated cutoffs and only 33,583 label-1 rows. Reconcile the cutoff, data version, and linked Kaggle discussion before using those dates for a temporal split; do not treat date alone as ground truth. The workbook and Frame brief document this mismatch.

The related [IBM Credit Card Transactions dataset](https://www.kaggle.com/datasets/ealtman2019/credit-card-transactions) is a separate synthetic **fraud** benchmark, not another AML variant. Its purchase/card/user data can support a separate fraud or purchase-behavior test suite, but its labels cannot validate laundering detection, account-to-account typologies, sanctions screening, or the AML measures in this workbook. The Kaggle page describes more than 20 million transactions and Apache License 2.0 terms in its description; verify the license before redistribution. It is not included in the IBM AML staged population.

The [Kaggle AML baseline notebook](https://www.kaggle.com/code/gbiamgaurav/baseline-ibm-transactions-for-aml) is recorded as a community reference. Its public page metadata is available, but the notebook cells and execution outputs were not retrievable through the public page/API during this review. No results or methods are attributed to it until the actual code can be inspected.

To rebuild the descriptive workbook and full HI-Small raw tabs without
rescanning Parquet:

```powershell
& ".\.venv\Scripts\python.exe" ".\analysis\build_frame_aml_descriptive_workbook.py" --reuse-profile
```

This full build expects the local source CSV and Ryno manifests. To rebuild the
smaller GitHub-friendly summary workbook, add `--without-hi-small-raw`. The
generated workbooks document every sheet and the scope/assumptions for the
Ryno conversions. Full raw source data and the workbook embedding it stay
local/OneDrive and are not uploaded to GitHub.

`Reports/PowerBI/IBM_AML_Analysis-updated.pbix` is the protected original; the walkthrough is a separate working copy. Bank values are identifiers, not verified bank names. `Is Laundering` is a synthetic benchmark label, not proof of real-world wrongdoing. Use `docs/IBM_AML_PowerBI_Transaction_Walkthrough.docx` for the fuller procedure and worked example, `docs/IBM_AML_Transaction_Analysis_Procedure.docx` for the broader analysis workflow, and `docs/IBM_AML_Interpretation_and_Review_Guide.xlsx` as the field glossary and review template.

The staging guide is in `docs/POWERBI_DATA_STAGING.md`. From the project root,
run the pipeline with
`& ".\.venv\Scripts\python.exe" ".\analysis\stage_aml_for_powerbi.py"`.

If a full-population profile JSON already exists and only the workbook/templates need rebuilding, run `python analysis/build_frame_aml_descriptive_workbook.py --reuse-profile`; this uses that profile without rescanning the Parquet population. The default command scans the selected staged variants and defaults to all six.
