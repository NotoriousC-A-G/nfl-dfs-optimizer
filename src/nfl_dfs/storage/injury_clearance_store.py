"""Hand-kept list of Questionable players cleared to be rostered this week.

**Why this exists (Chris, 2026-10-07):** lineups are built before final inactives are known, so a
Questionable (Q) player is assumed NOT to play -- `optimizer.lineup.EXCLUDED_INJURY_STATUSES`
contains "Q" -- unless there is real evidence he practiced Friday. This file is where that
evidence is recorded; a Q player with a row here is given the non-excluded `Q_CLEARED` status
(`normalization.injury_lookup.apply_questionable_clearances`), anyone else stays out. A player
who is "going to test it out pre-game" / a game-time decision is deliberately NOT clearable here
-- that is an avoid, so the right action is to leave him off the list.

**Evidence rule, enforced at write/read time, not left to discipline:** `practice` must be `Full`
or `Limited`. `Limited` additionally requires a non-empty `note` (the "positive report" that makes
a limited practice enough -- who said it, what they said). `Full` may carry a note but doesn't need
one. A `Limited` row with no note is rejected, so a limited practice can never clear a player on its
own.

**No live source feeds this yet** -- the official nflverse report lags and RotoGrinders' "PART"
column is the body part, not practice participation (ADR-0031), so the Friday status is entered by
hand (`scripts/log_q_clearance.py`). A scraper that pre-fills it is a follow-up.

CSV at `data/overrides/q_clearances.csv`; append-only and idempotent on `(season, week, name, team)`
-- re-logging the same player the same week replaces nothing and is reported as already present.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
CLEARANCES_PATH = _REPO_ROOT / "data" / "overrides" / "q_clearances.csv"

FIELDNAMES = ["season", "week", "name", "team", "practice", "note"]
VALID_PRACTICE = ("Full", "Limited")


@dataclass(frozen=True)
class QuestionableClearance:
    season: int
    week: int
    name: str
    team: str
    practice: str  # "Full" | "Limited"
    note: str = ""

    def __post_init__(self) -> None:
        if self.practice not in VALID_PRACTICE:
            raise ValueError(f"{self.name}: practice must be one of {VALID_PRACTICE}, got {self.practice!r}")
        if self.practice == "Limited" and not self.note.strip():
            raise ValueError(
                f"{self.name}: a Limited practice only clears a player with a positive report -- "
                "add a note saying who reported what (otherwise leave him excluded)"
            )
        if not self.name.strip() or not self.team.strip():
            raise ValueError("name and team are required")


def read_clearances(*, season: int | None = None, week: int | None = None, path: Path | None = None) -> list[QuestionableClearance]:
    path = path or CLEARANCES_PATH
    if not path.exists():
        return []
    rows: list[QuestionableClearance] = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            c = QuestionableClearance(
                season=int(r["season"]), week=int(r["week"]), name=r["name"], team=r["team"].upper(),
                practice=r["practice"], note=r.get("note", "") or "",
            )
            if (season is None or c.season == season) and (week is None or c.week == week):
                rows.append(c)
    return rows


def save_clearances(rows: list[QuestionableClearance], *, path: Path | None = None) -> list[QuestionableClearance]:
    """Appends rows not already present (idempotent on season/week/name/team); returns what was
    actually written."""
    path = path or CLEARANCES_PATH
    existing = {(c.season, c.week, c.name.lower(), c.team) for c in read_clearances(path=path)}
    new = []
    for row in rows:
        key = (row.season, row.week, row.name.lower(), row.team.upper())
        if key not in existing:
            existing.add(key)
            new.append(row)
    if not new:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(FIELDNAMES)
        for c in new:
            w.writerow([c.season, c.week, c.name, c.team.upper(), c.practice, c.note])
    return new
