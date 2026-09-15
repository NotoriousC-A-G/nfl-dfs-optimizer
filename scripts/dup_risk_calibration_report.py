"""CLI entry point for the per-season dup-risk calibration (ADR-0033).

Fits every season present in curated ResultsDB lineups storage (ADR-0032), prints the ownership-decile
dup-rate curve and per-trend dup-rate splits, the leave-one-out stability test, and the resulting
production calibration. Read-only against `data/curated/resultsdb/nfl/` -- writes nothing.

Usage:
    .venv/bin/python scripts/dup_risk_calibration_report.py
"""

from __future__ import annotations

from nfl_dfs.analysis.dup_risk_calibration import (
    compare_season_dup_calibrations,
    run_dup_risk_calibration,
    select_production_dup_calibration,
)


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

    calibrations = list(bundle.values())
    if len(calibrations) >= 3:
        print("\n=== Leave-one-out stability test (ADR-0025's method, reused) ===")
        stability = compare_season_dup_calibrations(calibrations)
        for season in sorted(stability.season_correlations):
            print(f"  {season}: r={stability.season_correlations[season]:+.4f}")
        print(f"  mean={stability.mean_correlation:+.4f}  std={stability.std_correlation:.4f}")
        verdict = "STABLE" if stability.is_stable else f"UNSTABLE (seasons: {stability.unstable_seasons})"
        print(f"  verdict: {verdict}")

        print("\n=== Production calibration ===")
        production = select_production_dup_calibration(calibrations, stability)
        mode = "blended (all seasons)" if production.blended else "recent-window only"
        print(f"  {mode}  source_seasons={production.source_seasons}  n_rows={production.n_rows}")
        deciles = "  ".join(f"{d}:{production.decile_dup_rate.get(d, 0.0):.4f}" for d in range(10))
        print(f"  decile dup-rate (0=highest avg ownership): {deciles}")
    else:
        print(f"\n=== Stability test skipped: only {len(calibrations)} season(s) fit, need >= 3 ===")


if __name__ == "__main__":
    main()
