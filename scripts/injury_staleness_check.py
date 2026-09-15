"""Live cross-source injury staleness check -- ADR-0031. Compares RotoGrinders' "Situation Room"
injury report (`ingestion/rotogrinders_injuries.py`, this project's only currently-wired injury
source) against the NFL's own official weekly injury report (`ingestion/official_injury_report.py`,
via `nfl_data_py.import_injuries()`) -- a genuinely independent source, not a second vendor
repackaging of RotoGrinders' own data (unlike LineupHQ, which shares a vendor with Situation Room;
see ADR-0031 for why that comparison was rejected in favor of this one).

**Real, disclosed scope limitation**: `import_injuries()` returns one row per (player, week), not
a daily practice-report time series (confirmed live, see `official_injury_report.py`'s module
docstring) -- so this check measures WEEK-level coverage/agreement (does Situation Room currently
show a status for a player the official report has, and does it roughly agree), not INTRA-week
staleness (how many hours Situation Room lags a specific Wednesday practice report). It also
compares RotoGrinders' CURRENT live snapshot (whatever week is upcoming) against nflverse's MOST
RECENTLY AGGREGATED official week -- printed explicitly below, since a week mismatch between the
two (RotoGrinders showing next week's injuries while nflverse's official feed still only has last
week's finalized report) is itself a real, disclosed possible confound, not silently assumed away.

Reuses the same DK/PFF/RotoGrinders/Footballguys reconciliation bootstrap
`live_integration_check_dashboard.py` already uses, to get a real `PlayerIdentity` pool bridging
RotoGrinders' native id space (Situation Room's join key) to nflverse's gsis_id space (the official
report's join key) -- no new crosswalk built for this script.

NOT part of `pytest` -- a one-time live-data research pass, run by hand:

    PYTHONPATH=. .venv/bin/python scripts/injury_staleness_check.py
"""

from __future__ import annotations

from nfl_dfs.ingestion.official_injury_report import fetch_official_injury_report, latest_week_entries
from nfl_dfs.ingestion.rotogrinders_injuries import fetch_injury_report
from nfl_dfs.normalization.crosswalk import fetch_crosswalk
from nfl_dfs.normalization.identity import MatchMethod
from nfl_dfs.normalization.matcher import reconcile_week
from nfl_dfs.normalization.registry import PlayerRegistry

from scripts.live_integration_check_output import fetch_dk_raw_for_live_slate
from scripts.live_integration_check_projection import fetch_footballguys_raw, fetch_rotogrinders_raw

SEASON = 2026
WEEK = 1

# A rough, disclosed mapping from the NFL's official three-tier report_status vocabulary to
# RotoGrinders' single-letter STATUS codes -- used only to classify agreement/disagreement, not
# treated as an exact or backtested equivalence (neither source publishes a crosswalk between the
# two vocabularies).
_OFFICIAL_TO_RG_STATUS = {"Out": "O", "Doubtful": "D", "Questionable": "Q"}


def main() -> None:
    print(f"=== Injury staleness check -- season={SEASON}, week={WEEK} ===\n")

    print("Fetching DraftKings (anchor)...")
    dk_payload, dk_pool, dk_slate = fetch_dk_raw_for_live_slate()
    print(f"  {len(dk_pool)} players")

    print("Fetching PFF...")
    from nfl_dfs.ingestion.pff import fetch_pff_players

    try:
        pff_pool = fetch_pff_players(season=SEASON, week=WEEK)
        print(f"  {len(pff_pool)} players")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        pff_pool = []

    print("Fetching RotoGrinders (LineupHQ projections, for identity reconciliation only)...")
    try:
        _, rg_pool = fetch_rotogrinders_raw()
        print(f"  {len(rg_pool)} players")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        rg_pool = []

    print("Fetching Footballguys...")
    try:
        _, fbg_pool = fetch_footballguys_raw(WEEK)
        print(f"  {len(fbg_pool)} players")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        fbg_pool = []

    print("Loading nflverse crosswalk...")
    crosswalk = fetch_crosswalk()

    registry = PlayerRegistry()
    identities = reconcile_week(dk_pool, pff_pool, rg_pool, fbg_pool, crosswalk, registry)
    print(f"\n{len(identities)} DK anchor players reconciled.\n")

    print("Fetching real RotoGrinders Situation Room injury report...")
    rg_entries = fetch_injury_report()
    print(f"  {len(rg_entries)} rows, STATUS codes observed: {sorted({e.status for e in rg_entries})}")
    by_rg_id = {e.rotogrinders_player_id: e for e in rg_entries}

    print(f"\nFetching real official NFL injury report ({SEASON})...")
    official_entries = fetch_official_injury_report(SEASON)
    latest_week, latest_entries = latest_week_entries(official_entries)
    print(f"  {len(official_entries)} total rows across every week nflverse has aggregated so far")
    print(f"  Most recent official week available: {latest_week} ({len(latest_entries)} rows)")
    if latest_week != WEEK:
        print(
            f"  ** WEEK MISMATCH DISCLOSED **: this script targets WEEK={WEEK}, but the official "
            f"feed's most recent aggregated week is {latest_week}. RotoGrinders' Situation Room "
            "pull above is always a CURRENT live snapshot (whatever week RotoGrinders itself "
            "currently shows) -- if that's ahead of the official feed's most recent week, this "
            "comparison is checking RotoGrinders' read of a week the official source hasn't "
            "caught up to publishing yet. Read the results below with that in mind, not as a "
            "same-week apples-to-apples check."
        )
    by_gsis = {e.gsis_id: e for e in latest_entries}

    # Bridge: every reconciled identity that has BOTH a resolved RotoGrinders native id AND a
    # resolved nflverse gsis_id -- the only players this comparison can actually check.
    bridged = []
    for identity in identities:
        rg_match = identity.sources.get("rotogrinders")
        if rg_match is None or rg_match.method == MatchMethod.UNRESOLVED or rg_match.native_id is None:
            continue
        if identity.nflverse_gsis_id is None:
            continue
        bridged.append((identity, rg_match.native_id, identity.nflverse_gsis_id))
    print(f"\n{len(bridged)} reconciled identities have BOTH a RotoGrinders id and a resolvable gsis_id.")

    only_rg, only_official, both, agree, disagree = [], [], [], [], []
    for identity, rg_id, gsis_id in bridged:
        rg_entry = by_rg_id.get(rg_id)
        official_entry = by_gsis.get(gsis_id)
        on_rg = rg_entry is not None
        on_official = official_entry is not None and official_entry.report_status is not None
        if on_rg and not on_official:
            only_rg.append((identity, rg_entry))
        elif on_official and not on_rg:
            only_official.append((identity, official_entry))
        elif on_rg and on_official:
            both.append((identity, rg_entry, official_entry))
            expected_rg_status = _OFFICIAL_TO_RG_STATUS.get(official_entry.report_status)
            if expected_rg_status is not None and rg_entry.status == expected_rg_status:
                agree.append((identity, rg_entry, official_entry))
            else:
                disagree.append((identity, rg_entry, official_entry))

    print("\n=== Coverage ===")
    print(f"  On RotoGrinders Situation Room only (official report has no game-status for them): {len(only_rg)}")
    for identity, rg_entry in only_rg:
        print(f"    {identity.display_name:<24} {identity.team:<4} RG status={rg_entry.status} impact={rg_entry.impact_rating}")
    print(f"\n  On official report only (Situation Room has NO row for them at all -- a real coverage gap): {len(only_official)}")
    for identity, official_entry in only_official:
        print(f"    {identity.display_name:<24} {identity.team:<4} official={official_entry.report_status} ({official_entry.report_primary_injury})")

    print(f"\n=== Agreement (players present on BOTH, with a real official game-status) ===")
    print(f"  n={len(both)}: {len(agree)} agree, {len(disagree)} disagree")
    if both:
        print(f"  Match rate: {len(agree)}/{len(both)} = {len(agree) / len(both):.1%}")
    for identity, rg_entry, official_entry in disagree:
        print(
            f"    DISAGREE  {identity.display_name:<24} {identity.team:<4} "
            f"RG={rg_entry.status} official={official_entry.report_status} ({official_entry.report_primary_injury})"
        )


if __name__ == "__main__":
    main()
