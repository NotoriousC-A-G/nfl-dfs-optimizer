import pandas as pd

from nfl_dfs.storage.agent_results_store import AgentResultRow, read_agent_results, save_agent_results
from nfl_dfs.tracking.agent_results_collector import score_and_backfill_agent_results


def _weekly_row(name: str, team: str, receptions=0, receiving_yards=0.0, week=2, season=2026) -> dict:
    return dict(
        season=season,
        week=week,
        season_type="REG",
        player_display_name=name,
        recent_team=team,
        passing_yards=0,
        passing_tds=0,
        interceptions=0,
        rushing_yards=0,
        rushing_tds=0,
        receptions=receptions,
        receiving_yards=receiving_yards,
        receiving_tds=0,
        rushing_fumbles_lost=0,
        receiving_fumbles_lost=0,
        sack_fumbles_lost=0,
        passing_2pt_conversions=0,
        rushing_2pt_conversions=0,
        receiving_2pt_conversions=0,
    )


_play_id_counter = iter(range(1, 100_000))


def _pbp_row(defteam: str, week: int = 2, season: int = 2026, game_id: str | None = None, **overrides) -> dict:
    # Each defteam gets its own synthetic opponent so distinct games never collide on
    # (defteam, week) in dst_actual_scoring's points-allowed index (real pbp never has two teams
    # named "ZZZ" playing separate games the same week -- this fixture must avoid that too).
    opponent = f"OPP_{defteam}"
    base = dict(
        season=season,
        season_type="REG",
        week=week,
        game_id=game_id or f"{season}_{week:02d}_{defteam}_{opponent}",
        play_id=next(_play_id_counter),
        defteam=defteam,
        posteam=opponent,
        play_type="pass",
        sack=0,
        interception=0,
        fumble=0,
        fumble_recovery_1_team=None,
        special_teams_play=0,
        touchdown=0,
        return_touchdown=0,
        return_team=None,
        td_team=None,
        safety=0,
        punt_blocked=0,
        field_goal_result=None,
        defensive_two_point_conv=0,
        total_home_score=0,
        total_away_score=0,
        home_team=defteam,
        away_team=opponent,
    )
    base.update(overrides)
    return base


def _row(agent_id: str, strategy_name: str, players: tuple, week: int = 2) -> AgentResultRow:
    return AgentResultRow(
        season=2026,
        week=week,
        agent_id=agent_id,
        strategy_name=strategy_name,
        proj_total=100.0,
        salary=49000,
        players=players,
    )


def test_score_and_backfill_computes_total_for_fully_resolved_row(tmp_path):
    path = tmp_path / "agent_results.csv"
    save_agent_results(
        [_row("chalk_anchor", "Chalk Anchor", ("Justin Jefferson (WR-MIN)",))],
        path=path,
    )
    weekly = pd.DataFrame([_weekly_row("Justin Jefferson", "MIN", receptions=5, receiving_yards=60)])
    pbp = pd.DataFrame([_pbp_row("SEA")])  # no DST rows referenced by this lineup

    result = score_and_backfill_agent_results(2026, 2, weekly=weekly, pbp=pbp, path=path)

    assert len(result.unresolved) == 0
    scored = result.scored[0]
    assert scored.total_dk_score == 5 + 6.0  # 5 receptions + 60 yards * 0.1
    assert scored.lineup_rank == 1


def test_score_and_backfill_leaves_unresolved_row_unscored(tmp_path):
    path = tmp_path / "agent_results.csv"
    save_agent_results(
        [_row("chalk_anchor", "Chalk Anchor", ("Nobody Real (WR-ZZZ)",))],
        path=path,
    )
    weekly = pd.DataFrame([_weekly_row("Justin Jefferson", "MIN")])
    pbp = pd.DataFrame([_pbp_row("SEA")])

    result = score_and_backfill_agent_results(2026, 2, weekly=weekly, pbp=pbp, path=path)

    assert len(result.unresolved) == 1
    agent_id, strategy_name, missing = result.unresolved[0]
    assert agent_id == "chalk_anchor"
    assert missing == ("Nobody Real (WR-ZZZ)",)
    assert result.scored[0].total_dk_score is None


def test_score_and_backfill_ranks_multiple_rows_by_total(tmp_path):
    path = tmp_path / "agent_results.csv"
    save_agent_results(
        [
            _row("chalk_anchor", "Chalk Anchor", ("Player A (WR-MIN)",)),
            _row("operator", "L1", ("Player B (WR-MIN)",)),
        ],
        path=path,
    )
    weekly = pd.DataFrame(
        [
            _weekly_row("Player A", "MIN", receptions=2, receiving_yards=20),  # 2 + 2.0 = 4.0
            _weekly_row("Player B", "MIN", receptions=10, receiving_yards=150),  # 10 + 15.0 + 3.0(bonus) = 28.0
        ]
    )
    pbp = pd.DataFrame([_pbp_row("SEA")])

    result = score_and_backfill_agent_results(2026, 2, weekly=weekly, pbp=pbp, path=path)

    by_agent = {r.agent_id: r for r in result.scored}
    assert by_agent["operator"].total_dk_score == 28.0
    assert by_agent["operator"].lineup_rank == 1
    assert by_agent["chalk_anchor"].lineup_rank == 2


def test_score_and_backfill_leaves_other_weeks_untouched(tmp_path):
    path = tmp_path / "agent_results.csv"
    save_agent_results(
        [
            _row("chalk_anchor", "Chalk Anchor", ("Player A (WR-MIN)",), week=1),
            _row("chalk_anchor", "Chalk Anchor", ("Player A (WR-MIN)",), week=2),
        ],
        path=path,
    )
    weekly = pd.DataFrame([_weekly_row("Player A", "MIN", receptions=1, receiving_yards=10)])
    pbp = pd.DataFrame([_pbp_row("SEA")])

    score_and_backfill_agent_results(2026, 2, weekly=weekly, pbp=pbp, path=path)

    week_1_row = read_agent_results(week=1, path=path)[0]
    assert week_1_row.total_dk_score is None


def test_score_and_backfill_resolves_dst_via_team_week_points(tmp_path):
    path = tmp_path / "agent_results.csv"
    save_agent_results(
        [_row("chalk_anchor", "Chalk Anchor", ("Jaguars (DST-JAX)",))],
        path=path,
    )
    weekly = pd.DataFrame([_weekly_row("Irrelevant Player", "MIN")])
    pbp = pd.DataFrame(
        [_pbp_row("JAX", sack=1), _pbp_row("JAX", interception=1), _pbp_row("SEA")]
    )

    result = score_and_backfill_agent_results(2026, 2, weekly=weekly, pbp=pbp, path=path)

    assert len(result.unresolved) == 0
    # 1 sack (1.0) + 1 interception (2.0) = 3.0, plus points-allowed band (0 points allowed -> +10.0
    # in this fixture's minimal scoring context handled entirely by dst_actual_scoring.py itself)
    assert result.scored[0].total_dk_score is not None
