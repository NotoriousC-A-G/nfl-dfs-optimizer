"""One-off/repeatable: fills `entry_fee` on existing contest_results.csv rows from a DraftKings
"contest entry history" CSV export (Account > Contest History > Export).

Matches each row on (contest name, rank == Place, fpts == Points) -- same real entry on both
sides, so the match is exact. Rows with no match keep a blank fee (never guessed); they're
listed at the end. Only rows for SEASON are touched.

    PYTHONPATH=. .venv/bin/python scripts/backfill_contest_entry_fees.py "<path to export csv>"
"""

from __future__ import annotations

import csv
import dataclasses
import re
import sys
from pathlib import Path

from nfl_dfs.storage.contest_results_store import CONTEST_RESULTS_PATH, read_contest_results, write_contest_rows

SEASON = 2026


def _money(text: str) -> float:
    return float(text.replace("$", "").replace(",", ""))


def _norm(name: str) -> str:
    name = re.sub(r"\s*\(\d+/\d+\)\s*$", "", name.strip())
    name = re.sub(r"^NFL\s+", "", name)
    name = re.sub(r",[^\]]*\]", "]", name)  # DK appends e.g. ", $100K Leaderboard Eligible" inside the brackets
    return name.casefold()


def main(export_path: str) -> None:
    fees: dict[tuple[str, int, float], float] = {}
    with open(export_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["Sport"] != "NFL" or not r["Contest_Date_EST"].startswith("2026"):
                continue
            fees[(_norm(r["Entry"]), int(r["Place"]), float(r["Points"]))] = _money(r["Entry_Fee"])

    rows = read_contest_results()
    out, unmatched, filled = [], [], 0
    for row in rows:
        if row.season == SEASON and row.entry_fee is None:
            fee = fees.get((_norm(row.contest_name), row.rank, row.fpts))
            if fee is None:
                unmatched.append(row)
            else:
                row = dataclasses.replace(row, entry_fee=fee)
                filled += 1
        out.append(row)
    write_contest_rows(Path(CONTEST_RESULTS_PATH), out)

    print(f"filled {filled} rows; {len(unmatched)} unmatched")
    for row in unmatched:
        print(f"  unmatched: wk{row.week} {row.lineup_label} {row.contest_name} rank={row.rank} fpts={row.fpts}")
    for wk in sorted({r.week for r in out if r.season == SEASON}):
        wr = [r for r in out if r.season == SEASON and r.week == wk and r.entry_fee is not None]
        fee_total, won = sum(r.entry_fee for r in wr), sum(r.winnings for r in wr)
        print(f"  week {wk}: {len(wr)} entries, fees ${fee_total:.2f}, won ${won:.2f}, net ${won - fee_total:+.2f}")


if __name__ == "__main__":
    main(sys.argv[1])
