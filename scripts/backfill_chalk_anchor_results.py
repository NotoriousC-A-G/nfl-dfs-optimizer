"""One-off/repeatable: logs Chalk Anchor's lineup for weeks it was never forward-logged, from each
week's saved pre-lock slate snapshot, then scores those rows against settled nflverse data
(same join `run_postmortem.py` does). Chalk Anchor survived the ADR-0046 redesign as the baseline
agent, so its season record should cover every week, not just week 2.

Idempotent. WEEKS must be kept current by hand, like every other live script here.
    PYTHONPATH=. .venv/bin/python scripts/backfill_chalk_anchor_results.py
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl

from nfl_dfs.ingestion.offense_actual_scoring import fetch_weekly_player_stats
from nfl_dfs.storage.agent_results_store import save_agent_results
from nfl_dfs.storage.slate_snapshot_store import load_latest_slate_snapshot
from nfl_dfs.tracking.agent_results_collector import score_and_backfill_agent_results
from nfl_dfs.tracking.redesign_agent_log import build_chalk_anchor_row

SEASON = 2026
WEEKS = (3, 4, 5)


def main() -> None:
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        weekly = fetch_weekly_player_stats(SEASON)
        pbp = nfl.import_pbp_data([SEASON], include_participation=False)

    for week in WEEKS:
        snapshot = load_latest_slate_snapshot(SEASON, week)
        row = build_chalk_anchor_row(snapshot, SEASON, week) if snapshot else None
        if row is None:
            print(f"week {week}: no snapshot / no Chalk Anchor lineup -- skipped")
            continue
        save_agent_results([row])
        result = score_and_backfill_agent_results(SEASON, week, weekly=weekly, pbp=pbp)
        scored = next((r for r in result.scored if r.agent_id == "chalk_anchor"), None)
        miss = [u for u in result.unresolved if u[0] == "chalk_anchor"]
        print(f"week {week}: proj {row.proj_total} -> actual {scored.total_dk_score if scored else None} {miss or ''}")


if __name__ == "__main__":
    main()
