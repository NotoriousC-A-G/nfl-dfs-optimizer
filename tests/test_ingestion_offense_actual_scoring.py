import pandas as pd

from nfl_dfs.ingestion.offense_actual_scoring import dk_points_row, settled_offensive_points_by_player


def _weekly_row(**overrides) -> dict:
    base = dict(
        season=2026,
        week=2,
        season_type="REG",
        player_display_name="Test Player",
        recent_team="GB",
        passing_yards=0,
        passing_tds=0,
        interceptions=0,
        rushing_yards=0,
        rushing_tds=0,
        receptions=0,
        receiving_yards=0,
        receiving_tds=0,
        rushing_fumbles_lost=0,
        receiving_fumbles_lost=0,
        sack_fumbles_lost=0,
        passing_2pt_conversions=0,
        rushing_2pt_conversions=0,
        receiving_2pt_conversions=0,
    )
    base.update(overrides)
    return base


def test_dk_points_row_basic_receiving_line():
    row = pd.Series(_weekly_row(receptions=8, receiving_yards=95, receiving_tds=1))
    # 8*1 + 95*0.1 + 1*6 = 8 + 9.5 + 6 = 23.5
    assert dk_points_row(row) == 23.5


def test_dk_points_row_100_yard_rushing_bonus():
    row = pd.Series(_weekly_row(rushing_yards=100, rushing_tds=1))
    # 100*0.1 + 1*6 + 3.0 (bonus) = 10 + 6 + 3 = 19
    assert dk_points_row(row) == 19.0


def test_dk_points_row_no_bonus_under_threshold():
    row = pd.Series(_weekly_row(rushing_yards=99))
    assert dk_points_row(row) == 9.9  # no 100-yard bonus


def test_dk_points_row_passing_300_bonus_and_interception_penalty():
    row = pd.Series(_weekly_row(passing_yards=310, passing_tds=2, interceptions=1))
    # 310*0.04 + 2*4 - 1 + 3.0 = 12.4 + 8 - 1 + 3 = 22.4
    assert round(dk_points_row(row), 2) == 22.4


def test_dk_points_row_fumbles_lost_penalty():
    row = pd.Series(_weekly_row(rushing_fumbles_lost=1, receiving_fumbles_lost=1, sack_fumbles_lost=0))
    assert dk_points_row(row) == -2.0


def test_settled_offensive_points_by_player_filters_season_week_and_type():
    weekly = pd.DataFrame(
        [
            _weekly_row(player_display_name="Justin Jefferson", recent_team="MIN", receptions=5, receiving_yards=60),
            _weekly_row(week=1, player_display_name="Justin Jefferson", recent_team="MIN", receptions=99),  # wrong week
            _weekly_row(season=2025, player_display_name="Justin Jefferson", recent_team="MIN", receptions=99),  # wrong season
            _weekly_row(season_type="POST", player_display_name="Justin Jefferson", recent_team="MIN", receptions=99),  # wrong type
        ]
    )
    points = settled_offensive_points_by_player(weekly, 2026, 2)
    assert list(points.keys()) == [("justin jefferson", "MIN")]
    assert points[("justin jefferson", "MIN")] == 5 + 6.0  # 5 receptions + 60*0.1


def test_settled_offensive_points_by_player_normalizes_crosswalk_team_codes():
    weekly = pd.DataFrame([_weekly_row(player_display_name="Jayden Reed", recent_team="GBP")])
    points = settled_offensive_points_by_player(weekly, 2026, 2)
    assert ("jayden reed", "GB") in points
