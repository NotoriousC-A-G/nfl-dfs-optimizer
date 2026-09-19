import pytest

from nfl_dfs.analysis.circumstance.depth_chart import DepthChartDivergence, detect_depth_chart_divergence
from nfl_dfs.analysis.circumstance.engine import MessagesResult, synthesize_circumstance
from nfl_dfs.ingestion.nflverse_depth_charts import DepthChartEntry
from nfl_dfs.ingestion.usage_share import PRIOR_LEAGUE_AVERAGE, ROLE_RB, PlayerRoleShare, RoleShareResult


def _depth_chart_entry(team: str, player_id: str, player_name: str, position: str, depth_rank: int) -> DepthChartEntry:
    return DepthChartEntry(
        team=team,
        player_id=player_id,
        player_name=player_name,
        position=position,
        depth_rank=depth_rank,
        snapshot_at="2026-09-19T11:56:08Z",
    )


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


# --------------------------------------------------------------------------------------------
# detect_depth_chart_divergence
# --------------------------------------------------------------------------------------------


def test_detects_a_real_divergence() -> None:
    # The real 2026-09-19 case: depth chart says Jones is #1, but trailing usage still (this
    # example) shows a different player leading.
    jones_dc = _depth_chart_entry("MIN", "jones", "Aaron Jones Sr.", "RB", 1)
    mason_dc = _depth_chart_entry("MIN", "mason", "Jordan Mason", "RB", 4)
    mason_usage = _player_role_share("mason", "J.Mason", 0.50)
    jones_usage = _player_role_share("jones", "A.Jones", 0.49)
    role_share = _role_share_result("MIN", [mason_usage, jones_usage])  # mason still trailing-leads

    divergence = detect_depth_chart_divergence([jones_dc, mason_dc], role_share)

    assert divergence is not None
    assert divergence.team == "MIN"
    assert divergence.depth_chart_leader.player_name == "Aaron Jones Sr."
    assert divergence.usage_leader.player_id == "mason"


def test_returns_none_when_sources_agree() -> None:
    jones_dc = _depth_chart_entry("MIN", "jones", "Aaron Jones Sr.", "RB", 1)
    jones_usage = _player_role_share("jones", "A.Jones", 0.49)
    role_share = _role_share_result("MIN", [jones_usage])

    assert detect_depth_chart_divergence([jones_dc], role_share) is None


def test_returns_none_when_no_trailing_usage_data() -> None:
    jones_dc = _depth_chart_entry("MIN", "jones", "Aaron Jones Sr.", "RB", 1)
    role_share = _role_share_result("MIN", [])  # no trailing candidates at all

    assert detect_depth_chart_divergence([jones_dc], role_share) is None


def test_returns_none_when_no_matching_depth_chart_entry() -> None:
    other_team_dc = _depth_chart_entry("CHI", "someone", "Some CHI RB", "RB", 1)
    jones_usage = _player_role_share("jones", "A.Jones", 0.49)
    role_share = _role_share_result("MIN", [jones_usage])

    assert detect_depth_chart_divergence([other_team_dc], role_share) is None


def test_only_depth_rank_one_counts_as_the_depth_chart_leader() -> None:
    jones_dc = _depth_chart_entry("MIN", "jones", "Aaron Jones Sr.", "RB", 2)  # rank 2, not 1
    mason_usage = _player_role_share("mason", "J.Mason", 0.50)
    role_share = _role_share_result("MIN", [mason_usage])

    assert detect_depth_chart_divergence([jones_dc], role_share) is None


def test_wrong_position_entry_is_ignored() -> None:
    wr_dc = _depth_chart_entry("MIN", "jefferson", "Justin Jefferson", "WR", 1)  # WR, not RB
    mason_usage = _player_role_share("mason", "J.Mason", 0.50)
    role_share = _role_share_result("MIN", [mason_usage])  # role_share.role is ROLE_RB

    assert detect_depth_chart_divergence([wr_dc], role_share) is None


def test_usage_leader_position_carried_through_when_supplied() -> None:
    # Real live case, 2026-09-19: usage_share.py's role is plurality-of-VOLUME, not position-
    # filtered -- a TE with real receiving volume can lead a "WR-role" trailing-usage group.
    jones_dc = _depth_chart_entry("MIN", "jones", "Aaron Jones Sr.", "RB", 1)
    mason_usage = _player_role_share("mason", "J.Mason", 0.50)
    role_share = _role_share_result("MIN", [mason_usage])
    position_by_id = {"mason": "TE"}

    divergence = detect_depth_chart_divergence([jones_dc], role_share, position_by_player_id=position_by_id)

    assert divergence is not None
    assert divergence.usage_leader_position == "TE"


def test_usage_leader_position_none_when_not_supplied() -> None:
    jones_dc = _depth_chart_entry("MIN", "jones", "Aaron Jones Sr.", "RB", 1)
    mason_usage = _player_role_share("mason", "J.Mason", 0.50)
    role_share = _role_share_result("MIN", [mason_usage])

    divergence = detect_depth_chart_divergence([jones_dc], role_share)

    assert divergence is not None
    assert divergence.usage_leader_position is None


# --------------------------------------------------------------------------------------------
# DepthChartDivergence's CircumstanceSource methods
# --------------------------------------------------------------------------------------------


def _divergence() -> DepthChartDivergence:
    jones_dc = _depth_chart_entry("MIN", "jones", "Aaron Jones Sr.", "RB", 1)
    mason_usage = _player_role_share("mason", "J.Mason", 0.50)
    role_share = _role_share_result("MIN", [mason_usage, _player_role_share("jones", "A.Jones", 0.49)])
    divergence = detect_depth_chart_divergence([jones_dc], role_share)
    assert divergence is not None
    return divergence


def test_circumstance_kind_team_season_week() -> None:
    d = _divergence()
    assert d.circumstance_kind() == "depth_chart_divergence"
    assert d.circumstance_team() == "MIN"
    assert d.circumstance_season() == 2026
    assert d.circumstance_week() == 2


def test_circumstance_subjects_is_both_players() -> None:
    assert set(_divergence().circumstance_subjects()) == {"jones", "mason"}


def test_circumstance_facts_is_stable_and_json_able() -> None:
    import json

    facts = _divergence().circumstance_facts()
    json.dumps(facts)  # must not raise -- cache-key material
    assert facts["depth_chart_leader_id"] == "jones"
    assert facts["usage_leader_id"] == "mason"
    assert facts["usage_leader_role_share_blended"] == pytest.approx(0.50)


def test_circumstance_prompt_block_shows_both_real_sources() -> None:
    block = _divergence().circumstance_prompt_block()
    assert "Aaron Jones Sr." in block
    assert "#1 RB" in block
    assert "MIN" in block
    assert "J.Mason" in block
    assert "50%" in block


def test_circumstance_instructions_asks_for_direction_judgment() -> None:
    instructions = _divergence().circumstance_instructions()
    assert "lead usage" in instructions
    assert "lag it" in instructions
    assert "Do not assume the depth chart is automatically right" in instructions
    assert "plurality-of-VOLUME grouping, not position-filtered" in instructions


def test_circumstance_prompt_block_shows_usage_leader_position_when_present() -> None:
    import dataclasses

    d = dataclasses.replace(_divergence(), usage_leader_position="TE")
    assert "real position TE" in d.circumstance_prompt_block()


# --------------------------------------------------------------------------------------------
# End-to-end: detect_depth_chart_divergence's output wired through engine.synthesize_circumstance
# --------------------------------------------------------------------------------------------


class _FakeMessagesClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.last_call: dict | None = None

    def messages_create(self, *, model: str, max_tokens: int, messages: list[dict]) -> MessagesResult:
        self.last_call = {"model": model, "max_tokens": max_tokens, "messages": messages}
        return MessagesResult(self.text, input_tokens=100, output_tokens=50, thinking_tokens=0)


def test_end_to_end_prompt_includes_divergence_block_and_instructions_via_engine() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance(_divergence(), [], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "Aaron Jones Sr." in prompt
    assert "J.Mason" in prompt
    assert "lag it" in prompt
