import pandas as pd
import pytest

from nfl_dfs.ingestion.dst_actual_scoring import (
    aggregate_team_week_dst_points,
    offensive_fumble_recovery_td_bonus,
)


def _row(**kwargs) -> dict:
    row = {
        "season": 2024,
        "week": 1,
        "season_type": "REG",
        "game_id": "2024_01_AWAY_HOME",
        "play_id": 1,
        "home_team": "HOME",
        "away_team": "AWAY",
        "total_home_score": 0,
        "total_away_score": 0,
        "posteam": "AWAY",
        "defteam": "HOME",
        "sack": 0,
        "interception": 0,
        "fumble": 0,
        "fumble_recovery_1_team": None,
        "fumble_recovery_1_player_id": None,
        "touchdown": 0,
        "td_team": None,
        "special_teams_play": 0,
        "return_touchdown": 0,
        "return_team": None,
        "safety": 0,
        "punt_blocked": 0,
        "field_goal_result": None,
        "defensive_two_point_conv": 0,
    }
    row.update(kwargs)
    return row


def _final_row(play_id: int, *, home_score: int, away_score: int) -> dict:
    """A trailing, event-free play carrying the game's real settled final score -- must have the
    highest `play_id` in the game for `aggregate_team_week_dst_points`'s "last play by play_id"
    logic to pick it up as the final score."""
    return _row(play_id=play_id, total_home_score=home_score, total_away_score=away_score)


def _dst_for(results, team: str):
    return next(r for r in results if r.team == team)


def test_sacks_counted_as_one_event_per_play_not_per_half_sack_credit():
    rows = [
        _row(play_id=1, sack=1),
        _row(play_id=2, sack=1),
        _final_row(3, home_score=0, away_score=0),
    ]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    assert home.sacks == 2
    assert home.dk_points == pytest.approx(2 * 1.0 + 10.0)  # 2 sacks + 0-points-allowed bonus


def test_interception_return_td_is_additive_with_base_interception():
    rows = [
        _row(play_id=1, interception=1, touchdown=1, td_team="HOME"),
        _final_row(2, home_score=0, away_score=0),
    ]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    assert home.interceptions == 1
    assert home.defensive_touchdowns == 1
    assert home.dk_points == pytest.approx(2.0 + 6.0 + 10.0)  # INT + return TD + 0-allowed bonus


def test_defensive_fumble_recovery_td_is_additive_with_base_recovery():
    rows = [
        _row(play_id=1, fumble=1, fumble_recovery_1_team="HOME", touchdown=1, td_team="HOME"),
        _final_row(2, home_score=0, away_score=0),
    ]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    assert home.fumble_recoveries == 1
    assert home.defensive_touchdowns == 1
    assert home.dk_points == pytest.approx(2.0 + 6.0 + 10.0)


def test_offense_recovering_its_own_fumble_is_not_credited_to_the_defense():
    rows = [
        _row(play_id=1, fumble=1, fumble_recovery_1_team="AWAY"),  # posteam recovers its own fumble
        _final_row(2, home_score=0, away_score=0),
    ]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    assert home.fumble_recoveries == 0
    assert home.dk_points == pytest.approx(10.0)  # only the 0-points-allowed bonus


def test_special_teams_return_td_credited_to_the_returning_team():
    rows = [
        # A punt: AWAY punts, HOME (defteam for this play) returns it for a TD.
        _row(play_id=1, special_teams_play=1, return_touchdown=1, return_team="HOME"),
        _final_row(2, home_score=0, away_score=0),
    ]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    assert home.defensive_touchdowns == 1
    assert home.dk_points == pytest.approx(6.0 + 10.0)


def test_special_teams_fumble_recovered_by_return_team_is_not_double_counted():
    # The returner fumbles but his own team recovers and scores -- this is ONE real punt-return
    # TD, not a punt-return TD AND a separate defensive-fumble-recovery TD.
    rows = [
        _row(
            play_id=1,
            special_teams_play=1,
            return_touchdown=1,
            return_team="HOME",
            fumble=1,
            fumble_recovery_1_team="HOME",
            touchdown=1,
            td_team="HOME",
        ),
        _final_row(2, home_score=0, away_score=0),
    ]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    assert home.fumble_recoveries == 0  # excluded here -- counted once, via defensive_touchdowns
    assert home.defensive_touchdowns == 1
    assert home.dk_points == pytest.approx(6.0 + 10.0)  # not 6 + 2 + 6 + 10


def test_blocked_kick_plus_return_td_is_additive():
    rows = [
        _row(play_id=1, punt_blocked=1, special_teams_play=1, return_touchdown=1, return_team="HOME"),
        _final_row(2, home_score=0, away_score=0),
    ]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    assert home.blocked_kicks == 1
    assert home.defensive_touchdowns == 1
    assert home.dk_points == pytest.approx(2.0 + 6.0 + 10.0)  # block + return TD + 0-allowed bonus


def test_blocked_field_goal_counts_as_a_blocked_kick():
    rows = [
        _row(play_id=1, field_goal_result="blocked"),
        _final_row(2, home_score=0, away_score=0),
    ]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    assert home.blocked_kicks == 1


def test_safety_and_defensive_two_point_return():
    rows = [
        _row(play_id=1, safety=1),
        _row(play_id=2, defensive_two_point_conv=1),
        _final_row(3, home_score=0, away_score=0),
    ]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    assert home.safeties == 1
    assert home.defensive_two_point_returns == 1
    assert home.dk_points == pytest.approx(2.0 + 2.0 + 10.0)


@pytest.mark.parametrize(
    "away_score,expected_bonus",
    [(0, 10.0), (6, 7.0), (13, 4.0), (20, 1.0), (27, 0.0), (34, -1.0), (35, -4.0), (48, -4.0)],
)
def test_points_allowed_bands(away_score, expected_bonus):
    # HOME's defense "allows" whatever AWAY (the offense it faced) scored.
    rows = [_final_row(1, home_score=0, away_score=away_score)]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    assert home.points_allowed == away_score
    assert home.dk_points == pytest.approx(expected_bonus)


def test_final_score_is_the_last_play_by_play_id_not_the_first():
    # Regression test for a real bug caught during live verification: taking the FIRST row per
    # game_id reads the 0-0 opening-kickoff state, not the real settled final score.
    rows = [
        _row(play_id=5, total_home_score=0, total_away_score=0),
        _row(play_id=1, total_home_score=0, total_away_score=0),  # out of order on purpose
        _final_row(10, home_score=17, away_score=24),
    ]
    results = aggregate_team_week_dst_points(pd.DataFrame(rows))
    home = _dst_for(results, "HOME")
    away = _dst_for(results, "AWAY")
    assert home.points_allowed == 24
    assert away.points_allowed == 17


def test_offensive_fumble_recovery_td_bonus_detects_own_team_recovery_for_a_td():
    rows = [
        _row(
            play_id=1,
            week=3,
            fumble=1,
            fumble_recovery_1_team="AWAY",
            fumble_recovery_1_player_id="player-123",
            touchdown=1,
        ),
    ]
    result = offensive_fumble_recovery_td_bonus(pd.DataFrame(rows))
    assert result == {("player-123", 3): 6.0}


def test_offensive_fumble_recovery_td_bonus_excludes_defensive_recoveries():
    rows = [
        _row(
            play_id=1,
            fumble=1,
            fumble_recovery_1_team="HOME",  # the DEFENSE recovers -- not an offensive event
            fumble_recovery_1_player_id="player-456",
            touchdown=1,
        ),
    ]
    assert offensive_fumble_recovery_td_bonus(pd.DataFrame(rows)) == {}


def test_offensive_fumble_recovery_td_bonus_excludes_non_touchdown_recoveries():
    rows = [
        _row(play_id=1, fumble=1, fumble_recovery_1_team="AWAY", fumble_recovery_1_player_id="player-789", touchdown=0),
    ]
    assert offensive_fumble_recovery_td_bonus(pd.DataFrame(rows)) == {}
