"""`MatchupContext`'s Pass protection row (PRD Section 6): z-score differential between a team's
PFF pass-block grade and the opponent's pass-rush win rate, mapped to a capped multiplier -- but
unlike Run game, this multiplier is computed **once per team** and then flows downstream onto
every pass-catcher on that team's projection (PRD's own words: "flows downstream into every
pass-catcher's blended target value"), not just the QB's.

**Flow-through representation:** rather than re-deriving this multiplier per receiver, this module
computes one `PassProtectionMultiplier` per offense team and `context.py` (the orchestrator) reads
the QB's own team's entry for every pass-catcher on that team -- QB, WR, TE, and a pass-catching
RB alike. This is the cleanest way to represent "one team-level signal, several downstream
consumers" without recomputing the same z-score differential once per player: compute once, look
up per player.

Field names: PRD Section 6 explicitly names `grades_pass_block` (offense, individual-lineman,
`offense/pass_blocking`) and "PFF team pass-rush win rate" (defense) as this row's two primary
inputs. ADR-0022's live audit confirmed the defensive side's real field name is
`pass_rush_win_rate` on the `defense/pass_rush` facet (added to `pff.py`'s `GRADE_FACETS` in the
same round that added `receiving/scheme`) -- used here rather than `grades_pass_rush_defense`
specifically because the PRD table itself names "win rate," not "grade," as the input.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.matchup.grading import MULTIPLIER_CAP, TeamAggregate, population_zscore, zscore_diff_to_multiplier

PASS_BLOCK_GRADE_FIELD = "grades_pass_block"
PASS_BLOCK_SNAP_FIELD = "snap_counts_pass_block"  # best-guess field name -- see run_game.py's
# identical caveat; snap_weighted_grade degrades to an unweighted mean if this is wrong/absent.
PASS_RUSH_GRADE_FIELD = "pass_rush_win_rate"
PASS_RUSH_SNAP_FIELD = None  # a win-rate is already a rate, not something to snap-weight further
# on top of -- team_aggregate_grades falls back to an unweighted mean across the defense's
# individual pass-rushers' win rates, which is itself already a reasonable team rollup.


@dataclass(frozen=True)
class PassProtectionMultiplier:
    """One offense-team-vs-defense-team pass-protection matchup -- the QB's own multiplier, and
    (per PRD Section 6) the same value every pass-catcher on `offense_team` inherits this week.
    `multiplier=None` (with `reason` set) means skip the adjustment for every player on this team
    this week -- never a guessed neutral value standing in for a genuinely-computed one.
    """

    offense_team: str
    defense_team: str
    multiplier: float | None
    offense_grade: float | None
    defense_grade: float | None
    offense_z: float | None
    defense_z: float | None
    reason: str | None = None


def compute_pass_protection_multiplier(
    offense_team: str,
    defense_team: str,
    offense_aggregates: dict[str, TeamAggregate],
    defense_aggregates: dict[str, TeamAggregate],
    cap: float = MULTIPLIER_CAP,
) -> PassProtectionMultiplier:
    """Same shape/contract as `run_game.compute_run_game_multiplier` -- see that function's
    docstring for the population-z rationale. `offense_aggregates`/`defense_aggregates` come from
    `grading.team_aggregate_grades` over the `offense/pass_blocking`/`defense/pass_rush` facet
    pulls respectively.
    """
    offense = offense_aggregates.get(offense_team)
    defense = defense_aggregates.get(defense_team)
    if offense is None or defense is None:
        missing = []
        if offense is None:
            missing.append(f"{offense_team} pass-block grade")
        if defense is None:
            missing.append(f"{defense_team} pass-rush win rate")
        return PassProtectionMultiplier(
            offense_team=offense_team,
            defense_team=defense_team,
            multiplier=None,
            offense_grade=None if offense is None else offense.value,
            defense_grade=None if defense is None else defense.value,
            offense_z=None,
            defense_z=None,
            reason=f"no computable pass-protection multiplier: missing {', '.join(missing)}.",
        )

    offense_population = [a.value for a in offense_aggregates.values()]
    defense_population = [a.value for a in defense_aggregates.values()]
    offense_z = population_zscore(offense.value, offense_population)
    defense_z = population_zscore(defense.value, defense_population)

    if offense_z is None or defense_z is None:
        return PassProtectionMultiplier(
            offense_team=offense_team,
            defense_team=defense_team,
            multiplier=None,
            offense_grade=offense.value,
            defense_grade=defense.value,
            offense_z=offense_z,
            defense_z=defense_z,
            reason="pass-protection z-score undefined: fewer than 2 teams in this week's grade population.",
        )

    # Better pass-blocking (higher offense_z) vs. weaker pass rush (lower defense_z, i.e. a lower
    # win rate) both push the multiplier up.
    z_diff = offense_z - defense_z
    multiplier = zscore_diff_to_multiplier(z_diff, cap=cap)

    return PassProtectionMultiplier(
        offense_team=offense_team,
        defense_team=defense_team,
        multiplier=multiplier,
        offense_grade=offense.value,
        defense_grade=defense.value,
        offense_z=offense_z,
        defense_z=defense_z,
        reason=None,
    )
