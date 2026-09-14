import math

import pandas as pd
import pytest

from nfl_dfs.ceiling.signals import (
    ADOT_MIN_TARGETS,
    CEILING_SHRINKAGE_K,
    COMPONENT_A_SCALE,
    EXPLOSIVE_RUSH_YARDS_THRESHOLD,
    MIN_TRAILING_WEEKS,
    QB_DESIGNED_RUN_MIN_TRAILING_VOLUME,
    QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME,
    CeilingSignal,
    adot_ceiling_signals,
    component_a_multiplier,
    qb_explosive_rush_rate_signals,
    qb_rushing_ceiling_signals,
    red_zone_ceiling_signals,
    role_share_ceiling_signals,
    trailing_red_zone_share_by_week,
)
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR

# For any 2-point population, a ddof=1 sample z-score is always exactly +-1/sqrt(2) regardless of
# the actual values (general property, not specific to these fixtures) -- used below so exact
# boom-rate arithmetic doesn't need to be re-derived by hand every time a population has 2 members.
_TWO_POINT_Z = 1 / math.sqrt(2)


def _pbp_row(**kwargs) -> dict:
    row = {
        "season": 2026,
        "week": 1,
        "season_type": "REG",
        "posteam": "GB",
        "play_type": "run",
        "rusher_player_id": None,
        "rusher_player_name": None,
        "receiver_player_id": None,
        "receiver_player_name": None,
        "passer_player_id": None,
        "passer_player_name": None,
        "air_yards": None,
        "yardline_100": 50,
        "qb_scramble": 0,
        "play_id": 1,
    }
    row.update(kwargs)
    return row


def _rush(week: int, team: str, player_id: str, name: str, n: int, play_id_start: int, *, yardline_100: int = 50) -> list[dict]:
    return [
        _pbp_row(
            week=week, posteam=team, play_type="run", rusher_player_id=player_id, rusher_player_name=name,
            play_id=play_id_start + i, yardline_100=yardline_100,
        )
        for i in range(n)
    ]


def _target(
    week: int, team: str, player_id: str, name: str, n: int, play_id_start: int, *,
    air_yards: float | None = None, yardline_100: int = 50,
) -> list[dict]:
    return [
        _pbp_row(
            week=week, posteam=team, play_type="pass", receiver_player_id=player_id, receiver_player_name=name,
            play_id=play_id_start + i, air_yards=air_yards, yardline_100=yardline_100,
        )
        for i in range(n)
    ]


def _pass_attempts(week: int, team: str, player_id: str, name: str, n: int, play_id_start: int) -> list[dict]:
    return [
        _pbp_row(week=week, posteam=team, play_type="pass", passer_player_id=player_id, passer_player_name=name, play_id=play_id_start + i)
        for i in range(n)
    ]


# --------------------------------------------------------------------------------------------
# role_share_ceiling_signals
# --------------------------------------------------------------------------------------------


def _two_rb_share_swing_rows() -> list[dict]:
    """Player A: steady 0.7/0.7/0.3/0.7 share (one down week, no spike above its own median).
    Player B: steady 0.3/0.3/0.7/0.3 share (one spike week above its own median) -- engineered so
    boom_rate(A) == 0.0 and boom_rate(B) == 0.25, a clean 2-point population for the z-score
    invariant above."""
    rows = []
    play_id = 1
    for week, (a_carries, b_carries) in enumerate([(7, 3), (7, 3), (3, 7), (7, 3)], start=1):
        rows += _rush(week, "GB", "RBA", "Runner A", a_carries, play_id)
        play_id += a_carries
        rows += _rush(week, "GB", "RBB", "Runner B", b_carries, play_id)
        play_id += b_carries
    return rows


def test_role_share_ceiling_signals_computes_boom_rate_and_z_scores():
    rows = _two_rb_share_swing_rows()
    signals = role_share_ceiling_signals(pd.DataFrame(rows), target_week=5, role=ROLE_RB)
    by_id = {s.player_id: s for s in signals}

    assert by_id["RBA"].sample_size == 4
    assert by_id["RBA"].raw_value == pytest.approx(0.0)
    assert by_id["RBB"].sample_size == 4
    assert by_id["RBB"].raw_value == pytest.approx(0.25)

    # 2-point population z-score invariant (see module-level comment).
    assert by_id["RBA"].z_score == pytest.approx(-_TWO_POINT_Z)
    assert by_id["RBB"].z_score == pytest.approx(_TWO_POINT_Z)

    expected_weight = 4 / (4 + CEILING_SHRINKAGE_K)
    assert by_id["RBA"].shrinkage_weight == pytest.approx(expected_weight)
    assert by_id["RBA"].shrunk_z_score == pytest.approx(-_TWO_POINT_Z * expected_weight)
    assert by_id["RBB"].shrunk_z_score == pytest.approx(_TWO_POINT_Z * expected_weight)


def test_role_share_ceiling_signals_gates_below_min_trailing_weeks():
    # Only 2 trailing weeks of volume -- below MIN_TRAILING_WEEKS=3 -- but still returned, never
    # silently dropped.
    rows = _rush(1, "GB", "RBC", "Runner C", 5, 1) + _rush(2, "GB", "RBC", "Runner C", 5, 10)
    signals = role_share_ceiling_signals(pd.DataFrame(rows), target_week=3, role=ROLE_RB)
    assert len(signals) == 1
    assert signals[0].sample_size == 2
    assert signals[0].raw_value is None
    assert signals[0].z_score is None
    assert signals[0].shrinkage_weight is None
    assert signals[0].shrunk_z_score is None


def test_role_share_ceiling_signals_excludes_qb_scramblers_from_rb_role():
    rows = (
        _two_rb_share_swing_rows()
        # A real passer (>= QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS=5 trailing attempts) who also
        # scrambles -- ADR-0020 Decision 1c's exact exclusion scenario.
        + _pass_attempts(1, "GB", "QB1", "Mobile QB", 30, 500)
        + _pass_attempts(2, "GB", "QB1", "Mobile QB", 30, 600)
        + _pass_attempts(3, "GB", "QB1", "Mobile QB", 30, 700)
        + _rush(1, "GB", "QB1", "Mobile QB", 4, 800)
        + _rush(2, "GB", "QB1", "Mobile QB", 4, 810)
        + _rush(3, "GB", "QB1", "Mobile QB", 4, 820)
        + _rush(4, "GB", "QB1", "Mobile QB", 4, 830)
    )
    signals = role_share_ceiling_signals(pd.DataFrame(rows), target_week=5, role=ROLE_RB)
    assert "QB1" not in {s.player_id for s in signals}
    assert {s.player_id for s in signals} == {"RBA", "RBB"}


def test_role_share_ceiling_signals_rejects_unsupported_role():
    with pytest.raises(ValueError, match="RB.*WR|WR.*RB"):
        role_share_ceiling_signals(pd.DataFrame([_pbp_row()]), target_week=2, role="TE")


def test_role_share_ceiling_signals_excludes_future_weeks():
    # A week-5 spike must not count toward a target_week=5 trailing computation (no-look-ahead).
    rows = _two_rb_share_swing_rows() + _rush(5, "GB", "RBA", "Runner A", 100, 900)
    signals = role_share_ceiling_signals(pd.DataFrame(rows), target_week=5, role=ROLE_RB)
    by_id = {s.player_id: s for s in signals}
    assert by_id["RBA"].sample_size == 4  # unchanged by the week-5 row


# --------------------------------------------------------------------------------------------
# red_zone_ceiling_signals
# --------------------------------------------------------------------------------------------


def test_red_zone_ceiling_signals_only_counts_plays_inside_the_red_zone():
    rows = []
    play_id = 1
    for week in range(1, 5):
        rows += _rush(week, "GB", "RBD", "Runner D", 3, play_id, yardline_100=15)  # in RZ
        play_id += 3
        rows += _rush(week, "GB", "RBD", "Runner D", 10, play_id, yardline_100=45)  # outside RZ
        play_id += 10
        rows += _rush(week, "GB", "RBE", "Runner E", 2, play_id, yardline_100=10)  # in RZ, other RB
        play_id += 2

    signals = red_zone_ceiling_signals(pd.DataFrame(rows), target_week=5, role=ROLE_RB)
    by_id = {s.player_id: s for s in signals}
    # Only the yardline_100<=20 rows should count -- 3 RZ carries for D, 2 for E, every week,
    # meaning D holds a steady 0.6 share and E a steady 0.4 share with no boom weeks at all.
    assert by_id["RBD"].sample_size == 4
    assert by_id["RBD"].raw_value == pytest.approx(0.0)
    assert by_id["RBE"].raw_value == pytest.approx(0.0)


def test_red_zone_ceiling_signals_zero_fills_real_shutout_weeks_not_team_no_redzone_weeks():
    # Fantasy Football Expert's required fix (ADR-0028): a player with overall volume but zero
    # red-zone touches on a week their TEAM did reach the red zone is a real 0.0-share
    # observation and must count toward sample_size -- but a week the team never reached the red
    # zone at all must stay excluded, not fabricated as a false shutout.
    rows = (
        # Week 1: RBF gets both of the team's 2 RZ carries (share=1.0), plus some non-RZ volume.
        _rush(1, "GB", "RBF", "Runner F", 2, 1, yardline_100=10)
        + _rush(1, "GB", "RBF", "Runner F", 3, 3, yardline_100=45)
        # Week 2: RBF has overall volume (active) but zero RZ carries -- RBG gets the team's only
        # RZ carries this week, so the team DID reach the red zone. This must zero-fill RBF.
        + _rush(2, "GB", "RBF", "Runner F", 3, 10, yardline_100=45)
        + _rush(2, "GB", "RBG", "Runner G", 2, 20, yardline_100=10)
        # Week 3: RBF gets both of the team's 2 RZ carries again (share=1.0).
        + _rush(3, "GB", "RBF", "Runner F", 2, 30, yardline_100=10)
        + _rush(3, "GB", "RBF", "Runner F", 3, 32, yardline_100=45)
        # Week 4: nobody on the team has a red-zone carry at all -- the team never reached the red
        # zone. RBF still has overall (non-RZ) volume, but this week must NOT be zero-filled.
        + _rush(4, "GB", "RBF", "Runner F", 3, 40, yardline_100=45)
        + _rush(4, "GB", "RBG", "Runner G", 3, 50, yardline_100=45)
    )
    signals = red_zone_ceiling_signals(pd.DataFrame(rows), target_week=5, role=ROLE_RB)
    rbf = next(s for s in signals if s.player_id == "RBF")

    # Without the fix, RBF would only have 2 real weeks (1 and 3) -- below MIN_TRAILING_WEEKS=3,
    # gated to raw_value=None. With the fix, week 2's real shutout is included (3 real weeks),
    # week 4 stays correctly excluded (the team had zero red-zone plays that week).
    assert rbf.sample_size == 3
    assert rbf.raw_value is not None


def test_red_zone_ceiling_signals_zero_median_still_credits_real_spike_weeks():
    # Model Analytics Expert's required fix (ADR-0028 Component B interpretation round): a player
    # whose trailing MEDIAN share is exactly 0 (common post-zero-fill, since red-zone involvement
    # is sparse) must not have every real spike week silently discarded down to a flat 0.0 boom
    # rate -- any real nonzero week counts as a boom relative to a genuine zero baseline.
    rows = []
    play_id = 1
    # Weeks 1 and 3: RBH gets both of the team's 2 RZ carries (a real spike, share=1.0).
    for week in (1, 3):
        rows += _rush(week, "GB", "RBH", "Runner H", 2, play_id, yardline_100=10)
        play_id += 2
        rows += _rush(week, "GB", "RBH", "Runner H", 3, play_id, yardline_100=45)  # overall volume
        play_id += 3
    # Weeks 2, 4, 5: RBH has overall volume but zero RZ touches; RBI takes the team's RZ carries,
    # so the team DID reach the red zone -- these are real, zero-filled shutout weeks for RBH.
    for week in (2, 4, 5):
        rows += _rush(week, "GB", "RBH", "Runner H", 3, play_id, yardline_100=45)
        play_id += 3
        rows += _rush(week, "GB", "RBI", "Runner I", 2, play_id, yardline_100=10)
        play_id += 2

    signals = red_zone_ceiling_signals(pd.DataFrame(rows), target_week=6, role=ROLE_RB)
    rbh = next(s for s in signals if s.player_id == "RBH")

    # Trailing shares: [1.0, 0.0, 1.0, 0.0, 0.0] -- median is exactly 0.0. The two real 1.0 spike
    # weeks must still be credited (2/5 = 0.4), not flattened to 0.0 just because the median sits
    # at zero.
    assert rbh.sample_size == 5
    assert rbh.raw_value == pytest.approx(0.4)


# --------------------------------------------------------------------------------------------
# trailing_red_zone_share_by_week
# --------------------------------------------------------------------------------------------


def test_trailing_red_zone_share_by_week_returns_ordered_real_sequence():
    # Same shutout-vs-no-redzone-week fixture as the boom-rate test above, but here checking the
    # raw per-week sequence itself (ADR-0029's "show the real inputs, not a collapsed score"
    # posture applied to Component B): week 2's real shutout must appear as (2, 0.0), while
    # week 4 (team never reached the red zone) must be absent entirely, not fabricated as 0.0.
    rows = (
        _rush(1, "GB", "RBF", "Runner F", 2, 1, yardline_100=10)
        + _rush(1, "GB", "RBF", "Runner F", 3, 3, yardline_100=45)
        + _rush(2, "GB", "RBF", "Runner F", 3, 10, yardline_100=45)
        + _rush(2, "GB", "RBG", "Runner G", 2, 20, yardline_100=10)
        + _rush(3, "GB", "RBF", "Runner F", 2, 30, yardline_100=10)
        + _rush(3, "GB", "RBF", "Runner F", 3, 32, yardline_100=45)
        + _rush(4, "GB", "RBF", "Runner F", 3, 40, yardline_100=45)
        + _rush(4, "GB", "RBG", "Runner G", 3, 50, yardline_100=45)
    )
    result = trailing_red_zone_share_by_week(pd.DataFrame(rows), target_week=5, role=ROLE_RB)
    assert result["RBF"] == [(1, pytest.approx(1.0)), (2, pytest.approx(0.0)), (3, pytest.approx(1.0))]


def test_trailing_red_zone_share_by_week_excludes_future_weeks_and_trailing_qbs():
    rows = (
        _two_rb_share_swing_rows()
        + _rush(1, "GB", "RBA", "Runner A", 1, 200, yardline_100=10)
        + _rush(5, "GB", "RBA", "Runner A", 1, 900, yardline_100=10)  # future week, excluded
        + _pass_attempts(1, "GB", "QB1", "Mobile QB", 30, 500)
        + _pass_attempts(2, "GB", "QB1", "Mobile QB", 30, 600)
        + _pass_attempts(3, "GB", "QB1", "Mobile QB", 30, 700)
        + _rush(1, "GB", "QB1", "Mobile QB", 1, 800, yardline_100=10)
    )
    result = trailing_red_zone_share_by_week(pd.DataFrame(rows), target_week=5, role=ROLE_RB)
    assert "QB1" not in result
    # The team only reached the red zone (by this fixture's construction) in weeks 1 and 5 --
    # week 5 is the target week itself and must not leak into the trailing sequence. Week 1's RZ
    # plays are split between RBA and the excluded QB1 scrambler, so RBA's share is 0.5.
    assert result["RBA"] == [(1, pytest.approx(0.5))]


def test_trailing_red_zone_share_by_week_rejects_unsupported_role():
    with pytest.raises(ValueError, match="RB.*WR|WR.*RB"):
        trailing_red_zone_share_by_week(pd.DataFrame([_pbp_row()]), target_week=2, role="TE")


# --------------------------------------------------------------------------------------------
# adot_ceiling_signals
# --------------------------------------------------------------------------------------------


def test_adot_ceiling_signals_computes_trailing_mean_and_splits_by_position():
    rows = (
        _target(1, "GB", "WR1", "Deep Threat", ADOT_MIN_TARGETS, 1, air_yards=20.0)
        + _target(1, "GB", "TE1", "Possession TE", ADOT_MIN_TARGETS, 100, air_yards=4.0)
    )
    result = adot_ceiling_signals(pd.DataFrame(rows), target_week=2, position_by_player_id={"WR1": "WR", "TE1": "TE"})

    assert {s.player_id for s in result["WR"]} == {"WR1"}
    assert {s.player_id for s in result["TE"]} == {"TE1"}
    assert result["WR"][0].raw_value == pytest.approx(20.0)
    assert result["TE"][0].raw_value == pytest.approx(4.0)
    assert result["WR"][0].sample_size == ADOT_MIN_TARGETS


def test_adot_ceiling_signals_gates_below_min_targets_without_dropping_the_row():
    rows = _target(1, "GB", "WR2", "Thin Sample", ADOT_MIN_TARGETS - 1, 1, air_yards=15.0)
    result = adot_ceiling_signals(pd.DataFrame(rows), target_week=2, position_by_player_id={"WR2": "WR"})
    assert len(result["WR"]) == 1
    assert result["WR"][0].sample_size == ADOT_MIN_TARGETS - 1
    assert result["WR"][0].raw_value is None


def test_adot_ceiling_signals_ignores_targets_with_no_air_yards_recorded():
    rows = _target(1, "GB", "WR3", "No Air Yards", ADOT_MIN_TARGETS, 1, air_yards=None)
    result = adot_ceiling_signals(pd.DataFrame(rows), target_week=2, position_by_player_id={"WR3": "WR"})
    assert result["WR"] == []


def test_adot_ceiling_signals_excludes_future_weeks():
    rows = _target(1, "GB", "WR4", "Trailing", ADOT_MIN_TARGETS, 1, air_yards=10.0) + _target(
        2, "GB", "WR4", "Trailing", ADOT_MIN_TARGETS, 100, air_yards=90.0
    )
    result = adot_ceiling_signals(pd.DataFrame(rows), target_week=2, position_by_player_id={"WR4": "WR"})
    assert result["WR"][0].raw_value == pytest.approx(10.0)  # week 2 excluded, only week 1 counts


# --------------------------------------------------------------------------------------------
# qb_rushing_ceiling_signals (ADR-0028/0030 Component D -- research/backtest phase only)
# --------------------------------------------------------------------------------------------


def _qb_pass_attempts(week: int, team: str, player_id: str, name: str, n: int, play_id_start: int) -> list[dict]:
    return _pass_attempts(week, team, player_id, name, n, play_id_start)


def _designed_run(week: int, team: str, player_id: str, name: str, play_id: int) -> dict:
    return _pbp_row(
        week=week, posteam=team, play_type="run", rusher_player_id=player_id, rusher_player_name=name,
        play_id=play_id, qb_scramble=0,
    )


def _scramble_run(week: int, team: str, player_id: str, name: str, play_id: int) -> dict:
    return _pbp_row(
        week=week, posteam=team, play_type="run", rusher_player_id=player_id, rusher_player_name=name,
        play_id=play_id, qb_scramble=1,
    )


def test_qb_rushing_ceiling_signals_counts_designed_runs_only_not_scrambles():
    # QB1: designed-run counts by week [3, 1, 3, 1] (sum=8, clears the volume floor exactly) --
    # trailing median=2.0, BOOM_THRESHOLD=1.35 -> threshold=2.7, so weeks 1 and 3 (3 > 2.7) boom.
    # Scrambles are interspersed on the same weeks and must NOT count toward designed_runs at all.
    rows = []
    play_id = 1
    for week, designed_n, scramble_n in [(1, 3, 2), (2, 1, 0), (3, 3, 1), (4, 1, 0)]:
        rows += _qb_pass_attempts(week, "GB", "QB1", "Field General", 10, play_id)
        play_id += 10
        for _ in range(designed_n):
            rows.append(_designed_run(week, "GB", "QB1", "Field General", play_id))
            play_id += 1
        for _ in range(scramble_n):
            rows.append(_scramble_run(week, "GB", "QB1", "Field General", play_id))
            play_id += 1

    signals = qb_rushing_ceiling_signals(pd.DataFrame(rows), target_week=5)
    qb1 = next(s for s in signals if s.player_id == "QB1")
    assert qb1.sample_size == 4
    assert qb1.raw_value == pytest.approx(0.5)  # 2 boom weeks / 4


def test_qb_rushing_ceiling_signals_zero_fills_real_played_weeks_with_no_designed_runs():
    # QB2 played (had real pass attempts) in weeks 1-3, but only had designed runs in weeks 1-2 --
    # week 3's real zero-designed-run week must still count as a real observation (sample_size=3),
    # not be silently dropped down to sample_size=2.
    rows = []
    play_id = 1
    for week in (1, 2, 3):
        rows += _qb_pass_attempts(week, "GB", "QB2", "Zero Week QB", 10, play_id)
        play_id += 10
    rows.append(_designed_run(1, "GB", "QB2", "Zero Week QB", play_id)); play_id += 1
    rows.append(_designed_run(1, "GB", "QB2", "Zero Week QB", play_id)); play_id += 1
    rows.append(_designed_run(2, "GB", "QB2", "Zero Week QB", play_id)); play_id += 1
    rows.append(_designed_run(2, "GB", "QB2", "Zero Week QB", play_id)); play_id += 1
    # Week 3: no designed_run rows at all -- a real, played, zero-designed-run week.

    signals = qb_rushing_ceiling_signals(pd.DataFrame(rows), target_week=4)
    qb2 = next(s for s in signals if s.player_id == "QB2")
    assert qb2.sample_size == 3


def test_qb_rushing_ceiling_signals_gates_below_min_trailing_volume():
    # QB3 has MIN_TRAILING_WEEKS=3 worth of played weeks (4, in fact) but only 1 designed run per
    # week (4 total, well under QB_DESIGNED_RUN_MIN_TRAILING_VOLUME=8) -- gated to raw_value=None
    # even though the weeks-floor alone would have passed.
    rows = []
    play_id = 1
    for week in (1, 2, 3, 4):
        rows += _qb_pass_attempts(week, "GB", "QB3", "Low Volume QB", 10, play_id)
        play_id += 10
        rows.append(_designed_run(week, "GB", "QB3", "Low Volume QB", play_id))
        play_id += 1

    signals = qb_rushing_ceiling_signals(pd.DataFrame(rows), target_week=5)
    qb3 = next(s for s in signals if s.player_id == "QB3")
    assert qb3.sample_size == 4  # weeks-floor alone would have passed
    assert qb3.raw_value is None  # but the volume floor (QB_DESIGNED_RUN_MIN_TRAILING_VOLUME) gates it
    assert qb3.z_score is None
    assert QB_DESIGNED_RUN_MIN_TRAILING_VOLUME == 8


def test_qb_rushing_ceiling_signals_excludes_future_weeks():
    rows = []
    play_id = 1
    for week in (1, 2, 3):
        rows += _qb_pass_attempts(week, "GB", "QB4", "Trailing Only QB", 10, play_id)
        play_id += 10
        rows.append(_designed_run(week, "GB", "QB4", "Trailing Only QB", play_id))
        play_id += 1
        rows.append(_designed_run(week, "GB", "QB4", "Trailing Only QB", play_id))
        play_id += 1
        rows.append(_designed_run(week, "GB", "QB4", "Trailing Only QB", play_id))
        play_id += 1
    # Week 5 itself (the target week) gets a huge designed-run spike that must not leak in.
    rows += _qb_pass_attempts(5, "GB", "QB4", "Trailing Only QB", 10, play_id)
    play_id += 10
    for _ in range(20):
        rows.append(_designed_run(5, "GB", "QB4", "Trailing Only QB", play_id))
        play_id += 1

    signals = qb_rushing_ceiling_signals(pd.DataFrame(rows), target_week=5)
    qb4 = next(s for s in signals if s.player_id == "QB4")
    assert qb4.sample_size == 3  # unchanged by the week-5 spike


# --------------------------------------------------------------------------------------------
# qb_explosive_rush_rate_signals (ADR-0028/0030 Component E -- level signal, backtest phase)
# --------------------------------------------------------------------------------------------


def _qb_rush_with_yards(week: int, team: str, player_id: str, name: str, play_id: int, yards: float, *, scramble: bool = False) -> dict:
    return _pbp_row(
        week=week, posteam=team, play_type="run", rusher_player_id=player_id, rusher_player_name=name,
        play_id=play_id, qb_scramble=1 if scramble else 0, rushing_yards=yards,
    )


def _qualifying_passer(week: int, team: str, player_id: str, name: str, play_id_start: int) -> list[dict]:
    return _pass_attempts(week, team, player_id, name, 10, play_id_start)


def test_qb_explosive_rush_rate_signals_pools_designed_and_scramble_attempts():
    # QB1: 30 pooled attempts (20 designed + 10 scramble, both types contribute to the pool), 6 of
    # them clearing EXPLOSIVE_RUSH_YARDS_THRESHOLD=15 (3 designed, 3 scramble) -- rate = 6/30 = 0.2.
    rows = _qualifying_passer(1, "GB", "QB1", "Dual Threat", 1)
    play_id = 100
    for i in range(20):
        yards = 20.0 if i < 3 else 5.0
        rows.append(_qb_rush_with_yards(1, "GB", "QB1", "Dual Threat", play_id, yards, scramble=False))
        play_id += 1
    for i in range(10):
        yards = 18.0 if i < 3 else 4.0
        rows.append(_qb_rush_with_yards(1, "GB", "QB1", "Dual Threat", play_id, yards, scramble=True))
        play_id += 1

    signals = qb_explosive_rush_rate_signals(pd.DataFrame(rows), target_week=2)
    qb1 = next(s for s in signals if s.player_id == "QB1")
    assert qb1.sample_size == 30
    assert qb1.raw_value == pytest.approx(0.2)


def test_qb_explosive_rush_rate_signals_gates_below_min_trailing_volume():
    # QB2 has 29 pooled trailing attempts -- one short of QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME=30.
    assert QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME == 30
    rows = _qualifying_passer(1, "GB", "QB2", "Just Short", 1)
    play_id = 100
    for _ in range(29):
        rows.append(_qb_rush_with_yards(1, "GB", "QB2", "Just Short", play_id, 20.0))
        play_id += 1

    signals = qb_explosive_rush_rate_signals(pd.DataFrame(rows), target_week=2)
    qb2 = next(s for s in signals if s.player_id == "QB2")
    assert qb2.sample_size == 29
    assert qb2.raw_value is None
    assert qb2.z_score is None


def test_qb_explosive_rush_rate_signals_excludes_rushers_who_never_clear_the_trailing_passer_gate():
    # A real rusher with real explosive gains but who never cleared the trailing-passer
    # identification threshold (>= QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS=5 pass attempts) must
    # not appear at all -- this signal only covers identified trailing passers, not every rusher.
    rows = _pass_attempts(1, "GB", "QB3", "Not Enough Passes", 4, 1)  # one short of the gate
    play_id = 100
    for _ in range(30):
        rows.append(_qb_rush_with_yards(1, "GB", "QB3", "Not Enough Passes", play_id, 20.0))
        play_id += 1
    rows += [_qb_rush_with_yards(1, "GB", "WR_JET", "Jet Sweep WR", 900 + i, 20.0) for i in range(30)]

    signals = qb_explosive_rush_rate_signals(pd.DataFrame(rows), target_week=2)
    assert "QB3" not in {s.player_id for s in signals}
    assert "WR_JET" not in {s.player_id for s in signals}


def test_qb_explosive_rush_rate_signals_excludes_future_weeks():
    rows = _qualifying_passer(1, "GB", "QB4", "Trailing Only", 1)
    play_id = 100
    for _ in range(30):
        rows.append(_qb_rush_with_yards(1, "GB", "QB4", "Trailing Only", play_id, 5.0))  # none explosive
        play_id += 1
    # Week 2 (the target week) gets a huge explosive spike that must not leak into the trailing read.
    rows += _qualifying_passer(2, "GB", "QB4", "Trailing Only", 500)
    for _ in range(30):
        rows.append(_qb_rush_with_yards(2, "GB", "QB4", "Trailing Only", play_id, 50.0))
        play_id += 1

    signals = qb_explosive_rush_rate_signals(pd.DataFrame(rows), target_week=2)
    qb4 = next(s for s in signals if s.player_id == "QB4")
    assert qb4.sample_size == 30  # unchanged by week 2's spike
    assert qb4.raw_value == pytest.approx(0.0)


def test_qb_explosive_rush_rate_signals_respects_custom_yards_threshold():
    # Same population computed at yards_threshold=10 instead of the default 15 -- the backtest
    # script's required sensitivity check reuses this same parameterization.
    rows = _qualifying_passer(1, "GB", "QB5", "Threshold Check", 1)
    play_id = 100
    for i in range(30):
        rows.append(_qb_rush_with_yards(1, "GB", "QB5", "Threshold Check", play_id, 12.0 if i < 9 else 3.0))
        play_id += 1

    default_signals = qb_explosive_rush_rate_signals(pd.DataFrame(rows), target_week=2)
    qb5_default = next(s for s in default_signals if s.player_id == "QB5")
    assert qb5_default.raw_value == pytest.approx(0.0)  # nothing clears 15 yards

    loose_signals = qb_explosive_rush_rate_signals(pd.DataFrame(rows), target_week=2, yards_threshold=10)
    qb5_loose = next(s for s in loose_signals if s.player_id == "QB5")
    assert qb5_loose.raw_value == pytest.approx(9 / 30)  # 9 rushes clear the looser 10-yard threshold
    assert EXPLOSIVE_RUSH_YARDS_THRESHOLD == 15  # confirms the production default is unchanged


# --------------------------------------------------------------------------------------------
# component_a_multiplier
# --------------------------------------------------------------------------------------------


def _signal(shrunk_z_score: float | None) -> CeilingSignal:
    return CeilingSignal(
        player_id="P1", player_name="Test Player", team="GB", sample_size=5,
        raw_value=0.5, z_score=shrunk_z_score, shrinkage_weight=0.5, shrunk_z_score=shrunk_z_score,
    )


def test_component_a_multiplier_matches_the_signed_off_formula():
    # m_i = max(1.0, exp(scale_i * z_i)) -- RB and WR use different, both-expert-signed-off scales.
    assert component_a_multiplier(_signal(1.0), ROLE_RB) == pytest.approx(math.exp(COMPONENT_A_SCALE[ROLE_RB]))
    assert component_a_multiplier(_signal(1.0), ROLE_WR) == pytest.approx(math.exp(COMPONENT_A_SCALE[ROLE_WR]))
    assert COMPONENT_A_SCALE[ROLE_RB] > COMPONENT_A_SCALE[ROLE_WR]  # RB gets more credit per unit z


def test_component_a_multiplier_floors_at_one_never_penalizes():
    # One-sided by design (ADR-0028) -- a negative z contributes no ceiling credit, but never
    # lowers the read below 1.0.
    assert component_a_multiplier(_signal(-2.0), ROLE_RB) == pytest.approx(1.0)
    assert component_a_multiplier(_signal(0.0), ROLE_RB) == pytest.approx(1.0)


def test_component_a_multiplier_none_when_signal_ungated_not_fabricated_neutral():
    assert component_a_multiplier(_signal(None), ROLE_RB) is None


def test_component_a_multiplier_rejects_unsupported_role():
    with pytest.raises(ValueError, match="RB.*WR|WR.*RB"):
        component_a_multiplier(_signal(1.0), "TE")
