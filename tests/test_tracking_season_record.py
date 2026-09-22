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


def _contest_row(label, rank, entries, winnings=0.0, week=2, paid=None, contest_name="Some Contest", fpts=100.0):
    return ContestResult(2026, week, label, contest_name, entries, paid or entries // 10, 1000.0, rank, fpts, winnings)


def test_empty_when_nothing_logged(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    assert compute_season_records(2026, agent_path=agent_path, contest_path=contest_path) == []


def test_operator_lineups_tracked_individually_not_pooled(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results(
        [
            _agent_row("operator", 2, 80.0, strategy_name="L1"),
            _agent_row("operator", 2, 130.0, strategy_name="L2"),
        ],
        path=agent_path,
    )
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    ids = {r.agent_id for r in records}
    assert ids == {"L1", "L2"}
    assert "operator" not in ids
    assert all(r.is_operator_lineup for r in records)


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
    assert records[0].is_operator_lineup is False
    assert records[0].basis == "estimated"


def test_unscored_rows_excluded_from_avg_delta_but_still_count_a_week(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results([_agent_row("chalk_anchor", 1, None, proj=100.0)], path=agent_path)
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    assert records[0].weeks_tracked == 1
    assert records[0].avg_delta is None
    assert records[0].wins == 0


def test_operator_win_is_real_cash_not_internal_ranking(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results([_agent_row("operator", 2, 80.0, strategy_name="L1")], path=agent_path)
    # L1 scored LOW (80) but still real-cashed this contest -- win must reflect the real cash,
    # not "did it have the best internal score."
    save_contest_results([_contest_row("L1", rank=5, entries=1000, winnings=25.0)], path=contest_path)

    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    l1 = records[0]
    assert l1.basis == "real"
    assert l1.contest_entries == 1
    assert l1.contest_cashes == 1
    assert l1.wins == 1


def test_operator_no_win_when_no_real_cash(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results([_agent_row("operator", 2, 200.0, strategy_name="L1")], path=agent_path)
    # L1 scored HIGH (200, best internal score) but still real-missed the cash line.
    save_contest_results([_contest_row("L1", rank=999, entries=1000, winnings=0.0)], path=contest_path)

    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    l1 = records[0]
    assert l1.contest_cashes == 0
    assert l1.wins == 0


def test_agent_win_uses_estimated_cash_against_real_contests(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results(
        [
            _agent_row("operator", 2, 80.0, strategy_name="L1"),
            _agent_row("operator", 2, 120.0, strategy_name="L2"),
            _agent_row("chalk_anchor", 2, 119.0),  # just below L2 -> should estimate-cash too
        ],
        path=agent_path,
    )
    save_contest_results(
        [
            _contest_row("L1", rank=8000, entries=10000, paid=2000, winnings=0.0, fpts=80.0),
            _contest_row("L2", rank=500, entries=10000, paid=2000, winnings=25.0, fpts=120.0),
        ],
        path=contest_path,
    )
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    chalk = next(r for r in records if r.agent_id == "chalk_anchor")
    assert chalk.basis == "estimated"
    assert chalk.contest_entries == 1  # one multi-entry contest this week
    assert chalk.contest_cashes == 1
    assert chalk.wins == 1


def test_agent_with_no_multi_entry_contest_gets_zero_entries(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results(
        [
            _agent_row("operator", 2, 80.0, strategy_name="L1"),
            _agent_row("chalk_anchor", 2, 90.0),
        ],
        path=agent_path,
    )
    # Only ONE real entry this week -- not enough anchors to estimate against.
    save_contest_results([_contest_row("L1", rank=500, entries=1000)], path=contest_path)

    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    chalk = next(r for r in records if r.agent_id == "chalk_anchor")
    assert chalk.contest_entries == 0
    assert chalk.wins == 0


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


def test_records_sorted_by_wins_then_cash_rate_then_avg_delta(tmp_path):
    agent_path = tmp_path / "agent_results.csv"
    contest_path = tmp_path / "contest_results.csv"
    save_agent_results(
        [
            _agent_row("operator", 2, 200.0, strategy_name="L1"),  # real cash
            _agent_row("operator", 2, 50.0, strategy_name="L2"),  # no cash, low score
        ],
        path=agent_path,
    )
    save_contest_results(
        [
            _contest_row("L1", rank=1, entries=1000, winnings=100.0),
            _contest_row("L2", rank=999, entries=1000, winnings=0.0),
        ],
        path=contest_path,
    )
    records = compute_season_records(2026, agent_path=agent_path, contest_path=contest_path)
    assert records[0].agent_id == "L1"
