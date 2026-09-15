import pytest

from nfl_dfs.analysis.dup_risk_calibration import (
    OwnershipDupCurve,
    SeasonDupCalibration,
    TrendDupRate,
    compare_two_seasons,
    fit_season_dup_calibration,
    run_dup_risk_calibration,
)
from nfl_dfs.ingestion.rotogrinders_resultsdb import LineupRow
from nfl_dfs.storage.resultsdb_store import write_curated_lineups

# Perfectly monotonic: highest avg_own -> highest lineup_ct, one lineup per decile, corr should come
# back strongly positive (not necessarily exactly 1.0, since is_duplicated is a 0/1 indicator not a
# continuous target -- see the correlation test below for the exact expected shape).
_STANDARD_AVG_OWN = [50.0, 45.0, 40.0, 35.0, 30.0, 25.0, 20.0, 15.0, 10.0, 5.0]
_STANDARD_LINEUP_CT = [10, 5, 3, 2, 1, 1, 1, 1, 1, 1]  # only the top 4 deciles are ever duplicated


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
# compare_two_seasons / run_dup_risk_calibration
# ---------------------------------------------------------------------------


def test_compare_two_seasons_reports_each_seasons_correlation(tmp_path):
    _write_season(tmp_path, 2024)
    _write_season(tmp_path, 2025)
    bundle = run_dup_risk_calibration([2024, 2025], base_dir=tmp_path)

    report = compare_two_seasons(list(bundle.values()))
    assert "season 2024" in report
    assert "season 2025" in report


def test_run_dup_risk_calibration_defaults_to_every_season_present(tmp_path):
    _write_season(tmp_path, 2024)
    _write_season(tmp_path, 2025)
    bundle = run_dup_risk_calibration(base_dir=tmp_path)
    assert set(bundle) == {2024, 2025}


def test_run_dup_risk_calibration_raises_when_nothing_backfilled(tmp_path):
    with pytest.raises(ValueError, match="lineups backfill"):
        run_dup_risk_calibration(base_dir=tmp_path)
