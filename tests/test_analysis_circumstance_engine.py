from datetime import date

import pytest

from nfl_dfs.analysis.circumstance.engine import (
    DEFAULT_MAX_TOKENS,
    MessagesResult,
    find_relevant_articles,
    synthesize_circumstance,
)
from nfl_dfs.storage.footballguys_article_store import ArchivedArticle
from nfl_dfs.storage.rotogrinders_article_store import RotoGrindersArchivedArticle


def _article(
    slug: str, title: str, text: str, *, published_date: str = "2026-09-18", tags: list[str] | None = None
) -> ArchivedArticle:
    return ArchivedArticle(
        slug=slug,
        url=f"https://www.footballguys.com/article/{slug}",
        title=title,
        author="Some Author",
        published_date=published_date,
        category_ids=[7],
        tags=tags or [],
        text=text,
        fetched_at="2026-09-18T12:00:00Z",
    )


def _rg_article(
    slug: str,
    title: str,
    text: str,
    *,
    published_date: str = "2026-09-18",
    platform_mixed: bool = True,
    is_gated: bool = False,
) -> RotoGrindersArchivedArticle:
    return RotoGrindersArchivedArticle(
        slug=slug,
        url=f"https://rotogrinders.com/articles/{slug}",
        title=title,
        author="Some Author",
        published_date=published_date,
        tags=[],
        text=text,
        fetched_at="2026-09-18T12:00:00Z",
        is_gated=is_gated,
        platform_mixed=platform_mixed,
    )


# --------------------------------------------------------------------------------------------
# find_relevant_articles -- team-generic, shared by every circumstance kind
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
# find_relevant_articles -- real, hard week-exclusion (confirmed live 2026-09-19: without this, a
# thin-current-coverage team's results backfilled with real Week 1 "Cracking DraftKings" pricing
# pieces -- genuinely stale, week-specific DFS analysis with no signal distinguishing it from real
# current coverage. Chris: "we need to be week aware... pulling sentiment from stale analysis would
# be a killer.")
# --------------------------------------------------------------------------------------------

_AS_OF = date(2026, 9, 19)


def test_wrong_week_article_excluded_via_clean_week_tag() -> None:
    # The exact real case: a real "Cracking DraftKings" Week 1 piece must never surface for Week 2.
    week1 = _article("a1", "Cracking DraftKings Week 1", "Vikings pricing notes.", tags=["week 1"])
    week2 = _article("a2", "Cracking DraftKings Week 2", "Vikings pricing notes.", tags=["week 2"])

    matches = find_relevant_articles("MIN", [week1, week2], week=2, as_of=_AS_OF)

    assert week2 in matches
    assert week1 not in matches


def test_wrong_week_article_excluded_via_title_mention_when_no_clean_tag() -> None:
    week1 = _article("a1", "NFL Week 1 Injury Report", "Vikings injury notes.", tags=["Injuries", "news"])
    matches = find_relevant_articles("MIN", [week1], week=2, as_of=_AS_OF)
    assert matches == []


def test_evergreen_article_with_no_determinable_week_is_kept_when_recent() -> None:
    evergreen = _article("a1", "24 Receivers Who Changed My Mind", "Vikings receiver notes.", tags=["strategy"])
    matches = find_relevant_articles("MIN", [evergreen], week=2, as_of=_AS_OF)
    assert evergreen in matches


def test_same_week_number_prior_season_is_excluded_by_recency_window() -> None:
    # A real "week 2" tag from a PRIOR season must not pass just because the week number matches --
    # published_date puts it far outside the recency window relative to as_of.
    stale_season = _article("a1", "Week 2 Old Notes", "Vikings notes.", published_date="2024-09-15", tags=["week 2"])
    current_season = _article("a2", "Week 2 New Notes", "Vikings notes.", published_date="2026-09-18", tags=["week 2"])

    matches = find_relevant_articles("MIN", [stale_season, current_season], week=2, as_of=_AS_OF)

    assert current_season in matches
    assert stale_season not in matches


def test_evergreen_but_stale_article_excluded_by_recency_window_even_without_a_week_tag() -> None:
    # Real gap confirmed live 2026-09-19 (Chris: "all these articles list the date that it was
    # published"): 62 real archived articles are genuinely evergreen-STYLE pieces from a stale
    # prior year with no week tag/title mention at all -- these must not slip through just because
    # _article_week can't determine a week number for them.
    stale_evergreen = _article(
        "a1", "Derrick Henry: Are We Sure He's Still a Workhorse?", "Vikings notes.", published_date="2024-07-18"
    )
    matches = find_relevant_articles("MIN", [stale_evergreen], week=2, as_of=_AS_OF)
    assert matches == []


def test_prior_season_playoffs_excluded_despite_same_calendar_year() -> None:
    # Real gap confirmed live 2026-09-19: 105 real January-2026 articles ("Cracking DraftKings Wild
    # Card Weekend") are dated "2026" despite being from the PRIOR season's playoffs, not the
    # current regular season -- a plain calendar-year check would wrongly keep these. The day-based
    # recency window has no such calendar-year seam.
    playoffs = _article(
        "a1", "Cracking DraftKings Wild Card Weekend", "Vikings notes.", published_date="2026-01-09", tags=["week 2"]
    )
    matches = find_relevant_articles("MIN", [playoffs], week=2, as_of=_AS_OF)
    assert matches == []


def test_custom_recency_window_is_honored() -> None:
    borderline = _article("a1", "Vikings notes from a while back", "Vikings notes.", published_date="2026-08-15")
    assert find_relevant_articles("MIN", [borderline], week=2, as_of=_AS_OF) == []  # ~35 days > default 21
    matches = find_relevant_articles("MIN", [borderline], week=2, as_of=_AS_OF, recency_window_days=60)
    assert borderline in matches


# --------------------------------------------------------------------------------------------
# find_relevant_articles -- always excludes FanDuel-platform-specific content (Chris: "be careful
# about mixing in fanduel content... different scoring. I primarily play on DraftKings")
# --------------------------------------------------------------------------------------------


def test_fanduel_titled_article_is_excluded() -> None:
    fanduel = _article("a1", "Cracking FanDuel Week 2", "Vikings pricing notes.", tags=["week 2"])
    assert find_relevant_articles("MIN", [fanduel]) == []


def test_draftkings_titled_article_is_kept() -> None:
    dk = _article("a1", "Cracking DraftKings Week 2", "Vikings pricing notes.", tags=["week 2"])
    assert dk in find_relevant_articles("MIN", [dk])


def test_fanduel_tag_alone_does_not_exclude_a_real_dk_article() -> None:
    # Real case confirmed live: "DraftKings Thursday Showdown" is genuinely DK-specific content but
    # is ALSO tagged "FanDuel" as a broad cross-platform DFS tag -- tag-based exclusion would
    # wrongly drop it. Only the TITLE is checked.
    dk_but_fanduel_tagged = _article(
        "a1", "DraftKings Thursday Showdown: Week 2", "Vikings notes.", tags=["week 2", "DraftKings", "FanDuel"]
    )
    assert dk_but_fanduel_tagged in find_relevant_articles("MIN", [dk_but_fanduel_tagged])


def test_week_filtering_is_opt_in_omitting_week_keeps_pre_existing_behavior() -> None:
    week1 = _article("a1", "Cracking DraftKings Week 1", "Vikings pricing notes.", tags=["week 1"])
    matches = find_relevant_articles("MIN", [week1])  # no season/week supplied
    assert week1 in matches


# --------------------------------------------------------------------------------------------
# synthesize_circumstance -- tested against a minimal fake CircumstanceSource, not any real
# detector's own dataclass, to confirm the engine is genuinely detector-agnostic (2026-09-19,
# Chris: "I don't think reasoning should be limited to injuries").
# --------------------------------------------------------------------------------------------


class _FakeSource:
    def __init__(
        self,
        *,
        kind: str = "test_kind",
        team: str = "MIN",
        season: int = 2026,
        week: int = 2,
        subjects: list[str] | None = None,
        facts: dict | None = None,
        prompt_block: str = "- Some Player: a real fact",
        instructions: str = "Some detector-specific guardrail text.",
    ) -> None:
        self._kind = kind
        self._team = team
        self._season = season
        self._week = week
        self._subjects = subjects or ["p1"]
        self._facts = facts or {"x": 1}
        self._prompt_block = prompt_block
        self._instructions = instructions

    def circumstance_kind(self) -> str:
        return self._kind

    def circumstance_team(self) -> str:
        return self._team

    def circumstance_season(self) -> int:
        return self._season

    def circumstance_week(self) -> int:
        return self._week

    def circumstance_subjects(self) -> list[str]:
        return self._subjects

    def circumstance_facts(self) -> dict:
        return self._facts

    def circumstance_prompt_block(self) -> str:
        return self._prompt_block

    def circumstance_instructions(self) -> str:
        return self._instructions


class _FakeMessagesClient:
    """Records the exact prompt it was called with -- no network, no real API key needed."""

    def __init__(self, text: str, *, input_tokens: int = 100, output_tokens: int = 50, thinking_tokens: int = 0) -> None:
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.thinking_tokens = thinking_tokens
        self.last_call: dict | None = None

    def messages_create(self, *, model: str, max_tokens: int, messages: list[dict]) -> MessagesResult:
        self.last_call = {"model": model, "max_tokens": max_tokens, "messages": messages}
        return MessagesResult(
            self.text,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            thinking_tokens=self.thinking_tokens,
        )


def test_synthesize_circumstance_returns_real_text_and_metadata() -> None:
    client = _FakeMessagesClient("Jones should see an expanded workhorse role this week.")
    articles = [_article("a1", "Vikings backfield notes", "Jones expected to see more work.")]

    assessment = synthesize_circumstance(_FakeSource(), articles, client=client)

    assert assessment.pov == "Jones should see an expanded workhorse role this week."
    assert assessment.kind == "test_kind"
    assert assessment.model == "claude-sonnet-5"
    assert assessment.evidence_article_titles == ["Vikings backfield notes"]
    assert assessment.generated_at  # a real timestamp was stamped, not left blank


def test_synthesize_circumstance_carries_real_token_usage() -> None:
    client = _FakeMessagesClient("pov text", input_tokens=5074, output_tokens=770, thinking_tokens=200)
    assessment = synthesize_circumstance(_FakeSource(), [], client=client)

    assert assessment.input_tokens == 5074
    assert assessment.output_tokens == 770
    assert assessment.thinking_tokens == 200


def test_synthesize_circumstance_prompt_includes_source_prompt_block_and_instructions() -> None:
    client = _FakeMessagesClient("pov text")
    source = _FakeSource(prompt_block="- Real Fact Line", instructions="Unique guardrail sentence XYZ.")

    synthesize_circumstance(source, [], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "Real Fact Line" in prompt
    assert "Unique guardrail sentence XYZ." in prompt
    assert "MIN" in prompt
    assert "test_kind" in prompt


def test_synthesize_circumstance_prompt_discloses_when_no_articles_found() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance(_FakeSource(), [], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "No archived articles mention this team" in prompt
    assert "do not imply you read any coverage" in prompt


def test_synthesize_circumstance_prompt_includes_article_text_when_found() -> None:
    client = _FakeMessagesClient("pov text")
    articles = [_article("a1", "Vikings backfield notes", "Real excerpt body text about the backfield.")]
    synthesize_circumstance(_FakeSource(), articles, client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "Vikings backfield notes" in prompt
    assert "Real excerpt body text about the backfield." in prompt


def test_synthesize_circumstance_prompt_requires_reliability_judgment_before_writing() -> None:
    # The whole point of an LLM step over a template: it must judge whether the real inputs given
    # actually support a meaningful conclusion, not just narrate whatever numbers it's handed. This
    # is genuinely detector-agnostic -- lives in the shared engine, not any one detector's prompt.
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance(_FakeSource(), [], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "judge how reliable each input actually is" in prompt
    assert "whole point" in prompt and "just printing the numbers" in prompt
    assert 'calibrated "there isn' in prompt  # explicitly permits/prefers a low-confidence answer


def test_synthesize_circumstance_custom_model_is_passed_through() -> None:
    client = _FakeMessagesClient("pov text")
    assessment = synthesize_circumstance(_FakeSource(), [], client=client, model="claude-opus-5")

    assert client.last_call["model"] == "claude-opus-5"
    assert assessment.model == "claude-opus-5"


def test_synthesize_circumstance_default_max_tokens_is_passed_through() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance(_FakeSource(), [], client=client)

    assert client.last_call["max_tokens"] == DEFAULT_MAX_TOKENS


def test_synthesize_circumstance_custom_max_tokens_is_passed_through() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance(_FakeSource(), [], client=client, max_tokens=1234)

    assert client.last_call["max_tokens"] == 1234


def test_synthesize_circumstance_raises_loudly_on_empty_response() -> None:
    # Confirmed live 2026-09-19: an under-budgeted max_tokens gets entirely consumed by extended
    # thinking, returning an empty string with no error -- this must never ship silently as a blank
    # CircumstanceAssessment (an empty box in the dashboard with no indication anything went wrong).
    client = _FakeMessagesClient("")
    with pytest.raises(RuntimeError, match="empty response"):
        synthesize_circumstance(_FakeSource(), [], client=client)


def test_synthesize_circumstance_raises_on_whitespace_only_response() -> None:
    client = _FakeMessagesClient("   \n  ")
    with pytest.raises(RuntimeError, match="empty response"):
        synthesize_circumstance(_FakeSource(), [], client=client)


# --------------------------------------------------------------------------------------------
# synthesize_circumstance -- cache integration (2026-09-19, the real token-burn fix: repeated
# live-script runs within the same week were resynthesizing every identical circumstance)
# --------------------------------------------------------------------------------------------


class _FakeCache:
    """In-memory CircumstanceCache -- no filesystem needed to test the engine's own cache logic
    (the real filesystem implementation is tested separately, storage/circumstance_cache_store.py)."""

    def __init__(self) -> None:
        self._store: dict[str, "CircumstanceAssessment"] = {}
        self.has_calls = 0
        self.read_calls = 0
        self.write_calls = 0

    def _key(self, source) -> str:
        return f"{source.circumstance_kind()}:{source.circumstance_team()}:{source.circumstance_facts()}"

    def has(self, source) -> bool:
        self.has_calls += 1
        return self._key(source) in self._store

    def read(self, source):
        self.read_calls += 1
        return self._store[self._key(source)]

    def write(self, source, assessment) -> None:
        self.write_calls += 1
        self._store[self._key(source)] = assessment


def test_synthesize_circumstance_cache_miss_calls_client_and_writes_through() -> None:
    cache = _FakeCache()
    client = _FakeMessagesClient("Real synthesized text.")

    assessment = synthesize_circumstance(_FakeSource(), [], client=client, cache=cache)

    assert client.last_call is not None  # a real call WAS made
    assert assessment.pov == "Real synthesized text."
    assert cache.write_calls == 1


def test_synthesize_circumstance_cache_hit_never_calls_client() -> None:
    cache = _FakeCache()
    client = _FakeMessagesClient("Real synthesized text.")
    source = _FakeSource()

    first = synthesize_circumstance(source, [], client=client, cache=cache)
    client.last_call = None  # reset -- confirm the SECOND call makes no new real call at all
    second = synthesize_circumstance(source, [], client=client, cache=cache)

    assert client.last_call is None  # no real API call happened on the cache hit
    assert second == first
    assert cache.write_calls == 1  # only the first (real) call ever wrote


def test_synthesize_circumstance_force_refresh_bypasses_cache_read_but_still_writes() -> None:
    cache = _FakeCache()
    client = _FakeMessagesClient("Fresh text.")
    source = _FakeSource()
    stale = synthesize_circumstance(source, [], client=_FakeMessagesClient("Old text."), cache=None)
    cache.write(source, stale)  # seed a stale cache entry directly, one write

    assessment = synthesize_circumstance(source, [], client=client, cache=cache, force_refresh=True)

    assert client.last_call is not None  # force_refresh made a real call despite a cache entry existing
    assert assessment.pov == "Fresh text."
    assert cache.write_calls == 2  # the seed write, then the force-refreshed result overwrites it
    assert cache.read(source).pov == "Fresh text."  # the stale entry was genuinely replaced


def test_synthesize_circumstance_without_cache_always_calls_client() -> None:
    client = _FakeMessagesClient("pov text")
    synthesize_circumstance(_FakeSource(), [], client=client)  # cache=None, the default
    assert client.last_call is not None


# --------------------------------------------------------------------------------------------
# Multi-source article evidence (RotoGrinders added 2026-09-20 alongside Footballguys) --
# find_relevant_articles/synthesize_circumstance must work over a MIXED list from both sources,
# and a RotoGrinders article's platform_mixed flag must be disclosed in the synthesis prompt.
# --------------------------------------------------------------------------------------------


def test_find_relevant_articles_matches_across_footballguys_and_rotogrinders_together() -> None:
    fb_article = _article("fb1", "Minnesota Vikings backfield notes", "Some body text.")
    rg_article = _rg_article("rg1", "NFL DFS Picks: Vikings stack for Week 2", "Some body text.")
    unrelated = _article("fb2", "Packers notes", "Nothing about MIN here.")

    matches = find_relevant_articles("MIN", [fb_article, rg_article, unrelated])

    assert {a.slug for a in matches} == {"fb1", "rg1"}


def test_synthesize_circumstance_discloses_platform_mixed_rotogrinders_evidence() -> None:
    client = _FakeMessagesClient("pov text")
    mixed = _rg_article("rg1", "NFL DFS Picks: DK & FD Survey", "Some Vikings-relevant body text.")

    synthesize_circumstance(_FakeSource(), [mixed], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "mixes DraftKings and FanDuel advice" in prompt
    assert "weigh any platform-specific detail here with real caution" in prompt


def test_synthesize_circumstance_does_not_flag_a_footballguys_article_as_platform_mixed() -> None:
    client = _FakeMessagesClient("pov text")
    clean = _article("fb1", "Cracking DraftKings Week 2", "Some Vikings-relevant body text.")

    synthesize_circumstance(_FakeSource(), [clean], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "mixes DraftKings and FanDuel advice" not in prompt


def test_synthesize_circumstance_does_not_flag_a_rotogrinders_article_marked_dk_only() -> None:
    client = _FakeMessagesClient("pov text")
    dk_only = _rg_article("rg1", "NFL DFS Picks: DraftKings-only cash plays", "Body text.", platform_mixed=False)

    synthesize_circumstance(_FakeSource(), [dk_only], client=client)

    prompt = client.last_call["messages"][0]["content"]
    assert "mixes DraftKings and FanDuel advice" not in prompt
