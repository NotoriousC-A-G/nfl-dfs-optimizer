"""Availability filtering for the stack / role-share / depth-chart layers (Chris, 2026-10-04: the
week-4 stack rationale named Jordan Mason (out), Mike Evans (questionable) and a nominal depth chart
that still listed an out QB as QB1).

`unavailable_player_ids` is the set of players the lineup solver already excludes (`IR`/`OUT`/`D` on
either vendor). The two filters below remove those players from `RoleShareResult.candidates` and
`DepthChartEntry` lists so nothing downstream names them as a stack leader, RB candidate or depth-chart
starter, and promote the next healthy player.

Disclosed judgment calls: (1) remaining candidates' shares are NOT renormalized to absorb the missing
player's volume -- that would be inventing a redistribution; (2) a promoted leader must still clear the
same identification gate (`usage_share._check_gate`), otherwise `identified` is None -- never a guessed
name; (3) Questionable players are NOT removed (a real DFS decision) -- they stay eligible and visible.
"""

from __future__ import annotations

from dataclasses import replace

from nfl_dfs.ingestion.nflverse_depth_charts import DepthChartEntry
from nfl_dfs.ingestion.usage_share import RoleShareResult, _check_gate


def unavailable_player_ids(identities, dk_injury_status: dict[str, str], excluded_statuses: frozenset[str]) -> set[str]:
    """`canonical_id` and nflverse gsis id of every identity whose DK-keyed status is excluded."""
    ids: set[str] = set()
    for identity in identities:
        dk = identity.sources.get("draftkings")
        if dk is None or dk.native_id is None:
            continue
        if dk_injury_status.get(str(dk.native_id)) in excluded_statuses:
            ids.add(identity.canonical_id)
            if identity.nflverse_gsis_id:
                ids.add(identity.nflverse_gsis_id)
    return ids


def filter_role_share_results(results: list[RoleShareResult], unavailable: set[str]) -> list[RoleShareResult]:
    out = []
    for result in results:
        dropped = [c for c in result.candidates if c.player_id in unavailable]
        if not dropped:
            out.append(result)
            continue
        remaining = [c for c in result.candidates if c.player_id not in unavailable]
        names = ", ".join(c.player_name or c.player_id for c in dropped)
        note = f"availability filter removed unavailable candidate(s): {names}"
        identified, gate_passed, gate_reason = None, False, "no available candidates"
        if remaining:
            gate_passed, gate_reason = _check_gate(remaining[0], result.role)
            identified = remaining[0] if gate_passed else None
        out.append(
            replace(
                result,
                candidates=remaining,
                identified=identified,
                gate_passed=gate_passed,
                gate_reason=gate_reason,
                notes=[*result.notes, note],
            )
        )
    return out


def filter_depth_chart(entries: list[DepthChartEntry], unavailable: set[str]) -> list[DepthChartEntry]:
    """Drops unavailable players and re-ranks each (team, position) group 1..n in the original order."""
    kept = [e for e in sorted(entries, key=lambda e: (e.team, e.position, e.depth_rank)) if e.player_id not in unavailable]
    counters: dict[tuple[str, str], int] = {}
    out = []
    for e in kept:
        counters[(e.team, e.position)] = counters.get((e.team, e.position), 0) + 1
        out.append(replace(e, depth_rank=counters[(e.team, e.position)]))
    return out
