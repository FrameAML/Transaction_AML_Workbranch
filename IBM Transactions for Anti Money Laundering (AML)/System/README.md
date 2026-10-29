# Local AML test system

This is a local-only system for exploring the original IBM HI-Small dataset and
building reproducible test files. The application code stays in `System/`.
Its SQLite database, lookups, and generated test runs are written to the
separate sibling `Data/` folder. `Data/` is ignored by Git.

The original CSV files remain unchanged in `archive/`. The build command copies
their transaction, account, label, and pattern information into the local
database:

```powershell
# Run from the AML project folder
& ".\.venv\Scripts\python.exe" ".\System\aml_store.py" build
& ".\.venv\Scripts\python.exe" ".\System\aml_store.py" check
& ".\.venv\Scripts\python.exe" ".\System\aml_store.py" verify-fixture
```

By default, the database is `Data/HI-Small.sqlite3`. The build checks the
original schemas, validates row values, preserves raw bank/account strings,
normalizes bank IDs only for account joins, and records source hashes. A new
database is built separately and replaces an existing one only after the new
build passes its integrity checks. Use `build --replace` to request a rebuild.
No data is uploaded or sent to an external service.

## Find a transaction and its accounts

Transaction IDs are the one-based source CSV row numbers, written as
`HI-Small:<row number>`. For example:

```powershell
& ".\.venv\Scripts\python.exe" ".\System\aml_store.py" show --txn 1838771
```

This writes a transaction/account summary JSON file and a CSV containing the
full history of both endpoint accounts, including incoming and outgoing
transactions, under `Data/lookups/`. Account joins use normalized bank ID plus
account number; account number alone is not unique. The history export is an
analysis view and includes the IBM benchmark label for reference.

## Create a test file

```powershell
& ".\.venv\Scripts\python.exe" ".\System\aml_store.py" select `
  --laundering 500 --clean 500 --expand accounts --seed 42 --name pilot
```

Choose a new `--name` for each run. To intentionally replace all outputs from
an existing run with the same name, add `--overwrite`.

Outputs are placed under `Data/test_sets/`:

- `pilot_test_input.csv`: two account-side rows (debit and credit) per unique
  source transfer ID. It contains **no benchmark labels**.
- `pilot_accounts.csv`: the account profiles needed by the selected input.
  IBM does not provide account type, account currency, branch, or country, so
  those fields remain blank.
- `pilot_ground_truth.csv`: one IBM label per transaction, with pattern and
  selection context. Keep it separate from model/system input and join it only
  after scoring.
- `pilot_manifest.json`: the seed, selection unit, source hashes, counts, and
  output hashes.

The count options refer to unique source transfers, not account-side rows.
Whole annotated IBM scenarios are retained by default; when a complete scenario
pushes the count over the requested number, the manifest reports the actual
selection. Unannotated positive transactions are sampled individually to fill
the requested total. Use `--no-whole-patterns` when an exact row count matters
more than preserving complete scenarios.

To select one specific transaction, optionally with its complete matched IBM
pattern:

```powershell
& ".\.venv\Scripts\python.exe" ".\System\aml_store.py" select `
  --txn HI-Small:1838771 --with-pattern --expand accounts --name one_case
```

To select particular IBM scenario families:

```powershell
& ".\.venv\Scripts\python.exe" ".\System\aml_store.py" select `
  --laundering 100 --pattern-type CYCLE --pattern-type FAN-OUT --name patterns
```

`--expand accounts` adds the selected accounts' one-hop histories. It does not
recursively expand every account encountered. Any added history transactions
are included in the separate ground truth; labels never enter the test input.
An active account can have a very large history, so expansion may produce far
more transactions than the requested sample. Use `--history-cap N` only when
intentionally limiting each account's history.

## Important interpretation

HI-Small is a **synthetic benchmark**, not real customer data. Its labels are
simulator ground truth, not findings about real people or payments. The system
helps test a transaction-monitoring workflow on the complete HI-Small source;
it cannot establish performance on real financial-institution transactions.

The database treats each original row as one directed transfer. Test input
represents that transfer with a debit and a credit event sharing one unique
transaction ID. Selection counts and ground truth are transaction-level. Keep
that unit consistent when evaluating system metrics.

Pattern files do not contain source transaction IDs. The build matches their
rows to positive source transactions by timestamp, endpoint banks/accounts,
amounts, currencies, and payment format. Unmatched and ambiguous pattern lines
are reported rather than silently assigned. A pattern selection includes only
complete, successfully matched attempts.

## Tests

Run the focused tests from this folder:

```powershell
pytest -q ".\test_aml_store.py"
```

Install `pytest` in the active Python environment if it is not already
available (`python -m pip install pytest`).

The tests use a small synthetic dataset with IBM's CSV shape and edge cases;
they do not replace the real-data `check` and `verify-fixture` commands above.
