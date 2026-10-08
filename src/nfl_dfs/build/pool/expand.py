"""Expand the expert's compact output into one `PoolEntry` per slate player.

Order of application (later wins): the default tier -> each `GroupTier` in order -> named
per-player overrides. Overrides naming a player not on the slate are kept as entries on purpose so
the validator reports them (an unknown id is a model error to surface, not to silently drop).
"""

from __future__ import annotations

from nfl_dfs.build.pool.contracts import DERIVED, SCHEMA_VERSION, ExpertAgentOutput, GroupTier, Pool, PoolEntry, PlayerRef


def _matches(sel: GroupTier, p: PlayerRef) -> bool:
    return (
        (sel.team is None or p.team == sel.team)
        and (sel.position is None or p.position == sel.position)
        and (sel.game_id is None or p.game_id == sel.game_id)
    )


def expand_pool(output: ExpertAgentOutput, universe: list[PlayerRef], derived: dict[str, tuple[str, str]] | None = None) -> Pool:
    """`derived` = the script's tier for each player (`pool/derive.py`); used for every player the expert did not name when the agent's
    default tier is "derived" (without it those players fall back to reach)."""
    def base(p: PlayerRef) -> PoolEntry:
        if output.default_tier == DERIVED:
            tier, reason = (derived or {}).get(p.canonical_id, ("reach", "no script given"))
            return PoolEntry(p.canonical_id, tier, reason)
        return PoolEntry(p.canonical_id, output.default_tier, "default tier")

    entries: dict[str, PoolEntry] = {p.canonical_id: base(p) for p in universe}
    for sel in output.group_tiers:
        for p in universe:
            if _matches(sel, p):
                entries[p.canonical_id] = PoolEntry(p.canonical_id, sel.tier, sel.reason)
    ordered = list(entries.values())
    for o in output.overrides:
        if o.canonical_id in entries:
            ordered = [o if e.canonical_id == o.canonical_id else e for e in ordered]
            entries[o.canonical_id] = o
        else:
            ordered.append(o)  # unknown id: kept so `validate_pool` flags it
    return Pool(SCHEMA_VERSION, output.agent_id, output.build_thesis, tuple(ordered), output.rules)
