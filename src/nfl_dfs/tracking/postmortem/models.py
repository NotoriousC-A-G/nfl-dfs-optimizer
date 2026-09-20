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
