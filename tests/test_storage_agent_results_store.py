from nfl_dfs.storage.agent_results_store import (
    AgentResultRow,
    read_agent_results,
    save_agent_results,
)


def _row(agent_id: str, strategy_name: str, week: int = 2, **overrides) -> AgentResultRow:
    defaults = dict(
        season=2026,
        week=week,
        agent_id=agent_id,
        strategy_name=strategy_name,
        proj_total=150.0,
        salary=49900,
        players=("QB Guy (QB-GB)", "RB Guy (RB-GB)"),
    )
    defaults.update(overrides)
    return AgentResultRow(**defaults)


def test_save_agent_results_returns_none_for_empty_rows(tmp_path):
    path = tmp_path / "agent_results.csv"
    assert save_agent_results([], path=path) is None
    assert not path.exists()


def test_save_then_read_round_trips(tmp_path):
    path = tmp_path / "agent_results.csv"
    row = _row("chalk_anchor", "Chalk Anchor")
    save_agent_results([row], path=path)

    results = read_agent_results(path=path)
    assert len(results) == 1
    assert results[0] == row


def test_operator_and_agent_rows_share_one_table(tmp_path):
    path = tmp_path / "agent_results.csv"
    agent_row = _row("chalk_anchor", "Chalk Anchor")
    operator_row = _row("operator", "L1", proj_total=145.6, salary=49900)
    save_agent_results([agent_row, operator_row], path=path)

    results = read_agent_results(path=path)
    assert {r.agent_id for r in results} == {"chalk_anchor", "operator"}
    assert [r.strategy_name for r in results if r.agent_id == "operator"] == ["L1"]


def test_multiple_operator_entries_distinguished_by_strategy_name(tmp_path):
    path = tmp_path / "agent_results.csv"
    rows = [_row("operator", label) for label in ("L1", "L2", "L3")]
    save_agent_results(rows, path=path)

    results = read_agent_results(path=path)
    assert sorted(r.strategy_name for r in results) == ["L1", "L2", "L3"]
    assert all(r.agent_id == "operator" for r in results)


def test_pending_score_fields_round_trip_as_none(tmp_path):
    path = tmp_path / "agent_results.csv"
    row = _row("operator", "L1", total_dk_score=None, boom=None, lineup_rank=None)
    save_agent_results([row], path=path)

    result = read_agent_results(path=path)[0]
    assert result.total_dk_score is None
    assert result.boom is None
    assert result.lineup_rank is None


def test_settled_score_fields_round_trip_when_present(tmp_path):
    path = tmp_path / "agent_results.csv"
    row = _row("operator", "L1", total_dk_score=142.3, boom=True, lineup_rank=2)
    save_agent_results([row], path=path)

    result = read_agent_results(path=path)[0]
    assert result.total_dk_score == 142.3
    assert result.boom is True
    assert result.lineup_rank == 2


def test_save_is_append_only_across_calls(tmp_path):
    path = tmp_path / "agent_results.csv"
    save_agent_results([_row("chalk_anchor", "Chalk Anchor")], path=path)
    save_agent_results([_row("arbitrageur", "Arbitrageur")], path=path)

    results = read_agent_results(path=path)
    assert {r.agent_id for r in results} == {"chalk_anchor", "arbitrageur"}


def test_save_is_idempotent_on_season_week_agent_id_strategy_name(tmp_path):
    path = tmp_path / "agent_results.csv"
    save_agent_results([_row("chalk_anchor", "Chalk Anchor", proj_total=100.0)], path=path)
    # Re-saving the same (season, week, agent_id, strategy_name) key must not duplicate the row,
    # even with different field values -- matches MLB's own append-only/idempotent posture.
    save_agent_results([_row("chalk_anchor", "Chalk Anchor", proj_total=999.0)], path=path)

    results = read_agent_results(path=path)
    assert len(results) == 1
    assert results[0].proj_total == 100.0


def test_read_agent_results_filters_by_season_and_week(tmp_path):
    path = tmp_path / "agent_results.csv"
    save_agent_results(
        [
            _row("chalk_anchor", "Chalk Anchor", week=2),
            _row("chalk_anchor", "Chalk Anchor", week=1),
        ],
        path=path,
    )

    week_2_only = read_agent_results(week=2, path=path)
    assert [r.week for r in week_2_only] == [2]


def test_read_agent_results_empty_when_file_does_not_exist(tmp_path):
    assert read_agent_results(path=tmp_path / "does_not_exist.csv") == []


def test_players_tuple_round_trips_in_roster_order(tmp_path):
    path = tmp_path / "agent_results.csv"
    row = _row("chalk_anchor", "Chalk Anchor", players=("Trevor Lawrence (QB-JAX)", "Bijan Robinson (RB-ATL)"))
    save_agent_results([row], path=path)

    result = read_agent_results(path=path)[0]
    assert result.players == ("Trevor Lawrence (QB-JAX)", "Bijan Robinson (RB-ATL)")
