"""Build lineups for ONE agent inside its expert-built pool (ADR-0046, plan D5/D6, Revision 2).

Flow: validate the pool -> widen ONLY at the failing spot, logged (relax the core minimum, then promote
players at the failing position/stack; never wholesale) -> solve with tail value as the objective base,
the required-stack / bring-back pair bonuses, a core-tier minimum and the hard bring-back sliders ->
re-verify every lineup independently of the solver -> return, or raise `PoolBuildFailure` with a
diagnostic naming the stage and cause. **Nothing is ever substituted** (Chris, 2026-10-07: a silent
projection-max fallback "checks a box"); a failed agent is reported, not papered over.

Pair bonuses are in lineup-q90 points (model-analytics review, draft, not backtested): a QB with a
same-team pass catcher +1.8 (rho ~0.35-0.4), a bring-back +0.8 (rho ~0.15). QB vs the opposing DST is
already penalized by the solver's PRD-section-7 DST-correlation term, so it is not repeated here.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pulp

from nfl_dfs.build.common import Violation
from nfl_dfs.build.pool.contracts import PlayerRef, Pool
from nfl_dfs.build.pool.validate import PoolReport, promote_at_failing, relax_min_core, validate_pool
from nfl_dfs.build.thesis.contracts import PairSign
from nfl_dfs.build.thesis.lint import lint_lineup_pair_signs
from nfl_dfs.optimizer.lineup import (
    EXCLUDED_INJURY_STATUSES, Lineup, LineupGenerationError, bring_back_violations, generate_lineups,
)
from nfl_dfs.projection.blend import PlayerProjection

STACK_BONUS = 1.8
BRING_BACK_BONUS = 0.8
# A lineup that leaves cap unspent means the pool lacks (or the objective is steering away from) players worth buying with it.
# This is a DETECTION trigger, not a solver constraint (Chris, 2026-10-07: no hard rules): the agent fails loudly with a supply
# diagnosis and the expert re-tiers once (`build/expert/repair.py`). Draft threshold, not backtested: $2,000 unspent.
MIN_SALARY_USED = 48_000
# A reach-tier player is allowed anywhere in the lineup but is worth less to the solver (draft, not backtested): he is taken to fit a
# salary or position need, or because his value still beats the alternatives after the haircut -- not as a default.
REACH_HAIRCUT = 0.25
# ...and a reach player already used in an earlier agent's lineup is worth 10% less per prior use (at most 3), so the open remainder cannot
# quietly become the same few value plays in every agent's lineups (2026-10-09 first look: three players were in 7-8 of 10 lineups).
REACH_REUSE_DISCOUNT = 0.10
REACH_REUSE_MAX = 3
# A milder version for eligible players (Chris, 2026-10-09: some overlap is fine, a bit more spread in how often a player appears is wanted).
ELIGIBLE_REUSE_DISCOUNT = 0.05
PAIR_TOP_N = 4  # only each team's top-N catchers by value get pair terms (bounds the model size)
# Failure codes widening can plausibly fix; anything else is an expert/contract error and fails at once.
_WIDENABLE_PREFIXES = ("feasibility_", "stack_teams", "min_core", "distinct_stacks")


class PoolBuildFailure(RuntimeError):
    """An agent could not produce valid lineups. `stage` names where it stopped; `diagnostics` is the
    full picture (violations, counts, stripped players, every widening attempted) for diagnosis."""

    def __init__(self, agent_id: str, stage: str, message: str, diagnostics: dict):
        super().__init__(f"[{agent_id}] {stage}: {message}")
        self.agent_id, self.stage, self.diagnostics = agent_id, stage, diagnostics


@dataclass(frozen=True)
class PoolBuildResult:
    agent_id: str
    lineups: tuple[Lineup, ...]
    pool: Pool  # the pool actually used (after any widening)
    report: PoolReport
    warnings: tuple[Violation, ...] = field(default_factory=tuple)


def player_refs_from_projections(projections: list[PlayerProjection], game_id_by_team: dict[str, str]) -> list[PlayerRef]:
    return [
        PlayerRef(p.canonical_id, p.display_name, p.team, p.position, p.salary, p.blended_projection, p.dk_injury_status, game_id_by_team.get(p.team))
        for p in projections
    ]


def stack_bonus_pairs(
    players: list[PlayerProjection], values: dict[str, float], opponent_of: dict[str, str],
    *, allow_bring_back: bool = True, top_n: int = PAIR_TOP_N,
) -> list[tuple[str, str, float]]:
    """`(a, b, bonus)` pairs: each QB with each of his team's top-N catchers (+STACK_BONUS) and, when a
    bring-back is allowed, with the opposing team's top-N catchers (+BRING_BACK_BONUS)."""
    by_team: dict[str, list[PlayerProjection]] = defaultdict(list)
    for p in players:
        if p.position in ("WR", "TE") and p.canonical_id in values:
            by_team[p.team].append(p)
    for team in by_team:
        by_team[team].sort(key=lambda p: -values[p.canonical_id])
    pairs: list[tuple[str, str, float]] = []
    for qb in (p for p in players if p.position == "QB"):
        for c in by_team.get(qb.team, [])[:top_n]:
            pairs.append((qb.canonical_id, c.canonical_id, STACK_BONUS))
        if allow_bring_back:
            for c in by_team.get(opponent_of.get(qb.team, ""), [])[:top_n]:
                pairs.append((qb.canonical_id, c.canonical_id, BRING_BACK_BONUS))
    return pairs


def _solve(
    pool: Pool, playable: list[PlayerProjection], core_ids: set[str], values: dict[str, float],
    *, n: int, opponent_of: dict[str, str], seed_core_stacks, distinct_core_stacks: bool,
    avoid_lineups: list[frozenset[str]] | None = None, min_player_difference: int = 1,
) -> list[Lineup]:
    rules = pool.rules
    reach_ids = {e.canonical_id for e in pool.entries if e.tier == "reach"}
    uses: dict[str, int] = defaultdict(int)
    for lineup in avoid_lineups or ():
        for pid in lineup:
            uses[pid] += 1
    eligible_ids = {e.canonical_id for e in pool.entries if e.tier == "eligible"}

    def priced(i: str, v: float) -> float:
        if i in reach_ids:
            return v * (1.0 - REACH_HAIRCUT) * (1.0 - REACH_REUSE_DISCOUNT * min(uses[i], REACH_REUSE_MAX))
        if i in eligible_ids:
            return v * (1.0 - ELIGIBLE_REUSE_DISCOUNT * min(uses[i], REACH_REUSE_MAX))
        return v

    values = {i: priced(i, v) for i, v in values.items()}
    pairs = stack_bonus_pairs(
        playable, values, opponent_of,
        allow_bring_back=not rules.forbid_pass_catcher_bring_back,
    )

    def core_minimum(prob: pulp.LpProblem, x: dict[str, pulp.LpVariable], _players: list[PlayerProjection]) -> None:
        members = [x[i] for i in core_ids if i in x]
        if members and rules.min_core > 0:
            prob += pulp.lpSum(members) >= rules.min_core, "core_minimum"

    return generate_lineups(
        playable, n=n, opponent_of=opponent_of,
        forbid_pass_catcher_bring_back=rules.forbid_pass_catcher_bring_back,
        forbid_rb_bring_back=rules.forbid_rb_bring_back,
        base_value_by_id={p.canonical_id: values[p.canonical_id] for p in playable},
        pair_bonuses=pairs, extra_constraints=core_minimum,
        seed_core_stacks=list(seed_core_stacks) if seed_core_stacks else None,
        distinct_core_stacks=distinct_core_stacks,
        avoid_lineups=avoid_lineups, min_player_difference=min_player_difference,
    )


def _supply(players: list[PlayerProjection]) -> dict[str, list[tuple[str, int]]]:
    """Per position, the five highest-priced players the solver was allowed to use -- what an under-spend diagnosis needs."""
    out: dict[str, list[tuple[str, int]]] = {}
    for pos in ("QB", "RB", "WR", "TE", "DST"):
        ranked = sorted((p for p in players if p.position == pos and p.salary), key=lambda p: -p.salary)[:5]
        out[pos] = [(p.display_name, p.salary) for p in ranked]
    return out


def _supply_summary(players: list[PlayerProjection]) -> str:
    return "; ".join(f"{pos} " + ", ".join(f"{n} ${s:,}" for n, s in top) for pos, top in _supply(players).items())


def _verify(lineup: Lineup, pool: Pool, core_ids: set[str], playable_ids: set[str], opponent_of: dict[str, str]) -> list[str]:
    """Independent post-solve checks (do not trust the solver to have honoured its own constraints)."""
    problems = []
    ids = {p.canonical_id for p in lineup.players}
    if not ids <= playable_ids:
        problems.append(f"players outside the pool: {sorted(ids - playable_ids)}")
    bad_status = [p.display_name for p in lineup.players if p.dk_injury_status in EXCLUDED_INJURY_STATUSES]
    if bad_status:
        problems.append(f"excluded-status players rostered: {bad_status}")
    if len(ids & core_ids) < pool.rules.min_core:
        problems.append(f"only {len(ids & core_ids)} core players (rules require {pool.rules.min_core})")
    problems += bring_back_violations(
        lineup, opponent_of,
        forbid_pass_catcher_bring_back=pool.rules.forbid_pass_catcher_bring_back, forbid_rb_bring_back=pool.rules.forbid_rb_bring_back,
    )
    qb = next((p for p in lineup.players if p.position == "QB"), None)
    if qb is None or not any(p.team == qb.team and p.position in ("WR", "TE") for p in lineup.players):
        problems.append("missing the required QB + same-team pass-catcher stack")
    return problems


def build_agent_lineups(
    pool: Pool,
    projections: list[PlayerProjection],
    values: dict[str, float],
    *,
    n: int,
    opponent_of: dict[str, str],
    game_id_by_team: dict[str, str] | None = None,
    seed_core_stacks: list[frozenset[str]] | None = None,
    pair_signs: list[PairSign] | tuple[PairSign, ...] = (),
    distinct_core_stacks: bool = True,
    avoid_lineups: list[frozenset[str]] | None = None,
    min_player_difference: int = 1,
) -> PoolBuildResult:
    refs = player_refs_from_projections(projections, game_id_by_team or {})
    by_id = {p.canonical_id: p for p in projections}
    attempts: list[dict] = []
    current = pool

    def dry_stacks(playable_refs: list[PlayerRef]) -> int:
        ids = {r.canonical_id for r in playable_refs}
        sub = [by_id[i] for i in ids if i in by_id and i in values]
        core = {e.canonical_id for e in current.entries if e.tier == "core"} & ids
        try:
            return len(_solve(current, sub, core, values, n=3, opponent_of=opponent_of, seed_core_stacks=None, distinct_core_stacks=True))
        except LineupGenerationError:
            return 0

    for step in range(4):  # 0 = as given, then relax_min_core, promote_at_failing, promote again after relaxing
        report = validate_pool(current, refs, distinct_stack_count=dry_stacks)
        attempts.append({"step": step, "widened_steps": list(current.widened_steps), "errors": [(v.code, v.message) for v in report.errors],
                         "counts": report.counts, "core_count": report.core_count, "stripped": list(report.stripped)})
        fatal = [v for v in report.errors if not v.code.startswith(_WIDENABLE_PREFIXES)]
        if fatal:
            raise PoolBuildFailure(pool.agent_id, "pool_validation", "; ".join(v.message for v in fatal[:3]), {"attempts": attempts})
        if report.ok:
            missing = [i for i in report.playable_ids if i in by_id and i not in values]
            if missing:
                raise PoolBuildFailure(pool.agent_id, "values", f"no tail value for {len(missing)} playable player(s), e.g. {missing[:3]}", {"attempts": attempts, "missing_values": missing})
            playable = [by_id[i] for i in report.playable_ids if i in by_id]
            core_ids = {e.canonical_id for e in current.entries if e.tier == "core"} & set(report.playable_ids)
            try:
                lineups = _solve(current, playable, core_ids, values, n=n, opponent_of=opponent_of,
                                 seed_core_stacks=seed_core_stacks, distinct_core_stacks=distinct_core_stacks,
                                 avoid_lineups=avoid_lineups, min_player_difference=min_player_difference)
            except LineupGenerationError as exc:
                attempts[-1]["solve_error"] = str(exc)
                lineups = []
            if lineups:
                playable_ids = set(report.playable_ids)
                warnings: list[Violation] = []
                for i, lu in enumerate(lineups):
                    problems = _verify(lu, current, core_ids, playable_ids, opponent_of)
                    if problems:
                        raise PoolBuildFailure(pool.agent_id, "post_solve_verification", f"lineup {i + 1}: " + "; ".join(problems), {"attempts": attempts, "lineup": [p.display_name for p in lu.players]})
                    if lu.total_salary < MIN_SALARY_USED:
                        raise PoolBuildFailure(
                            pool.agent_id, "underspend",
                            f"lineup {i + 1} spends only ${lu.total_salary:,} of $50,000 (${50_000 - lu.total_salary:,} unspent): the pool does not give the solver "
                            f"anything worth buying with the rest. Pool supply by position (top salaries, core+eligible): {_supply_summary(playable)}",
                            {"attempts": attempts, "lineup": [p.display_name for p in lu.players], "supply": _supply(playable)},
                        )
                    for v in lint_lineup_pair_signs({p.canonical_id for p in lu.players}, list(pair_signs)):
                        if v.severity == "error":
                            raise PoolBuildFailure(pool.agent_id, "pair_sign_lint", f"lineup {i + 1}: {v.message}", {"attempts": attempts, "lineup": [p.display_name for p in lu.players]})
                        warnings.append(v)
                return PoolBuildResult(pool.agent_id, tuple(lineups), current, report, tuple(warnings))
        # widen at the failing spot and retry
        nxt = relax_min_core(current) if step % 2 == 0 else promote_at_failing(current, refs, report)
        if nxt is current:
            nxt = promote_at_failing(current, refs, report) if step % 2 == 0 else relax_min_core(current)
        current = nxt
    raise PoolBuildFailure(pool.agent_id, "infeasible_after_widening", "no valid lineup after the permitted widening steps", {"attempts": attempts})
