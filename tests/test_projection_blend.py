import pytest

from nfl_dfs.ingestion.odds_api import GameOdds
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR, PlayerRoleShare, RoleShareResult
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch
from nfl_dfs.projection.blend import (
    PlayerProjection,
    apply_matchup_context,
    apply_rb_blowout_volume_discount,
    blend_dst_baseline,
    blend_player_projection,
    build_projection_pool,
    extract_dk_avg_points_per_game,
    extract_dk_salary,
    extract_footballguys_points,
    extract_rotogrinders_fpts,
    team_spreads_from_games,
)


def _identity(
    *,
    canonical_id: str = "gsis-1",
    position: str = "WR",
    team: str = "MIN",
    dk_id: str | None = "dk1",
    rg_id: str | None = "rg1",
    fbg_id: str | None = "fbg1",
) -> PlayerIdentity:
    sources = {}
    if dk_id is not None:
        sources["draftkings"] = SourceMatch(native_id=dk_id, method=MatchMethod.NAME_TEAM_POSITION)
    else:
        sources["draftkings"] = SourceMatch(native_id=None, method=MatchMethod.UNRESOLVED)
    sources["rotogrinders"] = (
        SourceMatch(native_id=rg_id, method=MatchMethod.NAME_TEAM_POSITION)
        if rg_id is not None
        else SourceMatch(native_id=None, method=MatchMethod.UNRESOLVED)
    )
    sources["footballguys"] = (
        SourceMatch(native_id=fbg_id, method=MatchMethod.NAME_TEAM_POSITION)
        if fbg_id is not None
        else SourceMatch(native_id=None, method=MatchMethod.UNRESOLVED)
    )
    sources["pff"] = SourceMatch(native_id=None, method=MatchMethod.UNRESOLVED)
    return PlayerIdentity(
        canonical_id=canonical_id,
        display_name="Test Player",
        position=position,
        team=team,
        sources=sources,
    )


# --- blend_player_projection: coverage scenarios -----------------------------------------


def test_full_source_coverage_is_an_equal_weighted_average():
    identity = _identity()
    result = blend_player_projection(
        identity,
        dk_salary={"dk1": 7300},
        rotogrinders_fpts={"rg1": 18.0},
        footballguys_points={"fbg1": 14.0},
    )
    assert result.canonical_id == "gsis-1"
    assert result.position == "WR"
    assert result.team == "MIN"
    assert result.salary == 7300
    assert result.source_count == 2
    assert result.source_values == {"rotogrinders": 18.0, "footballguys": 14.0}
    assert result.blended_projection == 16.0


def test_partial_source_coverage_blends_the_one_available_source_not_dropped():
    identity = _identity()
    # Footballguys resolved an identity match, but that native id has no numeric value this
    # week (e.g. the player wasn't in this pull) -- should behave the same as unresolved.
    result = blend_player_projection(
        identity,
        dk_salary={"dk1": 5000},
        rotogrinders_fpts={"rg1": 9.5},
        footballguys_points={},
    )
    assert result.source_count == 1
    assert result.source_values == {"rotogrinders": 9.5}
    assert result.blended_projection == 9.5
    # Salary/position/team still carried through even though the blend is thin.
    assert result.salary == 5000
    assert result.position == "WR"


def test_zero_source_coverage_keeps_player_with_null_projection_not_dropped():
    identity = _identity(rg_id=None, fbg_id=None)
    result = blend_player_projection(identity, dk_salary={"dk1": 3800}, rotogrinders_fpts={"rg1": 99.0})
    assert result.source_count == 0
    assert result.source_values == {}
    assert result.blended_projection is None
    # The whole point: still a full row for the optimizer's DK pool, salary/position/team intact.
    assert result.salary == 3800
    assert result.position == "WR"
    assert result.team == "MIN"
    assert result.canonical_id == "gsis-1"


def test_no_dk_match_means_no_salary_but_projection_still_blends():
    identity = _identity(dk_id=None)
    result = blend_player_projection(
        identity, dk_salary={}, rotogrinders_fpts={"rg1": 10.0}, footballguys_points={"fbg1": 12.0}
    )
    assert result.salary is None
    assert result.blended_projection == 11.0


# --- DST baseline -----------------------------------------------------------------------


def test_dst_baseline_blends_same_way_as_offense():
    identity = _identity(canonical_id="DST_NEP", position="DST", team="NEP", dk_id="dkdst", rg_id="rgdst", fbg_id="fbgdst")
    result = blend_dst_baseline(
        identity,
        dk_salary={"dkdst": 2600},
        rotogrinders_fpts={"rgdst": 8.0},
        footballguys_points={"fbgdst": 10.0},
    )
    assert result.position == "DST"
    assert result.source_count == 2
    assert result.blended_projection == 9.0
    assert result.salary == 2600


def test_dst_baseline_with_partial_coverage():
    identity = _identity(canonical_id="DST_JAX", position="DST", team="JAX", dk_id="dkdst2", rg_id="rgdst2", fbg_id=None)
    result = blend_dst_baseline(identity, dk_salary={"dkdst2": 2200}, rotogrinders_fpts={"rgdst2": 7.4})
    assert result.source_count == 1
    assert result.blended_projection == 7.4


def test_dst_baseline_rejects_non_dst_identity():
    identity = _identity(position="WR")
    try:
        blend_dst_baseline(identity, dk_salary={})
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- build_projection_pool ----------------------------------------------------------------


def test_build_projection_pool_routes_dst_and_offense_correctly():
    wr = _identity(canonical_id="wr-1", position="WR", team="MIN", dk_id="dk1", rg_id="rg1", fbg_id="fbg1")
    dst = _identity(canonical_id="DST_MIN", position="DST", team="MIN", dk_id="dkdst", rg_id="rgdst", fbg_id="fbgdst")
    pool = build_projection_pool(
        [wr, dst],
        dk_salary={"dk1": 7300, "dkdst": 2600},
        rotogrinders_fpts={"rg1": 18.0, "rgdst": 8.0},
        footballguys_points={"fbg1": 14.0, "fbgdst": 10.0},
    )
    assert len(pool) == 2
    by_id = {p.canonical_id: p for p in pool}
    assert by_id["wr-1"].blended_projection == 16.0
    assert by_id["DST_MIN"].blended_projection == 9.0
    assert by_id["DST_MIN"].position == "DST"


def test_build_projection_pool_keeps_uncovered_players_with_null_projection():
    uncovered = _identity(canonical_id="ghost-1", rg_id=None, fbg_id=None)
    pool = build_projection_pool([uncovered], dk_salary={"dk1": 4200})
    assert len(pool) == 1
    assert pool[0].blended_projection is None
    assert pool[0].source_count == 0
    assert pool[0].salary == 4200


# --- extraction helpers: RotoGrinders -------------------------------------------------------


def test_extract_rotogrinders_fpts_reads_fpts_keyed_by_playerid():
    payload = {
        "data": {
            "source": {
                "6228327": {"PLAYERID": "6228327", "PLAYER": "Drake Maye", "FPTS": "17.56"},
                "2861810": {"PLAYERID": "2861810", "PLAYER": "Rhamondre Stevenson", "FPTS": "14.48"},
            }
        }
    }
    result = extract_rotogrinders_fpts(payload)
    assert result == {"6228327": 17.56, "2861810": 14.48}


def test_extract_rotogrinders_fpts_skips_missing_or_unparseable_values():
    payload = {
        "data": {
            "source": {
                "1": {"PLAYERID": "1", "FPTS": None},
                "2": {"PLAYERID": "2", "FPTS": ""},
                "3": {"PLAYERID": "3", "FPTS": "not-a-number"},
                "4": {"PLAYERID": "4", "FPTS": "12.3"},
            }
        }
    }
    assert extract_rotogrinders_fpts(payload) == {"4": 12.3}


# --- extraction helpers: Footballguys --------------------------------------------------------


def _fbg_row(playerid: str, name: str, salary: str, points: str, pos: str = "QB") -> str:
    return (
        f'<tr data-playerid="{playerid}" data-playername="{name}">'
        f'<td class="rank">1</td>'
        f'<td class="name"><b>{name}</b></td>'
        f'<td><span class="pos-{pos}">{pos}</span></td>'
        f'<td class="ppg">{salary}</td>'
        f'<td class="text-nowrap">v OPP</td>'
        f'<td class="ppg ">{points}</td>'
        f"</tr>"
    )


def test_extract_footballguys_points_reads_second_ppg_cell_not_salary():
    html = f"<table><tbody>{_fbg_row('BurrJo01', 'Joe Burrow', '6900', '21.8')}</tbody></table>"
    result = extract_footballguys_points(html)
    assert result == {"BurrJo01": 21.8}


def test_extract_footballguys_points_handles_multiple_rows():
    html = (
        "<table><tbody>"
        + _fbg_row("BurrJo01", "Joe Burrow", "6900", "21.8")
        + _fbg_row("HerbJu00", "Justin Herbert", "6100", "21.27")
        + "</tbody></table>"
    )
    result = extract_footballguys_points(html)
    assert result == {"BurrJo01": 21.8, "HerbJu00": 21.27}


def test_extract_footballguys_points_skips_rows_missing_the_points_cell():
    html = '<tr data-playerid="x1"><td class="ppg">6900</td></tr>'
    assert extract_footballguys_points(html) == {}


# --- extraction helpers: DraftKings ----------------------------------------------------------


def test_extract_dk_salary_reads_salary_by_player_dk_id():
    payload = {
        "draftables": [
            {"playerDkId": 485454, "salary": 7300},
            {"playerDkId": 485454, "salary": 7300},  # duplicate roster-slot row, same salary
            {"playerDkId": 111111, "salary": 5000},
        ]
    }
    assert extract_dk_salary(payload) == {"485454": 7300, "111111": 5000}


def test_extract_dk_avg_points_per_game_reads_id_90_and_ignores_other_attribute_ids():
    payload = {
        "draftables": [
            {
                "playerDkId": 485454,
                "draftStatAttributes": [
                    {"id": 90, "value": "12.4", "sortValue": "12.4"},
                    {"id": -2, "value": "18th", "sortValue": "18"},
                ],
            }
        ]
    }
    assert extract_dk_avg_points_per_game(payload) == {"485454": 12.4}


# --- apply_rb_blowout_volume_discount ---------------------------------------------------------


def _projection(
    *,
    canonical_id: str,
    display_name: str = "Test RB",
    position: str = "RB",
    team: str = "DET",
    blended_projection: float | None = 15.0,
) -> PlayerProjection:
    return PlayerProjection(
        canonical_id=canonical_id,
        display_name=display_name,
        position=position,
        team=team,
        salary=6000,
        blended_projection=blended_projection,
        source_count=2 if blended_projection is not None else 0,
        source_values={} if blended_projection is None else {"rotogrinders": blended_projection},
    )


def _lead_rb_role_share(
    *,
    player_id: str = "00-0038542",
    player_name: str = "J.Gibbs",
) -> PlayerRoleShare:
    return PlayerRoleShare(
        player_id=player_id,
        player_name=player_name,
        role=ROLE_RB,
        weeks_played=8,
        trailing_volume=120,
        trailing_team_volume=200,
        trailing_share=0.60,
        shrinkage_weight=0.57,
        role_share_blended=0.58,
        role_tier="bell_cow",
        prior_used="league_average",
    )


def _rb_result(
    *,
    team: str = "DET",
    identified: PlayerRoleShare | None,
    gate_passed: bool,
) -> RoleShareResult:
    return RoleShareResult(
        season=2026,
        week=10,
        team=team,
        role=ROLE_RB,
        candidates=[identified] if identified is not None else [],
        identified=identified,
        gate_passed=gate_passed,
        gate_reason="test fixture",
    )


def test_confirmed_lead_rb_discount_not_applied_inside_the_no_effect_band():
    # |spread| <= 10 -> blowout_volume_discount == 1.00 -- ADR-0019/0020 band edge.
    lead_rb = _lead_rb_role_share()
    projections = [_projection(canonical_id=lead_rb.player_id, blended_projection=20.0)]
    result = _rb_result(identified=lead_rb, gate_passed=True)

    out = apply_rb_blowout_volume_discount(projections, [result], {"DET": -7.0})

    assert out[0].blended_projection == 20.0
    assert out[0] is projections[0]  # no-op discount -- same object, per docstring's contract


def test_confirmed_lead_rb_discount_applied_at_the_moderate_band():
    # 10 < |spread| <= 14 -> 0.97 (ADR-0019 Decision 2 band).
    lead_rb = _lead_rb_role_share()
    projections = [_projection(canonical_id=lead_rb.player_id, blended_projection=20.0)]
    result = _rb_result(identified=lead_rb, gate_passed=True)

    out = apply_rb_blowout_volume_discount(projections, [result], {"DET": -12.0})

    assert out[0].blended_projection == pytest.approx(20.0 * 0.97)


def test_confirmed_lead_rb_discount_applied_at_the_extreme_band():
    # |spread| > 14 -> 0.93 (ADR-0019 Decision 2 band). Sign of the spread shouldn't matter --
    # blowout_volume_discount takes abs().
    lead_rb = _lead_rb_role_share()
    projections = [_projection(canonical_id=lead_rb.player_id, blended_projection=20.0)]
    result = _rb_result(identified=lead_rb, gate_passed=True)

    out = apply_rb_blowout_volume_discount(projections, [result], {"DET": 21.0})

    assert out[0].blended_projection == pytest.approx(20.0 * 0.93)


def test_rb_role_gate_failed_applies_no_discount():
    # No confident lead-back identification this week -- must never guess a fallback discount,
    # even though a real (large) spread is available for the team.
    projections = [_projection(canonical_id="00-0099999", blended_projection=20.0)]
    result = _rb_result(identified=None, gate_passed=False)

    out = apply_rb_blowout_volume_discount(projections, [result], {"DET": 21.0})

    assert out[0].blended_projection == 20.0
    assert out[0] is projections[0]


def test_missing_spread_applies_no_discount_and_does_not_crash():
    lead_rb = _lead_rb_role_share()
    projections = [_projection(canonical_id=lead_rb.player_id, blended_projection=20.0)]
    result = _rb_result(identified=lead_rb, gate_passed=True)

    out = apply_rb_blowout_volume_discount(projections, [result], {})  # DET has no available spread

    assert out[0].blended_projection == 20.0
    assert out[0] is projections[0]


def test_non_lead_rb_and_non_rb_players_are_never_touched():
    lead_rb = _lead_rb_role_share(player_id="00-0038542")
    other_rb = _projection(canonical_id="00-0011111", display_name="Backup RB", blended_projection=5.0)
    lead_rb_projection = _projection(canonical_id=lead_rb.player_id, blended_projection=20.0)
    wr_projection = _projection(
        canonical_id="00-0022222", display_name="WR1", position="WR", blended_projection=12.0
    )
    projections = [lead_rb_projection, other_rb, wr_projection]

    rb_result = _rb_result(identified=lead_rb, gate_passed=True)
    # A WR-role RoleShareResult for the same team/spread must never contribute a discount --
    # ADR-0019 Decision 3, no WR1 BlowoutVolumeDiscount formula exists.
    wr_result = RoleShareResult(
        season=2026,
        week=10,
        team="DET",
        role=ROLE_WR,
        candidates=[],
        identified=PlayerRoleShare(
            player_id="00-0022222",
            player_name="A.StBrown",
            role=ROLE_WR,
            weeks_played=8,
            trailing_volume=60,
            trailing_team_volume=250,
            trailing_share=0.24,
            shrinkage_weight=0.57,
            role_share_blended=0.24,
            role_tier=None,
            prior_used="league_average",
        ),
        gate_passed=True,
        gate_reason="test fixture",
    )

    out = apply_rb_blowout_volume_discount(projections, [rb_result, wr_result], {"DET": 21.0})

    by_id = {p.canonical_id: p for p in out}
    assert by_id["00-0038542"].blended_projection == pytest.approx(20.0 * 0.93)  # lead RB discounted
    assert by_id["00-0011111"].blended_projection == 5.0  # untouched -- not the identified lead RB
    assert by_id["00-0022222"].blended_projection == 12.0  # untouched -- WR, no formula exists


def test_player_with_no_vendor_coverage_is_skipped_not_crashed():
    lead_rb = _lead_rb_role_share()
    projections = [_projection(canonical_id=lead_rb.player_id, blended_projection=None)]
    result = _rb_result(identified=lead_rb, gate_passed=True)

    out = apply_rb_blowout_volume_discount(projections, [result], {"DET": 21.0})

    assert out[0].blended_projection is None


def test_lead_rb_not_present_in_pool_is_skipped_not_crashed():
    lead_rb = _lead_rb_role_share(player_id="00-0038542")
    projections = [_projection(canonical_id="00-0099999", blended_projection=20.0)]
    result = _rb_result(identified=lead_rb, gate_passed=True)

    out = apply_rb_blowout_volume_discount(projections, [result], {"DET": 21.0})

    assert out[0].blended_projection == 20.0


def test_apply_matchup_context_moves_blended_projection_by_the_combined_multiplier():
    from nfl_dfs.matchup.context import MatchupContextResult

    projections = [_projection(canonical_id="rb1", blended_projection=20.0)]
    result = MatchupContextResult(
        canonical_player_id="rb1", team="DET", opponent="GB", position="RB",
        combined_multiplier=1.10, run_game=None, pass_protection=None, coverage=None,
        coverage_confidence=None,
    )
    out = apply_matchup_context(projections, {"rb1": result})
    assert out[0].blended_projection == pytest.approx(22.0)


def test_apply_matchup_context_no_op_when_player_missing_from_matchup_pool():
    from nfl_dfs.matchup.context import MatchupContextResult

    projections = [_projection(canonical_id="rb1", blended_projection=20.0), _projection(canonical_id="rb2", blended_projection=15.0)]
    result = MatchupContextResult(
        canonical_player_id="rb1", team="DET", opponent="GB", position="RB",
        combined_multiplier=1.10, run_game=None, pass_protection=None, coverage=None,
        coverage_confidence=None,
    )
    out = apply_matchup_context(projections, {"rb1": result})
    # rb2 has no MatchupContext entry -- untouched, same object, not multiplied by an assumed 1.0.
    assert out[1] is projections[1]
    assert out[1].blended_projection == 15.0


def test_apply_matchup_context_no_op_on_missing_blended_projection():
    from nfl_dfs.matchup.context import MatchupContextResult

    projections = [_projection(canonical_id="rb1", blended_projection=None)]
    result = MatchupContextResult(
        canonical_player_id="rb1", team="DET", opponent="GB", position="RB",
        combined_multiplier=1.10, run_game=None, pass_protection=None, coverage=None,
        coverage_confidence=None,
    )
    out = apply_matchup_context(projections, {"rb1": result})
    assert out[0].blended_projection is None


def test_team_spreads_from_games_maps_each_team_to_its_own_signed_spread():
    games = [
        GameOdds(
            home_team="DET",
            away_team="GB",
            commence_time="2026-09-13T17:00:00Z",
            home_spread=-7.0,
            away_spread=7.0,
            total=48.0,
            bookmaker_last_update="2026-09-13T12:00:00Z",
        )
    ]
    assert team_spreads_from_games(games) == {"DET": -7.0, "GB": 7.0}
