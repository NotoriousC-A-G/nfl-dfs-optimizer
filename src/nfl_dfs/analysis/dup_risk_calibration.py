"""Per-season ResultsDB dup-risk calibration (ADR-0033, building on ADR-0025's ownership-propensity
calibration precedent and ADR-0032's `lineups/` backfill).

Fits, per season, how a real DK GPP lineup's chance of being duplicated by the field (`lineup_ct > 1`,
the literal count of contest entries that built that exact roster) relates to (1) that lineup's own
average field ownership, decile-ranked within its own contest the same way ADR-0025 decile-ranks salary
within position, and (2) specific structural trends the `lineups/` payload already computes per lineup
(`lineupTrends` -- e.g. `qbPairedWithPassCatcher`, `minOnePlayerWithLowOwnership`).

**Only 2 seasons of lineup data exist right now (2024-2025, ADR-0032's deliberately scoped-down
backfill)** -- ADR-0025's leave-one-out stability test explicitly requires at least 3 seasons to be a
meaningful comparison (`compare_season_calibrations`'s own guard) and this module does not lower that
bar just because fewer seasons happen to be available; `compare_two_seasons` here is a lighter,
honestly-labeled side-by-side report, not a stability verdict, and there is no `select_production_
calibration`-equivalent yet -- extending the backfill to more seasons (ADR-0032's own named follow-up)
is a real precondition for that, not a decision this module can make with n=2.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd

from nfl_dfs.storage.resultsdb_store import read_curated_lineups

N_DECILES = 10


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


def compare_two_seasons(calibrations: Sequence[SeasonDupCalibration]) -> str:
    """A lightweight, honestly-labeled side-by-side report of the ownership-dup correlation across
    whatever seasons were fit -- explicitly NOT `ownership_calibration.py`'s leave-one-out stability
    test (that needs >=3 seasons to be a meaningful comparison at all, see module docstring). Returns
    a human-readable summary string rather than a typed stability verdict, since there's no
    is_stable/unstable_seasons judgment this module is prepared to make on 2 data points.
    """
    lines = [f"season {c.season}: r(avg_own, is_duplicated)={c.ownership_curve.ownership_dup_rate_correlation!r}" for c in calibrations]
    return "\n".join(lines)


def run_dup_risk_calibration(seasons: Sequence[int] | None = None, *, base_dir: Path | None = None) -> dict[int, SeasonDupCalibration]:
    """Convenience wrapper: fits every requested season (or every season present in curated lineups
    storage, if `seasons` is omitted)."""
    if seasons is None:
        df = read_curated_lineups(base_dir=base_dir)
        if df.empty:
            raise ValueError("no curated ResultsDB lineups data found -- run the lineups backfill first")
        seasons = sorted(int(s) for s in df["season"].unique())
    return {season: fit_season_dup_calibration(season, base_dir=base_dir) for season in seasons}
