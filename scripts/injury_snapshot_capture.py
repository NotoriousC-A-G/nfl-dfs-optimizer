"""Captures today's RotoGrinders Situation Room injury report into the longitudinal snapshot
archive (ADR-0031's named follow-on, built ADR-0038, `storage/injury_snapshot_store.py`).

**Meant to be run on a recurring (e.g. daily) cadence** -- a cron job or equivalent -- so the
archive actually accumulates a real multi-day time series across a week, not a single one-shot
pull. A single run just adds (or overwrites, if already run today) one calendar date's snapshot;
the retrospective payoff (`scripts/injury_snapshot_retrospective_check.py`) only exists once
enough of these have accumulated across a real week.

Update `SEASON`/`TARGET_WEEK` below each week, same convention every other live script in this
project uses (`live_integration_check_dashboard.py`, `injury_staleness_check.py`) -- Situation
Room's own CSV export carries no season/week field (confirmed live,
`ingestion/rotogrinders_injuries.py`'s own investigation: no query param changes the response), so
this project has to supply that context itself at capture time.

NOT part of `pytest` -- needs live RotoGrinders credentials. Run by hand (or on a schedule):

    PYTHONPATH=. .venv/bin/python scripts/injury_snapshot_capture.py
"""

from __future__ import annotations

import datetime as dt

from nfl_dfs.ingestion.rotogrinders_injuries import fetch_injury_report
from nfl_dfs.storage.injury_snapshot_store import has_snapshot, write_snapshot

SEASON = 2026
TARGET_WEEK = 1


def main() -> None:
    today = dt.date.today().isoformat()
    print(f"=== Injury snapshot capture -- {today}, season={SEASON}, target_week={TARGET_WEEK} ===")

    if has_snapshot(today):
        print(f"  A snapshot for {today} already exists -- this run will overwrite it (idempotent per-date write, same posture as storage/resultsdb_store.py).")

    entries = fetch_injury_report()
    print(f"  {len(entries)} row(s) fetched, STATUS codes observed: {sorted({e.status for e in entries})}")

    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    path = write_snapshot(today, SEASON, TARGET_WEEK, entries, fetched_at=fetched_at)
    print(f"  Wrote snapshot to {path}")


if __name__ == "__main__":
    main()
