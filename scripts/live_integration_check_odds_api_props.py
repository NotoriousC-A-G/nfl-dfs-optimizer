"""Manual, live-network integration check for `ingestion/odds_api_props.py` -- the player-props
extension of the already-in-production Odds API integration (`ingestion/odds_api.py`). NOT part
of `pytest` -- needs live network access, a configured `ODDS_API_KEY`, and a live DK Classic slate
to anchor matching against. Run by hand:

    PYTHONPATH=. .venv/bin/python scripts/live_integration_check_odds_api_props.py

Real credit cost: `len(DEFAULT_PROP_MARKETS) * (number of week's events not yet started)` -- see
`odds_api_props.py`'s module docstring for the confirmed cost model. For SEASON=2026/WEEK=3 this
is a handful of events x 7 markets, on the order of ~100 credits out of a 20,000/month budget --
fine to run by hand a few times a week, not something to loop repeatedly.

Prints, per event: the DK player pool size, how many props rows were parsed, and a match-quality
breakdown (NAME_TEAM / UNRESOLVED / AMBIGUOUS) with real player names for each -- so a human can
actually see whether names resolved, not just a count.
"""

from __future__ import annotations

import warnings
from collections import Counter

from nfl_dfs.ingestion.draftkings import fetch_draftkings_players
from nfl_dfs.ingestion.odds_api_props import (
    DEFAULT_PROP_MARKETS,
    PropMatchMethod,
    fetch_dk_player_props_for_week,
)

SEASON = 2026
WEEK = 3


def main() -> None:
    print(f"=== Odds API player props (season={SEASON}, week={WEEK}) ===")
    print(f"Markets requested: {DEFAULT_PROP_MARKETS}")

    print("\nFetching DraftKings (anchor pool)...")
    dk_pool = fetch_draftkings_players()
    print(f"  {len(dk_pool)} players")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = fetch_dk_player_props_for_week(SEASON, WEEK, dk_pool)

    print(f"\n{len(df)} total prop rows parsed across {df['event_id'].nunique()} event(s).")
    print(f"Markets actually present in the pull: {sorted(df['market'].unique().tolist())}")

    method_counts = Counter(df["match_method"])
    print(f"\nMatch quality: {dict(method_counts)}")

    print("\n--- Sample resolved rows (NAME_TEAM) ---")
    resolved = df[df["match_method"] == PropMatchMethod.NAME_TEAM.value]
    print(resolved.head(15).to_string(index=False))

    unresolved = df[df["match_method"] == PropMatchMethod.UNRESOLVED.value]
    if not unresolved.empty:
        print(f"\n--- UNRESOLVED ({len(unresolved)} rows) -- names that didn't match the DK pool ---")
        print(unresolved[["player_name", "market", "home_team", "away_team"]].drop_duplicates().to_string(index=False))

    ambiguous = df[df["match_method"] == PropMatchMethod.AMBIGUOUS.value]
    if not ambiguous.empty:
        print(f"\n--- AMBIGUOUS ({len(ambiguous)} rows) -- name collisions, never auto-picked ---")
        print(ambiguous[["player_name", "match_note"]].drop_duplicates().to_string(index=False))

    if caught:
        print(f"\n--- {len(caught)} warning(s) raised during the pull ---")
        for w in caught:
            print(f"  WARNING: {w.message}")


if __name__ == "__main__":
    main()
