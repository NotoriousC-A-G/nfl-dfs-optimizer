"""Unit tests for `dashboard/renderer.py` -- synthetic `WeeklyOutput`/`PlayerDetailRecord` data,
confirming the HTML renders without error for a normal case and a nullable/missing-field case, and
that real data actually appears in the output (not dropped, not rendered as a blank cell or the
literal text "None"). No live network access, no pixel-layout assertions -- string-containment
checks against the rendered HTML, matching this project's existing output-stage test style
(`tests/test_output_weekly.py`, `tests/test_player_detail.py`).
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from nfl_dfs.composition.player_detail import (
    MatchupThisWeek,
    OwnSchemeSplits,
    PlayerDetailRecord,
    PlayerDetailUsage,
    PlayerInjuryDetail,
    RedZoneUsage,
    RoleShareUsage,
    SnapShareUsage,
    StackContext,
)
from nfl_dfs.composition.lineup_dup_risk import LineupDupRiskAssessment
from nfl_dfs.correlation.stack_profile import classify_game_script_lean
from nfl_dfs.dashboard.renderer import SlateGameRow, render_dashboard_html, write_dashboard_html
from nfl_dfs.game_environment.score import ComponentScore, GameEnvironmentScore
from nfl_dfs.ingestion.pff import ResolvedGrade, TeamCoverageTendency
from nfl_dfs.ingestion.qb_rushing_profile import TrailingQbRushingProfile
from nfl_dfs.ingestion.receiving_profile import TrailingReceivingProfile
from nfl_dfs.ingestion.snap_share import PlayerSnapShare
from nfl_dfs.ingestion.usage_share import ROLE_WR, PlayerRoleShare
from nfl_dfs.ingestion.weather import WeatherReading
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch
from nfl_dfs.optimizer.lineup import Lineup
from nfl_dfs.output.weekly_output import build_weekly_output
from nfl_dfs.ownership.leverage import LeverageAssessment

SEASON = 2026
WEEK = 5


# --------------------------------------------------------------------------------------------
# Shared fixture builders (mirrors tests/test_output_weekly.py's helpers)
# --------------------------------------------------------------------------------------------


def _player(canonical_id, position, team, salary=5000, projection=10.0, name=None):
    from nfl_dfs.projection.blend import PlayerProjection

    return PlayerProjection(
        canonical_id=canonical_id,
        display_name=name or canonical_id,
        position=position,
        team=team,
        salary=salary,
        blended_projection=projection,
        source_count=1,
        source_values={"rotogrinders": projection},
    )


def _identity_for(p) -> PlayerIdentity:
    return PlayerIdentity(
        canonical_id=p.canonical_id,
        display_name=p.display_name,
        position=p.position,
        team=p.team,
        sources={
            "draftkings": SourceMatch(
                native_id=f"{p.canonical_id}-dkid", method=MatchMethod.NAME_TEAM_POSITION
            )
        },
    )


def _lineup(suffix: str, qb_team: str = "AAA") -> Lineup:
    qb = _player(f"qb{suffix}", "QB", qb_team, name=f"QB Guy {suffix}")
    wr = _player(f"wr{suffix}", "WR", qb_team, name=f"WR Guy {suffix}")
    rb1 = _player(f"rb1{suffix}", "RB", qb_team, name=f"RB One {suffix}")
    rb2 = _player(f"rb2{suffix}", "RB", "BBB", name=f"RB Two {suffix}")
    wr2 = _player(f"wr2{suffix}", "WR", "BBB", name=f"WR Two {suffix}")
    wr3 = _player(f"wr3{suffix}", "WR", "CCC", name=f"WR Three {suffix}")
    te = _player(f"te{suffix}", "TE", qb_team, name=f"TE One {suffix}")
    flex = _player(f"flex{suffix}", "RB", "DDD", name=f"Flex Guy {suffix}")
    dst = _player(f"dst{suffix}", "DST", "EEE", name=f"Defense {suffix}")

    slots = {
        "QB": qb, "RB1": rb1, "RB2": rb2, "WR1": wr, "WR2": wr2, "WR3": wr3,
        "TE": te, "FLEX": flex, "DST": dst,
    }
    players = tuple(slots.values())
    return Lineup(
        slots=slots,
        players=players,
        total_salary=sum(p.salary for p in players),
        total_projected_points=sum(p.blended_projection for p in players),
        core_stack=frozenset({qb.canonical_id, wr.canonical_id}),
        core_stack_team=qb_team,
    )


def _weekly_output_with_three_lineups():
    lineups = [_lineup("1", "AAA"), _lineup("2", "FFF"), _lineup("3", "GGG")]
    identities = []
    for lineup in lineups:
        identities.extend(_identity_for(p) for p in lineup.players)
    return build_weekly_output(lineups, identities), lineups


def _weekly_output_with_agent_labels():
    # 2026-09-20, Chris: "have you not named the agents? I want to track performance for each
    # one" -- same shape as _weekly_output_with_three_lineups, but with real per-lineup labels.
    lineups = [_lineup("1", "AAA"), _lineup("2", "FFF"), _lineup("3", "GGG")]
    identities = []
    for lineup in lineups:
        identities.extend(_identity_for(p) for p in lineup.players)
    labels = ["Chalk Anchor", "Arbitrageur", "Volatility Engine"]
    return build_weekly_output(lineups, identities, lineup_labels=labels), lineups


def _ges(team, *, is_available=True, composite=71.0, weather_applies=False, injury_flag=None):
    weather = ComponentScore(
        label="Weather",
        weight_pct=11.1 if weather_applies else 0.0,
        z=None,
        points=4.0 if weather_applies else None,
    )
    placeholder = ComponentScore(label="placeholder", weight_pct=0.0, z=None, points=None)
    return GameEnvironmentScore(
        team=team,
        season=SEASON,
        week=WEEK,
        is_available=is_available,
        composite_score=composite if is_available else None,
        implied_total=placeholder,
        pace=placeholder,
        proe=placeholder,
        weather=weather,
        injury_uncertainty_flag=injury_flag,
        notes=[] if is_available else ["no live Odds API line and no cached pre-kickoff fallback"],
    )


def _weather_reading(team: str, *, is_indoor: bool = False, precip_band: str = "none") -> WeatherReading:
    if is_indoor:
        return WeatherReading(
            team=team, is_indoor=True, kickoff_utc="2026-09-14T17:00:00Z",
            temperature_f=None, wind_mph=None, precipitation_in=None, has_precipitation=None,
            wind_any_a_effect_pct=0.0, wind_fg_effect_pct=0.0, temperature_effect_pct=0.0,
            rain_rate_in_hr=None, snowfall_rate_in_hr=None, precipitation_band=None,
            precipitation_relative_drop=0.0, nws_precipitation_probability_pct=None,
            nws_short_forecast=None, nws_storm_flag=None, source_notes="indoor (dome) -- no API call",
        )
    return WeatherReading(
        team=team, is_indoor=False, kickoff_utc="2026-09-14T17:00:00Z",
        temperature_f=42.0, wind_mph=14.0, precipitation_in=0.05 if precip_band != "none" else 0.0,
        has_precipitation=precip_band != "none",
        wind_any_a_effect_pct=0.05, wind_fg_effect_pct=0.01, temperature_effect_pct=0.0,
        rain_rate_in_hr=0.05 if "rain" in precip_band else None,
        snowfall_rate_in_hr=0.5 if "snow" in precip_band else None,
        precipitation_band=precip_band, precipitation_relative_drop=0.02 if precip_band != "none" else 0.0,
        nws_precipitation_probability_pct=None, nws_short_forecast=None, nws_storm_flag=None,
        source_notes="Open-Meteo primary, NWS cross-check",
    )


def _slate_game_row(
    away: str,
    home: str,
    *,
    home_env=None,
    away_env=None,
    home_env_reason=None,
    away_env_reason=None,
    weather=None,
    weather_reason=None,
    home_spread=-3.5,
    total=47.5,
) -> SlateGameRow:
    away_spread = -home_spread if home_spread is not None else None
    return SlateGameRow(
        away_team=away,
        home_team=home,
        kickoff_utc="2026-09-14T17:00:00Z" if home_spread is not None else None,
        kickoff_reason=None if home_spread is not None else "no schedule entry found for this game",
        home_spread=home_spread,
        away_spread=away_spread,
        total=total,
        odds_reason=None if home_spread is not None else "game already underway; dropped from the odds feed",
        home_implied_total=(total / 2 - home_spread / 2) if home_spread is not None and total is not None else None,
        away_implied_total=(total / 2 - away_spread / 2) if away_spread is not None and total is not None else None,
        weather=weather,
        weather_reason=weather_reason,
        home_environment=home_env,
        home_environment_reason=home_env_reason,
        away_environment=away_env,
        away_environment_reason=away_env_reason,
    )


def _fully_populated_player_detail(
    canonical_id: str,
    name: str,
    team: str,
    *,
    salary: int | None = 6500,
    is_primary_rb_stack_candidate: bool = False,
    is_bring_back_rb_candidate: bool = False,
) -> PlayerDetailRecord:
    identity = PlayerIdentity(
        canonical_id=canonical_id,
        display_name=name,
        position="WR",
        team=team,
        nflverse_gsis_id=canonical_id,
    )
    role_share = PlayerRoleShare(
        player_id=canonical_id,
        player_name=name,
        role=ROLE_WR,
        weeks_played=5,
        trailing_volume=42,
        trailing_team_volume=150,
        trailing_share=0.28,
        shrinkage_weight=0.45,
        role_share_blended=0.27,
        role_tier=None,
        prior_used="league_average",
    )
    snap_share = PlayerSnapShare(
        player_id=canonical_id,
        pfr_player_id="SomePl00",
        player_name=name,
        team=team,
        position="WR",
        season=SEASON,
        week=WEEK,
        weeks_played=5,
        offense_pct_last_week=0.91,
        defense_pct_last_week=0.0,
        st_pct_last_week=0.0,
        offense_pct_trailing=0.88,
        defense_pct_trailing=0.0,
        st_pct_trailing=0.0,
    )
    usage = PlayerDetailUsage(
        role_share=RoleShareUsage(role_share=role_share, is_team_identified_leader=True, reason=None),
        snap_share=SnapShareUsage(snap_share=snap_share, reason=None),
        red_zone=RedZoneUsage(
            carries_trailing=None,
            carry_share_trailing=None,
            targets_trailing=9,
            target_share_trailing=0.42,
            target_share_by_week=[(3, 0.60), (4, 0.0), (5, 0.50)],
            reason=None,
        ),
    )
    own_scheme_splits = OwnSchemeSplits(
        applicable=True,
        grades={"man_targets": 20.0, "zone_targets": 25.0, "man_yprr": 2.75, "zone_yprr": 1.90},
        population="cumulative_through_last_completed_week",
        pff_native_id="12345",
        reason=None,
    )
    matchup_this_week = MatchupThisWeek(
        opponent_team="MIN",
        own_unit_grade=None,
        opponent_unit_grade=None,
        coverage_tendency_faced=TeamCoverageTendency(
            team="MIN", man_snaps=180, zone_snaps=220, man_rate=0.45, zone_rate=0.55, defender_count=11
        ),
        coverage_tendency_reason=None,
    )
    return PlayerDetailRecord(
        season=SEASON,
        week=WEEK,
        identity=identity,
        team=team,
        position="WR",
        salary=salary,
        salary_reason=None if salary is not None else "no DK salary found for this player.",
        opponent_team_this_week="MIN",
        usage=usage,
        own_scheme_splits=own_scheme_splits,
        matchup_this_week=matchup_this_week,
        game_environment=_ges(team, composite=82.3, weather_applies=True, injury_flag="moderate"),
        game_environment_reason=None,
        ownership=LeverageAssessment(
            native_id="rg-1",
            name=name,
            position="WR",
            team=team,
            salary=salary or 6500,
            salary_decile=1,
            projected_ownership=4.5,
            ownership_percentile=0.3,
            baseline_ownership=14.0,
            ownership_vs_baseline=-9.5,
            is_chalk=False,
            is_leverage=True,
            note="Priced in the top half of WR salaries this slate (decile 1) but projected at 4.5% "
            "vs a 14.0% historical field-ownership baseline for that price tier (-9.5pt).",
        ),
        ownership_reason=None,
        projection=18.7,
        projection_reason=None,
        stack_context=StackContext(
            home_team=team,
            away_team="MIN",
            home_spread=-3.5,
            single_team_viability=62.0,
            game_stack_viability=55.0,
            is_primary_stack_candidate=True,
            primary_stack_rank=1,
            is_bring_back_candidate=False,
            is_primary_rb_stack_candidate=is_primary_rb_stack_candidate,
            is_bring_back_rb_candidate=is_bring_back_rb_candidate,
            game_script_lean=classify_game_script_lean(-3.5),
            bring_back_status="populated",
            pivot_to=f"{team} implied 27.5, {name} leads targets in a plus game environment.",
        ),
        stack_context_reason=None,
        injury=PlayerInjuryDetail(status="Q", body_part="Ankle", impact_rating=3),
        injury_reason=None,
        slate_window="early",
        slate_window_reason=None,
        implied_total=27.5,
        implied_total_reason=None,
        ceiling_multiplier=1.09,
        ceiling_multiplier_reason=None,
        red_zone_role_security_discount=None,
        red_zone_role_security_discount_reason="no WR red-zone role-security signal for this player.",
        receiving_profile=TrailingReceivingProfile(
            player_id="rz-1", player_name=name, team=team,
            trailing_targets=30, trailing_receptions=22, trailing_air_yards=270,
            trailing_adot=9.0, trailing_yac_per_reception=4.5,
        ),
        receiving_profile_reason=None,
        qb_rushing_profile=None,
        qb_rushing_profile_reason="trailing QB rushing-opportunity profile only applies to QB.",
    )


def _rb_with_tier_and_uncontested_prior(canonical_id: str, name: str, team: str) -> PlayerDetailRecord:
    from nfl_dfs.ingestion.usage_share import ROLE_RB

    identity = PlayerIdentity(
        canonical_id=canonical_id, display_name=name, position="RB", team=team, nflverse_gsis_id=canonical_id
    )
    role_share = PlayerRoleShare(
        player_id=canonical_id,
        player_name=name,
        role=ROLE_RB,
        weeks_played=5,
        trailing_volume=90,
        trailing_team_volume=140,
        trailing_share=0.64,
        shrinkage_weight=0.45,
        role_share_blended=0.62,
        role_tier="bell_cow",
        prior_used="uncontested",
    )
    usage = PlayerDetailUsage(
        role_share=RoleShareUsage(role_share=role_share, is_team_identified_leader=True, reason=None),
        snap_share=SnapShareUsage(snap_share=None, reason="no snap-share data for this player this week."),
        red_zone=RedZoneUsage(
            carries_trailing=12,
            carry_share_trailing=0.70,
            targets_trailing=None,
            target_share_trailing=None,
            reason=None,
        ),
    )
    own_scheme_splits = OwnSchemeSplits(
        applicable=True, grades={}, population=None, pff_native_id=None,
        reason="no PFF player_id resolved for this player.",
    )
    matchup_this_week = MatchupThisWeek(
        opponent_team=None,
        own_unit_grade=None,
        opponent_unit_grade=None,
        coverage_tendency_faced=None,
        coverage_tendency_reason="no opponent identified for this week (a bye week, or opponent not supplied to the composer).",
    )
    return PlayerDetailRecord(
        season=SEASON,
        week=WEEK,
        identity=identity,
        team=team,
        position="RB",
        salary=None,
        salary_reason="no DK salary found for this player -- no PlayerProjection supplied/matched.",
        opponent_team_this_week=None,
        usage=usage,
        own_scheme_splits=own_scheme_splits,
        matchup_this_week=matchup_this_week,
        game_environment=None,
        game_environment_reason="no GameEnvironmentScore supplied for this team this week.",
        ownership=None,
        ownership_reason="no LeverageAssessment found for this player.",
        projection=None,
        projection_reason="no blended projection found for this player.",
        stack_context=None,
        stack_context_reason="no StackProfile found for this player's game.",
        injury=None,
        injury_reason="not on this week's injury report -- presumed healthy.",
        slate_window=None,
        slate_window_reason="no kickoff time known for this player's game.",
        implied_total=None,
        implied_total_reason="no implied point total for this player's team.",
        ceiling_multiplier=None,
        ceiling_multiplier_reason="no Component A ceiling signal for this player.",
        red_zone_role_security_discount=None,
        red_zone_role_security_discount_reason="the WR red-zone role-security discount is WR-only.",
        receiving_profile=None,
        receiving_profile_reason="no trailing receiving-opportunity profile for this player.",
        qb_rushing_profile=None,
        qb_rushing_profile_reason="trailing QB rushing-opportunity profile only applies to QB.",
    )


def _qb_with_rushing_profile(canonical_id: str, name: str, team: str) -> PlayerDetailRecord:
    identity = PlayerIdentity(
        canonical_id=canonical_id, display_name=name, position="QB", team=team, nflverse_gsis_id=canonical_id
    )
    usage = PlayerDetailUsage(
        role_share=RoleShareUsage(role_share=None, is_team_identified_leader=False, reason="not a skill position."),
        snap_share=SnapShareUsage(snap_share=None, reason="no snap-share data for this player this week."),
        red_zone=RedZoneUsage(
            carries_trailing=None, carry_share_trailing=None, targets_trailing=None, target_share_trailing=None,
            reason="no red-zone trailing data for this player.",
        ),
    )
    own_scheme_splits = OwnSchemeSplits(
        applicable=False, grades={}, population=None, pff_native_id=None,
        reason="own-scheme splits only apply to pass-catchers (RB/WR/TE) -- not a data gap for QB.",
    )
    matchup_this_week = MatchupThisWeek(
        opponent_team=None, own_unit_grade=None, opponent_unit_grade=None, coverage_tendency_faced=None,
        coverage_tendency_reason="no opponent identified for this week.",
    )
    return PlayerDetailRecord(
        season=SEASON,
        week=WEEK,
        identity=identity,
        team=team,
        position="QB",
        salary=None,
        salary_reason="no DK salary found for this player.",
        opponent_team_this_week=None,
        usage=usage,
        own_scheme_splits=own_scheme_splits,
        matchup_this_week=matchup_this_week,
        game_environment=None,
        game_environment_reason="no GameEnvironmentScore supplied for this team this week.",
        ownership=None,
        ownership_reason="no LeverageAssessment found for this player.",
        projection=None,
        projection_reason="no blended projection found for this player.",
        stack_context=None,
        stack_context_reason="no StackProfile found for this player's game.",
        injury=None,
        injury_reason="not on this week's injury report -- presumed healthy.",
        slate_window=None,
        slate_window_reason="no kickoff time known for this player's game.",
        implied_total=None,
        implied_total_reason="no implied point total for this player's team.",
        ceiling_multiplier=None,
        ceiling_multiplier_reason="Component A ceiling is only calibrated for RB/WR.",
        red_zone_role_security_discount=None,
        red_zone_role_security_discount_reason="the WR red-zone role-security discount is WR-only.",
        receiving_profile=None,
        receiving_profile_reason="trailing receiving-opportunity profile only applies to pass-catchers.",
        qb_rushing_profile=TrailingQbRushingProfile(
            player_id=canonical_id, player_name=name, team=team,
            trailing_rush_attempts=25, trailing_designed_runs=10, trailing_scrambles=15,
            designed_run_rate=0.40, trailing_rushing_yards=140, trailing_rush_tds=3,
            trailing_redzone_rush_attempts=6, trailing_goalline_rush_attempts=3,
        ),
        qb_rushing_profile_reason=None,
    )


# --------------------------------------------------------------------------------------------
# 1. Normal case -- renders without error, real data appears
# --------------------------------------------------------------------------------------------


def test_normal_case_renders_all_three_tabs_and_real_lineup_data():
    weekly_output, lineups = _weekly_output_with_three_lineups()
    players = [
        _fully_populated_player_detail("wr_star", "Star Wideout", "AAA"),
        _rb_with_tier_and_uncontested_prior("rb_lead", "Lead Back", "AAA"),
    ]

    html = render_dashboard_html(weekly_output, players)

    # Three tabs present.
    assert 'data-panel="lineups"' in html
    assert 'data-panel="exposure"' in html
    assert 'data-panel="players"' in html

    # Lineup count matches input -- 3 lineup cards, real player names in the roster tables.
    assert html.count('class="lineup-card"') == 3
    for lineup in lineups:
        for player in lineup.players:
            assert player.display_name in html

    # The real rationale text is reused verbatim (not rewritten), and shown under each lineup,
    # not buried behind a click/drawer. HTML-escaped since it's rendered as element content.
    import html as _html_mod

    for rationale in weekly_output.rationales:
        assert _html_mod.escape(rationale.text, quote=True) in html

    # Exposure report: real counts/percentages appear.
    for pe in weekly_output.exposure_report.players:
        assert pe.display_name in html

    # Player-detail real values appear: role share pct, tier badge, leader badge, uncontested
    # badge, red-zone target share, own-scheme YPRR differential, coverage tendency, weather/
    # injury badges.
    assert "Star Wideout" in html
    assert "$6,500" in html  # salary
    assert "27.0%" in html  # role_share_blended
    assert "42.0%" in html  # target_share_trailing
    assert "W3 60.0%" in html and "W4 0.0%" in html and "W5 50.0%" in html  # target_share_by_week trend
    assert "Identified leader" in html
    assert "Bell-cow" in html
    assert "Uncontested prior" in html
    assert "MIN" in html  # opponent / coverage tendency team
    assert "45.0% man" in html
    assert "Weather: external, unvalidated" in html
    assert "Moderate injury uncertainty" in html
    assert "4.5% proj" in html  # ownership.projected_ownership
    assert "vs 14.0% baseline (-9.5pt)" in html
    assert ">Leverage<" in html  # leverage badge, not the chalk badge

    # New (ADR-0027): projection/value/stack-context/slate-window/injury real values appear.
    assert "18.7" in html  # projection
    assert "2.88" in html  # value: 18.7 / (6500 / 1000)
    assert ">Primary #1<" in html  # stack_context.is_primary_stack_candidate badge
    assert "AAA -3.5" in html  # stack_context.home_spread
    assert "62 viability" in html  # stack_context.single_team_viability
    assert "Early (1pm ET)" in html  # slate_window
    assert "Q &middot; Ankle" in html  # injury.status / body_part
    assert "Impact 3/10" in html  # injury.impact_rating

    # Sortable columns, view sub-tabs, and per-row expand markup are present.
    assert "sortPlayerDetailRows" in html
    assert 'class="view-filter' in html
    assert 'class="player-expand-row"' in html

    # ADR-0028: real Component A ceiling read appears -- 18.7 projection * 1.09 multiplier.
    assert "20.4" in html  # ceiling_projection
    assert "1.09x" in html

    # ADR-0029: real descriptive receiving-opportunity data appears (targets/air yards/aDOT/YAC).
    assert "30 targets, 22 rec" in html
    assert "270 air yds" in html
    assert "9.0 aDOT" in html
    assert "4.5 YAC/rec" in html

    # Search filter present for the browsable player table.
    assert 'id="player-search"' in html

    # No literal "None" leaking into a rendered cell as a stand-in for a real value (the
    # legend text intentionally mentions None inside <code> tags to document the placeholder
    # fields, so check specific cell-rendering patterns rather than a blanket ">None<" scan).
    assert "<td>None</td>" not in html
    assert 'cell-main">None<' not in html
    assert 'cell-sub">None<' not in html


def test_write_dashboard_html_writes_the_same_content_to_disk(tmp_path):
    weekly_output, _ = _weekly_output_with_three_lineups()
    players = [_fully_populated_player_detail("wr_star", "Star Wideout", "AAA")]

    out_path = tmp_path / "dashboard.html"
    write_dashboard_html(str(out_path), weekly_output, players)

    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "Star Wideout" in content
    assert "<!doctype html>" in content
    assert 'data-panel="players"' in content


# --------------------------------------------------------------------------------------------
# 2. Nullable / missing-field case -- reason strings appear, never blank/"None"
# --------------------------------------------------------------------------------------------


def test_missing_fields_show_reason_strings_not_blank_or_none():
    weekly_output, _ = _weekly_output_with_three_lineups()
    bye_week_rb = _rb_with_tier_and_uncontested_prior("rb_bye", "Bye Week Back", "ZZZ")

    html = render_dashboard_html(weekly_output, [bye_week_rb])

    # Salary missing -> real reason string shown, not a blank cell or "None".
    assert "no DK salary found for this player" in html
    # Snap share missing -> real reason string shown, not a blank cell or "None".
    assert "no snap-share data for this player this week." in html
    # Own-scheme-splits missing (no PFF id resolved) -> real reason string shown.
    assert "no PFF player_id resolved for this player." in html
    # No opponent this week (bye) -> real reason string shown.
    assert "no opponent identified for this week (a bye week" in html
    # No GameEnvironmentScore supplied -> real reason string shown.
    assert "no GameEnvironmentScore supplied for this team this week." in html
    # No LeverageAssessment supplied -> real reason string shown.
    assert "no LeverageAssessment found for this player." in html
    # New (ADR-0027): projection/stack-context/slate-window/implied-total missing -> real reasons.
    assert "no blended projection found for this player." in html
    assert "no StackProfile found for this player&#x27;s game." in html
    assert "no kickoff time known for this player&#x27;s game." in html
    # New (ADR-0028): no ceiling signal supplied -> real reason string shown.
    assert "no Component A ceiling signal for this player." in html
    # New (ADR-0029): no receiving-opportunity profile supplied -> real reason string shown.
    assert "no trailing receiving-opportunity profile for this player." in html
    # Not on the injury report is real, positive information -- rendered plainly, not as an N/A.
    assert "Healthy" in html
    assert "presumed healthy" not in html  # the raw reason text stays internal, not user-facing

    # Never the bare literal "None" standing in for one of these missing values.
    assert "<td>None</td>" not in html
    assert 'cell-main">None<' not in html
    assert 'cell-sub">None<' not in html
    assert "N/A &mdash; None" not in html


# --------------------------------------------------------------------------------------------
# ADR-0030: qb_rushing_profile -- QB-only expand block, descriptive not a ceiling signal
# --------------------------------------------------------------------------------------------


def test_qb_rushing_profile_renders_for_qb_but_not_for_other_positions():
    weekly_output, _ = _weekly_output_with_three_lineups()
    qb = _qb_with_rushing_profile("qb_mobile", "Mobile QB", "ZZZ")
    rb = _rb_with_tier_and_uncontested_prior("rb_bye", "Bye Week Back", "ZZZ")

    html = render_dashboard_html(weekly_output, [qb, rb])

    # The QB's real rushing numbers appear, not dropped or blanked.
    assert "25 rush att" in html
    assert "10 designed / 15 scramble" in html
    assert "40.0% designed-run rate" in html
    assert "140 yds, 3 TD" in html
    assert "6 RZ / 3 goal-line att" in html

    # The block label itself only appears once -- for the QB row, not the RB row (RB's
    # qb_rushing_profile_reason is the "not applicable" reason, which must not render a block).
    assert html.count(">QB Rushing<") == 1


# --------------------------------------------------------------------------------------------
# ADR-0034: dup_risk_by_lineup -- real historical dup-risk read per generated lineup
# --------------------------------------------------------------------------------------------


def test_dup_risk_renders_real_read_for_the_lineup_it_is_keyed_to():
    weekly_output, _ = _weekly_output_with_three_lineups()
    dup_risk_by_lineup = {
        0: LineupDupRiskAssessment(
            avg_projected_ownership=27.3, players_covered=9, bucket=8, dup_rate=0.0365,
            mean_lineup_ct=1.066, reason=None,
        ),
    }
    html = render_dashboard_html(weekly_output, [], dup_risk_by_lineup=dup_risk_by_lineup)

    assert "3.6% historical field-duplication rate" in html
    assert "27.3% avg proj. ownership across 9/9 players" in html
    assert "ADR-0033" in html
    # Only lineup 0 was keyed -- lineups 1/2 must render no Dup Risk block at all (absent input,
    # absent section, not a fabricated placeholder).
    assert html.count("Dup Risk") == 1


def test_dup_risk_shows_the_real_reason_when_ungated():
    weekly_output, _ = _weekly_output_with_three_lineups()
    dup_risk_by_lineup = {
        0: LineupDupRiskAssessment(
            avg_projected_ownership=None, players_covered=2, bucket=None, dup_rate=None,
            mean_lineup_ct=None, reason="only 2/9 of this lineup's players resolved to a live projected-ownership number.",
        ),
    }
    html = render_dashboard_html(weekly_output, [], dup_risk_by_lineup=dup_risk_by_lineup)
    assert "only 2/9 of this lineup" in html


def test_dup_risk_omitted_entirely_when_not_supplied():
    weekly_output, _ = _weekly_output_with_three_lineups()
    html = render_dashboard_html(weekly_output, [])
    assert "Dup Risk" not in html


# --------------------------------------------------------------------------------------------
# ADR-0036: red_zone_role_security_discount rendered as a sub-line in the Ceiling cell
# --------------------------------------------------------------------------------------------


def test_red_zone_role_security_discount_renders_as_a_ceiling_sub_line_when_real():
    import dataclasses

    weekly_output, _ = _weekly_output_with_three_lineups()
    wr = _fully_populated_player_detail("wr_star", "Star Wideout", "AAA")
    wr = dataclasses.replace(wr, red_zone_role_security_discount=0.94, red_zone_role_security_discount_reason=None)

    html = render_dashboard_html(weekly_output, [wr])
    assert "0.94x red-zone role-security" in html


def test_red_zone_role_security_discount_no_sub_line_when_neutral_or_absent():
    weekly_output, _ = _weekly_output_with_three_lineups()
    wr = _fully_populated_player_detail("wr_star", "Star Wideout", "AAA")  # discount is None by default
    html = render_dashboard_html(weekly_output, [wr])
    assert "red-zone role-security" not in html


def test_rb_stack_badge_renders_when_primary_rb_candidate() -> None:
    weekly_output, _ = _weekly_output_with_three_lineups()
    rb = _fully_populated_player_detail("rb_star", "Bell Cow", "AAA", is_primary_rb_stack_candidate=True)

    html = render_dashboard_html(weekly_output, [rb])

    assert "RB Stack" in html
    assert "favorite" in html  # game_script_lean.stance sub-text (spread=-3.5, home team)


def test_rb_bring_back_badge_renders_when_bring_back_rb_candidate() -> None:
    weekly_output, _ = _weekly_output_with_three_lineups()
    rb = _fully_populated_player_detail("rb_star", "Bring Back Back", "AAA", is_bring_back_rb_candidate=True)

    html = render_dashboard_html(weekly_output, [rb])

    assert "RB Bring-back" in html


def test_no_rb_badges_when_neither_rb_candidate_field_set() -> None:
    weekly_output, _ = _weekly_output_with_three_lineups()
    wr = _fully_populated_player_detail("wr_star", "Star Wideout", "AAA")

    html = render_dashboard_html(weekly_output, [wr])

    assert "RB Stack" not in html
    assert "RB Bring-back" not in html


def test_circumstance_expand_block_renders_when_assessment_present() -> None:
    import dataclasses

    from nfl_dfs.analysis.circumstance import CircumstanceAssessment

    weekly_output, _ = _weekly_output_with_three_lineups()
    rb = _fully_populated_player_detail("rb_star", "Bell Cow", "AAA")
    rb = dataclasses.replace(
        rb,
        circumstance_assessment=CircumstanceAssessment(
            kind="injury",
            pov="Expect an expanded workhorse role this week.",
            model="claude-sonnet-5",
            generated_at="2026-09-19T12:00:00+00:00",
            evidence_article_titles=["Vikings backfield notes"],
        ),
    )

    html = render_dashboard_html(weekly_output, [rb])

    assert "Circumstance" in html
    assert "Expect an expanded workhorse role this week." in html
    assert "Vikings backfield notes" in html
    assert "claude-sonnet-5" in html


def test_circumstance_expand_block_omitted_when_no_assessment() -> None:
    weekly_output, _ = _weekly_output_with_three_lineups()
    wr = _fully_populated_player_detail("wr_star", "Star Wideout", "AAA")

    html = render_dashboard_html(weekly_output, [wr])

    assert "expand-label\">Circumstance<" not in html


def test_empty_weekly_output_and_empty_player_pool_render_without_crashing():
    empty_output = build_weekly_output([], [])
    html = render_dashboard_html(empty_output, [])

    assert 'data-panel="lineups"' in html
    assert 'data-panel="exposure"' in html
    assert 'data-panel="players"' in html
    assert "No lineups in this weekly output." in html
    assert "No player-detail records supplied." in html


def test_unit_matchup_grade_renders_a_real_reason_when_not_supplied():
    # Wired in 2026-09-22 (Chris: "If we don't have that, what are we even doing?") -- previously
    # own_unit_grade/opponent_unit_grade were excluded from every row entirely and only explained
    # once in the legend. Now the "Unit Matchup Grade" expand block renders per row for real,
    # showing each player's own stated reason when the grade wasn't supplied (this fixture's
    # `matchup_this_week` sets both to None with no reason -- the honest "no data supplied"
    # fallback, not a blank cell or the literal text "None").
    weekly_output, _ = _weekly_output_with_three_lineups()
    players = [
        _fully_populated_player_detail("wr_a", "Player A", "AAA"),
        _fully_populated_player_detail("wr_b", "Player B", "AAA"),
        _fully_populated_player_detail("wr_c", "Player C", "AAA"),
    ]

    html = render_dashboard_html(weekly_output, players)

    assert "Unit Matchup Grade" in html
    assert html.count("no data supplied") >= 3  # once per player row, not just the legend


def test_unit_matchup_grade_renders_real_grade_values_when_supplied():
    weekly_output, _ = _weekly_output_with_three_lineups()
    record = _fully_populated_player_detail("rb_a", "Real Grade RB", "AAA")
    record = dataclasses.replace(
        record,
        position="RB",
        matchup_this_week=dataclasses.replace(
            record.matchup_this_week,
            own_unit_grade=ResolvedGrade(
                native_id="TEAM_AAA", position="TEAM_UNIT", grades={"grades_run_block": 72.5}, player_game_count=5,
                population="cumulative_through_last_completed_week",
            ),
            opponent_unit_grade=ResolvedGrade(
                native_id="TEAM_MIN", position="TEAM_UNIT", grades={"grades_run_defense": 61.0}, player_game_count=5,
                population="cumulative_through_last_completed_week",
            ),
        ),
    )

    html = render_dashboard_html(weekly_output, [record])

    assert "72.5" in html
    assert "61.0" in html
    assert "grades_run_block" in html
    assert "grades_run_defense" in html


# --------------------------------------------------------------------------------------------
# 3. Position filter -- only renders buttons for positions actually present
# --------------------------------------------------------------------------------------------


def test_position_filter_buttons_only_include_positions_present_in_the_data():
    # Only WR and RB records supplied -- no QB/TE/DST in this render at all.
    players = [
        _fully_populated_player_detail("wr_star", "Star Wideout", "AAA"),
        _rb_with_tier_and_uncontested_prior("rb_lead", "Lead Back", "AAA"),
    ]
    weekly_output, _ = _weekly_output_with_three_lineups()

    html = render_dashboard_html(weekly_output, players)

    assert 'data-pos="ALL"' in html
    assert 'data-pos="WR"' in html
    assert 'data-pos="RB"' in html
    assert 'data-pos="QB"' not in html
    assert 'data-pos="TE"' not in html
    assert 'data-pos="DST"' not in html

    # Each row carries its own position for the client-side filter to match against.
    assert '<tr class="player-row" data-position="WR"' in html
    assert '<tr class="player-row" data-position="RB"' in html


def test_position_filter_buttons_and_search_box_both_present_in_output():
    weekly_output, _ = _weekly_output_with_three_lineups()
    players = [_fully_populated_player_detail("wr_star", "Star Wideout", "AAA")]

    html = render_dashboard_html(weekly_output, players)

    assert 'class="pos-filters"' in html
    assert 'class="pos-filter active" data-pos="ALL"' in html
    assert 'id="player-search"' in html
    assert 'oninput="filterPlayerDetailRows()"' in html
    # Position buttons wire up the client-side filter, same JS-function pattern as the tab
    # switcher and the existing search box (no framework, no build step).
    assert "setPositionFilter(" in html
    assert "function setPositionFilter" in html


def test_no_position_filters_rendered_when_no_player_details_supplied():
    weekly_output, _ = _weekly_output_with_three_lineups()

    html = render_dashboard_html(weekly_output, [])

    assert 'class="pos-filters"' not in html


def test_lineup_membership_badge_shows_which_lineups_a_player_appears_in():
    weekly_output, lineups = _weekly_output_with_three_lineups()
    # Pick a real player from the first generated lineup and build a PlayerDetailRecord for them.
    lineup_1_qb = lineups[0].slots["QB"]
    record = _fully_populated_player_detail(lineup_1_qb.canonical_id, lineup_1_qb.display_name, lineup_1_qb.team)

    html = render_dashboard_html(weekly_output, [record])

    assert "In L1" in html


def test_lineup_card_shows_the_real_agent_name_when_labels_are_supplied():
    weekly_output, _ = _weekly_output_with_agent_labels()

    html = render_dashboard_html(weekly_output, [], [])

    assert "Chalk Anchor" in html
    assert "Arbitrageur" in html
    assert "Volatility Engine" in html
    assert "Lineup 1:" not in html  # the plain positional label is fully replaced, not appended


def test_lineup_membership_badge_shows_the_real_agent_name_when_labels_are_supplied():
    weekly_output, lineups = _weekly_output_with_agent_labels()
    lineup_1_qb = lineups[0].slots["QB"]
    record = _fully_populated_player_detail(lineup_1_qb.canonical_id, lineup_1_qb.display_name, lineup_1_qb.team)

    html = render_dashboard_html(weekly_output, [record])

    assert "In Chalk Anchor" in html


# --------------------------------------------------------------------------------------------
# 4. Slate Overview tab -- one row per game, real odds/weather/GameEnvironmentScore data,
#    missing-score and injury-flag cases, and the new tab's structural presence
# --------------------------------------------------------------------------------------------


def test_slate_overview_tab_appears_alongside_the_existing_three():
    weekly_output, _ = _weekly_output_with_three_lineups()

    html = render_dashboard_html(weekly_output, [], [])

    assert 'data-panel="lineups"' in html
    assert 'data-panel="exposure"' in html
    assert 'data-panel="players"' in html
    assert 'data-panel="slate"' in html
    assert 'id="panel-slate"' in html
    assert "Slate Overview" in html


def test_slate_overview_normal_case_renders_odds_weather_and_both_scores():
    weekly_output, _ = _weekly_output_with_three_lineups()
    game = _slate_game_row(
        "BUF", "MIA",
        home_env=_ges("MIA", composite=88.4, weather_applies=True),
        away_env=_ges("BUF", composite=81.2, weather_applies=True),
        weather=_weather_reading("MIA"),
        home_spread=-3.5,
        total=47.5,
    )

    html = render_dashboard_html(weekly_output, [], [game])

    # Matchup, kickoff, spread/total, implied totals all real values, not blank.
    assert "BUF @ MIA" in html
    assert "MIA -3.5" in html
    assert "O/U 47.5" in html
    assert "BUF 22.0" in html  # implied total: 47.5/2 - 3.5/2 = 22.0
    assert "MIA 25.5" in html  # implied total: 47.5/2 - (-3.5)/2 = 25.5
    # Both teams' composite scores appear.
    assert "88.4 / 100" in html
    assert "81.2 / 100" in html
    # Weather conditions and the reused "external, unvalidated" badge appear.
    assert "42&deg;F, 14mph wind" in html
    assert "Weather: external, unvalidated" in html
    # A conviction badge is present (this is the only game -- it's a clean top-conviction read).
    assert '>Top conviction<' in html

    # Never a blank cell or the literal "None".
    assert "<td>None</td>" not in html
    assert 'cell-main">None<' not in html


def test_slate_overview_implied_totals_sum_back_to_the_total():
    weekly_output, _ = _weekly_output_with_three_lineups()
    game = _slate_game_row("BUF", "MIA", home_spread=-3.5, total=47.5)
    html = render_dashboard_html(weekly_output, [], [game])
    # home (MIA, -3.5 favorite): 47.5/2 - (-3.5)/2 = 23.75 + 1.75 = 25.5
    # away (BUF, +3.5 underdog): 47.5/2 - 3.5/2 = 23.75 - 1.75 = 22.0
    assert "BUF 22.0" in html
    assert "MIA 25.5" in html


def test_slate_overview_missing_game_environment_score_shows_real_reason_and_is_deprioritized():
    weekly_output, _ = _weekly_output_with_three_lineups()
    # A clean, fully-scored, high-magnitude game...
    clean_game = _slate_game_row(
        "KC", "DEN",
        home_env=_ges("DEN", composite=60.0),
        away_env=_ges("KC", composite=62.0),
        weather=_weather_reading("DEN"),
    )
    # ...and a game where one team's score is unavailable, even though the other team's raw
    # number (if it existed) would be higher -- it must NOT be ranked above the clean game.
    unavailable_game = _slate_game_row(
        "DAL", "PHI",
        home_env=_ges("PHI", composite=95.0),
        away_env=_ges("DAL", is_available=False),
        away_env_reason=None,
        weather=_weather_reading("PHI"),
    )

    html = render_dashboard_html(weekly_output, [], [clean_game, unavailable_game])

    # The real reason string for the unavailable score renders, never a blank cell or "None".
    assert "no live Odds API line and no cached pre-kickoff fallback" in html
    assert "<td>None</td>" not in html

    # Ordering: the clean, fully-scored game's row must appear before the partially-unavailable
    # game's row in the rendered HTML (server-sorted, no JS re-sort, same as the Exposure tab).
    clean_pos = html.index("KC @ DEN")
    unavailable_pos = html.index("DAL @ PHI")
    assert clean_pos < unavailable_pos

    # The partially-unavailable game must not be crowned "Top conviction" even though PHI's own
    # number (95.0) is higher than either team in the clean game.
    unavailable_row_start = html.rindex("<tr>", 0, unavailable_pos)
    unavailable_row_end = html.index("</tr>", unavailable_pos)
    assert "Top conviction" not in html[unavailable_row_start:unavailable_row_end]
    assert "95.0 / 100" in html  # PHI's real score still shown, just not used to rank it first


def test_slate_overview_injury_uncertainty_flag_renders_badge_and_caps_conviction():
    weekly_output, _ = _weekly_output_with_three_lineups()
    game = _slate_game_row(
        "SF", "SEA",
        home_env=_ges("SEA", composite=90.0, injury_flag="high_uncertainty"),
        away_env=_ges("SF", composite=88.0),
        weather=_weather_reading("SEA"),
    )

    html = render_dashboard_html(weekly_output, [], [game])

    assert "High injury uncertainty" in html
    assert "Conviction capped" in html
    # Even with a very high composite score, the injury flag withholds the "Top conviction" badge
    # (the phrase itself also appears in the tab's legend prose, so check for the actual badge
    # markup rather than a bare substring match).
    assert '>Top conviction<' not in html


def test_slate_overview_both_teams_injury_flagged_shows_both_badges_labeled_and_separated():
    # Regression test for a real bug: when both teams in a game carried an injury-uncertainty
    # flag (DAL @ NYG both flagged `high_uncertainty` in live week-1 data), the away team's
    # "High injury uncertainty" badge in the Game Environment cell escaped its own flex column
    # and rendered glued directly against the Conviction cell's badge with no gap -- visually two
    # identical, unlabeled badges overlapping into unreadable text. The fix (a) contains each
    # team's Game Environment content in its own flex column so a badge can no longer overflow
    # into the next cell, and (b) has the Conviction cell itself show *both* teams' flags,
    # labeled, instead of collapsing to just the worse one.
    weekly_output, _ = _weekly_output_with_three_lineups()
    game = _slate_game_row(
        "DAL", "NYG",
        away_env=_ges("DAL", composite=79.4, injury_flag="high_uncertainty"),
        home_env=_ges("NYG", composite=44.1, injury_flag="high_uncertainty"),
        weather=_weather_reading("NYG"),
    )

    html = render_dashboard_html(weekly_output, [], [game])

    # Both teams' Game Environment blocks are now contained in their own `.ge-team` wrapper, so
    # each team's badge stays under its own score instead of all sub/main/badge divs flattening
    # into one un-scoped flex row.
    assert html.count('<div class="ge-team">') == 2

    # The Conviction cell shows both flags, each clearly labeled by team -- not one bare,
    # ambiguous badge standing in for both.
    conviction_start = html.index("Conviction capped")
    conviction_cell_start = html.rindex("<td>", 0, conviction_start)
    conviction_cell_end = html.index("</td>", conviction_start)
    conviction_cell = html[conviction_cell_start:conviction_cell_end]

    assert "DAL: " in conviction_cell
    assert "NYG: " in conviction_cell
    assert conviction_cell.count("High injury uncertainty") == 2

    # The two badges are in distinct block-level containers (real separation), not concatenated
    # with no separator between them -- this is the actual assertion that would have caught the
    # original glued-together rendering.
    assert conviction_cell.count('<div class="cell-injury">') == 2
    assert "</div><div class=\"cell-injury\">" in conviction_cell


def test_slate_overview_empty_games_list_renders_without_crashing():
    weekly_output, _ = _weekly_output_with_three_lineups()

    html = render_dashboard_html(weekly_output, [], [])

    assert "No games supplied for this slate." in html
    assert 'data-panel="slate"' in html


def test_slate_overview_default_games_param_is_backward_compatible():
    """Existing callers that don't pass slate_games at all (pre-this-round signature) still work
    -- the new tab just renders its own real empty-state, not a crash."""
    weekly_output, _ = _weekly_output_with_three_lineups()

    html = render_dashboard_html(weekly_output, [])

    assert 'data-panel="slate"' in html
    assert "No games supplied for this slate." in html


# --------------------------------------------------------------------------------------------
# Lineup cards: Opp and Proj Own columns
# --------------------------------------------------------------------------------------------


def test_lineup_card_shows_opponent_with_home_away_and_projected_ownership():
    weekly_output, _ = _weekly_output_with_three_lineups()
    qb = _fully_populated_player_detail("qb1", "QB Guy 1", "AAA")  # ownership fixture: 4.5% proj
    html = render_dashboard_html(
        weekly_output, [qb], slate_games=[_slate_game_row("AAA", "BBB"), _slate_game_row("CCC", "DDD")]
    )
    card = html.split('class="lineup-card"')[1].split("</table>")[0]
    assert "<th>Opp</th>" in card and "Proj Own" in card
    assert "@ BBB" in card  # AAA is the away team in the slate game
    assert "vs CCC" in card  # DDD is the home team
    assert "4.5%" in card  # only the QB has a live ownership read


def test_lineup_card_ownership_and_opponent_fall_back_to_placeholder_when_unknown():
    weekly_output, _ = _weekly_output_with_three_lineups()
    html = render_dashboard_html(weekly_output, [])
    card = html.split('class="lineup-card"')[1].split("</table>")[0]
    assert "<td>--</td>" in card  # no opponent supplied
    assert "%" not in card  # no fabricated ownership number
