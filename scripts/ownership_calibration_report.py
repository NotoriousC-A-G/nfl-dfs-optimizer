"""CLI entry point for the per-season ownership-propensity calibration (ADR-0025).

Fits every season present in curated ResultsDB storage, runs the leave-one-out stability test, selects the
production calibration per position, and prints a report. Read-only against `data/curated/resultsdb/nfl/`
(ADR-0024) -- writes nothing.

Usage:
    .venv/bin/python scripts/ownership_calibration_report.py
"""

from __future__ import annotations

from nfl_dfs.analysis.ownership_calibration import run_full_calibration


def main() -> None:
    bundle = run_full_calibration()

    print("=== Per-season salary-ownership correlation ===")
    for season in sorted(bundle.season_calibrations):
        calib = bundle.season_calibrations[season]
        flag = "  [REGIME-FLAGGED: " + calib.regime_note + "]" if calib.is_regime_flagged else ""
        print(f"\nSeason {season}{flag}")
        for position, curve in calib.curves.items():
            corr = f"{curve.salary_ownership_correlation:+.3f}" if curve.salary_ownership_correlation is not None else "n/a"
            print(f"  {position:>3}  r={corr}  n_rows={curve.n_rows:>5}  n_contests={curve.n_contests:>3}")

    print("\n=== Stability test (leave-one-out) ===")
    for position, stab in sorted(bundle.stability.items()):
        verdict = "STABLE" if stab.is_stable else f"UNSTABLE (seasons: {stab.unstable_seasons})"
        print(
            f"  {position:>3}  mean_r={stab.mean_correlation:+.3f}  std_r={stab.std_correlation:.3f}  {verdict}"
        )

    print("\n=== Production calibration ===")
    for position, prod in sorted(bundle.production.items()):
        mode = "blended (all seasons)" if prod.blended else "recent-window only"
        print(f"  {position:>3}  {mode}  source_seasons={prod.source_seasons}  n_rows={prod.n_rows}")
        deciles = "  ".join(f"{d}:{prod.decile_ownership.get(d, 0.0):.2f}" for d in range(10))
        print(f"       decile ownership (0=highest salary): {deciles}")


if __name__ == "__main__":
    main()
