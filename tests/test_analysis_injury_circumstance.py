import dataclasses

import pytest

from nfl_dfs.analysis.injury_circumstance import (
    DEPARTED_SHARE_FLOOR,
    CircumstanceChange,
    detect_injury_circumstance_change,
    find_relevant_articles,
    synthesize_circumstance_pov,
)
from nfl_dfs.ingestion.usage_share import PRIOR_LEAGUE_AVERAGE, ROLE_RB, PlayerRoleShare, RoleShareResult
from nfl_dfs.storage.footballguys_article_store import ArchivedArticle


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


def _article(slug: str, title: str, text: str, *, published_date: str = "2026-09-18") -> ArchivedArticle:
    return ArchivedArticle(
        slug=slug,
        url=f"https://www.footballguys.com/article/{slug}",
        title=title,
        author="Some Author",
        published_date=published_date,
        category_ids=[7],
        tags=[],
        text=text,
        fetched_at="2026-09-18T12:00:00Z",
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


def test_without_position_data_departure_detection_is_unaffected() -> None:
    mason = _player_role_share("mason", "J.Mason", 0.50)
    jones = _player_role_share("jones", "A.Jones", 0.49)
    role_share = _role_share_result("MIN", [mason, jones])
    status_by_id = {"mason": "OUT", "jones": None}

    change = detect_injury_circumstance_change(role_share, status_by_id)

    assert change is not None
    assert change.departed is mason


# --------------------------------------------------------------------------------------------
# _candidate_line / synthesize_circumstance_pov -- position shown as context, not filtered
# --------------------------------------------------------------------------------------------


def test_synthesize_circumstance_pov_prompt_shows_position_and_anomaly_instruction() -> None:
    client = _FakeMessagesClient("pov text")
    change = _change()  # departed=J.Mason, remaining=[A.Jones]
    change = dataclasses.replace(
        change,
        remaining=[*change.remaining, _player_role_share("wentz", "C.Wentz", 0.44, role_tier="committee")],
    )
    position_by_id = {"mason": "RB", "jones": "RB", "wentz": "QB"}

    synthesize_circumstance_pov(change, [], client=client, position_by_player_id=position_by_id)

    prompt = client.last_call["messages"][0]["content"]
    assert "C.Wentz" in prompt  # the anomalous candidate IS shown to the model, not hidden
    assert "position QB" in prompt
    assert "position RB" in prompt
    assert "showing up in an RB-role list" in prompt
    assert "do not guess at a specific play-by-play mechanism" in prompt
    # Raw trailing share + sample size + shrinkage weight are shown alongside the blended number --
    # confirmed live 2026-09-19: without these, the model invented an unverifiable "kneel-downs"
    # explanation for a small-sample QB's inflated blended share instead of reasoning from real data.
    assert "raw trailing:" in prompt
    # Position mismatch must NOT be handled as a blanket "QB means discount" rule either (Chris,
    # 2026-09-19: "Wentz isn't going to take on a significant percentage... that's not just because
    # of his position but because of how he plays. The answer could be different for another QB
    # [Jackson, Willis]") -- the model is pointed at its own knowledge of the specific named player.
    assert "do NOT apply a blanket" in prompt
    assert "Lamar Jackson" in prompt
    assert "real playing style" in prompt
    assert "shrinkage weight" in prompt


def test_synthesize_circumstance_pov_prompt_omits_position_when_not_supplied() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance_pov(_change(), [], client=client)

    candidate_lines = client.last_call["messages"][0]["content"].split("\n")
    assert not any(", position " in line for line in candidate_lines)


# --------------------------------------------------------------------------------------------
# find_relevant_articles
# --------------------------------------------------------------------------------------------


def test_find_relevant_articles_matches_full_team_name_or_nickname() -> None:
    vikings_article = _article("a1", "Minnesota Vikings backfield notes", "Some body text.")
    nickname_only = _article("a2", "Week 2 waiver wire", "The Vikings are expected to lean on...")
    unrelated = _article("a3", "Packers notes", "Nothing about MIN here.")

    matches = find_relevant_articles("MIN", [vikings_article, nickname_only, unrelated])

    assert vikings_article in matches
    assert nickname_only in matches
    assert unrelated not in matches


def test_find_relevant_articles_most_recent_first() -> None:
    older = _article("a1", "Vikings early notes", "text", published_date="2026-09-10")
    newer = _article("a2", "Vikings latest", "text", published_date="2026-09-18")

    matches = find_relevant_articles("MIN", [older, newer])

    assert matches == [newer, older]


def test_find_relevant_articles_respects_max_results() -> None:
    articles = [_article(f"a{i}", "Vikings notes", "text", published_date=f"2026-09-{10+i:02d}") for i in range(8)]
    matches = find_relevant_articles("MIN", articles, max_results=3)
    assert len(matches) == 3


def test_find_relevant_articles_empty_for_unrecognized_team() -> None:
    assert find_relevant_articles("ZZZ", [_article("a1", "Vikings notes", "text")]) == []


def test_find_relevant_articles_empty_when_no_match() -> None:
    unrelated = _article("a1", "Packers notes", "Nothing about the other team.")
    assert find_relevant_articles("MIN", [unrelated]) == []


# --------------------------------------------------------------------------------------------
# synthesize_circumstance_pov
# --------------------------------------------------------------------------------------------


class _FakeMessagesClient:
    """Records the exact prompt it was called with -- no network, no real API key needed."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.last_call: dict | None = None

    def messages_create(self, *, model: str, max_tokens: int, messages: list[dict]) -> str:
        self.last_call = {"model": model, "max_tokens": max_tokens, "messages": messages}
        return self.response_text


def _change() -> CircumstanceChange:
    mason = _player_role_share("mason", "J.Mason", 0.50)
    jones = _player_role_share("jones", "A.Jones", 0.49)
    return CircumstanceChange(
        season=2026, week=2, team="MIN", role=ROLE_RB, departed=mason, departed_status="OUT", remaining=[jones]
    )


def test_synthesize_circumstance_pov_returns_real_client_text_and_metadata() -> None:
    client = _FakeMessagesClient("Jones should see an expanded workhorse role this week.")
    articles = [_article("a1", "Vikings backfield notes", "Jones expected to see more work.")]

    assessment = synthesize_circumstance_pov(_change(), articles, client=client)

    assert assessment.pov == "Jones should see an expanded workhorse role this week."
    assert assessment.model == "claude-sonnet-5"
    assert assessment.evidence_article_titles == ["Vikings backfield notes"]
    assert assessment.generated_at  # a real timestamp was stamped, not left blank


def test_synthesize_circumstance_pov_prompt_names_departed_and_remaining_players() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance_pov(_change(), [], client=client)

    assert client.last_call is not None
    prompt = client.last_call["messages"][0]["content"]
    assert "J.Mason" in prompt
    assert "OUT" in prompt
    assert "A.Jones" in prompt
    assert "50%" in prompt
    assert "49%" in prompt


def test_synthesize_circumstance_pov_prompt_discloses_when_no_articles_found() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance_pov(_change(), [], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "No archived Footballguys articles mention this team" in prompt
    assert "do not imply you read any coverage" in prompt


def test_synthesize_circumstance_pov_prompt_requires_reliability_judgment_before_writing() -> None:
    # The whole point of an LLM step over a template: it must judge whether the real inputs given
    # actually support a meaningful conclusion, not just narrate whatever numbers it's handed.
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance_pov(_change(), [], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "judge how reliable each input actually is" in prompt
    assert "whole point" in prompt and "just printing the numbers" in prompt
    assert 'a calibrated "there isn' in prompt  # explicitly permits/prefers a low-confidence answer


def test_synthesize_circumstance_pov_prompt_includes_article_text_when_found() -> None:
    client = _FakeMessagesClient("pov text")
    articles = [_article("a1", "Vikings backfield notes", "Real excerpt body text about the backfield.")]
    synthesize_circumstance_pov(_change(), articles, client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "Vikings backfield notes" in prompt
    assert "Real excerpt body text about the backfield." in prompt


def test_synthesize_circumstance_pov_custom_model_is_passed_through() -> None:
    client = _FakeMessagesClient("pov text")
    assessment = synthesize_circumstance_pov(_change(), [], client=client, model="claude-opus-5")

    assert client.last_call["model"] == "claude-opus-5"
    assert assessment.model == "claude-opus-5"


def test_synthesize_circumstance_pov_default_max_tokens_is_passed_through() -> None:
    from nfl_dfs.analysis.injury_circumstance import DEFAULT_MAX_TOKENS

    client = _FakeMessagesClient("pov text")
    synthesize_circumstance_pov(_change(), [], client=client)

    assert client.last_call["max_tokens"] == DEFAULT_MAX_TOKENS


def test_synthesize_circumstance_pov_custom_max_tokens_is_passed_through() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance_pov(_change(), [], client=client, max_tokens=1234)

    assert client.last_call["max_tokens"] == 1234


def test_synthesize_circumstance_pov_raises_loudly_on_empty_response() -> None:
    # Confirmed live 2026-09-19: an under-budgeted max_tokens gets entirely consumed by extended
    # thinking, returning an empty string with no error -- this must never ship silently as a blank
    # CircumstanceAssessment (an empty box in the dashboard with no indication anything went wrong).
    client = _FakeMessagesClient("")
    with pytest.raises(RuntimeError, match="empty response"):
        synthesize_circumstance_pov(_change(), [], client=client)


def test_synthesize_circumstance_pov_raises_on_whitespace_only_response() -> None:
    client = _FakeMessagesClient("   \n  ")
    with pytest.raises(RuntimeError, match="empty response"):
        synthesize_circumstance_pov(_change(), [], client=client)
