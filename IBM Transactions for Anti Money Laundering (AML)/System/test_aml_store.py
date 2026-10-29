"""Tests for aml_store.py. Run:  pytest -q   (uses a small synthetic IBM-format dataset)."""
import csv
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import aml_store as A                     # noqa: E402
from make_synthetic_ibm import make       # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("ibm")
    info = make(d / "archive", n_txn=4000, n_accounts=600, n_attempts=30, unannotated=20)
    db = d / "t.sqlite"
    A.main(["build", "--source-dir", str(d / "archive"), "--db", str(db)])
    return d, db, info


def rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_counts_and_labels_stored_separately(built):
    _, db, info = built
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM txn").fetchone()[0] == info["rows"]
    assert c.execute("SELECT COUNT(*) FROM laundering").fetchone()[0] == info["positives"]
    cols = [r[1] for r in c.execute("PRAGMA table_info(txn)")]
    assert not any("launder" in x or "label" in x for x in cols), "label must not live in the txn table"


def test_every_pattern_line_matched_even_with_clean_twin(built):
    _, db, info = built
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM pattern_link").fetchone()[0] == info["pattern_lines"]
    # the clean twin must NOT have been linked: every linked row is a laundering row
    bad = c.execute("SELECT COUNT(*) FROM pattern_link pl LEFT JOIN laundering l USING(txn_id) "
                    "WHERE l.txn_id IS NULL").fetchone()[0]
    assert bad == 0


def test_account_joins_survive_zero_padding(built):
    _, db, _ = built
    c = sqlite3.connect(db)
    missing = c.execute("""SELECT COUNT(*) FROM (SELECT from_bank_n b, from_acct a FROM txn UNION
                           SELECT to_bank_n, to_acct FROM txn) e
                           LEFT JOIN account ac ON ac.bank_n=e.b AND ac.acct=e.a WHERE ac.acct IS NULL""").fetchone()[0]
    assert missing == 0


def test_select_laundering_and_clean(built, tmp_path):
    _, db, _ = built
    A.main([
        "select", "--db", str(db), "--laundering", "60", "--clean", "100",
        "--no-whole-patterns", "--out", str(tmp_path), "--name", "x",
    ])
    inp, truth, accts = (rows(tmp_path / f"x_{k}.csv") for k in ("test_input", "ground_truth", "accounts"))
    assert len(truth) == 160 and len(inp) == 320                      # two legs per transaction
    assert sum(int(r["IBM Benchmark Label (evaluation only)"]) for r in truth) == 60
    assert not any("launder" in k.lower() or "label" in k.lower() for k in inp[0]), "no label in test input"
    ids = {r["Unique Identifier for the transaction"] for r in inp}
    assert ids == {r["Unique Identifier for the transaction"] for r in truth}
    keys = {r["Account Key"] for r in inp}
    assert keys == {r["Account Key"] for r in accts}                  # every account in the input is described
    verified = A.verify_fixture(
        db, tmp_path / "x_test_input.csv", tmp_path / "x_ground_truth.csv"
    )
    assert verified["result"] == "PASS"


def test_legs_use_correct_side_amounts(built, tmp_path):
    _, db, _ = built
    A.main(["select", "--db", str(db), "--txn", "100", "--out", str(tmp_path), "--name", "y"])
    inp = rows(tmp_path / "y_test_input.csv")
    c = sqlite3.connect(db)
    t = c.execute("SELECT amt_paid, cur_paid, amt_recv, cur_recv, from_bank, to_bank FROM txn WHERE txn_id=100").fetchone()
    d = next(r for r in inp if r["Transaction Direction"] == "Debit")
    cr = next(r for r in inp if r["Transaction Direction"] == "Credit")
    assert (d["Transaction Amount in Original Currency"], d["Original Currency Code"], d["Opposite Bank"]) == (t[0], t[1], t[5])
    assert (cr["Transaction Amount in Original Currency"], cr["Original Currency Code"], cr["Opposite Bank"]) == (t[2], t[3], t[4])
    assert d["Unique Identifier for the transaction"] == cr["Unique Identifier for the transaction"] == "HI-Small:100"


def test_whole_patterns_stay_complete(built, tmp_path):
    _, db, _ = built
    A.main(["select", "--db", str(db), "--laundering", "80", "--out", str(tmp_path), "--name", "z"])
    truth = rows(tmp_path / "z_ground_truth.csv")
    per = {}
    for r in truth:
        if r["Pattern Attempt IDs (evaluation only)"]:
            for attempt_id in r["Pattern Attempt IDs (evaluation only)"].split(";"):
                per[attempt_id] = per.get(attempt_id, 0) + 1
    c = sqlite3.connect(db)
    for aid, n in per.items():
        assert c.execute("SELECT COUNT(*) FROM pattern_link WHERE attempt_id=?", (aid,)).fetchone()[0] == n


def test_expand_accounts_contains_all_history(built, tmp_path):
    _, db, _ = built
    A.main(["select", "--db", str(db), "--laundering", "20", "--expand", "accounts", "--out", str(tmp_path), "--name", "e"])
    truth = rows(tmp_path / "e_ground_truth.csv")
    chosen = {int(r["Unique Identifier for the transaction"].split(":")[1]) for r in truth}
    base = {int(r["Unique Identifier for the transaction"].split(":")[1]) for r in truth
            if r["Selection Reason (evaluation only)"] != "account-history"}
    c = sqlite3.connect(db)
    accts = set()
    for t in base:
        a_, b_, c_, d_ = c.execute("SELECT from_bank_n, from_acct, to_bank_n, to_acct FROM txn WHERE txn_id=?", (t,)).fetchone()
        accts |= {(a_, b_), (c_, d_)}
    expected = set()
    for bn, ac in accts:
        expected |= {r[0] for r in c.execute(
            "SELECT txn_id FROM txn WHERE (from_bank_n=? AND from_acct=?) OR (to_bank_n=? AND to_acct=?)", (bn, ac, bn, ac))}
    assert chosen == expected


def test_show_writes_both_endpoint_histories(built, tmp_path):
    _, db, _ = built
    A.main(["show", "--db", str(db), "--txn", "100", "--out", str(tmp_path)])
    detail = json.loads((tmp_path / "HI-Small_100_transaction.json").read_text())
    history = rows(tmp_path / "HI-Small_100_account_histories.csv")
    assert len(detail["accounts"]) == 2
    assert len(history) == detail["account_history_rows"]
    assert {row["Transaction Side"] for row in history} == {"sender", "receiver"}
    assert "IBM Benchmark Label (analysis only)" in history[0]


def test_same_seed_same_selection(built, tmp_path):
    _, db, _ = built
    for n in ("a", "b"):
        A.main(["select", "--db", str(db), "--laundering", "30", "--clean", "30", "--seed", "5", "--out", str(tmp_path), "--name", n])
    assert (tmp_path / "a_ground_truth.csv").read_text() == (tmp_path / "b_ground_truth.csv").read_text()


def test_existing_run_requires_explicit_overwrite(built, tmp_path):
    _, db, _ = built
    arguments = [
        "select", "--db", str(db), "--txn", "100", "--out", str(tmp_path), "--name", "same"
    ]
    assert A.main(arguments) == 0
    assert A.main(arguments) == 2
    assert A.main(arguments + ["--overwrite"]) == 0


def test_invalid_selection_options_fail_loudly(built, tmp_path):
    _, db, _ = built
    assert A.main([
        "select", "--db", str(db), "--pattern-type", "CYCLE", "--out", str(tmp_path)
    ]) == 2
    assert A.main([
        "select", "--db", str(db), "--txn", "100", "--clean", "1",
        "--out", str(tmp_path), "--name", "invalid",
    ]) == 2


def test_bad_header_fails_loudly(tmp_path):
    (tmp_path / "HI-Small_Trans.csv").write_text("a,b,c\n1,2,3\n")
    (tmp_path / "HI-Small_accounts.csv").write_text("x\n")
    assert A.main([
        "build", "--source-dir", str(tmp_path), "--db", str(tmp_path / "d.sqlite")
    ]) == 2
