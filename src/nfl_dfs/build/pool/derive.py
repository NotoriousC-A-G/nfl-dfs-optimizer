"""Tiers derived from a SCRIPT (Chris, 2026-10-09): a player's tier is not fixed for an agent, it depends on the version of the game the
lineup is built around. A back is core when the game is a lead-protecting script and reach when it is a shootout where his team trails.

A *script* is one branch of one game's thesis (`"GAME:branch_id"`). Given a script the code sets a first-pass tier for every player:

- in the scripted game: **core** when the script lifts him (his mean multiplier under that branch is at least `LIFT`) or he is an injury
  beneficiary the script does not hurt; **eligible** when the script leaves him roughly neutral; **reach** when it hurts him;
- in every other game: **eligible** when he is a beneficiary or his own game's probability-weighted mix lifts him (at least `OTHER_LIFT`),
  else **reach** (a fill, not a stand);
- anyone who cannot play: **exclude**, with the reason.

The expert's explicit tiers (group tiers and overrides, each with a reason) are ADJUSTMENTS applied on top of this baseline, so judgment still
overrides the arithmetic. Thresholds are disclosed drafts, not backtested.
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
BENEFICIARY_TARGET_GAIN = 0.03  # points of the team's targets (as a share)
BENEFICIARY_CARRY_GAIN = 0.05


def is_beneficiary(pe: PlayerEvidence | None) -> bool:
    if pe is None:
        return False
    t = (pe.target_share_expected or 0.0) - (pe.target_share_l4 or 0.0)
    c = (pe.carry_share_expected or 0.0) - (pe.carry_share_l4 or 0.0)
    return t >= BENEFICIARY_TARGET_GAIN or c >= BENEFICIARY_CARRY_GAIN


def split_ref(ref: str) -> tuple[str, str]:
    game_id, _, branch_id = ref.partition(":")
    return game_id, branch_id


def derive_tiers(
    universe: list[PlayerRef], theses: dict[str, GameThesis], packets: dict[str, EvidencePacket], ref: str,
) -> dict[str, tuple[str, str]]:
    """`{canonical_id: (tier, reason)}` for the script `ref`."""
    game_id, _ = split_ref(ref)
    if game_id not in theses:
        raise ValueError(f"script {ref!r} names a game with no thesis (games: {sorted(theses)})")
    in_script = conditional_multipliers([theses[game_id]], backs=[ref])
    elsewhere = conditional_multipliers([t for g, t in theses.items() if g != game_id])
    evidence = {p.canonical_id: p for pk in packets.values() for p in pk.players}
    out: dict[str, tuple[str, str]] = {}
    for p in universe:
        if p.status in EXCLUDED_INJURY_STATUSES:
            out[p.canonical_id] = ("exclude", f"unavailable ({p.status})")
            continue
        benef = is_beneficiary(evidence.get(p.canonical_id))
        if p.game_id == game_id:
            m = in_script.get(p.canonical_id, (1.0, 1.0))[0]
            if m >= LIFT:
                out[p.canonical_id] = ("core", f"script {ref} lifts him (mean x{m:.2f})")
            elif benef and m >= 1.0:
                out[p.canonical_id] = ("core", f"inherits a vacated role and script {ref} does not hurt him (mean x{m:.2f})")
            elif m >= HURT:
                out[p.canonical_id] = ("eligible", f"neutral under script {ref} (mean x{m:.2f})")
            else:
                out[p.canonical_id] = ("reach", f"script {ref} hurts him (mean x{m:.2f})")
        else:
            m = elsewhere.get(p.canonical_id, (1.0, 1.0))[0]
            if benef or m >= OTHER_LIFT:
                out[p.canonical_id] = ("eligible", "inherits a vacated role" if benef else f"lifted in his own game's mix (mean x{m:.2f})")
            else:
                out[p.canonical_id] = ("reach", "outside the scripted game")
    return out
