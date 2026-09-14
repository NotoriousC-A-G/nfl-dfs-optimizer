"""Live outcome backtest for `CeilingMultiplier` Component B (red-zone-share boom-rate), ADR-0028.

Same methodology as Component A's backtest (`scripts/ceiling_role_share_backtest.py`, whose DK
scoring and regression helpers this script reuses rather than restating) -- see that script's
docstring for the full method. Two things specific to Component B:

1. **The Fantasy Football Expert's required design fix is already in `ceiling/signals.py`** before
   this backtest was written: `red_zone_ceiling_signals` now zero-fills real "red-zone shutout"
   weeks (a player active that week, on a team that reached the red zone, who personally got no
   red-zone touch) rather than silently treating them as missing observations -- see that
   function's own docstring and `_zero_fill_red_zone_weekly`.
2. **The Model Analytics Expert's required A/B correlation check** runs alongside the calibration
   backtest: for every (player, week) with a real signal on both components, this script reports
   the correlation between Component A's and Component B's `shrunk_z_score` -- the empirical
   answer to "are these two signals actually independent, or would combining them double-count one
   underlying event," the same shape of question ADR-0005 already resolved for pass-protection/
   coverage.

NOT part of `pytest` -- a one-time live-data research pass, run by hand:

    PYTHONPATH=. .venv/bin/python scripts/ceiling_red_zone_backtest.py
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl
import numpy as np
import pandas as pd

from nfl_dfs.ceiling.signals import red_zone_ceiling_signals, role_share_ceiling_signals
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR
from scripts.ceiling_role_share_backtest import (
    BOOM_OUTCOME_MULTIPLE,
    MAX_TARGET_WEEK,
    MIN_TARGET_WEEK,
    N_DECILES,
    SEASONS,
    _linear_fit,
    _linear_fit_with_se,
    dk_points_row,
)


def main() -> None:
    all_pairs: list[dict] = []
    ab_pairs: list[dict] = []

    for season in SEASONS:
        print(f"=== Season {season} ===")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            pbp = nfl.import_pbp_data([season], include_participation=False)
            try:
                weekly = nfl.import_weekly_data([season])
            except Exception as exc:  # noqa: BLE001
                print(f"  SKIPPING season {season}: import_weekly_data failed live ({exc})")
                continue
        weekly = weekly[weekly["season_type"] == "REG"].copy()
        weekly["dk_points"] = weekly.apply(dk_points_row, axis=1)
        max_week_this_season = int(weekly["week"].max())

        for target_week in range(MIN_TARGET_WEEK, min(MAX_TARGET_WEEK, max_week_this_season + 1)):
            trailing_weekly = weekly[weekly["week"] < target_week]
            actual_weekly = weekly[weekly["week"] == target_week]
            if actual_weekly.empty:
                continue
            trailing_median = trailing_weekly.groupby("player_id")["dk_points"].median()
            actual_points = actual_weekly.set_index("player_id")["dk_points"]

            for role in (ROLE_RB, ROLE_WR):
                b_signals = red_zone_ceiling_signals(pbp, target_week, role)
                a_signals = {s.player_id: s for s in role_share_ceiling_signals(pbp, target_week, role)}

                for signal in b_signals:
                    a_signal = a_signals.get(signal.player_id)
                    if a_signal is not None and a_signal.shrunk_z_score is not None and signal.shrunk_z_score is not None:
                        ab_pairs.append(
                            {
                                "season": season, "week": target_week, "role": role,
                                "player_id": signal.player_id,
                                "a_shrunk_z": a_signal.shrunk_z_score,
                                "b_shrunk_z": signal.shrunk_z_score,
                            }
                        )

                    if signal.shrunk_z_score is None:
                        continue
                    if signal.player_id not in trailing_median.index or signal.player_id not in actual_points.index:
                        continue
                    median = trailing_median[signal.player_id]
                    if median <= 0:
                        continue
                    actual = actual_points[signal.player_id]
                    boom = 1 if actual > BOOM_OUTCOME_MULTIPLE * median else 0
                    all_pairs.append(
                        {
                            "season": season, "week": target_week, "role": role,
                            "player_id": signal.player_id,
                            "shrunk_z": signal.shrunk_z_score,
                            "boom": boom,
                            "relative_performance": actual / median,
                        }
                    )
        print(f"  {len(all_pairs)} cumulative (signal, outcome) pairs so far, {len(ab_pairs)} A/B pairs")

    df = pd.DataFrame(all_pairs)
    print(f"\n=== Total Component B sample: {len(df)} (role, player, week) observations ===\n")

    for role in (ROLE_RB, ROLE_WR):
        role_df = df[df["role"] == role].copy()
        role_df["decile"] = pd.qcut(role_df["shrunk_z"], N_DECILES, labels=False, duplicates="drop")
        summary = role_df.groupby("decile").agg(
            n=("boom", "size"), boom_rate=("boom", "mean"), mean_shrunk_z=("shrunk_z", "mean")
        )
        print(f"--- {role} role: boom rate by shrunk-z decile (0=lowest z, {N_DECILES - 1}=highest z) ---")
        print(summary.to_string(float_format=lambda v: f"{v:.4f}"))
        z = summary["mean_shrunk_z"].to_numpy()
        boom_slope, boom_r2 = _linear_fit(z, summary["boom_rate"].to_numpy())
        print(f"  Decile-level fit -- boom_rate ~ shrunk_z: slope={boom_slope:.4f}  R2={boom_r2:.3f}")

        median_summary = role_df.groupby("decile")["relative_performance"].median()
        print("  Decile MEDIAN relative_performance by decile:", [round(v, 4) for v in median_summary.to_numpy()])

        positive = role_df[role_df["relative_performance"] > 0]
        excluded = len(role_df) - len(positive)
        log_perf = np.log(positive["relative_performance"].to_numpy())
        player_z = positive["shrunk_z"].to_numpy()
        log_slope, log_r2, log_se, log_ci = _linear_fit_with_se(player_z, log_perf)
        print(
            f"  Player-level fit (n={len(positive)}, {excluded} excluded for relative_performance<=0) "
            f"-- log(relative_performance) ~ shrunk_z: "
            f"slope={log_slope:.4f}  SE={log_se:.4f}  95% CI=[{log_ci[0]:.4f}, {log_ci[1]:.4f}]  R2={log_r2:.3f}"
        )
        print(f"  --> exp(slope) = {np.exp(log_slope):.4f}\n")

    # Model Analytics Expert's required A/B correlation check.
    ab_df = pd.DataFrame(ab_pairs)
    print(f"=== A/B correlation check (n={len(ab_df)} player-weeks with both signals real) ===")
    for role in (ROLE_RB, ROLE_WR):
        role_ab = ab_df[ab_df["role"] == role]
        corr = role_ab["a_shrunk_z"].corr(role_ab["b_shrunk_z"])
        print(f"  {role}: Pearson r(Component A, Component B) = {corr:.4f}  (n={len(role_ab)})")


if __name__ == "__main__":
    main()
