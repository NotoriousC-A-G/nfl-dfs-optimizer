"""CLI entry point for the per-season dup-risk calibration (ADR-0033).

Fits every season present in curated ResultsDB lineups storage (ADR-0032), prints the ownership-decile
dup-rate curve and per-trend dup-rate splits, and a lightweight two-season comparison. Read-only against
`data/curated/resultsdb/nfl/` -- writes nothing.

Usage:
    .venv/bin/python scripts/dup_risk_calibration_report.py
"""

from __future__ import annotations

from nfl_dfs.analysis.dup_risk_calibration import compare_two_seasons, run_dup_risk_calibration


def main() -> None:
    bundle = run_dup_risk_calibration()

    print("=== Per-season ownership-decile dup-rate curve (decile 0 = highest avg ownership) ===")
    for season in sorted(bundle):
        calib = bundle[season]
        oc = calib.ownership_curve
        corr = f"{oc.ownership_dup_rate_correlation:+.4f}" if oc.ownership_dup_rate_correlation is not None else "n/a"
        print(f"\nSeason {season}  (n_contests={oc.n_contests}, n_rows={oc.n_rows}, r(avg_own, is_duplicated)={corr})")
        for d in range(10):
            rate = oc.decile_dup_rate.get(d)
            ct = oc.decile_mean_lineup_ct.get(d)
            print(f"  decile {d}: dup_rate={rate:.4f}  mean_lineup_ct={ct:.3f}" if rate is not None else f"  decile {d}: n/a")

    print("\n=== Per-trend dup-rate splits ===")
    for season in sorted(bundle):
        calib = bundle[season]
        print(f"\nSeason {season}")
        for name, tr in sorted(calib.trend_rates.items()):
            true_str = f"{tr.dup_rate_true:.4f}" if tr.dup_rate_true is not None else "n/a"
            false_str = f"{tr.dup_rate_false:.4f}" if tr.dup_rate_false is not None else "n/a"
            print(f"  {name:<38} true(n={tr.n_true:>7})={true_str}   false(n={tr.n_false:>7})={false_str}")

    print("\n=== Two-season comparison (NOT a stability test -- see module docstring) ===")
    print(compare_two_seasons(list(bundle.values())))


if __name__ == "__main__":
    main()
