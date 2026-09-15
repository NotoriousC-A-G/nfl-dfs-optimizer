"""Per-season ResultsDB dup-risk calibration (ADR-0033, building on ADR-0025's ownership-propensity
calibration precedent and ADR-0032's `lineups/` backfill).

Fits, per season, how a real DK GPP lineup's chance of being duplicated by the field (`lineup_ct > 1`,
the literal count of contest entries that built that exact roster) relates to (1) that lineup's own
average field ownership, decile-ranked within its own contest the same way ADR-0025 decile-ranks salary
within position, and (2) specific structural trends the `lineups/` payload already computes per lineup
(`lineupTrends` -- e.g. `qbPairedWithPassCatcher`, `minOnePlayerWithLowOwnership`).

**All 6 confirmed ResultsDB seasons (2020-2025) are now backfilled (ADR-0032, ADR-0033's addendum) --
`compare_season_dup_calibrations` runs the real leave-one-out stability test ADR-0025 established (a
season's own ownership-dup-rate correlation flagged unstable if it deviates from the OTHER seasons'
mean by more than 1.5x their std), the same heuristic, same disclosed "not a formal hypothesis test"
honesty. No position axis here (a lineup isn't position-specific, unlike ADR-0025's per-position
salary curves), so this is a single global test, not a per-position loop.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd

from nfl_dfs.storage.resultsdb_store import read_curated_lineups

N_DECILES = 10

DEFAULT_RECENT_WINDOW: tuple[int, ...] = (2023, 2024, 2025)

# Multiplier on the leave-one-out standard deviation of the *other* seasons beyond which a season's
# own correlation is flagged unstable -- reused unchanged from ADR-0025 (`ownership_calibration.py`'s
# `_UNSTABLE_DEVIATION_MULTIPLIER`), not a new number picked for this module.
_UNSTABLE_DEVIATION_MULTIPLIER = 1.5


@dataclass(frozen=True)
class OwnershipDupCurve:
    """One season's empirical dup-rate-by-average-ownership-decile curve, pooled across every
    already-backfilled contest that season (ADR-0032)."""

    season: int
    n_contests: int
    n_rows: int
    decile_dup_rate: dict[int, float]  # fraction with lineup_ct > 1, per avg_own decile (0=highest)
    decile_mean_lineup_ct: dict[int, float]  # mean lineup_ct, per avg_own decile
    ownership_dup_rate_correlation: float | None  # Pearson r(avg_own, is_duplicated 0/1)


@dataclass(frozen=True)
class TrendDupRate:
    """One `lineupTrends` flag's real dup-rate split (true vs. false) for one season -- e.g. does a
    lineup with `qbPairedWithPassCatcher=True` get duplicated more or less often than one without."""

    trend_name: str
    season: int
    n_true: int
    n_false: int
    dup_rate_true: float | None
    dup_rate_false: float | None


@dataclass(frozen=True)
class SeasonDupCalibration:
    season: int
    ownership_curve: OwnershipDupCurve
    trend_rates: dict[str, TrendDupRate]


@dataclass(frozen=True)
class DupRiskLookupTable:
    """Absolute-`avg_own`-value -> historical dup-risk lookup, POOLED across every backfilled
    lineup in `seasons` -- a deliberately different construction from `OwnershipDupCurve` above.
    That curve decile-RANKS each lineup relative only to its own contest (the right shape for
    describing "how did this settled historical contest's field actually behave"), which has no
    meaning for a brand-new candidate lineup that was never part of any historical contest -- there
    is nothing to rank it against within. This table instead fixes real, absolute `avg_own` VALUE
    breakpoints (global quantile cutpoints over the whole pooled sample) so ANY lineup's own average
    ownership number can be classified against real historical experience, in isolation.
    """

    seasons: tuple[int, ...]
    n_rows: int
    bucket_upper_bounds: tuple[float, ...]  # ascending avg_own value edges; len == n_buckets - 1
    bucket_dup_rate: dict[int, float]  # bucket 0 = lowest avg_own bucket, ascending
    bucket_mean_lineup_ct: dict[int, float]


def _assign_ownership_deciles(df: pd.DataFrame) -> pd.Series:
    """Decile 0 = the highest-average-ownership tenth of that CONTEST's distinct lineups, 9 = lowest
    -- ranked within `(date, contest_id)` only, mirroring `ownership_calibration.py`'s
    `_assign_salary_deciles` exactly (there: within `(date, contest_id, position)`; here there is no
    position axis, a lineup isn't position-specific)."""
    group_keys = ["date", "contest_id"]
    ranks = df.groupby(group_keys)["avg_own"].rank(method="first", ascending=False) - 1
    sizes = df.groupby(group_keys)["avg_own"].transform("size")
    raw_decile = (ranks / sizes * N_DECILES).astype(int)
    return raw_decile.clip(upper=N_DECILES - 1)


def _fit_ownership_curve(df: pd.DataFrame, season: int) -> OwnershipDupCurve:
    df = df.copy()
    df["is_duplicated"] = (df["lineup_ct"] > 1).astype(int)
    df["ownership_decile"] = _assign_ownership_deciles(df)

    dup_rate = df.groupby("ownership_decile")["is_duplicated"].mean()
    mean_ct = df.groupby("ownership_decile")["lineup_ct"].mean()
    n_contests = df[["date", "contest_id"]].drop_duplicates().shape[0]
    correlation = float(df["avg_own"].corr(df["is_duplicated"])) if df["avg_own"].nunique() >= 2 else None

    return OwnershipDupCurve(
        season=season,
        n_contests=n_contests,
        n_rows=int(len(df)),
        decile_dup_rate={int(d): float(v) for d, v in dup_rate.items()},
        decile_mean_lineup_ct={int(d): float(v) for d, v in mean_ct.items()},
        ownership_dup_rate_correlation=correlation,
    )


def _fit_trend_rates(df: pd.DataFrame, season: int) -> dict[str, TrendDupRate]:
    """One `TrendDupRate` per distinct `lineupTrends` key observed anywhere in `df` this season --
    the key set is a real, RG-computed fixed vocabulary (confirmed live, ADR-0032), but this doesn't
    assume every row carries every key (a row missing a key is simply excluded from that key's own
    true/false split, never treated as a fabricated `False`)."""
    is_duplicated = df["lineup_ct"] > 1
    trend_keys: set[str] = set()
    for trends in df["lineup_trends"]:
        trend_keys.update(trends.keys())

    rates: dict[str, TrendDupRate] = {}
    for key in sorted(trend_keys):
        values = df["lineup_trends"].map(lambda t, k=key: t.get(k))
        true_mask = values == True  # noqa: E712 -- explicit True/False/None distinction, not truthiness
        false_mask = values == False  # noqa: E712
        n_true = int(true_mask.sum())
        n_false = int(false_mask.sum())
        rates[key] = TrendDupRate(
            trend_name=key,
            season=season,
            n_true=n_true,
            n_false=n_false,
            dup_rate_true=float(is_duplicated[true_mask].mean()) if n_true > 0 else None,
            dup_rate_false=float(is_duplicated[false_mask].mean()) if n_false > 0 else None,
        )
    return rates


def fit_season_dup_calibration(season: int, *, base_dir: Path | None = None) -> SeasonDupCalibration:
    """Fits one season's ownership-decile dup-rate curve and per-trend dup-rate splits from the
    curated ResultsDB lineups rows already on disk (ADR-0032). Raises if that season has no curated
    lineups data -- never silently fits an empty/placeholder calibration."""
    df = read_curated_lineups(season=season, base_dir=base_dir)
    if df.empty:
        raise ValueError(f"no curated ResultsDB lineups data found for season {season} -- run the lineups backfill first")

    ownership_curve = _fit_ownership_curve(df, season)
    trend_rates = _fit_trend_rates(df, season)
    return SeasonDupCalibration(season=season, ownership_curve=ownership_curve, trend_rates=trend_rates)


@dataclass(frozen=True)
class DupRiskStabilityResult:
    """The leave-one-out stability verdict across every season fit -- single global result, no
    position axis (see module docstring)."""

    season_correlations: dict[int, float]
    mean_correlation: float
    std_correlation: float
    is_stable: bool
    unstable_seasons: tuple[int, ...]


@dataclass(frozen=True)
class ProductionDupCalibration:
    """The resolved production ownership-decile dup-rate curve -- blended across all seasons if the
    stability test found no outliers, otherwise recent-window-only with older seasons held out as
    validation (ADR-0025's exact selection rule, `select_production_calibration`)."""

    source_seasons: tuple[int, ...]
    blended: bool
    decile_dup_rate: dict[int, float]
    n_rows: int


def compare_season_dup_calibrations(calibrations: Sequence[SeasonDupCalibration]) -> DupRiskStabilityResult:
    """Leave-one-out stability check (ADR-0025 section 3, reused unchanged): a season is flagged
    unstable if its own `ownership_dup_rate_correlation` deviates from the mean of every *other*
    fitted season by more than `_UNSTABLE_DEVIATION_MULTIPLIER` times those other seasons' standard
    deviation. Needs at least 3 seasons with a defined correlation to run at all -- fewer than that,
    "leave one out and compare to the rest" isn't a meaningful comparison (same guard
    `ownership_calibration.py`'s own `compare_season_calibrations` uses).
    """
    if len(calibrations) < 3:
        raise ValueError("need at least 3 seasons of calibrations to run a leave-one-out stability test")

    season_correlations = {
        c.season: c.ownership_curve.ownership_dup_rate_correlation
        for c in calibrations
        if c.ownership_curve.ownership_dup_rate_correlation is not None
    }
    if len(season_correlations) < 3:
        raise ValueError("fewer than 3 seasons have a defined ownership_dup_rate_correlation -- cannot run the stability test")

    seasons = sorted(season_correlations)
    unstable: list[int] = []
    for season in seasons:
        others = [season_correlations[s] for s in seasons if s != season]
        other_mean = statistics.mean(others)
        other_std = statistics.pstdev(others) if len(others) > 1 else 0.0
        deviation = abs(season_correlations[season] - other_mean)
        is_unstable = deviation > (_UNSTABLE_DEVIATION_MULTIPLIER * other_std) if other_std > 0 else deviation > 1e-9
        if is_unstable:
            unstable.append(season)

    values = list(season_correlations.values())
    return DupRiskStabilityResult(
        season_correlations=season_correlations,
        mean_correlation=statistics.mean(values),
        std_correlation=statistics.pstdev(values),
        is_stable=len(unstable) == 0,
        unstable_seasons=tuple(sorted(unstable)),
    )


def select_production_dup_calibration(
    calibrations: Sequence[SeasonDupCalibration],
    stability: DupRiskStabilityResult,
    *,
    recent_window: Sequence[int] = DEFAULT_RECENT_WINDOW,
) -> ProductionDupCalibration:
    """Blends all fitted seasons' decile-dup-rate curves (row-count-weighted) if the stability test
    found no outlier season; otherwise uses only `recent_window` seasons, holding the rest out as
    validation reference (ADR-0025's exact selection rule, `select_production_calibration`)."""
    by_season = {c.season: c for c in calibrations}

    if stability.is_stable:
        source_seasons = tuple(season for season in sorted(stability.season_correlations) if season in by_season)
    else:
        source_seasons = tuple(season for season in sorted(recent_window) if season in by_season)
        if not source_seasons:
            source_seasons = tuple(season for season in sorted(stability.season_correlations) if season in by_season)

    curves = [by_season[season].ownership_curve for season in source_seasons if season in by_season]
    decile_dup_rate: dict[int, float] = {}
    for decile in range(N_DECILES):
        weighted_sum = 0.0
        weight_total = 0
        for curve in curves:
            if decile in curve.decile_dup_rate:
                weighted_sum += curve.decile_dup_rate[decile] * curve.n_rows
                weight_total += curve.n_rows
        if weight_total > 0:
            decile_dup_rate[decile] = weighted_sum / weight_total

    return ProductionDupCalibration(
        source_seasons=source_seasons,
        blended=stability.is_stable,
        decile_dup_rate=decile_dup_rate,
        n_rows=sum(curve.n_rows for curve in curves),
    )


def run_dup_risk_calibration(seasons: Sequence[int] | None = None, *, base_dir: Path | None = None) -> dict[int, SeasonDupCalibration]:
    """Convenience wrapper: fits every requested season (or every season present in curated lineups
    storage, if `seasons` is omitted)."""
    if seasons is None:
        df = read_curated_lineups(base_dir=base_dir)
        if df.empty:
            raise ValueError("no curated ResultsDB lineups data found -- run the lineups backfill first")
        seasons = sorted(int(s) for s in df["season"].unique())
    return {season: fit_season_dup_calibration(season, base_dir=base_dir) for season in seasons}


def build_dup_risk_lookup_table(
    seasons: Sequence[int] | None = None, *, n_buckets: int = N_DECILES, base_dir: Path | None = None
) -> DupRiskLookupTable:
    """Builds the absolute-value lookup table (see `DupRiskLookupTable`'s own docstring for why this
    is a different construction from the per-contest-relative-rank `OwnershipDupCurve`). Global
    quantile cutpoints over every pooled lineup's `avg_own`, not re-derived per contest -- bucket 0 =
    the LOWEST `avg_own` bucket, ascending (the opposite convention from `OwnershipDupCurve`'s
    decile 0 = highest, a real, disclosed difference driven by `pandas.qcut`'s own natural ascending
    bin-edge order, not an arbitrary inconsistency)."""
    df = read_curated_lineups(base_dir=base_dir)
    if seasons is not None:
        df = df[df["season"].isin(seasons)]
    if df.empty:
        raise ValueError(f"no curated ResultsDB lineups data found for seasons={seasons!r} -- run the lineups backfill first")

    df = df.copy()
    df["is_duplicated"] = (df["lineup_ct"] > 1).astype(int)
    bucket, bin_edges = pd.qcut(df["avg_own"], n_buckets, labels=False, duplicates="drop", retbins=True)
    df["bucket"] = bucket

    dup_rate = df.groupby("bucket")["is_duplicated"].mean()
    mean_ct = df.groupby("bucket")["lineup_ct"].mean()
    # bin_edges has n_buckets+1 edges (including the -inf/+inf-equivalent outer bounds pandas uses);
    # the real, usable classification thresholds are the INNER edges only (drop the first and last).
    upper_bounds = tuple(float(edge) for edge in bin_edges[1:-1])

    return DupRiskLookupTable(
        seasons=tuple(sorted(int(s) for s in df["season"].unique())),
        n_rows=int(len(df)),
        bucket_upper_bounds=upper_bounds,
        bucket_dup_rate={int(b): float(v) for b, v in dup_rate.items()},
        bucket_mean_lineup_ct={int(b): float(v) for b, v in mean_ct.items()},
    )


def classify_avg_ownership(avg_own: float, table: DupRiskLookupTable) -> tuple[int, float | None, float | None]:
    """Classifies an arbitrary `avg_own` value (e.g. a brand-new candidate lineup's own average
    projected ownership across its 9 roster spots) against `table`'s real historical bucket
    breakpoints. Returns `(bucket, dup_rate, mean_lineup_ct)` -- `dup_rate`/`mean_lineup_ct` are
    `None` only if `table` itself has no rows for the resolved bucket (shouldn't happen for a table
    built from `build_dup_risk_lookup_table`, which always populates every non-empty bucket, but
    never assumed here). A value below the lowest bucket's floor or above the highest bucket's
    ceiling still resolves to bucket 0 or the last bucket respectively (clamped, not rejected) --
    the real historical experience closest to that value, same "don't refuse to answer" posture
    `ceiling/signals.py`'s shrinkage-not-hard-gate convention already uses elsewhere in this project.
    """
    bucket = 0
    for bound in table.bucket_upper_bounds:
        if avg_own <= bound:
            break
        bucket += 1
    max_bucket = len(table.bucket_dup_rate) - 1 if table.bucket_dup_rate else 0
    bucket = min(bucket, max_bucket)
    return bucket, table.bucket_dup_rate.get(bucket), table.bucket_mean_lineup_ct.get(bucket)
