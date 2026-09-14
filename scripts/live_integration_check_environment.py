"""Manual, live-network integration check for the three game/team-level sources feeding
`GameEnvironmentScore`/`DSTProjection`: nflverse (pace/PROE), the Odds API (implied totals), and
weather (Open-Meteo + NWS). NOT part of `pytest` -- same pattern as `live_integration_check.py`
(the four vendor-source check from the prior round): needs live network access and, for the odds
pull, a configured `ODDS_API_KEY`. Run by hand:

    .venv/bin/python scripts/live_integration_check_environment.py

Prints pace/PROE for a sample of teams, implied totals + z-scores for the current odds pull, and
a weather reading for one outdoor and one domed game, plus any warnings raised along the way
(missing DK lines, unmatched schedule weeks, NWS falling back, etc.) so nothing is silently lost.
"""

from __future__ import annotations

import warnings

from nfl_dfs.ingestion.nflverse import DEFAULT_PRIOR_SEASONS, fetch_pace_proe
from nfl_dfs.ingestion.odds_api import fetch_dk_implied_totals
from nfl_dfs.ingestion.stadiums import STADIUMS
from nfl_dfs.ingestion.weather import fetch_dk_game_schedule, fetch_weather_reading

SEASON = 2026
WEEK = 1


def check_nflverse() -> None:
    print(f"\n=== nflverse pace/PROE (season={SEASON}, target_week={WEEK}, prior={DEFAULT_PRIOR_SEASONS}) ===")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = fetch_pace_proe(SEASON, WEEK)
    print(f"{len(df)} team rows returned (expect 32).")
    print(df.sort_values("pace_z", ascending=False).head(5).to_string(index=False))
    print(df.sort_values("pace_z").head(5).to_string(index=False))
    print(f"NaN pace_z rows: {df['pace_z'].isna().sum()}; NaN proe_z rows: {df['proe_z'].isna().sum()}")
    print(f"weeks_played distribution: {sorted(df['weeks_played'].unique().tolist())}")
    for w in caught:
        print(f"  WARNING: {w.message}")


def check_odds_api() -> None:
    print(f"\n=== Odds API implied totals (season={SEASON}) ===")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = fetch_dk_implied_totals(SEASON)
    print(f"{len(df)} team rows returned this pull (a full week would be up to 32; see module docstring")
    print("on games already underway dropping out of the odds feed).")
    print(df.sort_values("implied_total", ascending=False).to_string(index=False))
    for w in caught:
        print(f"  WARNING: {w.message}")


def check_weather() -> None:
    print("\n=== Weather (Open-Meteo + NWS) ===")
    # Find one Classic draftGroupId to source real kickoff times from, without touching
    # ingestion/draftkings.py (out of scope) -- reuse its live-slate lookup only.
    from nfl_dfs.ingestion.draftkings import fetch_classic_draft_group_id

    dg = fetch_classic_draft_group_id()
    games = fetch_dk_game_schedule(dg)
    print(f"{len(games)} games found in draftGroupId {dg}.")

    outdoor_game = next((g for g in games if not STADIUMS[g.home_team].is_indoor), None)
    domed_game = next((g for g in games if STADIUMS[g.home_team].is_indoor), None)

    if outdoor_game:
        print(f"\nOutdoor sample: {outdoor_game.away_team} @ {outdoor_game.home_team} ({outdoor_game.kickoff_utc})")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            reading = fetch_weather_reading(outdoor_game.home_team, outdoor_game.kickoff_utc)
        print(reading)
        for w in caught:
            print(f"  WARNING: {w.message}")
    else:
        print("No outdoor game found in this slate to sample.")

    if domed_game:
        print(f"\nDomed/retractable sample: {domed_game.away_team} @ {domed_game.home_team}")
        reading = fetch_weather_reading(domed_game.home_team, domed_game.kickoff_utc)
        print(reading)
    else:
        print("No domed/retractable game found in this slate to sample.")


def main() -> None:
    check_nflverse()
    check_odds_api()
    check_weather()


if __name__ == "__main__":
    main()
