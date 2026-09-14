"""Manual, live-network integration check for RotoGrinders' "Situation Room" injury report
(`ingestion/rotogrinders_injuries.py`, new this round). NOT part of `pytest` -- needs a live,
configured `ROTOGRINDERS_SESSION_COOKIE` and a live NFL slate, same pattern as
`live_integration_check.py` and `live_integration_check_environment.py`. Run by hand:

    .venv/bin/python scripts/live_integration_check_injuries.py

Prints the full STATUS code set actually observed this pull (the task brief asked for this
explicitly -- don't assume "O"/"Q"/"D" is the complete set), a sample of rows by severity, and a
live re-check of the grid-id-stability finding (does `/grids/<id>` still resolve to the same
slug'd CMS post, confirming it's still the stable evergreen id this module hardcodes rather than
a per-week value that needs rediscovery).
"""

from __future__ import annotations

from collections import Counter

import requests

from nfl_dfs.config import config
from nfl_dfs.ingestion.rotogrinders_injuries import (
    INJURY_GRID_ID,
    fetch_injury_report,
)


def check_injury_report() -> None:
    print(f"\n=== RotoGrinders Situation Room injury report (grid id {INJURY_GRID_ID}) ===")
    entries = fetch_injury_report()
    print(f"{len(entries)} player rows returned.")

    statuses = Counter(e.status for e in entries)
    print(f"\nFull STATUS code set observed this pull: {dict(statuses)}")
    unexpected = set(statuses) - {"O", "Q", "D", "P"}
    if unexpected:
        print(f"  NOTE: status code(s) not anticipated by the task brief at all: {unexpected}")
    if "D" not in statuses:
        print("  NOTE: 'D' (Doubtful) was NOT observed this pull -- same as the prior live check.")
    if "P" in statuses:
        print(
            "  ACTION NEEDED: 'P' (Probable) WAS observed this pull -- this confirms it's a real "
            "code for this source. Add it to RESOLVED_INJURY_STATUSES in game_environment/"
            "score.py (Probable is a known/expected-to-play outcome, not genuine uncertainty -- "
            "same category as 'O', unlike Questionable/Doubtful). See that module's "
            "RESOLVED_INJURY_STATUSES comment and rotogrinders_injuries.py's module docstring for "
            "the prior investigation that could not confirm this."
        )
    else:
        print(
            "  'P' (Probable) NOT observed this pull -- consistent with the prior finding that "
            "this source's page copy names only out/doubtful/questionable. Still not proof it "
            "never appears; re-run this check on a later-in-the-week pull (more practice-report-"
            "driven statuses) before concluding it's fully ruled out."
        )

    print("\nTop 10 by IMPACTRTG:")
    for e in sorted(entries, key=lambda e: -e.impact_rating)[:10]:
        print(f"  {e.impact_rating:>2}  {e.status}  {e.name:<24} {e.team:<4} {e.position:<3} {e.body_part}")


def check_grid_id_stability() -> None:
    print(f"\n=== Grid id stability re-check (id {INJURY_GRID_ID}) ===")
    cookie = config.rotogrinders_session_cookie
    if not cookie:
        print("  SKIPPED: ROTOGRINDERS_SESSION_COOKIE not configured.")
        return
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}
    resp = requests.get(f"https://rotogrinders.com/grids/{INJURY_GRID_ID}", headers=headers, timeout=20)
    print(f"  GET /grids/{INJURY_GRID_ID} -> {resp.status_code}, resolved to {resp.url}")
    if "situation-room" in resp.url and INJURY_GRID_ID in resp.url:
        print("  Still resolves to the same evergreen Situation Room CMS post -- consistent with")
        print("  the module docstring's 'stable, hardcoded, season-long id' conclusion.")
    else:
        print("  UNEXPECTED: did not resolve the way it did when this module was built -- the")
        print("  grid-id-stability finding may need re-checking; do not assume the hardcoded id")
        print("  is still correct without investigating further.")


def main() -> None:
    check_injury_report()
    check_grid_id_stability()


if __name__ == "__main__":
    main()
