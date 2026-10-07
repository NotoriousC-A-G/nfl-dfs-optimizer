"""Pure logic for the Sunday-morning availability check (`scripts/sunday_availability_check.py`).

Inactives land ~90 minutes before kickoff, after lineups were built Friday. This module takes each
lineup's players plus the *current* status readings from every source (DraftKings' live `status`,
the official NFL injury report, RotoGrinders' Situation Room, and Chris's overrides), flags every
rostered player who is at risk, and proposes same-slot swap candidates. It never decides a swap --
it surfaces what changed and the cleanest options; Chris decides (and must confirm the replacement's
game has not locked yet -- kickoff times are not modelled here).

Severity: 3 = out (IR/OUT/Out/O/BARRED), 2 = doubtful, 1 = questionable (or no game status yet but
did not practice). A player with severity >= 1 from ANY source is flagged -- the check is
deliberately risk-averse, matching the Questionable rule (ADR-0045).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from nfl_dfs.ingestion.official_injury_report import OfficialInjuryReportEntry

SALARY_CAP = 50_000
FLEX_ELIGIBLE = ("RB", "WR", "TE")


@dataclass(frozen=True)
class StatusReading:
    source: str  # "DK" | "official" | "RotoGrinders" | "override"
    label: str
    severity: int  # 1..3


def classify_dk(status: str | None) -> StatusReading | None:
    if not status or status == "None":
        return None
    sev = {"IR": 3, "OUT": 3, "BARRED": 3, "D": 2, "Q": 1, "Q_CLEARED": 1}.get(status)
    return StatusReading("DK", status, sev) if sev else None


def classify_official(entry: OfficialInjuryReportEntry | None) -> StatusReading | None:
    if entry is None:
        return None
    game = (entry.report_status or "").strip().lower()
    practice = entry.practice_status or ""
    detail = f" (practice: {practice.replace(' in Practice', '').replace(' In Practice', '')})" if practice else ""
    if game == "out":
        return StatusReading("official", f"Out{detail}", 3)
    if game == "doubtful":
        return StatusReading("official", f"Doubtful{detail}", 2)
    if game == "questionable":
        return StatusReading("official", f"Questionable{detail}", 1)
    if not game and practice == "Did Not Participate In Practice":
        return StatusReading("official", f"no game status yet{detail}", 1)
    return None


def classify_rotogrinders(status: str | None) -> StatusReading | None:
    sev = {"O": 3, "D": 2, "Q": 1}.get((status or "").strip().upper())
    return StatusReading("RotoGrinders", status.strip().upper(), sev) if sev else None


def classify_override(decision: str | None, note: str = "") -> StatusReading | None:
    if decision == "bar":
        return StatusReading("override", "BARRED by Chris" + (f" -- {note}" if note else ""), 3)
    return None


@dataclass(frozen=True)
class PlayerCheck:
    name: str
    team: str
    position: str
    slot: str  # DK roster-slot label: QB, RB, WR, TE, FLEX, DST
    salary: int
    readings: tuple[StatusReading, ...]

    @property
    def severity(self) -> int:
        return max((r.severity for r in self.readings), default=0)

    @property
    def flagged(self) -> bool:
        return self.severity >= 1


@dataclass(frozen=True)
class SwapCandidate:
    name: str
    team: str
    position: str
    salary: int
    projection: float | None
    ownership: float | None
    slate_window: str | None


@dataclass(frozen=True)
class LineupCheck:
    label: str
    players: tuple[PlayerCheck, ...]
    salary_used: int
    swaps: dict[str, tuple[SwapCandidate, ...]] = field(default_factory=dict)  # flagged player name -> options

    @property
    def flagged(self) -> tuple[PlayerCheck, ...]:
        return tuple(p for p in self.players if p.flagged)


ReadingsFor = Callable[[str, str, str | None], list[StatusReading]]  # (name, team, canonical_id) -> readings


def check_lineup(
    label: str, players: list[dict], readings_for: ReadingsFor, pool: list[dict], *, max_swaps: int = 5
) -> LineupCheck:
    """`players`: dicts with name/team/position/slot/salary/canonical_id. `pool`: slate snapshot
    `player_pool` rows (identity, team, position, salary, projection, ownership, slate_window)."""
    checks = []
    for p in players:
        readings = () if p["slot"] == "DST" else tuple(readings_for(p["name"], p["team"], p.get("canonical_id")))
        checks.append(PlayerCheck(p["name"], p["team"], p["position"], p["slot"], int(p["salary"]), readings))
    used = sum(c.salary for c in checks)
    in_lineup = {(c.name, c.team) for c in checks}
    swaps: dict[str, tuple[SwapCandidate, ...]] = {}
    for c in checks:
        if c.flagged and c.slot != "DST":
            swaps[c.name] = _swap_candidates(c, used, in_lineup, readings_for, pool, max_swaps)
    return LineupCheck(label, tuple(checks), used, swaps)


def _swap_candidates(
    flagged: PlayerCheck, salary_used: int, in_lineup: set[tuple[str, str]], readings_for: ReadingsFor,
    pool: list[dict], n: int,
) -> tuple[SwapCandidate, ...]:
    allowed_positions = FLEX_ELIGIBLE if flagged.slot == "FLEX" else (flagged.position,)
    budget = flagged.salary + (SALARY_CAP - salary_used)
    out: list[SwapCandidate] = []
    for row in pool:
        ident = row.get("identity") or {}
        name, team, pos = ident.get("display_name"), row.get("team"), row.get("position")
        sal = row.get("salary")
        if not name or pos not in allowed_positions or sal is None or sal > budget or (name, team) in in_lineup:
            continue
        if row.get("projection") is None:
            continue
        if readings_for(name, team, ident.get("canonical_id")):  # any flag at all -> not a clean option
            continue
        own = (row.get("ownership") or {}).get("projected_ownership")
        out.append(SwapCandidate(name, team, pos, int(sal), float(row["projection"]), own, row.get("slate_window")))
    out.sort(key=lambda c: (c.projection or 0.0), reverse=True)
    return tuple(out[:n])
