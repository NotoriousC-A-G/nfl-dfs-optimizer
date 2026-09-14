"""`MatchupContext` (PRD Section 6) top-level assembly: combines the Run game, Pass protection,
and Coverage rows (`run_game.py`, `pass_protection.py`, `coverage.py`) into one
`MatchupContextResult` per player, per ADR-0005's capped log-space combination where pass-
protection's flow-through multiplier and a receiver's own coverage multiplier both apply to the
same pass-catcher.

**Scheme / game flow row -- satisfied elsewhere, not duplicated here (PRD Section 6):** this row
"feeds directly into `GameEnvironmentScore`'s pace and PROE components rather than a separate
multiplier." That wiring already exists -- `ingestion/nflverse.py`'s `compute_pace_proe_for_week`
computes pace/PROE from `import_pbp_data()`, and `game_environment/score.py` consumes it directly
as `PaceProeInput`. This module intentionally produces no fourth row and no fourth multiplier for
scheme/game-flow; `MatchupContextResult.scheme_game_flow_note` below is a pointer, not a stub
formula, so a future reader doesn't mistake the row's absence here for an oversight.

## Position dispatch -- which rows apply to which player, and how they combine

- **RB**: `run_game` (own team's run-block vs. opponent's run-defense) is the dominant chain for a
  RB's rushing efficiency. `pass_protection` (flow-through from the RB's own team, since a RB who
  catches passes is a pass-catcher too) is recorded for transparency but is **not** multiplied into
  `combined_multiplier` for a RB -- ADR-0005 itself says the run-block multiplier is "a different
  signal chain ... with no comparable second multiplier stacking onto it," and this pipeline's
  `PlayerProjection.blended_projection` (the thing `combined_multiplier` ultimately scales, see
  `projection/blend.py`) is one already-blended rush+pass points total with no split to apply a
  second multiplier to only the receiving portion of. This mirrors the exact stated simplification
  `apply_rb_blowout_volume_discount` already makes in `blend.py` for the same reason -- a RB's
  points number can't be cleanly decomposed by this pipeline's current data, so one dominant
  multiplier (run game, the row PRD Section 6 built specifically for RB efficiency) is applied to
  the whole number, and the secondary row is exposed for audit but not compounded on top.
- **QB**: `pass_protection` is the QB's own multiplier (PRD: "applied to QB sack/pressure-adjusted
  efficiency"). No `run_game`/`coverage` row (a rushing QB's own run-game exposure is out of scope
  for this pass, consistent with `MatchupContext`'s table naming the RB as run game's target).
- **WR/TE**: `pass_protection` (flow-through from their own team's QB matchup) and `coverage` (their
  own receiver-specific row) combine via ADR-0005's capped log-space method (`±20%`) when both are
  computable; if only one is computable, `combined_multiplier` is that one multiplier alone (no
  combination needed, and no artificial `1.0` leg injected into the log-space sum).
- **Everyone else** (DST, etc.): no rows apply; `combined_multiplier=1.0`, `notes` explains why.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nfl_dfs.ingestion.pff import PffFacetGrades, ResolvedGrade
from nfl_dfs.matchup.combination import capped_log_combine
from nfl_dfs.matchup.coverage import (
    MAN_COVERAGE_GRADE_FIELD,
    MAN_COVERAGE_SNAP_FIELD,
    RECEIVER_MAN_GRADE_FIELD,
    RECEIVER_ZONE_GRADE_FIELD,
    ZONE_COVERAGE_GRADE_FIELD,
    ZONE_COVERAGE_SNAP_FIELD,
    CoverageConfidence,
    CoverageMultiplier,
    compute_coverage_multiplier,
    receiver_man_zone_rate,
)
from nfl_dfs.matchup.grading import PASS_PROTECTION_COVERAGE_COMBINED_CAP, TeamAggregate, team_aggregate_grades
from nfl_dfs.matchup.pass_protection import (
    PASS_BLOCK_GRADE_FIELD,
    PASS_BLOCK_SNAP_FIELD,
    PASS_RUSH_GRADE_FIELD,
    PASS_RUSH_SNAP_FIELD,
    PassProtectionMultiplier,
    compute_pass_protection_multiplier,
)
from nfl_dfs.matchup.run_game import (
    RUN_BLOCK_GRADE_FIELD,
    RUN_BLOCK_SNAP_FIELD,
    RUN_DEFENSE_GRADE_FIELD,
    RUN_DEFENSE_SNAP_FIELD,
    RunGameMultiplier,
    compute_run_game_multiplier,
)

SCHEME_GAME_FLOW_NOTE = (
    "Scheme/game flow (PRD Section 6's fourth MatchupContext row) produces no multiplier of its "
    "own -- it feeds directly into GameEnvironmentScore's pace/PROE components, already computed "
    "by ingestion/nflverse.py's compute_pace_proe_for_week and consumed by "
    "game_environment/score.py's PaceProeInput. Nothing in this module duplicates that wiring."
)


@dataclass(frozen=True)
class MatchupRowResult:
    """One row's contribution, exposed for transparency -- the same discipline
    `GameEnvironmentScore.ComponentScore` already establishes: never fold a sub-component into the
    final number without also surfacing it on its own.
    """

    label: str
    multiplier: float | None
    reason: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MatchupContextResult:
    """Final `MatchupContext` output for one player, one week: the combined multiplier plus every
    contributing row's own value, and (for the coverage row specifically) a three-state confidence
    indicator mirroring `StackProfile.bring_back_status`'s discipline -- "confidently computed"
    (`"confident"`), "fell back to team-wide" (`"team_wide_fallback"`), and "no data"
    (`"no_data"`/`None` when coverage doesn't apply to this position at all) are never collapsed
    into one ambiguous state.
    """

    canonical_player_id: str
    team: str
    opponent: str
    position: str
    combined_multiplier: float
    run_game: MatchupRowResult | None
    pass_protection: MatchupRowResult | None
    coverage: MatchupRowResult | None
    coverage_confidence: CoverageConfidence | None
    scheme_game_flow_note: str = SCHEME_GAME_FLOW_NOTE
    notes: list[str] = field(default_factory=list)


def _row_from_run_game(rgm: RunGameMultiplier) -> MatchupRowResult:
    return MatchupRowResult(label="run_game", multiplier=rgm.multiplier, reason=rgm.reason)


def _row_from_pass_protection(ppm: PassProtectionMultiplier) -> MatchupRowResult:
    return MatchupRowResult(label="pass_protection", multiplier=ppm.multiplier, reason=ppm.reason)


def _row_from_coverage(cov: CoverageMultiplier) -> MatchupRowResult:
    return MatchupRowResult(
        label="coverage",
        multiplier=cov.multiplier,
        reason=cov.reason,
        notes=[f"confidence={cov.confidence}"],
    )


def build_matchup_context_for_rb(
    canonical_player_id: str,
    team: str,
    opponent: str,
    run_game_multiplier: RunGameMultiplier,
    pass_protection_multiplier: PassProtectionMultiplier | None = None,
) -> MatchupContextResult:
    """RB: `run_game` is the sole driver of `combined_multiplier` -- see module docstring's
    position-dispatch section for why `pass_protection` (when supplied) is recorded but not
    compounded in.
    """
    notes = []
    run_row = _row_from_run_game(run_game_multiplier)
    pass_row = _row_from_pass_protection(pass_protection_multiplier) if pass_protection_multiplier else None
    if pass_row is not None:
        notes.append(
            "pass_protection is flow-through data for this RB as a pass-catcher, recorded for "
            "transparency but not compounded into combined_multiplier -- see context.py's module "
            "docstring (ADR-0005: run-block multiplier has no comparable second multiplier "
            "stacking onto it in the current formula set, and blended_projection has no rush/pass "
            "split to apply a second multiplier to only part of)."
        )

    combined = run_game_multiplier.multiplier if run_game_multiplier.multiplier is not None else 1.0
    if run_game_multiplier.multiplier is None:
        notes.append(f"run_game not computable: {run_game_multiplier.reason}")

    return MatchupContextResult(
        canonical_player_id=canonical_player_id,
        team=team,
        opponent=opponent,
        position="RB",
        combined_multiplier=combined,
        run_game=run_row,
        pass_protection=pass_row,
        coverage=None,
        coverage_confidence=None,
        notes=notes,
    )


def build_matchup_context_for_qb(
    canonical_player_id: str,
    team: str,
    opponent: str,
    pass_protection_multiplier: PassProtectionMultiplier,
) -> MatchupContextResult:
    """QB: `pass_protection` is the QB's own multiplier -- no run_game/coverage row."""
    pass_row = _row_from_pass_protection(pass_protection_multiplier)
    combined = pass_protection_multiplier.multiplier if pass_protection_multiplier.multiplier is not None else 1.0
    notes = [] if pass_protection_multiplier.multiplier is not None else [
        f"pass_protection not computable: {pass_protection_multiplier.reason}"
    ]
    return MatchupContextResult(
        canonical_player_id=canonical_player_id,
        team=team,
        opponent=opponent,
        position="QB",
        combined_multiplier=combined,
        run_game=None,
        pass_protection=pass_row,
        coverage=None,
        coverage_confidence=None,
        notes=notes,
    )


def build_matchup_context_for_receiver(
    canonical_player_id: str,
    team: str,
    opponent: str,
    coverage_multiplier: CoverageMultiplier,
    pass_protection_multiplier: PassProtectionMultiplier | None = None,
    combined_cap: float = PASS_PROTECTION_COVERAGE_COMBINED_CAP,
) -> MatchupContextResult:
    """WR/TE (and a pass-catching RB, if a caller chooses to also run this path for them):
    combine `pass_protection`'s flow-through multiplier and this receiver's own `coverage`
    multiplier via ADR-0005's capped log-space method when both are computable. If only one is
    computable, `combined_multiplier` is that one value directly -- never combined against an
    injected neutral `1.0`, which would be indistinguishable from a real second multiplier that
    happened to compute to exactly 1.0.
    """
    coverage_row = _row_from_coverage(coverage_multiplier)
    pass_row = _row_from_pass_protection(pass_protection_multiplier) if pass_protection_multiplier else None

    notes: list[str] = []
    available = []
    if coverage_multiplier.multiplier is not None:
        available.append(coverage_multiplier.multiplier)
    else:
        notes.append(f"coverage not computable: {coverage_multiplier.reason}")

    if pass_protection_multiplier is not None:
        if pass_protection_multiplier.multiplier is not None:
            available.append(pass_protection_multiplier.multiplier)
        else:
            notes.append(f"pass_protection not computable: {pass_protection_multiplier.reason}")

    if len(available) == 2:
        combine_result = capped_log_combine(available, cap=combined_cap)
        combined = combine_result.combined_multiplier
        if combine_result.capped:
            notes.append(
                f"combined pass_protection x coverage deviation capped at +-{combined_cap:.0%} (ADR-0005)."
            )
    elif len(available) == 1:
        combined = available[0]
    else:
        combined = 1.0

    return MatchupContextResult(
        canonical_player_id=canonical_player_id,
        team=team,
        opponent=opponent,
        position="WR/TE",
        combined_multiplier=combined,
        run_game=None,
        pass_protection=pass_row,
        coverage=coverage_row,
        coverage_confidence=coverage_multiplier.confidence,
        notes=notes,
    )


def neutral_matchup_context(canonical_player_id: str, team: str, opponent: str, position: str, reason: str) -> MatchupContextResult:
    """No `MatchupContext` row applies to this position (or no matchup data was available at all)
    -- `combined_multiplier=1.0`, explained rather than silently defaulted."""
    return MatchupContextResult(
        canonical_player_id=canonical_player_id,
        team=team,
        opponent=opponent,
        position=position,
        combined_multiplier=1.0,
        run_game=None,
        pass_protection=None,
        coverage=None,
        coverage_confidence=None,
        notes=[reason],
    )


# --------------------------------------------------------------------------------------------
# Whole-pool orchestration -- ties the four already-ingested grade facets (ADR-0014/ADR-0022)
# together into one MatchupContextResult per player, for a full slate at once. This is the
# function `projection/blend.py`'s wiring and the live integration check both call.
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchupFacetInputs:
    """Already-fetched `PffFacetGrades` pulls (ADR-0014-populated, via `pff.py`'s
    `fetch_matchup_grades`) this orchestrator consumes -- named to match `pff.py`'s own
    `GRADE_FACETS` keys 1:1 so a caller can build this straight from that module's output with no
    relabeling. Nothing here is fetched by this module.
    """

    run_blocking: PffFacetGrades  # offense/run_blocking
    run_defense: PffFacetGrades  # defense/run
    pass_blocking: PffFacetGrades  # offense/pass_blocking
    pass_rush: PffFacetGrades  # defense/pass_rush
    coverage_scheme: PffFacetGrades  # defense/coverage_scheme
    receiving_scheme: PffFacetGrades  # receiving/scheme


@dataclass(frozen=True)
class PlayerMatchupInput:
    """One player this week -- the minimal identity slice `build_matchup_context_pool` needs.
    `pff_native_id` is required for WR/TE (the `receiving/scheme` facet's own join key, PFF's
    native `player_id` -- see `composition/player_detail.py`'s `_pff_native_id_for_identity` for
    how a caller typically resolves this from a `PlayerIdentity`); `None` for RB/QB, whose rows
    (`run_game`/`pass_protection`) are team-level and need no per-player PFF ID at all.
    """

    canonical_player_id: str
    team: str
    position: str
    pff_native_id: str | None = None


def _team_aggregates(facets: MatchupFacetInputs) -> dict[str, dict[str, TeamAggregate]]:
    return {
        "run_blocking": team_aggregate_grades(facets.run_blocking.by_player_id, RUN_BLOCK_GRADE_FIELD, RUN_BLOCK_SNAP_FIELD),
        "run_defense": team_aggregate_grades(facets.run_defense.by_player_id, RUN_DEFENSE_GRADE_FIELD, RUN_DEFENSE_SNAP_FIELD),
        "pass_blocking": team_aggregate_grades(facets.pass_blocking.by_player_id, PASS_BLOCK_GRADE_FIELD, PASS_BLOCK_SNAP_FIELD),
        "pass_rush": team_aggregate_grades(facets.pass_rush.by_player_id, PASS_RUSH_GRADE_FIELD, PASS_RUSH_SNAP_FIELD),
        "coverage_man": team_aggregate_grades(facets.coverage_scheme.by_player_id, MAN_COVERAGE_GRADE_FIELD, MAN_COVERAGE_SNAP_FIELD),
        "coverage_zone": team_aggregate_grades(facets.coverage_scheme.by_player_id, ZONE_COVERAGE_GRADE_FIELD, ZONE_COVERAGE_SNAP_FIELD),
    }


def build_matchup_context_pool(
    players: list[PlayerMatchupInput],
    opponents: dict[str, str],
    facets: MatchupFacetInputs,
) -> dict[str, MatchupContextResult]:
    """Compute `MatchupContext` for a full slate of players at once -- the entry point
    `projection/blend.py`'s wiring calls. Returns `canonical_player_id -> MatchupContextResult`;
    a player whose team has no entry in `opponents` (e.g. on a bye) is skipped entirely (never
    guessed against a fabricated opponent).

    Team-level aggregates (run-block/run-defense/pass-block/pass-rush/coverage man-zone) and the
    coverage row's league populations are each computed once against the FULL facet pulls, then
    reused across every player -- not recomputed per player.
    """
    aggregates = _team_aggregates(facets)

    # league_defender_{man,zone}_population: one aggregated value per defense, used to z-score the
    # coverage row's defender-side grade (whether that's an ADR-0001 identified-defender blend or,
    # as is the real-world case today per coverage.py's own note, the team-wide fallback).
    league_defender_man_population = [a.value for a in aggregates["coverage_man"].values()]
    league_defender_zone_population = [a.value for a in aggregates["coverage_zone"].values()]
    # league_receiver_{man,zone}_population: individual-level, since a receiver's own performance
    # grade is inherently a per-player number, not a team aggregate.
    league_receiver_man_population = [
        row.grades[RECEIVER_MAN_GRADE_FIELD]
        for row in facets.receiving_scheme.by_player_id.values()
        if RECEIVER_MAN_GRADE_FIELD in row.grades
    ]
    league_receiver_zone_population = [
        row.grades[RECEIVER_ZONE_GRADE_FIELD]
        for row in facets.receiving_scheme.by_player_id.values()
        if RECEIVER_ZONE_GRADE_FIELD in row.grades
    ]

    run_game_cache: dict[tuple[str, str], RunGameMultiplier] = {}
    pass_protection_cache: dict[tuple[str, str], PassProtectionMultiplier] = {}

    def _run_game(team: str, opponent: str) -> RunGameMultiplier:
        key = (team, opponent)
        if key not in run_game_cache:
            run_game_cache[key] = compute_run_game_multiplier(
                team, opponent, aggregates["run_blocking"], aggregates["run_defense"]
            )
        return run_game_cache[key]

    def _pass_protection(team: str, opponent: str) -> PassProtectionMultiplier:
        key = (team, opponent)
        if key not in pass_protection_cache:
            pass_protection_cache[key] = compute_pass_protection_multiplier(
                team, opponent, aggregates["pass_blocking"], aggregates["pass_rush"]
            )
        return pass_protection_cache[key]

    results: dict[str, MatchupContextResult] = {}
    for player in players:
        opponent = opponents.get(player.team)
        if opponent is None:
            results[player.canonical_player_id] = neutral_matchup_context(
                player.canonical_player_id, player.team, "UNKNOWN", player.position,
                reason=f"no opponent found for {player.team} this week (bye week, or schedule not supplied).",
            )
            continue

        if player.position == "RB":
            results[player.canonical_player_id] = build_matchup_context_for_rb(
                player.canonical_player_id,
                player.team,
                opponent,
                _run_game(player.team, opponent),
                _pass_protection(player.team, opponent),
            )
        elif player.position == "QB":
            results[player.canonical_player_id] = build_matchup_context_for_qb(
                player.canonical_player_id, player.team, opponent, _pass_protection(player.team, opponent)
            )
        elif player.position in ("WR", "TE"):
            if player.pff_native_id is None:
                results[player.canonical_player_id] = neutral_matchup_context(
                    player.canonical_player_id, player.team, opponent, player.position,
                    reason="no PFF native player_id resolved -- coverage row (receiving/scheme "
                    "join) is not computable for this player.",
                )
                continue
            rates = receiver_man_zone_rate(player.pff_native_id, facets.receiving_scheme.by_player_id)
            if rates is None:
                results[player.canonical_player_id] = neutral_matchup_context(
                    player.canonical_player_id, player.team, opponent, player.position,
                    reason="no receiving/scheme man/zone rate data for this player -- coverage row "
                    "not computable this week.",
                )
                continue
            man_rate, zone_rate = rates
            coverage = compute_coverage_multiplier(
                receiver_id=player.pff_native_id,
                receiver_team=player.team,
                defense_team=opponent,
                receiver_man_rate=man_rate,
                receiver_zone_rate=zone_rate,
                coverage_facet_rows=facets.coverage_scheme.by_player_id,
                receiving_facet_rows=facets.receiving_scheme.by_player_id,
                league_defender_man_population=league_defender_man_population,
                league_defender_zone_population=league_defender_zone_population,
                league_receiver_man_population=league_receiver_man_population,
                league_receiver_zone_population=league_receiver_zone_population,
            )
            results[player.canonical_player_id] = build_matchup_context_for_receiver(
                player.canonical_player_id,
                player.team,
                opponent,
                coverage,
                _pass_protection(player.team, opponent),
            )
        else:
            results[player.canonical_player_id] = neutral_matchup_context(
                player.canonical_player_id, player.team, opponent, player.position,
                reason=f"MatchupContext has no row(s) defined for position {player.position!r}.",
            )

    return results


# --------------------------------------------------------------------------------------------
# composition/player_detail.py wiring -- own_unit_grade/opponent_unit_grade (ADR-0022 Round B),
# previously always-None placeholders "blocked on MatchupContext's defender-identification/
# grading logic ... not yet implemented anywhere in this pipeline." Now that it exists, this
# resolves the same team-level aggregates run_game.py/pass_protection.py already compute for
# their own multipliers into ADR-0022's originally-sketched shape -- a second read of the same
# number, not a new formula.
# --------------------------------------------------------------------------------------------


def _synthetic_team_grade(team: str, aggregate: TeamAggregate | None, grade_field: str, population: str) -> ResolvedGrade | None:
    """A team-level `ResolvedGrade` for a unit that has no single individual PFF `player_id` of
    its own (an O-line, a defensive front, a secondary) -- `native_id=f"TEAM_{team}"` makes this
    synthetic origin explicit rather than impersonating a real per-player row. `population`
    carries through the aggregation method/sample size (see `grading.TeamAggregate`) for
    traceability."""
    if aggregate is None:
        return None
    return ResolvedGrade(
        native_id=f"TEAM_{team}",
        position="TEAM_UNIT",
        grades={grade_field: aggregate.value},
        player_game_count=None,
        population=f"{population} (team aggregate: {aggregate.method}, n_players={aggregate.n_players})",
    )


def resolve_own_opponent_unit_grades(
    position: str,
    team: str,
    opponent_team: str | None,
    facets: MatchupFacetInputs,
) -> tuple[ResolvedGrade | None, ResolvedGrade | None, str | None]:
    """`own_unit_grade`/`opponent_unit_grade` for `composition/player_detail.py`'s
    `MatchupThisWeek` -- position-dependent per ADR-0022's own schema sketch ("this player's OL's
    `grades_pass_block`/`grades_run_block`" vs. "opponent DL's `grades_run_defense`/
    `pass_rush_win_rate`"):

    - **RB**: own = own team's run-blocking aggregate; opponent = opponent's run-defense aggregate.
    - **QB**: own = own team's pass-blocking aggregate; opponent = opponent's pass-rush aggregate.
    - **WR/TE**: own_unit_grade is `None` (a receiver has no clean "own unit" distinct from their
      own `receiving/scheme` performance, already surfaced in `own_scheme_splits` -- see reason);
      opponent = the opposing defense's team-wide coverage-grade aggregate (both man and zone,
      when computable).
    - **Anything else**: both `None`, reason states the position isn't covered.

    Returns `(own_unit_grade, opponent_unit_grade, reason)` -- `reason` is populated whenever at
    least one side is `None`, describing exactly why (mirrors `player_detail.py`'s existing
    per-section "every `None` has a stated reason" discipline).
    """
    if opponent_team is None:
        return None, None, "no opponent identified for this week (bye week, or not supplied to the composer)."

    aggregates = _team_aggregates(facets)

    if position == "RB":
        own = _synthetic_team_grade(team, aggregates["run_blocking"].get(team), RUN_BLOCK_GRADE_FIELD, facets.run_blocking.population)
        opp = _synthetic_team_grade(
            opponent_team, aggregates["run_defense"].get(opponent_team), RUN_DEFENSE_GRADE_FIELD, facets.run_defense.population
        )
        reason = None if (own is not None or opp is not None) else (
            "no run-blocking/run-defense team aggregate computable this week (see MatchupContext's "
            "Run game row, matchup/run_game.py)."
        )
        return own, opp, reason

    if position == "QB":
        own = _synthetic_team_grade(team, aggregates["pass_blocking"].get(team), PASS_BLOCK_GRADE_FIELD, facets.pass_blocking.population)
        opp = _synthetic_team_grade(
            opponent_team, aggregates["pass_rush"].get(opponent_team), PASS_RUSH_GRADE_FIELD, facets.pass_rush.population
        )
        reason = None if (own is not None or opp is not None) else (
            "no pass-blocking/pass-rush team aggregate computable this week (see MatchupContext's "
            "Pass protection row, matchup/pass_protection.py)."
        )
        return own, opp, reason

    if position in ("WR", "TE"):
        opp_man = aggregates["coverage_man"].get(opponent_team)
        opp_zone = aggregates["coverage_zone"].get(opponent_team)
        if opp_man is None and opp_zone is None:
            return None, None, (
                "no defense/coverage_scheme team aggregate computable for the opponent this week."
            )
        grades: dict[str, float] = {}
        if opp_man is not None:
            grades[MAN_COVERAGE_GRADE_FIELD] = opp_man.value
        if opp_zone is not None:
            grades[ZONE_COVERAGE_GRADE_FIELD] = opp_zone.value
        n_players = max((a.n_players for a in (opp_man, opp_zone) if a is not None), default=0)
        opponent_grade = ResolvedGrade(
            native_id=f"TEAM_{opponent_team}",
            position="TEAM_UNIT",
            grades=grades,
            player_game_count=None,
            population=f"{facets.coverage_scheme.population} (team aggregate, n_players={n_players})",
        )
        return None, opponent_grade, (
            "own_unit_grade is not applicable for WR/TE -- see own_scheme_splits for this "
            "player's own receiving/scheme man/zone performance instead."
        )

    return None, None, f"MatchupContext defines no own/opponent unit grade for position {position!r}."
