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
from nfl_dfs.storage.injury_clearance_store import QuestionableClearance
from nfl_dfs.tracking.name_matching import normalize_player_name

# Status a Questionable player is rewritten to once a Friday-practice clearance is on file
# (`storage/injury_clearance_store.py`). Deliberately NOT in `optimizer.lineup.EXCLUDED_INJURY_STATUSES`,
# so a cleared player is rosterable while everyone still on plain "Q" is dropped.
CLEARED_QUESTIONABLE_STATUS = "Q_CLEARED"


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


# RotoGrinders' Situation Room status codes that mean "not expected to play" -> the DK vocabulary
# `optimizer.lineup.EXCLUDED_INJURY_STATUSES` is written in. Q is included (Chris, 2026-10-07):
# Questionable is treated as not playing unless cleared by a Friday-practice report.
_RG_TO_DK_EXCLUDED_STATUS = {"D": "D", "O": "OUT", "Q": "Q"}

# Higher = more severe. The overlay only ever moves a status UP this ladder, so a vendor saying
# Doubtful/Out beats DK's Questionable (week 4's Breece Hall) and a clearance, which only touches
# plain "Q", can never resurrect a player either vendor has worse than Questionable.
_STATUS_SEVERITY = {"Q": 1, "D": 2, "OUT": 3, "IR": 3}


def overlay_injury_report_exclusions(
    dk_injury_status: dict[str, str],
    identities: list[PlayerIdentity],
    injuries: list[InjuryReportEntry],
    excluded_statuses: frozenset[str],
) -> dict[str, str]:
    """Returns a copy of `dk_injury_status` (`DK native id -> status`) where any player the
    RotoGrinders injury report marks Doubtful/Out is given the matching DK-vocabulary status,
    unless DK already carries an excluded status for them. Exists because the two vendors disagree
    in practice (week 4, 2026: Breece Hall was `Q` on DK, `D` on RotoGrinders) and the lineup
    solver only reads this one dict.
    """
    merged = dict(dk_injury_status)
    lookup = build_injury_lookup(identities, injuries)
    for identity in identities:
        entry = lookup.get(identity.canonical_id)
        dk_match = identity.sources.get("draftkings")
        if entry is None or dk_match is None or dk_match.native_id is None:
            continue
        mapped = _RG_TO_DK_EXCLUDED_STATUS.get(entry.status)
        if mapped is None or mapped not in excluded_statuses:
            continue
        current = merged.get(str(dk_match.native_id))
        if current in excluded_statuses and _STATUS_SEVERITY.get(current, 0) >= _STATUS_SEVERITY.get(mapped, 0):
            continue
        merged[str(dk_match.native_id)] = mapped
    return merged


def apply_questionable_clearances(
    dk_injury_status: dict[str, str],
    identities: list[PlayerIdentity],
    clearances: list[QuestionableClearance],
) -> tuple[dict[str, str], list[QuestionableClearance]]:
    """Returns `(status dict with cleared Q players rewritten to CLEARED_QUESTIONABLE_STATUS,
    clearances that matched no Questionable player)`. Only a player whose current status is exactly
    "Q" is rewritten -- a clearance can never override D/OUT/IR, so a stale or mistaken row can't
    put an unavailable player back in the pool. The unmatched list is returned (not dropped) so the
    caller can surface typos and clearances for players who aren't Q (e.g. already healthy)."""
    merged = dict(dk_injury_status)
    by_key = {(normalize_player_name(i.display_name), i.team.upper()): i for i in identities}
    unmatched: list[QuestionableClearance] = []
    for c in clearances:
        identity = by_key.get((normalize_player_name(c.name), c.team.upper()))
        dk = identity.sources.get("draftkings") if identity else None
        if dk is None or dk.native_id is None or merged.get(str(dk.native_id)) != "Q":
            unmatched.append(c)
            continue
        merged[str(dk.native_id)] = CLEARED_QUESTIONABLE_STATUS
    return merged, unmatched
