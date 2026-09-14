"""Joins RotoGrinders "Situation Room" injury data (`ingestion/rotogrinders_injuries.py`) to
canonical `PlayerIdentity` records, so injury status is queryable by `canonical_id` rather than
RotoGrinders' own native id directly.

**`PlayerIdentity`'s existing shape already carries what this join needs -- no new field added.**
Each identity's `sources["rotogrinders"]` (`SourceMatch.native_id`) is already RotoGrinders' own
player id (`PLAYERID`/`RGID`), and that's confirmed to be the exact same id scheme the injury
export's own `PLAYERID` column uses (see `rotogrinders_injuries.py`'s module docstring) -- so this
module is a plain dict join keyed on that existing field, not a new matcher and not a dataclass
change to `identity.py` or `matcher.py`. Per this task's constraints, `matcher.py`'s core matching
logic and `identity.py`'s dataclass shapes are untouched by this module.
"""

from __future__ import annotations

from nfl_dfs.ingestion.rotogrinders_injuries import InjuryReportEntry
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity


def build_injury_lookup(
    identities: list[PlayerIdentity], injuries: list[InjuryReportEntry]
) -> dict[str, InjuryReportEntry]:
    """`canonical_id -> InjuryReportEntry`, for every identity whose resolved RotoGrinders native
    id matches a row in this week's injury export. A player absent from the injury report simply
    has no entry in the returned dict (no injury == no row) -- callers should treat a missing key
    as "not on this week's injury report," not as an error or an implicit "healthy" record.
    """
    by_rg_id = {entry.rotogrinders_player_id: entry for entry in injuries}
    lookup: dict[str, InjuryReportEntry] = {}
    for identity in identities:
        match = identity.sources.get("rotogrinders")
        if match is None or match.method == MatchMethod.UNRESOLVED or match.native_id is None:
            continue
        entry = by_rg_id.get(match.native_id)
        if entry is not None:
            lookup[identity.canonical_id] = entry
    return lookup


def team_injuries(
    team: str, identities: list[PlayerIdentity], injuries: list[InjuryReportEntry]
) -> list[tuple[PlayerIdentity, InjuryReportEntry]]:
    """Every `(identity, injury)` pair for players on `team` who appear in this week's injury
    report -- the direct input `GameEnvironmentScore`'s rollup (see
    `game_environment/score.py`'s `PlayerInjuryStatus`/`compute_injury_uncertainty_flag`) needs.
    Returns the identity alongside the injury row (not just the injury row) so a future rollup
    that wants to weight by position/salary/starter-status doesn't need a second join -- out of
    scope for this pass, but the identity is cheap to keep attached now.
    """
    lookup = build_injury_lookup(identities, injuries)
    return [
        (identity, lookup[identity.canonical_id])
        for identity in identities
        if identity.team == team and identity.canonical_id in lookup
    ]
