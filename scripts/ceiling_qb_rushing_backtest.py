"""Live outcome backtest for `CeilingMultiplier` Component D candidate (QB rushing, designed-run
boom-rate), ADR-0028/ADR-0030.

**RESULT (2026-09-14): a clean null, both legs, both experts signed off on closing this out with
no live multiplier.** Primary test (designed-run boom-rate) and secondary test (scramble rate)
both flip sign between the TRAIN and HOLDOUT splits -- the single most decisive evidence this
project's backtest methodology has produced for any component, since it's exactly the failure mode
the out-of-sample holdout check (new to this component) exists to catch. Full record, both
experts' interpretation, and a genuinely different named future candidate (explosive-rush rate):
`docs/adr/0028-ceiling-signal-data-layer.md`'s "Update (2026-09-14): Component D (QB rushing)"
section. This script is kept for reproducibility, not because the result is still open.

Same core methodology as Components A/B/C's backtests (`scripts/ceiling_role_share_backtest.py`,
whose DK scoring and regression helpers this script reuses rather than restating) -- see that
script's docstring for the base method. Component D's design was jointly reviewed and
conditionally signed off by both experts BEFORE this script was written (see
`docs/adr/0030-qb-rushing-opportunity-profile.md`'s "Update" section and
`ceiling/signals.py`'s Component D section docstring for the full rationale); this script runs
every check that review required up front, not as reactive reruns triggered by a close call --
the Model Analytics Expert was explicit that this leg (no shipped RB/WR sibling to sanity-check
against, and no self-correcting floor if a false-positive positive slope ships) needed a stricter
initial bar than A/B/C got:

1. **Primary confirmatory test**: `qb_rushing_ceiling_signals` (`ceiling/signals.py`) -- boom-rate
   on trailing DESIGNED-RUN COUNT only (scrambles excluded from the predictor), gated at
   `QB_DESIGNED_RUN_MIN_TRAILING_VOLUME=8` trailing designed runs (which also removes near-zero
   pocket passers from the cross-sectional z-scoring population, not just their own output --
   `_z_score_and_shrink`'s existing "population stats from non-null raw_value rows only" behavior).
   Cluster-robust (player_id) SEs computed from the FIRST run, not as a reactive follow-up.
2. **Out-of-sample holdout**: fit on `TRAIN_SEASONS` (2020-2022), report the same fit computed
   independently on `HOLDOUT_SEASONS` (2023-2025) -- the Model Analytics Expert's specific check
   against a false-positive positive slope that a floored-at-1.0 multiplier has no way to
   self-correct against (unlike A/B/C, which all had a natural backstop: a null or negative result
   was simply self-limiting under the one-sided formula).
3. **Quantization check**: the distribution of trailing MEDIAN designed-run counts among the
   qualifying population -- `BOOM_THRESHOLD=1.35` was reused unchanged from A/B/C, but on small
   discrete integers (a median of 2-4 designed runs/week is plausible for this population) a
   1.35x-of-median threshold can trigger noisily; reported so that risk is checked, not assumed.
4. **QB-identity-continuity note**: `_aggregate_qb_designed_runs_weekly` groups by `player_id`
   (nflverse gsis_id) directly, not by team-role-slot the way `RoleShareResult`'s RB/WR
   identification does -- so a benched starter's trailing sample cannot leak into a new starter's
   by construction (each has their own player_id). This script reports the count of distinct
   qualifying player_ids per team-season as a real spot-check that the population isn't somehow
   collapsing multiple QBs into one row, not just an assertion.
5. **Scramble-RATE secondary test** (Fantasy Football Expert's first required addition): a real,
   equally-rigorous test of trailing scramble rate (`trailing_scrambles / (trailing_pass_attempts +
   trailing_scrambles)`, a dropback-proxy denominator -- `aggregate_passer_week`'s own `pass_attempts`
   already counts sacks per that function's docstring, so adding scrambles approximates dropbacks
   without a new pbp dependency) as a LEVEL signal (trailing mean rate, z-scored + shrunk, same
   shape as Component C's aDOT construction) against the SAME total-DK-points relative-performance
   target, with its own cluster-robust SE -- not a footnote, a real test the Fantasy Football
   Expert can act on (the mobile-QB archetype this component exists to serve is exactly the one
   where scramble yardage is plausibly the dominant real ceiling mechanism).
6. **Goal-line-share diagnostic split** (Fantasy Football Expert's second required addition): the
   primary designed-run population split into terciles by `trailing_goalline_rush_attempts /
   trailing_designed_runs` (both already computed by `ingestion.qb_rushing_profile`), mirroring
   Component B's WR volume-tier split -- tests whether the boom-rate signal holds for genuine
   open-field/broken-pocket-adjacent rushers or is being diluted (or driven) by short-yardage
   sneak specialists, the same level-vs-variance distinction ADR-0028 already drew for Component B.
7. **Game-script watch item** (disclosed, non-blocking per both experts): reports the correlation
   between the primary fit's residual and each player's trailing average `|score_differential|`
   at their own rush attempts (a pbp-native proxy for game-script exposure -- no new odds-data
   dependency) -- `BlowoutVolumeDiscount`-shaped contamination is a real, named risk this script
   surfaces but does not correct for this round.

NOT part of `pytest` -- a one-time live-data research pass, run by hand:

    PYTHONPATH=. .venv/bin/python scripts/ceiling_qb_rushing_backtest.py
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl
import numpy as np
import pandas as pd

from nfl_dfs.ceiling.signals import QB_DESIGNED_RUN_MIN_TRAILING_VOLUME, qb_rushing_ceiling_signals
from nfl_dfs.ingestion.qb_rushing_profile import aggregate_trailing_qb_rushing_profile
from nfl_dfs.ingestion.usage_share import aggregate_passer_week
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

MIN_SHRINKAGE_K = 6.0  # reused as-is (Model Analytics Expert's instruction), not refit here.
SCRAMBLE_RATE_MIN_TRAILING_WEEKS = 3  # same weeks-floor MIN_TRAILING_WEEKS uses, disclosed as a
# simpler gate for this diagnostic-only secondary test (no volume floor re-derived for a rate axis).


def _scramble_rate_signals(
    pbp: pd.DataFrame, passer_week: pd.DataFrame, target_week: int
) -> pd.DataFrame:
    """LEVEL signal (trailing mean scramble rate, not a boom-rate) -- same shape as Component C's
    aDOT construction (a receiver's aDOT is a level stat; a QB's scramble tendency is analogously a
    level stat, not a boom/bust variance measure). One row per (team, player_id):
    `sample_size` (trailing weeks with recorded pass attempts), `raw_value` (trailing scramble
    rate), gated to `None` below `SCRAMBLE_RATE_MIN_TRAILING_WEEKS`. Cross-sectional z-score +
    ADR-0011 shrinkage applied by the caller via the same `_z_score_and_shrink`-shaped logic
    inlined here (kept local to this diagnostic-only script rather than added to
    `ceiling/signals.py`, since the Model Analytics Expert scoped scramble rate as exploratory,
    not a second confirmatory signal function)."""
    trailing_passer = passer_week[passer_week["week"] < target_week]
    trailing_pass_attempts = trailing_passer.groupby("player_id", observed=True)["pass_attempts"].sum()
    weeks_played = trailing_passer.groupby("player_id", observed=True)["week"].nunique()

    df = pbp[pbp["season_type"] == "REG"]
    scrambles = df[(df["week"] < target_week) & (df["play_type"] == "run") & (df["qb_scramble"] == 1) & df["rusher_player_id"].notna()]
    trailing_scrambles = scrambles.groupby("rusher_player_id", observed=True).size()

    rows = []
    for player_id in trailing_pass_attempts.index:
        n_weeks = int(weeks_played.get(player_id, 0))
        attempts = float(trailing_pass_attempts[player_id])
        n_scrambles = float(trailing_scrambles.get(player_id, 0))
        denom = attempts + n_scrambles
        if n_weeks < SCRAMBLE_RATE_MIN_TRAILING_WEEKS or denom <= 0:
            rows.append({"player_id": player_id, "sample_size": n_weeks, "raw_value": None})
            continue
        rows.append({"player_id": player_id, "sample_size": n_weeks, "raw_value": n_scrambles / denom})
    return pd.DataFrame(rows, columns=["player_id", "sample_size", "raw_value"])


def _z_score_shrink_generic(df: pd.DataFrame, *, k: float = MIN_SHRINKAGE_K) -> pd.DataFrame:
    """Same cross-sectional z-score + ADR-0011 shrinkage math as `ceiling/signals.py`'s
    `_z_score_and_shrink`, inlined for the scramble-rate diagnostic (kept local per the docstring
    above's reasoning)."""
    from nfl_dfs.ingestion.game_environment_stats import blend_toward_prior, shrinkage_weight

    valid = df[df["raw_value"].notna()]
    if len(valid) >= 2 and valid["raw_value"].std() > 0:
        pop_mean, pop_std = valid["raw_value"].mean(), valid["raw_value"].std()
    else:
        pop_mean = pop_std = None

    out = df.copy()
    z_scores, shrunk = [], []
    for row in df.itertuples(index=False):
        raw = None if pd.isna(row.raw_value) else row.raw_value
        z = (raw - pop_mean) / pop_std if (raw is not None and pop_std is not None) else None
        w = shrinkage_weight(row.sample_size, k) if z is not None else None
        s = blend_toward_prior(z, 0.0, w) if z is not None else None
        z_scores.append(z)
        shrunk.append(s)
    out["z_score"] = z_scores
    out["shrunk_z"] = shrunk
    return out


def main() -> None:
    primary_pairs: list[dict] = []
    scramble_pairs: list[dict] = []
    continuity_rows: list[dict] = []

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
        passer_week = aggregate_passer_week(pbp)

        for target_week in range(MIN_TARGET_WEEK, min(MAX_TARGET_WEEK, max_week_this_season + 1)):
            trailing_weekly = weekly[weekly["week"] < target_week]
            actual_weekly = weekly[weekly["week"] == target_week]
            if actual_weekly.empty:
                continue
            trailing_median = trailing_weekly.groupby("player_id")["dk_points"].median()
            actual_points = actual_weekly.set_index("player_id")["dk_points"]

            # Game-script proxy: this player's trailing average |score_differential| at their own
            # rush attempts (posteam's perspective) -- a pbp-native game-script exposure proxy.
            trailing_rushes = pbp[
                (pbp["season_type"] == "REG") & (pbp["week"] < target_week)
                & (pbp["play_type"] == "run") & pbp["rusher_player_id"].notna()
            ]
            trailing_abs_spread = trailing_rushes.groupby("rusher_player_id", observed=True)["score_differential"].apply(
                lambda s: s.abs().mean()
            )

            # A mid-season trade gives `aggregate_trailing_qb_rushing_profile` two rows for the
            # same player_id (one per team, that function's groupby key) -- summed to one row per
            # player_id here so `.loc[player_id]` always returns a Series, never a same-player-id
            # multi-row DataFrame (which silently breaks a scalar `>` comparison downstream).
            profile = (
                aggregate_trailing_qb_rushing_profile(pbp, target_week)
                .groupby("player_id", observed=True)[["trailing_designed_runs", "trailing_goalline_rush_attempts"]]
                .sum()
            )

            for signal in qb_rushing_ceiling_signals(pbp, target_week):
                # QB-identity-continuity spot-check: record every qualifying (gate-cleared) row's
                # team/season/player_id, regardless of whether it has a further-computed outcome.
                if signal.raw_value is not None:
                    continuity_rows.append({"season": season, "team": signal.team, "player_id": signal.player_id})

                if signal.shrunk_z_score is None:
                    continue
                if signal.player_id not in trailing_median.index or signal.player_id not in actual_points.index:
                    continue
                median = trailing_median[signal.player_id]
                if median <= 0:
                    continue
                actual = actual_points[signal.player_id]
                row = profile.loc[signal.player_id] if signal.player_id in profile.index else None
                goalline_share = (
                    (row["trailing_goalline_rush_attempts"] / row["trailing_designed_runs"])
                    if row is not None and row["trailing_designed_runs"] > 0
                    else None
                )
                primary_pairs.append(
                    {
                        "season": season, "week": target_week, "player_id": signal.player_id,
                        "shrunk_z": signal.shrunk_z_score,
                        "relative_performance": actual / median,
                        "goalline_share": goalline_share,
                        "trailing_abs_spread": trailing_abs_spread.get(signal.player_id, float("nan")),
                        "is_holdout": season in HOLDOUT_SEASONS,
                    }
                )

            scramble_signals = _z_score_shrink_generic(_scramble_rate_signals(pbp, passer_week, target_week))
            for row in scramble_signals.itertuples(index=False):
                # Same NaN-vs-None trap this project has hit before (ceiling/signals.py's
                # `_z_score_and_shrink`): assigning a Python None into a mixed-type list that
                # becomes a float64 pandas column silently upcasts it to NaN, which `is None`
                # misses -- pd.isna() catches both.
                if pd.isna(row.shrunk_z):
                    continue
                if row.player_id not in trailing_median.index or row.player_id not in actual_points.index:
                    continue
                median = trailing_median[row.player_id]
                if median <= 0:
                    continue
                actual = actual_points[row.player_id]
                scramble_pairs.append(
                    {
                        "season": season, "week": target_week, "player_id": row.player_id,
                        "shrunk_z": row.shrunk_z, "relative_performance": actual / median,
                        "is_holdout": season in HOLDOUT_SEASONS,
                    }
                )
        print(f"  {len(primary_pairs)} cumulative primary pairs, {len(scramble_pairs)} cumulative scramble-rate pairs so far")

    df = pd.DataFrame(primary_pairs)
    print(f"\n=== Total Component D primary sample: {len(df)} (player, week) observations ===\n")

    # -------------------------------------------------------------------------------------------
    # QB-identity-continuity spot check
    # -------------------------------------------------------------------------------------------
    continuity_df = pd.DataFrame(continuity_rows).drop_duplicates()
    per_team_season = continuity_df.groupby(["season", "team"], observed=True)["player_id"].nunique()
    print("=== QB-identity-continuity spot check ===")
    print(f"  {len(continuity_df)} distinct (season, team, player_id) qualifying rows")
    print(f"  Mean distinct qualifying player_ids per team-season: {per_team_season.mean():.2f}")
    print(f"  Max distinct qualifying player_ids in one team-season: {per_team_season.max()} "
          f"({per_team_season.idxmax()})")
    print("  (each row is a real player_id -- a benched starter and his replacement each accrue\n"
          "   their own independent trailing sample by construction, never merged into one row)\n")

    # -------------------------------------------------------------------------------------------
    # Quantization check
    # -------------------------------------------------------------------------------------------
    print("=== Quantization check: distribution of qualifying players' trailing median designed-run counts ===")
    # Recompute medians directly from the same weekly aggregation the signal function uses, for
    # every player who cleared QB_DESIGNED_RUN_MIN_TRAILING_VOLUME this round (df's population).
    from nfl_dfs.ceiling.signals import _aggregate_qb_designed_runs_weekly  # local, diagnostic-only use

    medians = []
    for season in SEASONS:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            pbp = nfl.import_pbp_data([season], include_participation=False)
        weekly_designed = _aggregate_qb_designed_runs_weekly(pbp)
        for player_id, group in weekly_designed.groupby("player_id", observed=True):
            total = group["designed_runs"].sum()
            if total >= QB_DESIGNED_RUN_MIN_TRAILING_VOLUME and len(group) >= 3:
                medians.append(group["designed_runs"].median())
    medians = pd.Series(medians)
    print(f"  n={len(medians)} qualifying player-season windows, median trailing designed-run count distribution:")
    print(f"  {medians.describe().to_string()}")
    print(f"  Fraction with trailing median <= 2 (BOOM_THRESHOLD=1.35 x 2 = 2.7, i.e. a single 3-run week booms): "
          f"{(medians <= 2).mean():.1%}\n")

    # -------------------------------------------------------------------------------------------
    # Primary confirmatory test: designed-run boom-rate, train vs. holdout
    # -------------------------------------------------------------------------------------------
    for label, subset in [("TRAIN (2020-2022)", df[~df["is_holdout"]]), ("HOLDOUT (2023-2025)", df[df["is_holdout"]])]:
        print(f"--- Primary test -- {label}, n={len(subset)} ---")
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

    # Decile view on the full sample, diagnostic only (matches A/B/C's own reporting convention).
    df["decile"] = pd.qcut(df["shrunk_z"], N_DECILES, labels=False, duplicates="drop")
    decile_summary = df.groupby("decile").agg(n=("relative_performance", "size"), mean_shrunk_z=("shrunk_z", "mean"))
    z = decile_summary["mean_shrunk_z"].to_numpy()
    median_by_decile = df.groupby("decile")["relative_performance"].median()
    med_slope, med_r2 = _linear_fit(z, median_by_decile.to_numpy())
    print(f"Decile-level fit (median, full sample) -- relative_performance ~ shrunk_z: slope={med_slope:.4f}  R2={med_r2:.3f}\n")

    # -------------------------------------------------------------------------------------------
    # Goal-line-share diagnostic tercile split (full sample, not train/holdout-split -- diagnostic)
    # -------------------------------------------------------------------------------------------
    print("=== Goal-line-share diagnostic split (tercile of trailing goal-line-rush share of designed runs) ===")
    tiered = df.dropna(subset=["goalline_share"]).copy()
    tiered = tiered[tiered["relative_performance"] > 0]
    if len(tiered) >= 90:
        tiered["tier"] = pd.qcut(tiered["goalline_share"], 3, labels=["low", "mid", "high"], duplicates="drop")
        for tier in tiered["tier"].cat.categories:
            tier_df = tiered[tiered["tier"] == tier]
            if len(tier_df) < 30:
                print(f"  {tier}: n={len(tier_df)} too small to fit")
                continue
            t_slope, t_r2, t_se, t_ci = _linear_fit_with_se(tier_df["shrunk_z"].to_numpy(), np.log(tier_df["relative_performance"].to_numpy()))
            mean_share = tier_df["goalline_share"].mean()
            print(f"  {tier:>4} (mean goal-line share={mean_share:.2f}, n={len(tier_df)}): "
                  f"slope={t_slope:.4f}  SE={t_se:.4f}  95% CI=[{t_ci[0]:.4f}, {t_ci[1]:.4f}]  R2={t_r2:.3f}")
    else:
        print(f"  n={len(tiered)} too small overall to split into terciles")
    print()

    # -------------------------------------------------------------------------------------------
    # Game-script watch item: residual vs. trailing average |score_differential|
    # -------------------------------------------------------------------------------------------
    print("=== Game-script watch item (disclosed, non-blocking): fit residual vs. trailing avg |score_differential| ===")
    gs = df.dropna(subset=["trailing_abs_spread"])
    gs = gs[gs["relative_performance"] > 0]
    if len(gs) >= 30:
        slope, intercept = np.polyfit(gs["shrunk_z"].to_numpy(), np.log(gs["relative_performance"].to_numpy()), 1)
        predicted = slope * gs["shrunk_z"].to_numpy() + intercept
        residual = np.log(gs["relative_performance"].to_numpy()) - predicted
        corr = np.corrcoef(residual, gs["trailing_abs_spread"].to_numpy())[0, 1]
        print(f"  n={len(gs)}, Pearson r(residual, trailing avg |score_differential|) = {corr:.4f}")
    else:
        print(f"  n={len(gs)} too small to check")
    print()

    # -------------------------------------------------------------------------------------------
    # Secondary test: scramble rate (Fantasy Football Expert's required addition)
    # -------------------------------------------------------------------------------------------
    sdf = pd.DataFrame(scramble_pairs)
    print(f"=== Secondary test: scramble rate, n={len(sdf)} (player, week) observations ===")
    for label, subset in [("TRAIN (2020-2022)", sdf[~sdf["is_holdout"]]), ("HOLDOUT (2023-2025)", sdf[sdf["is_holdout"]])]:
        print(f"--- Scramble rate -- {label}, n={len(subset)} ---")
        if len(subset) < 30:
            print("  too small to fit\n")
            continue
        positive = subset[subset["relative_performance"] > 0]
        log_perf = np.log(positive["relative_performance"].to_numpy())
        z = positive["shrunk_z"].to_numpy()
        slope, r2, se, ci = _linear_fit_with_se(z, log_perf)
        c_slope, c_se, c_ci, n_clusters = _linear_fit_clustered_se(z, log_perf, positive["player_id"].to_numpy())
        print(f"  n={len(positive)}")
        print(f"  OLS            -- slope={slope:.4f}  SE={se:.4f}  95% CI=[{ci[0]:.4f}, {ci[1]:.4f}]  R2={r2:.3f}")
        print(f"  Cluster-robust (player_id, G={n_clusters}) -- slope={c_slope:.4f}  SE={c_se:.4f}  "
              f"95% CI=[{c_ci[0]:.4f}, {c_ci[1]:.4f}]")
        print(f"  --> clears zero (clustered): {c_ci[0] > 0 or c_ci[1] < 0}\n")


if __name__ == "__main__":
    main()
