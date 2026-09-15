"""Retrospective injury-staleness check -- the real fix ADR-0031 named and ADR-0038 built.

`scripts/injury_staleness_check.py` can only ever compare RotoGrinders' CURRENT live snapshot
against whatever week nflverse's official feed has already finalized -- almost always a week
behind, so it can't measure genuine same-week staleness (ADR-0031's Finding 1). This script is
the retrospective fix: once nflverse's official report for week `TARGET_WEEK` finally becomes
available (typically the following week), compare EVERY archived RotoGrinders Situation Room
snapshot captured while that week was still "current" (`scripts/injury_snapshot_capture.py`,
`storage/injury_snapshot_store.py`) against that now-available official report -- a genuine
same-week comparison for each snapshot.

Prints a match-rate-over-time table, one row per archived snapshot date, showing whether/how
Situation Room's agreement with the eventual official report trended as kickoff approached -- the
actual staleness signal ADR-0031 set out to measure and couldn't, for lack of an archive.

**Requires archived snapshots to already exist for `TARGET_WEEK`** (run
`scripts/injury_snapshot_capture.py` on a recurring cadence during that week first) **and** the
official report for that week to have been published by nflverse -- if either is missing, this
script says so plainly and exits rather than reporting a misleading empty/partial result.

Reuses `analysis/injury_staleness.py`'s `compare_injury_sources` -- the same bridging/
classification logic `injury_staleness_check.py` uses, not a second copy. The CURRENT reconciled
`PlayerIdentity` pool is used as the join-key space for every archived snapshot (a disclosed
simplification: canonical/native/gsis id schemes are stable per-player identifiers, not expected
to vary week to week for the same underlying players -- unlike the injury STATUS values
themselves, which are exactly what's being compared).

NOT part of `pytest` -- a one-time live-data research pass, run by hand once enough snapshots
exist for a settled week:

    PYTHONPATH=. .venv/bin/python scripts/injury_snapshot_retrospective_check.py
"""

from __future__ import annotations

from nfl_dfs.analysis.injury_staleness import compare_injury_sources
from nfl_dfs.ingestion.official_injury_report import fetch_official_injury_report
from nfl_dfs.normalization.crosswalk import fetch_crosswalk
from nfl_dfs.normalization.matcher import reconcile_week
from nfl_dfs.normalization.registry import PlayerRegistry
from nfl_dfs.storage.injury_snapshot_store import read_snapshots_for_week

from scripts.live_integration_check_output import fetch_dk_raw_for_live_slate
from scripts.live_integration_check_projection import fetch_footballguys_raw, fetch_rotogrinders_raw

SEASON = 2026
TARGET_WEEK = 1  # the settled week to retrospectively check -- update once its official report
# has been published and enough snapshots have accumulated for it.


def main() -> None:
    print(f"=== Retrospective injury staleness check -- season={SEASON}, week={TARGET_WEEK} ===\n")

    snapshots = read_snapshots_for_week(SEASON, TARGET_WEEK)
    if not snapshots:
        print(
            f"No archived snapshots found for season={SEASON}, week={TARGET_WEEK} -- run "
            "scripts/injury_snapshot_capture.py during that week first, then re-run this check."
        )
        return
    print(f"{len(snapshots)} archived snapshot(s) found: {[s.date for s in snapshots]}\n")

    print(f"Fetching real official NFL injury report ({SEASON})...")
    official_entries = fetch_official_injury_report(SEASON)
    week_official_entries = [e for e in official_entries if e.week == TARGET_WEEK]
    print(f"  {len(week_official_entries)} official row(s) for week {TARGET_WEEK}")
    if not week_official_entries:
        print(f"  Official report for week {TARGET_WEEK} isn't published yet -- nothing to compare against. Re-run once it is.")
        return

    print("\nReconciling identities (needed to bridge RotoGrinders native id <-> nflverse gsis_id)...")
    dk_payload, dk_pool, dk_slate = fetch_dk_raw_for_live_slate()

    from nfl_dfs.ingestion.pff import fetch_pff_players

    try:
        pff_pool = fetch_pff_players(season=SEASON, week=TARGET_WEEK)
    except Exception as exc:  # noqa: BLE001
        print(f"  PFF FAILED: {exc}")
        pff_pool = []
    try:
        _, rg_pool = fetch_rotogrinders_raw()
    except Exception as exc:  # noqa: BLE001
        print(f"  RotoGrinders (LineupHQ) FAILED: {exc}")
        rg_pool = []
    try:
        _, fbg_pool = fetch_footballguys_raw(TARGET_WEEK)
    except Exception as exc:  # noqa: BLE001
        print(f"  Footballguys FAILED: {exc}")
        fbg_pool = []

    crosswalk = fetch_crosswalk()
    registry = PlayerRegistry()
    identities = reconcile_week(dk_pool, pff_pool, rg_pool, fbg_pool, crosswalk, registry)
    print(f"  {len(identities)} identities reconciled.\n")

    print("=== Match rate by snapshot date (staleness over time) ===")
    for snapshot in snapshots:
        comparison = compare_injury_sources(identities, snapshot.entries, week_official_entries)
        rate = comparison.match_rate
        rate_str = f"{rate:.1%}" if rate is not None else "n/a (0 comparable players)"
        print(
            f"  {snapshot.date} (fetched_at={snapshot.fetched_at}): "
            f"n_comparable={len(comparison.both)}, match_rate={rate_str}, "
            f"only_rg={len(comparison.only_rg)}, only_official={len(comparison.only_official)}"
        )


if __name__ == "__main__":
    main()
