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
from dataclasses import dataclass
from datetime import datetime

from nfl_dfs.ingestion.official_injury_report import OfficialInjuryReportEntry
from nfl_dfs.storage.injury_clearance_store import QuestionableOverride
from nfl_dfs.tracking.name_matching import normalize_player_name

# Status a Questionable/Doubtful player is rewritten to once cleared (by the official practice report
# or a Chris override). Deliberately NOT in `optimizer.lineup.EXCLUDED_INJURY_STATUSES`, so a cleared
# player is rosterable while everyone still on plain "Q" is dropped.
CLEARED_QUESTIONABLE_STATUS = "Q_CLEARED"
# Status a Chris `bar` override writes -- excluded for any player, whatever DK/RotoGrinders say.
BARRED_STATUS = "BARRED"
# Time-aware Q handling (Chris, 2026-10-09): a Questionable tag early in the week is weak evidence; the same tag after a week of missed practice is
# strong. A Q player the evidence cannot yet settle is UNRESOLVED: available and flagged (NOT in EXCLUDED_INJURY_STATUSES), and his vacated work is
# shown as contingent, not assumed. By Friday's report everything resolves under the original rule (Full clears; otherwise out).
UNRESOLVED_STATUS = "Q_UNRESOLVED"

_FULL_PRACTICE = "Full Participation in Practice"
_LIMITED_PRACTICE = "Limited Participation in Practice"
_DID_NOT_PRACTICE = "Did Not Participate In Practice"


STAGES = ("early", "midweek", "final", "gameday")
_OUT_GAME_STATUSES = ("Out", "Doubtful")


def report_stage(when_utc: "datetime") -> str:
    """Where in the NFL injury-report week `when_utc` falls, in US/Eastern: Mon-Wed = early (first practice reports), Thu = midweek, Fri = final (the
    Friday report and its game designations), Sat/Sun = gameday."""
    from zoneinfo import ZoneInfo

    wd = when_utc.astimezone(ZoneInfo("America/New_York")).weekday()  # Mon=0
    return "early" if wd <= 2 else "midweek" if wd == 3 else "final" if wd == 4 else "gameday"


def practice_history(snapshots: list) -> dict[str, list[tuple[str, str | None, str | None]]]:
    """`{gsis_id: [(date, practice_status, game_status), ...]}` oldest first, one entry per US/Eastern calendar day (the LAST capture of the day)
    from archived official-report captures (`official_injury_snapshot_store`). The feeds keep only the latest day, so the trajectory exists only
    because we capture it."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    by_day: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    for snap in sorted(snapshots, key=lambda s: s.fetched_at):
        day = datetime.fromisoformat(snap.fetched_at).astimezone(ZoneInfo("America/New_York")).date().isoformat()
        by_day.setdefault(day, {}).update({e.gsis_id: (e.practice_status, e.report_status) for e in snap.entries})
    out: dict[str, list[tuple[str, str | None, str | None]]] = {}
    for day in sorted(by_day):
        for gsis, (practice, game) in by_day[day].items():
            out.setdefault(gsis, []).append((day, practice, game))
    return out


_SHORT = {_FULL_PRACTICE: "Full", _LIMITED_PRACTICE: "Limited", _DID_NOT_PRACTICE: "DNP", None: "no report"}


def time_aware_decision(history: list[tuple[str, str | None, str | None]], stage: str, stamp: str = "") -> tuple[str, str, str]:
    """`(decision, basis, source)` for a Q player: "cleared" | "excluded" | "unresolved" (see module notes and ADR-0045 amendment).

    - an official game status of Out/Doubtful -> excluded at any stage;
    - Full on the latest report -> cleared;
    - early: Limited, a first DNP or no report yet -> unresolved;
    - midweek: DNP on two different report days -> excluded; otherwise unresolved;
    - final/gameday: the original rule -- anything but Full is out (Limited needs a positive report, i.e. an override)."""
    traj = " -> ".join(f"{d[5:]} {_SHORT.get(p, p)}" for d, p, _ in history) or "no official report yet"
    latest_day, latest, game = history[-1] if history else (None, None, None)
    if game in _OUT_GAME_STATUSES:
        return "excluded", f"official game status {game} ({traj}){stamp}", "official_practice"
    if latest == _FULL_PRACTICE:
        return "cleared", f"official practice: Full ({traj}){stamp}", "official_practice"
    if stage in ("final", "gameday"):
        if latest == _LIMITED_PRACTICE:
            return "excluded", f"official practice: Limited -- needs a positive report to clear; override to clear ({traj}){stamp}", "official_practice"
        if latest == _DID_NOT_PRACTICE:
            return "excluded", f"official practice: Did Not Participate ({traj}){stamp}", "official_practice"
        return "excluded", f"no official practice evidence ({traj}){stamp}", "default"
    dnp_days = sum(1 for _, p, _ in history if p == _DID_NOT_PRACTICE)
    if stage == "midweek" and latest == _DID_NOT_PRACTICE and dnp_days >= 2:
        return "excluded", f"official practice: Did Not Participate on {dnp_days} report days ({traj}){stamp}", "official_practice"
    return "unresolved", f"too early to call at the {stage} stage: {traj}{stamp}", "official_practice" if history else "default"


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


@dataclass(frozen=True)
class AvailabilityDecision:
    """One player's Q/override decision with the evidence behind it -- printed every run and saved
    with the slate so Chris can see (and override) each call and the post-mortem can grade them."""

    name: str
    team: str
    decision: str  # "cleared" | "excluded" | "barred" | "unresolved" (time-aware Q handling)
    basis: str
    source: str  # "official_practice" | "override" | "default"


def resolve_questionable_players(
    dk_injury_status: dict[str, str],
    identities: list[PlayerIdentity],
    official_entries: list[OfficialInjuryReportEntry],
    overrides: list[QuestionableOverride],
    *,
    week: int,
    as_of: str = "",
    history: dict | None = None,
    stage: str | None = None,
) -> tuple[dict[str, str], list[AvailabilityDecision], list[QuestionableOverride]]:
    """Applies the Questionable rule (Chris, 2026-10-07) and his overrides.

    **Default rule, per player whose merged status is exactly "Q":** the latest official practice
    status for `week` decides -- Full participation clears him (`Q_CLEARED`); Limited, Did Not
    Participate, or no official row at all leaves him out (a limited practice alone is not enough:
    clearing him needs a positive report, which is what an override is for). A game-time decision
    is therefore an avoid unless Chris says otherwise.

    **Overrides win in both directions:** `bar` excludes any player (status `BARRED`); `clear`
    rewrites a Q or D player to `Q_CLEARED`. Neither can touch OUT/IR (guaranteed zero) -- a `clear`
    on those is returned unmatched.

    Returns `(new status dict, decisions for every Q player and every override, overrides that
    matched no player or were refused)` so nothing is silently dropped. `official_entries` are
    matched to identities by `canonical_id == gsis_id`; `as_of` labels the evidence timestamp.
    """
    merged = dict(dk_injury_status)
    decisions: list[AvailabilityDecision] = []
    unmatched: list[QuestionableOverride] = []

    by_key = {(normalize_player_name(i.display_name), i.team.upper()): i for i in identities}
    official_by_gsis = {e.gsis_id: e for e in official_entries if e.week == week}

    def dk_id(identity: PlayerIdentity) -> str | None:
        dk = identity.sources.get("draftkings")
        return str(dk.native_id) if dk is not None and dk.native_id is not None else None

    overridden: set[str] = set()
    for o in overrides:
        identity = by_key.get((normalize_player_name(o.name), o.team.upper()))
        key = dk_id(identity) if identity else None
        if key is None:
            unmatched.append(o)
            continue
        current = merged.get(key)
        note = f" -- {o.note}" if o.note else ""
        if o.decision == "bar":
            merged[key] = BARRED_STATUS
            decisions.append(AvailabilityDecision(identity.display_name, identity.team, "barred", f"override: bar{note}", "override"))
        elif current in ("Q", "D"):
            merged[key] = CLEARED_QUESTIONABLE_STATUS
            decisions.append(AvailabilityDecision(identity.display_name, identity.team, "cleared", f"override: clear (was {current}){note}", "override"))
        else:
            unmatched.append(o)  # clear on OUT/IR, or on a player who isn't Q/D, is refused/irrelevant
            continue
        overridden.add(key)

    for identity in identities:
        key = dk_id(identity)
        if key is None or key in overridden or merged.get(key) != "Q":
            continue
        entry = official_by_gsis.get(identity.canonical_id)
        practice = entry.practice_status if entry else None
        stamp = f" (as of {as_of})" if as_of else ""
        if stage is not None:  # time-aware path; without `stage` the original rule below applies unchanged
            decision, basis, source = time_aware_decision((history or {}).get(identity.canonical_id, []), stage, stamp)
            if decision == "cleared":
                merged[key] = CLEARED_QUESTIONABLE_STATUS
            elif decision == "unresolved":
                merged[key] = UNRESOLVED_STATUS
            decisions.append(AvailabilityDecision(identity.display_name, identity.team, decision, basis, source))
            continue
        if practice == _FULL_PRACTICE:
            merged[key] = CLEARED_QUESTIONABLE_STATUS
            decisions.append(AvailabilityDecision(identity.display_name, identity.team, "cleared", f"official practice: Full{stamp}", "official_practice"))
        elif practice == _LIMITED_PRACTICE:
            decisions.append(AvailabilityDecision(identity.display_name, identity.team, "excluded", f"official practice: Limited -- needs a positive report to clear; override to clear{stamp}", "official_practice"))
        elif practice == _DID_NOT_PRACTICE:
            decisions.append(AvailabilityDecision(identity.display_name, identity.team, "excluded", f"official practice: Did Not Participate{stamp}", "official_practice"))
        else:
            decisions.append(AvailabilityDecision(identity.display_name, identity.team, "excluded", f"no official practice evidence for week {week}{stamp}", "default"))
    return merged, decisions, unmatched
