"""Glue: validated theses + packets -> the runner's expert `StageSpec` (one item per slate)."""

from __future__ import annotations

from dataclasses import asdict

from nfl_dfs.build.agents import POOL_AGENTS
from nfl_dfs.build.evidence.contracts import EvidencePacket
from nfl_dfs.build.expert.parse import parse_expert_response
from nfl_dfs.build.expert.prompts import PROMPT_VERSION, branch_refs, expert_prompt
from nfl_dfs.build.pool.contracts import PlayerRef
from nfl_dfs.build.runner import StageSpec, cache_key
from nfl_dfs.build.thesis.contracts import GameThesis

STAGE = "expert"


def expert_spec(
    theses: dict[str, GameThesis], packets: dict[str, EvidencePacket], universe: list[PlayerRef], *, model: str, freshness: str,
) -> StageSpec:
    refs = branch_refs(theses)
    totals = {gid: p.lines.total for gid, p in packets.items()}
    key = cache_key(
        stage=STAGE, prompt_version=PROMPT_VERSION, model=model, freshness=freshness,
        theses=sorted((gid, t.packet_sha, t.model, t.prompt_version, [(b.branch_id, round(b.prob, 6)) for b in t.branches]) for gid, t in theses.items()),
        packets=sorted((gid, p.packet_sha) for gid, p in packets.items()),
    )
    return StageSpec(
        stage=STAGE, item_id="slate", key=key,
        prompt=lambda errs: expert_prompt(theses, packets, POOL_AGENTS, retry_errors=errs),
        parse=lambda text: parse_expert_response(text, universe=universe, branch_probs=refs, game_totals=totals),
        context={"games": sorted(packets), "theses": {gid: asdict(t) for gid, t in theses.items()}},
        meta={"prompt_version": PROMPT_VERSION, "model": model, "freshness": freshness},
    )
