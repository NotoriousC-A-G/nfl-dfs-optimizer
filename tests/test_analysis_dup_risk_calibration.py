import pytest

from nfl_dfs.analysis.dup_risk_calibration import (
    DupRiskLookupTable,
    DupRiskStabilityResult,
    OwnershipDupCurve,
    ProductionDupCalibration,
    SeasonDupCalibration,
    TrendDupRate,
    build_dup_risk_lookup_table,
    classify_avg_ownership,
    compare_season_dup_calibrations,
    fit_season_dup_calibration,
    run_dup_risk_calibration,
    select_production_dup_calibration,
)
from nfl_dfs.ingestion.rotogrinders_resultsdb import LineupRow
from nfl_dfs.storage.resultsdb_store import write_curated_lineups

# Perfectly monotonic: highest avg_own -> highest lineup_ct, one lineup per decile, corr should come
# back strongly positive (not necessarily exactly 1.0, since is_duplicated is a 0/1 indicator not a
# continuous target -- see the correlation test below for the exact expected shape).
_STANDARD_AVG_OWN = [50.0, 45.0, 40.0, 35.0, 30.0, 25.0, 20.0, 15.0, 10.0, 5.0]
_STANDARD_LINEUP_CT = [10, 5, 3, 2, 1, 1, 1, 1, 1, 1]  # only the top 4 deciles are ever duplicated
# Reversed: lowest avg_own -> highest lineup_ct, a real outlier-season pattern for the stability test.
_OUTLIER_LINEUP_CT = list(reversed(_STANDARD_LINEUP_CT))


def _lineup_row(lineup_hash: str, avg_own: float, lineup_ct: int, *, trends: dict | None = None) -> LineupRow:
    return LineupRow(
        lineup_hash=lineup_hash,
        lineup_ct=lineup_ct,
        lineup_user_ct=min(lineup_ct, 1),
        lineup_players={"QB1": 1},
        points=200.0,
        total_salary=50000,
        total_own=avg_own * 9,
        min_own=avg_own / 2,
        max_own=avg_own * 2,
        avg_own=avg_own,
        lineup_rank=1,
        is_cashing=True,
        payout=0.0,
        lineup_percentile=0.0,
        favorite_ct=1,
        underdog_ct=1,
        home_ct=1,
        visitor_ct=1,
        correlated_players=1,
        team_stacks={},
        game_stacks={},
        lineup_trends=trends or {},
        entry_name_list=["x"] * lineup_ct,
    )


def _write_season(tmp_path, season: int, avg_owns: list[float] = _STANDARD_AVG_OWN, lineup_cts: list[int] = _STANDARD_LINEUP_CT) -> None:
    rows = [
        _lineup_row(f"h{i}", avg_own, ct, trends={"qbPairedWithPassCatcher": i % 2 == 0})
        for i, (avg_own, ct) in enumerate(zip(avg_owns, lineup_cts))
    ]
    write_curated_lineups(f"{season}-09-08", season, season * 10, rows, base_dir=tmp_path)


# ---------------------------------------------------------------------------
# fit_season_dup_calibration
# ---------------------------------------------------------------------------


def test_fit_season_dup_calibration_raises_when_no_data(tmp_path):
    with pytest.raises(ValueError, match="2024"):
        fit_season_dup_calibration(2024, base_dir=tmp_path)


def test_fit_season_dup_calibration_ownership_curve_decile_shape(tmp_path):
    _write_season(tmp_path, 2024)
    calib = fit_season_dup_calibration(2024, base_dir=tmp_path)

    oc = calib.ownership_curve
    assert oc.n_rows == 10
    assert oc.n_contests == 1
    # Decile 0 = highest avg_own (50.0, lineup_ct=10 -> is_duplicated=1.0); decile 9 = lowest
    # avg_own (5.0, lineup_ct=1 -> is_duplicated=0.0).
    assert oc.decile_dup_rate[0] == pytest.approx(1.0)
    assert oc.decile_dup_rate[9] == pytest.approx(0.0)
    assert oc.decile_mean_lineup_ct[0] == pytest.approx(10.0)
    assert oc.decile_mean_lineup_ct[9] == pytest.approx(1.0)


def test_fit_season_dup_calibration_ownership_correlation_is_positive_when_chalk_duplicates_more(tmp_path):
    _write_season(tmp_path, 2024)
    calib = fit_season_dup_calibration(2024, base_dir=tmp_path)
    assert calib.ownership_curve.ownership_dup_rate_correlation > 0.5


def test_fit_season_dup_calibration_pools_across_multiple_contests(tmp_path):
    rows_a = [_lineup_row("a0", 50.0, 5), _lineup_row("a1", 10.0, 1)]
    rows_b = [_lineup_row("b0", 50.0, 3), _lineup_row("b1", 10.0, 1)]
    write_curated_lineups("2024-09-08", 2024, 1, rows_a, base_dir=tmp_path)
    write_curated_lineups("2024-09-15", 2024, 2, rows_b, base_dir=tmp_path)

    calib = fit_season_dup_calibration(2024, base_dir=tmp_path)
    assert calib.ownership_curve.n_contests == 2
    assert calib.ownership_curve.n_rows == 4


# ---------------------------------------------------------------------------
# trend dup rates
# ---------------------------------------------------------------------------


def test_trend_dup_rate_splits_true_vs_false_and_ignores_missing_keys(tmp_path):
    rows = [
        _lineup_row("t0", 50.0, 5, trends={"stackFlag": True}),
        _lineup_row("t1", 40.0, 1, trends={"stackFlag": True}),
        _lineup_row("t2", 30.0, 5, trends={"stackFlag": False}),
        _lineup_row("t3", 20.0, 1, trends={"stackFlag": False}),
        _lineup_row("t4", 10.0, 1, trends={}),  # missing the key entirely -- must not count either way
    ]
    write_curated_lineups("2024-09-08", 2024, 1, rows, base_dir=tmp_path)
    calib = fit_season_dup_calibration(2024, base_dir=tmp_path)

    tr = calib.trend_rates["stackFlag"]
    assert tr.n_true == 2
    assert tr.n_false == 2
    assert tr.dup_rate_true == pytest.approx(0.5)  # one of two duplicated (lineup_ct>1)
    assert tr.dup_rate_false == pytest.approx(0.5)


def test_trend_dup_rate_none_when_key_never_appears(tmp_path):
    rows = [_lineup_row("t0", 50.0, 1, trends={"onlyKey": True})]
    write_curated_lineups("2024-09-08", 2024, 1, rows, base_dir=tmp_path)
    calib = fit_season_dup_calibration(2024, base_dir=tmp_path)

    tr = calib.trend_rates["onlyKey"]
    assert tr.n_false == 0
    assert tr.dup_rate_false is None


# ---------------------------------------------------------------------------
# compare_season_dup_calibrations / select_production_dup_calibration
# ---------------------------------------------------------------------------


def test_compare_season_dup_calibrations_requires_at_least_three_seasons():
    with pytest.raises(ValueError, match="at least 3"):
        compare_season_dup_calibrations(
            [
                SeasonDupCalibration(season=2024, ownership_curve=OwnershipDupCurve(2024, 1, 10, {}, {}, 0.5), trend_rates={}),
                SeasonDupCalibration(season=2025, ownership_curve=OwnershipDupCurve(2025, 1, 10, {}, {}, 0.5), trend_rates={}),
            ]
        )


def test_compare_season_dup_calibrations_flags_the_one_outlier_season(tmp_path):
    for season in (2020, 2021, 2023, 2024, 2025):
        _write_season(tmp_path, season, lineup_cts=_STANDARD_LINEUP_CT)
    _write_season(tmp_path, 2022, lineup_cts=_OUTLIER_LINEUP_CT)

    calibrations = [fit_season_dup_calibration(season, base_dir=tmp_path) for season in range(2020, 2026)]
    stability = compare_season_dup_calibrations(calibrations)

    assert stability.season_correlations[2022] < 0  # the reversed pattern -- negative correlation
    assert stability.season_correlations[2023] > 0
    assert stability.is_stable is False
    assert stability.unstable_seasons == (2022,)


def test_compare_season_dup_calibrations_stable_when_all_seasons_agree(tmp_path):
    for season in range(2020, 2026):
        _write_season(tmp_path, season, lineup_cts=_STANDARD_LINEUP_CT)
    calibrations = [fit_season_dup_calibration(season, base_dir=tmp_path) for season in range(2020, 2026)]
    stability = compare_season_dup_calibrations(calibrations)
    assert stability.is_stable is True
    assert stability.unstable_seasons == ()


def test_select_production_dup_calibration_blends_when_stable(tmp_path):
    for season in range(2020, 2026):
        _write_season(tmp_path, season, lineup_cts=_STANDARD_LINEUP_CT)
    calibrations = [fit_season_dup_calibration(season, base_dir=tmp_path) for season in range(2020, 2026)]
    stability = compare_season_dup_calibrations(calibrations)
    production = select_production_dup_calibration(calibrations, stability)

    assert production.blended is True
    assert production.source_seasons == (2020, 2021, 2022, 2023, 2024, 2025)


def test_select_production_dup_calibration_uses_recent_window_when_unstable(tmp_path):
    for season in (2020, 2021, 2023, 2024, 2025):
        _write_season(tmp_path, season, lineup_cts=_STANDARD_LINEUP_CT)
    _write_season(tmp_path, 2022, lineup_cts=_OUTLIER_LINEUP_CT)

    calibrations = [fit_season_dup_calibration(season, base_dir=tmp_path) for season in range(2020, 2026)]
    stability = compare_season_dup_calibrations(calibrations)
    production = select_production_dup_calibration(calibrations, stability)

    assert production.blended is False
    assert production.source_seasons == (2023, 2024, 2025)


def test_select_production_dup_calibration_falls_back_when_recent_window_has_no_data():
    stability = DupRiskStabilityResult(
        season_correlations={2018: 0.5, 2019: -0.5, 2020: 0.9},
        mean_correlation=0.3,
        std_correlation=0.6,
        is_stable=False,
        unstable_seasons=(2019,),
    )
    curve = OwnershipDupCurve(season=2018, n_contests=1, n_rows=10, decile_dup_rate={0: 0.2}, decile_mean_lineup_ct={0: 1.2}, ownership_dup_rate_correlation=0.5)
    calibrations = [SeasonDupCalibration(season=2018, ownership_curve=curve, trend_rates={})]
    production = select_production_dup_calibration(calibrations, stability, recent_window=(2023, 2024, 2025))
    assert production.source_seasons == (2018,)


# ---------------------------------------------------------------------------
# run_dup_risk_calibration
# ---------------------------------------------------------------------------


def test_run_dup_risk_calibration_defaults_to_every_season_present(tmp_path):
    _write_season(tmp_path, 2024)
    _write_season(tmp_path, 2025)
    bundle = run_dup_risk_calibration(base_dir=tmp_path)
    assert set(bundle) == {2024, 2025}


def test_run_dup_risk_calibration_raises_when_nothing_backfilled(tmp_path):
    with pytest.raises(ValueError, match="lineups backfill"):
        run_dup_risk_calibration(base_dir=tmp_path)


# ---------------------------------------------------------------------------
# DupRiskLookupTable / classify_avg_ownership
# ---------------------------------------------------------------------------


def _write_many(tmp_path, season: int, n: int = 50) -> None:
    # A real spread of avg_own values, high-own lineups duplicated far more often than low-own --
    # enough rows (>= n_buckets) for pd.qcut to form real quantile buckets.
    rows = []
    for i in range(n):
        avg_own = 5.0 + i  # 5.0 .. 54.0
        lineup_ct = 10 if avg_own > 40 else 1
        rows.append(_lineup_row(f"h{i}", avg_own, lineup_ct))
    write_curated_lineups(f"{season}-09-08", season, season * 10, rows, base_dir=tmp_path)


def test_build_dup_risk_lookup_table_raises_when_no_data(tmp_path):
    with pytest.raises(ValueError, match="lineups backfill"):
        build_dup_risk_lookup_table(base_dir=tmp_path)


def test_build_dup_risk_lookup_table_higher_ownership_buckets_have_higher_dup_rate(tmp_path):
    _write_many(tmp_path, 2024)
    table = build_dup_risk_lookup_table(base_dir=tmp_path, n_buckets=5)

    assert table.seasons == (2024,)
    assert table.n_rows == 50
    buckets = sorted(table.bucket_dup_rate)
    # bucket 0 = LOWEST avg_own (this table's ascending convention, disclosed in its own docstring
    # -- the opposite of OwnershipDupCurve's decile-0-is-highest convention).
    assert table.bucket_dup_rate[buckets[0]] <= table.bucket_dup_rate[buckets[-1]]
    assert table.bucket_dup_rate[buckets[-1]] > 0.5  # the top bucket is mostly the duplicated (>40) rows


def test_build_dup_risk_lookup_table_filters_to_requested_seasons(tmp_path):
    _write_many(tmp_path, 2024)
    _write_many(tmp_path, 2025)
    table = build_dup_risk_lookup_table([2024], base_dir=tmp_path)
    assert table.seasons == (2024,)
    assert table.n_rows == 50


def test_classify_avg_ownership_low_value_falls_in_bucket_zero():
    table = DupRiskLookupTable(
        seasons=(2024,), n_rows=100,
        bucket_upper_bounds=(10.0, 20.0, 30.0),
        bucket_dup_rate={0: 0.01, 1: 0.02, 2: 0.05, 3: 0.20},
        bucket_mean_lineup_ct={0: 1.01, 1: 1.02, 2: 1.05, 3: 1.30},
    )
    assert classify_avg_ownership(5.0, table) == (0, 0.01, 1.01)
    assert classify_avg_ownership(10.0, table) == (0, 0.01, 1.01)  # inclusive at the boundary
    assert classify_avg_ownership(15.0, table) == (1, 0.02, 1.02)
    assert classify_avg_ownership(25.0, table) == (2, 0.05, 1.05)


def test_classify_avg_ownership_clamps_above_the_highest_bucket():
    table = DupRiskLookupTable(
        seasons=(2024,), n_rows=100,
        bucket_upper_bounds=(10.0, 20.0),
        bucket_dup_rate={0: 0.01, 1: 0.02, 2: 0.05},
        bucket_mean_lineup_ct={0: 1.01, 1: 1.02, 2: 1.05},
    )
    assert classify_avg_ownership(999.0, table) == (2, 0.05, 1.05)
