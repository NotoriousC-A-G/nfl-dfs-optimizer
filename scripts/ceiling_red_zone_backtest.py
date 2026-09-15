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

**Interpretation-round follow-up (also already fixed before this script was rerun):** the first
pass of this backtest surfaced a real, negative, statistically-significant relationship for WR
that neither expert expected. The Model Analytics Expert traced part of it to a second bug in
`_boom_rate_per_player`'s zero-median branch -- it used to flatly assign `raw_value=0.0` whenever
a player's trailing MEDIAN share was exactly 0, which (after the zero-fill above) silently
discarded real spike weeks for any player with a sub-50%-of-weeks red-zone involvement rate,
exactly the boom/bust players this signal exists to catch. Fixed: a zero-median player's real
nonzero weeks now count as booms relative to their own zero baseline. This script also now
reports the fraction of each role's population landing at exactly `raw_value=0.0` (to see how much
mass that branch still legitimately produces post-fix) and splits the WR regression by a trailing
team-red-zone-volume tercile, to test the Model Analytics Expert's other named concern: that a
small, noisy denominator (team red-zone play counts are often single digits) could itself produce
a systematic negative bias independent of any real role-security signal.

NOT part of `pytest` -- a one-time live-data research pass, run by hand:

    PYTHONPATH=. .venv/bin/python scripts/ceiling_red_zone_backtest.py
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl
import numpy as np
import pandas as pd

from nfl_dfs.ceiling.signals import red_zone_ceiling_signals, role_share_ceiling_signals
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR, aggregate_team_week_volume_red_zone
from scripts.ceiling_qb_explosive_rush_backtest import _linear_fit_multi_clustered_se
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

        # Model Analytics Expert's volume-tier check: team-level trailing red-zone play counts,
        # precomputed once per season so the small-denominator/quantization hypothesis can be
        # tested without re-aggregating pbp per target_week.
        team_rz_volume = aggregate_team_week_volume_red_zone(pbp)

        for target_week in range(MIN_TARGET_WEEK, min(MAX_TARGET_WEEK, max_week_this_season + 1)):
            trailing_weekly = weekly[weekly["week"] < target_week]
            actual_weekly = weekly[weekly["week"] == target_week]
            if actual_weekly.empty:
                continue
            trailing_median = trailing_weekly.groupby("player_id")["dk_points"].median()
            actual_points = actual_weekly.set_index("player_id")["dk_points"]
            trailing_team_rz = team_rz_volume[team_rz_volume["week"] < target_week]

            for role in (ROLE_RB, ROLE_WR):
                b_signals = red_zone_ceiling_signals(pbp, target_week, role)
                a_signals = {s.player_id: s for s in role_share_ceiling_signals(pbp, target_week, role)}
                volume_col = "team_rush_attempts" if role == ROLE_RB else "team_targets"
                trailing_team_rz_avg = trailing_team_rz.groupby("team")[volume_col].mean()

                for signal in b_signals:
                    a_signal = a_signals.get(signal.player_id)
                    if a_signal is not None and a_signal.shrunk_z_score is not None and signal.shrunk_z_score is not None:
                        # ADR-0035's required joint-composition check needs the real outcome
                        # alongside both z-scores, not just the z-score pair the original A/B
                        # correlation check used -- computed the same way `all_pairs` does below,
                        # duplicated here (not shared) since ab_pairs is a distinct, smaller
                        # population (both signals real, a stricter join than either signal alone).
                        rel_perf = None
                        if signal.player_id in trailing_median.index and signal.player_id in actual_points.index:
                            median = trailing_median[signal.player_id]
                            if median > 0:
                                rel_perf = actual_points[signal.player_id] / median
                        ab_pairs.append(
                            {
                                "season": season, "week": target_week, "role": role,
                                "player_id": signal.player_id,
                                "a_shrunk_z": a_signal.shrunk_z_score,
                                "b_shrunk_z": signal.shrunk_z_score,
                                "relative_performance": rel_perf,
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
                            "raw_value": signal.raw_value,
                            "team_rz_volume": trailing_team_rz_avg.get(signal.team, float("nan")),
                        }
                    )
        print(f"  {len(all_pairs)} cumulative (signal, outcome) pairs so far, {len(ab_pairs)} A/B pairs")

    df = pd.DataFrame(all_pairs)
    print(f"\n=== Total Component B sample: {len(df)} (role, player, week) observations ===\n")

    for role in (ROLE_RB, ROLE_WR):
        role_df = df[df["role"] == role].copy()

        # Model Analytics Expert's required check #1: how much of the population lands at exactly
        # raw_value=0.0 post-fix (a real "zero real spike weeks" result now, not the old flat-pin
        # artifact) -- reported for transparency, not because a large fraction is itself wrong.
        zero_frac = (role_df["raw_value"] == 0.0).mean()
        print(f"--- {role} role: {zero_frac:.1%} of the population has raw_value == 0.0 (post zero-median fix) ---")

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
        print(f"  --> exp(slope) = {np.exp(log_slope):.4f}")

        # Model Analytics Expert's required check: the non-clustered SE above assumes independence
        # across a player's own repeated weekly observations, which understates the true SE
        # whenever those repeats are correlated (they are -- a player's underlying talent/role/
        # matchup quality doesn't reset every week). Cluster-robust SEs (Cameron-Miller sandwich,
        # player_id clusters) are the decisive check. Originally gated to RB only (the borderline
        # result at the time this script was first run) -- extended to WR too (ADR-0035's WR
        # red-zone role-security design review) once the Model Analytics Expert flagged that WR's
        # own real, negative finding never actually got this same check: the "cluster-robust SEs
        # mandatory by default" bar was set in this project's methodology only after this script's
        # original run, never retroactively applied to WR's already-shipped-to-the-ADR numbers.
        clustered_slope, clustered_se, clustered_ci, n_clusters = _linear_fit_clustered_se(
            player_z, log_perf, positive["player_id"].to_numpy()
        )
        print(
            f"  Cluster-robust fit (player_id clusters, G={n_clusters}) -- "
            f"slope={clustered_slope:.4f}  SE={clustered_se:.4f}  95% CI=[{clustered_ci[0]:.4f}, {clustered_ci[1]:.4f}]"
        )
        print(f"  --> clears zero: {clustered_ci[0] > 0 or clustered_ci[1] < 0}")

        # Model Analytics Expert's required check #2 (WR only, where the negative finding was):
        # split by a tercile of trailing team red-zone play volume -- tests whether the negative
        # relationship is a small-denominator/quantization artifact (concentrated in the low-volume
        # tier) or survives in the high-volume, less-quantized tier (evidence for a real effect).
        if role == ROLE_WR:
            tiered = positive.dropna(subset=["team_rz_volume"]).copy()
            tiered["volume_tier"] = pd.qcut(tiered["team_rz_volume"], 3, labels=["low", "mid", "high"], duplicates="drop")
            print("  Volume-tier split (tercile of trailing team red-zone plays):")
            for tier in ["low", "mid", "high"]:
                tier_df = tiered[tiered["volume_tier"] == tier]
                if len(tier_df) < 30:
                    print(f"    {tier}: n={len(tier_df)} too small to fit")
                    continue
                t_log_perf = np.log(tier_df["relative_performance"].to_numpy())
                t_z = tier_df["shrunk_z"].to_numpy()
                t_slope, t_r2, t_se, t_ci = _linear_fit_with_se(t_z, t_log_perf)
                mean_vol = tier_df["team_rz_volume"].mean()
                print(
                    f"    {tier:>4} (mean trailing team RZ volume={mean_vol:.1f}, n={len(tier_df)}): "
                    f"slope={t_slope:.4f}  SE={t_se:.4f}  95% CI=[{t_ci[0]:.4f}, {t_ci[1]:.4f}]  R2={t_r2:.3f}"
                )
        print()

    # Model Analytics Expert's required A/B correlation check.
    ab_df = pd.DataFrame(ab_pairs)
    print(f"=== A/B correlation check (n={len(ab_df)} player-weeks with both signals real) ===")
    for role in (ROLE_RB, ROLE_WR):
        role_ab = ab_df[ab_df["role"] == role]
        corr = role_ab["a_shrunk_z"].corr(role_ab["b_shrunk_z"])
        print(f"  {role}: Pearson r(Component A, Component B) = {corr:.4f}  (n={len(role_ab)})")

    # ADR-0035's required joint-composition check (WR only, where a live A*B composition is
    # actually proposed): does Component B's real negative effect survive, and is there a real
    # interaction, once BOTH signals enter the same regression together -- the univariate A/B
    # correlation check above only tests whether the two z-scores move together in general, not
    # whether the log-space effect is still additive (multiplicative in levels) on the subset
    # where both signals are real and potentially large simultaneously.
    print("\n=== Joint composition check (WR only): log(relative_performance) ~ a_z + b_z + a_z*b_z ===")
    wr_ab = ab_df[(ab_df["role"] == ROLE_WR) & ab_df["relative_performance"].notna() & (ab_df["relative_performance"] > 0)]
    if len(wr_ab) >= 30:
        X = np.column_stack(
            [wr_ab["a_shrunk_z"].to_numpy(), wr_ab["b_shrunk_z"].to_numpy(), (wr_ab["a_shrunk_z"] * wr_ab["b_shrunk_z"]).to_numpy()]
        )
        y = np.log(wr_ab["relative_performance"].to_numpy())
        slopes, ses, cis, n_clusters = _linear_fit_multi_clustered_se(X, y, wr_ab["player_id"].to_numpy())
        labels = ["a_shrunk_z (Component A, controlling for B + interaction)",
                  "b_shrunk_z (Component B, controlling for A + interaction)",
                  "a_z*b_z (interaction)"]
        print(f"  n={len(wr_ab)}, G={n_clusters} clusters")
        for label, slope, se, ci in zip(labels, slopes, ses, cis):
            print(f"  {label:<55} slope={slope:.4f}  SE={se:.4f}  95% CI=[{ci[0]:.4f}, {ci[1]:.4f}]")
    else:
        print(f"  n={len(wr_ab)} too small to fit")


if __name__ == "__main__":
    main()
