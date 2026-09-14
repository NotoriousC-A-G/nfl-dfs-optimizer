"""Live outcome backtest for `CeilingMultiplier` Component A (role-share boom-rate), ADR-0028's
explicit follow-up: neither the Model Analytics Expert nor the Fantasy Football Expert would
propose a `scale_i` (how many multiplier points one unit of z-score is worth) without a real
outcome backtest -- this is that backtest, matching ADR-0012's own decile-bucket,
backtest-before-calibrate precedent (return-TD-rate vs. return-opportunity signal).

**Methodology:** for every (season, target_week) in the window below, compute each RB/WR's
Component A `shrunk_z_score` (`ceiling/signals.py`'s `role_share_ceiling_signals`, using only
weeks `1..target_week-1` -- the exact same no-look-ahead trailing window the live formula would
use) alongside that player's REAL actual outcome in `target_week` itself: did their real DK
fantasy points that week exceed `BOOM_OUTCOME_MULTIPLE`x their own trailing-median DK points (the
same trailing window). Bucket every (z_score, boom_outcome) pair by z-score decile across the full
multi-season sample and report the empirical boom rate per decile -- if the signal is real, boom
rate should climb monotonically-ish from bottom decile to top decile; the size of that climb is
the actual evidence `scale_i` should be calibrated against, not a guess.

DK Classic scoring computed directly from `nfl_data_py.import_weekly_data()`'s real box-score
columns (passing/rushing/receiving yards+TDs, INTs, fumbles lost, 2pt conversions, the 300/100-yard
bonuses) -- the same rule set this project already confirmed live earlier this session, extended
here to the full rushing/receiving side (this project's optimizer/output modules don't have a
standalone `dk_points()` function to reuse; PRD Section 3's scoring table is the source of truth
for every term computed below).

NOT part of `pytest` -- a one-time live-data research pass, run by hand:

    PYTHONPATH=. .venv/bin/python scripts/ceiling_role_share_backtest.py
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl
import numpy as np
import pandas as pd

from nfl_dfs.ceiling.signals import role_share_ceiling_signals
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR

SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]
MIN_TARGET_WEEK = 4
MAX_TARGET_WEEK = 18  # exclusive-ish upper bound -- REG season goes through 18, checked per season
BOOM_OUTCOME_MULTIPLE = 1.5
N_DECILES = 10


def dk_points_row(row: pd.Series) -> float:
    """DK Classic full-scoring formula (PRD Section 3) computed from one `import_weekly_data()`
    row's real box-score columns."""
    pts = 0.0
    pts += row["passing_yards"] * 0.04
    pts += row["passing_tds"] * 4
    pts += row["interceptions"] * -1
    pts += 3.0 if row["passing_yards"] >= 300 else 0.0
    pts += row["rushing_yards"] * 0.1
    pts += row["rushing_tds"] * 6
    pts += 3.0 if row["rushing_yards"] >= 100 else 0.0
    pts += row["receptions"] * 1
    pts += row["receiving_yards"] * 0.1
    pts += row["receiving_tds"] * 6
    pts += 3.0 if row["receiving_yards"] >= 100 else 0.0
    pts += (row["rushing_fumbles_lost"] + row["receiving_fumbles_lost"] + row["sack_fumbles_lost"]) * -1
    pts += (row["passing_2pt_conversions"] + row["rushing_2pt_conversions"] + row["receiving_2pt_conversions"]) * 2
    return pts


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

        for target_week in range(MIN_TARGET_WEEK, min(MAX_TARGET_WEEK, max_week_this_season + 1)):
            trailing_weekly = weekly[weekly["week"] < target_week]
            actual_weekly = weekly[weekly["week"] == target_week]
            if actual_weekly.empty:
                continue
            trailing_median = trailing_weekly.groupby("player_id")["dk_points"].median()
            actual_points = actual_weekly.set_index("player_id")["dk_points"]

            for role in (ROLE_RB, ROLE_WR):
                signals = role_share_ceiling_signals(pbp, target_week, role)
                for signal in signals:
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
                            "season": season,
                            "week": target_week,
                            "role": role,
                            "player_id": signal.player_id,
                            "shrunk_z": signal.shrunk_z_score,
                            "boom": boom,
                            # actual-points-relative-to-own-trailing-median -- the dimensionally
                            # correct target for calibrating a MULTIPLICATIVE ceiling_multiplier
                            # (a probability-of-boom regression alone doesn't translate directly
                            # into "how many points," this ratio does).
                            "relative_performance": actual / median,
                        }
                    )
        print(f"  {len(all_pairs)} cumulative (signal, outcome) pairs so far")

    df = pd.DataFrame(all_pairs)
    print(f"\n=== Total sample: {len(df)} (role, player, week) observations ===\n")

    for role in (ROLE_RB, ROLE_WR):
        role_df = df[df["role"] == role].copy()
        role_df["decile"] = pd.qcut(role_df["shrunk_z"], N_DECILES, labels=False, duplicates="drop")
        summary = role_df.groupby("decile").agg(
            n=("boom", "size"),
            boom_rate=("boom", "mean"),
            mean_shrunk_z=("shrunk_z", "mean"),
            mean_relative_performance=("relative_performance", "mean"),
        )
        print(f"--- {role} role: boom rate + relative performance by shrunk-z decile (0=lowest z, {N_DECILES - 1}=highest z) ---")
        print(summary.to_string(float_format=lambda v: f"{v:.4f}"))
        overall = role_df["boom"].mean()
        top_decile = summary.iloc[-1]["boom_rate"]
        bottom_decile = summary.iloc[0]["boom_rate"]
        print(f"  Overall boom rate: {overall:.4f}")
        print(f"  Top decile vs. overall: {top_decile / overall:.3f}x" if overall > 0 else "  n/a")
        print(f"  Top decile vs. bottom decile: {(top_decile - bottom_decile):.4f} absolute pp difference")

        # Decile-level linear fits -- boom-rate slope is diagnostic only (a probability doesn't
        # translate directly into a points multiplier); the relative-performance slope is the
        # dimensionally correct calibration target for `scale_i` in
        # `m_i = max(1.0, 1 + z_i * scale_i)`, since relative_performance is already expressed on
        # the same "multiple of a player's own trailing median" scale that multiplier is meant to
        # move. Both fit on the 10 decile means (n=10), a coarser method than a full player-level
        # regression -- reported plainly as decile-level, not oversold as more granular than it is.
        z = summary["mean_shrunk_z"].to_numpy()
        boom_slope, boom_r2 = _linear_fit(z, summary["boom_rate"].to_numpy())
        perf_slope, perf_r2 = _linear_fit(z, summary["mean_relative_performance"].to_numpy())
        print(f"  Decile-level fit (mean)   -- boom_rate ~ shrunk_z:            slope={boom_slope:.4f}  R2={boom_r2:.3f}")
        print(f"  Decile-level fit (mean)   -- relative_performance ~ shrunk_z: slope={perf_slope:.4f}  R2={perf_r2:.3f}")

        # Model Analytics Expert's required rerun #1: decile MEDIAN instead of mean -- the mean is
        # outlier-sensitive on right-skewed DK-points data (one huge game distorts a whole decile's
        # mean); the median targets that specific, diagnosed cause of instability directly.
        median_summary = role_df.groupby("decile")["relative_performance"].median()
        median_slope, median_r2 = _linear_fit(z, median_summary.to_numpy())
        print("  Decile MEDIAN relative_performance by decile:", [round(v, 4) for v in median_summary.to_numpy()])
        print(f"  Decile-level fit (MEDIAN) -- relative_performance ~ shrunk_z: slope={median_slope:.4f}  R2={median_r2:.3f}")

        # Model Analytics Expert's required rerun #2: a real player-level regression (n=len(role_df),
        # not n=10 decile means) -- fit in log-space (log(relative_performance) ~ shrunk_z), both
        # because it directly addresses the same right-skew/outlier-sensitivity concern (a log
        # transform compresses the influence of extreme-outlier games) and because it produces a
        # genuinely multiplicative model (relative_performance = exp(a) * exp(b*z)) consistent with
        # this project's own ADR-0005 log-space combination convention, rather than a plain linear
        # model bolted onto a multiplier formula.
        # log() requires strictly positive relative_performance -- a real DK week can be 0 or
        # negative (a fumble-heavy, no-production week), which log-space can't represent. Filtered
        # out for this specific regression only (not the mean/median analyses above), count
        # reported so the exclusion is visible, not silent.
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
        print(f"  --> exp(slope) = {np.exp(log_slope):.4f} (multiplicative reading, e.g. m_i = exp(slope * z_i))\n")


def _linear_fit(x, y) -> tuple[float, float]:
    """Plain-numpy least-squares slope + R^2 (no scipy dependency in this project's venv)."""
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    ss_res = float(((y - predicted) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return float(slope), r2


def _linear_fit_with_se(x, y) -> tuple[float, float, float, tuple[float, float]]:
    """OLS slope, R^2, the slope's standard error, and a 95% CI (normal approximation, no scipy) --
    the player-level regression Model Analytics Expert required before treating any decile-level
    fit as more than a diagnostic.
    """
    n = len(x)
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    residuals = y - predicted
    ss_res = float((residuals**2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    mse = ss_res / (n - 2)
    sxx = float(((x - x.mean()) ** 2).sum())
    se = float(np.sqrt(mse / sxx))
    ci = (slope - 1.96 * se, slope + 1.96 * se)
    return float(slope), r2, se, ci


def _linear_fit_clustered_se(x, y, clusters) -> tuple[float, float, tuple[float, float], int]:
    """OLS slope + cluster-robust standard error (Cameron-Miller sandwich estimator, Stata's
    default small-sample correction `(G/(G-1)) * ((N-1)/(N-K))`) -- the check the Model Analytics
    Expert required before treating a borderline (non-clustered) CI as final: this dataset has
    repeated weekly observations per player, and naive OLS SEs assume independence across those
    repeats, which understates the true SE whenever a player's own weeks are correlated (they are
    -- a player's underlying talent/role/matchup quality doesn't reset every week). No
    statsmodels/scipy dependency, matching this project's existing venv constraint.
    """
    n = len(x)
    design = np.column_stack([np.ones(n), x])
    xtx_inv = np.linalg.inv(design.T @ design)
    beta = xtx_inv @ design.T @ y
    resid = y - design @ beta

    unique_clusters = np.unique(clusters)
    n_clusters = len(unique_clusters)
    meat = np.zeros((2, 2))
    for cluster_id in unique_clusters:
        mask = clusters == cluster_id
        design_g = design[mask]
        resid_g = resid[mask]
        score_g = design_g.T @ resid_g
        meat += np.outer(score_g, score_g)

    k = 2  # intercept + slope
    correction = (n_clusters / (n_clusters - 1)) * ((n - 1) / (n - k))
    vcov = correction * xtx_inv @ meat @ xtx_inv
    slope_se = float(np.sqrt(vcov[1, 1]))
    slope = float(beta[1])
    ci = (slope - 1.96 * slope_se, slope + 1.96 * slope_se)
    return slope, slope_se, ci, n_clusters


if __name__ == "__main__":
    main()
