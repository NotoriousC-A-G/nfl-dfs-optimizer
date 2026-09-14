"""Per-season ResultsDB ownership-propensity calibration and the blend/recent stability test (ADR-0025,
building on ADR-0023's recommendation and ADR-0024's storage layer).

Fits, per `(season, position)`, how real DK GPP field ownership distributes across salary deciles within
that position's rostered pool -- the field-composition baseline the eventual Section 5 step 7 leverage layer
needs, not a projection-accuracy backtest (ADR-0023 section 6/ADR-0018 section 4 already establish why
historical vendor projections can't answer that question). Six seasons is too few points for a real
hypothesis test; `compare_season_calibrations`'s leave-one-out check is a stated-as-such heuristic, not a
claim of statistical rigor it can't support.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd

from nfl_dfs.storage.resultsdb_store import read_curated_player_exposures

# The only positions with a non-trivial, DK-rosterable row count in the real data (ADR-0025 section 2) --
# FB/LS/K/LB/CB/DL/DE/MLB are single-digit-to-low-hundreds artifacts of the payload's raw position tagging,
# not a real drafted pool, and are excluded from fitting.
CORE_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "D")

N_DECILES = 10

# 2020 is fit and included in every comparison below, never silently excluded -- this is a permanent domain
# annotation (COVID-affected, closed-stadium-heavy NFL season, ADR-0023 section 5 point 2), not something a
# six-point leave-one-out test should be relied on alone to catch.
REGIME_FLAGGED_SEASONS: dict[int, str] = {
    2020: (
        "2020 was a COVID-affected, closed-stadium/attendance-anomaly NFL season (ADR-0023 section 5 point 2)"
        " -- fit and included in the stability comparison, not silently excluded, but flagged as an atypical"
        " regime."
    ),
}

DEFAULT_RECENT_WINDOW: tuple[int, ...] = (2023, 2024, 2025)

# Multiplier on the leave-one-out standard deviation of the *other* seasons beyond which a season's own
# correlation is flagged unstable (ADR-0025 section 3).
_UNSTABLE_DEVIATION_MULTIPLIER = 1.5


@dataclass(frozen=True)
class SalaryOwnershipCurve:
    """One position's empirical field-ownership-by-salary-decile curve for one season."""

    position: str
    season: int
    n_contests: int
    n_rows: int
    decile_ownership: dict[int, float]
    salary_ownership_correlation: float | None


@dataclass(frozen=True)
class SeasonCalibration:
    """Every position's `SalaryOwnershipCurve` for one season, plus the regime-flag annotation."""

    season: int
    curves: dict[str, SalaryOwnershipCurve]
    is_regime_flagged: bool
    regime_note: str | None


@dataclass(frozen=True)
class CalibrationStabilityResult:
    """The leave-one-out stability verdict for one position, across every season it was fit."""

    position: str
    season_correlations: dict[int, float]
    mean_correlation: float
    std_correlation: float
    is_stable: bool
    unstable_seasons: tuple[int, ...]


@dataclass(frozen=True)
class ProductionCalibration:
    """The resolved production decile curve for one position -- blended across all seasons if the stability
    test found no outliers, otherwise recent-window-only with older seasons held out as validation.
    """

    position: str
    source_seasons: tuple[int, ...]
    blended: bool
    decile_ownership: dict[int, float]
    n_rows: int


@dataclass(frozen=True)
class CalibrationBundle:
    season_calibrations: dict[int, SeasonCalibration]
    stability: dict[str, CalibrationStabilityResult]
    production: dict[str, ProductionCalibration]


def _assign_salary_deciles(df: pd.DataFrame) -> pd.Series:
    """Decile 0 = the highest-salary tenth of that position's rostered pool in that contest, 9 = lowest.
    Ranked within `(date, contest_id, position)` -- a QB's salary is never compared against a TE's.
    """
    group_keys = ["date", "contest_id", "position"]
    ranks = df.groupby(group_keys)["salary"].rank(method="first", ascending=False) - 1
    sizes = df.groupby(group_keys)["salary"].transform("size")
    raw_decile = (ranks / sizes * N_DECILES).astype(int)
    return raw_decile.clip(upper=N_DECILES - 1)


def fit_season_calibration(season: int, *, base_dir: Path | None = None) -> SeasonCalibration:
    """Fits one season's per-position salary-ownership curves from the curated ResultsDB player-exposure
    rows already on disk (ADR-0024). Raises if that season has no curated data -- never silently fits an
    empty/placeholder calibration.
    """
    df = read_curated_player_exposures(season=season, base_dir=base_dir)
    if df.empty:
        raise ValueError(f"no curated ResultsDB player_exposures data found for season {season}")

    df = df[df["position"].isin(CORE_POSITIONS)].copy()
    df["salary_decile"] = _assign_salary_deciles(df)

    curves: dict[str, SalaryOwnershipCurve] = {}
    for position in CORE_POSITIONS:
        pos_df = df[df["position"] == position]
        if pos_df.empty:
            continue
        decile_means = pos_df.groupby("salary_decile")["ownership_overall"].mean()
        decile_ownership = {int(decile): float(value) for decile, value in decile_means.items()}
        n_contests = pos_df[["date", "contest_id"]].drop_duplicates().shape[0]
        correlation = (
            float(pos_df["salary"].corr(pos_df["ownership_overall"])) if pos_df["salary"].nunique() >= 2 else None
        )
        curves[position] = SalaryOwnershipCurve(
            position=position,
            season=season,
            n_contests=n_contests,
            n_rows=int(len(pos_df)),
            decile_ownership=decile_ownership,
            salary_ownership_correlation=correlation,
        )

    return SeasonCalibration(
        season=season,
        curves=curves,
        is_regime_flagged=season in REGIME_FLAGGED_SEASONS,
        regime_note=REGIME_FLAGGED_SEASONS.get(season),
    )


def compare_season_calibrations(
    calibrations: Sequence[SeasonCalibration],
) -> dict[str, CalibrationStabilityResult]:
    """Leave-one-out stability check per position (ADR-0025 section 3): a season is flagged unstable if its
    own salary-ownership correlation deviates from the mean of every *other* fitted season by more than
    `_UNSTABLE_DEVIATION_MULTIPLIER` times those other seasons' standard deviation. Needs at least 3 seasons
    with a defined correlation for a given position to run at all -- fewer than that, "leave one out and
    compare to the rest" isn't a meaningful comparison.
    """
    if len(calibrations) < 3:
        raise ValueError("need at least 3 seasons of calibrations to run a leave-one-out stability test")

    results: dict[str, CalibrationStabilityResult] = {}
    for position in CORE_POSITIONS:
        season_correlations = {
            calib.season: calib.curves[position].salary_ownership_correlation
            for calib in calibrations
            if position in calib.curves and calib.curves[position].salary_ownership_correlation is not None
        }
        if len(season_correlations) < 3:
            continue

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
        results[position] = CalibrationStabilityResult(
            position=position,
            season_correlations=season_correlations,
            mean_correlation=statistics.mean(values),
            std_correlation=statistics.pstdev(values),
            is_stable=len(unstable) == 0,
            unstable_seasons=tuple(sorted(unstable)),
        )
    return results


def select_production_calibration(
    calibrations: Sequence[SeasonCalibration],
    stability: dict[str, CalibrationStabilityResult],
    *,
    recent_window: Sequence[int] = DEFAULT_RECENT_WINDOW,
) -> dict[str, ProductionCalibration]:
    """Blends all fitted seasons' decile curves (row-count-weighted) if the stability test found no outlier
    season for that position; otherwise uses only `recent_window` seasons, holding the rest out as
    validation reference (ADR-0023 section 5 point 3 / ADR-0025 section 4).
    """
    by_season = {calib.season: calib for calib in calibrations}
    production: dict[str, ProductionCalibration] = {}

    for position, stab in stability.items():
        if stab.is_stable:
            source_seasons = tuple(sorted(stab.season_correlations))
        else:
            source_seasons = tuple(
                season for season in sorted(recent_window) if season in by_season and position in by_season[season].curves
            )
            if not source_seasons:
                source_seasons = tuple(
                    season
                    for season in sorted(stab.season_correlations)
                    if season in by_season and position in by_season[season].curves
                )

        curves = [by_season[season].curves[position] for season in source_seasons if position in by_season[season].curves]
        if not curves:
            continue

        decile_ownership: dict[int, float] = {}
        for decile in range(N_DECILES):
            weighted_sum = 0.0
            weight_total = 0
            for curve in curves:
                if decile in curve.decile_ownership:
                    weighted_sum += curve.decile_ownership[decile] * curve.n_rows
                    weight_total += curve.n_rows
            if weight_total > 0:
                decile_ownership[decile] = weighted_sum / weight_total

        production[position] = ProductionCalibration(
            position=position,
            source_seasons=source_seasons,
            blended=stab.is_stable,
            decile_ownership=decile_ownership,
            n_rows=sum(curve.n_rows for curve in curves),
        )

    return production


def run_full_calibration(*, base_dir: Path | None = None) -> CalibrationBundle:
    """Convenience wrapper: fits every season present in curated storage, runs the stability test, and
    selects production calibrations, returning all three as one bundle.
    """
    df = read_curated_player_exposures(base_dir=base_dir)
    if df.empty:
        raise ValueError("no curated ResultsDB player_exposures data found -- run the backfill first")

    seasons = sorted(int(season) for season in df["season"].unique())
    season_calibrations = {season: fit_season_calibration(season, base_dir=base_dir) for season in seasons}
    stability = compare_season_calibrations(list(season_calibrations.values()))
    production = select_production_calibration(list(season_calibrations.values()), stability)
    return CalibrationBundle(season_calibrations=season_calibrations, stability=stability, production=production)
