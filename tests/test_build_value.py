import numpy as np
import pandas as pd
import pytest

from nfl_dfs.build.thesis.contracts import Branch, GameThesis, PlayerBranchOutcome
from nfl_dfs.build.value.calibration import CalibrationTable, CellStat, fit_cells, holdout_check, load_resultsdb_player_rows
from nfl_dfs.build.value.tail_value import C_DEFAULT, expected_multipliers, tail_values


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
