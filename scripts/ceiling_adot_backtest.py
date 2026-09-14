"""Live outcome backtest for `CeilingMultiplier` Component C (WR/TE trailing depth-of-target,
aDOT), ADR-0028.

Same methodology as Components A/B (`scripts/ceiling_role_share_backtest.py`, whose DK scoring and
regression helpers this script reuses rather than restating), with one real difference in shape:
Component C is a LEVEL signal (trailing mean depth-of-target), not a boom-rate signal -- per the
Model Analytics Expert's original framing, a receiver who is consistently thrown deep has real
ceiling regardless of week-to-week volatility in that depth, so the calibration question here is
simply "does a higher (shrunk, cross-sectional) aDOT z-score predict better relative-to-own-median
DK performance," the same regression form (`log(relative_performance) ~ shrunk_z`) applied to a
different underlying signal.

`position_by_player_id` (WR/TE split, `adot_ceiling_signals`'s own requirement) is built from
`nfl_data_py.import_weekly_data()`'s real `position` column for that season -- the same weekly
pull already used for DK scoring, no new data source.

NOT part of `pytest` -- a one-time live-data research pass, run by hand:

    PYTHONPATH=. .venv/bin/python scripts/ceiling_adot_backtest.py
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl
import numpy as np
import pandas as pd

from nfl_dfs.ceiling.signals import adot_ceiling_signals
from scripts.ceiling_role_share_backtest import (
    BOOM_OUTCOME_MULTIPLE,
    MAX_TARGET_WEEK,
    MIN_TARGET_WEEK,
    N_DECILES,
    SEASONS,
    _linear_fit,
    _linear_fit_clustered_se,
    _linear_fit_with_se,
    dk_points_row,
)

POSITIONS = ("WR", "TE")


def main() -> None:
    all_pairs: list[dict] = []

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

        position_by_player_id = (
            weekly[weekly["position"].isin(POSITIONS)]
            .drop_duplicates(subset=["player_id"], keep="last")
            .set_index("player_id")["position"]
            .to_dict()
        )
        print(f"  {len(position_by_player_id)} WR/TE player_id(s) with a known position this season")

        for target_week in range(MIN_TARGET_WEEK, min(MAX_TARGET_WEEK, max_week_this_season + 1)):
            trailing_weekly = weekly[weekly["week"] < target_week]
            actual_weekly = weekly[weekly["week"] == target_week]
            if actual_weekly.empty:
                continue
            trailing_median = trailing_weekly.groupby("player_id")["dk_points"].median()
            actual_points = actual_weekly.set_index("player_id")["dk_points"]

            signals_by_position = adot_ceiling_signals(pbp, target_week, position_by_player_id)
            for position in POSITIONS:
                for signal in signals_by_position.get(position, []):
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
                            "season": season, "week": target_week, "role": position,
                            "player_id": signal.player_id,
                            "shrunk_z": signal.shrunk_z_score,
                            "boom": boom,
                            "relative_performance": actual / median,
                            "raw_adot": signal.raw_value,
                        }
                    )
        print(f"  {len(all_pairs)} cumulative (signal, outcome) pairs so far")

    df = pd.DataFrame(all_pairs)
    print(f"\n=== Total Component C sample: {len(df)} (position, player, week) observations ===\n")

    for position in POSITIONS:
        role_df = df[df["role"] == position].copy()
        role_df["decile"] = pd.qcut(role_df["shrunk_z"], N_DECILES, labels=False, duplicates="drop")
        summary = role_df.groupby("decile").agg(
            n=("boom", "size"), boom_rate=("boom", "mean"), mean_shrunk_z=("shrunk_z", "mean"),
            mean_raw_adot=("raw_adot", "mean"),
        )
        print(f"--- {position}: boom rate + raw aDOT by shrunk-z decile (0=lowest z, {N_DECILES - 1}=highest z) ---")
        print(summary.to_string(float_format=lambda v: f"{v:.4f}"))
        z = summary["mean_shrunk_z"].to_numpy()
        boom_slope, boom_r2 = _linear_fit(z, summary["boom_rate"].to_numpy())
        print(f"  Decile-level fit -- boom_rate ~ shrunk_z: slope={boom_slope:.4f}  R2={boom_r2:.3f}")

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
        print(f"  --> exp(slope) = {np.exp(log_slope):.4f}")

        clustered_slope, clustered_se, clustered_ci, n_clusters = _linear_fit_clustered_se(
            player_z, log_perf, positive["player_id"].to_numpy()
        )
        print(
            f"  Cluster-robust fit (player_id clusters, G={n_clusters}) -- "
            f"slope={clustered_slope:.4f}  SE={clustered_se:.4f}  95% CI=[{clustered_ci[0]:.4f}, {clustered_ci[1]:.4f}]"
        )
        print(f"  --> clears zero: {clustered_ci[0] > 0 or clustered_ci[1] < 0}\n")


if __name__ == "__main__":
    main()
