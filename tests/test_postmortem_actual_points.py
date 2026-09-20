import pandas as pd

from nfl_dfs.tracking.postmortem.actual_points import actual_points_by_canonical_id


def _weekly_row(name, team, receptions=0, receiving_yards=0.0, week=2, season=2026) -> dict:
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


_opp_counter = iter(range(1, 100000))


def _pbp_row(defteam, week=2, season=2026, **overrides) -> dict:
    opponent = f"OPP_{defteam}"
    base = dict(
        season=season,
        season_type="REG",
        week=week,
        game_id=f"{season}_{week:02d}_{defteam}_{opponent}",
        play_id=next(_opp_counter),
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


def _pool_row(canonical_id, name, team, position="WR") -> dict:
    return {"identity": {"canonical_id": canonical_id, "display_name": name}, "team": team, "position": position}


def test_resolves_offensive_player_by_name_and_team():
    pool = [_pool_row("p1", "Justin Jefferson", "MIN")]
    weekly = pd.DataFrame([_weekly_row("Justin Jefferson", "MIN", receptions=5, receiving_yards=60)])
    pbp = pd.DataFrame([_pbp_row("SEA")])

    points = actual_points_by_canonical_id(pool, season=2026, week=2, weekly=weekly, pbp=pbp)

    assert points["p1"] == 5 + 6.0


def test_resolves_dst_by_team():
    pool = [_pool_row("dst1", "Jaguars", "JAX", position="DST")]
    weekly = pd.DataFrame([_weekly_row("Irrelevant", "MIN")])
    pbp = pd.DataFrame([_pbp_row("JAX", sack=1), _pbp_row("SEA")])

    points = actual_points_by_canonical_id(pool, season=2026, week=2, weekly=weekly, pbp=pbp)

    assert "dst1" in points


def test_unmatched_player_is_absent_not_zero():
    pool = [_pool_row("p1", "Nobody Real", "ZZZ")]
    weekly = pd.DataFrame([_weekly_row("Justin Jefferson", "MIN")])
    pbp = pd.DataFrame([_pbp_row("SEA")])

    points = actual_points_by_canonical_id(pool, season=2026, week=2, weekly=weekly, pbp=pbp)

    assert "p1" not in points


def test_row_missing_canonical_id_is_skipped_not_crashed():
    pool = [{"identity": {"display_name": "No ID Guy"}, "team": "MIN", "position": "WR"}]
    weekly = pd.DataFrame([_weekly_row("No ID Guy", "MIN")])
    pbp = pd.DataFrame([_pbp_row("SEA")])

    points = actual_points_by_canonical_id(pool, season=2026, week=2, weekly=weekly, pbp=pbp)
    assert points == {}
