"""Glue: evidence packets -> runner `StageSpec`s for the game-analyst stage."""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable

import numpy as np

from nfl_dfs.build.evidence.contracts import EvidencePacket
from nfl_dfs.build.runner import StageSpec, cache_key
from nfl_dfs.build.thesis.parse import parse_analyst_response
from nfl_dfs.build.thesis.prompts import PROMPT_VERSION, analyst_prompt

STAGE = "analyst"


def analyst_specs(
    packets: dict[str, EvidencePacket], league_value_fn: Callable[[str], np.ndarray], *, model: str, freshness: str,
) -> list[StageSpec]:
    """One spec per game. `freshness` is a token for anything outside the packet that should invalidate a
    cached answer (e.g. a hash of the Q-override file + the injury capture the packet was built from)."""
    specs = []
    for game_id in sorted(packets):
        packet = packets[game_id]
        key = cache_key(packet_sha=packet.packet_sha, prompt_version=PROMPT_VERSION, model=model, freshness=freshness, stage=STAGE)
        specs.append(StageSpec(
            stage=STAGE, item_id=game_id, key=key,
            prompt=lambda errs, p=packet: analyst_prompt(p, league_value_fn, retry_errors=errs),
            parse=lambda text, p=packet: parse_analyst_response(text, p, league_value_fn, prompt_version=PROMPT_VERSION, model=model),
            context={"packet": asdict(packet)},
            meta={"prompt_version": PROMPT_VERSION, "model": model, "freshness": freshness, "packet_sha": packet.packet_sha},
        ))
    return specs
