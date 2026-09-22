from nfl_dfs.storage.agent_results_store import AgentResultRow, save_agent_results
from nfl_dfs.storage.contest_results_store import ContestResult, save_contest_results
from nfl_dfs.tracking.season_record import compute_season_records


def _agent_row(agent_id, week, total, proj=100.0, strategy_name=None, **overrides):
    defaults = dict(
        season=2026,
        week=week,
        agent_id=agent_id,
        strategy_name=strategy_name or agent_id,
        proj_total=proj,
        salary=49900,
        players=("A (QB-GB)",),
        total_dk_score=total,
        lineup_rank=None,
        boom=None,
    )
    defaults.update(overrides)
    return AgentResultRow(**defaults)


def _contest_row(label, rank, entries, winnings=0.0, week=2):
    return ContestResult(2026, week, label, "Some Contest", entries, entries // 10, 1000.0, rank, 100.0, winnings)


def test_empty_when_nothing_logged(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    assert compute_season_records(2026, agent_path=agent_path, contest_path=contest_path) == []


def test_single_week_records_are_zero_or_one_win(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results(
        [
            _agent_row("chalk_anchor", 2, 90.0),
            _agent_row("arbitrageur", 2, 110.0),
        ],
        path=agent_path,
    )
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    by_id = {r.agent_id: r for r in records}
    assert by_id["arbitrageur"].wins == 1
    assert by_id["arbitrageur"].weeks_tracked == 1
    assert by_id["chalk_anchor"].wins == 0
    assert by_id["chalk_anchor"].weeks_tracked == 1


def test_avg_delta_computed_correctly(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results(
        [
            _agent_row("chalk_anchor", 1, 90.0, proj=100.0),  # delta -10
            _agent_row("chalk_anchor", 2, 130.0, proj=100.0),  # delta +30
        ],
        path=agent_path,
    )
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    assert records[0].avg_delta == 10.0
    assert records[0].weeks_tracked == 2


def test_unscored_rows_excluded_from_avg_delta_but_still_count_a_week(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results([_agent_row("chalk_anchor", 1, None, proj=100.0)], path=agent_path)
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    assert records[0].weeks_tracked == 1
    assert records[0].avg_delta is None
    assert records[0].wins == 0


def test_top_three_credited_correctly(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results(
        [
            _agent_row("a", 1, 100.0),
            _agent_row("b", 1, 90.0),
            _agent_row("c", 1, 80.0),
            _agent_row("d", 1, 70.0),
        ],
        path=agent_path,
    )
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    by_id = {r.agent_id: r for r in records}
    assert by_id["a"].top_three == 1
    assert by_id["c"].top_three == 1
    assert by_id["d"].top_three == 0


def test_operator_contest_entries_aggregated_across_strategy_names(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results(
        [
            _agent_row("operator", 2, 80.0, strategy_name="L1"),
            _agent_row("operator", 2, 120.0, strategy_name="L2"),
        ],
        path=agent_path,
    )
    save_contest_results(
        [
            _contest_row("L1", rank=500, entries=1000, winnings=0.0),
            _contest_row("L1", rank=600, entries=1000, winnings=0.0),
            _contest_row("L2", rank=50, entries=1000, winnings=25.0),
        ],
        path=contest_path,
    )
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    operator = next(r for r in records if r.agent_id == "operator")
    assert operator.contest_entries == 3
    assert operator.contest_cashes == 1


def test_agents_never_have_contest_entries(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results([_agent_row("chalk_anchor", 2, 90.0)], path=agent_path)
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    assert records[0].contest_entries == 0
    assert records[0].contest_cashes == 0


def test_best_and_worst_week_identified(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results(
        [
            _agent_row("chalk_anchor", 1, 80.0),
            _agent_row("chalk_anchor", 2, 130.0),
            _agent_row("chalk_anchor", 3, 100.0),
        ],
        path=agent_path,
    )
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    assert records[0].best_week == (2, 130.0)
    assert records[0].worst_week == (1, 80.0)


def test_records_sorted_by_wins_then_top_three_then_avg_delta(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results(
        [
            _agent_row("winner", 1, 100.0),
            _agent_row("second", 1, 90.0),
            _agent_row("last", 1, 10.0),
        ],
        path=agent_path,
    )
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    assert [r.agent_id for r in records] == ["winner", "second", "last"]
