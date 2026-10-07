"""Live script: builds and renders one week's real, reusable postmortem -- lineup outcomes (all 6
agents + Chris's own played L1/L2/L3), the real DK contest results, the season-to-date record, the
chalk-proxy comparison, the process grade, player exposure, positional bias, each agent's
stack-thesis-hit review, and ceiling patterns. This is the one real path from week 3 on (unlike
week 2, which had no slate snapshot yet and needed `scripts/reconstruct_week2_postmortem.py`'s
one-off HTML-parsing reconstruction) -- run this same script, unmodified, every week; only
SEASON/WEEK change.

Requires: a real slate snapshot for the target week (`storage/slate_snapshot_store.py`, written
automatically by `live_integration_check_dashboard.py`'s own live run), real settled data for the
week's games (nflverse), and -- for the contest-results/season-record sections -- that week's
operator rows already logged via `scripts/log_operator_contest_results.py`. Missing either of the
latter two just renders those sections empty; it doesn't block the rest of the page.

**Also backfills `agent_results.csv`'s `total_dk_score`/`lineup_rank` for this week before reading
season records** (the same join `scripts/collect_agent_results.py` does standalone) -- caught live
on week 3: `tracking/postmortem/replay.py` computes each lineup's real actual total ITSELF, live,
every run, but never writes it back to the CSV (`log_operator_contest_results.py` forward-logs
`total_dk_score` as blank on purpose, "join later"). `season_record.py` reads that CSV column
directly, not `replay.py`'s output -- so without this backfill, a week's season record silently
stays blind to that week's own real scores forever (this week's own `best_week`/`worst_week`/
`avg_delta` all only reflected week 2 until this was added). Running `collect_agent_results.py`
separately is no longer required; this script does it every time, so the CSV can't drift stale.

Run by hand once a week's games are done (NOT part of pytest -- hits live nflverse data):
    PYTHONPATH=. .venv/bin/python scripts/run_postmortem.py

SEASON/WEEK below must be kept current by hand, same convention as every other live script in
this project.
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl

from nfl_dfs.ingestion.offense_actual_scoring import fetch_weekly_player_stats
from nfl_dfs.storage.contest_results_store import read_contest_results
from nfl_dfs.tracking.agent_results_collector import score_and_backfill_agent_results
from nfl_dfs.tracking.postmortem.replay import run_postmortem
from nfl_dfs.tracking.postmortem_renderer import render_postmortem_html
from nfl_dfs.tracking.season_record import compute_season_records

SEASON = 2026
WEEK = 4

OUTPUT_PATH = "dashboard_output/postmortem.html"


def main() -> None:
    print(f"Fetching real settled data for season={SEASON}...")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        weekly = fetch_weekly_player_stats(SEASON)
        pbp = nfl.import_pbp_data([SEASON], include_participation=False)

    report = run_postmortem(SEASON, WEEK, weekly=weekly, pbp=pbp)
    if report is None:
        print(f"No slate snapshot found for season={SEASON} week={WEEK} -- run live_integration_check_dashboard.py first.")
        return

    scored = sum(1 for lo in report.lineup_outcomes if lo.actual_total is not None)
    print(f"Scored {scored}/{len(report.lineup_outcomes)} lineup(s).")
    if report.process_grade:
        print(f"Process grade: {report.process_grade.letter} ({report.process_grade.summary})")
    if report.chalk_comparison and not report.chalk_comparison.infeasible:
        print(f"Chalk comparison: our best {report.chalk_comparison.our_actual}, chalk {report.chalk_comparison.chalk_actual}")
    elif report.chalk_comparison:
        print(f"Chalk comparison unavailable: {report.chalk_comparison.reason}")

    backfill = score_and_backfill_agent_results(SEASON, WEEK, weekly=weekly, pbp=pbp)
    backfilled_count = sum(1 for r in backfill.scored if r.total_dk_score is not None)
    print(f"Backfilled agent_results.csv: {backfilled_count}/{len(backfill.scored)} row(s) scored for season={SEASON} week={WEEK}.")
    for agent_id, strategy_name, missing in backfill.unresolved:
        print(f"  UNRESOLVED {agent_id}/{strategy_name}: could not match {list(missing)}")

    contest_results = tuple(read_contest_results(season=SEASON, week=WEEK))
    season_records = tuple(compute_season_records(SEASON))
    print(f"{len(contest_results)} real contest entries this week; {len(season_records)} season-to-date records.")

    html_out = render_postmortem_html(report, contest_results=contest_results, season_records=season_records)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html_out)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
