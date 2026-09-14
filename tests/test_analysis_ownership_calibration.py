import pytest

from nfl_dfs.analysis.ownership_calibration import (
    CORE_POSITIONS,
    CalibrationStabilityResult,
    SalaryOwnershipCurve,
    SeasonCalibration,
    compare_season_calibrations,
    fit_season_calibration,
    run_full_calibration,
    select_production_calibration,
)
from nfl_dfs.ingestion.rotogrinders_resultsdb import ContestSummary, PlayerExposureRow
from nfl_dfs.storage.resultsdb_store import write_curated_contest

# Perfectly monotonic: highest salary -> highest ownership, one player per decile, corr == 1.0 exactly.
_STANDARD_SALARIES = [10000, 9000, 8000, 7000, 6000, 5000, 4000, 3000, 2000, 1000]
_STANDARD_OWNERSHIP = [20.0, 18.0, 16.0, 14.0, 12.0, 10.0, 8.0, 6.0, 4.0, 2.0]
# Reversed: highest salary -> lowest ownership, corr == -1.0 exactly.
_OUTLIER_OWNERSHIP = list(reversed(_STANDARD_OWNERSHIP))


def _summary(contest_id: int, contest_date: str) -> ContestSummary:
    return ContestSummary(
        contest_id=contest_id,
        contest_name="Test Millionaire",
        contest_date=contest_date,
        entry_cost=20.0,
        contest_size=1000,
        cash_line=100,
        duplicate_lineups=5,
        unique_lineups=900,
        total_prizes=100_000.0,
    )


def _rows(position: str, salaries: list[float], ownerships: list[float]) -> list[PlayerExposureRow]:
    return [
        PlayerExposureRow(
            player_key=f"{position}-{i}:0",
            player_id=i,
            full_name=f"{position} Player {i}",
            position=position,
            team="KC",
            salary=int(salary),
            projected_points=None,
            actual_points=None,
            stat_details="",
            made_cut=1,
            ownership_overall=ownership,
        )
        for i, (salary, ownership) in enumerate(zip(salaries, ownerships))
    ]


def _write_season(
    tmp_path,
    season: int,
    ownership_pattern: list[float] = _STANDARD_OWNERSHIP,
    position: str = "WR",
    n_contests: int = 2,
) -> None:
    for contest_num in range(n_contests):
        date = f"{season}-09-{10 + contest_num:02d}"
        contest_id = season * 10 + contest_num
        rows = _rows(position, _STANDARD_SALARIES, ownership_pattern)
        write_curated_contest(date, season, contest_id, _summary(contest_id, date), rows, base_dir=tmp_path)


# ---------------------------------------------------------------------------
# fit_season_calibration
# ---------------------------------------------------------------------------


def test_fit_season_calibration_raises_when_no_data(tmp_path):
    with pytest.raises(ValueError, match="2024"):
        fit_season_calibration(2024, base_dir=tmp_path)


def test_fit_season_calibration_decile_ownership_and_correlation(tmp_path):
    _write_season(tmp_path, 2023)
    calib = fit_season_calibration(2023, base_dir=tmp_path)

    assert calib.season == 2023
    assert calib.is_regime_flagged is False
    assert calib.regime_note is None
    assert set(calib.curves) == {"WR"}

    curve = calib.curves["WR"]
    assert curve.n_contests == 2
    assert curve.n_rows == 20
    # One player per decile per contest; both contests identical, so each decile's mean == that player's own
    # ownership value.
    for decile, expected_ownership in enumerate(_STANDARD_OWNERSHIP):
        assert curve.decile_ownership[decile] == pytest.approx(expected_ownership)
    assert curve.salary_ownership_correlation == pytest.approx(1.0)


def test_fit_season_calibration_flags_2020_as_regime(tmp_path):
    _write_season(tmp_path, 2020)
    calib = fit_season_calibration(2020, base_dir=tmp_path)
    assert calib.is_regime_flagged is True
    assert "COVID" in calib.regime_note


def test_fit_season_calibration_excludes_non_core_positions(tmp_path):
    _write_season(tmp_path, 2023, position="WR")
    _write_season(tmp_path, 2023, position="K", n_contests=1)
    calib = fit_season_calibration(2023, base_dir=tmp_path)
    assert "K" not in calib.curves
    assert "WR" in calib.curves


# ---------------------------------------------------------------------------
# compare_season_calibrations
# ---------------------------------------------------------------------------


def test_compare_season_calibrations_requires_at_least_three_seasons():
    with pytest.raises(ValueError, match="at least 3"):
        compare_season_calibrations(
            [
                SeasonCalibration(season=2023, curves={}, is_regime_flagged=False, regime_note=None),
                SeasonCalibration(season=2024, curves={}, is_regime_flagged=False, regime_note=None),
            ]
        )


def test_compare_season_calibrations_flags_the_one_outlier_season(tmp_path):
    for season in (2020, 2021, 2023, 2024, 2025):
        _write_season(tmp_path, season, ownership_pattern=_STANDARD_OWNERSHIP)
    _write_season(tmp_path, 2022, ownership_pattern=_OUTLIER_OWNERSHIP)

    calibrations = [fit_season_calibration(season, base_dir=tmp_path) for season in range(2020, 2026)]
    stability = compare_season_calibrations(calibrations)

    wr_stability = stability["WR"]
    assert wr_stability.season_correlations[2022] == pytest.approx(-1.0)
    assert wr_stability.season_correlations[2023] == pytest.approx(1.0)
    assert wr_stability.is_stable is False
    assert wr_stability.unstable_seasons == (2022,)


def test_compare_season_calibrations_stable_when_all_seasons_agree(tmp_path):
    for season in range(2020, 2026):
        _write_season(tmp_path, season, ownership_pattern=_STANDARD_OWNERSHIP)
    calibrations = [fit_season_calibration(season, base_dir=tmp_path) for season in range(2020, 2026)]
    stability = compare_season_calibrations(calibrations)
    assert stability["WR"].is_stable is True
    assert stability["WR"].unstable_seasons == ()


# ---------------------------------------------------------------------------
# select_production_calibration
# ---------------------------------------------------------------------------


def test_select_production_calibration_blends_when_stable(tmp_path):
    for season in range(2020, 2026):
        _write_season(tmp_path, season, ownership_pattern=_STANDARD_OWNERSHIP)
    calibrations = [fit_season_calibration(season, base_dir=tmp_path) for season in range(2020, 2026)]
    stability = compare_season_calibrations(calibrations)
    production = select_production_calibration(calibrations, stability)

    wr = production["WR"]
    assert wr.blended is True
    assert wr.source_seasons == (2020, 2021, 2022, 2023, 2024, 2025)
    for decile, expected_ownership in enumerate(_STANDARD_OWNERSHIP):
        assert wr.decile_ownership[decile] == pytest.approx(expected_ownership)


def test_select_production_calibration_uses_recent_window_when_unstable(tmp_path):
    for season in (2020, 2021, 2023, 2024, 2025):
        _write_season(tmp_path, season, ownership_pattern=_STANDARD_OWNERSHIP)
    _write_season(tmp_path, 2022, ownership_pattern=_OUTLIER_OWNERSHIP)

    calibrations = [fit_season_calibration(season, base_dir=tmp_path) for season in range(2020, 2026)]
    stability = compare_season_calibrations(calibrations)
    production = select_production_calibration(calibrations, stability)

    wr = production["WR"]
    assert wr.blended is False
    assert wr.source_seasons == (2023, 2024, 2025)
    for decile, expected_ownership in enumerate(_STANDARD_OWNERSHIP):
        assert wr.decile_ownership[decile] == pytest.approx(expected_ownership)


def test_select_production_calibration_falls_back_when_recent_window_has_no_data():
    stability = {
        "WR": CalibrationStabilityResult(
            position="WR",
            season_correlations={2018: 0.5, 2019: -0.5, 2020: 0.9},
            mean_correlation=0.3,
            std_correlation=0.6,
            is_stable=False,
            unstable_seasons=(2019,),
        )
    }
    curve = SalaryOwnershipCurve(
        position="WR", season=2018, n_contests=1, n_rows=10, decile_ownership={0: 20.0}, salary_ownership_correlation=0.5
    )
    calibrations = [SeasonCalibration(season=2018, curves={"WR": curve}, is_regime_flagged=False, regime_note=None)]
    production = select_production_calibration(calibrations, stability, recent_window=(2023, 2024, 2025))
    assert production["WR"].source_seasons == (2018,)


# ---------------------------------------------------------------------------
# run_full_calibration
# ---------------------------------------------------------------------------


def test_run_full_calibration_raises_when_no_data(tmp_path):
    with pytest.raises(ValueError, match="backfill"):
        run_full_calibration(base_dir=tmp_path)


def test_run_full_calibration_end_to_end(tmp_path):
    for season in range(2020, 2026):
        _write_season(tmp_path, season, ownership_pattern=_STANDARD_OWNERSHIP)

    bundle = run_full_calibration(base_dir=tmp_path)

    assert set(bundle.season_calibrations) == set(range(2020, 2026))
    assert bundle.season_calibrations[2020].is_regime_flagged is True
    assert "WR" in bundle.stability
    assert bundle.stability["WR"].is_stable is True
    assert bundle.production["WR"].blended is True
    assert set(CORE_POSITIONS) == {"QB", "RB", "WR", "TE", "D"}
