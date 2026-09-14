#!/usr/bin/env python3
"""Live check: does MatchupContext's coverage confidence ever reach CONFIDENT?

Pulls real slot-coverage and receiving-alignment data from the PFF Developer
API for one league-season-week, runs it through build_matchup_context_pool
(ADR-0022), and reports the coverage_confidence distribution across that
week's WR/TE.

Requires a real PFF Pro API key in the PFF_API_KEY environment variable
(see www.pff.com/account/api-keys). Grade-differential ingestion for the six
MatchupFacetInputs facets isn't wired yet -- this script uses a neutral
placeholder value (identical across every team, so the z-score differential
resolves to 0.0 for everyone) for receiving_scheme/defense_coverage_scheme,
and leaves the other four facets empty. This validates the *confidence*
classification, not the multiplier magnitude.

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
from nfl_dfs.matchup.context import MatchupFacetInputs, build_matchup_context_pool
from nfl_dfs.matchup.coverage import CoverageConfidence

NEUTRAL_GRADE = 70.0


def _resolve_opponents(client: PFFClient, league: str, season: int, week: int) -> dict[str, str]:
    """team -> opponent team, from that week's schedule.

    `/v1/games`'s `home_team`/`away_team` are nested team objects
    (`{"abbreviation": "SF", ...}`), not flat strings as the OpenAPI spec's
    own example suggested -- this pulls the abbreviation out of each.
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


def _resolve_receiver_team_and_position(
    client: PFFClient, league: str, season: int, week: int
) -> dict[str, tuple[str, str]]:
    """player_id -> (team, position), from the receiving-summary report's raw rows.

    ReceiverAlignmentShare intentionally has no team/position field, so this
    script pulls them separately from the same report the typed ingestion
    consumes.
    """
    payload = client.get(RECEIVING_SUMMARY_PATH, {"league": league, "season": season, "week": week})
    result = {}
    for row in payload.get("receiving_summary", []):
        player_id, team, position = row.get("player_id"), row.get("team"), row.get("position")
        if player_id is not None and team and position:
            result[str(player_id)] = (team, position)
    return result


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
        receiver_info = _resolve_receiver_team_and_position(client, args.league, args.season, args.week)
    except PFFAPIError as exc:
        print(f"PFF API request failed: {exc}", file=sys.stderr)
        return 1

    teams_seen = {team for team, _position in receiver_info.values()} | set(opponents)
    neutral_by_team = {team: NEUTRAL_GRADE for team in teams_seen}
    facets = MatchupFacetInputs(
        offense_run_blocking={},
        defense_run={},
        offense_pass_blocking={},
        defense_pass_rush={},
        defense_coverage_scheme=neutral_by_team,
        receiving_scheme=neutral_by_team,
    )

    players = []
    for share in receiver_alignment_share:
        info = receiver_info.get(share.player_id)
        if info is None:
            continue
        team, position = info
        opponent = opponents.get(team)
        if not opponent:
            continue
        players.append((share.player_id, position, team, opponent))

    pool = build_matchup_context_pool(players, facets, defender_alignment_snaps, receiver_alignment_share)

    counts = Counter(
        context.coverage_confidence.value if context.coverage_confidence else "not_applicable"
        for context in pool.values()
    )
    print(f"{args.league} {args.season} week {args.week}: {len(pool)} WR/TE checked")
    for confidence, count in counts.most_common():
        print(f"  {confidence}: {count}")

    confident = [c for c in pool.values() if c.coverage_confidence == CoverageConfidence.CONFIDENT]
    if confident:
        print("\nConfident matches:")
        for context in confident[:10]:
            print(f"  player {context.player_id} -> defender {context.matched_defender_id}")
    else:
        print("\nNo confident matches this week (fell back to team_wide/not_applicable for everyone).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
