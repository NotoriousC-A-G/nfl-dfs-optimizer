import pandas as pd
import pytest

from nfl_dfs.build.evidence.opportunity import (
    MIN_VACATED_TARGET_SHARE, PlayerShares, redistribute, trailing_shares,
)


def _pbp():
    """Team KC, 5 regular-season games. Weeks 2-5 are the window; week 1 must be ignored."""
    rows = []

    def play(week, kind, pid, name):
        r = dict(season=2026, week=week, season_type="REG", posteam="KC", rush_attempt=0, pass_attempt=0, qb_kneel=0, qb_scramble=0,
                 rusher_player_id=None, rusher_player_name=None, receiver_player_id=None, receiver_player_name=None)
        if kind == "run":
            r.update(rush_attempt=1, rusher_player_id=pid, rusher_player_name=name)
        else:
            r.update(pass_attempt=1, receiver_player_id=pid, receiver_player_name=name)
        rows.append(r)

    for w in (1, 2, 3, 4, 5):
        for _ in range(8):
            play(w, "run", "rb1", "Back One")
        for _ in range(2):
            play(w, "run", "rb2", "Back Two")
        for _ in range(6):
            play(w, "pass", "wr1", "Wide One")  # 30% of 20 targets
        for _ in range(4):
            play(w, "pass", "wr2", "Wide Two")
        for _ in range(4):
            play(w, "pass", "te1", "Tight One")
        for _ in range(6):
            play(w, "pass", "wr3" if w != 3 else "wr1", "Wide Three")
    # a kneel and a scramble must not count as carries
    rows.append(dict(rows[0], rush_attempt=1, qb_kneel=1, rusher_player_id="qb", rusher_player_name="QB"))
    rows.append(dict(rows[0], rush_attempt=1, qb_scramble=1, rusher_player_id="qb", rusher_player_name="QB"))
    return pd.DataFrame(rows)


def test_trailing_shares_use_the_last_four_games_and_only_real_carries_and_targets():
    t = trailing_shares(_pbp(), season=2026, through_week=5)["KC"]
    assert "qb" not in t  # kneels and scrambles are not carries
    assert t["rb1"].carry_share == pytest.approx(0.8) and t["rb2"].carry_share == pytest.approx(0.2)
    assert t["wr2"].target_share == pytest.approx(4 / 20) and t["te1"].target_share == pytest.approx(0.2)
    assert t["rb1"].touch_share == pytest.approx(8 / 30)
    assert t["rb1"].games == 4  # week 1 is outside the window


def test_stability_is_the_lowest_single_game_touch_share():
    t = trailing_shares(_pbp(), season=2026, through_week=5)["KC"]
    # wr3 (named "Wide Three") only exists in 3 of the 4 window games (week 3 the same 6 targets go to wr1): a missed game is a 0
    assert t["wr3"].touch_share_min == 0.0 and t["wr3"].games == 3
    assert t["rb1"].touch_share_min == pytest.approx(8 / 30)
    # through_week limits what is seen
    assert trailing_shares(_pbp(), season=2026, through_week=2)["KC"]["rb1"].games == 2


def _shares():
    return trailing_shares(_pbp(), season=2026, through_week=5)["KC"]


def test_a_missing_receivers_targets_are_shared_pro_rata_and_the_total_is_conserved():
    s = _shares()
    listed = {"wr2": "WR", "te1": "TE", "rb1": "RB", "rb2": "RB", "wr1": "WR", "wr3": "WR"}
    res = redistribute("KC", s, {"wr1": ("Wide One", "OUT")}, listed)
    assert [v.name for v in res.vacated] == ["Wide One"]
    gains = {pid: e.target_gain for pid, e in res.expected.items()}
    assert sum(gains.values()) <= s["wr1"].target_share + 1e-9  # never more than the vacated share
    assert gains["wr2"] > 0 and gains["te1"] > gains["wr2"] * 0.9  # bigger existing roles inherit more
    assert "wr1" not in res.expected  # the out player gets nothing
    assert "Wide One (OUT" in res.expected["wr2"].note and "draft" in res.expected["wr2"].note


def test_a_player_with_no_role_yet_can_still_inherit_one_because_of_the_prior():
    s = _shares()
    listed = {"wr2": "WR", "newguy": "WR", "te1": "TE"}
    res = redistribute("KC", s, {"wr1": ("Wide One", "OUT")}, listed)
    assert res.expected["newguy"].target_gain > 0
    assert res.expected["newguy"].target_share == pytest.approx(res.expected["newguy"].target_gain)  # had zero before


def test_carries_go_to_running_backs_only_and_players_outside_the_pool_absorb_their_share():
    s = _shares()
    listed = {"rb2": "RB", "wr2": "WR"}
    res = redistribute("KC", s, {"rb1": ("Back One", "Q")}, listed)
    assert res.expected["rb2"].carry_gain > 0
    assert "wr2" not in res.expected  # a WR does not inherit carries, and nobody out had targets
    assert res.expected["rb2"].carry_gain == pytest.approx(0.8)  # the only listed back and the only other carrier takes all of it
    s2 = {**s, "rb3": PlayerShares("rb3", "Back Three", 0.0, 0.0, 0.0, 0.0, 0)}  # an unlisted back with no carries absorbs nothing
    assert redistribute("KC", s2, {"rb1": ("Back One", "Q")}, listed).expected["rb2"].carry_gain == pytest.approx(0.8)


def test_unlisted_teammates_dilute_what_listed_players_get():
    s = _shares()
    only = redistribute("KC", s, {"wr1": ("Wide One", "OUT")}, {"wr2": "WR"})
    with_te_unlisted = redistribute("KC", s, {"wr1": ("Wide One", "OUT")}, {"wr2": "WR", "te1": "TE"})
    # in `only`, te1 and the other receivers are unlisted and absorb their pro rata share; listing te1 gives him his portion
    assert "te1" not in only.expected and with_te_unlisted.expected["te1"].target_gain > 0
    assert sum(e.target_gain for e in only.expected.values()) < _shares()["wr1"].target_share


def test_small_roles_are_not_treated_as_vacated_and_nothing_vanishes_silently():
    s = {"x": PlayerShares("x", "Minor", 0.0, MIN_VACATED_TARGET_SHARE / 2, 0.01, 0.0, 1)}
    res = redistribute("KC", s, {"x": ("Minor", "OUT")}, {"y": "WR"})
    assert res.vacated == () and res.expected == {}
    res2 = redistribute("KC", _shares(), {"wr1": ("Wide One", "OUT")}, {"qb": "QB"})
    assert res2.expected == {} and any("no eligible listed candidate" in u for u in res2.unassigned)


def test_a_team_with_nobody_out_gets_no_redistribution():
    assert redistribute("KC", _shares(), {}, {"wr2": "WR"}).vacated == ()


def test_shares_are_measured_over_the_games_the_player_played():
    # wr3 caught 6 of the team's 20 targets in weeks 2, 4 and 5 and did not play in week 3 (the window is weeks 2-5)
    t = trailing_shares(_pbp(), season=2026, through_week=5, played_weeks={"wr3": {2, 4, 5}})["KC"]
    assert t["wr3"].target_share == pytest.approx(6 / 20)  # 18 of 60 over the 3 games he played, not 18 of 80
    assert t["wr3"].games_played == 3 and t["wr3"].window_games == 4
    assert t["wr3"].touch_share_min == pytest.approx(6 / 30)  # the week he sat is no longer scored as a 0
    # without snap data the old whole-window behaviour stands
    old = trailing_shares(_pbp(), season=2026, through_week=5)["KC"]["wr3"]
    assert old.target_share == pytest.approx(18 / 80) and old.touch_share_min == 0.0 and old.games_played == 4


def test_a_player_back_from_a_miss_is_not_diluted_by_the_games_he_missed():
    # one game played in the window with 4 of the team's 20 targets in it: 20%, not the diluted 4 of 68
    pbp = _pbp()
    pbp = pbp[~((pbp["week"].isin([2, 3, 4])) & (pbp["receiver_player_id"] == "wr2"))]
    t = trailing_shares(pbp, season=2026, through_week=5, played_weeks={"wr2": {5}})["KC"]
    assert t["wr2"].games_played == 1 and t["wr2"].target_share == pytest.approx(4 / 20)
    # the old whole-window share counted every team target in the games he missed
    assert trailing_shares(pbp, season=2026, through_week=5)["KC"]["wr2"].target_share == pytest.approx(4 / 68)


def test_a_player_missing_from_the_snap_data_counts_only_the_weeks_he_touched_the_ball():
    t = trailing_shares(_pbp(), season=2026, through_week=5, played_weeks={"rb1": {2, 3, 4, 5}})["KC"]
    assert t["wr3"].games_played == 3 and t["wr3"].target_share == pytest.approx(6 * 3 / 60)


def _hall_allen_pbp():
    """NYJ: Hall 60% of carries in weeks 1-3, then misses 4-7; Allen carries the load while he is away."""
    rows = []
    def carry(week, pid, n):
        rows.extend({"season": 2026, "week": week, "season_type": "REG", "posteam": "NYJ", "rush_attempt": 1, "pass_attempt": 0,
                     "rusher_player_id": pid, "rusher_player_name": pid, "receiver_player_id": None, "receiver_player_name": None} for _ in range(n))
    for w in (1, 2, 3):
        carry(w, "HALL", 6); carry(w, "ALLEN", 2); carry(w, "OTHER", 2)
    for w in (4, 5, 6, 7):
        carry(w, "ALLEN", 8); carry(w, "OTHER", 2)
    return pd.DataFrame(rows)


def test_a_player_back_from_a_long_absence_gets_his_pre_absence_role_and_teammates_shrink():
    from nfl_dfs.build.evidence.opportunity import redistribute
    played = {"HALL": {1, 2, 3}, "ALLEN": {1, 2, 3, 4, 5, 6, 7}, "OTHER": {1, 2, 3, 4, 5, 6, 7}}
    t = trailing_shares(_hall_allen_pbp(), season=2026, through_week=7, played_weeks=played)["NYJ"]
    assert t["HALL"].carry_share == 0.0 and t["HALL"].games_played == 0  # nothing in the window...
    assert t["HALL"].prior_carry_share == pytest.approx(0.6) and t["HALL"].prior_games == 3 and t["HALL"].prior_last_week == 3
    assert t["ALLEN"].carry_share == pytest.approx(0.8)
    opp = redistribute("NYJ", t, {}, {"HALL": "RB", "ALLEN": "RB"})
    assert opp.expected["HALL"].carry_share == pytest.approx(0.6)
    assert opp.expected["ALLEN"].carry_share == pytest.approx(0.8 * 0.4)  # what Hall's return leaves him
    assert opp.expected["ALLEN"].carry_gain < 0 and "HALL" in opp.expected["ALLEN"].note.upper()


def test_a_returner_who_is_out_hands_nothing_back():
    from nfl_dfs.build.evidence.opportunity import redistribute
    played = {"HALL": {1, 2, 3}, "ALLEN": {1, 2, 3, 4, 5, 6, 7}, "OTHER": {1, 2, 3, 4, 5, 6, 7}}
    t = trailing_shares(_hall_allen_pbp(), season=2026, through_week=7, played_weeks=played)["NYJ"]
    opp = redistribute("NYJ", t, {"HALL": ("HALL", "excluded")}, {"ALLEN": "RB"})
    assert "HALL" not in opp.expected and not opp.vacated  # his window share is 0: nothing to vacate, Allen keeps the role he has
