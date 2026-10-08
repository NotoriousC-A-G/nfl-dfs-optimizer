from types import SimpleNamespace

import pandas as pd
import pytest

from nfl_dfs.build.evidence.opportunity import team_volume
from nfl_dfs.build.value.opportunity_points import (
    MIN_OPPORTUNITIES, PASS_THROUGH, PointsPerOpportunity, fit_rates, opportunity_adjustments,
)


def _weekly(rb_carries=1000, rb_yards=4500, rb_tds=30, wr_targets=1000, te_targets=600, rb_targets=600):
    rows = [
        dict(position="RB", season_type="REG", carries=rb_carries, rushing_yards=rb_yards, rushing_tds=rb_tds, targets=rb_targets, receptions=int(rb_targets * 0.75),
             receiving_yards=rb_targets * 6.0, receiving_tds=10),
        dict(position="WR", season_type="REG", carries=0, rushing_yards=0, rushing_tds=0, targets=wr_targets, receptions=int(wr_targets * 0.65),
             receiving_yards=wr_targets * 8.5, receiving_tds=60),
        dict(position="TE", season_type="REG", carries=0, rushing_yards=0, rushing_tds=0, targets=te_targets, receptions=int(te_targets * 0.7),
             receiving_yards=te_targets * 8.0, receiving_tds=40),
        dict(position="RB", season_type="POST", carries=500, rushing_yards=0, rushing_tds=0, targets=0, receptions=0, receiving_yards=0, receiving_tds=0),  # ignored
    ]
    return pd.DataFrame(rows)


def test_rates_are_dk_points_per_opportunity_from_regular_season_box_scores():
    r = fit_rates(_weekly(), seasons=(2024,))
    assert r.per_carry_rb == pytest.approx((0.1 * 4500 + 6 * 30) / 1000)  # 0.63
    assert r.per_target["WR"] == pytest.approx((650 + 0.1 * 8500 + 6 * 60) / 1000)  # 1.86
    assert r.n_carries_rb == 1000 and r.seasons == (2024,)  # the playoff row is excluded
    assert PointsPerOpportunity.from_json(r.to_json()) == r


def test_a_position_with_too_few_opportunities_refuses_to_fit():
    with pytest.raises(ValueError, match="carries"):
        fit_rates(_weekly(rb_carries=MIN_OPPORTUNITIES - 1))
    with pytest.raises(ValueError, match="TE targets"):
        fit_rates(_weekly(te_targets=10))


RATES = PointsPerOpportunity(0.6, {"RB": 1.5, "WR": 1.7, "TE": 1.75}, 10_000, {"RB": 5000, "WR": 9000, "TE": 4000})


def _p(cid, pos, proj, c_l4=None, c_exp=None, t_l4=None, t_exp=None, team="TEN"):
    return SimpleNamespace(canonical_id=cid, name=cid, team=team, position=pos, projection=proj,
                           carry_share_l4=c_l4, carry_share_expected=c_exp, target_share_l4=t_l4, target_share_expected=t_exp)


def test_a_back_who_inherits_carries_gets_extra_points_priced_by_the_teams_volume():
    adj = opportunity_adjustments([_p("spears", "RB", 5.1, 0.188, 0.561)], {"TEN": (24.0, 30.0)}, RATES)["spears"]
    extra_carries = (0.561 - 0.188) * 24.0
    assert adj.extra_carries == pytest.approx(extra_carries)
    assert adj.extra_points == pytest.approx(extra_carries * 0.6 * PASS_THROUGH)
    assert adj.adjusted_projection == pytest.approx(5.1 + adj.extra_points) and adj.adjusted_projection > 5.1 + 3.0


def test_a_receiver_who_inherits_targets_is_priced_per_target_by_position():
    wr = opportunity_adjustments([_p("w", "WR", 10.0, t_l4=0.10, t_exp=0.20)], {"TEN": (24.0, 30.0)}, RATES)["w"]
    te = opportunity_adjustments([_p("t", "TE", 10.0, t_l4=0.10, t_exp=0.20)], {"TEN": (24.0, 30.0)}, RATES)["t"]
    assert wr.extra_points == pytest.approx(0.10 * 30.0 * 1.7 * PASS_THROUGH) and te.extra_points == pytest.approx(0.10 * 30.0 * 1.75 * PASS_THROUGH)
    assert wr.extra_carries == 0.0  # a receiver's carry share is not priced


def test_nobody_else_is_touched_no_gain_no_volume_no_projection_wrong_position_and_declines_are_ignored():
    players = [
        _p("same", "RB", 8.0, 0.30, 0.30), _p("down", "RB", 8.0, 0.50, 0.40), _p("noexp", "WR", 8.0), _p("qb", "QB", 20.0, c_l4=0.1, c_exp=0.3),
        _p("noproj", "RB", None, 0.1, 0.3), _p("nowhere", "RB", 8.0, 0.1, 0.3, team="ZZZ"),
    ]
    assert opportunity_adjustments(players, {"TEN": (24.0, 30.0)}, RATES) == {}


def test_a_player_with_no_prior_share_is_priced_from_zero():
    adj = opportunity_adjustments([_p("new", "WR", 4.0, t_l4=None, t_exp=0.12)], {"TEN": (24.0, 30.0)}, RATES)["new"]
    assert adj.target_gain == pytest.approx(0.12) and adj.extra_targets == pytest.approx(0.12 * 30.0)


def test_team_volume_is_carries_and_targets_per_game_over_the_last_four_games():
    from tests.test_build_opportunity import _pbp
    v = team_volume(_pbp(), season=2026, through_week=5)["KC"]
    assert v == pytest.approx((10.0, 20.0))  # 10 real carries (kneel/scramble excluded) and 20 targets in each of the 4 window games
