import pytest

from nfl_dfs.analysis.dup_risk_calibration import DupRiskLookupTable
from nfl_dfs.analysis.dup_risk_lineup_backtest import (
    fit_strong_lineup_dup_analysis,
    fit_strong_lineup_dup_analysis_pooled,
)
from nfl_dfs.ingestion.rotogrinders_resultsdb import LineupRow
from nfl_dfs.storage.resultsdb_store import write_curated_lineups

_TABLE = DupRiskLookupTable(
    seasons=(2024, 2025),
    n_rows=1000,
    bucket_upper_bounds=(10.0, 20.0, 30.0),
    bucket_dup_rate={0: 0.01, 1: 0.02, 2: 0.05, 3: 0.20},
    bucket_mean_lineup_ct={0: 1.01, 1: 1.02, 2: 1.05, 3: 1.30},
)


def _row(lineup_hash: str, avg_own: float, points: float, lineup_ct: int) -> LineupRow:
    return LineupRow(
        lineup_hash=lineup_hash, lineup_ct=lineup_ct, lineup_user_ct=1, lineup_players={"QB1": 1},
        points=points, total_salary=50000, total_own=avg_own * 9, min_own=avg_own / 2, max_own=avg_own * 2,
        avg_own=avg_own, lineup_rank=1, is_cashing=True, payout=0.0, lineup_percentile=0.0,
        favorite_ct=1, underdog_ct=1, home_ct=1, visitor_ct=1, correlated_players=1,
        team_stacks={}, game_stacks={}, lineup_trends={}, entry_name_list=["x"],
    )


def test_fit_strong_lineup_dup_analysis_keeps_only_top_pct_by_real_points(tmp_path):
    # 100 lineups, points 1..100, avg_own fixed at 5.0 (bucket 0) -- top 10% by points (top_pct=0.1)
    # keeps only the 10 highest-scoring lineups.
    rows = [_row(f"h{i}", 5.0, float(i), 1) for i in range(1, 101)]
    write_curated_lineups("2024-09-08", 2024, 1, rows, base_dir=tmp_path)

    analysis = fit_strong_lineup_dup_analysis(2024, _TABLE, top_pct=0.1, base_dir=tmp_path)
    assert analysis.n_total_rows == 100
    assert analysis.n_strong_rows == 10
    assert analysis.bucket_stats[0].mean_points == pytest.approx(95.5)  # mean of 91..100


def test_fit_strong_lineup_dup_analysis_classifies_via_the_real_lookup_table(tmp_path):
    rows = [
        _row("low", 5.0, 200.0, 1),   # bucket 0
        _row("mid", 15.0, 200.0, 1),  # bucket 1
        _row("high", 25.0, 200.0, 5),  # bucket 2, duplicated
    ]
    write_curated_lineups("2024-09-08", 2024, 1, rows, base_dir=tmp_path)

    analysis = fit_strong_lineup_dup_analysis(2024, _TABLE, top_pct=1.0, base_dir=tmp_path)  # keep all 3
    assert set(analysis.bucket_stats) == {0, 1, 2}
    assert analysis.bucket_stats[2].dup_rate == pytest.approx(1.0)
    assert analysis.bucket_stats[0].dup_rate == pytest.approx(0.0)


def test_fit_strong_lineup_dup_analysis_raises_when_no_data(tmp_path):
    with pytest.raises(ValueError, match="2024"):
        fit_strong_lineup_dup_analysis(2024, _TABLE, base_dir=tmp_path)


def test_fit_strong_lineup_dup_analysis_pooled_combines_seasons_weighted_by_n(tmp_path):
    write_curated_lineups("2024-09-08", 2024, 1, [_row("a", 5.0, 100.0, 1), _row("b", 5.0, 200.0, 1)], base_dir=tmp_path)
    write_curated_lineups("2025-09-08", 2025, 2, [_row("c", 5.0, 300.0, 1)], base_dir=tmp_path)

    pooled = fit_strong_lineup_dup_analysis_pooled([2024, 2025], _TABLE, top_pct=1.0, base_dir=tmp_path)
    assert pooled.season is None
    assert pooled.n_strong_rows == 3
    assert pooled.bucket_stats[0].n == 3
    assert pooled.bucket_stats[0].mean_points == pytest.approx((100.0 + 200.0 + 300.0) / 3)
