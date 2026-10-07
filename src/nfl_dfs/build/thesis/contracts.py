"""Data contracts for a game analyst's output: the **game thesis** (see `CONTEXT.md`).

A game analyst reads one game's evidence packet and returns a `GameThesis`: the game's pivotal
questions, their probabilities (an anchor computed in code plus a bounded adjustment), the branches
those questions create, per-player conditional outcomes, the analyst's stated correlation signs, and
the claims it rests on (each citing a packet field). These are *contracts*, not logic -- every rule
about what a valid thesis looks like lives in `validate.py` so a model's output is checked
deterministically before anything downstream trusts it (ADR-0046, plan D1-D3, Revision 2).

All values are frozen dataclasses of plain types so a thesis round-trips through JSON into the slate
snapshot. Magnitudes (multiplier bins, caps, thresholds) are disclosed drafts -- "draft, not
backtested" -- not validated constants.
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 1

# Per-player, per-branch multipliers on the shrunk baseline: coarse on purpose (never raw points) --
# false precision is the failure mode (model-analytics review). Draft clamps for the bins' range.
MULTIPLIER_BINS: tuple[float, ...] = (0.8, 0.9, 1.0, 1.15, 1.3)

# Proxy metrics a pivotal question may use: only things computable from the play-by-play we load for
# 2026 (no time-to-throw / box counts / pressure flags -- participation data does not exist for 2026).
PROXY_METRICS: frozenset[str] = frozenset(
    {
        "sack_rate",
        "qb_hit_rate",
        "pass_rate_over_expected_leading",
        "pass_rate_over_expected",
        "lead_at_q4",
        "lead_at_q3_start",
        "pace_seconds_per_play",
        "total_plays",
        "rush_epa",
        "pass_epa",
        "explosive_pass_rate",
        "rush_attempts_leading",
        "rb_target_count",
        "combined_pass_attempts",
        "red_zone_td_rate",
    }
)

# The unit/phase a question is about. Two independent questions must differ here, otherwise both are
# really "does the offense score" and the branches collapse to shootout-vs-not.
PHASES: frozenset[str] = frozenset(
    {
        "pass_protection",
        "pass_rush",
        "run_game",
        "coverage",
        "pace",
        "turnovers",
        "field_position",
        "weather",
        "qb_availability",
        "special_teams",
        "game_state",
    }
)

DIRECTIONS = ("gte", "lte")
SIGNS = ("positive", "negative", "neutral")
STRENGTHS = ("mild", "strong")
CLAIM_KINDS = ("general", "availability")


@dataclass(frozen=True)
class PivotalQuestion:
    question_id: str  # "q1", "q2"
    text: str
    phase: str  # one of PHASES
    metric: str  # one of PROXY_METRICS
    source_field: str  # a key into the evidence packet, e.g. "units.PHI.sack_rate"
    threshold: float
    direction: str  # "gte" | "lte" -- the question is answered YES when metric >= / <= threshold
    sample_n: int | None  # plays/dropbacks behind the proxy (>= ~25 for rate stats)
    adds_beyond_line: str  # what this question adds that the betting line does not already say
    parent_id: str | None = None  # set => a child question, only live when its parent resolves `parent_answer`
    parent_answer: bool | None = None


@dataclass(frozen=True)
class QuestionMarginal:
    """Probability the question resolves YES: the code-computed anchor and the analyst's adjusted
    value (5-point buckets, bounded to +/-0.25 log-odds of the anchor)."""

    question_id: str
    anchor: float
    adjusted: float
    reason: str = ""


@dataclass(frozen=True)
class Dependency:
    """One stated causal link between two questions. `lambda_` is the log odds ratio (restricted to
    +/-0.5, a diagnostic-grade effect: dependencies are not identifiable at our sample size)."""

    from_id: str
    to_id: str
    channel: str  # the causal channel, e.g. "pressure -> quick throws -> lower aDOT -> lower WR ceiling"
    lambda_: float


@dataclass(frozen=True)
class PlayerBranchOutcome:
    """A player's conditional outcome in one branch: multipliers on his shrunk baseline."""

    canonical_id: str
    mean_mult: float
    q90_mult: float
    # Opportunity-based reason (targets, carries, red-zone touches), never a fantasy-point claim.
    reason: str = ""


@dataclass(frozen=True)
class Branch:
    branch_id: str
    answers: tuple[tuple[str, bool], ...]  # (question_id, answered YES?) for each live question; () for residual
    is_residual: bool  # "neither question resolves as framed"
    prob: float
    description: str
    chain: tuple[str, ...] = ()  # the interaction chain, 3-4 short links
    player_outcomes: tuple[PlayerBranchOutcome, ...] = ()
    margin_shift: float = 0.0  # expected (favorite-perspective) margin shift vs the line in this branch
    total_shift: float = 0.0  # expected total-points shift vs the line in this branch


@dataclass(frozen=True)
class Claim:
    """One statement the thesis rests on. Numbers must be citeable: every `cite_keys` entry has to
    resolve against the saved evidence packet, or the claim is UNVERIFIED."""

    text: str
    cite_keys: tuple[str, ...]
    kind: str = "general"  # "availability" claims must also state `status` and `as_of`
    status: str | None = None
    as_of: str | None = None


@dataclass(frozen=True)
class PairSign:
    """The analyst's stated correlation sign for a pair of players in this game (a code lint rejects a
    lineup holding a stated-negative pair)."""

    player_a: str  # canonical_id
    player_b: str
    sign: str  # "positive" | "negative" | "neutral"
    strength: str  # "mild" | "strong"
    reason: str


@dataclass(frozen=True)
class GameThesis:
    schema_version: int
    game_id: str
    packet_sha: str
    prompt_version: str
    model: str
    headline: str
    questions: tuple[PivotalQuestion, ...]
    marginals: tuple[QuestionMarginal, ...]
    dependencies: tuple[Dependency, ...]
    branches: tuple[Branch, ...]
    counter_branch_id: str  # the strongest branch against the thesis's headline
    claims: tuple[Claim, ...]
    would_change_mind: tuple[str, ...]  # pre-kickoff observables (inactives, weather, line move)
    pair_signs: tuple[PairSign, ...] = ()
    # Declared, bounded disagreement with the closing line (points). Graded separately; |d| <= 2.
    declared_margin_disagreement: float = 0.0
    declared_total_disagreement: float = 0.0
