"""Manual, live-network integration check: pull all four vendor sources for real and run them
through the matcher (ADR-0013 / `normalization/matcher.py`). NOT part of `pytest` -- it needs
live credentials and a live NFL slate, so it isn't reproducible in CI or for anyone without their
own session cookies. Run by hand:

    .venv/bin/python scripts/live_integration_check.py

Prints per-source match-rate counts against the week's DK anchor pool and a sample of any
UNRESOLVED/AMBIGUOUS rows, the same way the matcher's own ADR-0013 smoke test was reported.
"""

from __future__ import annotations

from collections import Counter

from nfl_dfs.ingestion.draftkings import fetch_draftkings_players
from nfl_dfs.ingestion.footballguys import fetch_footballguys_players
from nfl_dfs.ingestion.pff import fetch_pff_players
from nfl_dfs.ingestion.rotogrinders import fetch_rotogrinders_players
from nfl_dfs.normalization.crosswalk import fetch_crosswalk
from nfl_dfs.normalization.identity import MatchMethod
from nfl_dfs.normalization.matcher import reconcile_week
from nfl_dfs.normalization.registry import PlayerRegistry

SEASON = 2026
WEEK = 1


def main() -> None:
    print("Fetching DraftKings (anchor)...")
    dk_pool = fetch_draftkings_players()
    print(f"  {len(dk_pool)} players")

    print("Fetching PFF...")
    pff_pool = fetch_pff_players(season=SEASON, week=WEEK)
    print(f"  {len(pff_pool)} players")

    print("Fetching RotoGrinders...")
    try:
        rg_pool = fetch_rotogrinders_players()
        print(f"  {len(rg_pool)} players")
    except Exception as exc:  # noqa: BLE001 -- report, don't crash the whole check
        print(f"  FAILED: {exc}")
        rg_pool = []

    print("Fetching Footballguys...")
    try:
        fbg_pool = fetch_footballguys_players(week=WEEK)
        print(f"  {len(fbg_pool)} players")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        fbg_pool = []

    print("Loading nflverse crosswalk...")
    crosswalk = fetch_crosswalk()

    registry = PlayerRegistry()
    identities = reconcile_week(dk_pool, pff_pool, rg_pool, fbg_pool, crosswalk, registry)

    print(f"\n{len(identities)} DK anchor players reconciled.\n")
    for source in ("pff", "rotogrinders", "footballguys"):
        counts = Counter(identity.sources[source].method for identity in identities)
        total = len(identities)
        resolved = counts[MatchMethod.CROSSWALK] + counts[MatchMethod.NAME_TEAM_POSITION]
        print(
            f"{source:>13}: {resolved}/{total} resolved "
            f"({resolved / total:.1%}) -- crosswalk={counts[MatchMethod.CROSSWALK]}, "
            f"name_team_position={counts[MatchMethod.NAME_TEAM_POSITION]}, "
            f"unresolved={counts[MatchMethod.UNRESOLVED]}, ambiguous={counts[MatchMethod.AMBIGUOUS]}"
        )

    ambiguous = [i for i in identities if i.has_ambiguous_matches()]
    if ambiguous:
        print(f"\n{len(ambiguous)} players with an AMBIGUOUS source match (sample up to 10):")
        for identity in ambiguous[:10]:
            print(f"  {identity.display_name} ({identity.team} {identity.position}): {identity.flags}")

    fully_missing = [i for i in identities if len(i.missing_from_sources()) == 3]
    if fully_missing:
        print(f"\n{len(fully_missing)} DK players resolved on ZERO vendor sources (sample up to 10):")
        for identity in fully_missing[:10]:
            print(f"  {identity.display_name} ({identity.team} {identity.position})")


if __name__ == "__main__":
    main()
