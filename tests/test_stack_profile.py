import pytest

from nfl_dfs.correlation.stack_profile import (
    BRING_BACK_VIABILITY_FLOOR,
    SPREAD_DAMPENER_BANDS,
    GameScriptLean,
    StackProfile,
    build_pivot_to,
    build_stack_profile,
    classify_game_script_lean,
    game_stack_viability,
    select_rb_stack_candidate,
    select_stack_candidates,
    single_team_viability,
    spread_dampener,
)
from nfl_dfs.game_environment.score import ComponentScore, GameEnvironmentScore
from nfl_dfs.ingestion.usage_share import (
    PRIOR_LEAGUE_AVERAGE,
    PRIOR_UNCONTESTED,
    ROLE_RB,
    ROLE_WR,
    PlayerRoleShare,
    RoleShareResult,
)
from nfl_dfs.matchup.context import MatchupContextResult


def _ges(team: str, composite: float | None, *, is_available: bool = True) -> GameEnvironmentScore:
    """Minimal synthetic GameEnvironmentScore -- the component-score fields aren't exercised by
    this module, so they're filled with harmless placeholders."""
    placeholder = ComponentScore(label="placeholder", weight_pct=0.0, z=None, points=None)
    return GameEnvironmentScore(
        team=team,
        season=2026,
        week=1,
        is_available=is_available,
        composite_score=composite,
        implied_total=placeholder,
        pace=placeholder,
        proe=placeholder,
        weather=placeholder,
    )


def _player_role_share(
    player_id: str,
    player_name: str | None,
    role: str,
    role_share_blended: float,
    *,
    weeks_played: int = 4,
    trailing_volume: int = 40,
    trailing_team_volume: int = 100,
    role_tier: str | None = None,
    prior_used: str = PRIOR_LEAGUE_AVERAGE,
) -> PlayerRoleShare:
    """Minimal synthetic PlayerRoleShare -- this module only ever reads player_id/player_name/
    role_share_blended/prior_used, but every field is filled so the dataclass is always valid."""
    return PlayerRoleShare(
        player_id=player_id,
        player_name=player_name,
        role=role,
        weeks_played=weeks_played,
        trailing_volume=trailing_volume,
        trailing_team_volume=trailing_team_volume,
        trailing_share=trailing_volume / trailing_team_volume,
        shrinkage_weight=weeks_played / (weeks_played + 6),
        role_share_blended=role_share_blended,
        role_tier=role_tier,
        prior_used=prior_used,
    )


def _matchup_context(player_id: str, combined_multiplier: float) -> MatchupContextResult:
    """Minimal synthetic MatchupContextResult -- this module only ever reads combined_multiplier
    (via player_id, as the dict key), but every field is filled so the dataclass is always valid."""
    return MatchupContextResult(
        canonical_player_id=player_id,
        team="X",
        opponent="Y",
        position="WR",
        combined_multiplier=combined_multiplier,
        run_game=None,
        pass_protection=None,
        coverage=None,
        coverage_confidence=None,
    )


def _role_share_result(
    team: str,
    role: str,
    candidates: list[PlayerRoleShare],
    *,
    season: int = 2026,
    week: int = 1,
    identified: PlayerRoleShare | None = None,
    gate_passed: bool = False,
    gate_reason: str = "",
) -> RoleShareResult:
    return RoleShareResult(
        season=season,
        week=week,
        team=team,
        role=role,
        candidates=candidates,
        identified=identified,
        gate_passed=gate_passed,
        gate_reason=gate_reason,
    )


# --------------------------------------------------------------------------------------------
# spread_dampener -- band table (ADR-0004, tail extended by ADR-0010)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "abs_spread,expected",
    [
        (0.0, 1.00),
        (2.5, 1.00),
        (3.0, 1.00),  # boundary: exactly 3 -> still the <=3 band
        (3.1, 0.85),
        (7.0, 0.85),  # boundary: exactly 7 -> still the 3-7 band
        (7.1, 0.65),
        (10.0, 0.65),  # boundary: exactly 10 -> still the 7-10 band
        (10.1, 0.40),
        (14.0, 0.40),  # boundary: exactly 14 -> still the 10-14 band
        (14.1, 0.25),
        (24.0, 0.25),  # deep blowout -- still floors at 0.25, never 0
    ],
)
def test_spread_dampener_bands(abs_spread: float, expected: float) -> None:
    assert spread_dampener(abs_spread) == pytest.approx(expected)


def test_spread_dampener_table_matches_prd_and_adr_0010() -> None:
    # Locks in the exact 5-band table PRD Section 6 / ADR-0010 document -- if this table is ever
    # revised again, this test should be the first thing to fail and force an explicit update.
    assert SPREAD_DAMPENER_BANDS == (
        (3.0, 1.00),
        (7.0, 0.85),
        (10.0, 0.65),
        (14.0, 0.40),
        (float("inf"), 0.25),
    )


def test_spread_dampener_rejects_negative_input() -> None:
    with pytest.raises(ValueError):
        spread_dampener(-3.0)


# --------------------------------------------------------------------------------------------
# single_team_viability
# --------------------------------------------------------------------------------------------


def test_single_team_viability_returns_composite_directly() -> None:
    ges = _ges("KC", composite=72.3)
    assert single_team_viability(ges) == pytest.approx(72.3)


def test_single_team_viability_none_when_unavailable() -> None:
    ges = _ges("NYJ", composite=None, is_available=False)
    assert single_team_viability(ges) is None


# --------------------------------------------------------------------------------------------
# game_stack_viability
# --------------------------------------------------------------------------------------------


def test_game_stack_viability_normal_case_both_available() -> None:
    ges_a = _ges("KC", composite=80.0)
    ges_b = _ges("BUF", composite=60.0)
    # min(80, 60) * dampener(|spread|=2 -> band <=3 -> 1.00) == 60.0
    assert game_stack_viability(ges_a, ges_b, spread=2.0) == pytest.approx(60.0)


def test_game_stack_viability_uses_bottleneck_not_average() -> None:
    ges_a = _ges("KC", composite=95.0)
    ges_b = _ges("BUF", composite=10.0)
    # min() bottleneck -- a very strong team_A must not mask a weak team_B.
    assert game_stack_viability(ges_a, ges_b, spread=0.0) == pytest.approx(10.0)


def test_game_stack_viability_applies_dampener_by_band() -> None:
    ges_a = _ges("KC", composite=100.0)
    ges_b = _ges("BUF", composite=100.0)
    assert game_stack_viability(ges_a, ges_b, spread=5.0) == pytest.approx(85.0)
    assert game_stack_viability(ges_a, ges_b, spread=8.0) == pytest.approx(65.0)
    assert game_stack_viability(ges_a, ges_b, spread=12.0) == pytest.approx(40.0)
    assert game_stack_viability(ges_a, ges_b, spread=20.0) == pytest.approx(25.0)


def test_game_stack_viability_accepts_signed_spread() -> None:
    ges_a = _ges("KC", composite=100.0)
    ges_b = _ges("BUF", composite=100.0)
    # A negative (favorite) spread should dampen identically to its positive magnitude.
    assert game_stack_viability(ges_a, ges_b, spread=-8.0) == pytest.approx(65.0)


def test_game_stack_viability_none_when_team_a_unavailable() -> None:
    ges_a = _ges("KC", composite=None, is_available=False)
    ges_b = _ges("BUF", composite=90.0)
    assert game_stack_viability(ges_a, ges_b, spread=3.0) is None


def test_game_stack_viability_none_when_team_b_unavailable() -> None:
    ges_a = _ges("KC", composite=90.0)
    ges_b = _ges("BUF", composite=None, is_available=False)
    assert game_stack_viability(ges_a, ges_b, spread=3.0) is None


def test_game_stack_viability_none_when_both_unavailable() -> None:
    ges_a = _ges("KC", composite=None, is_available=False)
    ges_b = _ges("BUF", composite=None, is_available=False)
    assert game_stack_viability(ges_a, ges_b, spread=3.0) is None


def test_game_stack_viability_does_not_treat_unavailable_as_low_score() -> None:
    # Regression guard for the specific failure mode the task brief called out: an unavailable
    # team must not silently act like a very-low (e.g. 0) composite_score inside min().
    # If it did, this would return 0.0 * dampener instead of None.
    ges_a = _ges("KC", composite=None, is_available=False)
    ges_b = _ges("BUF", composite=5.0)  # deliberately low, to distinguish None-as-zero from real
    result = game_stack_viability(ges_a, ges_b, spread=1.0)
    assert result is None
    assert result != 0.0


# --------------------------------------------------------------------------------------------
# build_stack_profile / StackProfile
# --------------------------------------------------------------------------------------------


def test_build_stack_profile_normal_case() -> None:
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("BUF", composite=60.0)
    home_wr = _role_share_result(
        "KC",
        ROLE_WR,
        [
            _player_role_share("KC-WR1", "Rashee Rice", ROLE_WR, 0.28),
            _player_role_share("KC-WR2", "Xavier Worthy", ROLE_WR, 0.20),
            _player_role_share("KC-WR3", "JuJu Smith-Schuster", ROLE_WR, 0.10),
        ],
    )
    away_wr = _role_share_result(
        "BUF",
        ROLE_WR,
        [_player_role_share("BUF-WR1", "Khalil Shakir", ROLE_WR, 0.24)],
    )
    profile = build_stack_profile(ges_home, ges_away, 2.0, home_wr, away_wr)

    assert isinstance(profile, StackProfile)
    assert profile.season == 2026
    assert profile.week == 1
    assert profile.home_team == "KC"
    assert profile.away_team == "BUF"
    assert profile.single_team_viability_home == pytest.approx(80.0)
    assert profile.single_team_viability_away == pytest.approx(60.0)
    assert profile.game_stack_viability == pytest.approx(60.0)
    assert profile.game_id == "BUF@KC-2026wk1"

    # Top-2 by role_share_blended, from the anchor (home) team's own roster.
    assert [c.player_name for c in profile.primary_stack_candidates] == ["Rashee Rice", "Xavier Worthy"]
    # Bring-back comes from the OPPOSING (away) team's roster, not the same team twice.
    assert [c.player_name for c in profile.bring_back_candidates] == ["Khalil Shakir"]

    assert profile.pivot_to is not None
    assert "KC" in profile.pivot_to
    assert "Rashee Rice" in profile.pivot_to
    assert "Xavier Worthy" in profile.pivot_to
    assert "MatchupContext" in profile.pivot_to  # explicit partial-implementation caveat

    # ADR-0021: game_stack_viability (60.0) clears BRING_BACK_VIABILITY_FLOOR (20.0), and a
    # confident opposing WR was found -- "populated" is the real-candidates state.
    assert profile.bring_back_status == "populated"
    assert "Bring-back viability" in profile.pivot_to


def test_build_stack_profile_one_team_unavailable() -> None:
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("NYJ", composite=None, is_available=False)
    home_wr = _role_share_result("KC", ROLE_WR, [_player_role_share("KC-WR1", "Rashee Rice", ROLE_WR, 0.28)])
    away_wr = _role_share_result("NYJ", ROLE_WR, [])
    profile = build_stack_profile(ges_home, ges_away, -6.0, home_wr, away_wr)

    assert profile.single_team_viability_home == pytest.approx(80.0)
    assert profile.single_team_viability_away is None
    assert profile.game_stack_viability is None
    assert any("NYJ" in note and "unavailable" in note for note in profile.notes)

    # The anchor (home) team is still available, so its own primary candidates are still real --
    # only the bring-back side (which needs BOTH teams' environments) is excluded.
    assert profile.primary_stack_candidates == [home_wr.candidates[0]]
    assert profile.bring_back_candidates is None
    # ADR-0021: this None is the pre-existing "no GameEnvironmentScore data at all" reason, NOT
    # the new gate -- distinguished explicitly via bring_back_status.
    assert profile.bring_back_status == "environment_unavailable"
    assert profile.pivot_to is not None


def test_build_stack_profile_anchor_team_unavailable_blocks_candidates_and_pivot() -> None:
    ges_home = _ges("KC", composite=None, is_available=False)
    ges_away = _ges("BUF", composite=60.0)
    home_wr = _role_share_result("KC", ROLE_WR, [_player_role_share("KC-WR1", "Rashee Rice", ROLE_WR, 0.28)])
    away_wr = _role_share_result("BUF", ROLE_WR, [_player_role_share("BUF-WR1", "Khalil Shakir", ROLE_WR, 0.24)])
    profile = build_stack_profile(ges_home, ges_away, 2.0, home_wr, away_wr)

    # ADR-0017 exclusion policy: no computable anchor-team GameEnvironmentScore means no basis for
    # a stack thesis at all -- None, not an empty list and not a fabricated candidate.
    assert profile.primary_stack_candidates is None
    assert profile.bring_back_candidates is None
    assert profile.bring_back_status == "environment_unavailable"
    assert profile.pivot_to is None


def test_build_stack_profile_no_confident_wr_candidates_is_empty_list_not_fabricated() -> None:
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("BUF", composite=60.0)
    home_wr = _role_share_result("KC", ROLE_WR, [])  # no trailing WR volume recorded at all
    away_wr = _role_share_result("BUF", ROLE_WR, [_player_role_share("BUF-WR1", "Khalil Shakir", ROLE_WR, 0.24)])
    profile = build_stack_profile(ges_home, ges_away, 2.0, home_wr, away_wr)

    assert profile.primary_stack_candidates == []  # a real "no confident candidate," not None
    assert "no WR-role trailing-volume candidates" in " ".join(profile.notes)
    assert profile.pivot_to is not None
    assert "no pass-catcher currently clears" in profile.pivot_to

    # This case is about the PRIMARY (home) side lacking a candidate -- the away/bring-back side
    # still has one, and game_stack_viability (60.0) clears the floor, so bring_back_candidates is
    # unaffected by the ADR-0021 gate here.
    assert [c.player_name for c in profile.bring_back_candidates] == ["Khalil Shakir"]
    assert profile.bring_back_status == "populated"


def test_build_stack_profile_rejects_mismatched_team_role_share() -> None:
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("BUF", composite=60.0)
    wrong_team_wr = _role_share_result("NYJ", ROLE_WR, [])
    ok_wr = _role_share_result("BUF", ROLE_WR, [])
    with pytest.raises(AssertionError):
        build_stack_profile(ges_home, ges_away, 2.0, wrong_team_wr, ok_wr)


# --------------------------------------------------------------------------------------------
# ADR-0021 -- bring_back_candidates gated on game_stack_viability (BRING_BACK_VIABILITY_FLOOR)
# --------------------------------------------------------------------------------------------


def test_build_stack_profile_low_viability_blowout_suppresses_bring_back() -> None:
    """A lopsided spread on an otherwise decent-bottleneck game pushes game_stack_viability below
    BRING_BACK_VIABILITY_FLOOR (20.0) -- bring_back_candidates must be None with
    bring_back_status="game_stack_not_viable", NOT the pre-existing "environment_unavailable"
    reason (both GameEnvironmentScores ARE available here), and NOT collapsed into the
    "no_confident_candidate" state either (a real, confident opposing WR candidate exists --
    it's the game environment itself that's being deliberately not recommended)."""
    ges_home = _ges("KC", composite=70.0)
    ges_away = _ges("BUF", composite=70.0)
    home_wr = _role_share_result("KC", ROLE_WR, [_player_role_share("KC-WR1", "Rashee Rice", ROLE_WR, 0.28)])
    away_wr = _role_share_result(
        "BUF", ROLE_WR, [_player_role_share("BUF-WR1", "Khalil Shakir", ROLE_WR, 0.24)]
    )
    # |spread|=20 -> extreme-blowout dampener band (0.25). bottleneck=70 -> viability=17.5 < 20.0.
    profile = build_stack_profile(ges_home, ges_away, 20.0, home_wr, away_wr)

    assert profile.game_stack_viability == pytest.approx(17.5)
    assert profile.game_stack_viability < BRING_BACK_VIABILITY_FLOOR
    assert profile.bring_back_candidates is None
    assert profile.bring_back_status == "game_stack_not_viable"

    # primary_stack_candidates/single_team_viability are explicitly NOT affected by this gate.
    assert profile.primary_stack_candidates == [home_wr.candidates[0]]
    assert profile.single_team_viability_home == pytest.approx(70.0)
    assert profile.single_team_viability_away == pytest.approx(70.0)

    # pivot_to must never assert a bring-back pairing the structured fields say isn't supported.
    # away_team ("BUF") only ever appears in pivot_to via the bring-back clause -- its absence
    # confirms that clause did not fire.
    assert profile.pivot_to is not None
    assert "Bring-back" not in profile.pivot_to
    assert "BUF" not in profile.pivot_to

    assert any("game_stack_not_viable" in note or "BRING_BACK_VIABILITY_FLOOR" in note for note in profile.notes)


def test_build_stack_profile_pivot_to_omits_bring_back_clause_when_not_viable() -> None:
    """Direct check of build_pivot_to's own gate (ADR-0021 Decision 4): a game_stack_score below
    the floor must not produce the affirmative 'Bring-back viability ... supports pairing' text,
    even though the score is a real, non-None number."""
    ges_home = _ges("KC", composite=70.0)
    candidates = [_player_role_share("A", "Rashee Rice", ROLE_WR, 0.28)]
    low_score = BRING_BACK_VIABILITY_FLOOR - 0.1
    text = build_pivot_to(ges_home, "BUF", candidates, low_score, None)
    assert text is not None
    assert "Bring-back" not in text
    assert "supports pairing" not in text

    # At/above the floor, the clause fires as before.
    at_floor_text = build_pivot_to(ges_home, "BUF", candidates, BRING_BACK_VIABILITY_FLOOR, None)
    assert at_floor_text is not None
    assert "Bring-back viability" in at_floor_text
    assert "supports pairing" in at_floor_text


def test_build_stack_profile_viable_game_still_populates_as_before() -> None:
    """A genuinely viable game (near-pick'em spread, solid bottleneck) keeps the pre-ADR-0021
    behavior exactly: bring_back_candidates populated with real candidates, status "populated"."""
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("BUF", composite=60.0)
    home_wr = _role_share_result("KC", ROLE_WR, [_player_role_share("KC-WR1", "Rashee Rice", ROLE_WR, 0.28)])
    away_wr = _role_share_result(
        "BUF", ROLE_WR, [_player_role_share("BUF-WR1", "Khalil Shakir", ROLE_WR, 0.24)]
    )
    profile = build_stack_profile(ges_home, ges_away, 2.0, home_wr, away_wr)

    assert profile.game_stack_viability == pytest.approx(60.0)
    assert profile.game_stack_viability >= BRING_BACK_VIABILITY_FLOOR
    assert [c.player_name for c in profile.bring_back_candidates] == ["Khalil Shakir"]
    assert profile.bring_back_status == "populated"
    assert profile.pivot_to is not None
    assert "Bring-back viability" in profile.pivot_to


def test_build_stack_profile_viable_game_no_confident_candidate() -> None:
    """Both GameEnvironmentScores available, game_stack_viability clears the floor, but the
    opposing roster has no trailing-volume WR candidate at all -- [] with
    bring_back_status="no_confident_candidate", distinguished from the new gate's None."""
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("BUF", composite=60.0)
    home_wr = _role_share_result("KC", ROLE_WR, [_player_role_share("KC-WR1", "Rashee Rice", ROLE_WR, 0.28)])
    away_wr = _role_share_result("BUF", ROLE_WR, [])  # no trailing WR volume recorded at all

    profile = build_stack_profile(ges_home, ges_away, 2.0, home_wr, away_wr)

    assert profile.game_stack_viability == pytest.approx(60.0)
    assert profile.bring_back_candidates == []
    assert profile.bring_back_status == "no_confident_candidate"
    assert "BUF: no WR-role trailing-volume candidates" in " ".join(profile.notes)


def test_build_stack_profile_folds_in_uncontested_rb_signal() -> None:
    ges_home = _ges("DET", composite=85.0)
    ges_away = _ges("GB", composite=70.0)
    home_wr = _role_share_result(
        "DET", ROLE_WR, [_player_role_share("DET-WR1", "Amon-Ra St. Brown", ROLE_WR, 0.30)]
    )
    away_wr = _role_share_result("GB", ROLE_WR, [_player_role_share("GB-WR1", "Jayden Reed", ROLE_WR, 0.22)])
    gibbs = _player_role_share(
        "DET-RB1",
        "Jahmyr Gibbs",
        ROLE_RB,
        0.68,
        role_tier="bell_cow",
        prior_used=PRIOR_UNCONTESTED,
    )
    home_rb = _role_share_result("DET", ROLE_RB, [gibbs], identified=gibbs, gate_passed=True)

    profile = build_stack_profile(ges_home, ges_away, -3.0, home_wr, away_wr, home_rb)

    assert profile.pivot_to is not None
    assert "Jahmyr Gibbs" in profile.pivot_to
    assert "uncontested" in profile.pivot_to.lower()
    assert "68%" in profile.pivot_to


def test_build_stack_profile_no_uncontested_note_when_signal_did_not_fire() -> None:
    ges_home = _ges("DET", composite=85.0)
    ges_away = _ges("GB", composite=70.0)
    home_wr = _role_share_result(
        "DET", ROLE_WR, [_player_role_share("DET-WR1", "Amon-Ra St. Brown", ROLE_WR, 0.30)]
    )
    away_wr = _role_share_result("GB", ROLE_WR, [_player_role_share("GB-WR1", "Jayden Reed", ROLE_WR, 0.22)])
    gibbs = _player_role_share(
        "DET-RB1",
        "Jahmyr Gibbs",
        ROLE_RB,
        0.55,
        role_tier="mid_tier",
        prior_used=PRIOR_LEAGUE_AVERAGE,  # gate passed, but the uncontested check did NOT fire
    )
    home_rb = _role_share_result("DET", ROLE_RB, [gibbs], identified=gibbs, gate_passed=True)

    profile = build_stack_profile(ges_home, ges_away, -3.0, home_wr, away_wr, home_rb)

    assert profile.pivot_to is not None
    assert "uncontested" not in profile.pivot_to.lower()


# --------------------------------------------------------------------------------------------
# select_stack_candidates
# --------------------------------------------------------------------------------------------


def test_select_stack_candidates_returns_top_two_by_role_share() -> None:
    wr = _role_share_result(
        "KC",
        ROLE_WR,
        [
            _player_role_share("A", "Player A", ROLE_WR, 0.30),
            _player_role_share("B", "Player B", ROLE_WR, 0.20),
            _player_role_share("C", "Player C", ROLE_WR, 0.10),
        ],
    )
    candidates = select_stack_candidates(wr)
    assert [c.player_id for c in candidates] == ["A", "B"]


def test_select_stack_candidates_returns_single_candidate_when_only_one_exists() -> None:
    wr = _role_share_result("KC", ROLE_WR, [_player_role_share("A", "Player A", ROLE_WR, 0.30)])
    candidates = select_stack_candidates(wr)
    assert [c.player_id for c in candidates] == ["A"]


def test_select_stack_candidates_empty_when_no_candidates() -> None:
    wr = _role_share_result("KC", ROLE_WR, [])
    assert select_stack_candidates(wr) == []


def test_select_stack_candidates_rejects_non_wr_role() -> None:
    rb = _role_share_result("KC", ROLE_RB, [_player_role_share("A", "Player A", ROLE_RB, 0.60)])
    with pytest.raises(ValueError):
        select_stack_candidates(rb)


# --------------------------------------------------------------------------------------------
# build_pivot_to
# --------------------------------------------------------------------------------------------


def test_build_pivot_to_none_when_anchor_unavailable() -> None:
    ges_home = _ges("KC", composite=None, is_available=False)
    assert build_pivot_to(ges_home, "BUF", None, None, None) is None


def test_build_pivot_to_names_candidates_and_score() -> None:
    ges_home = _ges("KC", composite=80.0)
    candidates = [_player_role_share("A", "Rashee Rice", ROLE_WR, 0.28)]
    text = build_pivot_to(ges_home, "BUF", candidates, 60.0, None)
    assert text is not None
    assert "KC" in text
    assert "Rashee Rice" in text
    assert "80" in text
    assert "60.0" in text
    assert "BUF" in text


def test_build_pivot_to_names_primary_rb_candidate() -> None:
    # RBs should be named as part of the stack thesis directly, not just via the narrower
    # uncontested-backfield clause -- the original complaint this round closes ("a bell-cow RB
    # isn't credited as part of the stack").
    ges_home = _ges("MIN", composite=80.0)
    candidates = [_player_role_share("A", "Justin Jefferson", ROLE_WR, 0.28)]
    rb = _player_role_share("B", "Aaron Jones", ROLE_RB, 0.55, role_tier="mid_tier")
    text = build_pivot_to(ges_home, "CHI", candidates, 60.0, None, primary_rb_candidate=rb)
    assert text is not None
    assert "Aaron Jones" in text
    assert "MIN stack thesis" in text
    assert "55%" in text


def test_build_pivot_to_names_bring_back_rb_candidate() -> None:
    ges_home = _ges("MIN", composite=80.0)
    candidates = [_player_role_share("A", "Justin Jefferson", ROLE_WR, 0.28)]
    rb = _player_role_share("B", "D.J. Moore RB", ROLE_RB, 0.60, role_tier="bell_cow")
    text = build_pivot_to(ges_home, "CHI", candidates, 60.0, None, bring_back_rb_candidate=rb)
    assert text is not None
    assert "bring-back candidate for CHI" in text
    assert "60%" in text


def test_build_pivot_to_omits_rb_clauses_when_no_candidate_supplied() -> None:
    ges_home = _ges("KC", composite=80.0)
    candidates = [_player_role_share("A", "Rashee Rice", ROLE_WR, 0.28)]
    text = build_pivot_to(ges_home, "BUF", candidates, 60.0, None)
    assert text is not None
    assert "stack thesis" not in text
    assert "bring-back candidate for" not in text


# --------------------------------------------------------------------------------------------
# select_rb_stack_candidate (NflAgentConstructor plan, foundation signals)
# --------------------------------------------------------------------------------------------


def test_select_rb_stack_candidate_returns_bell_cow() -> None:
    gibbs = _player_role_share("DET-RB1", "Jahmyr Gibbs", ROLE_RB, 0.68, role_tier="bell_cow")
    rb = _role_share_result("DET", ROLE_RB, [gibbs], identified=gibbs, gate_passed=True)
    assert select_rb_stack_candidate(rb) is gibbs


def test_select_rb_stack_candidate_returns_mid_tier() -> None:
    back = _player_role_share("X-RB1", "Some Back", ROLE_RB, 0.50, role_tier="mid_tier")
    rb = _role_share_result("X", ROLE_RB, [back], identified=back, gate_passed=True)
    assert select_rb_stack_candidate(rb) is back


def test_select_rb_stack_candidate_none_for_committee_tier() -> None:
    # Gate-cleared (identified is non-None), but role_tier is "committee" -- not dominant enough
    # to anchor a stack thesis, even though it was identifiable at all.
    back = _player_role_share("X-RB1", "Committee Back", ROLE_RB, 0.42, role_tier="committee")
    rb = _role_share_result("X", ROLE_RB, [back], identified=back, gate_passed=True)
    assert select_rb_stack_candidate(rb) is None


def test_select_rb_stack_candidate_none_when_not_identified() -> None:
    rb = _role_share_result("X", ROLE_RB, [], identified=None, gate_passed=False)
    assert select_rb_stack_candidate(rb) is None


def test_select_rb_stack_candidate_rejects_non_rb_role() -> None:
    wr = _role_share_result("KC", ROLE_WR, [_player_role_share("A", "Player A", ROLE_WR, 0.30)])
    with pytest.raises(ValueError):
        select_rb_stack_candidate(wr)


# --------------------------------------------------------------------------------------------
# classify_game_script_lean (NflAgentConstructor plan, foundation signals)
# --------------------------------------------------------------------------------------------


def test_classify_game_script_lean_favorite() -> None:
    lean = classify_game_script_lean(-6.5)
    assert lean.stance == "favorite"
    assert lean.abs_spread == pytest.approx(6.5)
    assert lean.intensity == pytest.approx(0.85)  # 3 < 6.5 <= 7 band


def test_classify_game_script_lean_underdog() -> None:
    lean = classify_game_script_lean(6.5)
    assert lean.stance == "underdog"
    assert lean.abs_spread == pytest.approx(6.5)
    assert lean.intensity == pytest.approx(0.85)


def test_classify_game_script_lean_pick_em() -> None:
    lean = classify_game_script_lean(0.0)
    assert lean.stance == "pick_em"
    assert lean.abs_spread == pytest.approx(0.0)
    assert lean.intensity == pytest.approx(1.00)


def test_classify_game_script_lean_extreme_blowout_favorite() -> None:
    lean = classify_game_script_lean(-20.0)
    assert lean.stance == "favorite"
    assert lean.intensity == pytest.approx(0.25)


def test_classify_game_script_lean_reuses_spread_dampener_bands() -> None:
    # Same table, new purpose -- locking in that classify_game_script_lean doesn't invent its own
    # thresholds (ADR-0011 "reuse before inventing").
    for upper_bound, dampener in SPREAD_DAMPENER_BANDS:
        if upper_bound == float("inf"):
            continue
        assert classify_game_script_lean(-upper_bound).intensity == pytest.approx(dampener)


# --------------------------------------------------------------------------------------------
# build_stack_profile -- RB candidates + game-script lean wiring (NflAgentConstructor plan)
# --------------------------------------------------------------------------------------------


def test_build_stack_profile_populates_primary_rb_candidate() -> None:
    ges_home = _ges("DET", composite=85.0)
    ges_away = _ges("GB", composite=70.0)
    home_wr = _role_share_result(
        "DET", ROLE_WR, [_player_role_share("DET-WR1", "Amon-Ra St. Brown", ROLE_WR, 0.30)]
    )
    away_wr = _role_share_result("GB", ROLE_WR, [_player_role_share("GB-WR1", "Jayden Reed", ROLE_WR, 0.22)])
    gibbs = _player_role_share("DET-RB1", "Jahmyr Gibbs", ROLE_RB, 0.68, role_tier="bell_cow")
    home_rb = _role_share_result("DET", ROLE_RB, [gibbs], identified=gibbs, gate_passed=True)

    profile = build_stack_profile(ges_home, ges_away, -3.0, home_wr, away_wr, home_rb)

    assert profile.primary_rb_candidate is gibbs


def test_build_stack_profile_primary_rb_candidate_none_when_role_share_not_supplied() -> None:
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("BUF", composite=60.0)
    home_wr = _role_share_result("KC", ROLE_WR, [_player_role_share("KC-WR1", "Rashee Rice", ROLE_WR, 0.28)])
    away_wr = _role_share_result("BUF", ROLE_WR, [_player_role_share("BUF-WR1", "Khalil Shakir", ROLE_WR, 0.24)])
    profile = build_stack_profile(ges_home, ges_away, 2.0, home_wr, away_wr)
    assert profile.primary_rb_candidate is None


def test_build_stack_profile_populates_bring_back_rb_candidate_when_viable() -> None:
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("BUF", composite=60.0)
    home_wr = _role_share_result("KC", ROLE_WR, [_player_role_share("KC-WR1", "Rashee Rice", ROLE_WR, 0.28)])
    away_wr = _role_share_result("BUF", ROLE_WR, [_player_role_share("BUF-WR1", "Khalil Shakir", ROLE_WR, 0.24)])
    cook = _player_role_share("BUF-RB1", "James Cook", ROLE_RB, 0.62, role_tier="bell_cow")
    away_rb = _role_share_result("BUF", ROLE_RB, [cook], identified=cook, gate_passed=True)

    # |spread|=2 -> dampener 1.00, bottleneck=60 -> game_stack_viability=60 >= floor (20.0).
    profile = build_stack_profile(ges_home, ges_away, 2.0, home_wr, away_wr, away_rb_role_share=away_rb)

    assert profile.bring_back_rb_candidate is cook


def test_build_stack_profile_bring_back_rb_candidate_none_when_game_stack_not_viable() -> None:
    """Same ADR-0021 floor gate as the WR bring_back_candidates -- an RB candidate that would
    otherwise be real must not be surfaced when the combined game-stack thesis doesn't clear the
    bar."""
    ges_home = _ges("KC", composite=70.0)
    ges_away = _ges("BUF", composite=70.0)
    home_wr = _role_share_result("KC", ROLE_WR, [_player_role_share("KC-WR1", "Rashee Rice", ROLE_WR, 0.28)])
    away_wr = _role_share_result("BUF", ROLE_WR, [_player_role_share("BUF-WR1", "Khalil Shakir", ROLE_WR, 0.24)])
    cook = _player_role_share("BUF-RB1", "James Cook", ROLE_RB, 0.62, role_tier="bell_cow")
    away_rb = _role_share_result("BUF", ROLE_RB, [cook], identified=cook, gate_passed=True)

    # |spread|=20 -> extreme-blowout dampener (0.25). bottleneck=70 -> viability=17.5 < 20.0.
    profile = build_stack_profile(ges_home, ges_away, 20.0, home_wr, away_wr, away_rb_role_share=away_rb)

    assert profile.bring_back_rb_candidate is None
    assert profile.bring_back_status == "game_stack_not_viable"


def test_build_stack_profile_bring_back_rb_candidate_none_when_environment_unavailable() -> None:
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("NYJ", composite=None, is_available=False)
    home_wr = _role_share_result("KC", ROLE_WR, [_player_role_share("KC-WR1", "Rashee Rice", ROLE_WR, 0.28)])
    away_wr = _role_share_result("NYJ", ROLE_WR, [])
    cook = _player_role_share("NYJ-RB1", "Some Back", ROLE_RB, 0.62, role_tier="bell_cow")
    away_rb = _role_share_result("NYJ", ROLE_RB, [cook], identified=cook, gate_passed=True)

    profile = build_stack_profile(ges_home, ges_away, -6.0, home_wr, away_wr, away_rb_role_share=away_rb)

    assert profile.bring_back_rb_candidate is None
    assert profile.bring_back_status == "environment_unavailable"


def test_build_stack_profile_game_script_lean_home_and_away_are_mirrored() -> None:
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("BUF", composite=60.0)
    home_wr = _role_share_result("KC", ROLE_WR, [])
    away_wr = _role_share_result("BUF", ROLE_WR, [])
    # spread=-6.5 -- home (KC) is the favorite, away (BUF) is the underdog by the same magnitude.
    profile = build_stack_profile(ges_home, ges_away, -6.5, home_wr, away_wr)

    assert profile.game_script_lean_home == GameScriptLean(stance="favorite", abs_spread=6.5, intensity=0.85)
    assert profile.game_script_lean_away == GameScriptLean(stance="underdog", abs_spread=6.5, intensity=0.85)


def test_build_stack_profile_game_script_lean_always_computed_even_when_environment_unavailable() -> None:
    # Pure function of spread -- unlike the WR/RB candidate fields, never gated on
    # GameEnvironmentScore availability.
    ges_home = _ges("KC", composite=None, is_available=False)
    ges_away = _ges("BUF", composite=60.0)
    home_wr = _role_share_result("KC", ROLE_WR, [])
    away_wr = _role_share_result("BUF", ROLE_WR, [])
    profile = build_stack_profile(ges_home, ges_away, 3.0, home_wr, away_wr)

    assert profile.game_script_lean_home is not None
    assert profile.game_script_lean_away is not None


# --------------------------------------------------------------------------------------------
# select_stack_candidates / build_stack_profile -- MatchupContext-combined ranking
# (NflAgentConstructor plan: "RBs was an example -- we have to do that all over the field")
# --------------------------------------------------------------------------------------------


def test_stack_ranking_score_falls_back_to_role_share_when_no_matchup_context_dict() -> None:
    candidates = select_stack_candidates(
        _role_share_result(
            "KC",
            ROLE_WR,
            [
                _player_role_share("A", "Player A", ROLE_WR, 0.30),
                _player_role_share("B", "Player B", ROLE_WR, 0.20),
            ],
        )
    )
    assert [c.player_id for c in candidates] == ["A", "B"]


def test_select_stack_candidates_reranks_by_matchup_context_when_supplied() -> None:
    # B has less raw target share than A, but a strong enough MatchupContext edge to outrank A --
    # this is the actual, previously-flagged PRD Section 6 gap ("target share AND MatchupContext
    # favorability, not raw season totals alone"), now closed.
    wr = _role_share_result(
        "KC",
        ROLE_WR,
        [
            _player_role_share("A", "Player A", ROLE_WR, 0.30),
            _player_role_share("B", "Player B", ROLE_WR, 0.28),
            _player_role_share("C", "Player C", ROLE_WR, 0.10),
        ],
    )
    matchup_context_by_player_id = {
        "A": _matchup_context("A", 0.85),  # tough matchup, dampens A's effective score to 0.255
        "B": _matchup_context("B", 1.15),  # favorable matchup, boosts B's effective score to 0.322
        # C has no MatchupContext read at all -- falls back to role_share_blended (0.10) alone.
    }
    candidates = select_stack_candidates(wr, matchup_context_by_player_id=matchup_context_by_player_id)
    assert [c.player_id for c in candidates] == ["B", "A"]


def test_select_stack_candidates_missing_matchup_context_read_degrades_to_role_share() -> None:
    wr = _role_share_result(
        "KC",
        ROLE_WR,
        [
            _player_role_share("A", "Player A", ROLE_WR, 0.30),
            _player_role_share("B", "Player B", ROLE_WR, 0.20),
        ],
    )
    # Neither candidate has a MatchupContext read in this dict -- ranking must be unchanged from
    # the role-share-only case, not crash on a missing key.
    candidates = select_stack_candidates(wr, matchup_context_by_player_id={})
    assert [c.player_id for c in candidates] == ["A", "B"]


def test_build_stack_profile_passes_matchup_context_through_to_both_sides() -> None:
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("BUF", composite=60.0)
    # Raw share order is B > A on both rosters -- the multiplier swing (1.15 vs 0.85) is enough to
    # flip that ordering once MatchupContext is combined in, which is the actual point of this test.
    home_wr = _role_share_result(
        "KC",
        ROLE_WR,
        [
            _player_role_share("KC-A", "Home Low Share High Matchup", ROLE_WR, 0.22),
            _player_role_share("KC-B", "Home High Share Bad Matchup", ROLE_WR, 0.24),
        ],
    )
    away_wr = _role_share_result(
        "BUF",
        ROLE_WR,
        [
            _player_role_share("BUF-A", "Away Low Share High Matchup", ROLE_WR, 0.20),
            _player_role_share("BUF-B", "Away High Share Bad Matchup", ROLE_WR, 0.22),
        ],
    )
    matchup_context_by_player_id = {
        "KC-A": _matchup_context("KC-A", 1.15),
        "KC-B": _matchup_context("KC-B", 0.85),
        "BUF-A": _matchup_context("BUF-A", 1.15),
        "BUF-B": _matchup_context("BUF-B", 0.85),
    }

    profile = build_stack_profile(
        ges_home, ges_away, 2.0, home_wr, away_wr, matchup_context_by_player_id=matchup_context_by_player_id
    )

    assert [c.player_id for c in profile.primary_stack_candidates] == ["KC-A", "KC-B"]
    assert [c.player_id for c in profile.bring_back_candidates] == ["BUF-A", "BUF-B"]
    assert "MatchupContext favorability" in profile.pivot_to
    assert any("combined with MatchupContext favorability" in note for note in profile.notes)


def test_build_stack_profile_omits_matchup_context_by_default() -> None:
    ges_home = _ges("KC", composite=80.0)
    ges_away = _ges("BUF", composite=60.0)
    home_wr = _role_share_result(
        "KC",
        ROLE_WR,
        [
            _player_role_share("KC-A", "Player A", ROLE_WR, 0.20),
            _player_role_share("KC-B", "Player B", ROLE_WR, 0.30),
        ],
    )
    away_wr = _role_share_result("BUF", ROLE_WR, [])
    profile = build_stack_profile(ges_home, ges_away, 2.0, home_wr, away_wr)

    # Unchanged, pre-existing behavior when matchup_context_by_player_id isn't supplied at all.
    assert [c.player_id for c in profile.primary_stack_candidates] == ["KC-B", "KC-A"]
    assert "MatchupContext favorability was not supplied" in profile.pivot_to
    assert any("no matchup_context_by_player_id was supplied" in note for note in profile.notes)
