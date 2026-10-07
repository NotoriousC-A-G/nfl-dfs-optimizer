"""Fits the tail-value calibration cells from ResultsDB history and prints the out-of-sample check
(fit on earlier seasons, test on the latest). Reproduces the numbers quoted in the pool-solve PR and
caches the fitted table to `data/cache/tail_value_cells.json` (gitignored) for the pipeline to load.

    PYTHONPATH=. .venv/bin/python scripts/tail_value_calibration_report.py [TEST_SEASON]

Read-only against `data/curated/resultsdb`. NOT part of `pytest` (needs the local ResultsDB backfill).
"""

from __future__ import annotations

import sys
from pathlib import Path

from nfl_dfs.build.value.calibration import fit_cells, holdout_check, load_resultsdb_player_rows

CACHE = Path("data/cache/tail_value_cells.json")


def main() -> None:
    test_season = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    df = load_resultsdb_player_rows()
    if df.empty:
        raise SystemExit("No ResultsDB player rows on disk -- run the backfill first (scripts/resultsdb_backfill.py).")
    print(f"{len(df):,} player rows across seasons {sorted(int(s) for s in df['season'].unique())}")
    h = holdout_check(df, test_season=test_season)
    print(f"\nOut-of-sample check: fit on {h['train_seasons']}, test on {h['test_season']} (n={h['n']:,})")
    print(f"  raw projection bias (actual/projected):   {h['raw_bias']:.3f}")
    print(f"  calibrated-mean bias (actual/mu):         {h['calibrated_bias']:.3f}   (1.000 = perfectly calibrated)")
    print(f"  share of actuals above the predicted q90: {h['share_above_q90']:.3f}   (0.100 = perfectly calibrated)")
    for pos, v in h["by_position"].items():
        print(f"    {pos:3} n={v['n']:4d}  raw {v['raw_bias']:.3f}  calibrated {v['calibrated_bias']:.3f}  above-q90 {v['share_above_q90']:.3f}")
    table = fit_cells(df)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(table.to_json())
    print(f"\nFitted on all seasons {list(table.fitted_seasons)} -> {CACHE}")
    for pos, cells in table.cells.items():
        print(f"  {pos}: " + "  ".join(f"[{('-inf' if c.lo < -1e9 else f'{c.lo:.1f}')},{('inf' if c.hi > 1e9 else f'{c.hi:.1f}')}) n={c.n} mean x{c.mean_ratio:.2f} q90 x{c.q90_ratio:.2f}" for c in cells))


if __name__ == "__main__":
    main()
