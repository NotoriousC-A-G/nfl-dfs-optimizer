"""Expert repair loop: when an agent's pool cannot produce good lineups, the expert is told exactly why and re-tiers ONCE.

Chris, 2026-10-07: no hard rules, no silent fallback -- "loud failure with the expert loop". A `PoolBuildFailure` of a repairable
kind (an under-spent lineup, an invalid or infeasible pool, a contradictory pair) becomes a one-agent request through the same
file contract as every other LLM stage: prompt.md written, a backend writes response.json, the runner validates it with the same
expert parser (restricted to that agent) and caches it content-addressed by the failure. The repaired pool is rebuilt; if that
fails too, the agent is reported UNAVAILABLE with both diagnoses. Nothing is padded or substituted.

Not checked on a repaired pool (the full-slate parse did that once): cross-agent core overlap and the two-agents-per-game cap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from nfl_dfs.build.agents import POOL_AGENT_BY_ID, PoolAgentSpec
from nfl_dfs.build.evidence.contracts import EvidencePacket
from nfl_dfs.build.expert.parse import parse_expert_response
from nfl_dfs.build.expert.prompts import _RULES, branch_refs, render_field_layer, render_theses, render_universe
from nfl_dfs.build.pool.contracts import ExpertAgentOutput, PlayerRef
from nfl_dfs.build.runner import (
    STATUS_AWAITING, STATUS_CACHED, STATUS_OK, STATUS_REJECTED, STATUS_RETRY, StageSpec, cache_key, collect_results, prepare_requests,
)
from nfl_dfs.build.thesis.contracts import GameThesis
from nfl_dfs.optimizer.pool_solve import PoolBuildFailure, PoolBuildResult

STAGE = "expert_repair"
PROMPT_VERSION = "expert-repair-v1"
# Failure stages a re-tier can plausibly fix. A missing tail value or a failed post-solve self-check is a code/data bug, not the expert's.
REPAIRABLE_STAGES = ("underspend", "pool_validation", "infeasible_after_widening", "pair_sign_lint")


def output_to_json(out: ExpertAgentOutput) -> dict:
    """The agent's pool in the exact shape the expert answers in, so it can edit what it wrote."""
    bt = out.build_thesis
    return {
        "agent_id": out.agent_id,
        "build_thesis": {"backs": list(bt.backs), "avoids": list(bt.avoids), "hedges": list(bt.hedges), "stack_anchor": list(bt.stack_anchor), "reason": bt.reason},
        "default_tier": out.default_tier,
        "group_tiers": [{"tier": g.tier, "reason": g.reason, "team": g.team, "position": g.position, "game_id": g.game_id} for g in out.group_tiers],
        "overrides": [{"player_id": o.canonical_id, "tier": o.tier, "reason": o.reason} for o in out.overrides],
        "min_core": out.rules.min_core,
    }


def describe_failure(f: PoolBuildFailure) -> str:
    lines = [f"stage: {f.stage}", f"message: {f}"]
    d = f.diagnostics
    if d.get("lineup"):
        lines.append("lineup that failed: " + ", ".join(d["lineup"]))
    if d.get("supply"):
        lines.append("pool supply (top-5 salaries per position the solver could use): " + json.dumps(d["supply"]))
    attempts = d.get("attempts") or []
    if attempts:
        last = attempts[-1]
        lines.append(f"last attempt: counts={last.get('counts')} core={last.get('core_count')} errors={last.get('errors')}")
        widened = [a.get("widened_steps") for a in attempts if a.get("widened_steps")]
        if widened:
            lines.append(f"widening already applied: {widened[-1]}")
    return "\n".join(lines)


def repair_prompt(
    spec: PoolAgentSpec, prior: ExpertAgentOutput, failure: PoolBuildFailure, theses: dict[str, GameThesis], packets: dict[str, EvidencePacket],
    *, retry_errors: list[str] | None = None,
) -> str:
    refs = branch_refs(theses)
    hard = [x for x, on in (("NO WR/TE bring-backs", spec.rules.forbid_pass_catcher_bring_back), ("NO RB bring-backs", spec.rules.forbid_rb_bring_back)) if on]
    parts = [
        f"[prompt {PROMPT_VERSION}]",
        f"You are the lineup-construction EXPERT. The pool you built for the agent {spec.agent_id!r} ({spec.display_name}) FAILED when the lineups "
        "were built from it. Diagnose the failure below and return a CORRECTED pool for this agent only (or mark it unavailable, with a reason, if "
        "this slate honestly has no fit -- do not stretch it). Change what the diagnosis says is wrong; keep the thesis the agent exists for.",
        "",
        "COMMON CAUSES: an under-spent lineup means the pool has nothing worth buying with the remaining cap (add the higher-priced players in the games/"
        "roles you back, or the secure-volume players at those prices); an infeasible or invalid pool means too few players at a position or stack. "
        "Raw expected output matters as much as value per dollar: a pool of cheap value plays caps the lineup's total.",
        "",
        f"AGENT BRIEF: {spec.brief}" + (f"  HARD: {'; '.join(hard)} (enforced in code)." if hard else ""),
        "",
        "WHAT FAILED:", describe_failure(failure), "",
        "YOUR PREVIOUS POOL FOR THIS AGENT:", json.dumps(output_to_json(prior), indent=1), "",
        "The output rules and format are the same as before:", _RULES,
        "Answer with the SAME JSON format, containing exactly ONE entry in \"agents\" (this agent), no markdown fences.", "",
        f"GAME THESES ({len(theses)} games):", render_theses(theses),
        f"VALID BRANCH REFS ({len(refs)}): " + " ; ".join(sorted(refs)), "",
        "FIELD LAYER:", render_field_layer(packets, []), "",
        "PLAYERS (the only ids you may name):", render_universe(packets),
    ]
    if retry_errors:
        parts += ["", "YOUR PREVIOUS ANSWER WAS REJECTED. Fix every problem below and return the corrected full JSON:", *[f"- {e}" for e in retry_errors]]
    return "\n".join(parts)


def repair_spec(
    prior: ExpertAgentOutput, failure: PoolBuildFailure, theses: dict[str, GameThesis], packets: dict[str, EvidencePacket], universe: list[PlayerRef],
    *, model: str, freshness: str,
) -> StageSpec:
    spec = POOL_AGENT_BY_ID[prior.agent_id]
    refs = branch_refs(theses)
    totals = {gid: p.lines.total for gid, p in packets.items()}
    key = cache_key(
        stage=STAGE, prompt_version=PROMPT_VERSION, model=model, freshness=freshness, agent=prior.agent_id, prior=output_to_json(prior),
        failure=describe_failure(failure), packets=sorted((gid, p.packet_sha) for gid, p in packets.items()),
    )

    def parse(text: str):
        result, violations = parse_expert_response(text, universe=universe, branch_probs=refs, game_totals=totals, agents=(spec,))
        return result, violations

    return StageSpec(
        stage=STAGE, item_id=prior.agent_id, key=key,
        prompt=lambda errs: repair_prompt(spec, prior, failure, theses, packets, retry_errors=errs),
        parse=parse,
        context={"agent": prior.agent_id, "failure": describe_failure(failure), "prior": output_to_json(prior)},
        meta={"prompt_version": PROMPT_VERSION, "model": model, "freshness": freshness},
    )


@dataclass(frozen=True)
class AgentOutcome:
    agent_id: str
    status: str  # "built" | "awaiting_repair" | "unavailable"
    result: PoolBuildResult | None = None
    repaired: bool = False
    first_failure: PoolBuildFailure | None = None
    detail: str = ""  # where the repair request is waiting, or why the agent is unavailable
    directory: Path | None = None


def build_with_repair(
    prior: ExpertAgentOutput, build: Callable[[ExpertAgentOutput], PoolBuildResult], theses: dict[str, GameThesis], packets: dict[str, EvidencePacket],
    universe: list[PlayerRef], *, model: str, freshness: str, season: int, week: int, root: Path | None = None,
) -> AgentOutcome:
    """Build the agent; on a repairable failure run the one-round expert loop. `build` maps an expert output to lineups (or raises
    `PoolBuildFailure`). Re-run after the backend writes the repair answer: the same failure is the same request."""
    try:
        return AgentOutcome(prior.agent_id, "built", build(prior))
    except PoolBuildFailure as first:
        if first.stage not in REPAIRABLE_STAGES:
            return AgentOutcome(prior.agent_id, "unavailable", first_failure=first, detail=f"not repairable by the expert ({first.stage}): {first}")
        spec = repair_spec(prior, first, theses, packets, universe, model=model, freshness=freshness)
        kw = dict(season=season, week=week, root=root)
        prepare_requests([spec], **kw)
        res = collect_results([spec], **kw)[0]
        if res.status in (STATUS_AWAITING, STATUS_RETRY):
            which = "retry_prompt.md" if res.status == STATUS_RETRY else "prompt.md"
            return AgentOutcome(prior.agent_id, "awaiting_repair", first_failure=first, directory=res.directory,
                                detail=f"{first.stage}: answer the expert repair request ({res.directory / which})")
        if res.status == STATUS_REJECTED:
            return AgentOutcome(prior.agent_id, "unavailable", first_failure=first, detail=f"the expert's repair was rejected twice ({res.errors[0].message if res.errors else ''}); original failure: {first}")
        assert res.status in (STATUS_OK, STATUS_CACHED)
        repaired = res.result
        if not repaired.outputs:  # the expert declared the agent unavailable
            return AgentOutcome(prior.agent_id, "unavailable", first_failure=first, repaired=True, detail=f"the expert chose unavailable: {repaired.unavailable.get(prior.agent_id, '')}; original failure: {first}")
        try:
            return AgentOutcome(prior.agent_id, "built", build(repaired.outputs[0]), repaired=True, first_failure=first)
        except PoolBuildFailure as second:
            return AgentOutcome(prior.agent_id, "unavailable", first_failure=first, repaired=True, detail=f"failed again after the expert's repair ({second.stage}): {second}; original failure: {first}")
