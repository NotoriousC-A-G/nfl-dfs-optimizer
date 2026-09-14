"""Shared math for `MatchupContext`'s unit-vs-unit rows (PRD Section 6): run game, pass
protection, and coverage all use the identical "z-score differential between two grades, mapped
to a capped multiplier (0.85x-1.15x)" pattern -- factored out here once rather than reimplemented
per row, the same way `game_environment_stats.py` factored out pace/PROE/implied-total's shared
z-score math.

**Population z-scoring reuses `game_environment_stats.cross_sectional_zscore` directly** (ADR-0003's
own population z-score, already built and already the pipeline's one implementation of "z-score a
cross-section of values, treating fewer than 2 distinct values as an undefined `NaN`, never a
silently-wrong 0.0") -- not reimplemented a second time here, per the task's instruction to reuse
an established pattern.

**JUDGMENT CALL, flagged the same way `game_environment/score.py` flags its own two formula-behavior
decisions (`_phi`, the missing-implied-total branch) -- not settled spec:** the PRD specifies the
multiplier *cap* (0.85x-1.15x) for run game/pass protection/coverage, and ADR-0005 specifies the
*combination* cap (+-20%) for stacked pass-protection/coverage multipliers, but neither specifies
how an unbounded z-score differential maps onto the 0.85x-1.15x range in the first place. This
module reuses the identical standard-normal-CDF mapping already established for
`GameEnvironmentScore`'s `_phi` (z=0 -> exactly neutral, smooth, bounded, consistent with this
project's general statistical-rigor posture) rather than inventing a second mapping convention for
`MatchupContext` or falling back to an arbitrary linear clip. Not yet backtested or signed off --
same status as every other Section 6 formula-behavior choice made without a fully-specified PRD
answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

import pandas as pd

from nfl_dfs.ingestion.game_environment_stats import cross_sectional_zscore

# PRD Section 6: run game / pass protection / coverage all cap their z-score-derived multiplier at
# 0.85x-1.15x.
MULTIPLIER_CAP = 0.15

# ADR-0005: the combined deviation cap when pass-protection's flow-through multiplier and a
# receiver's own coverage multiplier both apply to the same pass-catcher.
PASS_PROTECTION_COVERAGE_COMBINED_CAP = 0.20


def population_zscore(value: float | None, population: Iterable[float]) -> float | None:
    """Scalar z-score of `value` against `population` (which should include `value` itself, the
    same "the input *is* the full population" contract `cross_sectional_zscore` documents) via
    that exact function -- not a separate mean/std computation. `None` when `value` is `None`, or
    when the population has fewer than 2 distinct values (an undefined z-score, per
    `cross_sectional_zscore`'s own `NaN` contract -- translated to `None` here to match this
    module's "no data -> `None`, never a guessed number" convention).
    """
    if value is None:
        return None
    series = pd.Series(list(population), dtype=float)
    matches = series.index[series == value]
    if len(matches) > 0:
        z = cross_sectional_zscore(series)
        result = z.loc[matches[0]]
    else:
        # `value` wasn't literally a member of `population` (e.g. it was computed from a slightly
        # different row set than the population list) -- compute its z-score directly against the
        # same population mean/std cross_sectional_zscore would use, rather than requiring exact
        # membership.
        if len(series) < 2:
            return None
        std = series.std(ddof=0)
        if std == 0 or pd.isna(std):
            return None
        result = (value - series.mean()) / std
    return None if pd.isna(result) else float(result)


def zscore_diff_to_multiplier(z_diff: float | None, cap: float = MULTIPLIER_CAP) -> float | None:
    """Standard-normal-CDF mapping from an unbounded z-score differential to a `[1-cap, 1+cap]`
    multiplier -- see module docstring's judgment-call note. `z_diff=0` -> exactly `1.0` (neutral).

    `None` in, `None` out -- never silently neutral. A caller applying this multiplier downstream
    must treat `None` as "not computable this week" (skip the adjustment entirely), not as "no
    effect" -- those are different things (see `projection/blend.py`'s existing
    `apply_rb_blowout_volume_discount` for the same "skip on missing data, never guess" posture
    this module's callers should follow).
    """
    if z_diff is None:
        return None
    phi = 0.5 * (1.0 + math.erf(z_diff / math.sqrt(2.0)))
    return (1 - cap) + 2 * cap * phi


class _GradedRow(Protocol):
    team: str | None
    grades: dict[str, float]


def snap_weighted_grade(
    rows: Sequence[_GradedRow], grade_field: str, snap_field: str | None
) -> tuple[float | None, str]:
    """`Sigma(grade_i * snaps_i) / Sigma(snaps_i)` across `rows` (anything carrying a `.grades:
    dict[str, float]`, e.g. `pff.py`'s `PffGradeRow`) -- ADR-0010's snap-weighted team-grade
    formula, specified there for the coverage fallback ("otherwise a low-snap rotational defender
    would pull the team number as much as a CB1 playing 90% of snaps") and reused here for run-
    block/pass-block/pass-rush team aggregation on the identical reasoning.

    Falls back to an unweighted mean -- same fallback discipline as `pff.py`'s own
    `_league_average_by_position` -- when `snap_field` is `None` or isn't present on every row
    (never invented from a subset). Returns `(None, "no_data")` for an empty/all-missing `rows`.
    Returns `(value, "snap_weighted")` or `(value, "unweighted_mean")` so callers/QA can see which
    path actually produced the number, not just the number itself.
    """
    usable = [r for r in rows if grade_field in r.grades]
    if not usable:
        return None, "no_data"

    if snap_field is not None and all(snap_field in r.grades for r in usable):
        total_snaps = sum(r.grades[snap_field] for r in usable)
        if total_snaps > 0:
            weighted = sum(r.grades[grade_field] * r.grades[snap_field] for r in usable) / total_snaps
            return weighted, "snap_weighted"

    return sum(r.grades[grade_field] for r in usable) / len(usable), "unweighted_mean"


@dataclass(frozen=True)
class TeamAggregate:
    team: str
    value: float
    method: str  # "snap_weighted" or "unweighted_mean" -- see snap_weighted_grade
    n_players: int


def team_aggregate_grades(
    by_player_id: dict, grade_field: str, snap_field: str | None
) -> dict[str, TeamAggregate]:
    """`team -> TeamAggregate` over a whole `PffFacetGrades.by_player_id` pull (or any dict of
    objects carrying `.team`/`.grades`) -- the team-level rollup `MatchupContext`'s run-game/pass-
    protection rows need from PFF's individual-lineman/individual-defender grade facets (PRD
    Section 6: "aggregated to team"). Rows with `team is None` or missing `grade_field` entirely
    are excluded from that team's aggregate rather than treated as a zero-grade contributor.
    """
    rows_by_team: dict[str, list] = {}
    for row in by_player_id.values():
        if row.team is None:
            continue
        rows_by_team.setdefault(row.team, []).append(row)

    result: dict[str, TeamAggregate] = {}
    for team, rows in rows_by_team.items():
        value, method = snap_weighted_grade(rows, grade_field, snap_field)
        if value is not None:
            result[team] = TeamAggregate(team=team, value=value, method=method, n_players=len(rows))
    return result
