import numpy as np
import pandas as pd
import pytest

from nfl_dfs.build.thesis.contracts import Branch, GameThesis, PlayerBranchOutcome
from nfl_dfs.build.value.calibration import CalibrationTable, CellStat, fit_cells, holdout_check, load_resultsdb_player_rows
from nfl_dfs.build.value.tail_value import C_DEFAULT, conditional_multipliers, expected_multipliers, tail_values


def _df(seed=0, seasons=(2022, 2023, 2024, 2025), n_per=500):
    rng = np.random.default_rng(seed)
    rows = []
    for s in seasons:
        for pos, base in (("WR", 10.0), ("RB", 11.0), ("QB", 18.0), ("TE", 8.0), ("DST", 6.5)):
            proj = rng.uniform(base * 0.5, base * 1.5, n_per)
            actual = proj * rng.gamma(shape=4.0, scale=0.25, size=n_per)  # mean ratio ~1, right-skewed
            rows += [(s, pos, p, a) for p, a in zip(proj, actual)]
    return pd.DataFrame(rows, columns=["season", "position", "projected_points", "actual_points"])


def test_fit_cells_gives_ratios_near_one_for_the_mean_and_a_fatter_right_tail_for_q90():
    t = fit_cells(_df())
    for pos in ("WR", "RB", "QB", "TE", "DST"):
        assert pos in t.cells and len(t.cells[pos]) >= 3
        for c in t.cells[pos]:
            assert 0.85 < c.mean_ratio < 1.15
            assert c.q90_ratio > c.mean_ratio * 1.3 and c.n >= 150


def test_lookup_picks_the_right_tier_and_is_open_ended():
    t = CalibrationTable({"WR": (CellStat(-np.inf, 8.0, 100, 1.0, 2.0), CellStat(8.0, 12.0, 100, 0.9, 1.8), CellStat(12.0, np.inf, 100, 1.1, 1.6))})
    assert t.lookup("WR", 1.0).q90_ratio == 2.0 and t.lookup("WR", 8.0).q90_ratio == 1.8
    assert t.lookup("WR", 99.0).q90_ratio == 1.6 and t.lookup("QB", 10.0) is None


def test_table_round_trips_through_json():
    t = fit_cells(_df())
    t2 = CalibrationTable.from_json(t.to_json())
    assert t2.cells["WR"][0].q90_ratio == pytest.approx(t.cells["WR"][0].q90_ratio) and t2.fitted_seasons == t.fitted_seasons


def test_holdout_check_is_out_of_sample_and_roughly_calibrated():
    h = holdout_check(_df(), test_season=2025)
    assert h["train_seasons"] == [2022, 2023, 2024] and h["test_season"] == 2025
    assert 0.9 < h["calibrated_bias"] < 1.1
    assert 0.06 < h["share_above_q90"] < 0.14  # ~10% of out-of-sample actuals exceed the predicted q90
    assert set(h["by_position"]) == {"QB", "RB", "WR", "TE", "DST"}


def test_tail_value_formula_scales_the_excess_by_one_third_and_never_below_the_mean():
    t = CalibrationTable({"WR": (CellStat(-np.inf, np.inf, 1000, 1.0, 2.1),)})
    tv = tail_values([("w", "WR", 10.0)], t)["w"]
    assert tv.mu == pytest.approx(10.0) and tv.q90 == pytest.approx(21.0)
    assert tv.tv == pytest.approx(10.0 + C_DEFAULT * 11.0)
    flat = CalibrationTable({"WR": (CellStat(-np.inf, np.inf, 1000, 1.2, 1.0),)})  # q90 below the mean can't happen -> clamp
    f = tail_values([("w", "WR", 10.0)], flat)["w"]
    assert f.q90 >= f.mu and f.tv == pytest.approx(f.mu)


def test_uncalibrated_players_are_omitted_not_invented():
    t = CalibrationTable({"WR": (CellStat(-np.inf, np.inf, 1000, 1.0, 2.0),)})
    out = tail_values([("w", "WR", 10.0), ("k", "K", 5.0)], t)
    assert set(out) == {"w"}


def test_multipliers_shift_mean_and_q90_and_flow_into_tail_value():
    t = CalibrationTable({"WR": (CellStat(-np.inf, np.inf, 1000, 1.0, 2.0),)})
    base = tail_values([("w", "WR", 10.0)], t)["w"]
    up = tail_values([("w", "WR", 10.0)], t, multipliers={"w": (1.15, 1.3)})["w"]
    assert up.mu == pytest.approx(11.5) and up.q90 == pytest.approx(26.0) and up.tv > base.tv


def _thesis(branches):
    return GameThesis(1, "A@B", "sha", "p", "m", "h", (), (), (), tuple(branches), "res", (), ())


def test_expected_multipliers_weight_by_branch_probability_with_unnamed_branches_at_one():
    b1 = Branch("b1", (("q1", True),), False, 0.30, "d", player_outcomes=(PlayerBranchOutcome("x", 1.3, 1.3),))
    b2 = Branch("b2", (("q1", False),), False, 0.50, "d", player_outcomes=(PlayerBranchOutcome("x", 0.9, 0.8),))
    res = Branch("res", (), True, 0.20, "d")  # names nobody -> contributes 1.0
    m = expected_multipliers([_thesis([b1, b2, res])])
    assert m["x"][0] == pytest.approx(0.30 * 1.3 + 0.50 * 0.9 + 0.20 * 1.0)
    assert m["x"][1] == pytest.approx(0.30 * 1.3 + 0.50 * 0.8 + 0.20 * 1.0)
    assert "y" not in m


def test_real_resultsdb_loader_maps_defense_label_and_tolerates_a_missing_root(tmp_path):
    assert len(load_resultsdb_player_rows(root=tmp_path / "nope")) == 0


def _floor_table():
    # two WRs with the same mean: a steady one (tight tails) and a volatile one (low floor, high ceiling)
    return CalibrationTable({
        "WR": (CellStat(-np.inf, 10.0, 1000, 1.0, 1.6, 0.8), CellStat(10.0, np.inf, 1000, 1.0, 2.4, 0.2)),
    })


def test_floor_lean_zero_is_exactly_the_neutral_value():
    t = _floor_table()
    for cid, proj in (("steady", 8.0), ("boom", 12.0)):
        assert tail_values([(cid, "WR", proj)], t, floor_lean=0.0)[cid].tv == tail_values([(cid, "WR", proj)], t)[cid].tv


def test_a_floor_lean_prefers_the_steady_player_and_a_ceiling_lean_the_volatile_one():
    t = _floor_table()
    steady, boom = ("steady", "WR", 10.0 - 1e-6), ("boom", "WR", 10.0)
    floor = tail_values([steady, boom], t, floor_lean=0.6)
    ceiling = tail_values([steady, boom], t, floor_lean=-0.6)
    assert floor["steady"].tv > floor["boom"].tv
    assert ceiling["boom"].tv > ceiling["steady"].tv
    assert floor["boom"].q25 < floor["steady"].q25 <= floor["steady"].mu


def test_a_ceiling_lean_does_not_reward_a_bad_floor():
    t = _floor_table()
    # a ceiling lean only raises the weight on the upside; the left tail is never credited
    neutral = tail_values([("boom", "WR", 12.0)], t)["boom"]
    ceiling = tail_values([("boom", "WR", 12.0)], t, floor_lean=-0.5)["boom"]
    assert ceiling.tv == pytest.approx(neutral.mu + C_DEFAULT * 1.5 * (neutral.q90 - neutral.mu))


def test_a_floor_lean_needs_a_table_with_a_left_tail_and_refuses_to_invent_one():
    old = CalibrationTable({"WR": (CellStat(-np.inf, np.inf, 1000, 1.0, 2.0),)})
    with pytest.raises(ValueError, match="q25_ratio"):
        tail_values([("w", "WR", 10.0)], old, floor_lean=0.3)
    assert tail_values([("w", "WR", 10.0)], old, floor_lean=-0.3)["w"].tv > tail_values([("w", "WR", 10.0)], old)["w"].tv  # ceiling lean needs no q25
    with pytest.raises(ValueError, match="floor_lean"):
        tail_values([("w", "WR", 10.0)], old, floor_lean=1.5)


def test_fit_cells_measures_a_left_tail_below_the_mean_and_it_survives_the_json_round_trip():
    t = fit_cells(_df())
    for cells in t.cells.values():
        for c in cells:
            assert 0 < c.q25_ratio < c.mean_ratio < c.q90_ratio
    assert CalibrationTable.from_json(t.to_json()).cells["WR"][0].q25_ratio == pytest.approx(t.cells["WR"][0].q25_ratio)
    legacy = '{"fitted_seasons": [], "cells": {"WR": [{"lo": -Infinity, "hi": Infinity, "n": 9, "mean_ratio": 1.0, "q90_ratio": 2.0}]}}'
    assert CalibrationTable.from_json(legacy).cells["WR"][0].q25_ratio is None  # an old saved table still loads


def test_the_pool_agents_carry_a_floor_dial_in_range_with_anchors_leaning_floor_and_stack_agents_ceiling():
    from nfl_dfs.build.agents import POOL_AGENT_BY_ID, POOL_AGENTS
    assert all(-1.0 <= a.floor_lean <= 1.0 for a in POOL_AGENTS)
    assert POOL_AGENT_BY_ID["volume_anchor"].floor_lean > 0
    assert POOL_AGENT_BY_ID["shootout_stack"].floor_lean < 0 and POOL_AGENT_BY_ID["contrarian_game"].floor_lean < 0


def _game(gid="A@B", x="x", z="z"):
    b1 = Branch("b1", (("q1", True),), False, 0.30, "d", player_outcomes=(PlayerBranchOutcome(x, 1.3, 1.3), PlayerBranchOutcome(z, 1.15, 1.15)))
    b2 = Branch("b2", (("q1", False),), False, 0.50, "d", player_outcomes=(PlayerBranchOutcome(x, 0.9, 0.8),))
    res = Branch("res", (), True, 0.20, "d")
    return GameThesis(1, gid, "sha", "p", "m", "h", (), (), (), (b1, b2, res), "res", (), ())


def test_a_backed_branch_is_priced_in_full_with_no_pull_from_branches_the_agent_is_not_betting_on():
    m = conditional_multipliers([_game()], backs=["A@B:b1"])
    assert m["x"] == (pytest.approx(1.3), pytest.approx(1.3))  # not the 0.3*1.3 + ... average
    assert m["z"] == (pytest.approx(1.15), pytest.approx(1.15))
    assert expected_multipliers([_game()])["x"][0] < 1.3  # the averaged value is what used to wash the stand out


def test_several_backed_branches_are_weighted_by_their_own_probabilities():
    m = conditional_multipliers([_game()], backs=["A@B:b1", "A@B:b2"])
    assert m["x"][0] == pytest.approx((0.30 * 1.3 + 0.50 * 0.9) / 0.80)
    assert m["z"][0] == pytest.approx((0.30 * 1.15 + 0.50 * 1.0) / 0.80)  # b2 does not name z -> 1.0


def test_avoiding_a_branch_drops_and_renormalizes_the_rest_including_the_residual():
    m = conditional_multipliers([_game()], avoids=["A@B:b2"])
    assert m["x"][0] == pytest.approx((0.30 * 1.3 + 0.20 * 1.0) / 0.50)


def test_a_game_the_agent_says_nothing_about_gets_the_full_probability_weighted_mix_and_other_games_stay_independent():
    two = [_game("A@B"), _game("C@D", x="x2", z="z2")]
    m = conditional_multipliers(two, backs=["A@B:b1"])
    assert m["x"][0] == pytest.approx(1.3)  # the backed game is priced under its stand
    assert m["x2"][0] == pytest.approx(expected_multipliers([_game("C@D", x="x2", z="z2")])["x2"][0])  # the other keeps the full mix
    assert m["x2"][0] == pytest.approx(1.04) and m["x"][0] > m["x2"][0]


def test_no_stand_at_all_equals_the_expected_multipliers():
    a, b = conditional_multipliers([_game()]), expected_multipliers([_game()])
    assert a.keys() == b.keys() and all(a[k] == pytest.approx(b[k]) for k in a)
