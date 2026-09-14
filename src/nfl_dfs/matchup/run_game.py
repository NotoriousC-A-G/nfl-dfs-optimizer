"""`MatchupContext`'s Run game row (PRD Section 6): z-score differential between a team's PFF
run-block grade and the opponent's run-defense grade, mapped to a capped multiplier applied to the
RB's blended efficiency.

Both grade facets (`offense/run_blocking` -> `grades_run_block`, `defense/run` ->
`grades_run_defense`) are individual-lineman/individual-defender grades (ADR-0014, already
ingested by `pff.py`'s `GRADE_FACETS`) -- PRD Section 6 explicitly calls for these "aggregated to
team," which `grading.team_aggregate_grades` does via ADR-0010's snap-weighted formula.

**Snap-count field names, flagged as unconfirmed:** PFF's exact per-lineman/per-defender snap-count
field name for these two facets (`SNAP_FIELD` below) has not been live-confirmed the way
`grades_run_block`/`grades_run_defense` themselves are (PRD Section 6 names those two explicitly).
`grading.snap_weighted_grade` falls back to an unweighted mean automatically when the named field
isn't present on every row, so an incorrect guess here degrades gracefully to a slightly less
precise aggregate rather than crashing or silently mis-weighting -- but the live integration check
should confirm/correct these field names against a real payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.matchup.grading import MULTIPLIER_CAP, TeamAggregate, population_zscore, zscore_diff_to_multiplier

RUN_BLOCK_GRADE_FIELD = "grades_run_block"
RUN_BLOCK_SNAP_FIELD = "snap_counts_run_block"  # best-guess field name -- see module docstring
RUN_DEFENSE_GRADE_FIELD = "grades_run_defense"
RUN_DEFENSE_SNAP_FIELD = "snap_counts_run_defense"  # best-guess field name -- see module docstring


@dataclass(frozen=True)
class RunGameMultiplier:
    """One offense-team-vs-defense-team run-game matchup. `multiplier=None` (with `reason` set)
    means either team's aggregated grade wasn't computable this week -- callers must skip the
    adjustment entirely in that case (PRD Section 6's own ground rule: "if the data doesn't
    support an adjustment, no adjustment is made"), never substitute a neutral 1.0 that would look
    identical to a genuinely-computed neutral result.
    """

    offense_team: str
    defense_team: str
    multiplier: float | None
    offense_grade: float | None
    defense_grade: float | None
    offense_z: float | None
    defense_z: float | None
    reason: str | None = None


def compute_run_game_multiplier(
    offense_team: str,
    defense_team: str,
    offense_aggregates: dict[str, TeamAggregate],
    defense_aggregates: dict[str, TeamAggregate],
    cap: float = MULTIPLIER_CAP,
) -> RunGameMultiplier:
    """`offense_aggregates`/`defense_aggregates` are whole-league team->grade rollups (see
    `grading.team_aggregate_grades`, called once per week against the `offense/run_blocking`/
    `defense/run` facet pulls) -- passed as full dicts, not just the two teams in this matchup, so
    each team's z-score is computed against the real cross-sectional population (ADR-0003's
    population-z convention), not a meaningless two-team comparison.
    """
    offense = offense_aggregates.get(offense_team)
    defense = defense_aggregates.get(defense_team)
    if offense is None or defense is None:
        missing = []
        if offense is None:
            missing.append(f"{offense_team} run-block grade")
        if defense is None:
            missing.append(f"{defense_team} run-defense grade")
        return RunGameMultiplier(
            offense_team=offense_team,
            defense_team=defense_team,
            multiplier=None,
            offense_grade=None if offense is None else offense.value,
            defense_grade=None if defense is None else defense.value,
            offense_z=None,
            defense_z=None,
            reason=f"no computable run-game multiplier: missing {', '.join(missing)}.",
        )

    offense_population = [a.value for a in offense_aggregates.values()]
    defense_population = [a.value for a in defense_aggregates.values()]
    offense_z = population_zscore(offense.value, offense_population)
    defense_z = population_zscore(defense.value, defense_population)

    if offense_z is None or defense_z is None:
        return RunGameMultiplier(
            offense_team=offense_team,
            defense_team=defense_team,
            multiplier=None,
            offense_grade=offense.value,
            defense_grade=defense.value,
            offense_z=offense_z,
            defense_z=defense_z,
            reason="run-game z-score undefined: fewer than 2 teams in this week's grade population.",
        )

    # Better run-blocking (higher offense_z) vs. weaker run defense (lower defense_z) both push the
    # multiplier up -- hence offense_z - defense_z, mirroring pass protection's identical sign
    # convention below.
    z_diff = offense_z - defense_z
    multiplier = zscore_diff_to_multiplier(z_diff, cap=cap)

    return RunGameMultiplier(
        offense_team=offense_team,
        defense_team=defense_team,
        multiplier=multiplier,
        offense_grade=offense.value,
        defense_grade=defense.value,
        offense_z=offense_z,
        defense_z=defense_z,
        reason=None,
    )
