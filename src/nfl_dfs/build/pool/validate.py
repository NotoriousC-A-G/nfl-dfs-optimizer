"""The pool validator: deterministic checks on an agent's pool before the solver sees it, plus
principled widening and a loud failure (QA review, plan D6; Chris, 2026-10-07: no silent fallback).

`validate_pool` never repairs silently. Players that cannot be rostered (injury-excluded, no salary or
projection) are *stripped and reported*; an excluded-status player placed in the `core` tier is a hard
error (the expert must not want someone who can't play). Feasibility checks catch a pool too thin to
build any lineup. If a pool fails, widening is limited to the failing position/stack (`promote_at_failing`)
or the core minimum (`relax_min_core`) -- never a wholesale reset, because a fully widened pool is just
projection-max. A pool that still fails is reported with a diagnostic, and nothing is substituted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Callable

from nfl_dfs.build.common import Violation
from nfl_dfs.build.pool.contracts import TIERS, Pool, PoolEntry, PlayerRef
from nfl_dfs.optimizer.lineup import EXCLUDED_INJURY_STATUSES, SALARY_CAP

# Draft feasibility floors. The first cut (QB 3 / RB 6 / WR 9 / TE 3 / DST 3, 95% cap reach) was QA's; the 2026-10-07
# real-slate rehearsal showed it forced widening on EVERY narrow pool (and one hard salary-starved failure at 94% of
# the cap), turning a rare fallback into the norm. These are what building two distinct-stack lineups actually needs.
MIN_COUNTS = {"QB": 2, "RB": 4, "WR": 6, "TE": 2, "DST": 2}
MIN_QB_TEAMS = 2
MIN_STACKABLE_QB_TEAMS = 2
MIN_STACK_CATCHERS = 2
MIN_DISTINCT_STACKS = 3
CAP_REACH_FRACTION = 0.90
DEFAULT_TIER_REASON = "default tier"  # the reason `expand_pool` gives players nobody assigned a tier
FLEX_POSITIONS = ("RB", "WR", "TE")


@dataclass(frozen=True)
class PoolReport:
    agent_id: str
    violations: tuple[Violation, ...]
    stripped: tuple[tuple[str, str], ...]  # (canonical_id, why) -- removed from the playable set, reported not hidden
    playable_ids: tuple[str, ...]
    counts: dict[str, int]
    core_count: int

    @property
    def errors(self) -> tuple[Violation, ...]:
        return tuple(x for x in self.violations if x.severity == "error")

    @property
    def ok(self) -> bool:
        return not self.errors


def _cheapest_roster_cost(players: list[PlayerRef]) -> int | None:
    by = {pos: sorted(p.salary for p in players if p.position == pos) for pos in ("QB", "RB", "WR", "TE", "DST")}
    need = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
    if any(len(by[pos]) < n for pos, n in need.items()):
        return None
    cost, leftovers = 0, []
    for pos, n in need.items():
        cost += sum(by[pos][:n])
        if pos in FLEX_POSITIONS:
            leftovers.extend(by[pos][n:])
    if not leftovers:
        return None
    return cost + min(leftovers)


def _top_projection_roster_salary(players: list[PlayerRef]) -> int | None:
    by = {pos: sorted((p for p in players if p.position == pos), key=lambda p: -(p.projection or 0.0)) for pos in ("QB", "RB", "WR", "TE", "DST")}
    need = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
    if any(len(by[pos]) < n for pos, n in need.items()):
        return None
    chosen, leftovers = [], []
    for pos, n in need.items():
        chosen += by[pos][:n]
        if pos in FLEX_POSITIONS:
            leftovers += by[pos][n:]
    if not leftovers:
        return None
    chosen.append(max(leftovers, key=lambda p: p.projection or 0.0))
    return sum(p.salary for p in chosen)


def validate_pool(
    pool: Pool,
    universe: list[PlayerRef],
    *,
    excluded_statuses: frozenset[str] = EXCLUDED_INJURY_STATUSES,
    distinct_stack_count: Callable[[list[PlayerRef]], int] | None = None,
) -> PoolReport:
    v: list[Violation] = []
    stripped: list[tuple[str, str]] = []
    by_id = {p.canonical_id: p for p in universe}

    seen: dict[str, str] = {}
    for e in pool.entries:
        path = f"entries.{e.canonical_id}"
        if e.tier not in TIERS:
            v.append(Violation("tier", f"{e.canonical_id}: tier must be one of {TIERS}, got {e.tier!r}", path))
        if e.canonical_id not in by_id:
            v.append(Violation("unknown_player", f"player id {e.canonical_id!r} is not on the slate", path))
        if e.canonical_id in seen and seen[e.canonical_id] != e.tier:
            v.append(Violation("contradictory_tiers", f"{e.canonical_id} appears in both '{seen[e.canonical_id]}' and '{e.tier}'", path))
        elif e.canonical_id in seen:
            v.append(Violation("duplicate_entry", f"{e.canonical_id} appears more than once", path))
        seen[e.canonical_id] = e.tier
        if e.tier in ("core", "exclude") and not e.reason.strip():
            v.append(Violation("reason_required", f"{e.canonical_id}: a {e.tier}-tier entry needs a one-line reason", path))

    playable: list[PlayerRef] = []
    core_ids: set[str] = set()
    for e in pool.entries:
        p = by_id.get(e.canonical_id)
        if p is None or e.tier == "exclude" or e.tier not in TIERS:
            continue
        if p.status in excluded_statuses:
            if e.tier == "core":
                v.append(Violation("core_unavailable", f"{p.name} ({p.team}) is status {p.status!r} and cannot be a core player", f"entries.{e.canonical_id}"))
            stripped.append((e.canonical_id, f"status {p.status}"))
            continue
        if p.salary is None or p.projection is None:
            stripped.append((e.canonical_id, "missing salary or projection"))
            continue
        playable.append(p)
        if e.tier == "core":
            core_ids.add(e.canonical_id)

    counts = Counter(p.position for p in playable)
    for pos, floor in MIN_COUNTS.items():
        if counts.get(pos, 0) < floor:
            v.append(Violation(f"feasibility_{pos}", f"only {counts.get(pos, 0)} playable {pos} in the pool, need at least {floor}", "pool"))
    qb_teams = {p.team for p in playable if p.position == "QB"}
    if len(qb_teams) < MIN_QB_TEAMS and counts.get("QB", 0) >= MIN_COUNTS["QB"]:
        v.append(Violation("feasibility_QB", f"playable QBs come from only {len(qb_teams)} teams, need at least {MIN_QB_TEAMS}", "pool"))
    catchers = Counter(p.team for p in playable if p.position in ("WR", "TE"))
    stackable = [t for t in qb_teams if catchers.get(t, 0) >= MIN_STACK_CATCHERS]
    if len(stackable) < MIN_STACKABLE_QB_TEAMS:
        v.append(Violation("stack_teams", f"only {len(stackable)} QB team(s) have {MIN_STACK_CATCHERS}+ WR/TE in the pool, need {MIN_STACKABLE_QB_TEAMS} stackable teams", "pool"))

    floor_cost = _cheapest_roster_cost(playable)
    if floor_cost is not None and floor_cost > SALARY_CAP:
        v.append(Violation("cap_floor", f"even the cheapest legal roster costs ${floor_cost:,}, over the ${SALARY_CAP:,} cap", "pool"))
    top_cost = _top_projection_roster_salary(playable)
    if top_cost is not None and top_cost < CAP_REACH_FRACTION * SALARY_CAP:
        v.append(Violation("salary_starved", f"the highest-projection roster costs only ${top_cost:,} (< {CAP_REACH_FRACTION:.0%} of the cap): the pool is salary-starved", "pool"))

    if len(core_ids) < pool.rules.min_core:
        v.append(Violation("min_core", f"only {len(core_ids)} playable core players, the rules require at least {pool.rules.min_core}", "pool"))

    if distinct_stack_count is not None and not [x for x in v if x.code.startswith(("feasibility", "stack", "cap", "salary"))]:
        n = distinct_stack_count(playable)
        if n < MIN_DISTINCT_STACKS:
            v.append(Violation("distinct_stacks", f"a dry solve found only {n} distinct core stack(s), need at least {MIN_DISTINCT_STACKS}", "pool"))

    return PoolReport(pool.agent_id, tuple(v), tuple(stripped), tuple(p.canonical_id for p in playable), dict(counts), len(core_ids))


def relax_min_core(pool: Pool, *, floor: int = 1) -> Pool:
    """Widening step 2: lower the core minimum by one (never below `floor`)."""
    new = max(floor, pool.rules.min_core - 1)
    if new == pool.rules.min_core:
        return pool
    return replace(pool, rules=replace(pool.rules, min_core=new), widened_steps=pool.widened_steps + (f"relaxed min_core {pool.rules.min_core} -> {new}",))


def promote_at_failing(
    pool: Pool, universe: list[PlayerRef], report: PoolReport,
    *, excluded_statuses: frozenset[str] = EXCLUDED_INJURY_STATUSES, per_position: int = 3,
) -> Pool:
    """Widening step 3: for each *failing* position (or the stack check), make the next-best players by
    projection `eligible`, tagged with the check that triggered it. Only players nobody deliberately tiered are
    promotable: one on the default tier is RE-TIERED IN PLACE (never a second, contradictory entry), one absent from
    the pool is appended, and an exclusion the expert made with a reason is NEVER undone. Never touches positions
    that passed, never promotes an injury-excluded player."""
    existing = {e.canonical_id: e for e in pool.entries}
    live = {cid for cid, e in existing.items() if e.tier != "exclude"}  # already usable in the pool
    promoted: dict[str, PoolEntry] = {}
    log: list[str] = []

    def promotable(p: PlayerRef) -> bool:
        prior = existing.get(p.canonical_id)
        # An exclusion the expert made ON PURPOSE (it carries a reason) is never undone by widening: Chalk Pivot's
        # whole identity is barring the chalk game's top-owned players, and a widening that re-adds them defeats the
        # agent. Only players nobody tiered (absent, or the default tier) can be promoted; if that is not enough the
        # build fails loudly and the expert must widen its own pool.
        deliberately_excluded = prior is not None and prior.tier == "exclude" and prior.reason != DEFAULT_TIER_REASON
        return (
            p.canonical_id not in live and p.canonical_id not in promoted and not deliberately_excluded
            and p.status not in excluded_statuses and p.salary is not None and p.projection is not None
        )

    failing = {x.code.removeprefix("feasibility_") for x in report.errors if x.code.startswith("feasibility_")}
    for pos in sorted(failing & set(MIN_COUNTS)):
        picks = sorted((p for p in universe if p.position == pos and promotable(p)), key=lambda p: -p.projection)[:per_position]
        for p in picks:
            promoted[p.canonical_id] = PoolEntry(p.canonical_id, "eligible", f"widened: {pos} depth check failed")
        if picks:
            log.append(f"promoted {len(picks)} {pos} (feasibility_{pos})")
    if any(x.code == "stack_teams" for x in report.errors):
        qb_teams = sorted({p.team for p in universe if p.position == "QB" and p.canonical_id in live})
        for team in qb_teams:
            picks = sorted((p for p in universe if p.team == team and p.position in ("WR", "TE") and promotable(p)), key=lambda p: -p.projection)[:MIN_STACK_CATCHERS]
            for p in picks:
                promoted[p.canonical_id] = PoolEntry(p.canonical_id, "eligible", "widened: stackable-team check failed")
        log.append("promoted pass catchers for QB teams (stack_teams)")
    if not promoted:
        return pool
    entries = tuple(promoted.get(e.canonical_id, e) for e in pool.entries)  # re-tier in place
    entries += tuple(e for cid, e in promoted.items() if cid not in existing)  # append the genuinely new
    return replace(pool, entries=entries, widened_steps=pool.widened_steps + tuple(log))


def core_overlap(pools: list[Pool], *, warn_at: float = 0.70) -> list[Violation]:
    """Warn when two agents' core tiers largely coincide (the expert wrote near-identical pools)."""
    cores = {p.agent_id: {e.canonical_id for e in p.entries if e.tier == "core"} for p in pools}
    out = []
    ids = sorted(cores)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if not cores[a] or not cores[b]:
                continue
            share = len(cores[a] & cores[b]) / min(len(cores[a]), len(cores[b]))
            if share >= warn_at:
                out.append(Violation("core_overlap", f"{a} and {b} share {share:.0%} of the smaller core tier -- pools are near-duplicates", "pools", "warning"))
    return out
