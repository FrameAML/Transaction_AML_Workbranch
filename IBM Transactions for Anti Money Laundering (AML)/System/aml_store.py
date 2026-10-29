#!/usr/bin/env python3
"""Local HI-Small transaction store and label-safe test-file builder."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sqlite3
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_DIR / "archive"
DATA_DIR = PROJECT_DIR / "Data"
DEFAULT_DB = DATA_DIR / "HI-Small.sqlite3"
DEFAULT_EXPORT_DIR = DATA_DIR / "test_sets"
SCHEMA_VERSION = "1"
BATCH_SIZE = 25_000
KNOWN_HI_SMALL_COUNTS = {
    "transaction_rows": 5_078_345,
    "positive_transactions": 5_177,
    "account_rows": 518_581,
}
TRANSACTION_HEADER = [
    "Timestamp",
    "From Bank",
    "Account",
    "To Bank",
    "Account",
    "Amount Received",
    "Receiving Currency",
    "Amount Paid",
    "Payment Currency",
    "Payment Format",
    "Is Laundering",
]
ACCOUNT_HEADER = [
    "Bank Name",
    "Bank ID",
    "Account Number",
    "Entity ID",
    "Entity Name",
]
INPUT_HEADER = [
    "Dataset Variant (benchmark only)",
    "Account Key",
    "Transaction Amount in Account Currency",
    "Transaction Amount in Original Currency",
    "Original Currency Code",
    "Transaction Branch",
    "Transaction DateTime",
    "Opposite Account Number",
    "Opposite Bank",
    "Opposite Country Code",
    "Transaction Type",
    "Transaction Direction",
    "Unique Identifier for the transaction",
]
ACCOUNT_EXPORT_HEADER = [
    "Dataset Variant (benchmark only)",
    "Account Key",
    "Bank ID",
    "Account Number",
    "Bank Name",
    "Entity ID",
    "Entity Name",
    "Account Type",
    "Account Currency",
    "Branch",
    "Country Code",
    "Effective From",
    "Effective To",
]
GROUND_TRUTH_HEADER = [
    "Dataset Variant",
    "Unique Identifier for the transaction",
    "IBM Benchmark Label (evaluation only)",
    "Pattern Families (evaluation only)",
    "Pattern Attempt IDs (evaluation only)",
    "Selection Reason (evaluation only)",
]
ATTEMPT_START = re.compile(r"^BEGIN LAUNDERING ATTEMPT\s*-\s*([^:]+?)(?::\s*(.*))?$")
ATTEMPT_END = re.compile(r"^END LAUNDERING ATTEMPT")


class StoreError(Exception):
    """Raised when source data, the store, or a requested operation is invalid."""


@dataclass(frozen=True)
class PatternAnnotation:
    attempt_id: int
    family: str
    description: str
    line_number: int
    signature: tuple[str, ...]


def _normalise_bank_id(value: str) -> int:
    try:
        return int(value.strip())
    except (AttributeError, ValueError) as exc:
        raise StoreError(f"Bank ID is not a valid integer: {value!r}") from exc


def _transaction_signature(row: Sequence[str]) -> tuple[str, ...]:
    if len(row) != 11:
        raise StoreError(f"Expected 11 transaction fields, received {len(row)}.")
    return (
        row[0].strip(),
        str(_normalise_bank_id(row[1])),
        row[2].strip(),
        str(_normalise_bank_id(row[3])),
        row[4].strip(),
        row[5].strip(),
        row[6].strip(),
        row[7].strip(),
        row[8].strip(),
        row[9].strip(),
    )


def _validate_amount(value: str, field: str, row_number: int) -> None:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise StoreError(
            f"Invalid {field} amount on source row {row_number}: {value!r}"
        ) from exc
    if not amount.is_finite() or amount < 0:
        raise StoreError(
            f"Invalid {field} amount on source row {row_number}: {value!r}"
        )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_patterns(path: Path) -> tuple[list[tuple[int, str, str, int]], list[PatternAnnotation]]:
    attempts: list[tuple[int, str, str, int]] = []
    annotations: list[PatternAnnotation] = []
    if not path.is_file():
        raise StoreError(f"Required pattern file not found: {path}")

    active_attempt: tuple[int, str, str] | None = None
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        for line_number, raw_line in enumerate(source, 1):
            line = raw_line.strip()
            if not line:
                continue
            start = ATTEMPT_START.match(line)
            if start:
                if active_attempt is not None:
                    raise StoreError(
                        f"Nested pattern attempt at {path.name}:{line_number}."
                    )
                attempt_id = len(attempts) + 1
                family = start.group(1).strip().upper()
                description = (start.group(2) or "").strip()
                active_attempt = (attempt_id, family, description)
                attempts.append((attempt_id, family, description, 0))
                continue
            if ATTEMPT_END.match(line):
                if active_attempt is None:
                    raise StoreError(
                        f"Pattern end without a matching start at "
                        f"{path.name}:{line_number}."
                    )
                active_attempt = None
                continue
            if active_attempt is None:
                raise StoreError(
                    f"Unexpected pattern content at {path.name}:{line_number}."
                )
            try:
                row = next(csv.reader([line]))
            except csv.Error as exc:
                raise StoreError(
                    f"Invalid CSV pattern line at {path.name}:{line_number}."
                ) from exc
            if len(row) != 11 or row[10].strip() != "1":
                raise StoreError(
                    f"Expected an 11-field positive transaction at "
                    f"{path.name}:{line_number}."
                )
            try:
                signature = _transaction_signature(row)
            except StoreError as exc:
                raise StoreError(
                    f"Invalid pattern line at {path.name}:{line_number}: {exc}"
                ) from exc
            attempt_id, family, description = active_attempt
            annotations.append(
                PatternAnnotation(
                    attempt_id=attempt_id,
                    family=family,
                    description=description,
                    line_number=line_number,
                    signature=signature,
                )
            )
            old = attempts[attempt_id - 1]
            attempts[attempt_id - 1] = (old[0], old[1], old[2], old[3] + 1)

    if active_attempt is not None:
        raise StoreError(f"Pattern attempt {active_attempt[0]} has no END marker.")
    if not attempts or not annotations:
        raise StoreError(f"Pattern file contains no laundering attempts: {path}")
    return attempts, annotations


def _count_missing_account_endpoints(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT t.from_bank_n AS bank_n, t.from_acct AS acct
                   FROM txn AS t
                   LEFT JOIN account AS a
                     ON a.bank_n=t.from_bank_n AND a.acct=t.from_acct
                   WHERE a.acct IS NULL
                   UNION ALL
                   SELECT t.to_bank_n AS bank_n, t.to_acct AS acct
                   FROM txn AS t
                   LEFT JOIN account AS a
                     ON a.bank_n=t.to_bank_n AND a.acct=t.to_acct
                   WHERE a.acct IS NULL
               )"""
        ).fetchone()[0]
    )


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE txn (
            txn_id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            from_bank TEXT NOT NULL,
            from_bank_n INTEGER NOT NULL,
            from_acct TEXT NOT NULL,
            to_bank TEXT NOT NULL,
            to_bank_n INTEGER NOT NULL,
            to_acct TEXT NOT NULL,
            amt_recv TEXT NOT NULL,
            cur_recv TEXT NOT NULL,
            amt_paid TEXT NOT NULL,
            cur_paid TEXT NOT NULL,
            payment_format TEXT NOT NULL
        );

        CREATE TABLE laundering (
            txn_id INTEGER PRIMARY KEY REFERENCES txn(txn_id),
            label INTEGER NOT NULL DEFAULT 1 CHECK (label = 1)
        );

        CREATE TABLE account (
            bank_n INTEGER NOT NULL,
            bank_id_raw TEXT NOT NULL,
            acct TEXT NOT NULL,
            bank_name TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            PRIMARY KEY (bank_n, acct)
        ) WITHOUT ROWID;

        CREATE TABLE pattern_attempt (
            attempt_id INTEGER PRIMARY KEY,
            family TEXT NOT NULL,
            description TEXT NOT NULL,
            annotation_count INTEGER NOT NULL,
            matched_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'incomplete'
        );

        CREATE TABLE pattern_annotation (
            attempt_id INTEGER NOT NULL REFERENCES pattern_attempt(attempt_id),
            line_number INTEGER NOT NULL,
            signature_hash TEXT NOT NULL,
            txn_id INTEGER REFERENCES txn(txn_id),
            status TEXT NOT NULL CHECK (status IN ('matched', 'ambiguous', 'unmatched')),
            PRIMARY KEY (attempt_id, line_number)
        ) WITHOUT ROWID;

        CREATE TABLE pattern_link (
            txn_id INTEGER NOT NULL REFERENCES txn(txn_id),
            attempt_id INTEGER NOT NULL REFERENCES pattern_attempt(attempt_id),
            line_number INTEGER NOT NULL,
            family TEXT NOT NULL,
            PRIMARY KEY (attempt_id, line_number)
        ) WITHOUT ROWID;

        CREATE INDEX idx_txn_from_account
            ON txn(from_bank_n, from_acct, timestamp, txn_id);
        CREATE INDEX idx_txn_to_account
            ON txn(to_bank_n, to_acct, timestamp, txn_id);
        CREATE INDEX idx_pattern_link_txn ON pattern_link(txn_id);
        CREATE INDEX idx_pattern_attempt_family ON pattern_attempt(family, status);
        """
    )


def _load_accounts(connection: sqlite3.Connection, path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        header = next(reader, None)
        if header != ACCOUNT_HEADER:
            raise StoreError(
                f"{path.name} has an unexpected header: {header!r}; "
                f"expected {ACCOUNT_HEADER!r}."
            )
        batch: list[tuple[Any, ...]] = []
        for line_number, row in enumerate(reader, 2):
            if len(row) != len(ACCOUNT_HEADER):
                raise StoreError(
                    f"{path.name}:{line_number} has {len(row)} fields; "
                    f"expected {len(ACCOUNT_HEADER)}."
                )
            bank_name, bank_id, account, entity_id, entity_name = row
            batch.append(
                (
                    _normalise_bank_id(bank_id),
                    bank_id,
                    account,
                    bank_name,
                    entity_id,
                    entity_name,
                )
            )
            if len(batch) >= BATCH_SIZE:
                try:
                    connection.executemany(
                        """INSERT INTO account
                           (bank_n, bank_id_raw, acct, bank_name, entity_id, entity_name)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        batch,
                    )
                except sqlite3.IntegrityError as exc:
                    raise StoreError(
                        f"Duplicate account key in {path.name}; expected unique "
                        "(Bank ID, Account Number)."
                    ) from exc
                count += len(batch)
                batch.clear()
        if batch:
            try:
                connection.executemany(
                    """INSERT INTO account
                       (bank_n, bank_id_raw, acct, bank_name, entity_id, entity_name)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    batch,
                )
            except sqlite3.IntegrityError as exc:
                raise StoreError(
                    f"Duplicate account key in {path.name}; expected unique "
                    "(Bank ID, Account Number)."
                ) from exc
            count += len(batch)
    return count


def build_store(source_dir: Path, db_path: Path, replace: bool = False) -> dict[str, Any]:
    transaction_path = source_dir / "HI-Small_Trans.csv"
    account_path = source_dir / "HI-Small_accounts.csv"
    pattern_path = source_dir / "HI-Small_Patterns.txt"
    for required in (transaction_path, account_path):
        if not required.is_file():
            raise StoreError(f"Required source file not found: {required}")
    if db_path.exists() and not replace:
        raise StoreError(
            f"Database already exists: {db_path}. Use --replace to build a fresh copy."
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = db_path.with_name(f".{db_path.name}.building")
    if temp_path.exists():
        temp_path.unlink()

    attempts, annotations = _read_patterns(pattern_path)
    annotations_by_signature: dict[tuple[str, ...], list[PatternAnnotation]] = defaultdict(list)
    for annotation in annotations:
        annotations_by_signature[annotation.signature].append(annotation)

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temp_path)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA cache_size=-131072")
        connection.execute("PRAGMA foreign_keys=ON")
        _create_schema(connection)

        print(f"Loading accounts from {account_path}", file=sys.stderr, flush=True)
        account_count = _load_accounts(connection, account_path)
        connection.commit()

        attempt_rows = [
            (attempt_id, family, description, annotation_count)
            for attempt_id, family, description, annotation_count in attempts
        ]
        connection.executemany(
            """INSERT INTO pattern_attempt
               (attempt_id, family, description, annotation_count)
               VALUES (?, ?, ?, ?)""",
            attempt_rows,
        )

        print(f"Loading transactions from {transaction_path}", file=sys.stderr, flush=True)
        positive_candidate: dict[tuple[str, ...], int] = {}
        ambiguous_signatures: set[tuple[str, ...]] = set()
        transaction_count = 0
        positive_count = 0
        min_timestamp: str | None = None
        max_timestamp: str | None = None
        transaction_batch: list[tuple[Any, ...]] = []
        positive_batch: list[tuple[int, int]] = []

        def flush_batch() -> None:
            if transaction_batch:
                connection.executemany(
                    """INSERT INTO txn (
                         txn_id, timestamp, from_bank, from_bank_n, from_acct,
                         to_bank, to_bank_n, to_acct, amt_recv, cur_recv,
                         amt_paid, cur_paid, payment_format
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    transaction_batch,
                )
                connection.executemany(
                    "INSERT INTO laundering (txn_id, label) VALUES (?, ?)",
                    positive_batch,
                )
                transaction_batch.clear()
                positive_batch.clear()
                connection.commit()

        with transaction_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source)
            header = next(reader, None)
            if header != TRANSACTION_HEADER:
                raise StoreError(
                    f"{transaction_path.name} has an unexpected header: {header!r}; "
                    f"expected the original 11 columns, including the repeated "
                    "'Account' heading."
                )
            for txn_id, row in enumerate(reader, 1):
                if len(row) != 11:
                    raise StoreError(
                        f"{transaction_path.name} source row {txn_id} has "
                        f"{len(row)} fields; expected 11."
                    )
                timestamp, from_bank, from_acct, to_bank, to_acct = row[:5]
                amount_received, currency_received, amount_paid, currency_paid = row[5:9]
                payment_format, raw_label = row[9:11]
                try:
                    datetime.strptime(timestamp, "%Y/%m/%d %H:%M")
                except ValueError as exc:
                    raise StoreError(
                        f"Invalid timestamp on source row {txn_id}: {timestamp!r}"
                    ) from exc
                _validate_amount(amount_received, "received", txn_id)
                _validate_amount(amount_paid, "paid", txn_id)
                if raw_label not in {"0", "1"}:
                    raise StoreError(
                        f"Unexpected laundering label on source row {txn_id}: "
                        f"{raw_label!r}."
                    )
                from_bank_n = _normalise_bank_id(from_bank)
                to_bank_n = _normalise_bank_id(to_bank)
                transaction_batch.append(
                    (
                        txn_id,
                        timestamp,
                        from_bank,
                        from_bank_n,
                        from_acct,
                        to_bank,
                        to_bank_n,
                        to_acct,
                        amount_received,
                        currency_received,
                        amount_paid,
                        currency_paid,
                        payment_format,
                    )
                )
                if raw_label == "1":
                    positive_count += 1
                    positive_batch.append((txn_id, 1))
                    signature = _transaction_signature(row)
                    if signature in annotations_by_signature:
                        if signature in positive_candidate:
                            positive_candidate.pop(signature, None)
                            ambiguous_signatures.add(signature)
                        elif signature not in ambiguous_signatures:
                            positive_candidate[signature] = txn_id
                transaction_count = txn_id
                min_timestamp = timestamp if min_timestamp is None else min(min_timestamp, timestamp)
                max_timestamp = timestamp if max_timestamp is None else max(max_timestamp, timestamp)
                if len(transaction_batch) >= BATCH_SIZE:
                    flush_batch()
                    if transaction_count % 250_000 < BATCH_SIZE:
                        print(
                            f"  loaded {transaction_count:,} transactions "
                            f"({positive_count:,} labelled positive)",
                            file=sys.stderr,
                            flush=True,
                        )
            flush_batch()

        print("Indexing accounts, histories, and pattern matches...", file=sys.stderr, flush=True)
        connection.execute(
            "CREATE INDEX idx_laundering_txn ON laundering(txn_id)"
        )
        connection.execute(
            "CREATE INDEX idx_txn_timestamp ON txn(timestamp, txn_id)"
        )

        annotation_batch: list[tuple[Any, ...]] = []
        link_batch: list[tuple[Any, ...]] = []
        matched_by_attempt: dict[int, int] = defaultdict(int)
        unmatched_count = 0
        ambiguous_count = 0
        for annotation in annotations:
            txn_id = positive_candidate.get(annotation.signature)
            if annotation.signature in ambiguous_signatures:
                status = "ambiguous"
                ambiguous_count += 1
            elif txn_id is None:
                status = "unmatched"
                unmatched_count += 1
            else:
                status = "matched"
                matched_by_attempt[annotation.attempt_id] += 1
                link_batch.append(
                    (
                        txn_id,
                        annotation.attempt_id,
                        annotation.line_number,
                        annotation.family,
                    )
                )
            signature_hash = hashlib.sha256(
                json.dumps(annotation.signature, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            annotation_batch.append(
                (
                    annotation.attempt_id,
                    annotation.line_number,
                    signature_hash,
                    txn_id if status == "matched" else None,
                    status,
                )
            )
            if len(annotation_batch) >= BATCH_SIZE:
                connection.executemany(
                    """INSERT INTO pattern_annotation
                       (attempt_id, line_number, signature_hash, txn_id, status)
                       VALUES (?, ?, ?, ?, ?)""",
                    annotation_batch,
                )
                annotation_batch.clear()
            if len(link_batch) >= BATCH_SIZE:
                connection.executemany(
                    """INSERT INTO pattern_link
                       (txn_id, attempt_id, line_number, family)
                       VALUES (?, ?, ?, ?)""",
                    link_batch,
                )
                link_batch.clear()
        if annotation_batch:
            connection.executemany(
                """INSERT INTO pattern_annotation
                   (attempt_id, line_number, signature_hash, txn_id, status)
                   VALUES (?, ?, ?, ?, ?)""",
                annotation_batch,
            )
        if link_batch:
            connection.executemany(
                """INSERT INTO pattern_link
                   (txn_id, attempt_id, line_number, family)
                   VALUES (?, ?, ?, ?)""",
                link_batch,
            )
        complete_attempt_count = 0
        for attempt_id, family, description, expected_annotations in attempts:
            matched = matched_by_attempt[attempt_id]
            unique_transactions = connection.execute(
                "SELECT COUNT(DISTINCT txn_id) FROM pattern_link WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()[0]
            is_complete = (
                matched == expected_annotations
                and unique_transactions == expected_annotations
            )
            status = "complete" if is_complete else "incomplete"
            complete_attempt_count += int(is_complete)
            connection.execute(
                """UPDATE pattern_attempt
                   SET matched_count=?, status=?
                   WHERE attempt_id=?""",
                (matched, status, attempt_id),
            )

        missing_endpoint_count = _count_missing_account_endpoints(connection)

        input_files = {
            "HI-Small_Trans.csv": _hash_file(transaction_path),
            "HI-Small_accounts.csv": _hash_file(account_path),
        }
        if pattern_path.is_file():
            input_files["HI-Small_Patterns.txt"] = _hash_file(pattern_path)
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "status": "complete",
            "dataset_variant": "HI-Small",
            "transaction_rows": transaction_count,
            "positive_transactions": positive_count,
            "clean_transactions": transaction_count - positive_count,
            "account_rows": account_count,
            "missing_account_endpoints": int(missing_endpoint_count),
            "pattern_attempts": len(attempts),
            "pattern_complete_attempts": complete_attempt_count,
            "pattern_annotation_lines": len(annotations),
            "pattern_matched_lines": len(annotations) - unmatched_count - ambiguous_count,
            "pattern_unmatched_lines": unmatched_count,
            "pattern_ambiguous_lines": ambiguous_count,
            "timestamp_min": min_timestamp or "",
            "timestamp_max": max_timestamp or "",
            "source_hashes": input_files,
            "built_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        connection.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                (key, json.dumps(value, separators=(",", ":")))
                for key, value in metadata.items()
            ],
        )
        connection.commit()
        connection.execute("ANALYZE")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise StoreError(f"SQLite integrity check failed: {integrity}")
        connection.close()
        connection = None
        temp_path.replace(db_path)
        return metadata
    except Exception:
        if connection is not None:
            connection.close()
        if temp_path.exists():
            temp_path.unlink()
        raise


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise StoreError(f"Local database not found: {db_path}. Run the build command first.")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    status = connection.execute(
        "SELECT value FROM meta WHERE key='status'"
    ).fetchone()
    if status is None or json.loads(status["value"]) != "complete":
        connection.close()
        raise StoreError(f"Database is incomplete or unsupported: {db_path}")
    return connection


def _metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        row["key"]: json.loads(row["value"])
        for row in connection.execute("SELECT key, value FROM meta")
    }


def check_store(db_path: Path) -> dict[str, Any]:
    connection = _connect(db_path)
    try:
        metadata = _metadata(connection)
        actual = {
            "transaction_rows": connection.execute("SELECT COUNT(*) FROM txn").fetchone()[0],
            "positive_transactions": connection.execute(
                "SELECT COUNT(*) FROM laundering"
            ).fetchone()[0],
            "account_rows": connection.execute(
                "SELECT COUNT(*) FROM account"
            ).fetchone()[0],
            "pattern_attempts": connection.execute(
                "SELECT COUNT(*) FROM pattern_attempt"
            ).fetchone()[0],
            "pattern_complete_attempts": connection.execute(
                "SELECT COUNT(*) FROM pattern_attempt WHERE status='complete'"
            ).fetchone()[0],
            "pattern_annotation_lines": connection.execute(
                "SELECT COUNT(*) FROM pattern_annotation"
            ).fetchone()[0],
            "pattern_matched_lines": connection.execute(
                "SELECT COUNT(*) FROM pattern_link"
            ).fetchone()[0],
            "pattern_unmatched_lines": connection.execute(
                "SELECT COUNT(*) FROM pattern_annotation WHERE status='unmatched'"
            ).fetchone()[0],
            "pattern_ambiguous_lines": connection.execute(
                "SELECT COUNT(*) FROM pattern_annotation WHERE status='ambiguous'"
            ).fetchone()[0],
            "missing_account_endpoints": _count_missing_account_endpoints(connection),
        }
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        actual["integrity"] = quick_check
        actual["matches_build_metadata"] = all(
            actual[key] == metadata[key]
            for key in (
                "transaction_rows",
                "positive_transactions",
                "account_rows",
                "pattern_attempts",
                "pattern_complete_attempts",
                "pattern_annotation_lines",
                "pattern_matched_lines",
                "pattern_unmatched_lines",
                "pattern_ambiguous_lines",
                "missing_account_endpoints",
            )
        )
        actual["known_hi_small_reference_counts"] = KNOWN_HI_SMALL_COUNTS
        actual["matches_known_hi_small_reference"] = all(
            actual[key] == expected
            for key, expected in KNOWN_HI_SMALL_COUNTS.items()
        )
        actual["valid"] = (
            quick_check == "ok"
            and actual["matches_build_metadata"]
            and actual["missing_account_endpoints"] == 0
            and actual["matches_known_hi_small_reference"]
            and actual["transaction_rows"] == metadata["transaction_rows"]
            and actual["positive_transactions"] == metadata["positive_transactions"]
        )
        actual["built_at_utc"] = metadata["built_at_utc"]
        actual["source_hashes"] = metadata["source_hashes"]
        return actual
    finally:
        connection.close()


def _get_transaction(connection: sqlite3.Connection, txn_id: int) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM txn WHERE txn_id=?", (txn_id,)).fetchone()
    if row is None:
        raise StoreError(f"Transaction ID {txn_id} was not found.")
    return row


def _account(connection: sqlite3.Connection, bank_n: int, acct: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM account WHERE bank_n=? AND acct=?",
        (bank_n, acct),
    ).fetchone()


def _account_key(bank_n: int, acct: str) -> str:
    return f"HI-Small|{bank_n}|{acct}"


def _transaction_key(txn_id: int) -> str:
    return f"HI-Small:{txn_id}"


def _parse_transaction_key(value: str) -> int:
    match = re.fullmatch(r"(?:HI-Small:)?([0-9]+)", value.strip())
    if not match:
        raise StoreError(
            f"Invalid transaction ID {value!r}; use a source row number such as "
            "1838771 or HI-Small:1838771."
        )
    return int(match.group(1))


def _pattern_details(connection: sqlite3.Connection, txn_id: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT pl.attempt_id, pl.family, pa.description
           FROM pattern_link AS pl
           JOIN pattern_attempt AS pa USING (attempt_id)
           WHERE pl.txn_id=?
           GROUP BY pl.attempt_id, pl.family, pa.description
           ORDER BY pl.attempt_id""",
        (txn_id,),
    )
    return [dict(row) for row in rows]


def _make_show_files(
    connection: sqlite3.Connection,
    txn_id: int,
    output_dir: Path,
) -> dict[str, Any]:
    selected = _get_transaction(connection, txn_id)
    accounts = [
        {
            "role": "sender",
            "bank_id": selected["from_bank"],
            "bank_id_normalized": selected["from_bank_n"],
            "account_number": selected["from_acct"],
            "account_key": _account_key(selected["from_bank_n"], selected["from_acct"]),
            "profile": _account(connection, selected["from_bank_n"], selected["from_acct"]),
        },
        {
            "role": "receiver",
            "bank_id": selected["to_bank"],
            "bank_id_normalized": selected["to_bank_n"],
            "account_number": selected["to_acct"],
            "account_key": _account_key(selected["to_bank_n"], selected["to_acct"]),
            "profile": _account(connection, selected["to_bank_n"], selected["to_acct"]),
        },
    ]
    for account in accounts:
        if account["profile"] is not None:
            account["profile"] = dict(account["profile"])

    prefix = f"HI-Small_{txn_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    transaction_path = output_dir / f"{prefix}_transaction.json"
    history_path = output_dir / f"{prefix}_account_histories.csv"
    label = connection.execute(
        "SELECT label FROM laundering WHERE txn_id=?", (txn_id,)
    ).fetchone()

    connection.execute(
        """CREATE TEMP TABLE show_accounts (
               bank_n INTEGER NOT NULL,
               acct TEXT NOT NULL,
               selected_role TEXT NOT NULL,
               PRIMARY KEY (bank_n, acct, selected_role)
           ) WITHOUT ROWID"""
    )
    connection.executemany(
        "INSERT INTO show_accounts (bank_n, acct, selected_role) VALUES (?, ?, ?)",
        [
            (
                selected["from_bank_n"],
                selected["from_acct"],
                "sender",
            ),
            (
                selected["to_bank_n"],
                selected["to_acct"],
                "receiver",
            ),
        ],
    )
    history_records = connection.execute(
        """SELECT t.*, a.selected_role AS selected_account_role, 'sender' AS txn_role,
                  t.from_bank_n AS selected_bank_n, t.from_acct AS selected_acct
           FROM show_accounts AS a
           JOIN txn AS t INDEXED BY idx_txn_from_account
             ON t.from_bank_n=a.bank_n AND t.from_acct=a.acct
           UNION ALL
           SELECT t.*, a.selected_role AS selected_account_role, 'receiver' AS txn_role,
                  t.to_bank_n AS selected_bank_n, t.to_acct AS selected_acct
           FROM show_accounts AS a
           JOIN txn AS t INDEXED BY idx_txn_to_account
             ON t.to_bank_n=a.bank_n AND t.to_acct=a.acct
           ORDER BY timestamp, txn_id, selected_account_role, txn_role"""
    )
    history_count = 0
    with history_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(
            [
                "Transaction ID",
                "Selected Account",
                "Transaction Side",
                "Account Key",
                "Timestamp",
                "From Bank",
                "From Account",
                "To Bank",
                "To Account",
                "Amount Received",
                "Receiving Currency",
                "Amount Paid",
                "Payment Currency",
                "Payment Format",
                "IBM Benchmark Label (analysis only)",
            ]
        )
        for history in history_records:
            current_label = connection.execute(
                "SELECT label FROM laundering WHERE txn_id=?",
                (history["txn_id"],),
            ).fetchone()
            writer.writerow(
                [
                    _transaction_key(history["txn_id"]),
                    history["selected_account_role"],
                    history["txn_role"],
                    _account_key(
                        history["selected_bank_n"], history["selected_acct"]
                    ),
                    history["timestamp"],
                    history["from_bank"],
                    history["from_acct"],
                    history["to_bank"],
                    history["to_acct"],
                    history["amt_recv"],
                    history["cur_recv"],
                    history["amt_paid"],
                    history["cur_paid"],
                    history["payment_format"],
                    current_label["label"] if current_label else 0,
                ]
            )
            history_count += 1

    detail = {
        "dataset_variant": "HI-Small",
        "transaction_id": _transaction_key(txn_id),
        "source_row_number": txn_id,
        "timestamp": selected["timestamp"],
        "from_bank": selected["from_bank"],
        "from_account": selected["from_acct"],
        "to_bank": selected["to_bank"],
        "to_account": selected["to_acct"],
        "amount_received": selected["amt_recv"],
        "receiving_currency": selected["cur_recv"],
        "amount_paid": selected["amt_paid"],
        "payment_currency": selected["cur_paid"],
        "payment_format": selected["payment_format"],
        "ibm_benchmark_label_for_analysis_only": label["label"] if label else 0,
        "accounts": accounts,
        "patterns": _pattern_details(connection, txn_id),
        "account_history_rows": history_count,
        "account_history_file": str(history_path),
    }
    transaction_path.write_text(
        json.dumps(detail, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "transaction_file": str(transaction_path),
        "account_history_file": str(history_path),
        "history_rows": history_count,
        "unique_history_transactions": connection.execute(
            """SELECT COUNT(DISTINCT txn_id) FROM (
                   SELECT t.txn_id
                   FROM show_accounts AS a
                   JOIN txn AS t INDEXED BY idx_txn_from_account
                     ON t.from_bank_n=a.bank_n AND t.from_acct=a.acct
                   UNION ALL
                   SELECT t.txn_id
                   FROM show_accounts AS a
                   JOIN txn AS t INDEXED BY idx_txn_to_account
                     ON t.to_bank_n=a.bank_n AND t.to_acct=a.acct
               )"""
        ).fetchone()[0],
        "patterns": detail["patterns"],
    }


def _sample_ids(
    connection: sqlite3.Connection,
    count: int,
    label: int,
    rng: random.Random,
    excluded: set[int] | None = None,
) -> list[int]:
    if count <= 0:
        return []
    excluded = excluded or set()
    total = int(connection.execute("SELECT COUNT(*) FROM txn").fetchone()[0])
    if label == 1:
        candidates = [
            row[0]
            for row in connection.execute(
                "SELECT txn_id FROM laundering ORDER BY txn_id"
            )
            if row[0] not in excluded
        ]
        available = len(candidates)
        if count > available:
            raise StoreError(
                f"Requested {count:,} laundering transactions, but only "
                f"{available:,} are available."
            )
        return rng.sample(candidates, count)

    positive_count = int(
        connection.execute("SELECT COUNT(*) FROM laundering").fetchone()[0]
    )
    excluded_clean_count = sum(
        1
        for txn_id in excluded
        if connection.execute(
            "SELECT 1 FROM txn WHERE txn_id=? AND NOT EXISTS "
            "(SELECT 1 FROM laundering WHERE laundering.txn_id=txn.txn_id)",
            (txn_id,),
        ).fetchone()
    )
    available = total - positive_count - excluded_clean_count
    if count > available:
        raise StoreError(
            f"Requested {count:,} clean transactions, but only {available:,} "
            "are available after the current selection."
        )
    selected: set[int] = set()
    if count > total // 20:
        reservoir: list[int] = []
        seen = 0
        for (txn_id,) in connection.execute(
            """SELECT t.txn_id FROM txn AS t
               LEFT JOIN laundering AS l USING (txn_id)
               WHERE l.txn_id IS NULL"""
        ):
            if txn_id in excluded:
                continue
            seen += 1
            if len(reservoir) < count:
                reservoir.append(txn_id)
            else:
                position = rng.randrange(seen)
                if position < count:
                    reservoir[position] = txn_id
        if len(reservoir) != count:
            raise StoreError(
                f"Unable to sample {count:,} clean transactions; found "
                f"{len(reservoir):,}."
            )
        return reservoir

    max_attempts = max(100_000, count * 100)
    attempts = 0
    while len(selected) < count and attempts < max_attempts:
        txn_id = rng.randint(1, total)
        attempts += 1
        if txn_id in selected or txn_id in excluded:
            continue
        if connection.execute(
            "SELECT 1 FROM laundering WHERE txn_id=?", (txn_id,)
        ).fetchone():
            continue
        if connection.execute("SELECT 1 FROM txn WHERE txn_id=?", (txn_id,)).fetchone():
            selected.add(txn_id)
    if len(selected) != count:
        raise StoreError(
            f"Unable to sample the requested {count:,} clean transactions."
        )
    return list(selected)


def _pattern_attempts(
    connection: sqlite3.Connection,
    families: Sequence[str] | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM pattern_attempt WHERE status='complete'"
    parameters: list[Any] = []
    if families:
        placeholders = ",".join("?" for _ in families)
        sql += f" AND family IN ({placeholders})"
        parameters.extend(family.upper() for family in families)
    sql += " ORDER BY attempt_id"
    return list(connection.execute(sql, parameters))


def _ids_for_attempt(connection: sqlite3.Connection, attempt_id: int) -> list[int]:
    return [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT txn_id FROM pattern_link WHERE attempt_id=? ORDER BY txn_id",
            (attempt_id,),
        )
    ]


def _expand_account_history(
    connection: sqlite3.Connection,
    base_ids: set[int],
    history_cap: int | None,
) -> set[int]:
    connection.execute(
        "CREATE TEMP TABLE selected_accounts (bank_n INTEGER, acct TEXT, "
        "PRIMARY KEY (bank_n, acct)) WITHOUT ROWID"
    )
    base_transactions = _transactions_by_ids(connection, sorted(base_ids))
    account_rows = {
        (row["from_bank_n"], row["from_acct"]) for row in base_transactions
    } | {(row["to_bank_n"], row["to_acct"]) for row in base_transactions}
    connection.executemany(
        "INSERT INTO selected_accounts (bank_n, acct) VALUES (?, ?)",
        sorted(account_rows),
    )

    if history_cap is None:
        history_ids = {
            row[0]
            for row in connection.execute(
                """SELECT t.txn_id
                   FROM selected_accounts AS a
                   JOIN txn AS t INDEXED BY idx_txn_from_account
                     ON t.from_bank_n=a.bank_n AND t.from_acct=a.acct
                   UNION
                   SELECT t.txn_id
                   FROM selected_accounts AS a
                   JOIN txn AS t INDEXED BY idx_txn_to_account
                     ON t.to_bank_n=a.bank_n AND t.to_acct=a.acct"""
            )
        }
    else:
        history_ids: set[int] = set()
        for bank_n, acct in sorted(account_rows):
            for row in connection.execute(
                """SELECT txn_id FROM txn
                   WHERE from_bank_n=? AND from_acct=?
                   UNION
                   SELECT txn_id FROM txn
                   WHERE to_bank_n=? AND to_acct=?
                   ORDER BY txn_id LIMIT ?""",
                (bank_n, acct, bank_n, acct, history_cap),
            ):
                history_ids.add(row[0])
    return history_ids - base_ids


def _transactions_by_ids(
    connection: sqlite3.Connection,
    txn_ids: Sequence[int],
) -> list[sqlite3.Row]:
    records: list[sqlite3.Row] = []
    for start in range(0, len(txn_ids), 500):
        batch = txn_ids[start : start + 500]
        if not batch:
            continue
        placeholders = ",".join("?" for _ in batch)
        records.extend(
            connection.execute(
                f"SELECT * FROM txn WHERE txn_id IN ({placeholders})",
                batch,
            )
        )
    records.sort(key=lambda row: (row["timestamp"], row["txn_id"]))
    return records


def _selected_patterns(
    connection: sqlite3.Connection,
    selected_attempts: set[int],
) -> dict[int, list[tuple[int, str]]]:
    result: dict[int, list[tuple[int, str]]] = defaultdict(list)
    if not selected_attempts:
        return result
    ids = sorted(selected_attempts)
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        placeholders = ",".join("?" for _ in batch)
        for row in connection.execute(
            f"""SELECT txn_id, attempt_id, family FROM pattern_link
                WHERE attempt_id IN ({placeholders})
                ORDER BY attempt_id, txn_id""",
            batch,
        ):
            result[row["txn_id"]].append((row["attempt_id"], row["family"]))
    return result


def _export_test_set(
    connection: sqlite3.Connection,
    metadata: dict[str, Any],
    output_dir: Path,
    name: str,
    selected_reasons: dict[int, set[str]],
    selected_attempts: set[int],
    seed: int,
    expand_accounts: bool,
    history_cap: int | None,
    selection_spec: dict[str, Any],
    overwrite: bool,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", name):
        raise StoreError(
            "Run name must use 1–80 letters, numbers, dots, underscores, or hyphens."
        )
    base_ids = set(selected_reasons)
    if not base_ids:
        raise StoreError("No transactions were selected.")
    reasons = {txn_id: set(values) for txn_id, values in selected_reasons.items()}
    if expand_accounts:
        context_ids = _expand_account_history(connection, base_ids, history_cap)
        for txn_id in context_ids:
            reasons.setdefault(txn_id, set()).add("account-history")

    txn_ids = sorted(reasons)
    transactions = _transactions_by_ids(connection, txn_ids)
    if len(transactions) != len(txn_ids):
        raise StoreError("The selection contains a transaction missing from the store.")
    selected_patterns = _selected_patterns(connection, selected_attempts)

    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / f"{name}_test_input.csv"
    accounts_path = output_dir / f"{name}_accounts.csv"
    truth_path = output_dir / f"{name}_ground_truth.csv"
    manifest_path = output_dir / f"{name}_manifest.json"
    output_paths = (input_path, accounts_path, truth_path, manifest_path)
    existing = [path.name for path in output_paths if path.exists()]
    if existing and not overwrite:
        raise StoreError(
            f"Output run already exists ({', '.join(existing)}). Use --overwrite "
            "to replace this run intentionally, or choose another --name."
        )
    account_keys: set[tuple[int, str]] = set()

    with input_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(INPUT_HEADER)
        for txn in transactions:
            txn_id = txn["txn_id"]
            account_keys.add((txn["from_bank_n"], txn["from_acct"]))
            account_keys.add((txn["to_bank_n"], txn["to_acct"]))
            common = [
                "HI-Small",
                None,
                "",
                None,
                None,
                "",
                txn["timestamp"].replace("/", "-") + ":00",
                None,
                None,
                "",
                txn["payment_format"],
                None,
                _transaction_key(txn_id),
            ]
            debit = common.copy()
            debit[1] = _account_key(txn["from_bank_n"], txn["from_acct"])
            debit[3] = txn["amt_paid"]
            debit[4] = txn["cur_paid"]
            debit[7] = txn["to_acct"]
            debit[8] = txn["to_bank"]
            debit[11] = "Debit"
            writer.writerow(debit)

            credit = common.copy()
            credit[1] = _account_key(txn["to_bank_n"], txn["to_acct"])
            credit[3] = txn["amt_recv"]
            credit[4] = txn["cur_recv"]
            credit[7] = txn["from_acct"]
            credit[8] = txn["from_bank"]
            credit[11] = "Credit"
            writer.writerow(credit)

    missing_accounts = 0
    with accounts_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(ACCOUNT_EXPORT_HEADER)
        for bank_n, acct in sorted(account_keys):
            account = _account(connection, bank_n, acct)
            if account is None:
                missing_accounts += 1
                writer.writerow(
                    [
                        "HI-Small",
                        _account_key(bank_n, acct),
                        str(bank_n),
                        acct,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                    ]
                )
            else:
                writer.writerow(
                    [
                        "HI-Small",
                        _account_key(bank_n, acct),
                        account["bank_id_raw"],
                        account["acct"],
                        account["bank_name"],
                        account["entity_id"],
                        account["entity_name"],
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                    ]
                )

    with truth_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(GROUND_TRUTH_HEADER)
        for txn in transactions:
            txn_id = txn["txn_id"]
            is_positive = connection.execute(
                "SELECT 1 FROM laundering WHERE txn_id=?", (txn_id,)
            ).fetchone() is not None
            pattern_details = selected_patterns.get(txn_id, [])
            families = sorted({family for _, family in pattern_details})
            attempts = sorted({attempt_id for attempt_id, _ in pattern_details})
            reason_values = sorted(reasons[txn_id])
            writer.writerow(
                [
                    "HI-Small",
                    _transaction_key(txn_id),
                    1 if is_positive else 0,
                    ";".join(families),
                    ";".join(str(attempt_id) for attempt_id in attempts),
                    ";".join(reason_values),
                ]
            )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_variant": "HI-Small",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "selection_request": selection_spec,
        "selection_unit": "one directed source transfer per unique transaction ID",
        "input_representation": (
            "Two account-side event rows (debit and credit) per transfer; both share "
            "the source transaction ID."
        ),
        "labels": (
            "Labels and pattern annotations are in the separate ground-truth file; "
            "they are not in the test input."
        ),
        "requested_transaction_count": len(base_ids),
        "selected_seed_transaction_count": len(base_ids),
        "exported_transaction_count": len(transactions),
        "exported_account_side_rows": len(transactions) * 2,
        "positive_transactions": sum(
            1
            for txn in transactions
            if connection.execute(
                "SELECT 1 FROM laundering WHERE txn_id=?", (txn["txn_id"],)
            ).fetchone()
        ),
        "clean_transactions": sum(
            1
            for txn in transactions
            if not connection.execute(
                "SELECT 1 FROM laundering WHERE txn_id=?", (txn["txn_id"],)
            ).fetchone()
        ),
        "context_transactions": sum(
            1 for reason_set in reasons.values() if "account-history" in reason_set
        ),
        "account_profiles": len(account_keys),
        "missing_account_profiles": missing_accounts,
        "whole_pattern_attempts_included": sorted(selected_attempts),
        "account_history_expanded": expand_accounts,
        "history_cap_per_account": history_cap,
        "source_hashes": metadata["source_hashes"],
        "files": {
            input_path.name: _hash_file(input_path),
            accounts_path.name: _hash_file(accounts_path),
            truth_path.name: _hash_file(truth_path),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "input_file": str(input_path),
        "accounts_file": str(accounts_path),
        "ground_truth_file": str(truth_path),
        "manifest_file": str(manifest_path),
        "requested_transactions": len(base_ids),
        "exported_transactions": len(transactions),
        "account_side_rows": len(transactions) * 2,
        "positive_transactions": manifest["positive_transactions"],
        "clean_transactions": manifest["clean_transactions"],
        "context_transactions": manifest["context_transactions"],
        "account_profiles": len(account_keys),
        "missing_account_profiles": missing_accounts,
    }


def select_test_set(args: argparse.Namespace) -> dict[str, Any]:
    connection = _connect(args.db)
    try:
        metadata = _metadata(connection)
        rng = random.Random(args.seed)
        selected: dict[int, set[str]] = {}
        selected_attempts: set[int] = set()
        if args.history_cap is not None and args.expand != "accounts":
            raise StoreError("--history-cap requires --expand accounts.")

        if args.txn is not None:
            if args.laundering or args.clean or args.pattern_type:
                raise StoreError(
                    "Use --txn by itself for a specific transfer; do not combine it "
                    "with class counts or pattern-family filters."
                )
            txn_id = _parse_transaction_key(args.txn)
            _get_transaction(connection, txn_id)
            selected[txn_id] = {"requested-transaction"}
            if args.with_pattern:
                attempts = connection.execute(
                    """SELECT DISTINCT pa.attempt_id, pa.status
                       FROM pattern_link AS pl
                       JOIN pattern_attempt AS pa USING (attempt_id)
                       WHERE pl.txn_id=?
                       ORDER BY pa.attempt_id""",
                    (txn_id,),
                )
                for row in attempts:
                    if row["status"] != "complete":
                        continue
                    selected_attempts.add(row["attempt_id"])
                    for pattern_txn_id in _ids_for_attempt(
                        connection, row["attempt_id"]
                    ):
                        selected.setdefault(pattern_txn_id, set()).add(
                            f"whole-pattern:{row['attempt_id']}"
                        )
        else:
            if args.with_pattern:
                raise StoreError("--with-pattern requires --txn.")
            laundering_target = args.laundering or 0
            clean_target = args.clean or 0
            if laundering_target < 0 or clean_target < 0:
                raise StoreError("Selection counts cannot be negative.")
            if laundering_target == 0 and clean_target == 0:
                raise StoreError(
                    "Select a transaction with --txn, or request a positive "
                    "laundering/clean count."
                )
            families = args.pattern_type or None
            if families and laundering_target == 0:
                raise StoreError("--pattern-type requires --laundering.")
            if args.whole_patterns and laundering_target and not args.no_whole_patterns:
                attempts = _pattern_attempts(connection, families)
                rng.shuffle(attempts)
                for attempt in attempts:
                    if len(selected) >= laundering_target:
                        break
                    selected_attempts.add(attempt["attempt_id"])
                    for txn_id in _ids_for_attempt(
                        connection, attempt["attempt_id"]
                    ):
                        selected.setdefault(txn_id, set()).add(
                            f"whole-pattern:{attempt['attempt_id']}"
                        )

                current_positive = {
                    txn_id
                    for txn_id in selected
                    if connection.execute(
                        "SELECT 1 FROM laundering WHERE txn_id=?", (txn_id,)
                    ).fetchone()
                }
                if len(current_positive) < laundering_target and families is None:
                    needed = laundering_target - len(current_positive)
                    remaining_unpatterned = [
                        row[0]
                        for row in connection.execute(
                            """SELECT l.txn_id
                               FROM laundering AS l
                               LEFT JOIN pattern_link AS pl USING (txn_id)
                               WHERE pl.txn_id IS NULL
                               ORDER BY l.txn_id"""
                        )
                        if row[0] not in selected
                    ]
                    if len(remaining_unpatterned) < needed:
                        raise StoreError(
                            f"Whole matched patterns and unannotated positives provide "
                            f"only {len(current_positive) + len(remaining_unpatterned):,} "
                            f"transactions, fewer than the requested {laundering_target:,}. "
                            "Use --no-whole-patterns to select individual transactions."
                        )
                    for txn_id in rng.sample(remaining_unpatterned, needed):
                        selected.setdefault(txn_id, set()).add(
                            "unannotated-laundering"
                        )
                elif len(current_positive) < laundering_target:
                    raise StoreError(
                        f"Complete patterns in the requested family/families cover "
                        f"only {len(current_positive):,} transactions, fewer than the "
                        f"requested {laundering_target:,}."
                    )
            elif laundering_target:
                if families:
                    attempts = _pattern_attempts(connection, families)
                    pattern_txn_ids = {
                        txn_id
                        for attempt in attempts
                        for txn_id in _ids_for_attempt(
                            connection, attempt["attempt_id"]
                        )
                    }
                    available_ids = sorted(pattern_txn_ids)
                    if laundering_target > len(available_ids):
                        raise StoreError(
                            f"Requested {laundering_target:,} positive transactions "
                            f"from those pattern families; only {len(available_ids):,} "
                            "are available."
                        )
                    chosen_positive = rng.sample(available_ids, laundering_target)
                else:
                    chosen_positive = _sample_ids(
                        connection, laundering_target, 1, rng
                    )
                for txn_id in chosen_positive:
                    selected.setdefault(txn_id, set()).add(
                        "requested-laundering"
                    )

            clean_ids = _sample_ids(
                connection,
                clean_target,
                0,
                rng,
                excluded=set(selected),
            )
            for txn_id in clean_ids:
                selected.setdefault(txn_id, set()).add("requested-clean")

        if not selected:
            raise StoreError("The selection did not include any transactions.")
        selection_spec = {
            "transaction_id": args.txn,
            "requested_laundering_transactions": args.laundering or 0,
            "requested_clean_transactions": args.clean or 0,
            "pattern_families": [family.upper() for family in args.pattern_type or []],
            "whole_patterns": not args.no_whole_patterns,
            "with_pattern_for_transaction": args.with_pattern,
            "expand_account_history": args.expand == "accounts",
            "history_cap_per_account": args.history_cap,
        }
        return _export_test_set(
            connection=connection,
            metadata=metadata,
            output_dir=args.out,
            name=args.name,
            selected_reasons=selected,
            selected_attempts=selected_attempts,
            seed=args.seed,
            expand_accounts=args.expand == "accounts",
            history_cap=args.history_cap,
            selection_spec=selection_spec,
            overwrite=args.overwrite,
        )
    finally:
        connection.close()


def verify_fixture(
    db_path: Path,
    fixture_path: Path,
    ground_truth_path: Path | None,
) -> dict[str, Any]:
    connection = _connect(db_path)
    try:
        if not fixture_path.is_file():
            raise StoreError(f"Fixture file not found: {fixture_path}")
        checked_rows = 0
        seen_directions: dict[int, set[str]] = defaultdict(set)
        expected_labels: dict[int, int] = {}
        with fixture_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            required = {
                "Dataset Variant (benchmark only)",
                "Account Key",
                "Transaction Amount in Original Currency",
                "Original Currency Code",
                "Transaction DateTime",
                "Opposite Account Number",
                "Opposite Bank",
                "Transaction Type",
                "Transaction Direction",
                "Unique Identifier for the transaction",
            }
            if not required.issubset(reader.fieldnames or []):
                raise StoreError(
                    f"Fixture is missing required columns: "
                    f"{sorted(required - set(reader.fieldnames or []))}."
                )
            for line_number, row in enumerate(reader, 2):
                if row["Dataset Variant (benchmark only)"] != "HI-Small":
                    raise StoreError(
                        f"Unexpected variant on fixture line {line_number}."
                    )
                txn_id = _parse_transaction_key(
                    row["Unique Identifier for the transaction"]
                )
                txn = _get_transaction(connection, txn_id)
                direction = row["Transaction Direction"].strip().casefold()
                if direction not in {"debit", "credit"}:
                    raise StoreError(
                        f"Invalid direction on fixture line {line_number}: "
                        f"{direction!r}."
                    )
                if direction in seen_directions[txn_id]:
                    raise StoreError(
                        f"Duplicate {direction} row for transaction {txn_id} "
                        f"in fixture."
                    )
                seen_directions[txn_id].add(direction)
                if direction == "debit":
                    expected = {
                        "Account Key": _account_key(
                            txn["from_bank_n"], txn["from_acct"]
                        ),
                        "Transaction Amount in Original Currency": txn["amt_paid"],
                        "Original Currency Code": txn["cur_paid"],
                        "Opposite Account Number": txn["to_acct"],
                        "Opposite Bank": txn["to_bank"],
                    }
                else:
                    expected = {
                        "Account Key": _account_key(
                            txn["to_bank_n"], txn["to_acct"]
                        ),
                        "Transaction Amount in Original Currency": txn["amt_recv"],
                        "Original Currency Code": txn["cur_recv"],
                        "Opposite Account Number": txn["from_acct"],
                        "Opposite Bank": txn["from_bank"],
                    }
                for field, expected_value in expected.items():
                    actual_value = row[field].strip()
                    if field == "Transaction Amount in Original Currency":
                        try:
                            matches = Decimal(actual_value) == Decimal(
                                str(expected_value)
                            )
                        except InvalidOperation:
                            matches = False
                    else:
                        matches = actual_value == str(expected_value)
                    if not matches:
                        raise StoreError(
                            f"Fixture mismatch at line {line_number}, field "
                            f"{field!r}: expected {expected_value!r}, found "
                            f"{row[field]!r}."
                        )
                expected_time = datetime.strptime(
                    txn["timestamp"], "%Y/%m/%d %H:%M"
                ).strftime("%Y-%m-%d %H:%M:%S")
                fixture_time = row["Transaction DateTime"].strip().replace("T", " ")
                if fixture_time != expected_time:
                    raise StoreError(
                        f"Fixture timestamp mismatch on line {line_number}: "
                        f"expected {expected_time!r}, found {fixture_time!r}."
                    )
                if row["Transaction Type"].strip() != txn["payment_format"]:
                    raise StoreError(
                        f"Fixture payment type mismatch on line {line_number}."
                    )
                label_row = connection.execute(
                    "SELECT label FROM laundering WHERE txn_id=?", (txn_id,)
                ).fetchone()
                expected_labels[txn_id] = 1 if label_row else 0
                checked_rows += 1

        missing_sides = {
            txn_id: sorted({"debit", "credit"} - directions)
            for txn_id, directions in seen_directions.items()
            if directions != {"debit", "credit"}
        }
        if missing_sides:
            raise StoreError(
                f"Fixture is missing a debit or credit row for "
                f"{len(missing_sides)} transaction(s)."
            )

        checked_truth_rows = 0
        if ground_truth_path is None:
            sibling = fixture_path.with_name(
                "FrameAML_HI-Small_Synthetic_Fixture_Ground_Truth.csv"
            )
            ground_truth_path = sibling if sibling.is_file() else None
        if ground_truth_path is not None:
            if not ground_truth_path.is_file():
                raise StoreError(f"Ground-truth file not found: {ground_truth_path}")
            with ground_truth_path.open(
                "r", encoding="utf-8-sig", newline=""
            ) as source:
                reader = csv.DictReader(source)
                required = {
                    "Dataset Variant",
                    "Unique Identifier for the transaction",
                    "IBM Benchmark Label (evaluation only)",
                }
                if not required.issubset(reader.fieldnames or []):
                    raise StoreError(
                        "Fixture ground truth is missing required columns."
                    )
                for line_number, row in enumerate(reader, 2):
                    txn_id = _parse_transaction_key(
                        row["Unique Identifier for the transaction"]
                    )
                    if row["Dataset Variant"] != "HI-Small":
                        raise StoreError(
                            f"Unexpected ground-truth variant on line {line_number}."
                        )
                    try:
                        actual_label = int(
                            row["IBM Benchmark Label (evaluation only)"]
                        )
                    except ValueError as exc:
                        raise StoreError(
                            f"Invalid ground-truth label on line {line_number}."
                        ) from exc
                    if expected_labels.get(txn_id) != actual_label:
                        raise StoreError(
                            f"Ground-truth label mismatch for {_transaction_key(txn_id)}."
                        )
                    checked_truth_rows += 1
            if checked_truth_rows != len(expected_labels):
                raise StoreError(
                    "The fixture and separate ground-truth file contain different "
                    "numbers of unique transaction IDs."
                )

        return {
            "result": "PASS",
            "fixture_rows_verified": checked_rows,
            "unique_transactions_verified": len(seen_directions),
            "ground_truth_transactions_verified": checked_truth_rows,
        }
    finally:
        connection.close()


def _add_db_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Local SQLite database path.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build and query a private local store of the original IBM HI-Small "
            "transactions, account records, and pattern annotations."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Load the source CSVs into a local SQLite database.")
    build.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    build.add_argument("--replace", action="store_true", help="Replace the existing database after a successful rebuild.")

    check = subparsers.add_parser("check", help="Verify row counts, account joins, and database integrity.")
    _add_db_option(check)

    show = subparsers.add_parser("show", help="Export a transaction, its two accounts, and all their transaction history.")
    _add_db_option(show)
    show.add_argument("--txn", required=True, help="Source row number or HI-Small:<row number>.")
    show.add_argument("--out", type=Path, default=DATA_DIR / "lookups")

    select = subparsers.add_parser("select", help="Create a label-free test input plus separate ground truth.")
    _add_db_option(select)
    select.add_argument("--txn", help="Select one exact source transaction ID.")
    select.add_argument("--with-pattern", action="store_true", help="Include complete matched IBM scenario attempts for --txn.")
    select.add_argument("--laundering", type=int, default=0, help="Number of positive transactions to select.")
    select.add_argument("--clean", type=int, default=0, help="Number of clean transactions to select.")
    select.add_argument("--pattern-type", action="append", help="Restrict positive pattern selection to a family, e.g. CYCLE.")
    pattern_group = select.add_mutually_exclusive_group()
    pattern_group.add_argument("--whole-patterns", dest="whole_patterns", action="store_true", default=True, help="Keep selected annotated scenarios complete (default).")
    pattern_group.add_argument("--no-whole-patterns", dest="no_whole_patterns", action="store_true", help="Select individual positive transactions instead of whole patterns.")
    select.add_argument("--expand", choices=("none", "accounts"), default="none", help="Add one-hop history for the selected transactions' accounts.")
    select.add_argument("--history-cap", type=int, help="Optional maximum history rows per selected account; default is no cap.")
    select.add_argument("--seed", type=int, default=20261029, help="Random seed for reproducible selection.")
    select.add_argument("--out", type=Path, default=DEFAULT_EXPORT_DIR)
    select.add_argument("--name", default="sample", help="Output file prefix.")
    select.add_argument("--overwrite", action="store_true", help="Replace an existing output run with the same name.")

    verify = subparsers.add_parser("verify-fixture", help="Verify fixture IDs, account legs, amounts, and labels against the local store.")
    _add_db_option(verify)
    verify.add_argument("--fixture", type=Path, default=PROJECT_DIR / "test_data" / "FrameAML_HI-Small_Synthetic_Development_Fixture.csv")
    verify.add_argument("--ground-truth", type=Path)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            metadata = build_store(args.source_dir, args.db, args.replace)
            print(json.dumps({"database": str(args.db), **metadata}, indent=2))
        elif args.command == "check":
            report = check_store(args.db)
            print(json.dumps(report, indent=2))
            return 0 if report["valid"] else 1
        elif args.command == "show":
            txn_id = _parse_transaction_key(args.txn)
            connection = _connect(args.db)
            try:
                report = _make_show_files(connection, txn_id, args.out)
            finally:
                connection.close()
            print(json.dumps(report, indent=2))
        elif args.command == "select":
            if args.history_cap is not None and args.history_cap <= 0:
                raise StoreError("--history-cap must be greater than zero.")
            report = select_test_set(args)
            print(json.dumps(report, indent=2))
        elif args.command == "verify-fixture":
            report = verify_fixture(args.db, args.fixture, args.ground_truth)
            print(json.dumps(report, indent=2))
        return 0
    except (StoreError, OSError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
