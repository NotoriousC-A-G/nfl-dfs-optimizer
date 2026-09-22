"""Real DraftKings contest results for the lineups Chris actually played -- the NFL analog of
MLB's own `PostMortemResultInputs` (entered_score/cashed/winnings/cash_line/rank/total_entries),
except transcribed by hand from DK's GameCenter rather than scraped: DK exposes no results API,
so this data only exists as what Chris reads off the contest page himself (2026-09-22, working
through week 2's real contest history screenshot by screenshot).

**One row per (lineup, contest) pair, not one row per lineup.** A single played lineup (Chris's
own `agent_results.csv` `strategy_name`, e.g. "L1") is routinely entered into several different
real contests -- confirmed live this week: L1 alone went into 4 separate contests. `lineup_label`
join back to `storage/agent_results_store.py`'s `agent_id="operator"` rows via `(season, week,
lineup_label == strategy_name)`; `contest_name` + `rank` distinguish DK's own multi-entry naming
convention ("cgasparro", "cgasparro (2)", "cgasparro (3)", ...) when several of Chris's lineups
share one contest, which is common (a `[N Entry Max]` contest, as opposed to `[Single Entry]`).

**Filesystem/CSV is the source of truth, append-only, idempotent on `(season, week, contest_name,
lineup_label, rank)`** -- same posture as `agent_results_store.py`. `rank` is part of the
idempotency key (not just `contest_name` + `lineup_label`) because DK's own multi-entry naming
doesn't map cleanly onto which of Chris's three lineups is "entry 1" vs "entry 2" from the
contest's own UI alone -- rank is the one value guaranteed unique per real entry within one
contest, so it's the safe de-dup anchor even if `lineup_label` were ever mis-transcribed.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
CONTEST_RESULTS_DIR = _REPO_ROOT / "data" / "agent-performance"
CONTEST_RESULTS_PATH = CONTEST_RESULTS_DIR / "contest_results.csv"

FIELDNAMES = [
    "season",
    "week",
    "lineup_label",  # matches agent_results.csv's strategy_name for agent_id="operator"
    "contest_name",
    "entries",
    "positions_paid",
    "total_prizes",
    "rank",
    "fpts",
    "winnings",
]


@dataclass(frozen=True)
class ContestResult:
    season: int
    week: int
    lineup_label: str
    contest_name: str
    entries: int
    positions_paid: int
    total_prizes: float
    rank: int
    fpts: float
    winnings: float

    @property
    def cashed(self) -> bool:
        return self.winnings > 0


def _row_to_csv_dict(row: ContestResult) -> dict[str, str]:
    return {
        "season": str(row.season),
        "week": str(row.week),
        "lineup_label": row.lineup_label,
        "contest_name": row.contest_name,
        "entries": str(row.entries),
        "positions_paid": str(row.positions_paid),
        "total_prizes": str(row.total_prizes),
        "rank": str(row.rank),
        "fpts": str(row.fpts),
        "winnings": str(row.winnings),
    }


def _csv_dict_to_row(d: dict[str, str]) -> ContestResult:
    return ContestResult(
        season=int(d["season"]),
        week=int(d["week"]),
        lineup_label=d["lineup_label"],
        contest_name=d["contest_name"],
        entries=int(d["entries"]),
        positions_paid=int(d["positions_paid"]),
        total_prizes=float(d["total_prizes"]),
        rank=int(d["rank"]),
        fpts=float(d["fpts"]),
        winnings=float(d["winnings"]),
    )


def save_contest_results(rows: list[ContestResult], *, path: Path | None = None) -> Path | None:
    """Append rows to contest_results.csv. Idempotent on `(season, week, contest_name,
    lineup_label, rank)`. Returns `None` (writes nothing) if `rows` is empty."""
    if not rows:
        return None

    results_path = path if path is not None else CONTEST_RESULTS_PATH
    results_path.parent.mkdir(parents=True, exist_ok=True)

    existing_keys: set[tuple[str, str, str, str, str]] = set()
    if results_path.exists():
        with open(results_path, newline="") as f:
            for existing in csv.DictReader(f):
                existing_keys.add(
                    (
                        existing["season"],
                        existing["week"],
                        existing["contest_name"],
                        existing["lineup_label"],
                        existing["rank"],
                    )
                )

    fresh = [
        row
        for row in rows
        if (str(row.season), str(row.week), row.contest_name, row.lineup_label, str(row.rank)) not in existing_keys
    ]
    if not fresh:
        return results_path

    write_header = not results_path.exists()
    with open(results_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in fresh:
            writer.writerow(_row_to_csv_dict(row))

    return results_path


def read_contest_results(
    *, season: int | None = None, week: int | None = None, path: Path | None = None
) -> list[ContestResult]:
    results_path = path if path is not None else CONTEST_RESULTS_PATH
    if not results_path.exists():
        return []

    with open(results_path, newline="") as f:
        rows = [_csv_dict_to_row(d) for d in csv.DictReader(f)]

    if season is not None:
        rows = [r for r in rows if r.season == season]
    if week is not None:
        rows = [r for r in rows if r.week == week]
    return rows
