"""The MATERIAL facts of a game's evidence packet -- what should make an analyst's cached thesis stale.

The packet hash changes whenever any number in the packet moves, including a vendor projection drifting by a tenth of a point
between two pulls. Keying the game analysts on it would force all of them to be re-asked on Sunday at noon for a change that
cannot alter a game thesis. The fingerprint hashes only what a thesis is actually about: the line (to the half point), the
unit metrics, who is available and what that vacates, and the weather (coarsely). A cached thesis is still RE-VALIDATED
against the current packet on every load (runner.collect_results), so a thesis naming a player who has since dropped out of
the packet is discarded and re-asked rather than trusted.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from nfl_dfs.build.evidence.contracts import EvidencePacket


def _half(x: float | None) -> float | None:
    return None if x is None else round(x * 2) / 2


def _coarse(v: Any) -> Any:
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return round(v / 5) * 5
    if isinstance(v, dict):
        return {k: _coarse(x) for k, x in sorted(v.items())}
    return str(v)


def material_fingerprint(p: EvidencePacket) -> str:
    body = {
        "game": p.game_id, "season": p.season, "week": p.week,
        "lines": {"favorite": p.lines.favorite, "spread": _half(p.lines.abs_spread), "total": _half(p.lines.total)},
        "units": {
            t: {k: [None if mv.value is None else round(mv.value, 4), mv.n] for k, mv in sorted({**te.metrics, **te.def_metrics}.items())}
            for t, te in sorted(p.teams.items())
        },
        # availability: the decision per player, not the basis text or capture time
        "availability": sorted((a.name, a.team, a.decision) for a in p.availability),
        "status": sorted((pl.canonical_id, pl.status_after_q_pass or pl.injury_status or "") for pl in p.players if pl.status_after_q_pass or pl.injury_status),
        "vacated": sorted((v.canonical_id, v.status, round(v.carry_share, 2), round(v.target_share, 2)) for v in p.vacated),
        "weather": _coarse(p.weather),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
