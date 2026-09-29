"""Builds one real `PlayerContext` per canonical_id in a slate snapshot's pool -- the same fields
`retrospective.compute_signal_verdicts` already reads and trusts for the slate-wide process grade
(ownership/game-environment/stack-context/ceiling/injury/etc.), surfaced per player for the first
time (2026-09-29 postmortem player-detail proposal, Tiers 1a/1b -- following a review of the sister
MLB project's own postmortem, at Chris's request). Nothing here is a new signal or a new formula --
every field is already computed by `composition/player_detail.py` and already serialized into every
snapshot's `player_pool`; this module's only job is the canonical_id-keyed lookup + box-score-line
join, same "one real join, every downstream consumer does an O(1) lookup" shape `actual_points.py`
already established.
"""

from __future__ import annotations

import pandas as pd

from nfl_dfs.tracking.postmortem.actual_points import box_score_lines_by_canonical_id
from nfl_dfs.tracking.postmortem.models import PlayerContext


def build_player_context_by_id(
    player_pool: list[dict], *, season: int, week: int, weekly: pd.DataFrame
) -> dict[str, PlayerContext]:
    """One `PlayerContext` per real canonical_id in `player_pool`. A row with no canonical_id
    (shouldn't happen for a real snapshot, but not asserted here) is simply skipped."""
    box_scores = box_score_lines_by_canonical_id(player_pool, season=season, week=week, weekly=weekly)

    contexts: dict[str, PlayerContext] = {}
    for row in player_pool:
        canonical_id = (row.get("identity") or {}).get("canonical_id")
        if not canonical_id:
            continue

        ownership = row.get("ownership") or {}
        game_environment = row.get("game_environment") or {}
        stack_context = row.get("stack_context") or {}
        injury = row.get("injury") or {}
        circumstance = row.get("circumstance_assessment") or {}

        contexts[canonical_id] = PlayerContext(
            is_chalk=bool(ownership.get("is_chalk")),
            is_leverage=bool(ownership.get("is_leverage")),
            game_environment_score=game_environment.get("composite_score"),
            is_primary_stack_candidate=bool(stack_context.get("is_primary_stack_candidate")),
            ceiling_multiplier=row.get("ceiling_multiplier"),
            red_zone_role_security_discount=row.get("red_zone_role_security_discount"),
            injury_status=injury.get("status"),
            implied_total=row.get("implied_total"),
            slate_window=row.get("slate_window"),
            circumstance_note=circumstance.get("pov"),
            box_score_line=box_scores.get(canonical_id),
        )
    return contexts
