"""Parse and validate the expert's answer into one `ExpertAgentOutput` per pool agent.

Per agent the expert returns either a pool (a build thesis backing branches of the game theses, a default tier,
group-level tiers by team/position/game, and named overrides) or `"unavailable": true` with a reason. Hard sliders
come from `agents.py`, never from the model. Checks beyond the schema (QA/football/model-analytics reviews):
referenced branches and players exist; every core/exclude assignment has a reason; pools are NARROW (a broad pool
converges every agent on the same value plays); a backed branch meets the agent's probability floor and a game meets
its minimum total; at most two agents back the same game; near-duplicate core tiers are flagged.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from nfl_dfs.build.agents import POOL_AGENT_BY_ID, POOL_AGENTS, PoolAgentSpec
from nfl_dfs.build.common import Violation
from nfl_dfs.build.pool.contracts import TIERS, BuildThesis, ExpertAgentOutput, GroupTier, PlayerRef, PoolEntry
from nfl_dfs.build.pool.expand import expand_pool
from nfl_dfs.build.pool.validate import core_overlap
from nfl_dfs.build.thesis.parse import _strip_fences

MAX_BROAD_FRACTION = 0.45  # (core + eligible) / slate universe above this is "not narrow"
MIN_CORE, MAX_CORE = 4, 16
MAX_AGENTS_PER_GAME = 2
POSITIONS = ("QB", "RB", "WR", "TE", "DST")


@dataclass(frozen=True)
class ExpertResult:
    outputs: tuple[ExpertAgentOutput, ...]
    unavailable: dict[str, str] = field(default_factory=dict)  # agent_id -> reason
    portfolio_notes: str = ""


def _game_of_ref(ref: str) -> str:
    return ref.split(":", 1)[0]


def parse_expert_response(
    raw: str | dict[str, Any],
    *,
    universe: list[PlayerRef],
    branch_probs: dict[str, float],  # "GAME:branch_id" -> probability, from the validated theses
    game_totals: dict[str, float | None],
    agents: tuple[PoolAgentSpec, ...] = POOL_AGENTS,
) -> tuple[ExpertResult | None, list[Violation]]:
    try:
        d = json.loads(_strip_fences(raw)) if isinstance(raw, str) else raw
        if not isinstance(d, dict) or "agents" not in d:
            raise ValueError("top-level JSON must be an object with an 'agents' list")
    except (ValueError, TypeError) as exc:
        return None, [Violation("parse", f"response is not valid JSON: {exc}")]
    v: list[Violation] = []
    ids = {p.canonical_id for p in universe}
    teams = {p.team for p in universe}
    games = {p.game_id for p in universe if p.game_id}
    given = {a.get("agent_id"): a for a in d["agents"] if isinstance(a, dict)}
    for spec in agents:
        if spec.agent_id not in given:
            v.append(Violation("missing_agent", f"no entry for agent {spec.agent_id!r} (give a pool or set unavailable with a reason)", spec.agent_id))
    for unknown in set(given) - {a.agent_id for a in agents}:
        v.append(Violation("unknown_agent", f"{unknown!r} is not a pool agent", str(unknown)))

    outputs: list[ExpertAgentOutput] = []
    unavailable: dict[str, str] = {}
    for spec in agents:
        a = given.get(spec.agent_id)
        if a is None:
            continue
        try:
            out, unavail = _parse_agent(a, spec, ids, teams, games, v)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            v.append(Violation("parse", f"{spec.agent_id}: does not match the schema: {type(exc).__name__}: {exc}", spec.agent_id))
            continue
        if unavail is not None:
            unavailable[spec.agent_id] = unavail
            continue
        _check_agent(out, spec, universe, branch_probs, game_totals, v)
        outputs.append(out)

    backs = Counter(g for o in outputs for g in {_game_of_ref(r) for r in o.build_thesis.backs})
    for g, n in backs.items():
        if n > MAX_AGENTS_PER_GAME:
            v.append(Violation("game_concentration", f"{n} agents back {g} (at most {MAX_AGENTS_PER_GAME}); spread the agents across games", g))
    pools = [expand_pool(o, universe) for o in outputs]
    v += core_overlap(pools)
    return ExpertResult(tuple(outputs), unavailable, str(d.get("portfolio_notes", ""))), v


def _parse_agent(a: dict, spec: PoolAgentSpec, ids: set, teams: set, games: set, v: list[Violation]) -> tuple[ExpertAgentOutput | None, str | None]:
    if a.get("unavailable"):
        reason = str(a.get("reason", "")).strip()
        if not reason:
            v.append(Violation("unavailable_reason", f"{spec.agent_id}: 'unavailable' needs a reason", spec.agent_id))
        return None, reason or "no reason given"
    bt = a["build_thesis"]
    thesis = BuildThesis(tuple(bt.get("backs", ())), tuple(bt.get("avoids", ())), tuple(bt.get("hedges", ())), tuple(bt.get("stack_anchor", ())), bt.get("reason", ""))
    for cid in thesis.stack_anchor:
        if cid not in ids:
            v.append(Violation("unknown_player", f"{spec.agent_id}: stack_anchor id {cid!r} is not on the slate", spec.agent_id))
    if not thesis.reason.strip():
        v.append(Violation("build_thesis_reason", f"{spec.agent_id}: the build thesis needs a reason", spec.agent_id))
    default_tier = a["default_tier"]
    if default_tier not in TIERS:
        v.append(Violation("tier", f"{spec.agent_id}: default_tier must be one of {TIERS}", spec.agent_id))
    groups = []
    for g in a.get("group_tiers", ()):
        gt = GroupTier(g["tier"], g.get("reason", ""), g.get("team"), g.get("position"), g.get("game_id"))
        bad = [x for x, ok in (("tier", gt.tier in TIERS), ("team", gt.team in teams or gt.team is None), ("position", gt.position in POSITIONS or gt.position is None), ("game_id", gt.game_id in games or gt.game_id is None)) if not ok]
        if bad:
            v.append(Violation("group_tier", f"{spec.agent_id}: group tier has invalid {bad}: {g}", spec.agent_id))
        if gt.tier in ("core", "exclude") and not gt.reason.strip():
            v.append(Violation("reason_required", f"{spec.agent_id}: a {gt.tier} group tier needs a one-line reason", spec.agent_id))
        groups.append(gt)
    overrides = []
    for o in a.get("overrides", ()):
        pe = PoolEntry(o["player_id"], o["tier"], o.get("reason", ""))
        if pe.canonical_id not in ids:
            v.append(Violation("unknown_player", f"{spec.agent_id}: override id {pe.canonical_id!r} is not on the slate", spec.agent_id))
        if pe.tier not in TIERS:
            v.append(Violation("tier", f"{spec.agent_id}: override tier must be one of {TIERS}", spec.agent_id))
        if pe.tier in ("core", "exclude") and not pe.reason.strip():
            v.append(Violation("reason_required", f"{spec.agent_id}: override for {pe.canonical_id} needs a reason", spec.agent_id))
        overrides.append(pe)
    min_core = int(a.get("min_core", spec.rules.min_core))
    rules = replace(spec.rules, min_core=min(max(min_core, 1), 8))  # hard sliders stay the spec's; only the minimum is the expert's call
    return ExpertAgentOutput(spec.agent_id, thesis, default_tier, tuple(groups), tuple(overrides), rules), None


def _check_agent(out: ExpertAgentOutput, spec: PoolAgentSpec, universe: list[PlayerRef], branch_probs: dict[str, float], game_totals: dict, v: list[Violation]) -> None:
    aid = spec.agent_id
    backs = out.build_thesis.backs
    if not backs:
        v.append(Violation("backs_empty", f"{aid}: a build thesis must back at least one branch (e.g. 'LAR@PHI:b0')", aid))
    for ref in backs + out.build_thesis.avoids + out.build_thesis.hedges:
        if ref not in branch_probs:
            v.append(Violation("unknown_branch", f"{aid}: {ref!r} is not a branch of any game thesis (valid: {sorted(branch_probs)[:6]}...)", aid))
    if spec.must_back_probability is not None and backs:
        if not any(branch_probs.get(r, 0.0) >= spec.must_back_probability for r in backs):
            v.append(Violation("probability_floor", f"{aid}: needs a backed branch with probability >= {spec.must_back_probability}; none of {list(backs)} qualifies -- say unavailable instead", aid))
    if spec.min_game_total is not None:
        for g in {_game_of_ref(r) for r in backs}:
            total = game_totals.get(g)
            if total is None or total < spec.min_game_total:
                v.append(Violation("min_total", f"{aid}: backs {g} with total {total}, below the {spec.min_game_total} minimum", aid))
    pool = expand_pool(out, universe)
    n = len(universe)
    core = sum(1 for e in pool.entries if e.tier == "core")
    live = sum(1 for e in pool.entries if e.tier != "exclude")
    if not (MIN_CORE <= core <= MAX_CORE):
        v.append(Violation("core_size", f"{aid}: {core} core players; use between {MIN_CORE} and {MAX_CORE}", aid))
    if n and live / n > MAX_BROAD_FRACTION:
        v.append(Violation("pool_too_broad", f"{aid}: {live} of {n} players are core/eligible ({live / n:.0%}); a pool above {MAX_BROAD_FRACTION:.0%} converges on the same value plays as every other agent -- exclude more", aid))
