#!/usr/bin/env python3
"""
Makes a SYNTHETIC stand-in for the IBM HI-Small files, in the exact same file format,
so aml_store.py can be tested without the real 5M-row CSV. Not real data and not
statistically like IBM - it only reproduces the *shape* and the awkward details:

  * bank ids zero-padded differently in the transaction file vs the account file
  * the same account number reused at different banks
  * pattern annotations that cover only part of the laundering rows
  * pattern lines with no transaction id (must be matched back by content)
  * exact-duplicate rows (one is label 1, its twin is label 0) to test ambiguity handling
"""
import argparse
import csv
import random
from pathlib import Path

CURRENCIES = ["US Dollar", "Euro", "Yuan", "Shekel", "Rupee", "Yen", "Swiss Franc", "Canadian Dollar"]
FORMATS = ["ACH", "Cheque", "Credit Card", "Wire", "Cash", "Reinvestment", "Bitcoin"]
TYPES = ["FAN-OUT", "FAN-IN", "CYCLE", "STACK", "BIPARTITE", "GATHER-SCATTER", "SCATTER-GATHER", "RANDOM"]


def make(out: Path, n_txn=20000, n_accounts=3000, n_banks=60, n_attempts=40, unannotated=25, seed=7,
         variant="HI-Small"):
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    banks = rng.sample(range(1, 5000), n_banks)
    # accounts: account numbers are deliberately reused across banks
    pool = [f"80{rng.randrange(16**7):07X}0"[:9] for _ in range(int(n_accounts * 0.85))]
    accounts = set()
    while len(accounts) < n_accounts:
        accounts.add((rng.choice(banks), rng.choice(pool)))
    accounts = sorted(accounts)
    with open(out / f"{variant}_accounts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Bank Name", "Bank ID", "Account Number", "Entity ID", "Entity Name"])
        for b, a in accounts:
            w.writerow([f"Bank #{b}", str(b), a, f"E{abs(hash((b, a))) % 10**6:06d}", f"Entity {a[-4:]}"])

    def bank_txt(b):                      # zero-padding varies, like the real transaction file
        return f"{b:0{len(str(b)) + rng.choice([0, 0, 1, 2])}d}"

    def amt():
        return f"{rng.lognormvariate(8, 1.6):.2f}"

    def ts(day, minute):
        return f"2022/09/{day:02d} {minute // 60 % 24:02d}:{minute % 60:02d}"

    rows = []   # (ts, from_bank_n, from_acct, to_bank_n, to_acct, amt, cur, fmt, label)
    hubs = rng.sample(accounts, 15)
    for _ in range(n_txn):
        s = rng.choice(hubs) if rng.random() < 0.15 else rng.choice(accounts)
        r = rng.choice(hubs) if rng.random() < 0.10 else rng.choice(accounts)
        c1 = rng.choice(CURRENCIES)
        c2 = c1 if rng.random() < 0.9 else rng.choice(CURRENCIES)
        a1 = amt()
        a2 = a1 if c1 == c2 else f"{float(a1) * rng.uniform(0.5, 1.5):.2f}"
        rows.append((ts(rng.randint(1, 18), rng.randrange(1440)), s[0], s[1], r[0], r[1], a2, c2, a1, c1,
                     rng.choice(FORMATS[:5]), 0))

    attempts = []
    for _ in range(n_attempts):
        t = rng.choice(TYPES)
        k = rng.randint(4, 14)
        nodes = rng.sample(accounts, k + 1)
        if t == "FAN-OUT":
            edges = [(nodes[0], x) for x in nodes[1:]]
        elif t == "FAN-IN":
            edges = [(x, nodes[0]) for x in nodes[1:]]
        elif t == "CYCLE":
            edges = [(nodes[i], nodes[(i + 1) % len(nodes)]) for i in range(len(nodes))]
        elif t == "BIPARTITE":
            half = len(nodes) // 2
            edges = [(a, b) for a in nodes[:half] for b in nodes[half:half + 2]]
        else:
            edges = [(nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)]
        day, minute = rng.randint(1, 14), rng.randrange(1440)
        lines = []
        for s, r in edges:
            minute += rng.randint(1, 300)
            day += minute // 1440
            minute %= 1440
            c = rng.choice(CURRENCIES)
            a = amt()
            row = (ts(min(day, 18), minute), s[0], s[1], r[0], r[1], a, c, a, c, "ACH", 1)
            rows.append(row)
            lines.append(row)
        attempts.append((t, lines))
    for _ in range(unannotated):          # laundering rows that no pattern file mentions
        s, r = rng.sample(accounts, 2)
        a = amt()
        rows.append((ts(rng.randint(1, 18), rng.randrange(1440)), s[0], s[1], r[0], r[1], a, "Euro", a, "Euro",
                     "Wire", 1))
    # ambiguity trap: a clean twin that is identical to the first pattern line
    twin = attempts[0][1][0]
    rows.append(twin[:-1] + (0,))

    rows.sort(key=lambda r: r[0])        # source file is time-ordered; row number = ordinal
    with open(out / f"{variant}_Trans.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp", "From Bank", "Account", "To Bank", "Account", "Amount Received",
                    "Receiving Currency", "Amount Paid", "Payment Currency", "Payment Format", "Is Laundering"])
        for (t_, fb, fa, tb, ta, ar, rc, ap, pc, fmt, lab) in rows:
            w.writerow([t_, bank_txt(fb), fa, bank_txt(tb), ta, ar, rc, ap, pc, fmt, lab])
    with open(out / f"{variant}_Patterns.txt", "w", encoding="utf-8") as f:
        for t, lines in attempts:
            f.write(f"BEGIN LAUNDERING ATTEMPT - {t}:  synthetic\n")
            for (t_, fb, fa, tb, ta, ar, rc, ap, pc, fmt, lab) in lines:
                f.write(",".join([t_, bank_txt(fb), fa, bank_txt(tb), ta, ar, rc, ap, pc, fmt, "1"]) + "\n")
            f.write(f"END LAUNDERING ATTEMPT - {t}\n\n")
    return dict(rows=len(rows), positives=sum(r[-1] for r in rows), attempts=len(attempts),
                pattern_lines=sum(len(l) for _, l in attempts))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-txn", type=int, default=20000)
    ap.add_argument("--accounts", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    print(make(Path(a.out), n_txn=a.n_txn, n_accounts=a.accounts, seed=a.seed))
