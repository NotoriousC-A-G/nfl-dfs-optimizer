"""Live outcome backtest for `CeilingMultiplier` Component E candidate (QB explosive-rush rate),
ADR-0028/ADR-0030 -- the Fantasy Football Expert's named successor to Component D's clean null.

**RESULT (2026-09-14): a clean null, if anything more immediately decisive than Component D's --
both the 15yd primary and required 10yd sensitivity check nulled in both TRAIN and HOLDOUT splits,
and a residualized regression showed neither the explosive-rush signal nor scramble share carries
any independent relationship to ceiling outcomes. Both experts signed off on closing this out, and
explicitly recommend stopping the QB-rushing CeilingMultiplier line of work here (three independent
hypotheses now null: Component D's two legs, this one).** Full record:
`docs/adr/0028-ceiling-signal-data-layer.md`'s "Update (2026-09-14): Component E (QB explosive-rush
rate)" section. This script is kept for reproducibility, not because the result is still open.

Same core methodology as prior components' backtests (`scripts/ceiling_role_share_backtest.py`,
whose DK scoring and regression helpers this script reuses rather than restating) -- see that
script's docstring for the base method. Component E's design was jointly reviewed by both experts
BEFORE this script was written (see `ceiling/signals.py`'s Component E section docstring for the
full rationale, and `docs/adr/0028-ceiling-signal-data-layer.md`'s Component E Update section for
the final record); this script runs every check that review required up front, matching Component
D's "stricter initial bar" precedent (cluster-robust SEs and a train/holdout split from run 1, not
reactive reruns):

1. **Primary confirmatory test**: `qb_explosive_rush_rate_signals` (`ceiling/signals.py`, default
   `EXPLOSIVE_RUSH_YARDS_THRESHOLD=15`) -- a LEVEL signal (trailing pooled designed+scramble
   explosive-rush rate, not a week-to-week boom-rate), gated at
   `QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME=30` pooled trailing rush attempts (a value DERIVED from a
   target standard error, not asserted by analogy to Component D's designed-run floor -- see that
   constant's own comment in `ceiling/signals.py`).
2. **Required sensitivity check**: the same primary test recomputed at a 10-yard threshold
   (`EXPLOSIVE_RUSH_YARDS_MIN_THRESHOLD` below) -- the Model Analytics Expert's explicit
   requirement, since 20+ was ruled out as collapsing toward a near-binary indicator and 15+'s own
   robustness needed a real comparison point, not just a single arbitrarily-chosen cut.
3. **Out-of-sample holdout**: fit on `TRAIN_SEASONS` (2020-2022), independently check sign/
   magnitude on `HOLDOUT_SEASONS` (2023-2025) -- reused from Component D's precedent, which caught
   two real sign flips no in-sample diagnostic would have caught.
4. **Scramble-share diagnostics (Fantasy Football Expert's required addition, in TWO forms, not
   one)**: (a) a tercile split by trailing scramble share (same shape as Component D's goal-line
   split) and (b) a regression of `log(relative_performance)` on BOTH `shrunk_z` AND trailing
   scramble share simultaneously (cluster-robust, via `_linear_fit_multi_clustered_se` below) --
   explicitly requested because a tercile split alone could miss a confound a continuous covariate
   catches. Football rationale: scrambles are plausibly more likely than designed runs to produce
   a long gain (broken-pocket improvisation vs. a blocked, defined running lane), so this
   construct is at real risk of just re-deriving Component D's already-nulled scramble-rate finding
   through a more outcome-adjacent lens -- both checks test whether any effect here survives
   independent of a player's scramble mix.
5. **Goal-line-share diagnostic split** (carried forward from Component D, now correcting a
   STRUCTURAL confound rather than a role-conflation risk -- `yardline_100` mechanically caps how
   long a run near the goal line CAN be, so a short-yardage/goal-line specialist would show a
   mechanically DEPRESSED explosive-rate for a reason having nothing to do with real explosiveness,
   the reverse direction of Component D's own goal-line concern).
6. **Quantization check**: the distribution of qualifying players' trailing pooled rush-attempt
   counts and achievable distinct rate values -- Component D's own quantization finding (71% of its
   population sat at a trailing median of <=2, making its boom threshold nearly meaningless) is the
   reason Component E was deliberately built as a pooled-window LEVEL signal rather than a per-week
   boom-rate; this check confirms that design choice actually solved the problem rather than just
   moving it.

**Expectation, stated going in (Fantasy Football Expert's explicit request, not asserted after the
fact the way Component D's quantization risk was only found reactively)**: given scramble rate
already nulled with a sign flip on Component D's own secondary test, and this construct partly
overlaps with the same scramble population, the prior going in is that explosive-rush rate is at
real risk of nulling for a related reason -- both experts flagged this as a materially lower-
confidence starting point than Component D's original framing, not a fresh, neutral hypothesis.

NOT part of `pytest` -- a one-time live-data research pass, run by hand:

    PYTHONPATH=. .venv/bin/python scripts/ceiling_qb_explosive_rush_backtest.py
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl
import numpy as np
import pandas as pd

from nfl_dfs.ceiling.signals import QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME, qb_explosive_rush_rate_signals
from nfl_dfs.ingestion.qb_rushing_profile import aggregate_trailing_qb_rushing_profile
from scripts.ceiling_role_share_backtest import (
    MAX_TARGET_WEEK,
    MIN_TARGET_WEEK,
    N_DECILES,
    _linear_fit,
    _linear_fit_clustered_se,
    _linear_fit_with_se,
    dk_points_row,
)

TRAIN_SEASONS = [2020, 2021, 2022]
HOLDOUT_SEASONS = [2023, 2024, 2025]
SEASONS = TRAIN_SEASONS + HOLDOUT_SEASONS

EXPLOSIVE_RUSH_YARDS_SENSITIVITY_THRESHOLD = 10  # required alongside the production 15+ default.


def _linear_fit_multi_clustered_se(X: np.ndarray, y: np.ndarray, clusters: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[tuple[float, float]], int]:
    """Multiple-regressor generalization of `_linear_fit_clustered_se` (same Cameron-Miller
    sandwich estimator, same Stata-style small-sample correction) -- `X` is an (n, p) design matrix
    of predictors WITHOUT an intercept column (one is added here); returns per-predictor slopes,
    SEs, and 95% CIs, plus the cluster count. Used only for the scramble-share-residualized
    regression the Fantasy Football Expert required -- `_linear_fit_clustered_se` itself (single
    predictor) is untouched and still used for every other regression in this script.
    """
    n, p = X.shape
    design = np.column_stack([np.ones(n), X])
    k = p + 1
    xtx_inv = np.linalg.inv(design.T @ design)
    beta = xtx_inv @ design.T @ y
    resid = y - design @ beta

    unique_clusters = np.unique(clusters)
    n_clusters = len(unique_clusters)
    meat = np.zeros((k, k))
    for cluster_id in unique_clusters:
        mask = clusters == cluster_id
        design_g = design[mask]
        resid_g = resid[mask]
        score_g = design_g.T @ resid_g
        meat += np.outer(score_g, score_g)

    correction = (n_clusters / (n_clusters - 1)) * ((n - 1) / (n - k))
    vcov = correction * xtx_inv @ meat @ xtx_inv
    slopes = beta[1:]
    ses = np.sqrt(np.diag(vcov)[1:])
    cis = [(float(slopes[i] - 1.96 * ses[i]), float(slopes[i] + 1.96 * ses[i])) for i in range(p)]
    return slopes, ses, cis, n_clusters


def _collect_pairs(threshold: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One full 6-season pass at a given explosive-yards threshold -- returns (primary_df,
    continuity/quantization support omitted here, computed separately in main() for the default
    threshold only, to avoid re-pulling pbp 4x)."""
    pairs: list[dict] = []
    for season in SEASONS:
        print(f"  [{threshold}yd] season {season}...")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            pbp = nfl.import_pbp_data([season], include_participation=False)
            try:
                weekly = nfl.import_weekly_data([season])
            except Exception as exc:  # noqa: BLE001
                print(f"    SKIPPING season {season}: import_weekly_data failed live ({exc})")
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

            profile = (
                aggregate_trailing_qb_rushing_profile(pbp, target_week)
                .groupby("player_id", observed=True)[["trailing_designed_runs", "trailing_scrambles", "trailing_rush_attempts", "trailing_goalline_rush_attempts"]]
                .sum()
            )

            for signal in qb_explosive_rush_rate_signals(pbp, target_week, yards_threshold=threshold):
                if signal.shrunk_z_score is None:
                    continue
                if signal.player_id not in trailing_median.index or signal.player_id not in actual_points.index:
                    continue
                median = trailing_median[signal.player_id]
                if median <= 0:
                    continue
                actual = actual_points[signal.player_id]
                row = profile.loc[signal.player_id] if signal.player_id in profile.index else None
                scramble_share = (row["trailing_scrambles"] / row["trailing_rush_attempts"]) if row is not None and row["trailing_rush_attempts"] > 0 else None
                goalline_share = (row["trailing_goalline_rush_attempts"] / row["trailing_designed_runs"]) if row is not None and row["trailing_designed_runs"] > 0 else None
                pairs.append(
                    {
                        "season": season, "week": target_week, "player_id": signal.player_id,
                        "shrunk_z": signal.shrunk_z_score, "sample_size": signal.sample_size,
                        "raw_value": signal.raw_value,
                        "relative_performance": actual / median,
                        "scramble_share": scramble_share,
                        "goalline_share": goalline_share,
                        "is_holdout": season in HOLDOUT_SEASONS,
                    }
                )
        print(f"  [{threshold}yd] {len(pairs)} cumulative pairs so far")
    return pd.DataFrame(pairs), pd.DataFrame()


def _report_train_holdout(label: str, df: pd.DataFrame) -> None:
    for split_label, subset in [("TRAIN (2020-2022)", df[~df["is_holdout"]]), ("HOLDOUT (2023-2025)", df[df["is_holdout"]])]:
        print(f"--- {label} -- {split_label}, n={len(subset)} ---")
        if len(subset) < 30:
            print("  too small to fit\n")
            continue
        positive = subset[subset["relative_performance"] > 0]
        excluded = len(subset) - len(positive)
        log_perf = np.log(positive["relative_performance"].to_numpy())
        z = positive["shrunk_z"].to_numpy()
        slope, r2, se, ci = _linear_fit_with_se(z, log_perf)
        c_slope, c_se, c_ci, n_clusters = _linear_fit_clustered_se(z, log_perf, positive["player_id"].to_numpy())
        print(f"  n={len(positive)} ({excluded} excluded for relative_performance<=0)")
        print(f"  OLS            -- slope={slope:.4f}  SE={se:.4f}  95% CI=[{ci[0]:.4f}, {ci[1]:.4f}]  R2={r2:.3f}")
        print(f"  Cluster-robust (player_id, G={n_clusters}) -- slope={c_slope:.4f}  SE={c_se:.4f}  "
              f"95% CI=[{c_ci[0]:.4f}, {c_ci[1]:.4f}]")
        print(f"  --> clears zero (clustered): {c_ci[0] > 0 or c_ci[1] < 0}")
        print(f"  --> exp(slope) = {np.exp(slope):.4f}\n")


def main() -> None:
    print("=== Primary test (15yd threshold, production default) ===")
    df15, _ = _collect_pairs(15)
    print(f"\nTotal Component E primary sample (15yd): {len(df15)} (player, week) observations\n")

    print("=== Required sensitivity check (10yd threshold) ===")
    df10, _ = _collect_pairs(EXPLOSIVE_RUSH_YARDS_SENSITIVITY_THRESHOLD)
    print(f"\nTotal Component E sensitivity sample (10yd): {len(df10)} (player, week) observations\n")

    # ---------------------------------------------------------------------------------------
    # Quantization check (15yd population)
    # ---------------------------------------------------------------------------------------
    qualifying = df15.drop_duplicates(subset=["season", "player_id"])
    qualifying = qualifying[qualifying["raw_value"].notna()] if "raw_value" in qualifying.columns else qualifying
    print("=== Quantization check: qualifying (15yd) population's trailing pooled rush-attempt counts ===")
    sizes = df15.drop_duplicates(subset=["season", "week", "player_id"])["sample_size"]
    print(f"  n={len(sizes)} qualifying (player, week) observations, sample_size (pooled trailing attempts) distribution:")
    print(f"  {sizes.describe().to_string()}")
    print(f"  QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME={QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME} -- at that floor, achievable rate "
          f"granularity is 1/{QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME}={1/QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME:.3f} per step\n")

    # ---------------------------------------------------------------------------------------
    # Primary + sensitivity, train/holdout
    # ---------------------------------------------------------------------------------------
    _report_train_holdout("Primary (15yd)", df15)
    _report_train_holdout("Sensitivity (10yd)", df10)

    # Decile view, full sample, 15yd only (matches prior components' own reporting convention).
    df15_dec = df15.copy()
    df15_dec["decile"] = pd.qcut(df15_dec["shrunk_z"], N_DECILES, labels=False, duplicates="drop")
    decile_summary = df15_dec.groupby("decile").agg(n=("relative_performance", "size"), mean_shrunk_z=("shrunk_z", "mean"))
    z = decile_summary["mean_shrunk_z"].to_numpy()
    median_by_decile = df15_dec.groupby("decile")["relative_performance"].median()
    med_slope, med_r2 = _linear_fit(z, median_by_decile.to_numpy())
    print(f"Decile-level fit (median, full sample, 15yd) -- relative_performance ~ shrunk_z: slope={med_slope:.4f}  R2={med_r2:.3f}\n")

    # ---------------------------------------------------------------------------------------
    # Scramble-share diagnostics (tercile split + residualized regression), 15yd, full sample
    # ---------------------------------------------------------------------------------------
    print("=== Scramble-share diagnostic (a): tercile split ===")
    ss = df15.dropna(subset=["scramble_share"])
    ss = ss[ss["relative_performance"] > 0]
    if len(ss) >= 90:
        ss = ss.copy()
        ss["tier"] = pd.qcut(ss["scramble_share"], 3, labels=["low", "mid", "high"], duplicates="drop")
        for tier in ss["tier"].cat.categories:
            tier_df = ss[ss["tier"] == tier]
            if len(tier_df) < 30:
                print(f"  {tier}: n={len(tier_df)} too small to fit")
                continue
            t_slope, t_r2, t_se, t_ci = _linear_fit_with_se(tier_df["shrunk_z"].to_numpy(), np.log(tier_df["relative_performance"].to_numpy()))
            print(f"  {tier:>4} (mean scramble share={tier_df['scramble_share'].mean():.2f}, n={len(tier_df)}): "
                  f"slope={t_slope:.4f}  SE={t_se:.4f}  95% CI=[{t_ci[0]:.4f}, {t_ci[1]:.4f}]  R2={t_r2:.3f}")
    else:
        print(f"  n={len(ss)} too small overall to split into terciles")
    print()

    print("=== Scramble-share diagnostic (b): residualized regression (shrunk_z + scramble_share, clustered) ===")
    if len(ss) >= 30:
        X = np.column_stack([ss["shrunk_z"].to_numpy(), ss["scramble_share"].to_numpy()])
        y = np.log(ss["relative_performance"].to_numpy())
        slopes, ses, cis, n_clusters = _linear_fit_multi_clustered_se(X, y, ss["player_id"].to_numpy())
        print(f"  n={len(ss)}, G={n_clusters} clusters")
        print(f"  shrunk_z        (controlling for scramble_share) -- slope={slopes[0]:.4f}  SE={ses[0]:.4f}  "
              f"95% CI=[{cis[0][0]:.4f}, {cis[0][1]:.4f}]")
        print(f"  scramble_share  (controlling for shrunk_z)        -- slope={slopes[1]:.4f}  SE={ses[1]:.4f}  "
              f"95% CI=[{cis[1][0]:.4f}, {cis[1][1]:.4f}]")
    else:
        print(f"  n={len(ss)} too small to fit")
    print()

    # ---------------------------------------------------------------------------------------
    # Goal-line-share diagnostic split (structural confound, 15yd, full sample)
    # ---------------------------------------------------------------------------------------
    print("=== Goal-line-share diagnostic split (structural confound: yardline_100 caps run length) ===")
    gl = df15.dropna(subset=["goalline_share"])
    gl = gl[gl["relative_performance"] > 0]
    if len(gl) >= 90:
        gl = gl.copy()
        gl["tier"] = pd.qcut(gl["goalline_share"], 3, labels=["low", "mid", "high"], duplicates="drop")
        for tier in gl["tier"].cat.categories:
            tier_df = gl[gl["tier"] == tier]
            if len(tier_df) < 30:
                print(f"  {tier}: n={len(tier_df)} too small to fit")
                continue
            t_slope, t_r2, t_se, t_ci = _linear_fit_with_se(tier_df["shrunk_z"].to_numpy(), np.log(tier_df["relative_performance"].to_numpy()))
            print(f"  {tier:>4} (mean goal-line share={tier_df['goalline_share'].mean():.2f}, n={len(tier_df)}): "
                  f"slope={t_slope:.4f}  SE={t_se:.4f}  95% CI=[{t_ci[0]:.4f}, {t_ci[1]:.4f}]  R2={t_r2:.3f}")
    else:
        print(f"  n={len(gl)} too small overall to split into terciles")
    print()


if __name__ == "__main__":
    main()
