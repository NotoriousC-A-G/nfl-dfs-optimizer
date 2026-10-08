"""Correlation links derived from an agent's views (Chris, 2026-10-09: "stack" is one case of a broader idea -- players whose outcomes move
together because the same thing drives them).

The declared BETS (`Variation.bets`) are required in the lineup. This module adds the OPTIONAL positive links a view implies, as pair bonuses
for the solver (in lineup-points, draft, not backtested), so that when the lineup has room it takes the players that co-move:

- **lead protection** -- in a game where the agent's view has a team ahead by at least `LEAD_MARGIN` points (the branch's `margin_shift`, favorite
  perspective), that team's top running backs go with its own defense: the same lead produces the carries and the takeaways.

The QB + pass-catcher and bring-back links already live in `pool_solve.stack_bonus_pairs`. Negative links (a QB against the defense he faces,
a pair an analyst called negative) are the existing DST-correlation penalty and the pair-sign lint. The world-sampling scorer measures real
co-movement; these are the crude proxies the solver can use while generating candidates.
"""

from __future__ import annotations

from nfl_dfs.build.evidence.contracts import EvidencePacket
from nfl_dfs.build.pool.contracts import PlayerRef
from nfl_dfs.build.thesis.contracts import GameThesis

LEAD_MARGIN = 2.0
LEAD_PROTECT_BONUS = 1.0
LEAD_PROTECT_TOP_RBS = 2


def lead_protect_pairs(
    universe: list[PlayerRef], theses: dict[str, GameThesis], packets: dict[str, EvidencePacket], views: tuple[str, ...] | list[str],
) -> list[tuple[str, str, float]]:
    pairs: list[tuple[str, str, float]] = []
    for ref in views:
        gid, _, bid = ref.partition(":")
        thesis, packet = theses.get(gid), packets.get(gid)
        if thesis is None or packet is None:
            continue
        branch = next((b for b in thesis.branches if b.branch_id == bid), None)
        if branch is None or abs(branch.margin_shift) < LEAD_MARGIN:
            continue
        favorite = packet.lines.favorite
        other = packet.away if favorite == packet.home else packet.home
        ahead = favorite if branch.margin_shift > 0 else other
        dsts = [p for p in universe if p.position == "DST" and p.team == ahead and p.game_id == gid]
        rbs = sorted((p for p in universe if p.position == "RB" and p.team == ahead and p.game_id == gid), key=lambda p: -(p.projection or 0.0))
        for d in dsts:
            for rb in rbs[:LEAD_PROTECT_TOP_RBS]:
                pairs.append((rb.canonical_id, d.canonical_id, LEAD_PROTECT_BONUS))
    return pairs
