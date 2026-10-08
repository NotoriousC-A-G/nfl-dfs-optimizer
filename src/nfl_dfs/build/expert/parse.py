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
from nfl_dfs.build.pool.contracts import DEFAULT_TIERS, DERIVED, MECHANISMS, SPEND_VALUES, TIERS, Bet, Variation, BuildThesis, ExpertAgentOutput, GroupTier, PlayerRef, PoolEntry
from nfl_dfs.build.pool.bets import roster_shape_problems
from nfl_dfs.build.pool.expand import expand_pool
from nfl_dfs.build.pool.validate import core_overlap
from nfl_dfs.build.thesis.parse import _strip_fences

MAX_BROAD_FRACTION = 0.45  # (core + eligible) / slate universe above this is "not narrow"
MIN_CORE, MAX_CORE = 4, 16
MAX_AGENTS_PER_GAME = 2
MAX_SCRIPTS = 3  # variations per agent
MAX_BETS = 3  # bets (correlated groups) per variation
POSITIONS = ("QB", "RB", "WR", "TE", "DST")


@dataclass(frozen=True)
class ExpertResult:
    outputs: tuple[ExpertAgentOutput, ...]
    unavailable: dict[str, str] = field(default_factory=dict)  # agent_id -> reason
    portfolio_notes: str = ""


def _stack_games(out: ExpertAgentOutput, universe: list[PlayerRef]) -> set[str]:
    """The games an agent places a pass-volume or shootout bet in (its stacks); a lead-protection or pressure bet is not a stack."""
    game_of = {p.canonical_id: p.game_id for p in universe}
    return {
        game_of[c] for var in out.variations for b in var.bets if b.mechanism in ("pass_volume", "shootout") for c in b.players if game_of.get(c)
    } or {game_of[c] for var in out.variations for c in var.stack if game_of.get(c)}


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
            out, unavail = _parse_agent(a, spec, ids, teams, games, v, {p.canonical_id: p for p in universe})
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            v.append(Violation("parse", f"{spec.agent_id}: does not match the schema: {type(exc).__name__}: {exc}", spec.agent_id))
            continue
        if unavail is not None:
            unavailable[spec.agent_id] = unavail
            continue
        _check_agent(out, spec, universe, branch_probs, game_totals, v)
        outputs.append(out)

    stacked = Counter(g for o in outputs for g in _stack_games(o, universe))
    for g, n in stacked.items():
        if n > MAX_AGENTS_PER_GAME:
            v.append(Violation("game_concentration", f"{n} agents stack in {g} (at most {MAX_AGENTS_PER_GAME}); spread the agents' stacks across games", g))
    pools = [expand_pool(o, universe) for o in outputs]
    v += core_overlap(pools)
    return ExpertResult(tuple(outputs), unavailable, str(d.get("portfolio_notes", ""))), v


def _parse_agent(a: dict, spec: PoolAgentSpec, ids: set, teams: set, games: set, v: list[Violation], by_id: dict | None = None) -> tuple[ExpertAgentOutput | None, str | None]:
    if a.get("unavailable"):
        reason = str(a.get("reason", "")).strip()
        if not reason:
            v.append(Violation("unavailable_reason", f"{spec.agent_id}: 'unavailable' needs a reason", spec.agent_id))
        return None, reason or "no reason given"
    bt = a["build_thesis"]
    by_id = by_id or {}
    variations = _parse_variations(a, bt, spec, ids, by_id, v)
    # the build thesis's `backs` is the union of the variations' views (the record of what the agent believes), `stack_anchor` of their stacks
    backs = tuple(dict.fromkeys(r for var in variations for r in var.views))
    anchors = tuple(dict.fromkeys(i for var in variations for i in var.stack))
    thesis = BuildThesis(backs, tuple(bt.get("avoids", ())), tuple(bt.get("hedges", ())), anchors, bt.get("reason", ""))
    spend = []
    for pos, how in (a.get("spend_plan") or {}).items():
        if pos not in POSITIONS or how not in SPEND_VALUES:
            v.append(Violation("spend_plan", f"{spec.agent_id}: spend_plan entry {pos!r}: {how!r} must be a position in {POSITIONS} with a value in {SPEND_VALUES}", spec.agent_id))
        else:
            spend.append((pos, how))
    for cid in thesis.stack_anchor:
        if cid not in ids:
            v.append(Violation("unknown_player", f"{spec.agent_id}: stack_anchor id {cid!r} is not on the slate", spec.agent_id))
    if not thesis.reason.strip():
        v.append(Violation("build_thesis_reason", f"{spec.agent_id}: the build thesis needs a reason", spec.agent_id))
    default_tier = a["default_tier"]
    if default_tier not in DEFAULT_TIERS:
        v.append(Violation("tier", f"{spec.agent_id}: default_tier must be one of {DEFAULT_TIERS}", spec.agent_id))
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
    return ExpertAgentOutput(spec.agent_id, thesis, default_tier, tuple(groups), tuple(overrides), rules, variations, tuple(spend)), None


def _parse_variations(a: dict, bt: dict, spec: PoolAgentSpec, ids: set, by_id: dict, v: list[Violation]) -> tuple[Variation, ...]:
    """The agent's lineup variations. An older-style answer (no `variations`) becomes one: its `backs` as views and its `stack_anchor` as the stack."""
    raw = a.get("variations")
    if raw is None:
        raw = [{"views": list(bt.get("backs", ())), "stack": list(bt.get("stack_anchor", ())), "note": ""}]
    out = []
    aid = spec.agent_id
    if not (1 <= len(raw) <= MAX_SCRIPTS):
        v.append(Violation("variations_count", f"{aid}: give 1-{MAX_SCRIPTS} variations (one lineup each), got {len(raw)}", aid))
    for i, x in enumerate(raw):
        # `bets` is the model: a legacy `stack` list becomes one pass-volume bet
        bets = tuple(Bet(tuple(b.get("players", ())), b.get("mechanism", "other"), b.get("note", "")) for b in x["bets"]) if "bets" in x else (
            (Bet(tuple(x.get("stack", ())), "pass_volume", ""),) if x.get("stack") else ()
        )
        var = Variation(tuple(x.get("views", ())), tuple(dict.fromkeys(c for b in bets for c in b.players)), x.get("note", ""), bets)
        games_seen: dict[str, str] = {}
        for ref in var.views:
            g = _game_of_ref(ref)
            if g in games_seen:
                v.append(Violation("one_view_per_game", f"{aid}: variation {i + 1} holds two views of {g} ({games_seen[g]!r}, {ref!r}); games are independent and a game has one view", aid))
            games_seen[g] = ref
        if not (1 <= len(var.bets) <= MAX_BETS):
            v.append(Violation("bets_count", f"{aid}: variation {i + 1} needs 1-{MAX_BETS} bets (groups of players that move together), got {len(var.bets)}", aid))
        for j, bet in enumerate(var.bets):
            if bet.mechanism not in MECHANISMS:
                v.append(Violation("bet_mechanism", f"{aid}: variation {i + 1} bet {j + 1} mechanism {bet.mechanism!r} must be one of {MECHANISMS}", aid))
            for cid in bet.players:
                if cid not in ids:
                    v.append(Violation("unknown_player", f"{aid}: variation {i + 1} bet {j + 1} player id {cid!r} is not on the slate", aid))
            members = [by_id[c] for c in bet.players if c in by_id]
            if len(set(bet.players)) < 2:
                v.append(Violation("bet_size", f"{aid}: variation {i + 1} bet {j + 1} needs at least two players", aid))
            elif bet.mechanism == "pass_volume" and by_id:
                qbs = [p for p in members if p.position == "QB"]
                if not (qbs and any(p.team == qbs[0].team and p.position in ("WR", "TE") for p in members)):
                    v.append(Violation("stack_shape", f"{aid}: variation {i + 1} bet {j + 1} (pass_volume) needs a QB plus at least one of his pass catchers (WR/TE); got {[p.name for p in members]}", aid))
        union = [by_id[c] for c in var.stack if c in by_id]
        if union:
            for problem in roster_shape_problems(union):
                v.append(Violation("bets_roster", f"{aid}: variation {i + 1}'s bets cannot all be in one lineup -- {problem}. Bets are required in the lineup: drop or shrink a bet (a lineup has ONE quarterback, so only one bet may contain a QB)", aid))
        out.append(var)
    return tuple(out)


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
        for g in _stack_games(out, universe):
            total = game_totals.get(g)
            if total is None or total < spec.min_game_total:
                v.append(Violation("min_total", f"{aid}: stacks in {g} with total {total}, below the {spec.min_game_total} minimum", aid))
    pool = expand_pool(out, universe)
    n = len(universe)
    core = sum(1 for e in pool.entries if e.tier == "core")
    live = sum(1 for e in pool.entries if e.tier in ("core", "eligible"))  # reach is the open remainder, not part of the narrow shape
    # With default_tier "derived" the engine supplies each script's core from the script itself; the expert's explicit core is then optional.
    low = 0 if out.default_tier == DERIVED else MIN_CORE
    if not (low <= core <= MAX_CORE):
        v.append(Violation("core_size", f"{aid}: {core} explicit core players; use between {low} and {MAX_CORE}", aid))
    if n and live / n > MAX_BROAD_FRACTION:
        # An advisory, not a limit (Chris, 2026-10-09): a wide pool is the expert's call. It is surfaced so the build record shows it.
        v.append(Violation("pool_broad", f"{aid}: {live} of {n} players are core/eligible ({live / n:.0%}); past about {MAX_BROAD_FRACTION:.0%} the stand gets diluted and tends to converge on the same plays as other agents", aid, "warning"))
