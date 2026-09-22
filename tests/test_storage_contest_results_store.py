from nfl_dfs.storage.contest_results_store import (
    ContestResult,
    read_contest_results,
    save_contest_results,
)


def _row(lineup_label: str, contest_name: str, rank: int, week: int = 2, **overrides) -> ContestResult:
    defaults = dict(
        season=2026,
        week=week,
        lineup_label=lineup_label,
        contest_name=contest_name,
        entries=1000,
        positions_paid=100,
        total_prizes=10000.0,
        rank=rank,
        fpts=100.0,
        winnings=0.0,
    )
    defaults.update(overrides)
    return ContestResult(**defaults)


def test_save_contest_results_returns_none_for_empty_rows(tmp_path):
    path = tmp_path / "contest_results.csv"
    assert save_contest_results([], path=path) is None
    assert not path.exists()


def test_save_then_read_round_trips(tmp_path):
    path = tmp_path / "contest_results.csv"
    row = _row("L1", "$7.5K Huddle", 1640)
    save_contest_results([row], path=path)

    results = read_contest_results(path=path)
    assert len(results) == 1
    assert results[0] == row


def test_multiple_lineups_can_share_one_contest(tmp_path):
    path = tmp_path / "contest_results.csv"
    rows = [
        _row("L1", "$15K Mini-Max", 33559),
        _row("L2", "$15K Mini-Max", 16893),
        _row("L3", "$15K Mini-Max", 22529),
    ]
    save_contest_results(rows, path=path)

    results = read_contest_results(path=path)
    assert len(results) == 3
    assert {r.lineup_label for r in results} == {"L1", "L2", "L3"}
    assert all(r.contest_name == "$15K Mini-Max" for r in results)


def test_one_lineup_can_appear_in_multiple_contests(tmp_path):
    path = tmp_path / "contest_results.csv"
    rows = [
        _row("L1", "$7.5K Huddle", 1640),
        _row("L1", "$40K Pylon", 14520),
        _row("L1", "$175K Fair Catch", 15776),
    ]
    save_contest_results(rows, path=path)

    results = read_contest_results(path=path)
    assert len(results) == 3
    assert {r.contest_name for r in results} == {"$7.5K Huddle", "$40K Pylon", "$175K Fair Catch"}


def test_save_is_idempotent_on_season_week_contest_lineup_rank(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results([_row("L1", "$7.5K Huddle", 1640, fpts=80.22)], path=path)
    save_contest_results([_row("L1", "$7.5K Huddle", 1640, fpts=999.0)], path=path)

    results = read_contest_results(path=path)
    assert len(results) == 1
    assert results[0].fpts == 80.22


def test_cashed_property_true_when_winnings_positive():
    assert _row("L1", "C", 1, winnings=25.0).cashed is True
    assert _row("L1", "C", 1, winnings=0.0).cashed is False


def test_read_contest_results_filters_by_season_and_week(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results([_row("L1", "C", 1, week=2), _row("L1", "C", 1, week=1)], path=path)

    week_2_only = read_contest_results(week=2, path=path)
    assert [r.week for r in week_2_only] == [2]


def test_read_contest_results_empty_when_file_does_not_exist(tmp_path):
    assert read_contest_results(path=tmp_path / "does_not_exist.csv") == []
