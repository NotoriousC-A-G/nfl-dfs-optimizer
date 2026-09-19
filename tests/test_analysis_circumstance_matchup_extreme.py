import pytest

from nfl_dfs.analysis.circumstance.engine import MessagesResult, synthesize_circumstance
from nfl_dfs.analysis.circumstance.matchup_extreme import (
    EXTREME_MULTIPLIER_DISTANCE_FRACTION,
    MatchupExtremeCircumstance,
    detect_matchup_extreme_circumstance,
)
from nfl_dfs.matchup.context import MatchupContextResult, MatchupRowResult
from nfl_dfs.matchup.grading import MULTIPLIER_CAP, PASS_PROTECTION_COVERAGE_COMBINED_CAP


def _matchup(
    *,
    position: str,
    combined_multiplier: float,
    team: str = "MIN",
    opponent: str = "CHI",
    player_id: str = "p1",
    run_game: MatchupRowResult | None = None,
    pass_protection: MatchupRowResult | None = None,
    coverage: MatchupRowResult | None = None,
) -> MatchupContextResult:
    return MatchupContextResult(
        canonical_player_id=player_id,
        team=team,
        opponent=opponent,
        position=position,
        combined_multiplier=combined_multiplier,
        run_game=run_game,
        pass_protection=pass_protection,
        coverage=coverage,
        coverage_confidence=None,
    )


# --------------------------------------------------------------------------------------------
# detect_matchup_extreme_circumstance
# --------------------------------------------------------------------------------------------


def test_default_extreme_fraction_constant() -> None:
    assert EXTREME_MULTIPLIER_DISTANCE_FRACTION == pytest.approx(0.95)


def test_detects_favorable_rb_extreme_at_threshold() -> None:
    # RB cap is MULTIPLIER_CAP (0.15); 0.95 * 0.15 = 0.1425 -- exactly at the threshold clears it.
    run_row = MatchupRowResult(label="run_game", multiplier=1.1425, reason="Real run-block vs run-D differential.")
    matchup = _matchup(position="RB", combined_multiplier=1.1425, run_game=run_row)

    circumstance = detect_matchup_extreme_circumstance(matchup, "J.Gibbs", 2026, 2)

    assert circumstance is not None
    assert circumstance.team == "MIN"
    assert circumstance.player_id == "p1"
    assert circumstance.position == "RB"


def test_returns_none_when_below_threshold() -> None:
    matchup = _matchup(position="RB", combined_multiplier=1.05)  # distance 0.05 < 0.1425
    assert detect_matchup_extreme_circumstance(matchup, "J.Gibbs", 2026, 2) is None


def test_detects_unfavorable_extreme_direction_too() -> None:
    matchup = _matchup(position="RB", combined_multiplier=0.85)  # distance 0.15 >= 0.1425
    circumstance = detect_matchup_extreme_circumstance(matchup, "J.Gibbs", 2026, 2)
    assert circumstance is not None


def test_wr_uses_the_larger_combined_cap() -> None:
    # WR/TE cap is PASS_PROTECTION_COVERAGE_COMBINED_CAP (0.20); 0.95 * 0.20 = 0.19.
    # A 0.15 distance clears RB's 0.1425 threshold but NOT WR's 0.19 threshold -- distinguishes them.
    rb_matchup = _matchup(position="RB", combined_multiplier=1.15)
    wr_matchup = _matchup(position="WR", combined_multiplier=1.15, player_id="p2")

    assert detect_matchup_extreme_circumstance(rb_matchup, "Some RB", 2026, 2) is not None
    assert detect_matchup_extreme_circumstance(wr_matchup, "Some WR", 2026, 2) is None


def test_returns_none_for_position_with_no_applicable_cap() -> None:
    matchup = _matchup(position="DST", combined_multiplier=1.5)
    assert detect_matchup_extreme_circumstance(matchup, "Some DST", 2026, 2) is None


def test_custom_extreme_fraction_is_honored() -> None:
    matchup = _matchup(position="RB", combined_multiplier=1.05)  # distance 0.05
    assert detect_matchup_extreme_circumstance(matchup, "J.Gibbs", 2026, 2) is None
    circumstance = detect_matchup_extreme_circumstance(matchup, "J.Gibbs", 2026, 2, extreme_fraction=0.30)
    assert circumstance is not None  # 0.30 * 0.15 = 0.045 <= 0.05


def test_cap_constants_match_real_matchup_grading_module() -> None:
    # Locks in that this module reads the REAL cap constants, not invented ones.
    assert MULTIPLIER_CAP == pytest.approx(0.15)
    assert PASS_PROTECTION_COVERAGE_COMBINED_CAP == pytest.approx(0.20)


# --------------------------------------------------------------------------------------------
# MatchupExtremeCircumstance's CircumstanceSource methods
# --------------------------------------------------------------------------------------------


def _circumstance() -> MatchupExtremeCircumstance:
    run_row = MatchupRowResult(label="run_game", multiplier=1.15, reason="Real run-block vs run-D differential.")
    matchup = _matchup(position="RB", combined_multiplier=1.15, run_game=run_row)
    return MatchupExtremeCircumstance(
        season=2026, week=2, team="MIN", player_id="p1", player_name="J.Gibbs", position="RB", matchup=matchup
    )


def test_circumstance_kind_team_season_week() -> None:
    c = _circumstance()
    assert c.circumstance_kind() == "matchup_extreme"
    assert c.circumstance_team() == "MIN"
    assert c.circumstance_season() == 2026
    assert c.circumstance_week() == 2


def test_circumstance_subjects_is_the_one_player() -> None:
    assert _circumstance().circumstance_subjects() == ["p1"]


def test_circumstance_facts_is_stable_and_json_able() -> None:
    import json

    facts = _circumstance().circumstance_facts()
    json.dumps(facts)  # must not raise -- cache-key material
    assert facts["player_id"] == "p1"
    assert facts["position"] == "RB"
    assert facts["opponent"] == "CHI"
    assert facts["combined_multiplier"] == pytest.approx(1.15)
    assert facts["rows"] == [{"label": "run_game", "multiplier": pytest.approx(1.15), "reason": "Real run-block vs run-D differential."}]


def test_circumstance_facts_changes_when_multiplier_changes() -> None:
    import dataclasses

    a = _circumstance()
    b = dataclasses.replace(a, matchup=_matchup(position="RB", combined_multiplier=0.85))
    assert a.circumstance_facts() != b.circumstance_facts()


def test_circumstance_facts_includes_player_id_so_teammates_never_collide() -> None:
    # Real bug, confirmed live 2026-09-19: run_game/pass_protection multipliers are computed at
    # TEAM level (matchup/context.py), so two teammates at the same position facing the same
    # opponent can share an otherwise-IDENTICAL position/opponent/multiplier/rows combination. Only
    # player_id distinguishes them in the cache key -- without it, a cached POV naming one player
    # would get served under a different teammate's name (81 detected, only 18 real cache files
    # written before this fix).
    run_row = MatchupRowResult(label="run_game", multiplier=1.15, reason="Team-level run-block grade.")
    teammate_a = _matchup(position="RB", combined_multiplier=1.15, run_game=run_row, player_id="rb-a")
    teammate_b = _matchup(position="RB", combined_multiplier=1.15, run_game=run_row, player_id="rb-b")
    circumstance_a = MatchupExtremeCircumstance(
        season=2026, week=2, team="MIN", player_id="rb-a", player_name="Player A", position="RB", matchup=teammate_a
    )
    circumstance_b = MatchupExtremeCircumstance(
        season=2026, week=2, team="MIN", player_id="rb-b", player_name="Player B", position="RB", matchup=teammate_b
    )
    assert circumstance_a.circumstance_facts() != circumstance_b.circumstance_facts()


def test_circumstance_prompt_block_shows_real_numbers_and_driving_row() -> None:
    block = _circumstance().circumstance_prompt_block()
    assert "J.Gibbs" in block
    assert "RB" in block
    assert "CHI" in block
    assert "1.150x" in block
    assert "favorable" in block
    assert "run_game" in block
    assert "Real run-block vs run-D differential." in block


def test_circumstance_prompt_block_labels_unfavorable_direction() -> None:
    matchup = _matchup(position="RB", combined_multiplier=0.85, run_game=MatchupRowResult(label="run_game", multiplier=0.85, reason="Weak run-block grade."))
    c = MatchupExtremeCircumstance(season=2026, week=2, team="MIN", player_id="p1", player_name="J.Gibbs", position="RB", matchup=matchup)
    assert "unfavorable" in c.circumstance_prompt_block()


def test_circumstance_instructions_asks_for_reason_specificity_and_no_invention() -> None:
    instructions = _circumstance().circumstance_instructions()
    assert "specific and" in instructions
    assert "mechanistic" in instructions
    assert "Do not invent" in instructions


# --------------------------------------------------------------------------------------------
# End-to-end: detect_matchup_extreme_circumstance's output wired through engine.synthesize_circumstance
# --------------------------------------------------------------------------------------------


class _FakeMessagesClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.last_call: dict | None = None

    def messages_create(self, *, model: str, max_tokens: int, messages: list[dict]) -> MessagesResult:
        self.last_call = {"model": model, "max_tokens": max_tokens, "messages": messages}
        return MessagesResult(self.text, input_tokens=100, output_tokens=50, thinking_tokens=0)


def test_end_to_end_prompt_includes_matchup_block_and_instructions_via_engine() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance(_circumstance(), [], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "J.Gibbs" in prompt
    assert "run_game" in prompt
    assert "mechanistic" in prompt
