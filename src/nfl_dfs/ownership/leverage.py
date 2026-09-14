"""Stage 7: ownership/leverage layer (PRD Section 5 step 7, ADR-0026).

"Use RotoGrinders ownership projections to flag high-owned chalk and identify leverage spots" --
joins this week's live LineupHQ projected ownership (`ingestion/rotogrinders.py`'s
`fetch_rotogrinders_ownership`/`parse_projected_ownership`, POWN, newly extracted this pass) against the
real 2020-2025 field-ownership-by-salary baseline (ADR-0025's `ownership_calibration.py` production
calibration) to ask: is this player's projected ownership normal for a player at this salary/position, or
does it diverge from what a real DK field has historically done at that price tier.

**Chalk and leverage are both defined relative to this week's own slate, never a fixed absolute percentage**
(Chris's explicit stated convention this session: ownership/uniqueness thresholds are "always relative to
slate size and circumstances," not capped) -- `is_chalk` is a percentile rank within this slate's own
position pool, and the leverage deviation cutoff is the bottom quartile of *this slate's own* distribution
of (projected - historical-baseline) ownership among comparably-priced players, not a hardcoded point gap.

Deliberately narrow scope for this pass: flags real over/under-ownership-vs-price-history, the concrete
signal Section 5 step 7 asks for. It does not attempt game-script/matchup-aware leverage (that's
`MatchupContext`'s job, already built) or dup-risk modeling (needs the still-uningested ResultsDB `lineups/`
endpoint, ADR-0023's own explicit follow-up) -- this module answers "is the field pricing this player
correctly relative to history," not "will this specific lineup be unique."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from nfl_dfs.analysis.ownership_calibration import CORE_POSITIONS, N_DECILES, ProductionCalibration
from nfl_dfs.ingestion.rotogrinders import LineupHqOwnershipRow

# Relative thresholds only -- no fixed absolute ownership-percentage cap (see module docstring).
CHALK_OWNERSHIP_PERCENTILE = 0.10
LEVERAGE_SALARY_DECILE_MAX = 4
LEVERAGE_DEVIATION_PERCENTILE = 0.25
_MIN_DEVIATION_POOL_SIZE = 4


@dataclass(frozen=True)
class LeverageAssessment:
    """One player's chalk/leverage read for this week's slate."""

    native_id: str
    name: str
    position: str
    team: str | None
    salary: int
    salary_decile: int
    projected_ownership: float
    ownership_percentile: float
    baseline_ownership: float | None
    ownership_vs_baseline: float | None
    is_chalk: bool
    is_leverage: bool
    note: str


def _salary_deciles(rows: Sequence[LineupHqOwnershipRow]) -> dict[str, int]:
    by_position: dict[str, list[LineupHqOwnershipRow]] = {}
    for row in rows:
        by_position.setdefault(row.position, []).append(row)

    deciles: dict[str, int] = {}
    for position_rows in by_position.values():
        ordered = sorted(position_rows, key=lambda row: row.salary, reverse=True)
        n = len(ordered)
        for rank, row in enumerate(ordered):
            deciles[row.native_id] = min(N_DECILES - 1, int(rank / n * N_DECILES))
    return deciles


def _ownership_percentiles(rows: Sequence[LineupHqOwnershipRow]) -> dict[str, float]:
    """0.0 = the most-owned player at that position this slate, 1.0 = the least."""
    by_position: dict[str, list[LineupHqOwnershipRow]] = {}
    for row in rows:
        by_position.setdefault(row.position, []).append(row)

    percentiles: dict[str, float] = {}
    for position_rows in by_position.values():
        ordered = sorted(position_rows, key=lambda row: row.projected_ownership, reverse=True)
        n = len(ordered)
        for rank, row in enumerate(ordered):
            percentiles[row.native_id] = rank / (n - 1) if n > 1 else 0.0
    return percentiles


def _note(row: LineupHqOwnershipRow, decile: int, baseline: float | None, deviation: float | None, is_chalk: bool, is_leverage: bool) -> str:
    if is_chalk:
        return (
            f"Top decile of {row.position} ownership on this slate ({row.projected_ownership:.1f}% projected) "
            "-- real chalk to build around or pointedly fade."
        )
    if is_leverage and baseline is not None and deviation is not None:
        return (
            f"Priced in the top half of {row.position} salaries this slate (decile {decile}) but projected at "
            f"{row.projected_ownership:.1f}% vs a {baseline:.1f}% historical field-ownership baseline for that "
            f"price tier ({deviation:+.1f}pt) -- among the slate's most underowned relative to price."
        )
    return ""


def build_leverage_assessments(
    rows: Sequence[LineupHqOwnershipRow],
    production: dict[str, ProductionCalibration],
) -> list[LeverageAssessment]:
    """Builds one `LeverageAssessment` per core-position player in `rows` (this week's live LineupHQ pull),
    joined against `production` (ADR-0025's `run_full_calibration().production`, the historical
    field-ownership-by-salary-decile baseline). Non-core positions (K, and the raw-payload position-tagging
    noise ADR-0025 already excludes) are dropped, matching `ownership_calibration.py`'s own scope.
    """
    core_rows = [row for row in rows if row.position in CORE_POSITIONS]
    if not core_rows:
        return []

    salary_deciles = _salary_deciles(core_rows)
    ownership_percentiles = _ownership_percentiles(core_rows)

    interim = []
    for row in core_rows:
        decile = salary_deciles[row.native_id]
        percentile = ownership_percentiles[row.native_id]
        calibration = production.get(row.position)
        baseline = calibration.decile_ownership.get(decile) if calibration else None
        deviation = row.projected_ownership - baseline if baseline is not None else None
        is_chalk = percentile <= CHALK_OWNERSHIP_PERCENTILE
        interim.append((row, decile, percentile, baseline, deviation, is_chalk))

    # The leverage deviation cutoff is the bottom quartile of THIS slate's own deviation distribution among
    # comparably-priced, non-chalk players at that position -- never a fixed point-gap constant.
    deviation_pool: dict[str, list[float]] = {}
    for row, decile, _percentile, _baseline, deviation, is_chalk in interim:
        if deviation is not None and decile <= LEVERAGE_SALARY_DECILE_MAX and not is_chalk:
            deviation_pool.setdefault(row.position, []).append(deviation)

    leverage_cutoff: dict[str, float] = {}
    for position, deviations in deviation_pool.items():
        if len(deviations) >= _MIN_DEVIATION_POOL_SIZE:
            ordered = sorted(deviations)
            index = max(0, int(len(ordered) * LEVERAGE_DEVIATION_PERCENTILE) - 1)
            leverage_cutoff[position] = ordered[index]

    assessments: list[LeverageAssessment] = []
    for row, decile, percentile, baseline, deviation, is_chalk in interim:
        is_leverage = (
            not is_chalk
            and decile <= LEVERAGE_SALARY_DECILE_MAX
            and deviation is not None
            and row.position in leverage_cutoff
            and deviation <= leverage_cutoff[row.position]
        )
        assessments.append(
            LeverageAssessment(
                native_id=row.native_id,
                name=row.name,
                position=row.position,
                team=row.team,
                salary=row.salary,
                salary_decile=decile,
                projected_ownership=row.projected_ownership,
                ownership_percentile=percentile,
                baseline_ownership=baseline,
                ownership_vs_baseline=deviation,
                is_chalk=is_chalk,
                is_leverage=is_leverage,
                note=_note(row, decile, baseline, deviation, is_chalk, is_leverage),
            )
        )
    return assessments
