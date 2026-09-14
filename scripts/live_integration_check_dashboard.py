"""Manual, live-network integration check for `dashboard/renderer.py`: run the full real pipeline
end to end -- real DK slate, real vendor projections, real lineups, real `WeeklyOutput` (reusing
`scripts/live_integration_check_output.py`'s pattern) -- then build real `PlayerDetailRecord`s
(`composition/player_detail.py`) for this slate's full reconciled player pool and render a real
dashboard HTML file to disk. NOT part of `pytest` -- needs live credentials and a live NFL slate,
not reproducible in CI. Run by hand:

    PYTHONPATH=. .venv/bin/python scripts/live_integration_check_dashboard.py

**Real-world timing note (inherited from `live_integration_check_output.py`):** run in week 1,
before any games have been played -- `RoleShareResult`/`PlayerSnapShare`/red-zone trailing data
all need completed weeks `1..W-1`, and week 1 has none, so those three sections are expected to
come back as real, honest "no trailing data yet" reasons for most players, not populated numbers
-- a genuine (not synthetic) exercise of this dashboard's nullability discipline. PFF's grade
facets (`own_scheme_splits`, opponent coverage tendency) use ADR-0014's week-1 prior-season
fallback instead, so those two sections DO populate with real 2024/2025 PFF grades.
"""

from __future__ import annotations

import warnings

from nfl_dfs.composition.player_detail import build_gsis_to_pff_id_map, build_player_detail_record
from nfl_dfs.config import config
from nfl_dfs.dashboard.renderer import SlateGameRow, write_dashboard_html
from nfl_dfs.game_environment.score import (
    GameEnvironmentScore,
    ImpliedTotalInput,
    PaceProeInput,
    PlayerInjuryStatus,
    WeatherInput,
    compute_game_environment_score,
)
from nfl_dfs.ingestion.nflverse import fetch_pace_proe
from nfl_dfs.ingestion.odds_api import ODDS_URL, fetch_dk_implied_totals, implied_team_totals, parse_dk_odds_events
from nfl_dfs.ingestion.pff import fetch_matchup_grades, team_coverage_tendency
from nfl_dfs.ingestion.rotogrinders_injuries import fetch_injury_report
from nfl_dfs.ingestion.snap_share import fetch_snap_shares
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR, aggregate_player_trailing_red_zone, fetch_role_shares
from nfl_dfs.ingestion.weather import WeatherReading, fetch_weather_reading
from nfl_dfs.normalization.crosswalk import fetch_crosswalk
from nfl_dfs.normalization.injury_lookup import team_injuries
from nfl_dfs.normalization.matcher import reconcile_week
from nfl_dfs.normalization.registry import PlayerRegistry
from nfl_dfs.optimizer.lineup import LineupGenerationError, generate_lineups
from nfl_dfs.output.weekly_output import build_weekly_output
from nfl_dfs.projection.blend import (
    build_projection_pool,
    extract_dk_salary,
    extract_footballguys_points,
    extract_rotogrinders_fpts,
)

from scripts.live_integration_check_output import _build_ges, _fetch_real_spreads, fetch_dk_raw_for_live_slate
from scripts.live_integration_check_projection import fetch_footballguys_raw, fetch_rotogrinders_raw

SEASON = 2026
WEEK = 1


def main() -> None:
    print(f"=== Live dashboard integration check -- season={SEASON}, week={WEEK} ===\n")

    print("Fetching DraftKings (anchor)...")
    dk_payload, dk_pool, dk_slate = fetch_dk_raw_for_live_slate()
    slate_teams = sorted(dk_slate.teams)
    print(f"  {len(dk_pool)} players, slate games: {[(g.away_team, g.home_team) for g in dk_slate.games]}")

    print("Fetching PFF...")
    from nfl_dfs.ingestion.pff import fetch_pff_players

    try:
        pff_pool = fetch_pff_players(season=SEASON, week=WEEK)
        print(f"  {len(pff_pool)} players")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        pff_pool = []

    print("Fetching RotoGrinders...")
    try:
        rg_payload, rg_pool = fetch_rotogrinders_raw()
        print(f"  {len(rg_pool)} players")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        rg_payload, rg_pool = {}, []

    print("Fetching Footballguys...")
    try:
        fbg_html_by_position, fbg_pool = fetch_footballguys_raw(WEEK)
        print(f"  {len(fbg_pool)} players")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        fbg_html_by_position, fbg_pool = {}, []

    print("Loading nflverse crosswalk...")
    crosswalk = fetch_crosswalk()

    registry = PlayerRegistry()
    identities = reconcile_week(dk_pool, pff_pool, rg_pool, fbg_pool, crosswalk, registry)
    print(f"\n{len(identities)} DK anchor players reconciled.\n")

    dk_salary = extract_dk_salary(dk_payload)
    rotogrinders_fpts = extract_rotogrinders_fpts(rg_payload) if rg_payload else {}
    footballguys_points = {}
    for html in fbg_html_by_position.values():
        footballguys_points.update(extract_footballguys_points(html))

    pool = build_projection_pool(identities, dk_salary, rotogrinders_fpts, footballguys_points)
    usable = [p for p in pool if p.blended_projection is not None and p.salary is not None]
    print(f"Projection pool: {len(pool)} total, {len(usable)} usable by the optimizer.\n")

    projections_by_canonical_id = {p.canonical_id: p for p in pool}

    print("=== Solving for 3 lineups ===")
    try:
        lineups = generate_lineups(pool, n=3)
    except LineupGenerationError as exc:
        print(f"LineupGenerationError: {exc}")
        return
    print(f"Generated {len(lineups)} lineup(s). Core stack teams: {[lu.core_stack_team for lu in lineups]}\n")

    print("=== Building real StackProfiles (for rationale text) ===")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        pace_proe_df = fetch_pace_proe(SEASON, WEEK)
        implied_df = fetch_dk_implied_totals(SEASON)
    implied_total_z_by_team = dict(zip(implied_df["team"], implied_df["implied_total_z"], strict=False))

    slate_games = {(g.away_team, g.home_team) for g in dk_slate.games}
    spreads = _fetch_real_spreads()

    role_share_results = fetch_role_shares(SEASON, WEEK)
    role_share_by_key = {(r.team, r.role): r for r in role_share_results}

    from nfl_dfs.correlation.stack_profile import build_stack_profile

    stack_profiles = []
    for (away, home), spread in spreads.items():
        if (away, home) not in slate_games:
            continue
        home_wr = role_share_by_key.get((home, ROLE_WR))
        away_wr = role_share_by_key.get((away, ROLE_WR))
        home_rb = role_share_by_key.get((home, ROLE_RB))
        if home_wr is None or away_wr is None:
            continue
        try:
            ges_home = _build_ges(home, pace_proe_df, implied_total_z_by_team)
            ges_away = _build_ges(away, pace_proe_df, implied_total_z_by_team)
        except (IndexError, KeyError):
            continue
        stack_profiles.append(build_stack_profile(ges_home, ges_away, spread, home_wr, away_wr, home_rb))
    print(f"  {len(stack_profiles)} StackProfile(s) built.\n")

    weekly = build_weekly_output(lineups, identities, stack_profiles)

    # ------------------------------------------------------------------------------------------
    # Player Detail: build real PlayerDetailRecords for this slate's FULL reconciled player pool
    # (not just the 27 lineup slots) -- a "browsable" dashboard tab is more representative of the
    # real deliverable with the whole slate's pool than with just the 3 lineups' worth of names.
    # ------------------------------------------------------------------------------------------
    print("=== Building real PlayerDetailRecords for the full slate pool ===")

    print("Fetching real RoleShare/snap-share/red-zone trailing data...")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        snap_shares = fetch_snap_shares(SEASON, WEEK)
    for w in caught:
        print(f"  WARNING (snap shares): {w.message}")
    snap_shares_by_player = {s.player_id: s for s in snap_shares}
    print(f"  {len(role_share_results)} RoleShareResult(s), {len(snap_shares)} PlayerSnapShare(s)")

    import nfl_data_py as nfl

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        pbp = nfl.import_pbp_data([SEASON], include_participation=False)
    red_zone_trailing = aggregate_player_trailing_red_zone(pbp, WEEK)
    print(f"  {len(red_zone_trailing)} trailing red-zone player-role row(s)")

    print("Fetching real PFF receiving/scheme and defense/coverage_scheme facet grades...")
    receiving_scheme_grades = fetch_matchup_grades("receiving/scheme", WEEK, SEASON)
    coverage_scheme_grades = fetch_matchup_grades("defense/coverage_scheme", WEEK, SEASON)
    coverage_tendency_by_team = team_coverage_tendency(coverage_scheme_grades)
    print(
        f"  receiving/scheme population={receiving_scheme_grades.population!r}, "
        f"{len(receiving_scheme_grades.by_player_id)} player row(s)"
    )
    print(f"  {len(coverage_tendency_by_team)} team(s) with a coverage tendency rollup")

    gsis_to_pff_id = build_gsis_to_pff_id_map(crosswalk)

    opponent_of: dict[str, str] = {}
    for g in dk_slate.games:
        opponent_of[g.away_team] = g.home_team
        opponent_of[g.home_team] = g.away_team

    print("Building real GameEnvironmentScores for this slate's teams...")
    game_environment_by_team: dict[str, GameEnvironmentScore] = {}
    for team in slate_teams:
        try:
            game_environment_by_team[team] = _build_ges(team, pace_proe_df, implied_total_z_by_team)
        except (IndexError, KeyError) as exc:
            print(f"  {team}: no pace/PROE row this pull ({exc}) -- skipping GameEnvironmentScore")

    player_details = []
    for identity in identities:
        if identity.team not in slate_teams:
            continue  # defensive only -- every reconciled identity should be from this slate
        record = build_player_detail_record(
            identity,
            SEASON,
            WEEK,
            team=identity.team,
            position=identity.position,
            opponent_team_this_week=opponent_of.get(identity.team),
            role_share_results=role_share_by_key,
            snap_shares_by_player=snap_shares_by_player,
            red_zone_trailing=red_zone_trailing,
            receiving_scheme_grades=receiving_scheme_grades,
            gsis_to_pff_id=gsis_to_pff_id,
            team_coverage_tendency=coverage_tendency_by_team,
            game_environment_by_team=game_environment_by_team,
            projections_by_canonical_id=projections_by_canonical_id,
        )
        player_details.append(record)

    print(f"  Built {len(player_details)} PlayerDetailRecord(s) for the full slate pool.\n")

    populated_role_share = sum(1 for r in player_details if r.usage.role_share.role_share is not None)
    populated_own_scheme = sum(1 for r in player_details if r.own_scheme_splits.reason is None and r.own_scheme_splits.applicable)
    populated_ge = sum(1 for r in player_details if r.game_environment is not None and r.game_environment.is_available)
    print(
        f"  Populated: role_share={populated_role_share}/{len(player_details)}, "
        f"own_scheme_splits={populated_own_scheme}/{len(player_details)}, "
        f"game_environment={populated_ge}/{len(player_details)}"
    )

    # ------------------------------------------------------------------------------------------
    # Slate Overview: one row per game -- real Vegas odds/implied totals, real weather, real
    # injury data, and a GameEnvironmentScore per team that wires in *real* weather. This is a
    # separate, additive computation from `game_environment_by_team` above (which -- unchanged,
    # matching `live_integration_check_output.py`'s own existing `_build_ges` pattern this script
    # otherwise reuses as-is -- always passes `NEUTRAL_WEATHER` and no injury data, since that
    # dict feeds the Player Detail tab, out of scope to touch per this task). The two tabs' composite
    # scores for the same team can therefore differ slightly this week by the (small, 11.1%-
    # weighted) weather component and by the injury flag, which the Player Detail tab's path never
    # populates today. Real, not synthetic: real Odds API line, real Open-Meteo/NWS weather, real
    # RotoGrinders Situation Room injury data.
    # ------------------------------------------------------------------------------------------
    print("=== Building real Slate Overview rows (odds, weather, GameEnvironmentScore) ===")

    print("Fetching real Odds API spreads/totals...")
    import requests

    odds_response = requests.get(
        ODDS_URL,
        params={
            "regions": "us",
            "markets": "spreads,totals",
            "oddsFormat": "american",
            "apiKey": config.odds_api_key,
        },
        timeout=20.0,
    )
    odds_response.raise_for_status()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        odds_games = parse_dk_odds_events(odds_response.json())
    for w in caught:
        print(f"  WARNING (odds): {w.message}")
    odds_by_pair = {(g.away_team, g.home_team): g for g in odds_games}
    print(f"  {len(odds_games)} game(s) with a DraftKings odds line.")

    print("Fetching real injury data (RotoGrinders Situation Room)...")
    try:
        injury_entries = fetch_injury_report()
        print(f"  {len(injury_entries)} injury report row(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        injury_entries = []

    def _injury_statuses_for(team: str) -> list[PlayerInjuryStatus]:
        return [
            PlayerInjuryStatus(status=entry.status, impact_rating=entry.impact_rating)
            for _, entry in team_injuries(team, identities, injury_entries)
        ]

    print("Fetching real weather (Open-Meteo primary, NWS cross-check)...")
    weather_by_home_team: dict[str, WeatherReading] = {}
    weather_reason_by_home_team: dict[str, str] = {}
    for game in dk_slate.games:
        try:
            weather_by_home_team[game.home_team] = fetch_weather_reading(game.home_team, game.start_time_utc)
        except Exception as exc:  # noqa: BLE001
            weather_reason_by_home_team[game.home_team] = f"weather fetch failed: {exc}"
            print(f"  {game.home_team}: weather fetch FAILED ({exc})")
    print(f"  {len(weather_by_home_team)}/{len(dk_slate.games)} game(s) with a weather reading.")

    def _ges_with_real_weather(team: str, weather_reading: WeatherReading | None) -> GameEnvironmentScore | None:
        rows = pace_proe_df[pace_proe_df["team"] == team]
        if rows.empty:
            return None
        row = rows.iloc[0]
        pace_proe = PaceProeInput(
            pace_z=float(row["pace_z"]),
            proe_z=float(row["proe_z"]),
            weeks_played=int(row["weeks_played"]),
            shrinkage_weight=float(row["shrinkage_weight"]),
        )
        implied_total = ImpliedTotalInput(z=implied_total_z_by_team.get(team))
        if weather_reading is None:
            weather_input = WeatherInput(is_indoor=False)
        else:
            weather_input = WeatherInput(
                is_indoor=weather_reading.is_indoor,
                wind_any_a_relative_drop=weather_reading.wind_any_a_effect_pct,
                temperature_relative_drop=weather_reading.temperature_effect_pct,
                precipitation_relative_drop=weather_reading.precipitation_relative_drop,
            )
        return compute_game_environment_score(
            team,
            SEASON,
            WEEK,
            implied_total,
            pace_proe,
            weather_input,
            team_injuries=_injury_statuses_for(team),
        )

    slate_games: list[SlateGameRow] = []
    for game in dk_slate.games:
        away, home = game.away_team, game.home_team
        odds = odds_by_pair.get((away, home))
        odds_reason = None
        home_spread = away_spread = total = home_implied = away_implied = None
        if odds is None:
            odds_reason = (
                "no DraftKings odds line for this game -- either already underway and dropped "
                "from the Odds API feed, or a team-name mapping gap (odds_api.py's module "
                "docstring's documented known limitation)"
            )
        else:
            home_spread, away_spread, total = odds.home_spread, odds.away_spread, odds.total
            if home_spread is not None and away_spread is not None and total is not None:
                implied = implied_team_totals(odds)
                home_implied, away_implied = implied.get(home), implied.get(away)
            else:
                odds_reason = "DraftKings line present but missing a spread or total field this pull"

        weather_reading = weather_by_home_team.get(home)
        weather_reason = weather_reason_by_home_team.get(home) if weather_reading is None else None

        home_ges = _ges_with_real_weather(home, weather_reading)
        away_ges = _ges_with_real_weather(away, weather_reading)

        slate_games.append(
            SlateGameRow(
                away_team=away,
                home_team=home,
                kickoff_utc=game.start_time_utc,
                kickoff_reason=None,
                home_spread=home_spread,
                away_spread=away_spread,
                total=total,
                odds_reason=odds_reason,
                home_implied_total=home_implied,
                away_implied_total=away_implied,
                weather=weather_reading,
                weather_reason=weather_reason,
                home_environment=home_ges,
                home_environment_reason=None if home_ges is not None else f"no pace/PROE row this pull for {home}",
                away_environment=away_ges,
                away_environment_reason=None if away_ges is not None else f"no pace/PROE row this pull for {away}",
            )
        )
    print(f"  Built {len(slate_games)} SlateGameRow(s) for the Slate Overview tab.\n")

    # Report the actual conviction ranking this pull produces, reusing the renderer's own sort
    # key (not a re-derived copy) so this printed summary can never drift from what the rendered
    # HTML actually shows.
    from nfl_dfs.dashboard.renderer import _conviction_sort_key, _game_composite_scores, _worst_injury_flag

    print("=== Conviction ranking this pull produced ===")
    for rank, g in enumerate(sorted(slate_games, key=_conviction_sort_key), start=1):
        scores = _game_composite_scores(g)
        flag = _worst_injury_flag(g)
        avg = f"{sum(scores) / len(scores):.1f}" if scores else "n/a"
        print(
            f"  {rank}. {g.away_team} @ {g.home_team}: avg composite={avg} "
            f"(scores={[f'{s:.1f}' for s in scores]}), injury_flag={flag!r}, "
            f"weather_indoor={g.weather.is_indoor if g.weather else 'unknown'}"
        )

    out_path = "dashboard_output/weekly_dashboard.html"
    import os

    os.makedirs("dashboard_output", exist_ok=True)
    write_dashboard_html(out_path, weekly, player_details, slate_games)
    print(f"\nWrote real dashboard HTML to {out_path}")


if __name__ == "__main__":
    main()
