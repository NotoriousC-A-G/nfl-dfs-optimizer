"""Chris's manual overrides to the automatic Questionable-player decisions.

**How Questionable (Q) players are handled (Chris, 2026-10-07):** lineups are generated before final
inactives, so a Q player is assumed NOT to play unless there is evidence he practiced Friday. The
*system* makes that call from the official NFL practice report
(`normalization.injury_lookup.resolve_questionable_players`: Full participation clears a Q player;
Limited, Did Not Participate or no evidence leaves him out) and prints every decision with its
basis. **This file is where Chris overrides the system** when he has additional information or
disagrees -- in either direction:

- `clear`: roster this player. Allowed on a Q or Doubtful player (never on OUT/IR, a guaranteed zero).
- `bar`: do not roster this player, whatever his status says -- allowed on anyone.

A "will test it out pre-game" / game-time-decision player is an avoid by default, so he needs no
row; add a `clear` row only if you have information that changes that. `note` is optional but is
echoed in the decision printout and saved with the run, so the post-mortem can see which calls were
overrides and why.

CSV at `data/overrides/q_overrides.csv`; append-only, idempotent on `(season, week, name, team)`
-- re-logging the same player the same week is reported as already present (edit the file by hand
to change a decision).
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
OVERRIDES_PATH = _REPO_ROOT / "data" / "overrides" / "q_overrides.csv"

FIELDNAMES = ["season", "week", "name", "team", "decision", "note"]
VALID_DECISIONS = ("clear", "bar")


@dataclass(frozen=True)
class QuestionableOverride:
    season: int
    week: int
    name: str
    team: str
    decision: str  # "clear" | "bar"
    note: str = ""

    def __post_init__(self) -> None:
        if self.decision not in VALID_DECISIONS:
            raise ValueError(f"{self.name}: decision must be one of {VALID_DECISIONS}, got {self.decision!r}")
        if not self.name.strip() or not self.team.strip():
            raise ValueError("name and team are required")


def read_overrides(*, season: int | None = None, week: int | None = None, path: Path | None = None) -> list[QuestionableOverride]:
    path = path or OVERRIDES_PATH
    if not path.exists():
        return []
    rows: list[QuestionableOverride] = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            o = QuestionableOverride(
                season=int(r["season"]), week=int(r["week"]), name=r["name"], team=r["team"].upper(),
                decision=r["decision"], note=r.get("note", "") or "",
            )
            if (season is None or o.season == season) and (week is None or o.week == week):
                rows.append(o)
    return rows


def save_overrides(rows: list[QuestionableOverride], *, path: Path | None = None) -> list[QuestionableOverride]:
    """Appends rows not already present (idempotent on season/week/name/team); returns what was
    actually written."""
    path = path or OVERRIDES_PATH
    existing = {(o.season, o.week, o.name.lower(), o.team) for o in read_overrides(path=path)}
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
        for o in new:
            w.writerow([o.season, o.week, o.name, o.team.upper(), o.decision, o.note])
    return new
