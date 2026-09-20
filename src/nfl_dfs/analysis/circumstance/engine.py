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

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

from nfl_dfs.ingestion.odds_api import TEAM_NAME_TO_ABBR

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

# Real judgment call, flagged as a draft starting value per this project's ADR-0019/ADR-0020
# convention (not backtested): how many days back an article can be published and still count as
# "recent enough" to ground a real current-week circumstance, when it has no determinable week
# (`_article_week`) of its own to check directly. ~3 weeks is generous enough to catch a real
# current-week piece published slightly early, tight enough to exclude prior-season/offseason
# content. Confirmed live 2026-09-19 this genuinely matters: a plain calendar-YEAR check (the
# module's first version of this fix) let 105 real January-2026 articles through -- e.g. "Cracking
# DraftKings Wild Card Weekend" -- because they're dated "2026" despite being from the PRIOR NFL
# season's playoffs, not the current 2026 regular season. A day-based window relative to a real
# reference date doesn't have that calendar-year seam.
DEFAULT_RECENCY_WINDOW_DAYS = 21


_WEEK_TAG_RE = re.compile(r"(?i)^week\s*(\d+)$")
_WEEK_MENTION_RE = re.compile(r"(?i)\bweek\s*(\d+)\b")


class ArticleEvidence(Protocol):
    """Structural interface any article-evidence source implements (added 2026-09-20 when
    RotoGrinders became a second real source alongside Footballguys -- `storage/
    footballguys_article_store.ArchivedArticle` and `storage/rotogrinders_article_store.
    RotoGrindersArchivedArticle` both satisfy this without either needing to inherit from
    anything, same structural-typing style as `CircumstanceSource` above). `find_relevant_articles`
    accepts a mixed list from multiple sources -- everything below only ever reads these four
    fields, never anything source-specific.
    """

    title: str
    published_date: str | None
    tags: list[str]
    text: str


def _article_week(article: ArticleEvidence) -> int | None:
    """Best-effort REAL week number this article is ABOUT (not just when it was published) --
    prefers the clean `"week N"` tag (confirmed live 2026-09-19: present on ~82% of real September
    2026 articles, exact values `"week 1"`/`"week 2"`), falling back to a "week N" mention in the
    title when no clean tag exists. `None` means this article isn't clearly about one specific week
    (evergreen/draft-strategy/"changed my mind" content, or a genuinely untagged piece) -- treated
    as week-agnostic by `find_relevant_articles`, not excluded and not assumed current either.
    """
    for tag in article.tags:
        match = _WEEK_TAG_RE.match(tag.strip())
        if match:
            return int(match.group(1))
    match = _WEEK_MENTION_RE.search(article.title)
    if match:
        return int(match.group(1))
    return None


_FANDUEL_TITLE_RE = re.compile(r"(?i)\bfanduel\b")
_DRAFTKINGS_TITLE_RE = re.compile(r"(?i)\bdraftkings\b")


def _is_fanduel_specific(article: ArticleEvidence) -> bool:
    """True when this article's own TITLE identifies it as FanDuel-ONLY content, never mentioning
    DraftKings at all (real "Cracking FanDuel"/"FanDuel GPP Guide"/"FanDuel Top 10" style pieces --
    different salary cap, different scoring/bonus rules than DraftKings). Confirmed live
    2026-09-19: among Footballguys' own archive, title is a clean, reliable signal for this -- zero
    real Footballguys articles have both "FanDuel" and "DraftKings" in the title, so this check
    never had to distinguish "FanDuel-only" from "mentions FanDuel too." That changed once
    RotoGrinders became a second source (2026-09-20): its real "DraftKings & FanDuel Expert
    Survey"-style titles genuinely name both, and this function correctly does NOT exclude them
    (they're not FanDuel-ONLY) -- `RotoGrindersArchivedArticle.platform_mixed` is the separate,
    dedicated signal for "this DOES mix platforms, weigh it accordingly," not a reason to also
    make THIS function stricter. TAGS are deliberately NOT used for this check: many genuinely
    DK-specific Footballguys pieces ("DraftKings Thursday Showdown", "Vegas Value Chart") are ALSO
    tagged "FanDuel" as a broad cross-platform DFS tag, so a tag-based check would wrongly drop
    real DK-relevant content. This project is DraftKings-only by design (Chris: "I primarily play
    on DraftKings"), so this is an unconditional exclusion in `find_relevant_articles`, not an
    opt-in.
    """
    return bool(_FANDUEL_TITLE_RE.search(article.title)) and not _DRAFTKINGS_TITLE_RE.search(article.title)


def find_relevant_articles(
    team: str,
    articles: list[ArticleEvidence],
    *,
    max_results: int = DEFAULT_MAX_ARTICLES,
    week: int | None = None,
    as_of: date | None = None,
    recency_window_days: int = DEFAULT_RECENCY_WINDOW_DAYS,
) -> list[ArticleEvidence]:
    """Every archived article (Footballguys, RotoGrinders, or any future `ArticleEvidence`
    source -- callers merge multiple archives into one `articles` list, this function itself has
    no source-specific logic) whose title or body mentions `team` by its full name or nickname
    (e.g. "Minnesota Vikings" or "Vikings" for "MIN") -- a real, blunt substring match, not
    semantic search; reuses `odds_api.py`'s `TEAM_NAME_TO_ABBR` (ADR-0011 "reuse before
    inventing") rather than a new team-name table.

    **Always excludes FanDuel-platform-specific content** -- see `_is_fanduel_specific`. This
    project is DraftKings-only; FanDuel's salary/scoring rules differ enough that a FanDuel-specific
    value/pricing take isn't reliable evidence for a DK decision.

    **`week`, when supplied, is a real hard exclusion, not a ranking nudge** (confirmed live
    2026-09-19: without this, a thin-current-coverage team's top-5 backfilled with real Week 1
    "Cracking DraftKings" pricing pieces -- genuinely stale, week-specific DFS analysis handed to
    the synthesis step with no signal distinguishing it from real current coverage. Chris: "we need
    to be week aware... pulling sentiment from stale analysis would be a killer."):

    - An article whose real week (`_article_week` -- the clean `"week N"` tag, or a "week N" title
      mention) is determinable and does NOT equal `week` is dropped.
    - **Every** article -- including one with NO determinable week -- must also be published within
      `recency_window_days` of `as_of` (real calendar days, default `DEFAULT_RECENCY_WINDOW_DAYS`
      -- confirmed live 2026-09-19 this matters on its own, Chris: "all these articles list the date
      that it was published": a plain calendar-YEAR check let 105 real January-2026 "Wild Card
      Weekend"/"Divisional Round" articles through, since they're dated "2026" despite being from
      the PRIOR season's playoffs, not the current regular season -- a day-based window relative to
      a real reference date has no such calendar-year seam). `as_of` defaults to the real current
      UTC date; pass an explicit value for a reproducible/testable reference point. An article with
      no `published_date` at all is kept (never fabricate a "too old" exclusion from missing data).

    Returns the most recent `max_results` matches (after the above exclusions), most-recent-first
    (by `published_date`). Team-generic (not tied to any one detector type) -- shared by every
    circumstance kind.

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
        if (full_name in a.title or nickname in a.title or full_name in a.text or nickname in a.text)
        and not _is_fanduel_specific(a)
    ]
    if week is not None:
        reference = as_of if as_of is not None else datetime.now(timezone.utc).date()
        matches = [a for a in matches if _matches_target_week(a, week, as_of=reference, window_days=recency_window_days)]
    matches.sort(key=lambda a: a.published_date or "", reverse=True)
    return matches[:max_results]


def _matches_target_week(article: ArticleEvidence, week: int, *, as_of: date, window_days: int) -> bool:
    if article.published_date:
        try:
            published = date.fromisoformat(article.published_date)
        except ValueError:
            published = None
        if published is not None and (as_of - published).days > window_days:
            return False

    article_week = _article_week(article)
    if article_week is None:
        return True  # recent enough (or undated), but no determinable week -- kept as evergreen
    return article_week == week


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


def _article_evidence_header(a: ArticleEvidence) -> str:
    """`### Title (date)` plus a disclosed caveat when the source marks itself as not cleanly
    DraftKings-specific (`platform_mixed`, added 2026-09-20 for RotoGrinders -- `getattr` with a
    default rather than an `isinstance` check, so this stays source-agnostic: any future
    `ArticleEvidence` source can opt into the same disclosure just by carrying this attribute,
    without `engine.py` needing to know that source's concrete type).
    """
    header = f"### {a.title} ({a.published_date or 'undated'})"
    if getattr(a, "platform_mixed", False):
        header += " [NOTE: this source mixes DraftKings and FanDuel advice without separating " \
            "which pick applies to which platform -- this project is DraftKings-only, so weigh " \
            "any platform-specific detail here with real caution]"
    return header


def _build_prompt(source: CircumstanceSource, articles: list[ArticleEvidence], *, max_article_chars: int) -> str:
    team = source.circumstance_team()
    if articles:
        evidence_block = "\n\n".join(
            f"{_article_evidence_header(a)}\n{a.text[:max_article_chars]}" for a in articles
        )
        evidence_instruction = (
            "Ground your answer in the article excerpts above where they're relevant, and say which "
            "article(s) informed any specific claim you make. Where an excerpt is flagged as mixing "
            "DraftKings and FanDuel advice, treat any platform-specific claim from it with extra "
            "skepticism rather than assuming it applies to DraftKings."
        )
    else:
        evidence_block = "(No archived articles mention this team.)"
        evidence_instruction = (
            "No article coverage was found for this team. Say so explicitly, and base your answer "
            "ONLY on the real inputs above -- do not imply you read any coverage."
        )

    return f"""You are helping a DFS (daily fantasy sports) lineup builder understand WHY a projection
might be what it is, not predict a new number yourself.

Real, current circumstance for {team}, {source.circumstance_season()} week {source.circumstance_week()}
({source.circumstance_kind()}):
{source.circumstance_prompt_block()}

Real article coverage found for {team}:
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
    articles: list[ArticleEvidence],
    *,
    client: _MessagesClient,
    model: str = "claude-sonnet-5",
    max_article_chars: int = DEFAULT_MAX_ARTICLE_CHARS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    cache: CircumstanceCache | None = None,
    force_refresh: bool = False,
) -> CircumstanceAssessment:
    """Calls `client.messages_create(...)` with a prompt grounded exclusively in `source` (a
    detector's own real detection result) and `articles` (real archived article coverage from any
    `ArticleEvidence` source -- Footballguys, RotoGrinders, or a mix, from
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
