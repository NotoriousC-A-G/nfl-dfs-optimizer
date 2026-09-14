import pandas as pd
import pytest

from nfl_dfs.ceiling.signals import CeilingSignal
from nfl_dfs.composition.player_detail import (
    SCHEME_SPLIT_POSITIONS,
    build_gsis_to_pff_id_map,
    build_player_detail_record,
    slate_window_label,
)
from nfl_dfs.correlation.stack_profile import StackProfile
from nfl_dfs.ingestion.qb_rushing_profile import TrailingQbRushingProfile
from nfl_dfs.ingestion.receiving_profile import TrailingReceivingProfile
from nfl_dfs.game_environment.score import ComponentScore, GameEnvironmentScore
from nfl_dfs.ingestion.pff import PffFacetGrades, PffGradeRow, TeamCoverageTendency
from nfl_dfs.ingestion.rotogrinders_injuries import InjuryReportEntry
from nfl_dfs.ingestion.snap_share import PlayerSnapShare
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR, PlayerRoleShare, RoleShareResult
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch
from nfl_dfs.ownership.leverage import LeverageAssessment
from nfl_dfs.projection.blend import PlayerProjection

SEASON = 2026
WEEK = 5


# --------------------------------------------------------------------------------------------
# Fixture builders -- one "real" player per position, cross-module-consistent gsis_id/pff_id
# --------------------------------------------------------------------------------------------


def _identity(
    canonical_id: str,
    name: str,
    position: str,
    team: str,
    *,
    gsis_id: str | None,
    pff_native_id: str | None = None,
    pff_method: MatchMethod = MatchMethod.CROSSWALK,
    rotogrinders_native_id: str | None = None,
    rotogrinders_method: MatchMethod = MatchMethod.CROSSWALK,
) -> PlayerIdentity:
    sources = {}
    if pff_native_id is not None:
        sources["pff"] = SourceMatch(native_id=pff_native_id, method=pff_method)
    if rotogrinders_native_id is not None:
        sources["rotogrinders"] = SourceMatch(native_id=rotogrinders_native_id, method=rotogrinders_method)
    return PlayerIdentity(
        canonical_id=canonical_id,
        display_name=name,
        position=position,
        team=team,
        nflverse_gsis_id=gsis_id,
        sources=sources,
    )


def _role_share(player_id: str, name: str, role: str, team: str, *, blended: float, tier: str | None) -> PlayerRoleShare:
    return PlayerRoleShare(
        player_id=player_id,
        player_name=name,
        role=role,
        weeks_played=4,
        trailing_volume=40,
        trailing_team_volume=80,
        trailing_share=0.50,
        shrinkage_weight=0.4,
        role_share_blended=blended,
        role_tier=tier,
        prior_used="league_average",
    )


def _role_share_result(team: str, role: str, candidates: list[PlayerRoleShare], identified: PlayerRoleShare | None) -> RoleShareResult:
    return RoleShareResult(
        season=SEASON,
        week=WEEK,
        team=team,
        role=role,
        candidates=candidates,
        identified=identified,
        gate_passed=identified is not None,
        gate_reason="gate passed" if identified is not None else "gate failed",
    )


def _snap_share(player_id: str, name: str, team: str, position: str) -> PlayerSnapShare:
    return PlayerSnapShare(
        player_id=player_id,
        pfr_player_id="SomePl00",
        player_name=name,
        team=team,
        position=position,
        season=SEASON,
        week=WEEK,
        weeks_played=4,
        offense_pct_last_week=0.85,
        defense_pct_last_week=0.0,
        st_pct_last_week=0.05,
        offense_pct_trailing=0.80,
        defense_pct_trailing=0.0,
        st_pct_trailing=0.06,
    )


def _red_zone_trailing_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _ges(team: str, *, is_available: bool = True, composite: float | None = 62.5) -> GameEnvironmentScore:
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
        weather=placeholder,
    )


def _projection(
    canonical_id: str, *, salary: int | None, position: str = "WR", team: str = "DET", blended_projection: float | None = None
) -> PlayerProjection:
    return PlayerProjection(
        canonical_id=canonical_id,
        display_name="ignored -- salary join is by canonical_id, not name",
        position=position,
        team=team,
        salary=salary,
        blended_projection=blended_projection,
        source_count=0,
    )


def _receiving_scheme_grades(rows: dict[str, dict]) -> PffFacetGrades:
    by_id = {
        pid: PffGradeRow(
            native_id=pid,
            name=grades.pop("_name", "Some Player"),
            team=grades.pop("_team", None),
            position=grades.pop("_position", "WR"),
            jersey_number=None,
            player_game_count=4,
            grades=grades,
        )
        for pid, grades in rows.items()
    }
    return PffFacetGrades(
        population="cumulative_through_last_completed_week",
        weeks_requested="1,2,3,4",
        seasons_requested=str(SEASON),
        by_player_id=by_id,
    )


# --------------------------------------------------------------------------------------------
# 1. Fully-populated record -- a real cross-module join, WR case
# --------------------------------------------------------------------------------------------


def test_fully_populated_record_joins_every_source_for_the_right_player():
    wr_gsis = "00-0038557"  # a WR
    other_wr_gsis = "00-0099999"  # a teammate, present in every collection to prove no bleed-over
    wr_pff_id = "12345"

    identity = _identity(
        canonical_id=wr_gsis, name="Star Wideout", position="WR", team="DET", gsis_id=wr_gsis, pff_native_id=wr_pff_id
    )

    wr_candidate = _role_share(wr_gsis, "Star Wideout", ROLE_WR, "DET", blended=0.28, tier=None)
    other_candidate = _role_share(other_wr_gsis, "Other Guy", ROLE_WR, "DET", blended=0.15, tier=None)
    wr_role_result = _role_share_result("DET", ROLE_WR, [wr_candidate, other_candidate], identified=wr_candidate)

    snap_shares = {
        wr_gsis: _snap_share(wr_gsis, "Star Wideout", "DET", "WR"),
        other_wr_gsis: _snap_share(other_wr_gsis, "Other Guy", "DET", "WR"),
    }

    red_zone = _red_zone_trailing_frame(
        [
            {
                "team": "DET",
                "player_id": wr_gsis,
                "player_name": "Star Wideout",
                "role": ROLE_WR,
                "rz_trailing_volume": 6,
                "rz_trailing_team_volume": 20,
                "rz_trailing_share": 0.30,
            },
            {
                "team": "DET",
                "player_id": other_wr_gsis,
                "player_name": "Other Guy",
                "role": ROLE_WR,
                "rz_trailing_volume": 2,
                "rz_trailing_team_volume": 20,
                "rz_trailing_share": 0.10,
            },
        ]
    )

    receiving_scheme = _receiving_scheme_grades(
        {
            wr_pff_id: {
                "_name": "Star Wideout",
                "_team": "DET",
                "_position": "WR",
                "man_targets": 20.0,
                "zone_targets": 25.0,
                "man_yprr": 2.5,
                "zone_yprr": 1.9,
            },
            "99999": {  # some other player's PFF row -- must not leak into wr's grades
                "_name": "Other Guy",
                "_team": "DET",
                "_position": "WR",
                "man_targets": 3.0,
                "zone_targets": 4.0,
            },
        }
    )

    coverage_tendency = {
        "MIN": TeamCoverageTendency(
            team="MIN", man_snaps=180, zone_snaps=220, man_rate=180 / 400, zone_rate=220 / 400, defender_count=11
        )
    }
    ges_by_team = {"DET": _ges("DET", composite=71.0)}

    projections_by_canonical_id = {
        wr_gsis: _projection(wr_gsis, salary=6500, position="WR", team="DET"),
        other_wr_gsis: _projection(other_wr_gsis, salary=4200, position="WR", team="DET"),
    }

    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="DET",
        position="WR",
        opponent_team_this_week="MIN",
        role_share_results={("DET", ROLE_WR): wr_role_result},
        snap_shares_by_player=snap_shares,
        red_zone_trailing=red_zone,
        receiving_scheme_grades=receiving_scheme,
        team_coverage_tendency=coverage_tendency,
        game_environment_by_team=ges_by_team,
        projections_by_canonical_id=projections_by_canonical_id,
        target_share_by_week_by_gsis_id={
            wr_gsis: [(1, 0.5), (2, 0.0)],
            other_wr_gsis: [(1, 0.1), (2, 0.2)],
        },
    )

    # salary -- joined via canonical_id, the right player's row, not the teammate's.
    assert record.salary == 6500
    assert record.salary_reason is None

    # role share -- this player's own row, correctly picked out of the candidate list, and
    # correctly identified as the team's gate leader (the OTHER candidate is not).
    assert record.usage.role_share.role_share is wr_candidate
    assert record.usage.role_share.role_share.role_share_blended == pytest.approx(0.28)
    assert record.usage.role_share.is_team_identified_leader is True
    assert record.usage.role_share.reason is None

    # snap share -- the right player's row, not the teammate's.
    assert record.usage.snap_share.snap_share is not None
    assert record.usage.snap_share.snap_share.player_id == wr_gsis
    assert record.usage.snap_share.snap_share.offense_pct_trailing == pytest.approx(0.80)

    # red zone -- WR-role targets only, from the right row.
    assert record.usage.red_zone.targets_trailing == 6
    assert record.usage.red_zone.target_share_trailing == pytest.approx(0.30)
    assert record.usage.red_zone.carries_trailing is None  # no RB-role row for a WR
    assert record.usage.red_zone.reason is None

    # red zone weekly sequence (ADR-0029 addendum) -- the right player's own list, not the
    # teammate's, and carry_share_by_week stays empty since no RB-role dict was supplied.
    assert record.usage.red_zone.target_share_by_week == [(1, 0.5), (2, 0.0)]
    assert record.usage.red_zone.carry_share_by_week == []

    # own scheme splits -- joined via identity.sources["pff"], the right PFF row, not the
    # teammate's "99999" row.
    assert record.own_scheme_splits.applicable is True
    assert record.own_scheme_splits.pff_native_id == wr_pff_id
    assert record.own_scheme_splits.grades["man_targets"] == 20.0
    assert record.own_scheme_splits.grades["zone_targets"] == 25.0
    assert record.own_scheme_splits.reason is None

    # matchup -- opponent's coverage tendency, own/opponent unit grade still explicit placeholders.
    assert record.matchup_this_week.opponent_team == "MIN"
    assert record.matchup_this_week.coverage_tendency_faced is not None
    assert record.matchup_this_week.coverage_tendency_faced.man_rate == pytest.approx(0.45)
    assert record.matchup_this_week.own_unit_grade is None
    assert record.matchup_this_week.opponent_unit_grade is None
    assert "MatchupContext" in record.matchup_this_week.matchup_grade_note

    # game environment
    assert record.game_environment is not None
    assert record.game_environment.composite_score == pytest.approx(71.0)
    assert record.game_environment_reason is None


# --------------------------------------------------------------------------------------------
# 2. Partial-data cases -- graceful nullability with DISTINGUISHABLE reasons
# --------------------------------------------------------------------------------------------


def test_missing_role_share_result_is_distinguishable_from_no_trailing_volume():
    identity = _identity("00-1", "Some RB", "RB", "GB", gsis_id="00-1")

    # Case A: no RoleShareResult supplied at all for this team/role.
    record_a = build_player_detail_record(identity, SEASON, WEEK, team="GB", position="RB", opponent_team_this_week="CHI")
    assert record_a.usage.role_share.role_share is None
    assert "no" in record_a.usage.role_share.reason and "RoleShareResult supplied" in record_a.usage.role_share.reason

    # Case B: a RoleShareResult IS supplied, but this player has no trailing volume in it.
    other_candidate = _role_share("00-2", "Someone Else", ROLE_RB, "GB", blended=0.55, tier="bell_cow")
    rb_result = _role_share_result("GB", ROLE_RB, [other_candidate], identified=other_candidate)
    record_b = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="RB",
        opponent_team_this_week="CHI",
        role_share_results={("GB", ROLE_RB): rb_result},
    )
    assert record_b.usage.role_share.role_share is None
    assert "no trailing" in record_b.usage.role_share.reason

    # The two reasons must be distinguishable from each other, not the same generic string.
    assert record_a.usage.role_share.reason != record_b.usage.role_share.reason


def test_role_share_not_applicable_for_qb_is_distinguishable_from_no_data():
    identity = _identity("00-9", "A Quarterback", "QB", "KC", gsis_id="00-9")
    record = build_player_detail_record(identity, SEASON, WEEK, team="KC", position="QB", opponent_team_this_week="DEN")
    assert record.usage.role_share.role_share is None
    assert "not applicable" in record.usage.role_share.reason


def test_missing_snap_share_has_its_own_reason():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        snap_shares_by_player={"00-2": _snap_share("00-2", "Someone Else", "GB", "WR")},
    )
    assert record.usage.snap_share.snap_share is None
    assert "snap-share" in record.usage.snap_share.reason


def test_missing_game_environment_score_has_its_own_reason():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        game_environment_by_team={"CHI": _ges("CHI")},  # only the OPPONENT's GES supplied, not GB's
    )
    assert record.game_environment is None
    assert "GB" in record.game_environment_reason


def test_game_environment_unavailable_object_passes_through_without_a_composer_reason():
    """An unavailable GameEnvironmentScore is a real, already-self-documented outcome (its own
    is_available/notes) -- the composer must not re-explain it with a second reason string."""
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        game_environment_by_team={"GB": _ges("GB", is_available=False, composite=None)},
    )
    assert record.game_environment is not None
    assert record.game_environment.is_available is False
    assert record.game_environment_reason is None


def test_no_gsis_id_nulls_out_every_nflverse_joined_section_with_a_shared_reason():
    identity = _identity("local_abc123", "Undrafted Rookie", "WR", "GB", gsis_id=None)
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        role_share_results={("GB", ROLE_WR): _role_share_result("GB", ROLE_WR, [], identified=None)},
        snap_shares_by_player={"00-2": _snap_share("00-2", "Someone Else", "GB", "WR")},
        red_zone_trailing=_red_zone_trailing_frame(
            [{"team": "GB", "player_id": "00-2", "player_name": "x", "role": ROLE_WR, "rz_trailing_volume": 1, "rz_trailing_team_volume": 10, "rz_trailing_share": 0.1}]
        ),
    )
    assert record.usage.role_share.role_share is None
    assert record.usage.snap_share.snap_share is None
    assert record.usage.red_zone.reason is not None
    assert "gsis_id" in record.usage.role_share.reason
    assert "gsis_id" in record.usage.snap_share.reason
    assert "gsis_id" in record.usage.red_zone.reason
    # own_scheme_splits does NOT depend on gsis_id directly (it can resolve via identity.sources
    # ["pff"] alone) -- confirm it fails via its OWN distinct reason (no PFF id resolvable at
    # all), not the shared "_NO_GSIS_ID_REASON" every gsis_id-joined section above uses.
    assert record.own_scheme_splits.reason != record.usage.role_share.reason
    assert "no PFF player_id resolved" in record.own_scheme_splits.reason


def test_red_zone_weekly_shares_populate_independently_of_red_zone_trailing_frame():
    # ADR-0029 addendum: carry_share_by_week/target_share_by_week come from a separate, pbp-
    # derived lookup (ceiling.signals.trailing_red_zone_share_by_week) than red_zone_trailing (the
    # pre-aggregated summary DataFrame) -- so the weekly sequence must still populate even when
    # red_zone_trailing itself is entirely absent (the single-number fields correctly stay None).
    gsis_id = "00-0038557"
    identity = _identity(canonical_id=gsis_id, name="Change-of-Pace Back", position="RB", team="GB", gsis_id=gsis_id)
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="RB",
        opponent_team_this_week="CHI",
        carry_share_by_week_by_gsis_id={gsis_id: [(1, 1.0), (2, 0.0), (3, 0.5)]},
    )
    assert record.usage.red_zone.carries_trailing is None
    assert record.usage.red_zone.reason is not None
    assert record.usage.red_zone.carry_share_by_week == [(1, 1.0), (2, 0.0), (3, 0.5)]
    assert record.usage.red_zone.target_share_by_week == []


def test_own_scheme_splits_not_applicable_for_qb():
    identity = _identity("00-9", "A Quarterback", "QB", "KC", gsis_id="00-9")
    record = build_player_detail_record(identity, SEASON, WEEK, team="KC", position="QB", opponent_team_this_week="DEN")
    assert record.own_scheme_splits.applicable is False
    assert record.own_scheme_splits.grades == {}
    assert "not applicable" in record.own_scheme_splits.reason


def test_own_scheme_splits_applicable_but_no_pff_id_resolved():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")  # no pff_native_id, no crosswalk map
    record = build_player_detail_record(identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI")
    assert record.own_scheme_splits.applicable is True
    assert record.own_scheme_splits.grades == {}
    assert "no PFF player_id resolved" in record.own_scheme_splits.reason


def test_own_scheme_splits_falls_back_to_crosswalk_when_identity_sources_missing():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")  # sources["pff"] NOT set
    receiving_scheme = _receiving_scheme_grades(
        {"555": {"_name": "Some WR", "_team": "GB", "_position": "WR", "man_targets": 9.0, "zone_targets": 11.0}}
    )
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        receiving_scheme_grades=receiving_scheme,
        gsis_to_pff_id={"00-1": "555"},
    )
    assert record.own_scheme_splits.pff_native_id == "555"
    assert record.own_scheme_splits.grades["man_targets"] == 9.0
    assert record.own_scheme_splits.reason is None


def test_own_scheme_splits_pff_id_resolved_but_unmatched_in_facet_pull():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1", pff_native_id="777")
    receiving_scheme = _receiving_scheme_grades({"other": {"_name": "x", "_team": "GB", "_position": "WR"}})
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        receiving_scheme_grades=receiving_scheme,
    )
    assert record.own_scheme_splits.applicable is True
    assert record.own_scheme_splits.pff_native_id == "777"
    assert record.own_scheme_splits.grades == {}
    assert "unmatched" in record.own_scheme_splits.reason


def test_bye_week_opponent_none_gives_distinct_matchup_reason():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week=None)
    assert record.matchup_this_week.opponent_team is None
    assert record.matchup_this_week.coverage_tendency_faced is None
    assert "bye" in record.matchup_this_week.coverage_tendency_reason


def test_missing_player_projection_gives_null_salary_with_a_real_reason_not_a_crash():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")

    # Case A: no projections_by_canonical_id lookup supplied at all.
    record_a = build_player_detail_record(identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI")
    assert record_a.salary is None
    assert record_a.salary_reason is not None
    assert "no DK salary found for this player" in record_a.salary_reason

    # Case B: a lookup IS supplied, but has no entry for this player's canonical_id (only a
    # teammate's).
    record_b = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        projections_by_canonical_id={"00-2": _projection("00-2", salary=5000, team="GB")},
    )
    assert record_b.salary is None
    assert "no DK salary found for this player" in record_b.salary_reason

    # Case C: the matched PlayerProjection itself has an unresolved salary (e.g. no DK match on
    # that vendor row) -- still a real reason, not a crash, not a silently-wrong number.
    record_c = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        projections_by_canonical_id={"00-1": _projection("00-1", salary=None, team="GB")},
    )
    assert record_c.salary is None
    assert "no DK salary found for this player" in record_c.salary_reason


def _leverage_assessment(native_id: str, *, is_chalk: bool = False, is_leverage: bool = False) -> LeverageAssessment:
    return LeverageAssessment(
        native_id=native_id,
        name="Some Player",
        position="WR",
        team="GB",
        salary=7000,
        salary_decile=1,
        projected_ownership=5.0,
        ownership_percentile=0.2,
        baseline_ownership=15.0,
        ownership_vs_baseline=-10.0,
        is_chalk=is_chalk,
        is_leverage=is_leverage,
        note="",
    )


def test_ownership_joins_via_rotogrinders_native_id():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1", rotogrinders_native_id="rg-1")
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        leverage_by_native_id={"rg-1": _leverage_assessment("rg-1", is_leverage=True)},
    )
    assert record.ownership is not None
    assert record.ownership.native_id == "rg-1"
    assert record.ownership.is_leverage is True
    assert record.ownership_reason is None


def test_ownership_none_with_reason_when_no_rotogrinders_source_match():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        leverage_by_native_id={"rg-1": _leverage_assessment("rg-1")},
    )
    assert record.ownership is None
    assert "no LeverageAssessment found" in record.ownership_reason


def test_ownership_none_with_reason_when_lookup_has_no_matching_row():
    # Matched on rotogrinders, but the caller's leverage pool has no row for this id (e.g. a
    # non-core position, or the caller didn't run build_leverage_assessments this week).
    identity = _identity("00-1", "Some K", "K", "GB", gsis_id="00-1", rotogrinders_native_id="rg-1")
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="K",
        opponent_team_this_week="CHI",
        leverage_by_native_id={"rg-2": _leverage_assessment("rg-2")},
    )
    assert record.ownership is None
    assert "no LeverageAssessment found" in record.ownership_reason


def test_ownership_none_with_reason_when_rotogrinders_match_is_unresolved():
    identity = _identity(
        "00-1", "Some WR", "WR", "GB", gsis_id="00-1",
        rotogrinders_native_id="rg-1", rotogrinders_method=MatchMethod.UNRESOLVED,
    )
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        leverage_by_native_id={"rg-1": _leverage_assessment("rg-1")},
    )
    assert record.ownership is None
    assert "no LeverageAssessment found" in record.ownership_reason


def _grade_facet(rows: list[PffGradeRow]) -> PffFacetGrades:
    return PffFacetGrades(
        population="cumulative_through_last_completed_week",
        weeks_requested="1,2,3,4",
        seasons_requested="2026",
        by_player_id={r.native_id: r for r in rows},
    )


def _matchup_facets_for_rb_test() -> "MatchupFacetInputs":
    from nfl_dfs.matchup.context import MatchupFacetInputs

    empty = _grade_facet([])
    run_blocking = _grade_facet([
        PffGradeRow(native_id="gb_ol", name="gb_ol", team="GB", position="T", jersey_number=None,
                    player_game_count=4, grades={"grades_run_block": 88.0}),
        PffGradeRow(native_id="chi_ol", name="chi_ol", team="CHI", position="T", jersey_number=None,
                    player_game_count=4, grades={"grades_run_block": 65.0}),
    ])
    run_defense = _grade_facet([
        PffGradeRow(native_id="gb_dl", name="gb_dl", team="GB", position="DI", jersey_number=None,
                    player_game_count=4, grades={"grades_run_defense": 70.0}),
        PffGradeRow(native_id="chi_dl", name="chi_dl", team="CHI", position="DI", jersey_number=None,
                    player_game_count=4, grades={"grades_run_defense": 40.0}),
    ])
    return MatchupFacetInputs(
        run_blocking=run_blocking, run_defense=run_defense, pass_blocking=empty,
        pass_rush=empty, coverage_scheme=empty, receiving_scheme=empty,
    )


def test_matchup_facets_populates_own_and_opponent_unit_grade_for_rb():
    identity = _identity("00-rb1", "Some RB", "RB", "GB", gsis_id="00-rb1")
    facets = _matchup_facets_for_rb_test()
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="RB", opponent_team_this_week="CHI",
        matchup_facets=facets,
    )
    assert record.matchup_this_week.own_unit_grade is not None
    assert record.matchup_this_week.own_unit_grade.grades["grades_run_block"] == pytest.approx(88.0)
    assert record.matchup_this_week.opponent_unit_grade is not None
    assert record.matchup_this_week.opponent_unit_grade.grades["grades_run_defense"] == pytest.approx(40.0)
    assert record.matchup_this_week.unit_grade_reason is None


def test_matchup_facets_not_supplied_leaves_unit_grades_none_with_reason():
    identity = _identity("00-rb1", "Some RB", "RB", "GB", gsis_id="00-rb1")
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="RB", opponent_team_this_week="CHI",
    )
    assert record.matchup_this_week.own_unit_grade is None
    assert record.matchup_this_week.opponent_unit_grade is None
    assert "matchup_facets" in record.matchup_this_week.unit_grade_reason
    assert any("matchup_facets" in n for n in record.notes)


def test_matchup_facets_wr_has_no_own_unit_grade_but_gets_opponent_coverage():
    identity = _identity("00-wr1", "Some WR", "WR", "GB", gsis_id="00-wr1")
    from nfl_dfs.matchup.context import MatchupFacetInputs

    empty = _grade_facet([])
    coverage_scheme = _grade_facet([
        PffGradeRow(native_id="chi_cb", name="chi_cb", team="CHI", position="CB", jersey_number=None,
                    player_game_count=4, grades={
                        "man_grades_coverage_defense": 55.0, "zone_grades_coverage_defense": 58.0,
                        "man_snap_counts_coverage": 30, "zone_snap_counts_coverage": 30,
                    }),
    ])
    facets = MatchupFacetInputs(
        run_blocking=empty, run_defense=empty, pass_blocking=empty, pass_rush=empty,
        coverage_scheme=coverage_scheme, receiving_scheme=empty,
    )
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI",
        matchup_facets=facets,
    )
    assert record.matchup_this_week.own_unit_grade is None
    assert record.matchup_this_week.opponent_unit_grade is not None
    assert record.matchup_this_week.opponent_unit_grade.grades["man_grades_coverage_defense"] == pytest.approx(55.0)
    assert "not applicable for WR/TE" in record.matchup_this_week.unit_grade_reason


def test_opponent_supplied_but_no_coverage_tendency_computed():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI")
    assert record.matchup_this_week.opponent_team == "CHI"
    assert record.matchup_this_week.coverage_tendency_faced is None
    assert "CHI" in record.matchup_this_week.coverage_tendency_reason


# --------------------------------------------------------------------------------------------
# 3. build_gsis_to_pff_id_map -- the crosswalk-reversal join helper
# --------------------------------------------------------------------------------------------


def test_build_gsis_to_pff_id_map_drops_rows_missing_either_id():
    crosswalk = pd.DataFrame(
        [
            {"gsis_id": "00-1", "pff_id": "111"},
            {"gsis_id": "00-2", "pff_id": None},
            {"gsis_id": None, "pff_id": "333"},
        ]
    )
    mapping = build_gsis_to_pff_id_map(crosswalk)
    assert mapping == {"00-1": "111"}


def test_scheme_split_positions_excludes_qb_and_dst():
    assert "QB" not in SCHEME_SPLIT_POSITIONS
    assert "DST" not in SCHEME_SPLIT_POSITIONS
    assert {"WR", "TE", "RB"} == SCHEME_SPLIT_POSITIONS


# --------------------------------------------------------------------------------------------
# ADR-0027: projection, value, StackContext, injury, slate_window, implied_total
# --------------------------------------------------------------------------------------------


def test_projection_joins_via_same_canonical_id_as_salary():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        projections_by_canonical_id={"00-1": _projection("00-1", salary=6500, blended_projection=14.2)},
    )
    assert record.projection == pytest.approx(14.2)
    assert record.projection_reason is None
    assert record.salary == 6500
    assert record.value == pytest.approx(14.2 / 6.5)


def test_projection_none_with_reason_when_no_match():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI")
    assert record.projection is None
    assert "no blended projection found" in record.projection_reason
    assert record.value is None


def test_value_property_none_when_salary_is_zero_or_missing():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        projections_by_canonical_id={"00-1": _projection("00-1", salary=None, blended_projection=14.2)},
    )
    assert record.salary is None
    assert record.value is None


def _stack_profile(
    home_team: str,
    away_team: str,
    *,
    primary_stack_candidates: list[PlayerRoleShare] | None = None,
    bring_back_candidates: list[PlayerRoleShare] | None = None,
    bring_back_status: str = "populated",
) -> StackProfile:
    return StackProfile(
        season=SEASON,
        week=WEEK,
        home_team=home_team,
        away_team=away_team,
        spread=-3.5,
        single_team_viability_home=62.0,
        single_team_viability_away=48.0,
        game_stack_viability=55.0,
        bring_back_status=bring_back_status,
        primary_stack_candidates=primary_stack_candidates,
        bring_back_candidates=bring_back_candidates,
        pivot_to="GB implied 27.5, leads targets.",
    )


def test_stack_context_home_team_primary_candidate_gets_rank():
    identity = _identity("00-1", "Home WR1", "WR", "GB", gsis_id="gsis-home-1")
    candidate = _role_share("gsis-home-1", "Home WR1", ROLE_WR, "GB", blended=0.30, tier=None)
    profile = _stack_profile("GB", "CHI", primary_stack_candidates=[candidate])
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI", stack_profiles=[profile]
    )
    assert record.stack_context is not None
    assert record.stack_context.home_team == "GB"
    assert record.stack_context.home_spread == pytest.approx(-3.5)
    assert record.stack_context.single_team_viability == pytest.approx(62.0)
    assert record.stack_context.is_primary_stack_candidate is True
    assert record.stack_context.primary_stack_rank == 1
    assert record.stack_context.is_bring_back_candidate is False
    assert record.stack_context_reason is None


def test_stack_context_away_team_reads_bring_back_candidates_and_away_viability():
    identity = _identity("00-2", "Away WR1", "WR", "CHI", gsis_id="gsis-away-1")
    candidate = _role_share("gsis-away-1", "Away WR1", ROLE_WR, "CHI", blended=0.28, tier=None)
    profile = _stack_profile("GB", "CHI", bring_back_candidates=[candidate])
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="CHI", position="WR", opponent_team_this_week="GB", stack_profiles=[profile]
    )
    assert record.stack_context is not None
    assert record.stack_context.single_team_viability == pytest.approx(48.0)
    assert record.stack_context.is_bring_back_candidate is True
    assert record.stack_context.is_primary_stack_candidate is False
    assert record.stack_context.primary_stack_rank is None


def test_stack_context_none_with_reason_when_no_matching_profile():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    profile = _stack_profile("NYJ", "NE")  # different game, doesn't include GB
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI", stack_profiles=[profile]
    )
    assert record.stack_context is None
    assert "no StackProfile found" in record.stack_context_reason


def test_injury_distinguishes_no_data_from_healthy_from_a_real_entry():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")

    # No injury data supplied at all.
    record_a = build_player_detail_record(identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI")
    assert record_a.injury is None
    assert "no injury report data supplied" in record_a.injury_reason

    # Injury data supplied, this player not on it -- real, positive "healthy" information.
    record_b = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI", injury_by_canonical_id={}
    )
    assert record_b.injury is None
    assert "presumed healthy" in record_b.injury_reason

    # A real injury report row.
    entry = InjuryReportEntry(
        rotogrinders_player_id="999", name="Some WR", team="GBP", position="WR", status="Q", body_part="Ankle", impact_rating=3
    )
    record_c = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        injury_by_canonical_id={"00-1": entry},
    )
    assert record_c.injury is not None
    assert record_c.injury.status == "Q"
    assert record_c.injury.body_part == "Ankle"
    assert record_c.injury.impact_rating == 3
    assert record_c.injury_reason is None


def test_slate_window_and_implied_total_join_by_team():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        kickoff_utc_by_team={"GB": "2026-09-13T17:00:00Z"},  # 1:00pm ET Sunday
        implied_total_by_team={"GB": 27.5},
    )
    assert record.slate_window == "early"
    assert record.slate_window_reason is None
    assert record.implied_total == pytest.approx(27.5)
    assert record.implied_total_reason is None


def test_slate_window_and_implied_total_none_with_reason_when_team_missing():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity,
        SEASON,
        WEEK,
        team="GB",
        position="WR",
        opponent_team_this_week="CHI",
        kickoff_utc_by_team={"CHI": "2026-09-13T17:00:00Z"},
        implied_total_by_team={"CHI": 24.0},
    )
    assert record.slate_window is None
    assert "no kickoff time known" in record.slate_window_reason
    assert record.implied_total is None
    assert "no implied point total" in record.implied_total_reason


@pytest.mark.parametrize(
    "kickoff_utc,expected",
    [
        ("2026-09-13T17:00:00Z", "early"),  # Sunday 1:00pm ET
        ("2026-09-13T20:25:00Z", "late"),  # Sunday 4:25pm ET
        ("2026-09-14T00:20:00Z", "snf"),  # Sunday 8:20pm ET (00:20 UTC Monday)
        ("2026-09-15T00:15:00Z", "mnf"),  # Monday 8:15pm ET (00:15 UTC Tuesday)
        ("2026-09-11T00:15:00Z", "tnf"),  # Thursday 8:15pm ET (00:15 UTC Friday)
        ("2026-09-19T17:00:00Z", "other"),  # Saturday
    ],
)
def test_slate_window_label_buckets_real_kickoff_times(kickoff_utc, expected):
    assert slate_window_label(kickoff_utc) == expected


# --------------------------------------------------------------------------------------------
# ADR-0028: ceiling_multiplier / ceiling_projection
# --------------------------------------------------------------------------------------------


def _ceiling_signal(shrunk_z_score: float | None, player_id: str = "00-1") -> CeilingSignal:
    return CeilingSignal(
        player_id=player_id, player_name="Some RB", team="GB", sample_size=5,
        raw_value=0.5, z_score=shrunk_z_score, shrinkage_weight=0.5, shrunk_z_score=shrunk_z_score,
    )


def test_ceiling_multiplier_joins_via_gsis_id_for_rb():
    identity = _identity("00-1", "Some RB", "RB", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="RB", opponent_team_this_week="CHI",
        ceiling_signals_by_gsis_id={"00-1": _ceiling_signal(1.0)},
        projections_by_canonical_id={"00-1": _projection("00-1", salary=6000, blended_projection=15.0)},
    )
    assert record.ceiling_multiplier is not None
    assert record.ceiling_multiplier > 1.0
    assert record.ceiling_multiplier_reason is None
    assert record.ceiling_projection == pytest.approx(15.0 * record.ceiling_multiplier)


def test_ceiling_multiplier_not_applicable_for_te():
    identity = _identity("00-1", "Some TE", "TE", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="TE", opponent_team_this_week="CHI",
        ceiling_signals_by_gsis_id={"00-1": _ceiling_signal(1.0)},
    )
    assert record.ceiling_multiplier is None
    assert "only calibrated for RB/WR" in record.ceiling_multiplier_reason


def test_ceiling_multiplier_none_with_reason_when_no_pool_supplied():
    identity = _identity("00-1", "Some RB", "RB", "GB", gsis_id="00-1")
    record = build_player_detail_record(identity, SEASON, WEEK, team="GB", position="RB", opponent_team_this_week="CHI")
    assert record.ceiling_multiplier is None
    assert "no Component A ceiling signal" in record.ceiling_multiplier_reason


def test_ceiling_multiplier_none_with_reason_when_signal_itself_ungated():
    # A real CeilingSignal exists for this player but its own shrunk_z_score is None (didn't clear
    # MIN_TRAILING_WEEKS) -- still a real, distinguishable reason, not a fabricated neutral value.
    identity = _identity("00-1", "Some RB", "RB", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="RB", opponent_team_this_week="CHI",
        ceiling_signals_by_gsis_id={"00-1": _ceiling_signal(None)},
    )
    assert record.ceiling_multiplier is None
    assert "no Component A ceiling signal" in record.ceiling_multiplier_reason


def test_ceiling_projection_none_when_either_input_missing():
    identity = _identity("00-1", "Some RB", "RB", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="RB", opponent_team_this_week="CHI",
        ceiling_signals_by_gsis_id={"00-1": _ceiling_signal(1.0)},
    )
    assert record.ceiling_multiplier is not None  # real multiplier
    assert record.projection is None  # no projections_by_canonical_id supplied
    assert record.ceiling_projection is None


# --------------------------------------------------------------------------------------------
# ADR-0029: receiving_profile (descriptive, not a ceiling signal)
# --------------------------------------------------------------------------------------------


def _profile(player_id: str = "00-1") -> TrailingReceivingProfile:
    return TrailingReceivingProfile(
        player_id=player_id, player_name="Some WR", team="GB",
        trailing_targets=25, trailing_receptions=18, trailing_air_yards=210,
        trailing_adot=8.4, trailing_yac_per_reception=4.1,
    )


def test_receiving_profile_joins_via_gsis_id_for_pass_catchers():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI",
        receiving_profile_by_gsis_id={"00-1": _profile()},
    )
    assert record.receiving_profile is not None
    assert record.receiving_profile.trailing_targets == 25
    assert record.receiving_profile.trailing_adot == pytest.approx(8.4)
    assert record.receiving_profile_reason is None


def test_receiving_profile_not_applicable_for_qb():
    identity = _identity("00-1", "Some QB", "QB", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="QB", opponent_team_this_week="CHI",
        receiving_profile_by_gsis_id={"00-1": _profile()},
    )
    assert record.receiving_profile is None
    assert "only applies to pass-catchers" in record.receiving_profile_reason


def test_receiving_profile_none_with_reason_when_no_pool_supplied():
    identity = _identity("00-1", "Some RB", "RB", "GB", gsis_id="00-1")
    record = build_player_detail_record(identity, SEASON, WEEK, team="GB", position="RB", opponent_team_this_week="CHI")
    assert record.receiving_profile is None
    assert "no trailing receiving-opportunity profile" in record.receiving_profile_reason


# --------------------------------------------------------------------------------------------
# ADR-0030: qb_rushing_profile (descriptive, not a ceiling signal)
# --------------------------------------------------------------------------------------------


def _qb_rushing_profile(player_id: str = "00-1") -> TrailingQbRushingProfile:
    return TrailingQbRushingProfile(
        player_id=player_id, player_name="Some QB", team="GB",
        trailing_rush_attempts=20, trailing_designed_runs=8, trailing_scrambles=12,
        designed_run_rate=0.4, trailing_rushing_yards=95, trailing_rush_tds=2,
        trailing_redzone_rush_attempts=4, trailing_goalline_rush_attempts=2,
    )


def test_qb_rushing_profile_joins_via_gsis_id_for_qb():
    identity = _identity("00-1", "Some QB", "QB", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="QB", opponent_team_this_week="CHI",
        qb_rushing_profile_by_gsis_id={"00-1": _qb_rushing_profile()},
    )
    assert record.qb_rushing_profile is not None
    assert record.qb_rushing_profile.trailing_rush_attempts == 20
    assert record.qb_rushing_profile.designed_run_rate == pytest.approx(0.4)
    assert record.qb_rushing_profile_reason is None


def test_qb_rushing_profile_not_applicable_for_wr():
    identity = _identity("00-1", "Some WR", "WR", "GB", gsis_id="00-1")
    record = build_player_detail_record(
        identity, SEASON, WEEK, team="GB", position="WR", opponent_team_this_week="CHI",
        qb_rushing_profile_by_gsis_id={"00-1": _qb_rushing_profile()},
    )
    assert record.qb_rushing_profile is None
    assert "only applies to QB" in record.qb_rushing_profile_reason


def test_qb_rushing_profile_none_with_reason_when_no_pool_supplied():
    identity = _identity("00-1", "Some QB", "QB", "GB", gsis_id="00-1")
    record = build_player_detail_record(identity, SEASON, WEEK, team="GB", position="QB", opponent_team_this_week="CHI")
    assert record.qb_rushing_profile is None
    assert "no trailing QB rushing-opportunity profile" in record.qb_rushing_profile_reason
