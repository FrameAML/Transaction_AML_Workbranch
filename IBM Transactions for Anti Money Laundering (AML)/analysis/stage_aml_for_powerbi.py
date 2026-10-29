from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


TRANSACTION_COLUMNS = [
    "Timestamp",
    "From Bank",
    "Account",
    "To Bank",
    "Account.1",
    "Amount Received",
    "Receiving Currency",
    "Amount Paid",
    "Payment Currency",
    "Payment Format",
    "Is Laundering",
]
ACCOUNT_COLUMNS = [
    "Bank Name",
    "Bank ID",
    "Account Number",
    "Entity ID",
    "Entity Name",
]
DERIVED_TRANSACTION_COLUMNS = [
    "Dataset Variant",
    "Transaction Key",
    "Sender Account",
    "Receiver Account",
    "Debit Amount",
    "Credit Amount",
]
TRANSACTION_OUTPUT_COLUMNS = [
    "Dataset Variant",
    "Transaction Key",
    *TRANSACTION_COLUMNS,
    "Sender Account",
    "Receiver Account",
    "Debit Amount",
    "Credit Amount",
]
ACCOUNT_OUTPUT_COLUMNS = [
    "Dataset Variant",
    *ACCOUNT_COLUMNS,
    "Account",
]
TRANSACTION_DTYPES = {
    "Timestamp": "string",
    "From Bank": "string",
    "Account": "string",
    "To Bank": "string",
    "Account.1": "string",
    "Amount Received": "float64",
    "Receiving Currency": "string",
    "Amount Paid": "float64",
    "Payment Currency": "string",
    "Payment Format": "string",
    "Is Laundering": "Int64",
}
ACCOUNT_DTYPES = {column: "string" for column in ACCOUNT_COLUMNS}
CHUNK_SIZE = 250_000
EXPECTED_VARIANTS = {
    "HI-Small",
    "HI-Medium",
    "HI-Large",
    "LI-Small",
    "LI-Medium",
    "LI-Large",
}
SOURCE_FILE_PATTERN = re.compile(
    r"^(?P<variant>(?:HI|LI)-(?:Small|Medium|Large))_"
    r"(?P<kind>Trans\.csv|accounts\.csv|Patterns\.txt)$"
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, content: dict[str, Any]) -> None:
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")


def _discover_sources(source_dir: Path) -> dict[str, dict[str, Path]]:
    if not source_dir.is_dir():
        raise NotADirectoryError(f"Source folder does not exist: {source_dir}")

    sources: dict[str, dict[str, Path]] = {}
    unexpected: list[str] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        match = SOURCE_FILE_PATTERN.fullmatch(path.name)
        if match is None:
            unexpected.append(path.name)
            continue
        variant = match.group("variant")
        kind = match.group("kind")
        if kind in sources.setdefault(variant, {}):
            raise ValueError(f"Duplicate {kind} source for {variant}")
        sources[variant][kind] = path

    if unexpected:
        raise ValueError(
            "Unrecognized files in the source folder were not staged: "
            + ", ".join(unexpected)
        )

    expected_kinds = {"Trans.csv", "accounts.csv", "Patterns.txt"}
    for variant, files in sources.items():
        missing = expected_kinds - files.keys()
        if missing:
            raise FileNotFoundError(
                f"{variant} is missing required source file(s): "
                + ", ".join(sorted(missing))
            )
    if not sources:
        raise FileNotFoundError(f"No IBM AML source files found in {source_dir}")
    missing_variants = EXPECTED_VARIANTS - sources.keys()
    if missing_variants:
        raise FileNotFoundError(
            "Expected all six IBM variants; missing: "
            + ", ".join(sorted(missing_variants))
        )
    return sources


def stage_transactions(source_path: Path, output_dir: Path, variant: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / f"{variant}_Trans.parquet"
    temp_path = parquet_path.with_suffix(".parquet.tmp")
    summary_path = output_dir / f"{variant}_Trans_quality.json"
    writer: pq.ParquetWriter | None = None
    total_rows = 0
    null_counts = pd.Series(dtype="int64")
    label_counts: dict[str, int] = {}
    min_timestamp: str | None = None
    max_timestamp: str | None = None

    try:
        for chunk in pd.read_csv(
            source_path,
            chunksize=CHUNK_SIZE,
            dtype=TRANSACTION_DTYPES,
            low_memory=False,
        ):
            if list(chunk.columns) != TRANSACTION_COLUMNS:
                raise ValueError(
                    f"Unexpected columns in {source_path.name}: "
                    f"{list(chunk.columns)!r}"
                )

            chunk["Timestamp"] = pd.to_datetime(
                chunk["Timestamp"],
                format="%Y/%m/%d %H:%M",
                errors="raise",
            )
            if not chunk["Is Laundering"].dropna().isin([0, 1]).all():
                raise ValueError(
                    f"Is Laundering contains values other than 0 and 1 "
                    f"in {source_path.name}"
                )

            first_row = total_rows + 1
            keys = [
                f"{variant}:{row_number}"
                for row_number in range(first_row, first_row + len(chunk))
            ]
            chunk.insert(0, "Transaction Key", pd.Series(keys, index=chunk.index))
            chunk.insert(0, "Dataset Variant", variant)
            chunk["Sender Account"] = chunk["Account"]
            chunk["Receiver Account"] = chunk["Account.1"]
            chunk["Debit Amount"] = chunk["Amount Paid"]
            chunk["Credit Amount"] = chunk["Amount Received"]

            if list(chunk.columns) != TRANSACTION_OUTPUT_COLUMNS:
                raise ValueError(
                    f"Unexpected staged columns for {variant}: "
                    f"{list(chunk.columns)!r}"
                )

            total_rows += len(chunk)
            null_counts = null_counts.add(chunk.isna().sum(), fill_value=0)
            counts = chunk["Is Laundering"].value_counts(dropna=False)
            for label, count in counts.items():
                key = str(label)
                label_counts[key] = label_counts.get(key, 0) + int(count)

            chunk_min = chunk["Timestamp"].min()
            chunk_max = chunk["Timestamp"].max()
            if pd.notna(chunk_min):
                value = chunk_min.isoformat()
                min_timestamp = (
                    value if min_timestamp is None else min(min_timestamp, value)
                )
            if pd.notna(chunk_max):
                value = chunk_max.isoformat()
                max_timestamp = (
                    value if max_timestamp is None else max(max_timestamp, value)
                )

            table = pa.Table.from_pandas(chunk, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp_path, table.schema, compression="zstd")
            writer.write_table(table)
    except Exception:
        if writer is not None:
            writer.close()
            writer = None
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            writer.close()

    if total_rows == 0:
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"No transaction rows found in {source_path}")
    temp_path.replace(parquet_path)

    summary = {
        "dataset_variant": variant,
        "source_file": source_path.name,
        "source_path": str(source_path),
        "source_bytes": source_path.stat().st_size,
        "staged_file": str(parquet_path),
        "row_count": total_rows,
        "source_column_count": len(TRANSACTION_COLUMNS),
        "source_columns": TRANSACTION_COLUMNS,
        "column_count": len(TRANSACTION_OUTPUT_COLUMNS),
        "columns": TRANSACTION_OUTPUT_COLUMNS,
        "derived_columns": {
            "Dataset Variant": "IBM dataset variant (HI/LI and Small/Medium/Large).",
            "Transaction Key": (
                "Generated surrogate key scoped by dataset variant and source row "
                "order; not an IBM-provided transaction identifier."
            ),
            "Sender Account": "Alias of Account; the account associated with From Bank.",
            "Receiver Account": (
                "Alias of Account.1; the account associated with To Bank."
            ),
            "Debit Amount": "Alias of Amount Paid; the sender-side debit.",
            "Credit Amount": "Alias of Amount Received; the receiver-side credit.",
        },
        "identifier_columns_preserved_as_text": ["From Bank", "To Bank", "Account", "Account.1"],
        "null_counts": {key: int(value) for key, value in null_counts.items()},
        "is_laundering_counts": label_counts,
        "timestamp_min": min_timestamp,
        "timestamp_max": max_timestamp,
        "chunk_size": CHUNK_SIZE,
        "format": "Parquet",
        "compression": "zstd",
    }
    _write_json(summary_path, summary)
    return summary


def stage_accounts(source_path: Path, output_dir: Path, variant: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / f"{variant}_accounts.parquet"
    temp_path = parquet_path.with_suffix(".parquet.tmp")
    summary_path = output_dir / f"{variant}_accounts_quality.json"
    writer: pq.ParquetWriter | None = None
    total_rows = 0
    null_counts = pd.Series(dtype="int64")
    seen_composite_keys: set[str] = set()

    try:
        for chunk in pd.read_csv(
            source_path,
            chunksize=CHUNK_SIZE,
            dtype=ACCOUNT_DTYPES,
            low_memory=False,
        ):
            if list(chunk.columns) != ACCOUNT_COLUMNS:
                raise ValueError(
                    f"Unexpected columns in {source_path.name}: "
                    f"{list(chunk.columns)!r}"
                )

            chunk.insert(0, "Dataset Variant", variant)
            chunk["Account"] = chunk["Account Number"]
            if list(chunk.columns) != ACCOUNT_OUTPUT_COLUMNS:
                raise ValueError(
                    f"Unexpected staged account columns for {variant}: "
                    f"{list(chunk.columns)!r}"
                )
            if not chunk["Bank ID"].str.fullmatch(r"\d+").fillna(False).all():
                raise ValueError(f"Bank ID must be numeric in {source_path.name}")
            if chunk["Account Number"].isna().any():
                raise ValueError(
                    f"Account Number must be present in {source_path.name}"
                )
            normalized_bank_ids = chunk["Bank ID"].str.lstrip("0")
            normalized_bank_ids = normalized_bank_ids.mask(
                normalized_bank_ids.eq(""), "0"
            )
            composite_keys = normalized_bank_ids + ":" + chunk["Account Number"]
            if composite_keys.duplicated().any() or any(
                key in seen_composite_keys for key in composite_keys
            ):
                raise ValueError(
                    f"Bank ID and Account Number do not uniquely identify "
                    f"accounts in {source_path.name}"
                )
            seen_composite_keys.update(composite_keys)

            total_rows += len(chunk)
            null_counts = null_counts.add(chunk.isna().sum(), fill_value=0)
            table = pa.Table.from_pandas(chunk, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp_path, table.schema, compression="zstd")
            writer.write_table(table)
    except Exception:
        if writer is not None:
            writer.close()
            writer = None
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            writer.close()

    if total_rows == 0:
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"No account rows found in {source_path}")
    temp_path.replace(parquet_path)

    summary = {
        "dataset_variant": variant,
        "source_file": source_path.name,
        "source_path": str(source_path),
        "source_bytes": source_path.stat().st_size,
        "staged_file": str(parquet_path),
        "row_count": total_rows,
        "source_columns": ACCOUNT_COLUMNS,
        "staged_columns": ACCOUNT_OUTPUT_COLUMNS,
        "identifier_columns_preserved_as_text": [
            "Bank ID",
            "Account Number",
            "Entity ID",
        ],
        "join_guidance": (
            "Account Number is not unique by itself. Join using Dataset Variant, "
            "Bank ID (compare its numeric value to From Bank or To Bank), and "
            "Account Number (compare to Sender Account or Receiver Account)."
        ),
        "composite_join_key_fields": [
            "Dataset Variant",
            "Bank ID with leading zeroes ignored",
            "Account Number",
        ],
        "composite_join_key_unique": True,
        "owner_column": "Entity Name",
        "null_counts": {key: int(value) for key, value in null_counts.items()},
        "format": "Parquet",
        "compression": "zstd",
    }
    _write_json(summary_path, summary)
    return summary


def stage_patterns(source_path: Path, output_dir: Path, variant: str) -> dict[str, Any]:
    pattern_dir = output_dir / "patterns"
    pattern_dir.mkdir(parents=True, exist_ok=True)
    staged_path = pattern_dir / source_path.name
    shutil.copyfile(source_path, staged_path)

    summary = {
        "dataset_variant": variant,
        "source_file": source_path.name,
        "source_path": str(source_path),
        "source_bytes": source_path.stat().st_size,
        "staged_file": str(staged_path),
        "staged_bytes": staged_path.stat().st_size,
        "format": "Original text, copied without transformation",
    }
    return summary


def stage_archive(source_dir: Path, output_dir: Path) -> dict[str, Any]:
    sources = _discover_sources(source_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    variant_summaries: dict[str, dict[str, Any]] = {}
    manifest_path = output_dir / "manifest.json"
    manifest = {
        "status": "in_progress",
        "source_directory": str(source_dir),
        "staging_directory": str(output_dir),
        "dataset_variants": sorted(sources),
        "source_file_count": sum(len(files) for files in sources.values()),
        "staged_source_file_count": 0,
        "variant_count": len(sources),
        "total_transaction_rows": 0,
        "total_account_rows": 0,
        "variants": variant_summaries,
    }
    _write_json(manifest_path, manifest)

    stage_order = [
        "HI-Small",
        "LI-Small",
        "HI-Medium",
        "LI-Medium",
        "HI-Large",
        "LI-Large",
    ]
    for variant in stage_order:
        files = sources[variant]
        print(f"Staging {variant} transaction data...", flush=True)
        transactions = stage_transactions(files["Trans.csv"], output_dir, variant)
        print(f"Staging {variant} account data...", flush=True)
        accounts = stage_accounts(files["accounts.csv"], output_dir, variant)
        patterns = stage_patterns(files["Patterns.txt"], output_dir, variant)
        variant_summaries[variant] = {
            "transactions": transactions,
            "accounts": accounts,
            "patterns": patterns,
        }
        manifest["staged_source_file_count"] += 3
        manifest["total_transaction_rows"] += transactions["row_count"]
        manifest["total_account_rows"] += accounts["row_count"]
        _write_json(manifest_path, manifest)

    manifest["status"] = "complete"
    manifest["notes"] = [
        "All transaction and account CSV columns are retained in Parquet.",
        "Bank and account identifiers are preserved as text, including leading zeros.",
        "Pattern files are staged as byte-for-byte copies of the IBM source text.",
        "The generated Transaction Key is not an IBM-issued transaction ID.",
        (
            "Account owners are in the account dimension Entity Name. Account "
            "numbers can repeat across banks; join on Dataset Variant, numeric "
            "Bank ID, and Account Number."
        ),
    ]
    _write_json(manifest_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage all IBM AML transaction, account, and pattern files."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=PROJECT_ROOT / "archive",
        help="Folder containing the IBM source files (default: ./archive).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "powerbi_staging",
        help="Output folder (default: ./powerbi_staging).",
    )
    args = parser.parse_args()
    manifest = stage_archive(args.source_dir, args.output_dir)
    print(
        json.dumps(
            {
                "source_file_count": manifest["source_file_count"],
                "variant_count": manifest["variant_count"],
                "total_transaction_rows": manifest["total_transaction_rows"],
                "total_account_rows": manifest["total_account_rows"],
                "manifest": str(args.output_dir / "manifest.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
