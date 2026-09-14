#!/usr/bin/env python3
"""Live check: does MatchupContext's coverage confidence ever reach CONFIDENT?

Pulls real slot-coverage and receiving-alignment data from the PFF Developer
API for one league-season-week, runs it through build_matchup_context_pool
(ADR-0022), and reports the coverage_confidence distribution across that
week's receivers.

Requires a real PFF Pro API key in the PFF_API_KEY environment variable
(see www.pff.com/account/api-keys). Grade-differential ingestion isn't wired
yet (see matchup/coverage.py's "Known ingestion gap"), so every receiver here
uses a neutral placeholder grade differential of 0.0 — this script validates
the *confidence* classification, not the multiplier magnitude.

Usage:
    PFF_API_KEY=ak_... python scripts/live_integration_check_matchup.py --season 2024 --week 1
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from nfl_dfs.ingestion.pff import (
    GAMES_PATH,
    RECEIVING_SUMMARY_PATH,
    PFFAPIError,
    PFFClient,
    PFFCredentialsError,
)
from nfl_dfs.matchup.context import PassCatcherInput, build_matchup_context_pool

NEUTRAL_GRADE_DIFFERENTIAL = 0.0


def _resolve_opponents(client: PFFClient, league: str, season: int, week: int) -> dict[str, str]:
    """team -> opponent team, from that week's schedule.

    `/v1/games`'s `home_team`/`away_team` are nested team objects
    (`{"abbreviation": "SF", ...}`), not flat strings as the OpenAPI spec's
    own example suggested — this pulls the abbreviation out of each.
    """
    payload = client.get(GAMES_PATH, {"league": league, "season": season, "week": week})
    opponents = {}
    for game in payload.get("games", []):
        home, away = game.get("home_team"), game.get("away_team")
        home_abbr = home.get("abbreviation") if isinstance(home, dict) else home
        away_abbr = away.get("abbreviation") if isinstance(away, dict) else away
        if home_abbr and away_abbr:
            opponents[home_abbr] = away_abbr
            opponents[away_abbr] = home_abbr
    return opponents


def _resolve_receiver_teams(client: PFFClient, league: str, season: int, week: int) -> dict[str, str]:
    """player_id -> team, from the receiving-summary report's raw rows.

    ReceiverAlignmentShare intentionally has no team field, so this script
    pulls it separately from the same report the typed ingestion consumes.
    """
    payload = client.get(RECEIVING_SUMMARY_PATH, {"league": league, "season": season, "week": week})
    return {
        str(row["player_id"]): row["team"]
        for row in payload.get("receiving_summary", [])
        if row.get("player_id") is not None and row.get("team")
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="nfl")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week", type=int, required=True)
    args = parser.parse_args()

    try:
        client = PFFClient()
    except PFFCredentialsError as exc:
        print(f"Cannot run live check: {exc}", file=sys.stderr)
        return 1

    try:
        defender_alignment_snaps = client.fetch_defender_alignment_snaps(
            args.league, args.season, args.week
        )
        receiver_alignment_share = client.fetch_receiver_alignment_share(
            args.league, args.season, args.week
        )
        opponents = _resolve_opponents(client, args.league, args.season, args.week)
        receiver_teams = _resolve_receiver_teams(client, args.league, args.season, args.week)
    except PFFAPIError as exc:
        print(f"PFF API request failed: {exc}", file=sys.stderr)
        return 1

    pass_catchers = []
    for share in receiver_alignment_share:
        team = receiver_teams.get(share.player_id)
        opponent = opponents.get(team) if team else None
        if not team or not opponent:
            continue
        pass_catchers.append(
            PassCatcherInput(
                player_id=share.player_id,
                team=team,
                opponent=opponent,
                team_coverage_grade_differential=NEUTRAL_GRADE_DIFFERENTIAL,
            )
        )

    results = build_matchup_context_pool(pass_catchers, defender_alignment_snaps, receiver_alignment_share)

    counts = Counter(r.coverage_confidence.value for r in results)
    print(f"{args.league} {args.season} week {args.week}: {len(results)} receivers checked")
    for confidence, count in counts.most_common():
        print(f"  {confidence}: {count}")

    confident = [r for r in results if r.coverage_confidence.value == "confident"]
    if confident:
        print("\nConfident matches:")
        for r in confident[:10]:
            print(f"  player {r.player_id} -> defender {r.matched_defender_id}")
    else:
        print("\nNo confident matches this week (fell back to team_wide/no_data for everyone).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
