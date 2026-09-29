"""Shared dataclasses for the NFL postmortem package -- read after building `storage/
slate_snapshot_store.py` (the persistence layer this package consumes) and after directly
reviewing the sister MLB project's own `mlb_dfs/tracking/postmortem/models.py`. Same JOB as that
module (projected-vs-actual per player/lineup, a process grade, a chalk-proxy comparison, missed-
ceiling patterns) but every field here is grounded in a real NFL signal this project actually
has -- none of MLB's baseball-specific fields (HR props, batting order, bullpen ISO) are ported.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlayerContext:
    """Real, already-computed per-player signal context from the slate snapshot's
    `PlayerDetailRecord` -- the exact same fields `retrospective.compute_signal_verdicts` already
    reads and trusts for the slate-wide process grade (ownership/game-environment/stack-context/
    ceiling), now surfaced per player instead of only ever aggregated into one verdict (2026-09-29
    player-detail proposal, Tier 1a/1b). Every field is `None`/`False` when the snapshot's own
    record has no real value there -- never fabricated, same "every None has a reason" posture as
    `PlayerDetailRecord` itself.

    `box_score_line` is the one field NOT sourced from the snapshot -- it's a human-readable real
    stat line (e.g. "18/27, 245 pass yds, 2 TD, 1 INT") built from the same nflverse weekly
    box-score row `offense_actual_scoring.dk_points_row` already reduces to one DK-point scalar,
    kept here instead of discarded (Tier 1b)."""

    is_chalk: bool = False
    is_leverage: bool = False
    game_environment_score: float | None = None
    is_primary_stack_candidate: bool = False
    ceiling_multiplier: float | None = None
    red_zone_role_security_discount: float | None = None
    injury_status: str | None = None
    implied_total: float | None = None
    slate_window: str | None = None
    circumstance_note: str | None = None
    box_score_line: str | None = None


@dataclass(frozen=True)
class PlayerOutcome:
    """One player's projected vs. real settled DK points."""

    canonical_id: str
    display_name: str
    team: str
    position: str
    salary: int | None
    projected: float
    actual: float | None  # None when no real settled match was found (see LineupOutcome docstring)
    delta: float | None  # actual - projected; None when actual is None
    # True when `actual` is a real 0.0 because the player did not play (his team's game is settled
    # and his id has no row that week) -- DK scores an inactive rostered player 0, so a lineup
    # holding one is still fully scoreable. Distinct from `actual=None` (a genuine unknown).
    did_not_play: bool = False
    # Optional real per-player context (see `PlayerContext`) -- `None` for a player the context
    # builder never saw (shouldn't happen for a real snapshot pool row, but a caller building a
    # `PlayerOutcome` by hand, e.g. in a test, isn't required to supply one).
    context: PlayerContext | None = None


@dataclass(frozen=True)
class LineupOutcome:
    """One lineup's (an agent's generated build, or Chris's own played L1/L2/L3) projected vs.
    real settled total. `label` is the agent's `display_name` or Chris's own L1/L2/L3 label --
    same identity `storage/agent_results_store.py` already uses, so a postmortem lineup and its
    `agent_results.csv` row are trivially cross-referenced by (agent_id, label)."""

    agent_id: str  # a real NflAgentConstructor.agent_id slug, or "operator"
    label: str
    players: tuple[PlayerOutcome, ...]
    projected_total: float
    actual_total: float | None  # None when ANY player in the lineup has no settled match
    delta: float | None
    unresolved_players: tuple[str, ...] = field(default_factory=tuple)
    # The agent's own disclosed core stack (canonical_ids), straight from the real slate snapshot's
    # `lineup.core_stack` -- not parsed from rationale text. Empty for an operator (L1/L2/L3) row:
    # `agent_results.csv` carries no disclosed stack-lever thesis for Chris's own manual builds, so
    # `compute_stack_thesis_reviews` naturally only reviews the 6 agents, same scope week 2's
    # regex-based version had.
    core_stack: tuple[str, ...] = field(default_factory=tuple)
    core_stack_team: str | None = None


@dataclass(frozen=True)
class ProcessGrade:
    """Weighted signal-strength grade -- direct port of MLB's own `compute_process_grade`
    aggregation formula (tanh-normalized delta, sqrt(min(n)) sample-size weighting), applied to
    NFL-native signal verdicts instead of MLB's baseball ones. See `retrospective.py`."""

    letter: str  # "A"/"B"/"C"/"D"/"F"/"N/A"
    score: float | None  # 0-100, or None when N/A
    signals_hit: int
    signals_evaluated: int
    signal_names_hit: list[str] = field(default_factory=list)
    signal_names_missed: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass(frozen=True)
class ChalkComparison:
    """Real, actual chalk-proxy lineup (max-ownership MILP over the real DK roster shape) vs.
    our best-scoring lineup this week."""

    chalk_lineup: LineupOutcome | None = None
    chalk_actual: float | None = None
    our_actual: float | None = None
    delta: float | None = None  # our_actual - chalk_actual
    beat_chalk: bool | None = None
    infeasible: bool = False
    reason: str = ""
    # (our_player_name, chalk_player_at_same_position_name, actual_delta) for each of our
    # non-chalk picks.
    differentiators: list[tuple[str, str, float]] = field(default_factory=list)


@dataclass(frozen=True)
class CeilingPatterns:
    """Actionable patterns extracted from the real high scorers no lineup rostered this week --
    same job as MLB's own `extract_ceiling_patterns`, DK-NFL salary bands and QB/RB/WR/TE/DST
    positions instead of MLB's."""

    missed_count: int = 0
    salary_bucket_summary: str = ""
    position_theme: str = ""
    highlights: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlayerExposure:
    """Every distinct player rostered across ALL of this week's lineups (the 6 agents' + the
    operator's), with how many of those lineups carried them. Deliberately NOT deduped by lineup
    weight -- a player in 5 of 9 lineups who busted did more real portfolio damage than a bust in
    one lineup alone, so `lineup_labels` counts every real appearance. Direct port of week 2's own
    `compute_player_exposure` (built ad hoc against parsed HTML that week), now real, tested code
    against `LineupOutcome` objects instead of regex output."""

    canonical_id: str
    display_name: str
    team: str
    position: str
    projected: float | None
    actual: float | None
    delta: float | None
    lineup_labels: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.lineup_labels)


@dataclass(frozen=True)
class PositionalDelta:
    """Avg projected/actual/delta BY DISTINCT PLAYER (one vote each, regardless of how many
    lineups rostered them) -- a check on whether the projection system itself ran hot or cold by
    position this week, separate from `PlayerExposure`'s portfolio-damage view."""

    position: str
    n: int
    avg_projected: float
    avg_actual: float
    avg_delta: float


@dataclass(frozen=True)
class StackThesisReview:
    """Did an agent's own named core stack (its real, disclosed `core_stack` canonical_ids from
    the slate snapshot -- not a name parsed from rationale text) actually deliver, independent of
    whether the whole lineup did. A lineup can win despite its stack, or lose despite a stack that
    hit, if the rest of the roster swings the other way -- this isolates just the stack's own real
    settled performance."""

    label: str
    agent_id: str
    stack_player_names: tuple[str, ...]
    stack_team: str | None
    projected: float
    actual: float | None  # None if any stack player is unresolved
    delta: float | None
    hit: bool | None  # actual > projected; None when actual is None


@dataclass(frozen=True)
class PostMortemReport:
    """One week's full postmortem."""

    season: int
    week: int
    lineup_outcomes: tuple[LineupOutcome, ...]
    top_performers: tuple[PlayerOutcome, ...]  # best real scorers in the full pool
    missed_players: tuple[PlayerOutcome, ...]  # high scorers not in ANY lineup this week
    chalk_comparison: ChalkComparison | None = None
    ceiling_patterns: CeilingPatterns | None = None
    process_grade: ProcessGrade | None = None
    player_exposure: tuple[PlayerExposure, ...] = field(default_factory=tuple)
    positional_deltas: tuple[PositionalDelta, ...] = field(default_factory=tuple)
    stack_thesis_reviews: tuple[StackThesisReview, ...] = field(default_factory=tuple)
