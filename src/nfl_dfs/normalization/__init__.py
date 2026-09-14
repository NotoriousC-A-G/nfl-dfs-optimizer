"""Stage 2: reconcile player IDs across sources, resolve name collisions, flag missing players (PRD Section 5).

Output shape is `PlayerIdentity` (see `identity.py`); design and live crosswalk findings are in
ADR-0013 (`docs/adr/0013-player-id-reconciliation.md`). Matching logic (`matcher.py`, plus the
crosswalk fetch/cache, name normalization, and team/position alias tables) implements that design.
"""

from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch, VENDOR_SOURCES
from nfl_dfs.normalization.matcher import SourcePlayer, reconcile_week, resolve_player_identity

__all__ = [
    "MatchMethod",
    "PlayerIdentity",
    "SourceMatch",
    "VENDOR_SOURCES",
    "SourcePlayer",
    "reconcile_week",
    "resolve_player_identity",
]
