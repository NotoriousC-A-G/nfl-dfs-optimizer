"""Live script: backfills `data/agent-performance/agent_results.csv`'s `total_dk_score`/
`lineup_rank` for one settled week, then renders the Agent Performance table.

Run by hand once a week's games are done (NOT part of pytest -- hits live nflverse data):
    PYTHONPATH=. .venv/bin/python scripts/collect_agent_results.py

SEASON/WEEK below must be kept current by hand, same convention as every other live script in
this project (see CLAUDE.md) -- there's no auto-detection of "which week just finished."
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl

from nfl_dfs.storage.agent_results_store import read_agent_results
from nfl_dfs.tracking.agent_results_collector import score_and_backfill_agent_results
from nfl_dfs.tracking.agent_results_renderer import render_agent_performance_html

SEASON = 2026
WEEK = 2

OUTPUT_PATH = "dashboard_output/agent_performance.html"


def main() -> None:
    before = read_agent_results(season=SEASON, week=WEEK)
    if not before:
        print(f"No agent_results.csv rows logged for season={SEASON} week={WEEK} -- nothing to score.")
        return

    print(f"Fetching real settled data for season={SEASON}...")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        weekly = nfl.import_weekly_data([SEASON])
        pbp = nfl.import_pbp_data([SEASON], include_participation=False)

    result = score_and_backfill_agent_results(SEASON, WEEK, weekly=weekly, pbp=pbp)

    scored_count = sum(1 for r in result.scored if r.total_dk_score is not None)
    print(f"Scored {scored_count}/{len(result.scored)} row(s) for season={SEASON} week={WEEK}.")
    for agent_id, strategy_name, missing in result.unresolved:
        print(f"  UNRESOLVED {agent_id}/{strategy_name}: could not match {list(missing)}")

    all_rows_this_week = read_agent_results(season=SEASON, week=WEEK)
    html = render_agent_performance_html(all_rows_this_week, season=SEASON, week=WEEK)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
