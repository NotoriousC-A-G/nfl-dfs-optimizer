"""Tiers derived from an agent's VARIATION (Chris, 2026-10-09).

A lineup is not one game's script. It is one set of beliefs across the slate, and building it is three steps:

1. **Core stack(s)** -- the stack(s) the agent believes in (a QB and his pass catchers). Those players, and teammates the agent's view of
   that game lifts, are **core**.
2. **A view per game** -- for each game the agent has an opinion on (at most one branch per game; games are independent), players are
   tiered by what THEIR game's view does to them: lifted (mean multiplier >= `LIFT`) -> **eligible** ("grab a piece" of a game the agent
   expects to outperform), neutral -> eligible, hurt (< `HURT`) -> **reach** (a game, or a role, the agent expects to underperform: avoid).
   A game with no view uses the full probability-weighted mix: a beneficiary or a player that mix lifts (>= `OTHER_LIFT`) is eligible,
   everyone else reach.
3. **Fill** -- everything else is reach, taken to fit a salary or position need; unavailable players are **exclude**.

A defense follows what the view does to the OPPOSING quarterback: hurt -> eligible, lifted -> reach. The expert's explicit tiers (group
tiers and overrides, each with a reason) adjust all of this. Thresholds are disclosed drafts, not backtested.
"""

from __future__ import annotations

from nfl_dfs.build.evidence.contracts import EvidencePacket, PlayerEvidence
from nfl_dfs.build.pool.contracts import PlayerRef
from nfl_dfs.build.thesis.contracts import GameThesis
from nfl_dfs.build.value.tail_value import conditional_multipliers
from nfl_dfs.optimizer.lineup import EXCLUDED_INJURY_STATUSES

LIFT = 1.15
HURT = 0.95
OTHER_LIFT = 1.10
BENEFICIARY_TARGET_GAIN = 0.03  # share of the team's targets
BENEFICIARY_CARRY_GAIN = 0.05


def is_beneficiary(pe: PlayerEvidence | None) -> bool:
    if pe is None:
        return False
    t = (pe.target_share_expected or 0.0) - (pe.target_share_l4 or 0.0)
    c = (pe.carry_share_expected or 0.0) - (pe.carry_share_l4 or 0.0)
    return t >= BENEFICIARY_TARGET_GAIN or c >= BENEFICIARY_CARRY_GAIN


def opposing_qb_by_dst(universe: list[PlayerRef]) -> dict[str, str]:
    """`{dst_id: the opposing team's highest-projected QB id}` within each DST's game."""
    best: dict[str, PlayerRef] = {}
    for q in universe:
        if q.position == "QB" and q.game_id:
            for d in universe:
                if d.position == "DST" and d.game_id == q.game_id and d.team != q.team:
                    if d.canonical_id not in best or (q.projection or 0.0) > (best[d.canonical_id].projection or 0.0):
                        best[d.canonical_id] = q
    return {d: q.canonical_id for d, q in best.items()}


DST_SENSITIVITY = 0.5  # a defense's value moves half as much, in the opposite direction, as the opposing QB's (draft, not backtested)
DST_MULT_RANGE = (0.9, 1.2)


def dst_multipliers(
    universe: list[PlayerRef], multipliers: dict[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """A defense the analysts did not price directly moves opposite to the quarterback it faces: the script that hurts him (x0.8) lifts the
    defense (x1.10), one that lifts him (x1.3) lowers it (x0.90, floor of the range). Players the analysts named keep their own multipliers."""
    out = {}
    for dst_id, qb_id in opposing_qb_by_dst(universe).items():
        if dst_id in multipliers:
            continue
        qm = multipliers.get(qb_id, (1.0, 1.0))[0]
        m = min(max(1.0 + DST_SENSITIVITY * (1.0 - qm), DST_MULT_RANGE[0]), DST_MULT_RANGE[1])
        if m != 1.0:
            out[dst_id] = (m, m)
    return out


def game_of_ref(ref: str) -> str:
    return ref.partition(":")[0]


def derive_tiers(
    universe: list[PlayerRef], theses: dict[str, GameThesis], packets: dict[str, EvidencePacket],
    views: tuple[str, ...] | list[str], stack: tuple[str, ...] | list[str],
) -> dict[str, tuple[str, str]]:
    """`{canonical_id: (tier, reason)}` for one variation (its `views` and `stack`)."""
    view_by_game: dict[str, str] = {}
    for ref in views:
        g = game_of_ref(ref)
        if g not in theses:
            raise ValueError(f"view {ref!r} names a game with no thesis (games: {sorted(theses)})")
        view_by_game[g] = ref
    stack_ids = set(stack)
    by_id = {p.canonical_id: p for p in universe}
    stack_teams = {by_id[c].team for c in stack_ids if c in by_id}
    # each game priced under ITS OWN view (full multipliers); games with no view under the full mix
    under_view = conditional_multipliers(theses.values(), views)
    mix = conditional_multipliers([t for g, t in theses.items() if g not in view_by_game])
    evidence = {p.canonical_id: p for pk in packets.values() for p in pk.players}

    opposing_qb = opposing_qb_by_dst(universe)

    out: dict[str, tuple[str, str]] = {}
    for p in universe:
        cid = p.canonical_id
        if p.status in EXCLUDED_INJURY_STATUSES:
            out[cid] = ("exclude", f"unavailable ({p.status})")
            continue
        if cid in stack_ids:
            out[cid] = ("core", "on the agent's core stack")
            continue
        ref = view_by_game.get(p.game_id or "")
        benef = is_beneficiary(evidence.get(cid))
        if p.position == "DST":
            # Never a "fill" with a haircut (Chris, 2026-10-09): defenses are the cheapest slot, $200-500 more can matter a lot, they can go
            # negative, and whether to spend up on one is an aggregate trade-off the solver should weigh at the defense's own value. So a
            # defense is always eligible (priced on its merits; the view's effect on the opposing QB is in its value, see `dst_multipliers`).
            qb = opposing_qb.get(cid)
            qm = under_view.get(qb, (1.0, 1.0))[0] if (qb and ref) else 1.0
            out[cid] = ("eligible", f"priced on its own merits (opposing QB mean x{qm:.2f} under {ref or 'no view'})")
            continue
        if ref:
            m = under_view.get(cid, (1.0, 1.0))[0]
            if p.team in stack_teams and (m >= LIFT or (benef and m >= 1.0)):
                out[cid] = ("core", f"teammate on a stack team that view {ref} lifts (mean x{m:.2f})")
            elif m >= LIFT:
                out[cid] = ("eligible", f"view {ref} lifts him (mean x{m:.2f}): a piece of a game the agent expects to outperform")
            elif benef and m >= 1.0:
                out[cid] = ("eligible", f"inherits a vacated role; view {ref} does not hurt him (mean x{m:.2f})")
            elif m >= HURT:
                out[cid] = ("eligible", f"neutral under view {ref} (mean x{m:.2f})")
            else:
                out[cid] = ("reach", f"view {ref} hurts him (mean x{m:.2f}): avoid")
        else:
            m = mix.get(cid, (1.0, 1.0))[0]
            if benef or m >= OTHER_LIFT:
                out[cid] = ("eligible", "inherits a vacated role" if benef else f"lifted in his game's full mix (mean x{m:.2f})")
            else:
                out[cid] = ("reach", "a fill: no view of his game")
    return out
