import math

import pandas as pd
import pytest

from nfl_dfs.ceiling.signals import (
    ADOT_MIN_TARGETS,
    CEILING_SHRINKAGE_K,
    COMPONENT_A_SCALE,
    MIN_TRAILING_WEEKS,
    CeilingSignal,
    adot_ceiling_signals,
    component_a_multiplier,
    red_zone_ceiling_signals,
    role_share_ceiling_signals,
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
