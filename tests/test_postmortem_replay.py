import pandas as pd

from nfl_dfs.storage.agent_results_store import AgentResultRow, save_agent_results
from nfl_dfs.storage.slate_snapshot_store import save_slate_snapshot
from nfl_dfs.tracking.postmortem.replay import run_postmortem


def _weekly_row(name, team, receptions=0, receiving_yards=0.0, week=2, season=2026) -> dict:
    return dict(
        season=season, week=week, season_type="REG", player_display_name=name, recent_team=team,
        passing_yards=0, passing_tds=0, interceptions=0, rushing_yards=0, rushing_tds=0,
        receptions=receptions, receiving_yards=receiving_yards, receiving_tds=0,
        rushing_fumbles_lost=0, receiving_fumbles_lost=0, sack_fumbles_lost=0,
        passing_2pt_conversions=0, rushing_2pt_conversions=0, receiving_2pt_conversions=0,
    )


_ctr = iter(range(1, 100000))


def _pbp_row(defteam, week=2, season=2026, **overrides) -> dict:
    opponent = f"OPP_{defteam}"
    base = dict(
        season=season, season_type="REG", week=week, game_id=f"{season}_{week:02d}_{defteam}_{opponent}",
        play_id=next(_ctr), defteam=defteam, posteam=opponent, play_type="pass", sack=0, interception=0,
        fumble=0, fumble_recovery_1_team=None, special_teams_play=0, touchdown=0, return_touchdown=0,
        return_team=None, td_team=None, safety=0, punt_blocked=0, field_goal_result=None,
        defensive_two_point_conv=0, total_home_score=0, total_away_score=0, home_team=defteam, away_team=opponent,
    )
    base.update(overrides)
    return base


def _pool_row(cid, name, team, position, salary, projection=10.0) -> dict:
    return {
        "identity": {"canonical_id": cid, "display_name": name},
        "team": team,
        "position": position,
        "salary": salary,
        "projection": projection,
        "ownership": {"projected_ownership": 15.0, "is_chalk": False, "is_leverage": False},
        "game_environment": None,
        "stack_context": None,
        "ceiling_multiplier": None,
    }


def _agent_lineup_entry(agent_id, players) -> dict:
    return {
        "agent": {"agent_id": agent_id, "display_name": agent_id.replace("_", " ").title()},
        "lineup": {
            "players": [
                {"canonical_id": cid, "display_name": name, "team": team, "position": pos, "salary": sal, "blended_projection": proj}
                for (cid, name, team, pos, sal, proj) in players
            ],
            "total_projected_points": sum(p[5] for p in players),
        },
        "achieved_bucket": None,
        "gpp_grade": None,
    }


def test_run_postmortem_returns_none_when_no_snapshot(tmp_path, monkeypatch):
    import nfl_dfs.storage.slate_snapshot_store as store
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)
    result = run_postmortem(2026, 2, weekly=pd.DataFrame(), pbp=pd.DataFrame())
    assert result is None


def test_run_postmortem_scores_agent_lineup_and_flags_process_grade(tmp_path, monkeypatch):
    import nfl_dfs.storage.slate_snapshot_store as snap_store
    import nfl_dfs.storage.agent_results_store as agent_store
    monkeypatch.setattr(snap_store, "DEFAULT_ROOT", tmp_path / "snapshots")
    agent_results_path = tmp_path / "agent_results.csv"
    monkeypatch.setattr(agent_store, "AGENT_RESULTS_PATH", agent_results_path)

    player_pool = [
        _pool_row("qb1", "QB One", "MIN", "QB", 6000, 18.0),
        _pool_row("wr1", "Justin Jefferson", "MIN", "WR", 7800, 20.0),
        _pool_row("dst1", "Jaguars", "JAX", "DST", 2400, 6.0),
    ]
    agent_entry = _agent_lineup_entry(
        "chalk_anchor",
        [
            ("qb1", "QB One", "MIN", "QB", 6000, 18.0),
            ("wr1", "Justin Jefferson", "MIN", "WR", 7800, 20.0),
            ("dst1", "Jaguars", "JAX", "DST", 2400, 6.0),
        ],
    )
    snap_store.save_slate_snapshot(
        2026, 2, player_details=player_pool, agent_results=[agent_entry], stack_profiles=[],
        timestamp="120000", base_dir=tmp_path / "snapshots",
    )
    save_agent_results(
        [
            AgentResultRow(
                season=2026, week=2, agent_id="operator", strategy_name="L1",
                proj_total=44.0, salary=16200,
                players=("QB One (QB-MIN)", "Justin Jefferson (WR-MIN)", "Jaguars (DST-JAX)"),
            )
        ],
        path=agent_results_path,
    )

    weekly = pd.DataFrame(
        [_weekly_row("QB One", "MIN"), _weekly_row("Justin Jefferson", "MIN", receptions=5, receiving_yards=60)]
    )
    pbp = pd.DataFrame([_pbp_row("JAX", sack=1), _pbp_row("SEA")])

    report = run_postmortem(2026, 2, weekly=weekly, pbp=pbp)

    assert report is not None
    assert report.season == 2026
    assert report.week == 2
    labels = {lo.label for lo in report.lineup_outcomes}
    assert "Chalk Anchor" in labels
    assert "L1" in labels

    chalk_anchor = next(lo for lo in report.lineup_outcomes if lo.label == "Chalk Anchor")
    assert chalk_anchor.actual_total is not None  # every player in this fixture resolves

    operator = next(lo for lo in report.lineup_outcomes if lo.label == "L1")
    assert operator.actual_total is not None
    assert operator.agent_id == "operator"


def test_run_postmortem_surfaces_unresolved_players_without_crashing(tmp_path, monkeypatch):
    import nfl_dfs.storage.slate_snapshot_store as snap_store
    import nfl_dfs.storage.agent_results_store as agent_store
    monkeypatch.setattr(snap_store, "DEFAULT_ROOT", tmp_path / "snapshots")
    agent_results_path = tmp_path / "agent_results.csv"
    monkeypatch.setattr(agent_store, "AGENT_RESULTS_PATH", agent_results_path)

    player_pool = [_pool_row("qb1", "Nobody Matched", "ZZZ", "QB", 6000, 18.0)]
    agent_entry = _agent_lineup_entry("chalk_anchor", [("qb1", "Nobody Matched", "ZZZ", "QB", 6000, 18.0)])
    snap_store.save_slate_snapshot(
        2026, 2, player_details=player_pool, agent_results=[agent_entry], stack_profiles=[],
        timestamp="120000", base_dir=tmp_path / "snapshots",
    )

    weekly = pd.DataFrame([_weekly_row("Someone Else", "MIN")])
    pbp = pd.DataFrame([_pbp_row("SEA")])

    report = run_postmortem(2026, 2, weekly=weekly, pbp=pbp)

    chalk_anchor = next(lo for lo in report.lineup_outcomes if lo.agent_id == "chalk_anchor")
    assert chalk_anchor.actual_total is None
    assert chalk_anchor.unresolved_players == ("Nobody Matched",)
