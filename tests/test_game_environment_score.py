import math

import pytest

from nfl_dfs.game_environment.score import (
    ImpliedTotalInput,
    PaceProeInput,
    PlayerInjuryStatus,
    WeatherInput,
    compute_game_environment_score,
    compute_injury_uncertainty_flag,
    compute_weather_subscore,
)


def _neutral_weather(is_indoor: bool = False) -> WeatherInput:
    return WeatherInput(is_indoor=is_indoor)


def test_normal_case_all_neutral_lands_near_midpoint():
    # z=0 everywhere, neutral (dry, calm, temperate) outdoor weather -- every z-scored component
    # should land at exactly half its own weight (Phi(0) = 0.5), and weather should be at its
    # full, undamped weight (no wind/temp/precip degradation at all).
    score = compute_game_environment_score(
        team="KC",
        season=2026,
        week=8,
        implied_total=ImpliedTotalInput(z=0.0),
        pace_proe=PaceProeInput(pace_z=0.0, proe_z=0.0, weeks_played=7, shrinkage_weight=7 / 13),
        weather=_neutral_weather(),
    )
    assert score.is_available
    assert score.implied_total.points == pytest.approx(100 * 4 / 9 * 0.5)
    assert score.pace.points == pytest.approx(100 * 2 / 9 * 0.5)
    assert score.proe.points == pytest.approx(100 * 2 / 9 * 0.5)
    assert score.weather.points == pytest.approx(100 * 1 / 9)  # no degradation -> full weight
    expected_composite = (
        100 * 4 / 9 * 0.5 + 100 * 2 / 9 * 0.5 + 100 * 2 / 9 * 0.5 + 100 * 1 / 9
    )
    assert score.composite_score == pytest.approx(expected_composite)
    # Sanity: a fully-neutral team-week should land near the middle of the 0-100 scale.
    assert 45 < score.composite_score < 60


def test_favorable_environment_scores_above_50_unfavorable_below():
    good = compute_game_environment_score(
        team="BUF",
        season=2026,
        week=8,
        implied_total=ImpliedTotalInput(z=1.5),
        pace_proe=PaceProeInput(pace_z=1.5, proe_z=1.5, weeks_played=7, shrinkage_weight=0.5),
        weather=_neutral_weather(),
    )
    bad = compute_game_environment_score(
        team="NYJ",
        season=2026,
        week=8,
        implied_total=ImpliedTotalInput(z=-1.5),
        pace_proe=PaceProeInput(pace_z=-1.5, proe_z=-1.5, weeks_played=7, shrinkage_weight=0.5),
        weather=_neutral_weather(),
    )
    assert good.composite_score > 60
    assert bad.composite_score < 40
    assert good.composite_score > bad.composite_score


def test_early_season_shrinkage_metadata_surfaced_on_pace_and_proe():
    # Week 1: nflverse.py's compute_pace_proe_for_week would report weeks_played=0 and
    # shrinkage_weight=0.0 (n/(n+6) at n=0) -- fully prior-season baseline. This module doesn't
    # redo that blend, but it must surface the metadata transparently on the component output so
    # a QA reader can see *why* a week-1 z-score is what it is without re-deriving it.
    week1 = compute_game_environment_score(
        team="DET",
        season=2026,
        week=1,
        implied_total=ImpliedTotalInput(z=0.4),
        pace_proe=PaceProeInput(pace_z=0.2, proe_z=0.1, weeks_played=0, shrinkage_weight=0.0),
        weather=_neutral_weather(),
    )
    assert week1.pace.notes[0].startswith("weeks_played=0, shrinkage_weight=0.000")
    assert week1.proe.notes[0].startswith("weeks_played=0, shrinkage_weight=0.000")

    # A mid-season week for the same team: shrinkage_weight should have moved toward 1 (more
    # current-season trust) -- e.g. week 8 -> 7 completed weeks -> w = 7/13 (ADR-0011, k=6).
    mid_season = compute_game_environment_score(
        team="DET",
        season=2026,
        week=8,
        implied_total=ImpliedTotalInput(z=0.4),
        pace_proe=PaceProeInput(pace_z=0.2, proe_z=0.1, weeks_played=7, shrinkage_weight=7 / 13),
        weather=_neutral_weather(),
    )
    assert "weeks_played=7" in mid_season.pace.notes[0]
    assert f"shrinkage_weight={7 / 13:.3f}" in mid_season.pace.notes[0]
    # The z-scores passed in are identical, so both weeks should score pace/PROE identically --
    # this module's job is to combine the already-blended z-score, not to re-derive confidence
    # from weeks_played itself (that's nflverse.py's job, upstream).
    assert week1.pace.points == pytest.approx(mid_season.pace.points)


def test_missing_implied_total_marks_composite_unavailable_not_degraded():
    # ADR-0016 tier 3: no live line, no cached pre-kickoff value at all for this team this week.
    score = compute_game_environment_score(
        team="SEA",
        season=2026,
        week=3,
        implied_total=ImpliedTotalInput(z=None),
        pace_proe=PaceProeInput(pace_z=0.5, proe_z=0.5, weeks_played=2, shrinkage_weight=2 / 8),
        weather=_neutral_weather(),
    )
    assert score.is_available is False
    assert score.composite_score is None
    assert score.implied_total.z is None
    assert score.implied_total.points is None
    # The other components are still computed and visible for QA, even though the composite as a
    # whole is unavailable -- this module's chosen judgment call (see score.py's docstring) is to
    # fail the *composite*, not to hide the components that did compute successfully.
    assert score.pace.points is not None
    assert score.proe.points is not None
    assert any("unavailable" in note for note in score.notes)


def test_indoor_game_redistributes_weather_weight_50_25_25():
    score = compute_game_environment_score(
        team="NO",
        season=2026,
        week=5,
        implied_total=ImpliedTotalInput(z=0.0),
        pace_proe=PaceProeInput(pace_z=0.0, proe_z=0.0, weeks_played=4, shrinkage_weight=0.4),
        weather=_neutral_weather(is_indoor=True),
    )
    assert score.implied_total.weight_pct == pytest.approx(50.0)
    assert score.pace.weight_pct == pytest.approx(25.0)
    assert score.proe.weight_pct == pytest.approx(25.0)
    assert score.weather.weight_pct == pytest.approx(0.0)
    assert score.weather.points is None
    # Weights still sum to 100 after redistribution.
    assert score.implied_total.weight_pct + score.pace.weight_pct + score.proe.weight_pct == pytest.approx(100.0)
    assert any("redistributed" in note for note in score.notes)
    # All-neutral z-scores -> composite should land at exactly 50 now that weather (which never
    # exceeds its own neutral ceiling) is entirely out of the mix.
    assert score.composite_score == pytest.approx(50.0)


def test_weather_subscore_missing_precipitation_treated_as_neutral_leg():
    weather = WeatherInput(
        is_indoor=False,
        wind_any_a_relative_drop=0.12,  # steep-tier damped wind (ADR-0007)
        temperature_relative_drop=0.03,  # mid-cold-dip damped temperature (ADR-0007)
        precipitation_relative_drop=None,  # known gap -- weather.py doesn't supply this yet
    )
    component = compute_weather_subscore(weather, weight_pct=100 / 9)

    m_wind = 1 - 0.12
    m_temp = 1 - 0.03
    m_precip = 1.0  # neutral, per the None-handling contract
    expected_multiplier = m_wind * m_temp * m_precip
    assert component.points == pytest.approx((100 / 9) * expected_multiplier)
    assert any("precipitation leg treated as neutral" in note for note in component.notes)


def test_weather_subscore_combination_cap_binds_on_extreme_conditions():
    # Extreme wind + extreme temperature + a hypothetical severe precipitation leg (as if
    # weather.py already supplied one) should combine to a deviation deeper than 30% before
    # capping -- verify the ADR-0015 +-30% cap actually binds rather than passing the raw product
    # through uncapped.
    weather = WeatherInput(
        is_indoor=False,
        wind_any_a_relative_drop=0.12,
        temperature_relative_drop=0.048,
        precipitation_relative_drop=0.20,  # hypothetical, exercises the cap path
    )
    component = compute_weather_subscore(weather, weight_pct=100 / 9)

    uncapped_log = math.log(1 - 0.12) + math.log(1 - 0.048) + math.log(1 - 0.20)
    assert uncapped_log < math.log(0.70)  # confirms this scenario really does exceed the cap
    assert component.points == pytest.approx((100 / 9) * 0.70)
    assert any("capped" in note for note in component.notes)


def test_weather_subscore_indoor_short_circuits_with_zero_weight():
    component = compute_weather_subscore(WeatherInput(is_indoor=True), weight_pct=100 / 9)
    assert component.weight_pct == 0.0
    assert component.points is None
    assert component.z is None


def test_injury_uncertainty_flag_defaults_to_none_when_no_team_injuries_passed():
    score = compute_game_environment_score(
        team="MIA",
        season=2026,
        week=2,
        implied_total=ImpliedTotalInput(z=0.0),
        pace_proe=PaceProeInput(pace_z=0.0, proe_z=0.0, weeks_played=1, shrinkage_weight=1 / 7),
        weather=_neutral_weather(),
    )
    # `team_injuries` is optional (default None) -- every pre-existing caller that doesn't pass it
    # keeps getting None, unchanged from before this round's wiring.
    assert score.injury_uncertainty_flag is None


def test_injury_uncertainty_flag_none_when_only_resolved_out_players():
    # A confirmed "O" (Out) is known, not uncertain -- see score.py's policy-judgment-call
    # writeup. Should not raise the flag at all, even with a maximum-severity IMPACTRTG.
    assert compute_injury_uncertainty_flag([PlayerInjuryStatus(status="O", impact_rating=10)]) is None


def test_injury_uncertainty_flag_moderate_for_low_impact_questionable_player():
    assert (
        compute_injury_uncertainty_flag([PlayerInjuryStatus(status="Q", impact_rating=3)])
        == "moderate"
    )


def test_injury_uncertainty_flag_none_below_low_impact_floor():
    # ADR-0017 Decision 3's regression case: a worst-unresolved IMPACTRTG of 1 (below the new
    # LOW_IMPACT_FLOOR of 2) is a deep-bench/trivial questionable tag -- it should no longer
    # raise the flag to "moderate" at all.
    assert compute_injury_uncertainty_flag([PlayerInjuryStatus(status="Q", impact_rating=1)]) is None
    assert compute_injury_uncertainty_flag([PlayerInjuryStatus(status="Q", impact_rating=0)]) is None


def test_injury_uncertainty_flag_moderate_at_low_impact_floor_boundary():
    # 2 is the floor itself -- still "moderate" (< 2 is the only thing that maps to None).
    assert (
        compute_injury_uncertainty_flag([PlayerInjuryStatus(status="Q", impact_rating=2)])
        == "moderate"
    )


def test_injury_uncertainty_flag_high_uncertainty_at_or_above_threshold():
    assert (
        compute_injury_uncertainty_flag([PlayerInjuryStatus(status="Q", impact_rating=5)])
        == "high_uncertainty"
    )
    assert (
        compute_injury_uncertainty_flag([PlayerInjuryStatus(status="Q", impact_rating=10)])
        == "high_uncertainty"
    )


def test_injury_uncertainty_flag_treats_unconfirmed_future_status_codes_as_unresolved():
    # "D" (Doubtful) was flagged as "likely" by the task brief but never observed live -- the
    # allowlist-of-resolved-codes design means it (and any other unfamiliar code) defaults to
    # unresolved/uncertain rather than silently being treated as resolved.
    assert (
        compute_injury_uncertainty_flag([PlayerInjuryStatus(status="D", impact_rating=8)])
        == "high_uncertainty"
    )


def test_injury_uncertainty_flag_treats_p_as_unresolved_pending_confirmation():
    # "P" (Probable) was checked for live and deliberately NOT added to RESOLVED_INJURY_STATUSES
    # -- see score.py's RESOLVED_INJURY_STATUSES comment and rotogrinders_injuries.py's module
    # docstring for the full investigation (no legend/glossary found confirming it's a real code
    # this source uses; the grid page's own description names only out/doubtful/questionable).
    # This test locks in that "P" is currently treated the same as any other unconfirmed code
    # (fail-safe: unresolved), so a future change to that allowlist has to consciously update this
    # test rather than silently drifting.
    assert (
        compute_injury_uncertainty_flag([PlayerInjuryStatus(status="P", impact_rating=8)])
        == "high_uncertainty"
    )


def test_injury_uncertainty_flag_uses_worst_unresolved_player_not_an_average():
    # One severely-in-doubt player should read as high_uncertainty even alongside a trivial one,
    # and a resolved "O" player mixed in should not dilute the unresolved player's own severity.
    team = [
        PlayerInjuryStatus(status="O", impact_rating=10),
        PlayerInjuryStatus(status="Q", impact_rating=1),
        PlayerInjuryStatus(status="Q", impact_rating=9),
    ]
    assert compute_injury_uncertainty_flag(team) == "high_uncertainty"


def test_compute_game_environment_score_wires_injury_uncertainty_flag_through():
    score = compute_game_environment_score(
        team="SF",
        season=2026,
        week=3,
        implied_total=ImpliedTotalInput(z=0.2),
        pace_proe=PaceProeInput(pace_z=0.1, proe_z=0.1, weeks_played=2, shrinkage_weight=2 / 8),
        weather=_neutral_weather(),
        team_injuries=[PlayerInjuryStatus(status="Q", impact_rating=7)],
    )
    assert score.is_available
    assert score.injury_uncertainty_flag == "high_uncertainty"


def test_compute_game_environment_score_injury_flag_still_set_when_composite_unavailable():
    # The injury flag is independent of the implied-total-missing branch (ADR-0016 tier 3) --
    # it should still be computed and surfaced even when the composite itself is None.
    score = compute_game_environment_score(
        team="CHI",
        season=2026,
        week=3,
        implied_total=ImpliedTotalInput(z=None),
        pace_proe=PaceProeInput(pace_z=0.0, proe_z=0.0, weeks_played=2, shrinkage_weight=2 / 8),
        weather=_neutral_weather(),
        team_injuries=[PlayerInjuryStatus(status="Q", impact_rating=2)],
    )
    assert not score.is_available
    assert score.composite_score is None
    assert score.injury_uncertainty_flag == "moderate"
