"""Manual, live-network integration check for `MatchupContext` (PRD Section 6, `src/nfl_dfs/
matchup/`) -- pulls REAL PFF grade facets and the REAL nflverse season schedule, runs the actual
run-game/pass-protection/coverage computation end to end, and reports real computed multipliers
for real teams/players this week. NOT part of `pytest` -- needs live PFF/nflverse network access
and a live `PFF_API_KEY`, not reproducible in CI. Run by hand:

    .venv/bin/python scripts/live_integration_check_matchup.py

Week choice: `WEEK = 2` (season-to-date grades need at least one completed week behind them --
`completed_weeks_param(1)` is `None`, ADR-0014's week-1 prior-season fallback -- so week 2 is the
first week with real current-season `week=1` cumulative grades to differentiate teams).
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl

from nfl_dfs.ingestion.pff import fetch_matchup_grades
from nfl_dfs.matchup.context import MatchupFacetInputs, PlayerMatchupInput, build_matchup_context_pool
from nfl_dfs.matchup.coverage import compute_coverage_multiplier, receiver_man_zone_rate
from nfl_dfs.matchup.grading import team_aggregate_grades
from nfl_dfs.matchup.pass_protection import (
    PASS_BLOCK_GRADE_FIELD,
    PASS_BLOCK_SNAP_FIELD,
    PASS_RUSH_GRADE_FIELD,
    PASS_RUSH_SNAP_FIELD,
    compute_pass_protection_multiplier,
)
from nfl_dfs.matchup.run_game import (
    RUN_BLOCK_GRADE_FIELD,
    RUN_BLOCK_SNAP_FIELD,
    RUN_DEFENSE_GRADE_FIELD,
    RUN_DEFENSE_SNAP_FIELD,
    compute_run_game_multiplier,
)

SEASON = 2026
WEEK = 2


def _fetch(facet: str):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = fetch_matchup_grades(facet, WEEK, SEASON)
    for w in caught:
        print(f"  WARNING ({facet}): {w.message}")
    return result


def main() -> None:
    print(f"=== Live MatchupContext check (season={SEASON}, week={WEEK}) ===\n")

    print("Fetching real PFF grade facets...")
    run_blocking = _fetch("offense/run_blocking")
    run_defense = _fetch("defense/run")
    pass_blocking = _fetch("offense/pass_blocking")
    pass_rush = _fetch("defense/pass_rush")
    coverage_scheme = _fetch("defense/coverage_scheme")
    receiving_scheme = _fetch("receiving/scheme")

    for label, facet in [
        ("offense/run_blocking", run_blocking),
        ("defense/run", run_defense),
        ("offense/pass_blocking", pass_blocking),
        ("defense/pass_rush", pass_rush),
        ("defense/coverage_scheme", coverage_scheme),
        ("receiving/scheme", receiving_scheme),
    ]:
        print(f"  {label}: {len(facet.by_player_id)} rows, population={facet.population!r}")

    print("\nFetching real nflverse season schedule for opponent pairing...")
    schedule = nfl.import_schedules([SEASON])
    week_games = schedule[(schedule["week"] == WEEK) & (schedule["game_type"] == "REG")][
        ["home_team", "away_team"]
    ].dropna()
    opponents: dict[str, str] = {}
    for _, row in week_games.iterrows():
        opponents[row["home_team"]] = row["away_team"]
        opponents[row["away_team"]] = row["home_team"]
    print(f"  {len(week_games)} real week-{WEEK} games found, {len(opponents)} teams paired.")

    run_block_agg = team_aggregate_grades(run_blocking.by_player_id, RUN_BLOCK_GRADE_FIELD, RUN_BLOCK_SNAP_FIELD)
    run_def_agg = team_aggregate_grades(run_defense.by_player_id, RUN_DEFENSE_GRADE_FIELD, RUN_DEFENSE_SNAP_FIELD)
    pass_block_agg = team_aggregate_grades(pass_blocking.by_player_id, PASS_BLOCK_GRADE_FIELD, PASS_BLOCK_SNAP_FIELD)
    pass_rush_agg = team_aggregate_grades(pass_rush.by_player_id, PASS_RUSH_GRADE_FIELD, PASS_RUSH_SNAP_FIELD)

    print(f"\nTeam run-block grade aggregates computed: {len(run_block_agg)} teams")
    print(f"Team run-defense grade aggregates computed: {len(run_def_agg)} teams")
    print(f"Team pass-block grade aggregates computed: {len(pass_block_agg)} teams")
    print(f"Team pass-rush win-rate aggregates computed: {len(pass_rush_agg)} teams")

    print("\n=== Run game multipliers (offense RB's team vs. this week's real opponent) ===")
    run_results = []
    for team, opponent in opponents.items():
        result = compute_run_game_multiplier(team, opponent, run_block_agg, run_def_agg)
        if result.multiplier is not None:
            run_results.append((team, opponent, result))
    run_results.sort(key=lambda t: t[2].multiplier, reverse=True)
    for team, opponent, r in run_results[:5] + run_results[-5:]:
        print(
            f"  {team} RB vs {opponent} run D: multiplier={r.multiplier:.3f} "
            f"(offense_grade={r.offense_grade:.1f} z={r.offense_z:+.2f}, "
            f"defense_grade={r.defense_grade:.1f} z={r.defense_z:+.2f})"
        )

    print("\n=== Pass protection multipliers (offense QB's team vs. this week's real opponent) ===")
    pass_results = []
    for team, opponent in opponents.items():
        result = compute_pass_protection_multiplier(team, opponent, pass_block_agg, pass_rush_agg)
        if result.multiplier is not None:
            pass_results.append((team, opponent, result))
    pass_results.sort(key=lambda t: t[2].multiplier, reverse=True)
    for team, opponent, r in pass_results[:5] + pass_results[-5:]:
        print(
            f"  {team} QB vs {opponent} pass rush: multiplier={r.multiplier:.3f} "
            f"(offense_grade={r.offense_grade:.1f} z={r.offense_z:+.2f}, "
            f"defense_win_rate={r.defense_grade:.3f} z={r.defense_z:+.2f})"
        )

    print("\n=== Coverage multipliers for real receivers with a real opponent this week ===")
    league_defender_man_population = [a.value for a in team_aggregate_grades(coverage_scheme.by_player_id, "man_grades_coverage_defense", "man_snap_counts_coverage").values()]
    league_defender_zone_population = [a.value for a in team_aggregate_grades(coverage_scheme.by_player_id, "zone_grades_coverage_defense", "zone_snap_counts_coverage").values()]
    league_receiver_man_population = [r.grades["man_grades_pass_route"] for r in receiving_scheme.by_player_id.values() if "man_grades_pass_route" in r.grades]
    league_receiver_zone_population = [r.grades["zone_grades_pass_route"] for r in receiving_scheme.by_player_id.values() if "zone_grades_pass_route" in r.grades]

    coverage_results = []
    for row in receiving_scheme.by_player_id.values():
        if row.position not in ("WR", "TE") or row.team is None:
            continue
        opponent = opponents.get(row.team)
        if opponent is None:
            continue
        rates = receiver_man_zone_rate(row.native_id, receiving_scheme.by_player_id)
        if rates is None:
            continue
        man_rate, zone_rate = rates
        result = compute_coverage_multiplier(
            receiver_id=row.native_id, receiver_team=row.team, defense_team=opponent,
            receiver_man_rate=man_rate, receiver_zone_rate=zone_rate,
            coverage_facet_rows=coverage_scheme.by_player_id, receiving_facet_rows=receiving_scheme.by_player_id,
            league_defender_man_population=league_defender_man_population,
            league_defender_zone_population=league_defender_zone_population,
            league_receiver_man_population=league_receiver_man_population,
            league_receiver_zone_population=league_receiver_zone_population,
        )
        if result.multiplier is not None:
            coverage_results.append((row, opponent, result))

    coverage_results.sort(key=lambda t: t[2].multiplier, reverse=True)
    print(f"  {len(coverage_results)} receivers with a computable coverage multiplier this week.")
    for row, opponent, r in coverage_results[:5] + coverage_results[-5:]:
        print(
            f"  {row.name} ({row.team} vs {opponent}, {row.position}): multiplier={r.multiplier:.3f} "
            f"confidence={r.confidence} (receiver_man_grade={r.receiver_man_grade}, "
            f"receiver_zone_grade={r.receiver_zone_grade}, defender_man_grade={r.defender_man_grade:.1f}, "
            f"defender_zone_grade={r.defender_zone_grade:.1f})"
        )

    print("\n=== Full pool (build_matchup_context_pool) -- combined multipliers for real WR/TEs ===")
    players = [
        PlayerMatchupInput(canonical_player_id=row.native_id, team=row.team, position=row.position, pff_native_id=row.native_id)
        for row in receiving_scheme.by_player_id.values()
        if row.position in ("WR", "TE") and row.team is not None
    ]
    facets = MatchupFacetInputs(
        run_blocking=run_blocking, run_defense=run_defense, pass_blocking=pass_blocking,
        pass_rush=pass_rush, coverage_scheme=coverage_scheme, receiving_scheme=receiving_scheme,
    )
    pool = build_matchup_context_pool(players, opponents, facets)
    computed = {pid: r for pid, r in pool.items() if r.combined_multiplier != 1.0 or not r.notes}
    sorted_pool = sorted(pool.items(), key=lambda kv: kv[1].combined_multiplier, reverse=True)
    print(f"  {len(pool)} WR/TE MatchupContext results computed.")
    for pid, r in sorted_pool[:5] + sorted_pool[-5:]:
        row = receiving_scheme.by_player_id[pid]
        print(
            f"  {row.name} ({r.team} vs {r.opponent}): combined_multiplier={r.combined_multiplier:.3f} "
            f"coverage_confidence={r.coverage_confidence}"
        )


if __name__ == "__main__":
    main()
