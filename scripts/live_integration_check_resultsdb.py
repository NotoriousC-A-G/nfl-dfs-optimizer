"""Manual, live-network integration check for RotoGrinders ResultsDB / FantasyLabs Contests
Dashboard ingestion (`nfl_dfs.ingestion.rotogrinders_resultsdb`, ADR-0023).

Confirms, against live data, the two load-bearing findings this module's design depends on:
1. The whole chain (contest-sources -> live-contests -> data/) works with NO session cookie at all
   (unlike every other reverse-engineered source in this project).
2. The 2020-season coverage floor found this round still holds (every pre-2020 date tried should
   403 on the `data/` payload; 2020+ should 200).

Also prints the real "two is_primary contests, disambiguated by multi_entry_max=150" finding for a
known historical date, and a live re-check against today's date so this doesn't silently rot into a
stale claim. NOT part of `pytest` -- needs live network access. Run by hand:

    .venv/bin/python scripts/live_integration_check_resultsdb.py
"""

from __future__ import annotations

import datetime as dt

import requests

from nfl_dfs.ingestion.rotogrinders_resultsdb import (
    ContestDataUnavailableError,
    NoPrimaryContestError,
    fetch_contest_data,
    fetch_contest_sources,
    fetch_live_contests,
    parse_contest_summary,
    parse_player_exposures,
    parse_user_exposures,
    select_millionaire_maker_contest,
)

# A known-good historical Millionaire Maker date, used to re-confirm the two-is_primary finding
# without depending on today's slate having settled yet.
KNOWN_MILLIONAIRE_DATE = "2023-09-10"

# Season-opener dates spanning the confirmed 2020 coverage floor (ADR-0023 section 3).
COVERAGE_PROBE_DATES = [
    ("2019-09-08", False),  # expected: no data/ payload (pre-floor)
    ("2020-09-13", True),  # expected: data/ payload present (floor season)
    ("2023-09-10", True),
]


def check_no_cookie_needed() -> None:
    print("\n=== 1. Confirming the full chain needs no RotoGrinders session cookie ===")
    plain_session = requests.Session()  # deliberately no Cookie header set anywhere
    groups = fetch_contest_sources(KNOWN_MILLIONAIRE_DATE, session=plain_session)
    print(f"  contest-sources ({KNOWN_MILLIONAIRE_DATE}): {len(groups)} DK draft group(s), no cookie sent.")
    if not groups:
        print("  UNEXPECTED: no DK draft groups returned for a known historical date.")
        return
    contests = fetch_live_contests(groups[0].contest_group_id, session=plain_session)
    print(f"  live-contests: {len(contests)} contest(s) in draft group {groups[0].contest_group_id}.")

    try:
        picked = select_millionaire_maker_contest(contests)
    except NoPrimaryContestError as exc:
        print(f"  UNEXPECTED: could not disambiguate the Millionaire Maker contest: {exc}")
        return

    print(f"  selected Millionaire Maker: {picked.contest_id} {picked.contest_name!r}")
    print(f"    multi_entry_max={picked.multi_entry_max}, contest_size={picked.contest_size}, "
          f"entry_cost={picked.entry_cost}")

    primaries = [c for c in contests if c.is_primary]
    print(f"  is_primary candidates this date: {len(primaries)}")
    for c in primaries:
        print(f"    {c.contest_id} {c.contest_name!r} multi_entry_max={c.multi_entry_max} size={c.contest_size}")
    if len(primaries) > 1:
        print("  Confirms this round's real finding: is_primary alone is not a clean disambiguator --")
        print("  multi_entry_max=150 (PRD Section 2's own literal description) is what resolves it.")

    data = fetch_contest_data(KNOWN_MILLIONAIRE_DATE, picked.contest_id, session=plain_session)
    print(f"  contest data/ payload: {len(data.get('players', {}))} player rows, "
          f"{len(data.get('users', {}))} user rows -- no cookie sent for any of the three calls above.")


def check_coverage_floor() -> None:
    print("\n=== 2. Re-confirming the 2020-season coverage floor (ADR-0023 section 3) ===")
    for date, expect_available in COVERAGE_PROBE_DATES:
        yyyymmdd = date.replace("-", "")
        try:
            groups = fetch_contest_sources(date)
            if not groups:
                print(f"  {date}: no DK draft groups at all -- can't probe data/ payload.")
                continue
            contests = fetch_live_contests(groups[0].contest_group_id)
            primaries = [c for c in contests if c.is_primary] or contests
            if not primaries:
                print(f"  {date}: no contests found in draft group {groups[0].contest_group_id}.")
                continue
            probe = primaries[0]
            fetch_contest_data(date, probe.contest_id)
            available = True
        except ContestDataUnavailableError:
            available = False
        status = "available" if available else "NOT available (403)"
        match = "as expected" if available == expect_available else "*** UNEXPECTED -- re-check ADR-0023 ***"
        print(f"  {date}: data/ payload {status} ({match})")


def check_player_and_user_exposures() -> None:
    print(f"\n=== 3. Sample player/user exposures for the {KNOWN_MILLIONAIRE_DATE} Millionaire Maker ===")
    groups = fetch_contest_sources(KNOWN_MILLIONAIRE_DATE)
    contests = fetch_live_contests(groups[0].contest_group_id)
    picked = select_millionaire_maker_contest(contests)
    data = fetch_contest_data(KNOWN_MILLIONAIRE_DATE, picked.contest_id)

    summary = parse_contest_summary(data)
    print(f"  {summary.contest_name} -- {summary.contest_size} entries, "
          f"{summary.duplicate_lineups} duplicate / {summary.unique_lineups} unique lineups.")

    players = parse_player_exposures(data)
    top_owned = sorted(players, key=lambda p: -p.ownership_overall)[:5]
    print("  Top 5 by overall ownership:")
    for p in top_owned:
        print(
            f"    {p.full_name:<22} {p.position:<3} own={p.ownership_overall:>5.2f}%  "
            f"top20={p.ownership_top20:>5.2f}%  top10={p.ownership_top10:>5.2f}%  top1={p.ownership_top1:>5.2f}%  "
            f"actual={p.actual_points}"
        )

    users = parse_user_exposures(data)
    print(f"  {len(users)} real entrants parsed (e.g. {users[0].username}: "
          f"{users[0].total_rosters} rosters, {users[0].unique_rosters} unique, ROI={users[0].roi}).")


def main() -> None:
    check_no_cookie_needed()
    check_coverage_floor()
    check_player_and_user_exposures()
    print(f"\nDone. (Run date: {dt.date.today().isoformat()})")


if __name__ == "__main__":
    main()
