"""Injury-driven role-share circumstance detection + real-evidence-grounded point-of-view
synthesis.

Chris's framing (2026-09-19), in his own words:
1. The injury forces a change.
2. Our content-consumption layer should help us build a point of view on how the team will handle
   the situation.
3. Projections tell us what we think is going to happen -- we should be able to see the reason for
   the projection to be what it is.

**Distinct from ADR-0020's `ConcurrentActivityWindow`** (RB role only, `ingestion/usage_share.py`):
that mechanism only corrects RETROACTIVE dilution -- excluding a PAST trailing week where a
teammate was blanked from the denominator, so an already-healthy back's trailing share isn't
artificially deflated by weeks the committee mate was actually out. Nothing in `usage_share.py` has
any awareness of a teammate's CURRENT-week injury status: `RoleShareResult` is built purely from
completed pbp through `week - 1`. This module is the first place that circumstance gets detected
and reasoned about at all -- a genuinely different problem, not a duplicate of ADR-0020's fix.

Two layers, deliberately kept separate and independently testable:

1. **Deterministic circumstance detection** (`detect_injury_circumstance_change`) -- a pure
   function, no LLM, no network: given a `RoleShareResult` and DraftKings' own real roster-status
   map (the same `dk_injury_status` every `PlayerProjection` already carries,
   `projection/blend.py`), finds a teammate at the same role with real trailing volume now ruled
   OUT/IR (`EXCLUDED_INJURY_STATUSES`, `optimizer/lineup.py`, reused not reinvented) alongside the
   remaining candidate(s) whose outlook that circumstance plausibly changes. This is "the point to
   evaluate," not yet a point of view.

2. **LLM-synthesized point of view** (`synthesize_circumstance_pov`) -- Chris's explicit choice
   (2026-09-19, over a plain evidence-surfacing alternative he was also offered): a real Anthropic
   API call, grounded ONLY in the real inputs this module and the article archive
   (`storage/footballguys_article_store.py`, Chris's own 2026-09-19 build) actually supply --
   instructed not to invent a player, a stat, or a source it wasn't given. **This is the FIRST live
   LLM call anywhere in this pipeline's otherwise fully-deterministic build path** -- a real,
   disclosed architecture choice, not a pattern to repeat casually for every future signal in this
   codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from nfl_dfs.ingestion.odds_api import TEAM_NAME_TO_ABBR
from nfl_dfs.ingestion.usage_share import PlayerRoleShare, RoleShareResult
from nfl_dfs.optimizer.lineup import EXCLUDED_INJURY_STATUSES
from nfl_dfs.storage.footballguys_article_store import ArchivedArticle

_ABBR_TO_FULL_NAME: dict[str, str] = {abbr: full for full, abbr in TEAM_NAME_TO_ABBR.items()}

# Real judgment call, flagged as a draft starting value per this project's ADR-0019/ADR-0020
# convention (not backtested against history): a departed teammate only counts as a real
# "circumstance change" when they held real trailing volume -- not a bit-part committee back whose
# departure barely moves anything. Deliberately lower than usage_share.py's own MIDTIER_THRESHOLD
# (0.45, the bar for CROWNING someone a lead back) -- a departure worth SURFACING (this module's
# job) is a lower bar than a departure worth naming a new lead back.
DEPARTED_SHARE_FLOOR = 0.20

# How many of the most-recent matching articles to hand the synthesis step as grounding context --
# small on purpose (prompt-size/cost discipline, and a handful of the freshest articles is more
# useful than every match going back to week 1).
DEFAULT_MAX_ARTICLES = 5
# Per-article truncation for the same reason -- a full article's text is usually far more than the
# synthesis step needs to ground one paragraph.
DEFAULT_MAX_ARTICLE_CHARS = 2000
# NOT a "short answer" budget -- see synthesize_circumstance_pov's own docstring. This prompt's
# "judge reliability first" instruction routinely spends real extended-thinking tokens before any
# visible text, and a too-small max_tokens silently returns an EMPTY pov (thinking alone consumes
# the whole budget, stop_reason="max_tokens") rather than an error -- confirmed live 2026-09-19 at
# max_tokens=400. 4000 completes cleanly for every real circumstance tested this session.
DEFAULT_MAX_TOKENS = 4000


@dataclass(frozen=True)
class CircumstanceChange:
    """One detected real, current circumstance: a teammate with real trailing role share at this
    `(team, role)` is ruled OUT/IR for the week being projected, and at least one teammate at the
    same role remains eligible. This is the deterministic "point to evaluate" -- see
    `detect_injury_circumstance_change`.
    """

    season: int
    week: int
    team: str
    role: str
    departed: PlayerRoleShare  # the OUT/IR teammate, with their real trailing role share
    departed_status: str  # DraftKings' own real roster-status code (e.g. "OUT", "IR")
    remaining: list[PlayerRoleShare]  # every OTHER real trailing-volume candidate at this
    # (team, role), in role_share.candidates's existing role_share_blended order -- not just the
    # single highest, since more than one teammate's outlook can plausibly be affected


def detect_injury_circumstance_change(
    role_share: RoleShareResult,
    dk_injury_status_by_player_id: dict[str, str | None],
    *,
    departed_share_floor: float = DEPARTED_SHARE_FLOOR,
    position_by_player_id: dict[str, str] | None = None,
) -> CircumstanceChange | None:
    """Finds the most consequential real, current-week injury circumstance at this `(team, role)`,
    or `None` when there isn't one.

    `role_share.candidates` is already sorted descending by `role_share_blended`
    (`usage_share.py`'s own contract) -- the first candidate whose DraftKings status is in
    `EXCLUDED_INJURY_STATUSES` ("IR"/"OUT") AND whose trailing share clears
    `departed_share_floor` is the departure this returns. Only the single most consequential
    departure is detected per call -- a second, smaller same-week departure at the same role is a
    genuinely rarer case this function doesn't try to layer reasoning on top of.

    Returns `None` (never fabricates a circumstance) when: no candidate clears both the injury-
    status and share-floor checks, or every remaining candidate is ALSO OUT/IR (nobody's outlook is
    left to reason about). `dk_injury_status_by_player_id` is keyed by the same id space
    `PlayerRoleShare.player_id` uses (nflverse `gsis_id`) -- a caller sourcing this from
    `PlayerProjection.dk_injury_status` (`projection/blend.py`) should key it by `canonical_id`,
    which equals `gsis_id` for these roles in the common case (ADR-0013).

    `position_by_player_id`, when supplied (same gsis_id-space key -- e.g. built from the
    reconciled `PlayerIdentity` pool's own real `position` field), is used ONLY to decide whether a
    candidate can be the DEPARTED player -- a real position mismatch (a confirmed `"QB"`) means
    this isn't a genuine RB/WR-role circumstance at all, just that backup QB's own usage pattern
    (`usage_share.py`'s RB/WR role computation is plurality-of-volume, not position-filtered, that
    module's own disclosed "Judgment call 2" -- a backup QB's kneel/scramble carries can genuinely
    clear an RB-role `RoleShareResult`'s candidate list). This is a mechanical correctness check on
    whether to fire a circumstance AT ALL, not an interpretive judgment.

    **Deliberately NOT applied to `remaining`** (confirmed live 2026-09-19, then reconsidered same
    day): an earlier version of this function also filtered `remaining` by position, silently
    dropping an anomalous candidate (Carson Wentz, MIN's real backup QB) before the reasoning step
    ever saw him. That's exactly the wrong instinct this project has been pushing back against --
    deciding "this candidate doesn't count" in code is still a rule standing in for reasoning, just
    a quieter one. `remaining` stays unfiltered; `synthesize_circumstance_pov` is instead given
    real position data for every remaining candidate so the model can reason about an anomaly like
    this itself (and say so in its own POV), rather than have it invisibly disappear upstream.
    """
    departed_candidate: PlayerRoleShare | None = None
    for candidate in role_share.candidates:
        status = dk_injury_status_by_player_id.get(candidate.player_id)
        is_real_role_position = (
            position_by_player_id is None or position_by_player_id.get(candidate.player_id) != "QB"
        )
        if (
            status in EXCLUDED_INJURY_STATUSES
            and candidate.role_share_blended >= departed_share_floor
            and is_real_role_position
        ):
            departed_candidate = candidate
            break
    if departed_candidate is None:
        return None

    remaining = [
        c
        for c in role_share.candidates
        if c.player_id != departed_candidate.player_id
        and dk_injury_status_by_player_id.get(c.player_id) not in EXCLUDED_INJURY_STATUSES
    ]
    if not remaining:
        return None

    return CircumstanceChange(
        season=role_share.season,
        week=role_share.week,
        team=role_share.team,
        role=role_share.role,
        departed=departed_candidate,
        departed_status=dk_injury_status_by_player_id[departed_candidate.player_id],
        remaining=remaining,
    )


def find_relevant_articles(
    team: str, articles: list[ArchivedArticle], *, max_results: int = DEFAULT_MAX_ARTICLES
) -> list[ArchivedArticle]:
    """Every archived Footballguys article (`storage/footballguys_article_store.py`) whose title or
    body mentions `team` by its full name or nickname (e.g. "Minnesota Vikings" or "Vikings" for
    "MIN") -- a real, blunt substring match, not semantic search; reuses `odds_api.py`'s
    `TEAM_NAME_TO_ABBR` (ADR-0011 "reuse before inventing") rather than a new team-name table.

    Returns the most recent `max_results` matches, most-recent-first (by `published_date`), since a
    fresher article is more likely to reflect this week's real circumstance than an older one.

    Returns `[]` (never fabricates a match) when `team` isn't a recognized abbreviation or no
    archived article mentions it -- a caller (`synthesize_circumstance_pov`) must treat an empty
    result as "no real evidence found," never silently proceed as if coverage exists.
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
    """The synthesized point of view (Chris's explicit choice, 2026-09-19) plus enough about its
    OWN inputs that a reader can judge how much to trust it -- an LLM output never gets presented as
    a bare paragraph with no visibility into what it was, and wasn't, grounded in (this project's
    "every `None` has a reason" discipline, extended here to "every assertion has a visible
    source").
    """

    pov: str  # the synthesized paragraph itself
    model: str  # exact model id used -- reproducibility/audit trail
    generated_at: str  # ISO-8601 UTC timestamp of the real API call
    evidence_article_titles: list[str]  # titles of the real archived articles actually supplied as
    # grounding context -- [] when none were found, in which case the prompt explicitly told the
    # model no article coverage existed and the resulting pov should read accordingly


class _MessagesClient(Protocol):
    """The one method this module actually calls on an `anthropic.Anthropic`-shaped client --
    named here so `synthesize_circumstance_pov` can be unit-tested against a small fake instead of
    a real network call, without importing `anthropic`'s own types into this module's signature."""

    def messages_create(self, *, model: str, max_tokens: int, messages: list[dict]) -> str: ...


def _candidate_line(candidate: PlayerRoleShare, position_by_player_id: dict[str, str] | None) -> str:
    """One candidate's real inputs, position included when known -- the model reasons about
    whether a candidate's numbers deserve full weight (e.g. a backup QB's real position showing up
    in an RB-role RoleShareResult, `usage_share.py`'s own disclosed non-position-filtered
    computation) itself, from real data, rather than that judgment being made silently in code
    before it ever sees the candidate. See `detect_injury_circumstance_change`'s docstring for why
    `remaining` is deliberately NOT filtered by position the way `departed` detection is.

    **Shows the RAW trailing share alongside the shrinkage-BLENDED one, not just the blended
    number alone (confirmed live 2026-09-19: showing only `role_share_blended` led the model to
    invent an unverifiable specific mechanism -- "kneel-downs" -- to explain a small-sample QB's
    44% blended share, when the real explanation was visible in data it simply wasn't shown: 2 real
    carries over 1 tracked week (raw share 6.5%), shrinkage-blended 86% of the way toward the
    league-wide RB prior. Giving the model the real raw share, sample size, and shrinkage weight
    lets it explain the actual divergence instead of guessing a plausible-sounding one.**
    """
    position = (position_by_player_id or {}).get(candidate.player_id)
    position_note = f", position {position}" if position else ""
    tier_note = f", role tier {candidate.role_tier}" if candidate.role_tier else ""
    return (
        f"- {candidate.player_name or candidate.player_id}: {candidate.role_share_blended:.0%} blended "
        f"role share (raw trailing: {candidate.trailing_volume}/{candidate.trailing_team_volume} = "
        f"{candidate.trailing_share:.0%} over {candidate.weeks_played} tracked week(s), shrinkage weight "
        f"{candidate.shrinkage_weight:.2f} toward the league-wide prior){tier_note}{position_note}"
    )


def _build_prompt(
    change: CircumstanceChange,
    articles: list[ArchivedArticle],
    *,
    max_article_chars: int,
    position_by_player_id: dict[str, str] | None = None,
) -> str:
    departed_line = (
        f"{_candidate_line(change.departed, position_by_player_id)} ({change.departed_status})"
    )
    remaining_lines = "\n".join(_candidate_line(c, position_by_player_id) for c in change.remaining)
    anomaly_instruction = (
        "Where a listed candidate's own position looks out of place for this role (e.g. a QB "
        "showing up in an RB-role list), do NOT apply a blanket 'wrong position means discount it' "
        "rule -- that's still not real judgment, just a different rule. Use what you actually know "
        "about THIS specific player's real playing style: a known mobile/dual-threat quarterback "
        "(the Lamar Jackson/Malik Willis type) can carry genuine, repeatable rushing volume that "
        "IS meaningful for this role, while a pocket passer with minimal real rushing history "
        "showing the same numbers almost certainly reflects an unrepresentative small sample, not a "
        "real role. Say explicitly which case applies to this named player and why. Separately, "
        "where a candidate's blended share diverges sharply from their raw trailing share (a small "
        "sample being pulled hard toward the league-wide prior), explain that divergence using ONLY "
        "the real numbers given above (raw share, tracked weeks, shrinkage weight) -- do not guess "
        "at a specific play-by-play mechanism (e.g. claiming it reflects kneel-downs) that isn't "
        "actually given to you."
    )
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
            "ONLY on the real trailing role-share numbers above -- do not imply you read any coverage."
        )

    return f"""You are helping a DFS (daily fantasy sports) lineup builder understand WHY a projection
might be what it is, not predict a new number yourself.

Real, current circumstance for {change.team} ({change.role} role), {change.season} week {change.week}:
{departed_line}

Remaining {change.role}-role teammates with real trailing volume:
{remaining_lines}

Real Footballguys article coverage found for {change.team}:
{evidence_block}

FIRST, before writing anything, judge how reliable each input actually is -- this is the whole point
of asking you rather than just printing the numbers above. Specifically: is the tracked-week sample
large enough that the raw share is a meaningful read, or is it a couple of plays getting stretched by
shrinkage toward a league-wide prior? Does the article evidence actually address this team's specific
situation, or is it tangential/generic coverage that happens to mention the team? A candidate whose
position doesn't match this role, or whose numbers come from a 1-2-play sample, deserves real
skepticism, not a confident-sounding story built on top of it.

THEN write a short (2-4 sentence) point of view on how {change.team} is likely to handle this
backfield/role situation this week. If, after that assessment, the real inputs above don't actually
support a meaningful point of view -- too small a sample, no relevant coverage, or both -- say that
plainly as your answer; a calibrated "there isn't enough here to say" is a MORE useful answer than a
fluent-sounding one built past what the data supports. {evidence_instruction} {anomaly_instruction}
Do not invent player names, stats, or sources not given above. Do not give betting or financial
advice -- this is about expected on-field role/usage only."""


def synthesize_circumstance_pov(
    change: CircumstanceChange,
    articles: list[ArchivedArticle],
    *,
    client: _MessagesClient,
    model: str = "claude-sonnet-5",
    max_article_chars: int = DEFAULT_MAX_ARTICLE_CHARS,
    position_by_player_id: dict[str, str] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> CircumstanceAssessment:
    """Calls `client.messages_create(...)` with a prompt grounded exclusively in `change` (the
    deterministic detection result) and `articles` (real archived Footballguys coverage, from
    `find_relevant_articles`) -- never asked to invent a player, a stat, or a source it wasn't
    given. `client` is injected (a thin wrapper around `anthropic.Anthropic().messages.create`,
    see `build_anthropic_messages_client`), not constructed here, so this function is unit-testable
    against a fake without a real network call or API key.

    `position_by_player_id`, when supplied (same gsis_id-space key as `detect_injury_circumstance_
    change`'s own parameter), is shown alongside every candidate's real trailing-share numbers so
    the model can reason about a position anomaly (a QB's volume showing up in an RB-role list)
    itself -- see `_candidate_line`/`detect_injury_circumstance_change`'s docstring for why this
    reasoning belongs here, in the synthesis step, rather than as a silent pre-filter upstream.

    `max_tokens` defaults to `DEFAULT_MAX_TOKENS` (4000), not a small "short answer" budget --
    confirmed live 2026-09-19: this prompt's "judge reliability FIRST" instruction routinely makes
    the model spend real extended-thinking tokens before writing anything, and at `max_tokens=400`
    the entire budget was consumed by thinking with ZERO tokens left for the actual visible answer
    (`stop_reason="max_tokens"`, an empty `pov`, live-confirmed on this exact prompt). 2000 still
    truncated the answer mid-sentence; 4000 completed cleanly (`stop_reason="end_turn"`,
    ~770 output tokens for the case tested). This feature only fires a handful of times per slate
    (one detected circumstance = one call), so the extra token budget is a real but bounded cost.
    """
    prompt = _build_prompt(
        change, articles, max_article_chars=max_article_chars, position_by_player_id=position_by_player_id
    )
    pov = client.messages_create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    if not pov.strip():
        # Never silently ship an empty assessment -- confirmed live 2026-09-19 this happens
        # specifically when max_tokens is exhausted by extended thinking before any visible text is
        # written (see this function's own docstring). A blank CircumstanceAssessment would render
        # as an empty box in the dashboard with no indication anything went wrong -- loud failure
        # here instead, so a caller raises max_tokens or investigates rather than shipping silence.
        raise RuntimeError(
            f"synthesize_circumstance_pov got an empty response for {change.team} {change.role} "
            f"(model={model!r}, max_tokens={max_tokens}) -- likely the whole max_tokens budget was "
            "consumed by extended thinking before any visible text was written. Try a larger "
            "max_tokens."
        )
    return CircumstanceAssessment(
        pov=pov,
        model=model,
        generated_at=datetime.now(timezone.utc).isoformat(),
        evidence_article_titles=[a.title for a in articles],
    )


def build_anthropic_messages_client(api_key: str):  # pragma: no cover -- thin real-SDK wrapper
    """Real `anthropic.Anthropic`-backed `_MessagesClient` -- the only place this module imports
    `anthropic` itself, so every other function here (and every unit test) stays independent of the
    real SDK/network. Not covered by the unit suite (this project's live-call boundary, same
    posture as `ingestion/*.py`'s own `requests`-calling functions) -- exercised by live
    verification instead.
    """
    import anthropic

    real_client = anthropic.Anthropic(api_key=api_key)

    class _RealMessagesClient:
        def messages_create(self, *, model: str, max_tokens: int, messages: list[dict]) -> str:
            response = real_client.messages.create(model=model, max_tokens=max_tokens, messages=messages)
            return "".join(block.text for block in response.content if block.type == "text")

    return _RealMessagesClient()
