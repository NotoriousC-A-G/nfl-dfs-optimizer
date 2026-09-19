import numpy as np
import pandas as pd

from nfl_dfs.ingestion.nflverse_depth_charts import SKILL_POSITIONS, parse_latest_depth_chart


def _row(dt: str, team: str, player_name: str, gsis_id, pos_abb: str, pos_rank: int) -> dict:
    return {
        "dt": dt,
        "team": team,
        "player_name": player_name,
        "espn_id": 1,
        "gsis_id": gsis_id,
        "pos_grp_id": 1,
        "pos_grp": "Offense",
        "pos_id": 1,
        "pos_name": pos_abb,
        "pos_abb": pos_abb,
        "pos_slot": 1,
        "pos_rank": pos_rank,
    }


def test_default_skill_positions_constant() -> None:
    assert SKILL_POSITIONS == frozenset({"QB", "RB", "WR", "TE"})


def test_returns_only_the_most_recent_snapshot() -> None:
    df = pd.DataFrame(
        [
            _row("2026-09-19T11:56:08Z", "MIN", "Aaron Jones Sr.", "00-0033293", "RB", 1),
            _row("2026-04-08T08:10:30Z", "MIN", "Aaron Jones Sr.", "00-0033293", "RB", 1),
            _row("2026-04-08T08:10:30Z", "MIN", "Jordan Mason", "00-0037525", "RB", 2),
        ]
    )
    entries = parse_latest_depth_chart(df)
    assert len(entries) == 1
    assert entries[0].player_name == "Aaron Jones Sr."
    assert entries[0].snapshot_at == "2026-09-19T11:56:08Z"


def test_real_demotion_case_confirmed_live_2026_09_19() -> None:
    # The exact real case that motivated this module: Jordan Mason correctly demoted to depth-chart
    # rank 4 the same week he was ruled IR, while trailing role-share data alone (usage_share.py)
    # doesn't have any current-week injury awareness at all.
    df = pd.DataFrame(
        [
            _row("2026-09-19T11:56:08Z", "MIN", "Aaron Jones Sr.", "00-0033293", "RB", 1),
            _row("2026-09-19T11:56:08Z", "MIN", "Demond Claiborne", "00-0041104", "RB", 2),
            _row("2026-09-19T11:56:08Z", "MIN", "DeeJay Dallas", "00-0036425", "RB", 3),
            _row("2026-09-19T11:56:08Z", "MIN", "Jordan Mason", "00-0037525", "RB", 4),
        ]
    )
    entries = {e.player_name: e.depth_rank for e in parse_latest_depth_chart(df)}
    assert entries["Aaron Jones Sr."] == 1
    assert entries["Jordan Mason"] == 4


def test_filters_to_skill_positions_by_default() -> None:
    df = pd.DataFrame(
        [
            _row("2026-09-19T11:56:08Z", "MIN", "Aaron Jones Sr.", "00-0033293", "RB", 1),
            _row("2026-09-19T11:56:08Z", "MIN", "Some Lineman", "00-0099999", "LT", 1),
        ]
    )
    entries = parse_latest_depth_chart(df)
    assert [e.player_name for e in entries] == ["Aaron Jones Sr."]


def test_custom_positions_is_honored() -> None:
    df = pd.DataFrame(
        [
            _row("2026-09-19T11:56:08Z", "MIN", "Aaron Jones Sr.", "00-0033293", "RB", 1),
            _row("2026-09-19T11:56:08Z", "MIN", "Some Lineman", "00-0099999", "LT", 1),
        ]
    )
    entries = parse_latest_depth_chart(df, positions=frozenset({"LT"}))
    assert [e.player_name for e in entries] == ["Some Lineman"]


def test_skips_rows_with_no_real_gsis_id() -> None:
    df = pd.DataFrame(
        [
            _row("2026-09-19T11:56:08Z", "MIN", "Aaron Jones Sr.", "00-0033293", "RB", 1),
            _row("2026-09-19T11:56:08Z", "MIN", np.nan, np.nan, "TE", 1),
        ]
    )
    entries = parse_latest_depth_chart(df)
    assert len(entries) == 1
    assert entries[0].player_name == "Aaron Jones Sr."


def test_normalizes_la_to_lar_the_one_real_nflverse_exception() -> None:
    df = pd.DataFrame([_row("2026-09-19T11:56:08Z", "LA", "Some RB", "00-0099998", "RB", 1)])
    entries = parse_latest_depth_chart(df)
    assert entries[0].team == "LAR"


def test_empty_dataframe_returns_empty_list() -> None:
    assert parse_latest_depth_chart(pd.DataFrame()) == []
