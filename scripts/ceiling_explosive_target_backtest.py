"""Live outcome backtest for `CeilingMultiplier` Component F candidate (explosive-target rate),
ADR-0028 -- the Fantasy Football Expert's named successor to Component C's (aDOT) own null.

**RESULT (2026-09-15): closed as a null, no live multiplier.** WR shows a weak, consistently
negatively-signed lean across every threshold (15yd/10yd/20yd) and diagnostic (decile fit,
unshrunk-z, YAC-residualized), but it never clears zero in its own primary (15yd) holdout spec --
train slope=-0.0517 (clustered CI=[-0.1045, 0.0012], barely misses), holdout slope=-0.0124
(CI=[-0.0751, 0.0504], comfortably includes zero, magnitude collapses to ~24% of the already-
borderline train estimate) -- the exact "borderline in-sample, evaporates in holdout" pattern this
project's train/holdout discipline exists to catch, not the clean, decisively-clearing effect
Component B's WR red-zone finding (ADR-0036) had before it shipped. The 20yd sensitivity threshold
is the most internally consistent result (train and holdout slopes both ~-0.05, same sign and
similar magnitude) but its holdout CI still doesn't clear zero. TE shows no consistent signal at
any threshold, sign flips between splits, and the YAC-residualized regression retains only ~20% of
its already-weak univariate effect. Both experts signed off on closing this out -- full record:
`docs/adr/0028-ceiling-signal-data-layer.md`'s "Update (2026-09-15): Component F (explosive-target
rate)" section. This script is kept for reproducibility, not because the result is still open.

Same core methodology as prior components' backtests (`scripts/ceiling_role_share_backtest.py`,
whose DK scoring and regression helpers this script reuses rather than restating;
`scripts/ceiling_qb_explosive_rush_backtest.py`, whose `_linear_fit_multi_clustered_se` this script
also reuses). Component F's design was jointly reviewed by both experts BEFORE this script was
written (see `ceiling/signals.py`'s Component F section docstring for the full rationale, and
`docs/adr/0028-ceiling-signal-data-layer.md`'s Component F Update section for the final record);
this script runs every check that review required up front, matching Component E's "stricter
initial bar" precedent (cluster-robust SEs and a train/holdout split from run 1, not reactive
reruns):

1. **Primary confirmatory test**: `explosive_target_rate_ceiling_signals` (`ceiling/signals.py`,
   default `EXPLOSIVE_TARGET_YARDS_THRESHOLD=15`) -- a LEVEL signal (trailing pooled
   TARGETS-denominator explosive-target rate, not a week-to-week boom-rate), gated at
   `EXPLOSIVE_TARGET_MIN_TRAILING_TARGETS` (position-specific, DERIVED from a target standard
   error against the Fantasy Football Expert's real base-rate estimates -- see that constant's own
   comment in `ceiling/signals.py`), split WR/TE.
2. **Required sensitivity checks, BIDIRECTIONAL (10+ and 20+)**: the same primary test recomputed
   at both thresholds, each with its OWN separately-derived floor -- computed empirically from this
   script's own first (floor=1) pass at that threshold (the observed population mean rate), via the
   same `n = p*(1-p)/SE**2` method used for the production 15yd constants, rather than reusing the
   15yd floor at a different threshold where the achievable base rate is different.
3. **Out-of-sample holdout**: fit on `TRAIN_SEASONS` (2020-2022), independently check sign/
   magnitude on `HOLDOUT_SEASONS` (2023-2025) -- reused from Component D/E's precedent, which
   caught real sign flips no in-sample diagnostic would have caught.
4. **Quantization check**: the distribution of qualifying players' trailing target counts and
   achievable distinct rate values at the production floor.
5. **Up-front (not reactive) `CEILING_SHRINKAGE_K=6.0` due-diligence rerun** (Model Analytics
   Expert's required check, run proactively this time rather than reactively the way Component C's
   own version was first triggered): (a) refit on the UNSHRUNK z-score, (b) a trailing-target
   tercile split -- both already computed on `CeilingSignal`, no new data pull.
6. **YAC-per-reception residualized regression** (Fantasy Football Expert's specific condition):
   `log(relative_performance) ~ shrunk_z + trailing_yac_per_reception` (cluster-robust, via
   `_linear_fit_multi_clustered_se`), reporting BOTH whether the CI clears zero AND how much of the
   univariate effect size survives controlling for YAC -- a real but small residual effect matters
   differently than a real large one, the Fantasy Football Expert's explicit reason for requiring
   magnitude retention, not just a significance check.
7. **Correlation check against Component C's own (null) aDOT signal**: population-level Pearson
   correlation between this signal's `shrunk_z` and `adot_ceiling_signals`' own `shrunk_z` for the
   same (player, week) -- tests whether this construct is just re-deriving Component C's null
   through a different lens, or measuring something independent.

**Expectation, stated going in**: both experts converged on this design after the Fantasy Football
Expert's original "boom-shaped" framing was shown to import Component D/E's own quantization
failure and conceded wrong -- this is a fresh hypothesis, not one either expert has a strong prior
about (unlike Component E, which both experts flagged as at real risk of re-deriving an
already-nulled finding).

NOT part of `pytest` -- a one-time live-data research pass, run by hand:

    PYTHONPATH=. .venv/bin/python scripts/ceiling_explosive_target_backtest.py
"""

from __future__ import annotations

import math
import warnings

import nfl_data_py as nfl
import numpy as np
import pandas as pd

from nfl_dfs.ceiling.signals import (
    EXPLOSIVE_TARGET_MIN_TRAILING_TARGETS,
    EXPLOSIVE_TARGET_YARDS_THRESHOLD,
    adot_ceiling_signals,
    explosive_target_rate_ceiling_signals,
)
from nfl_dfs.ingestion.receiving_profile import trailing_receiving_profiles
from scripts.ceiling_qb_explosive_rush_backtest import _linear_fit_multi_clustered_se
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

SENSITIVITY_THRESHOLDS = (10, 20)  # required alongside the production 15+ default, bidirectional.
TARGET_SE = 0.05
POSITIONS = ("WR", "TE")


def _derive_floor(observed_rate: float, *, target_se: float = TARGET_SE) -> int:
    """Same `n = p*(1-p)/SE**2` method as `QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME`/
    `EXPLOSIVE_TARGET_MIN_TRAILING_TARGETS`'s own derivation, applied here to an EMPIRICALLY
    observed population rate (rather than a football-expert-supplied estimate) since neither expert
    supplied a base-rate range for the 10yd/20yd sensitivity thresholds -- only the 15yd primary."""
    p = max(min(observed_rate, 0.999), 0.001)
    return math.ceil(p * (1 - p) / target_se**2)


def _collect_pairs(threshold: int, floors: dict[str, int]) -> pd.DataFrame:
    """One full 6-season pass at a given explosive-yards threshold and floor set -- also carries
    `trailing_yac_per_reception`/`trailing_adot` (already computed by
    `ingestion/receiving_profile.py`, no new data pull) for the diagnostics below."""
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

        position_by_player_id = (
            weekly[weekly["position"].isin(POSITIONS)]
            .drop_duplicates(subset=["player_id"], keep="last")
            .set_index("player_id")["position"]
            .to_dict()
        )

        for target_week in range(MIN_TARGET_WEEK, min(MAX_TARGET_WEEK, max_week_this_season + 1)):
            trailing_weekly = weekly[weekly["week"] < target_week]
            actual_weekly = weekly[weekly["week"] == target_week]
            if actual_weekly.empty:
                continue
            trailing_median = trailing_weekly.groupby("player_id")["dk_points"].median()
            actual_points = actual_weekly.set_index("player_id")["dk_points"]

            receiving_profiles = trailing_receiving_profiles(pbp, target_week)
            adot_by_position = adot_ceiling_signals(pbp, target_week, position_by_player_id)
            adot_z_by_player_id = {s.player_id: s.shrunk_z_score for pos in POSITIONS for s in adot_by_position.get(pos, [])}

            signals_by_position = explosive_target_rate_ceiling_signals(
                pbp, target_week, position_by_player_id, yards_threshold=threshold, min_trailing_targets=floors
            )
            for position in POSITIONS:
                for signal in signals_by_position.get(position, []):
                    if signal.player_id not in trailing_median.index or signal.player_id not in actual_points.index:
                        continue
                    median = trailing_median[signal.player_id]
                    if median <= 0:
                        continue
                    actual = actual_points[signal.player_id]
                    profile = receiving_profiles.get(signal.player_id)
                    pairs.append(
                        {
                            "season": season, "week": target_week, "position": position,
                            "player_id": signal.player_id,
                            "shrunk_z": signal.shrunk_z_score,
                            "raw_z": signal.z_score,
                            "sample_size": signal.sample_size,
                            "raw_value": signal.raw_value,
                            "relative_performance": actual / median,
                            "trailing_yac_per_reception": profile.trailing_yac_per_reception if profile else None,
                            "adot_shrunk_z": adot_z_by_player_id.get(signal.player_id),
                            "is_holdout": season in HOLDOUT_SEASONS,
                        }
                    )
        print(f"  [{threshold}yd] {len(pairs)} cumulative (position, player, week) pairs so far")
    return pd.DataFrame(pairs)


def _report_train_holdout(label: str, df: pd.DataFrame) -> None:
    for position in POSITIONS:
        role_df = df[(df["position"] == position) & df["shrunk_z"].notna()]
        for split_label, subset in [("TRAIN (2020-2022)", role_df[~role_df["is_holdout"]]), ("HOLDOUT (2023-2025)", role_df[role_df["is_holdout"]])]:
            print(f"--- {label} {position} -- {split_label}, n={len(subset)} ---")
            positive = subset[subset["relative_performance"] > 0]
            if len(positive) < 30:
                print(f"  n={len(positive)} too small to fit\n")
                continue
            log_perf = np.log(positive["relative_performance"].to_numpy())
            z = positive["shrunk_z"].to_numpy()
            slope, r2, se, ci = _linear_fit_with_se(z, log_perf)
            c_slope, c_se, c_ci, n_clusters = _linear_fit_clustered_se(z, log_perf, positive["player_id"].to_numpy())
            print(f"  OLS            -- slope={slope:.4f}  SE={se:.4f}  95% CI=[{ci[0]:.4f}, {ci[1]:.4f}]  R2={r2:.3f}")
            print(f"  Cluster-robust (player_id, G={n_clusters}) -- slope={c_slope:.4f}  SE={c_se:.4f}  "
                  f"95% CI=[{c_ci[0]:.4f}, {c_ci[1]:.4f}]")
            print(f"  --> clears zero (clustered): {c_ci[0] > 0 or c_ci[1] < 0}")
            print(f"  --> exp(slope) = {np.exp(slope):.4f}\n")


def main() -> None:
    print("=== Primary test (15yd threshold, production default, production floors) ===")
    df15 = _collect_pairs(EXPLOSIVE_TARGET_YARDS_THRESHOLD, EXPLOSIVE_TARGET_MIN_TRAILING_TARGETS)
    print(f"\nTotal Component F primary sample (15yd): {len(df15)} (position, player, week) observations\n")

    # ---------------------------------------------------------------------------------------
    # Sensitivity thresholds -- each gets its OWN empirically-derived floor (base rates differ by
    # threshold), computed from a floor=1 (unfloored) first pass at that threshold.
    # ---------------------------------------------------------------------------------------
    sensitivity_frames: dict[int, pd.DataFrame] = {}
    for threshold in SENSITIVITY_THRESHOLDS:
        print(f"=== Deriving floor for {threshold}yd sensitivity check ===")
        unfloored = _collect_pairs(threshold, {"WR": 1, "TE": 1})
        derived_floors: dict[str, int] = {}
        for position in POSITIONS:
            observed = unfloored[(unfloored["position"] == position) & unfloored["raw_value"].notna()]["raw_value"]
            if len(observed) == 0:
                print(f"  {position}: no observations, skipping")
                continue
            rate = float(observed.mean())
            derived_floors[position] = _derive_floor(rate)
            print(f"  {position}: observed mean rate={rate:.4f} --> derived floor n={derived_floors[position]}")
        print(f"=== Re-running {threshold}yd sensitivity check with derived floors {derived_floors} ===")
        sensitivity_frames[threshold] = _collect_pairs(threshold, derived_floors)
        print(f"Total Component F sensitivity sample ({threshold}yd): {len(sensitivity_frames[threshold])} observations\n")

    # ---------------------------------------------------------------------------------------
    # Quantization check (15yd production-floor population)
    # ---------------------------------------------------------------------------------------
    print("=== Quantization check: qualifying (15yd) population's trailing target counts ===")
    for position in POSITIONS:
        sizes = df15[(df15["position"] == position) & df15["shrunk_z"].notna()].drop_duplicates(
            subset=["season", "week", "player_id"]
        )["sample_size"]
        floor = EXPLOSIVE_TARGET_MIN_TRAILING_TARGETS[position]
        print(f"  {position}: n={len(sizes)} qualifying (player, week) observations, sample_size distribution:")
        print(f"  {sizes.describe().to_string()}")
        print(f"  floor={floor} -- achievable rate granularity at that floor is 1/{floor}={1 / floor:.3f} per step\n")

    # ---------------------------------------------------------------------------------------
    # Primary + sensitivity, train/holdout, per position
    # ---------------------------------------------------------------------------------------
    _report_train_holdout("Primary (15yd)", df15)
    for threshold, frame in sensitivity_frames.items():
        _report_train_holdout(f"Sensitivity ({threshold}yd)", frame)

    # Decile view, full sample, 15yd only, per position (diagnostic only, matches prior components).
    for position in POSITIONS:
        role_df = df15[(df15["position"] == position) & df15["shrunk_z"].notna()].copy()
        if len(role_df) < N_DECILES * 3:
            print(f"Decile-level fit ({position}, 15yd): n={len(role_df)} too small\n")
            continue
        role_df["decile"] = pd.qcut(role_df["shrunk_z"], N_DECILES, labels=False, duplicates="drop")
        decile_summary = role_df.groupby("decile").agg(n=("relative_performance", "size"), mean_shrunk_z=("shrunk_z", "mean"))
        z = decile_summary["mean_shrunk_z"].to_numpy()
        median_by_decile = role_df.groupby("decile")["relative_performance"].median()
        med_slope, med_r2 = _linear_fit(z, median_by_decile.to_numpy())
        print(f"Decile-level fit ({position}, full sample, 15yd) -- relative_performance ~ shrunk_z: "
              f"slope={med_slope:.4f}  R2={med_r2:.3f}\n")

    # ---------------------------------------------------------------------------------------
    # Up-front CEILING_SHRINKAGE_K due-diligence rerun (unshrunk-z + trailing-target tercile split)
    # ---------------------------------------------------------------------------------------
    print("=== CEILING_SHRINKAGE_K due-diligence: unshrunk-z refit + trailing-target tercile split ===")
    for position in POSITIONS:
        positive = df15[(df15["position"] == position) & df15["shrunk_z"].notna() & (df15["relative_performance"] > 0)]
        if len(positive) < 30:
            print(f"  {position}: n={len(positive)} too small\n")
            continue
        log_perf = np.log(positive["relative_performance"].to_numpy())
        raw_z = positive["raw_z"].to_numpy()
        raw_slope, raw_se, raw_ci, raw_g = _linear_fit_clustered_se(raw_z, log_perf, positive["player_id"].to_numpy())
        print(f"  {position} unshrunk-z cluster-robust fit -- slope={raw_slope:.4f}  SE={raw_se:.4f}  "
              f"95% CI=[{raw_ci[0]:.4f}, {raw_ci[1]:.4f}]")

        tiered = positive.copy()
        tiered["target_tier"] = pd.qcut(tiered["sample_size"], 3, labels=["low", "mid", "high"], duplicates="drop")
        print(f"  {position} trailing-target tercile split (shrunk_z, cluster-robust):")
        for tier in ["low", "mid", "high"]:
            tier_df = tiered[tiered["target_tier"] == tier]
            if len(tier_df) < 30:
                print(f"    {tier}: n={len(tier_df)} too small to fit")
                continue
            t_log_perf = np.log(tier_df["relative_performance"].to_numpy())
            t_z = tier_df["shrunk_z"].to_numpy()
            t_slope, t_se, t_ci, t_g = _linear_fit_clustered_se(t_z, t_log_perf, tier_df["player_id"].to_numpy())
            mean_targets = tier_df["sample_size"].mean()
            print(f"    {tier:>4} (mean trailing targets={mean_targets:.1f}, n={len(tier_df)}, G={t_g}): "
                  f"slope={t_slope:.4f}  SE={t_se:.4f}  95% CI=[{t_ci[0]:.4f}, {t_ci[1]:.4f}]")
        print()

    # ---------------------------------------------------------------------------------------
    # YAC-per-reception residualized regression (significance AND magnitude retention)
    # ---------------------------------------------------------------------------------------
    print("=== YAC-per-reception residualized regression (shrunk_z + trailing_yac_per_reception, clustered) ===")
    for position in POSITIONS:
        yac = df15[
            (df15["position"] == position) & df15["shrunk_z"].notna() & df15["trailing_yac_per_reception"].notna()
            & (df15["relative_performance"] > 0)
        ]
        if len(yac) < 30:
            print(f"  {position}: n={len(yac)} too small to fit\n")
            continue
        log_perf = np.log(yac["relative_performance"].to_numpy())
        univariate_slope, _, _, _ = _linear_fit_clustered_se(yac["shrunk_z"].to_numpy(), log_perf, yac["player_id"].to_numpy())
        X = np.column_stack([yac["shrunk_z"].to_numpy(), yac["trailing_yac_per_reception"].to_numpy()])
        slopes, ses, cis, n_clusters = _linear_fit_multi_clustered_se(X, log_perf, yac["player_id"].to_numpy())
        retention = (slopes[0] / univariate_slope) if univariate_slope != 0 else float("nan")
        print(f"  {position}: n={len(yac)}, G={n_clusters} clusters")
        print(f"    univariate shrunk_z slope (no YAC control) = {univariate_slope:.4f}")
        print(f"    shrunk_z              (controlling for YAC) -- slope={slopes[0]:.4f}  SE={ses[0]:.4f}  "
              f"95% CI=[{cis[0][0]:.4f}, {cis[0][1]:.4f}]")
        print(f"    trailing_yac_per_reception (controlling for shrunk_z) -- slope={slopes[1]:.4f}  SE={ses[1]:.4f}  "
              f"95% CI=[{cis[1][0]:.4f}, {cis[1][1]:.4f}]")
        print(f"    --> magnitude retention (residualized/univariate slope) = {retention:.1%}\n")

    # ---------------------------------------------------------------------------------------
    # Correlation check against Component C's own (null) aDOT signal
    # ---------------------------------------------------------------------------------------
    print("=== Correlation check against Component C (aDOT) shrunk_z ===")
    for position in POSITIONS:
        both = df15[(df15["position"] == position) & df15["shrunk_z"].notna() & df15["adot_shrunk_z"].notna()]
        if len(both) < 30:
            print(f"  {position}: n={len(both)} too small\n")
            continue
        corr = float(np.corrcoef(both["shrunk_z"].to_numpy(), both["adot_shrunk_z"].to_numpy())[0, 1])
        print(f"  {position}: n={len(both)}  Pearson r(explosive_target_shrunk_z, adot_shrunk_z) = {corr:.4f}")
    print()


if __name__ == "__main__":
    main()
