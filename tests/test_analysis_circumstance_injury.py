import dataclasses

import pytest

from nfl_dfs.analysis.circumstance.engine import MessagesResult, synthesize_circumstance
from nfl_dfs.analysis.circumstance.injury import (
    DEPARTED_SHARE_FLOOR,
    CircumstanceChange,
    detect_injury_circumstance_change,
)
from nfl_dfs.ingestion.usage_share import PRIOR_LEAGUE_AVERAGE, ROLE_RB, PlayerRoleShare, RoleShareResult


def _player_role_share(
    player_id: str, player_name: str, role_share_blended: float, *, role_tier: str | None = "mid_tier"
) -> PlayerRoleShare:
    return PlayerRoleShare(
        player_id=player_id,
        player_name=player_name,
        role=ROLE_RB,
        weeks_played=4,
        trailing_volume=40,
        trailing_team_volume=100,
        trailing_share=0.40,
        shrinkage_weight=0.4,
        role_share_blended=role_share_blended,
        role_tier=role_tier,
        prior_used=PRIOR_LEAGUE_AVERAGE,
    )


def _role_share_result(team: str, candidates: list[PlayerRoleShare]) -> RoleShareResult:
    return RoleShareResult(
        season=2026,
        week=2,
        team=team,
        role=ROLE_RB,
        candidates=candidates,
        identified=candidates[0] if candidates else None,
        gate_passed=bool(candidates),
    )


def _change() -> CircumstanceChange:
    mason = _player_role_share("mason", "J.Mason", 0.50)
    jones = _player_role_share("jones", "A.Jones", 0.49)
    return CircumstanceChange(
        season=2026, week=2, team="MIN", role=ROLE_RB, departed=mason, departed_status="OUT", remaining=[jones]
    )


# --------------------------------------------------------------------------------------------
# detect_injury_circumstance_change
# --------------------------------------------------------------------------------------------


def test_detects_out_teammate_with_real_share_and_a_real_remaining_candidate() -> None:
    mason = _player_role_share("mason", "J.Mason", 0.50)
    jones = _player_role_share("jones", "A.Jones", 0.49)
    role_share = _role_share_result("MIN", [mason, jones])
    status_by_id = {"mason": "OUT", "jones": None}

    change = detect_injury_circumstance_change(role_share, status_by_id)

    assert change is not None
    assert change.team == "MIN"
    assert change.role == ROLE_RB
    assert change.departed is mason
    assert change.departed_status == "OUT"
    assert change.remaining == [jones]


def test_returns_none_when_nobody_is_out() -> None:
    mason = _player_role_share("mason", "J.Mason", 0.50)
    jones = _player_role_share("jones", "A.Jones", 0.49)
    role_share = _role_share_result("MIN", [mason, jones])
    status_by_id = {"mason": "Q", "jones": None}

    assert detect_injury_circumstance_change(role_share, status_by_id) is None


def test_returns_none_when_out_players_share_is_below_the_floor() -> None:
    bit_part = _player_role_share("bit", "Bit Part", 0.10, role_tier="committee")
    jones = _player_role_share("jones", "A.Jones", 0.49)
    role_share = _role_share_result("MIN", [jones, bit_part])
    status_by_id = {"bit": "OUT", "jones": None}

    assert detect_injury_circumstance_change(role_share, status_by_id) is None


def test_returns_none_when_every_remaining_candidate_is_also_out() -> None:
    mason = _player_role_share("mason", "J.Mason", 0.50)
    jones = _player_role_share("jones", "A.Jones", 0.49)
    role_share = _role_share_result("MIN", [mason, jones])
    status_by_id = {"mason": "OUT", "jones": "IR"}

    assert detect_injury_circumstance_change(role_share, status_by_id) is None


def test_picks_the_most_consequential_departure_when_multiple_are_out() -> None:
    # candidates are already sorted descending by role_share_blended (usage_share.py's contract) --
    # the first OUT/IR candidate clearing the floor is the one detected, not every one.
    mason = _player_role_share("mason", "J.Mason", 0.50)
    bit_part = _player_role_share("bit", "Bit Part", 0.22, role_tier="committee")
    jones = _player_role_share("jones", "A.Jones", 0.15, role_tier="committee")
    role_share = _role_share_result("MIN", [mason, bit_part, jones])
    status_by_id = {"mason": "OUT", "bit": "OUT", "jones": None}

    change = detect_injury_circumstance_change(role_share, status_by_id)

    assert change is not None
    assert change.departed is mason
    assert change.remaining == [jones]  # bit_part is also OUT -- excluded from remaining too


def test_custom_departed_share_floor_is_honored() -> None:
    small_role = _player_role_share("small", "Small Role", 0.15, role_tier="committee")
    jones = _player_role_share("jones", "A.Jones", 0.49)
    role_share = _role_share_result("MIN", [jones, small_role])
    status_by_id = {"small": "OUT", "jones": None}

    assert detect_injury_circumstance_change(role_share, status_by_id) is None
    change = detect_injury_circumstance_change(role_share, status_by_id, departed_share_floor=0.10)
    assert change is not None
    assert change.departed is small_role


def test_default_departed_share_floor_constant() -> None:
    assert DEPARTED_SHARE_FLOOR == pytest.approx(0.20)


# --------------------------------------------------------------------------------------------
# detect_injury_circumstance_change -- position_by_player_id as a DEPARTED-only correctness gate
# (confirmed live 2026-09-19: Carson Wentz, MIN's real backup QB, named as an "RB-role teammate";
# reconsidered same day -- position data informs the model's own reasoning, it doesn't pre-filter
# what the model gets to see. See detect_injury_circumstance_change's own docstring.)
# --------------------------------------------------------------------------------------------


def test_qb_never_becomes_the_detected_departure_when_position_data_supplied() -> None:
    # A backup QB with real trailing "RB-role" volume who gets hurt should never itself become the
    # detected departure -- there's no real RB circumstance here, just that QB's own scramble volume.
    wentz = _player_role_share("wentz", "C.Wentz", 0.44, role_tier="committee")
    jones = _player_role_share("jones", "A.Jones", 0.30, role_tier="mid_tier")
    role_share = _role_share_result("MIN", [wentz, jones])
    status_by_id = {"wentz": "OUT", "jones": None}
    position_by_id = {"wentz": "QB", "jones": "RB"}

    assert detect_injury_circumstance_change(role_share, status_by_id, position_by_player_id=position_by_id) is None


def test_remaining_is_never_filtered_by_position_even_when_position_data_supplied() -> None:
    # A QB anomaly among the REMAINING candidates is deliberately left in place -- see the module
    # docstring for why this isn't silently pre-filtered the way the departed check is.
    mason = _player_role_share("mason", "J.Mason", 0.50)
    jones = _player_role_share("jones", "A.Jones", 0.49)
    wentz = _player_role_share("wentz", "C.Wentz", 0.44, role_tier="committee")
    role_share = _role_share_result("MIN", [mason, jones, wentz])
    status_by_id = {"mason": "OUT", "jones": None, "wentz": None}
    position_by_id = {"mason": "RB", "jones": "RB", "wentz": "QB"}

    change = detect_injury_circumstance_change(role_share, status_by_id, position_by_player_id=position_by_id)

    assert change is not None
    assert change.remaining == [jones, wentz]
    assert change.position_by_player_id == position_by_id


def test_without_position_data_departure_detection_is_unaffected() -> None:
    mason = _player_role_share("mason", "J.Mason", 0.50)
    jones = _player_role_share("jones", "A.Jones", 0.49)
    role_share = _role_share_result("MIN", [mason, jones])
    status_by_id = {"mason": "OUT", "jones": None}

    change = detect_injury_circumstance_change(role_share, status_by_id)

    assert change is not None
    assert change.departed is mason
    assert change.position_by_player_id is None


# --------------------------------------------------------------------------------------------
# CircumstanceChange's CircumstanceSource methods (engine.CircumstanceSource protocol)
# --------------------------------------------------------------------------------------------


def test_circumstance_kind_team_season_week() -> None:
    change = _change()
    assert change.circumstance_kind() == "injury"
    assert change.circumstance_team() == "MIN"
    assert change.circumstance_season() == 2026
    assert change.circumstance_week() == 2


def test_circumstance_subjects_is_remaining_player_ids() -> None:
    change = _change()
    assert change.circumstance_subjects() == ["jones"]


def test_circumstance_facts_is_stable_and_json_able() -> None:
    import json

    change = _change()
    facts = change.circumstance_facts()
    json.dumps(facts)  # must not raise -- this is cache-key material (storage/circumstance_cache_store.py)
    assert facts["departed_player_id"] == "mason"
    assert facts["departed_status"] == "OUT"
    assert facts["remaining"] == [{"player_id": "jones", "role_share_blended": pytest.approx(0.49)}]


def test_circumstance_facts_changes_when_departed_status_changes() -> None:
    # The whole point of using real facts as cache-key material: a genuine status flip (Q -> OUT)
    # must produce a different key, so a stale cached assessment is never served for a new fact.
    change_out = dataclasses.replace(_change(), departed_status="OUT")
    change_ir = dataclasses.replace(_change(), departed_status="IR")
    assert change_out.circumstance_facts() != change_ir.circumstance_facts()


def test_circumstance_prompt_block_shows_departed_and_remaining_with_real_numbers() -> None:
    change = _change()
    block = change.circumstance_prompt_block()

    assert "J.Mason" in block
    assert "OUT" in block
    assert "A.Jones" in block
    assert "50%" in block
    assert "49%" in block
    assert "raw trailing:" in block


def test_circumstance_prompt_block_shows_position_when_supplied() -> None:
    change = dataclasses.replace(_change(), position_by_player_id={"mason": "RB", "jones": "RB"})
    block = change.circumstance_prompt_block()
    assert "position RB" in block


def test_circumstance_instructions_contains_anomaly_and_reliability_guardrails() -> None:
    instructions = _change().circumstance_instructions()
    assert "do NOT apply a blanket" in instructions
    assert "Lamar Jackson" in instructions
    assert "real playing style" in instructions
    assert "do not guess at a specific play-by-play mechanism" in instructions


# --------------------------------------------------------------------------------------------
# End-to-end: detect_injury_circumstance_change's output wired through engine.synthesize_circumstance
# -- confirms the injury.py <-> engine.py split still composes into the same real prompt content.
# --------------------------------------------------------------------------------------------


class _FakeMessagesClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.last_call: dict | None = None

    def messages_create(self, *, model: str, max_tokens: int, messages: list[dict]) -> MessagesResult:
        self.last_call = {"model": model, "max_tokens": max_tokens, "messages": messages}
        return MessagesResult(self.text, input_tokens=100, output_tokens=50, thinking_tokens=0)


def test_end_to_end_prompt_shows_position_and_anomaly_instruction_via_engine() -> None:
    client = _FakeMessagesClient("pov text")
    wentz = _player_role_share("wentz", "C.Wentz", 0.44, role_tier="committee")
    change = dataclasses.replace(
        _change(),
        remaining=[*_change().remaining, wentz],
        position_by_player_id={"mason": "RB", "jones": "RB", "wentz": "QB"},
    )

    synthesize_circumstance(change, [], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "C.Wentz" in prompt  # the anomalous candidate IS shown to the model, not hidden
    assert "position QB" in prompt
    assert "position RB" in prompt
    assert "do NOT apply a blanket" in prompt
    assert "Lamar Jackson" in prompt


def test_end_to_end_prompt_names_departed_and_remaining_players() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance(_change(), [], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "J.Mason" in prompt
    assert "OUT" in prompt
    assert "A.Jones" in prompt
    assert "50%" in prompt
    assert "49%" in prompt
