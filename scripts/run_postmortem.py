"""Live script: builds and renders one week's real postmortem -- lineup outcomes (all 6 agents +
Chris's own played lineups), the chalk-proxy comparison, the process grade, and ceiling patterns.

Requires a real slate snapshot for the target week (`storage/slate_snapshot_store.py`, written
automatically by `live_integration_check_dashboard.py`'s own live run) and real settled data for
the week's games (nflverse).

Run by hand once a week's games are done (NOT part of pytest -- hits live nflverse data):
    PYTHONPATH=. .venv/bin/python scripts/run_postmortem.py

SEASON/WEEK below must be kept current by hand, same convention as every other live script in
this project.
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl

from nfl_dfs.tracking.postmortem.replay import run_postmortem
from nfl_dfs.tracking.postmortem_renderer import render_postmortem_html

SEASON = 2026
WEEK = 2

OUTPUT_PATH = "dashboard_output/postmortem.html"


def main() -> None:
    print(f"Fetching real settled data for season={SEASON}...")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        weekly = nfl.import_weekly_data([SEASON])
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

    html = render_postmortem_html(report)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
