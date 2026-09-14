"""CLI entry point for the live ownership/leverage layer (PRD Section 5 step 7, ADR-0026).

Pulls this week's live LineupHQ projected ownership, joins it against the historical
field-ownership-by-salary production calibration (ADR-0025), and prints the real chalk and leverage flags
for the current slate. Live network call -- needs `ROTOGRINDERS_SESSION_COOKIE` configured.

Usage:
    .venv/bin/python scripts/ownership_leverage_report.py
"""

from __future__ import annotations

from nfl_dfs.analysis.ownership_calibration import run_full_calibration
from nfl_dfs.ingestion.rotogrinders import fetch_rotogrinders_ownership, filter_to_main_slate
from nfl_dfs.ownership.leverage import build_leverage_assessments


def main() -> None:
    print("Fetching live LineupHQ projected ownership...")
    all_rows = fetch_rotogrinders_ownership()
    rows = filter_to_main_slate(all_rows)
    print(f"  {len(all_rows)} players pulled across every slate window, {len(rows)} on the main slate")

    print("Loading production ownership calibration from curated ResultsDB storage...")
    bundle = run_full_calibration()

    assessments = build_leverage_assessments(rows, bundle.production)
    assessments.sort(key=lambda a: a.projected_ownership, reverse=True)

    print(f"\n=== Chalk ({sum(1 for a in assessments if a.is_chalk)}) ===")
    for a in assessments:
        if a.is_chalk:
            print(f"  {a.name:<28} {a.position:>3}  ${a.salary}  {a.projected_ownership:.1f}% proj")

    print(f"\n=== Leverage ({sum(1 for a in assessments if a.is_leverage)}) ===")
    for a in sorted((a for a in assessments if a.is_leverage), key=lambda a: a.ownership_vs_baseline):
        print(
            f"  {a.name:<28} {a.position:>3}  ${a.salary}  {a.projected_ownership:.1f}% proj  "
            f"vs {a.baseline_ownership:.1f}% baseline ({a.ownership_vs_baseline:+.1f}pt)"
        )


if __name__ == "__main__":
    main()
