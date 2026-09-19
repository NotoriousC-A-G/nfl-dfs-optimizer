"""Detector-agnostic "detect a real circumstance -> synthesize a reliability-aware, grounded point
of view" engine.

Extracted 2026-09-19 from what was originally `analysis/injury_circumstance.py` (injury-only), when
Chris generalized the direction: "I don't think reasoning should be limited to injuries." Everything
here is deliberately detector-agnostic -- a detector (e.g. `circumstance/injury.py`) produces a real,
typed result implementing `CircumstanceSource`, and this module builds the ONE shared prompt
scaffold and makes the ONE real Anthropic API call. New detector types (matchup-extreme, depth-chart,
...) plug into this same engine rather than re-deriving the reliability-judgment instructions --
that instruction set is the actual load-bearing part of this feature and must not get weaker or
duplicated per detector.

**This is the FIRST live LLM call anywhere in this pipeline's otherwise fully-deterministic build
path** -- a real, disclosed architecture choice, not a pattern to repeat casually for every future
signal in this codebase.

## The three real corrections this prompt scaffold encodes (all confirmed live, 2026-09-19)

1. Show detectors' RAW real numbers, not just a derived/blended one -- a model with only a
   blended summary number will invent a plausible-sounding but unverifiable mechanism to explain
   it (confirmed: "kneel-downs" invented to explain a shrinkage-inflated share).
2. Never hand the model a blanket category rule to apply mechanically (e.g. "QB position means
   discount it") -- that's still a rule standing in for reasoning, just relocated. Point it at real
   knowledge of the specific subject instead.
3. Require judging input RELIABILITY before writing anything, and explicitly prefer a calibrated
   "there isn't enough here to say" over a fluent, confident-sounding answer built past what the
   real inputs support -- otherwise the model degrades into a narration engine no better than a
   template, just slower and non-deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from nfl_dfs.ingestion.odds_api import TEAM_NAME_TO_ABBR
from nfl_dfs.storage.footballguys_article_store import ArchivedArticle

_ABBR_TO_FULL_NAME: dict[str, str] = {abbr: full for full, abbr in TEAM_NAME_TO_ABBR.items()}

# How many of the most-recent matching articles to hand the synthesis step as grounding context --
# small on purpose (prompt-size/cost discipline, and a handful of the freshest articles is more
# useful than every match going back to week 1).
DEFAULT_MAX_ARTICLES = 5
# Per-article truncation for the same reason -- a full article's text is usually far more than the
# synthesis step needs to ground one paragraph.
DEFAULT_MAX_ARTICLE_CHARS = 2000
# NOT a "short answer" budget -- see synthesize_circumstance's own docstring. This prompt's "judge
# reliability first" instruction routinely spends real extended-thinking tokens before any visible
# text, and a too-small max_tokens silently returns an EMPTY response (thinking alone consumes the
# whole budget, stop_reason="max_tokens") rather than an error -- confirmed live 2026-09-19 at
# max_tokens=400. 4000 completes cleanly for every real circumstance tested this session.
DEFAULT_MAX_TOKENS = 4000


def find_relevant_articles(
    team: str, articles: list[ArchivedArticle], *, max_results: int = DEFAULT_MAX_ARTICLES
) -> list[ArchivedArticle]:
    """Every archived Footballguys article (`storage/footballguys_article_store.py`) whose title or
    body mentions `team` by its full name or nickname (e.g. "Minnesota Vikings" or "Vikings" for
    "MIN") -- a real, blunt substring match, not semantic search; reuses `odds_api.py`'s
    `TEAM_NAME_TO_ABBR` (ADR-0011 "reuse before inventing") rather than a new team-name table.

    Returns the most recent `max_results` matches, most-recent-first (by `published_date`), since a
    fresher article is more likely to reflect a real current circumstance than an older one. Team-
    generic (not tied to any one detector type) -- shared by every circumstance kind.

    Returns `[]` (never fabricates a match) when `team` isn't a recognized abbreviation or no
    archived article mentions it -- a caller (`synthesize_circumstance`) must treat an empty result
    as "no real evidence found," never silently proceed as if coverage exists.
    """
    full_name = _ABBR_TO_FULL_NAME.get(team)
    if full_name is None:
        return []
    nickname = full_name.split()[-1]
    matches = [
        a
        for a in articles
        if full_name in a.title or nickname in a.title or full_name in a.text or nickname in a.text
    ]
    matches.sort(key=lambda a: a.published_date or "", reverse=True)
    return matches[:max_results]


@dataclass(frozen=True)
class CircumstanceAssessment:
    """The synthesized point of view plus enough about its OWN inputs (and real cost) that a reader
    can judge how much to trust it and what it cost -- an LLM output never gets presented as a bare
    paragraph with no visibility into what it was, and wasn't, grounded in (this project's "every
    `None` has a reason" discipline, extended here to "every assertion has a visible source").
    """

    kind: str  # which CircumstanceSource produced this -- e.g. "injury", "matchup_extreme"
    pov: str  # the synthesized paragraph itself
    model: str  # exact model id used -- reproducibility/audit trail
    generated_at: str  # ISO-8601 UTC timestamp of the real API call
    evidence_article_titles: list[str]  # titles of the real archived articles actually supplied as
    # grounding context -- [] when none were found, in which case the prompt explicitly told the
    # model no article coverage existed and the resulting pov should read accordingly
    # Real token usage from the ORIGINAL API response that produced this pov -- preserved as-is
    # when this assessment is later served from a cache read (storage/circumstance_cache_store.py),
    # so the real historical cost of generating this text stays visible even on a free re-read. A
    # caller tracking "how many tokens did THIS run actually spend" must check cache hit/miss
    # itself (e.g. via CircumstanceCache.has() before calling synthesize_circumstance), not infer it
    # from these fields. Default 0 only for a caller that never made a real call at all.
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0


class CircumstanceSource(Protocol):
    """Structural interface every circumstance detector's own result type implements (matching this
    module's existing `_MessagesClient(Protocol)` style -- no base class, no generic dict-bag).
    `engine.py` only ever talks to a `CircumstanceSource` through these methods; everything
    detector-specific (what a "departure" or an "extreme" even means) stays in that detector's own
    module.
    """

    def circumstance_kind(self) -> str: ...
    def circumstance_team(self) -> str: ...
    def circumstance_season(self) -> int: ...
    def circumstance_week(self) -> int: ...
    def circumstance_subjects(self) -> list[str]:
        """Player ids (gsis_id-space) this assessment should attach to once synthesized."""
        ...

    def circumstance_facts(self) -> dict:
        """A stable, JSON-able bag of the real facts this circumstance was detected from -- the
        cache-key material (`storage/circumstance_cache_store.py`). Must change whenever a fact that
        would change the synthesized answer changes (e.g. a status flip, a multiplier shift), so the
        cache naturally invalidates without any TTL/expiry logic."""
        ...

    def circumstance_prompt_block(self) -> str:
        """This detector's own real-inputs text, pre-formatted (mirrors the old `_candidate_line`
        pattern) -- spliced verbatim into the shared prompt template."""
        ...

    def circumstance_instructions(self) -> str:
        """This detector's own reliability/anomaly guardrails -- e.g. injury's "don't guess at a
        play-by-play mechanism, don't apply a blanket position rule" text. Spliced into the shared
        template alongside (not instead of) the engine's own generic reliability instruction."""
        ...


class MessagesResult:
    """Real response text plus real token usage from one `messages.create` call -- returned by
    `_MessagesClient.messages_create` instead of a bare `str` so cost is never silently discarded."""

    __slots__ = ("text", "input_tokens", "output_tokens", "thinking_tokens")

    def __init__(self, text: str, *, input_tokens: int, output_tokens: int, thinking_tokens: int) -> None:
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.thinking_tokens = thinking_tokens


class _MessagesClient(Protocol):
    """The one method this module actually calls on an `anthropic.Anthropic`-shaped client --
    named here so `synthesize_circumstance` can be unit-tested against a small fake instead of a
    real network call, without importing `anthropic`'s own types into this module's signature."""

    def messages_create(self, *, model: str, max_tokens: int, messages: list[dict]) -> MessagesResult: ...


class CircumstanceCache(Protocol):
    """Structural interface for a real-vs-cached read/write of a `CircumstanceAssessment`, keyed on
    a `CircumstanceSource`'s own facts (`storage/circumstance_cache_store.py`'s real filesystem
    implementation). Named here, not in the storage module, so `engine.py` stays independent of any
    concrete storage backend -- same posture as `_MessagesClient`. `synthesize_circumstance` never
    calls the real API for a circumstance whose facts haven't changed since the last real call."""

    def has(self, source: CircumstanceSource) -> bool: ...
    def read(self, source: CircumstanceSource) -> CircumstanceAssessment: ...
    def write(self, source: CircumstanceSource, assessment: CircumstanceAssessment) -> None: ...


def _build_prompt(source: CircumstanceSource, articles: list[ArchivedArticle], *, max_article_chars: int) -> str:
    team = source.circumstance_team()
    if articles:
        evidence_block = "\n\n".join(
            f"### {a.title} ({a.published_date or 'undated'})\n{a.text[:max_article_chars]}" for a in articles
        )
        evidence_instruction = (
            "Ground your answer in the article excerpts above where they're relevant, and say which "
            "article(s) informed any specific claim you make."
        )
    else:
        evidence_block = "(No archived Footballguys articles mention this team.)"
        evidence_instruction = (
            "No article coverage was found for this team. Say so explicitly, and base your answer "
            "ONLY on the real inputs above -- do not imply you read any coverage."
        )

    return f"""You are helping a DFS (daily fantasy sports) lineup builder understand WHY a projection
might be what it is, not predict a new number yourself.

Real, current circumstance for {team}, {source.circumstance_season()} week {source.circumstance_week()}
({source.circumstance_kind()}):
{source.circumstance_prompt_block()}

Real Footballguys article coverage found for {team}:
{evidence_block}

FIRST, before writing anything, judge how reliable each input actually is -- this is the whole point
of asking you rather than just printing the numbers above. A small sample, thin or tangential
evidence, or an input that doesn't really fit the situation deserves real skepticism, not a
confident-sounding story built on top of it. {source.circumstance_instructions()}

THEN write a short (2-4 sentence) point of view on how {team} is likely to handle this situation this
week. If, after that assessment, the real inputs above don't actually support a meaningful point of
view -- too small a sample, no relevant coverage, or both -- say that plainly as your answer; a
calibrated "there isn't enough here to say" is a MORE useful answer than a fluent-sounding one built
past what the data supports. {evidence_instruction}
Do not invent player names, stats, or sources not given above. Do not give betting or financial
advice -- this is about expected on-field role/usage only."""


def synthesize_circumstance(
    source: CircumstanceSource,
    articles: list[ArchivedArticle],
    *,
    client: _MessagesClient,
    model: str = "claude-sonnet-5",
    max_article_chars: int = DEFAULT_MAX_ARTICLE_CHARS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    cache: CircumstanceCache | None = None,
    force_refresh: bool = False,
) -> CircumstanceAssessment:
    """Calls `client.messages_create(...)` with a prompt grounded exclusively in `source` (a
    detector's own real detection result) and `articles` (real archived Footballguys coverage, from
    `find_relevant_articles`) -- never asked to invent a player, a stat, or a source it wasn't
    given. `client` is injected (a thin wrapper around `anthropic.Anthropic().messages.create`, see
    `build_anthropic_messages_client`), not constructed here, so this function is unit-testable
    against a fake without a real network call or API key.

    `max_tokens` defaults to `DEFAULT_MAX_TOKENS` (4000) -- see the module-level constant's own
    comment for why a smaller budget silently produces an empty response on this prompt shape.

    `cache`, when supplied (`storage/circumstance_cache_store.py`'s real filesystem implementation,
    or a fake in tests), avoids a real API call entirely when a cached assessment already exists for
    this EXACT `source.circumstance_facts()` -- content-addressed, so a genuine fact change (a
    status flip, a multiplier shift) naturally produces a cache miss with no TTL/expiry logic. This
    is the fix for the real, confirmed-live cost problem of every dashboard re-run resynthesizing
    every identical circumstance from scratch (2026-09-19: ~8 re-runs in one day, ~35-63k tokens
    each, all producing the exact same output). `force_refresh=True` skips the cache READ (always
    makes a real call) but still WRITES the fresh result, for a caller that explicitly wants an
    up-to-date read regardless of what's cached (e.g. new article evidence became available, which
    the cache key deliberately does NOT track -- see `circumstance_cache_store.py`'s own docstring).
    """
    if cache is not None and not force_refresh and cache.has(source):
        return cache.read(source)

    prompt = _build_prompt(source, articles, max_article_chars=max_article_chars)
    result = client.messages_create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    if not result.text.strip():
        # Never silently ship an empty assessment -- confirmed live 2026-09-19 this happens
        # specifically when max_tokens is exhausted by extended thinking before any visible text is
        # written. A blank CircumstanceAssessment would render as an empty box in the dashboard with
        # no indication anything went wrong -- loud failure here instead.
        raise RuntimeError(
            f"synthesize_circumstance got an empty response for {source.circumstance_team()} "
            f"{source.circumstance_kind()} (model={model!r}, max_tokens={max_tokens}) -- likely the "
            "whole max_tokens budget was consumed by extended thinking before any visible text was "
            "written. Try a larger max_tokens."
        )
    assessment = CircumstanceAssessment(
        kind=source.circumstance_kind(),
        pov=result.text,
        model=model,
        generated_at=datetime.now(timezone.utc).isoformat(),
        evidence_article_titles=[a.title for a in articles],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        thinking_tokens=result.thinking_tokens,
    )
    if cache is not None:
        cache.write(source, assessment)
    return assessment


def build_anthropic_messages_client(api_key: str):  # pragma: no cover -- thin real-SDK wrapper
    """Real `anthropic.Anthropic`-backed `_MessagesClient` -- the only place this module imports
    `anthropic` itself, so every other function here (and every unit test) stays independent of the
    real SDK/network. Not covered by the unit suite (this project's live-call boundary, same posture
    as `ingestion/*.py`'s own `requests`-calling functions) -- exercised by live verification instead.
    """
    import anthropic

    real_client = anthropic.Anthropic(api_key=api_key)

    class _RealMessagesClient:
        def messages_create(self, *, model: str, max_tokens: int, messages: list[dict]) -> MessagesResult:
            response = real_client.messages.create(model=model, max_tokens=max_tokens, messages=messages)
            text = "".join(block.text for block in response.content if block.type == "text")
            usage = response.usage
            details = usage.output_tokens_details
            thinking_tokens = getattr(details, "thinking_tokens", 0) if details is not None else 0
            return MessagesResult(
                text,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                thinking_tokens=thinking_tokens or 0,
            )

    return _RealMessagesClient()
