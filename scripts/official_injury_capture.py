"""Captures the official NFL injury report (nflverse `import_injuries`, EVERY position -- OL/DL/CB/S
included) into `storage/official_injury_snapshot_store.py`. Run Wednesday, Thursday and Friday (and
Saturday/Sunday morning) so the practice-status trajectory exists: the feeds keep only the latest day.
The Friday capture is what the automatic Questionable-player decisions read (ADR-0045).

Update `SEASON`/`TARGET_WEEK` each week, same convention as every other live script here.
NOT part of `pytest` (live network). Run by hand or on a schedule:

    PYTHONPATH=. .venv/bin/python scripts/official_injury_capture.py
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

from nfl_dfs.ingestion.official_injury_report import fetch_official_injury_report
from nfl_dfs.storage.official_injury_snapshot_store import OfficialInjurySnapshot, write_snapshot

SEASON = 2026
TARGET_WEEK = 5


def main() -> None:
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    print(f"=== Official injury capture -- {fetched_at}, season={SEASON}, week={TARGET_WEEK} ===")
    entries = [e for e in fetch_official_injury_report(SEASON) if e.week == TARGET_WEEK]
    teams = sorted({e.team for e in entries})
    print(f"  {len(entries)} row(s) for week {TARGET_WEEK} across {len(teams)} team(s): {', '.join(teams) or 'none yet'}")
    print(f"  practice status: {dict(Counter(e.practice_status for e in entries))}")
    print(f"  game status:     {dict(Counter(e.report_status for e in entries))}")
    if len(teams) < 32:
        print(f"  NOTE: only {len(teams)}/32 teams have posted -- later captures will fill in the rest.")
    path = write_snapshot(OfficialInjurySnapshot(fetched_at, SEASON, TARGET_WEEK, entries))
    print(f"  Wrote {path}")


if __name__ == "__main__":
    main()
