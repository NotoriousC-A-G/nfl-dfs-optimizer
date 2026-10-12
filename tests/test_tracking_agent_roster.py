from nfl_dfs.tracking.agent_roster import ACTIVE_AGENT_IDS, agent_display_name, is_active_agent
from nfl_dfs.tracking.redesign_agent_log import build_redesign_agent_rows
from nfl_dfs.tracking.season_record import REAL_AGENT_IDS


def test_live_roster_is_chalk_anchor_plus_the_five_pool_agents():
    assert ACTIVE_AGENT_IDS == {"chalk_anchor", "shootout_stack", "contrarian_game", "chalk_pivot", "short_field", "volume_anchor"}


def test_retired_agents_are_inactive_but_the_operator_is_always_live():
    for retired in ("game_script_architect", "matchup_purist", "arbitrageur", "explosion_shootout", "volatility_engine"):
        assert not is_active_agent(retired)
        assert retired in REAL_AGENT_IDS  # their records are retained
    assert is_active_agent("operator")
    assert is_active_agent("chalk_pivot")


def test_display_names_cover_both_generations():
    assert agent_display_name("chalk_pivot") == "Chalk Pivot"
    assert agent_display_name("arbitrageur") == "Arbitrageur"


def test_build_redesign_agent_rows_logs_first_lineup_with_tokens():
    pool = [
        {"identity": {"canonical_id": "00-1", "display_name": "A Player"}, "team": "GB", "position": "WR"},
        {"identity": {"canonical_id": "DST_GB", "display_name": "Packers"}, "team": "GB", "position": "DST"},
    ]
    artifact = {
        "agents": {
            "chalk_pivot": {"lineups": [{"player_ids": ["00-1", "DST_GB"], "projected": 20.123, "salary": 49900}]},
            "short_field": {"lineups": []},  # unbuilt -> no row
            "not_an_agent": {"lineups": [{"player_ids": ["00-1"], "projected": 1.0, "salary": 1}]},
        }
    }
    (row,) = build_redesign_agent_rows(artifact, pool, 2026, 5)
    assert (row.agent_id, row.strategy_name, row.proj_total, row.salary) == ("chalk_pivot", "Chalk Pivot", 20.12, 49900)
    assert row.players == ("A Player (WR-GB)", "Packers (DST-GB)")
    assert row.total_dk_score is None


def test_build_chalk_anchor_row_reads_the_snapshot_agent_lineup():
    from nfl_dfs.tracking.redesign_agent_log import build_chalk_anchor_row

    snapshot = {
        "agent_lineups": [
            {"agent": {"agent_id": "arbitrageur"}, "lineup": {"total_projected_points": 1.0, "total_salary": 1, "players": []}},
            {
                "agent": {"agent_id": "chalk_anchor"},
                "lineup": {
                    "total_projected_points": 161.666,
                    "total_salary": 50000,
                    "players": [{"display_name": "A Player", "position": "WR", "team": "GB"}],
                },
            },
        ]
    }
    row = build_chalk_anchor_row(snapshot, 2026, 5)
    assert (row.agent_id, row.proj_total, row.salary, row.players) == ("chalk_anchor", 161.67, 50000, ("A Player (WR-GB)",))
    assert build_chalk_anchor_row({"agent_lineups": []}, 2026, 5) is None
