"""DraftKings Classic NFL slate ingestion (PRD Section 4/5 step 1; Phase 0 confirmed live).

DK needs no auth. Two calls: `getcontests` to find this week's live Classic draftGroupId (Phase 0
found some Classic `dg` values return 0 players -- e.g. an already-locked slate -- so a candidate
is verified against `draftables` before use, not assumed live from `gameType` alone), then
`draftables` for the actual player pool.

DK is the matcher's anchor vocabulary for team/position (ADR-0013 decision 3) -- confirmed live
here (see `parse_draftables`' docstring) that DK's own payload needs no aliasing into itself.

**Urgent correctness fix (Data Integration Engineer, 2026-09-13):** `fetch_classic_draft_group_id`
previously picked whichever live Classic-gameType draftGroupId had the most total draftables rows,
with zero awareness of which games/days it actually covered. A live check run this week (2026 wk1,
Sunday evening, after the real Sunday-afternoon main slate had already locked and dropped out of
`getcontests` entirely) found only two live Classic draftGroupIds: `153071`, labeled "(Primetime)"
in every one of its contest names, whose 188 draftables rows span **two different games on two
different calendar days** -- DAL @ NYG (Sun 8:20pm ET, i.e. Sunday Night Football) and DEN @ KC
(Mon 8:15pm ET, Monday Night Football) -- and `153109`, labeled "(Mon-Thu)", which returned 0
players (not yet released). The old "most players wins" heuristic would have silently picked
`153071` and called it the main slate: a real, coherent, enterable DK product (DK's own combined
SNF+MNF "Primetime" slate), but not remotely what PRD Section 2 means by "Main slate (Sunday 1pm
window)". Every lineup built off this pipeline during a window like this one would have been built
against Sunday-night-plus-Monday-night player pool, not Sunday's actual afternoon slate.

The fix (`select_classic_slate` / `DraftKingsSlate` below): classify each live Classic
draftGroupId's slate-type label from its contest names, exclude known non-main labels, require
day-coherence (every game in the slate starts on the same America/New_York calendar date) even for
candidates that pass the label check, and raise a loud, specific `SlateSelectionError` -- naming
every live candidate -- rather than silently substituting a non-main slate when no main slate is
currently live. `fetch_classic_draft_group_id` keeps its old signature/return type (`int`) for
backward compatibility with existing callers, but is now a thin wrapper around this policy.

**What's confirmed vs. assumed here, reported precisely (this role's mandate):** the contest-name
trailing-parenthetical label pattern (`"... (Primetime)"`, `"... (Mon-Thu)"`) is live-confirmed
this week; `"(Afternoon Only)"` is confirmed from Phase 0's historical fixture capture
(`tests/fixtures/draftkings_contests.json`, draftGroupId 153070) as a further, *different* partial
Sunday-window slate (late-afternoon games only, not the full main slate). What DK actually calls
the true combined Sunday main slate -- literally "Main", no suffix at all, or something else -- is
**not confirmed live by this project**, because it was not live at the moment this fix was written
(the real main slate had already locked). This is a genuine gap, not papered over: see
`_NON_MAIN_LABEL_MARKERS` and `select_classic_slate`'s docstring for how the selection logic is
designed to be robust to that gap (an exclusion-based, not inclusion-based, definition of "main"),
and the Product Owner/Architect flag in this round's report for whether automatic selection should
even be the default path.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from nfl_dfs.normalization.matcher import SourcePlayer
from nfl_dfs.normalization.position_aliases import CANONICAL_POSITIONS

CONTESTS_URL = "https://www.draftkings.com/lobby/getcontests?sport=NFL"
DRAFTABLES_URL = "https://api.draftkings.com/draftgroups/v1/draftgroups/{draft_group_id}/draftables"

_TIMEOUT = 20.0
_EASTERN = ZoneInfo("America/New_York")

# Non-main slate-type label markers, matched case-insensitively as a substring of the label parsed
# from a contest's trailing parenthetical (see `_extract_slate_label`). "primetime" and "mon-thu"
# are LIVE-CONFIRMED this week (2026 wk1, live `getcontests` pull, 2026-09-13 -- see module
# docstring). "afternoon only" is CONFIRMED from Phase 0's historical fixture capture
# (draftGroupId 153070) -- also a partial-day subset, not the full main slate. The remaining
# markers are **not live-confirmed by this project** -- added defensively because they describe
# the same category of alternate/partial-window DK slate product as the confirmed ones, using
# DK's general public naming conventions. Treat these as a documented best guess, not verified
# fact: re-check against real live data the first time any of them is actually seen live, and
# widen/narrow this list from that real observation rather than from this comment.
_CONFIRMED_NON_MAIN_MARKERS = ("primetime", "mon-thu", "afternoon only")
_UNCONFIRMED_NON_MAIN_MARKERS = (
    "thu-mon",
    "sun-mon",
    "early only",
    "turbo",
    "showcase",
    "single game",
    "thursday night",
    "sunday night",
    "monday night",
)
_NON_MAIN_LABEL_MARKERS = _CONFIRMED_NON_MAIN_MARKERS + _UNCONFIRMED_NON_MAIN_MARKERS

_TRAILING_PAREN_RE = re.compile(r"\(([^)]*)\)\s*$")


class SlateSelectionError(RuntimeError):
    """Raised when the live Classic draftGroupId set can't be safely resolved to one slate
    automatically -- no live candidate looks like the main slate, more than one plausibly does, or
    every candidate is empty. Deliberately not a silent fallback: this is exactly the failure mode
    (see module docstring) that produced lineups built on the wrong slate with no one noticing.
    """


def classic_draft_group_ids(contests_payload: dict) -> list[int]:
    """Pure parse of the `getcontests` payload: distinct draftGroupIds tagged `gameType ==
    "Classic"` on at least one contest. Several draft groups can share `gameType` while differing
    in which games/players they include, so this is a *candidate* list, not a single answer --
    callers still need to check each one's `draftables` for an actual player count (and, as of
    this fix, its actual game/day composition -- see `select_classic_slate`).
    """
    contests = contests_payload.get("Contests", [])
    return sorted({c["dg"] for c in contests if c.get("gameType") == "Classic"})


def _extract_slate_label(contest_name: str) -> str:
    """Parses a Classic contest name's trailing parenthetical as its DK-assigned slate-type label
    -- e.g. `"NFL $30K Wildcat [$10K to 1st] (Primetime)"` -> `"Primetime"`. Live-confirmed present
    on every live Classic contest checked this week. Returns `""` (not None) when there's no
    trailing parenthetical at all -- a plausible, but *not* live-confirmed, pattern for the true
    main slate (see module docstring): DK may reserve the parenthetical suffix for
    alternate/partial slates and leave the default main slate unlabeled. `""` is deliberately
    treated as a non-excluded (main-candidate) label by `_looks_non_main` on that basis.
    """
    match = _TRAILING_PAREN_RE.search(contest_name)
    return match.group(1).strip() if match else ""


def _looks_non_main(label: str) -> bool:
    lower = label.lower()
    return any(marker in lower for marker in _NON_MAIN_LABEL_MARKERS)


def _contest_labels_by_draft_group(contests_payload: dict) -> dict[int, str]:
    """Maps each live Classic draftGroupId to its slate-type label. If a draftGroupId's own
    contests disagree on label (not expected, not verified impossible), the alphabetically-first
    label is used and the disagreement is surfaced via `warnings.warn` rather than silently picked.
    """
    labels_by_dg: dict[int, set[str]] = {}
    for c in contests_payload.get("Contests", []):
        if c.get("gameType") != "Classic":
            continue
        labels_by_dg.setdefault(c["dg"], set()).add(_extract_slate_label(c.get("n") or ""))

    result: dict[int, str] = {}
    for draft_group_id, labels in labels_by_dg.items():
        if len(labels) > 1:
            warnings.warn(
                f"draftGroupId {draft_group_id} has contests with disagreeing slate-type labels "
                f"{sorted(labels)} -- using the alphabetically-first; this is unexpected and worth "
                "checking against live data.",
                stacklevel=2,
            )
        result[draft_group_id] = sorted(labels)[0]
    return result


@dataclass(frozen=True)
class SlateGame:
    """One game in a slate, parsed from DK's own `competition` field on a `draftables` payload."""

    away_team: str
    home_team: str
    start_time_utc: str  # DK's raw ISO8601 string, unmodified


def _slate_games_from_draftables(payload: dict) -> tuple[SlateGame, ...]:
    """Pure parse of a `draftables` payload's `competition` field into one `SlateGame` per distinct
    `competitionId` -- same field and shape `weather.py`'s `GameSchedule`/`parse_dk_game_schedule`
    already use, duplicated here (not imported) to keep this module's slate-selection fix
    self-contained, per this round's "don't touch other ingestion modules" scope.
    """
    seen: dict[int, SlateGame] = {}
    for d in payload.get("draftables", []):
        comp = d.get("competition") or {}
        comp_id, name, start = comp.get("competitionId"), comp.get("name"), comp.get("startTime")
        if comp_id is None or not name or not start or comp_id in seen:
            continue
        if " @ " not in name:
            warnings.warn(f"unexpected DK competition name format: {name!r} -- skipping", stacklevel=2)
            continue
        away, home = name.split(" @ ", 1)
        seen[comp_id] = SlateGame(away_team=away.strip(), home_team=home.strip(), start_time_utc=start)
    return tuple(seen.values())


def _eastern_date(start_time_utc: str) -> str:
    return datetime.fromisoformat(start_time_utc).astimezone(_EASTERN).date().isoformat()


@dataclass(frozen=True)
class DraftKingsSlate:
    """The full identity of a candidate (or selected) Classic slate -- not just its draftGroupId.
    This is the new output shape this round's fix adds: downstream consumers (the optimizer, a
    future output stage, and Chris himself) can see and verify exactly which games/teams a lineup
    is about to be built against, before it's built, rather than trusting an opaque integer.

    **Interface flag, not resolved here:** `optimizer/lineup.py` and `projection/blend.py` don't
    consume this yet -- they're out of this round's scope (see module docstring) and still only
    see `fetch_draftkings_players`'s existing `list[SourcePlayer]` return, which is unchanged.
    Wiring `DraftKingsSlate`'s game list through to the optimizer/output layer so it's actually
    surfaced to Chris before lineups get built is a real follow-up, not done by this fix.
    """

    draft_group_id: int
    slate_label: str
    player_count: int
    games: tuple[SlateGame, ...]

    @property
    def teams(self) -> frozenset[str]:
        return frozenset(team for g in self.games for team in (g.home_team, g.away_team))

    @property
    def eastern_dates(self) -> frozenset[str]:
        """Distinct calendar dates (America/New_York) this slate's games kick off on. More than
        one means the slate spans multiple days -- e.g. a combined Sunday-night + Monday-night
        "Primetime" slate -- which is never what PRD Section 2 means by the Sunday main slate, even
        when the slate is otherwise a real, coherent, enterable DK product.
        """
        return frozenset(_eastern_date(g.start_time_utc) for g in self.games)


def _describe_slate(slate: DraftKingsSlate) -> str:
    label = slate.slate_label or "(no label)"
    if not slate.games:
        return f"dg={slate.draft_group_id} '{label}': 0 players (not currently live)"
    games_desc = ", ".join(f"{g.away_team}@{g.home_team} {g.start_time_utc}" for g in slate.games)
    return f"dg={slate.draft_group_id} '{label}': {slate.player_count} players -- {games_desc}"


def _candidate_slates(
    contests_payload: dict, http, *, timeout: float = _TIMEOUT
) -> list[DraftKingsSlate]:
    """Every live Classic draftGroupId, each verified against its own `draftables` payload (some
    return 0 players -- e.g. an already-locked slate `getcontests` hasn't dropped yet, or one not
    released yet) and annotated with its parsed slate-type label and actual game list.
    """
    labels_by_dg = _contest_labels_by_draft_group(contests_payload)
    slates = []
    for draft_group_id in sorted(labels_by_dg):
        payload = http.get(DRAFTABLES_URL.format(draft_group_id=draft_group_id), timeout=timeout).json()
        rows = payload.get("draftables", [])
        slates.append(
            DraftKingsSlate(
                draft_group_id=draft_group_id,
                slate_label=labels_by_dg[draft_group_id],
                player_count=len(rows),
                games=_slate_games_from_draftables(payload),
            )
        )
    return slates


def parse_draftables(payload: dict) -> list[SourcePlayer]:
    """Pure parse of one `draftables` response into `SourcePlayer` rows, one per unique
    `playerDkId`.

    Live-confirmed (draftGroupId 153070, 2026 wk1): `position` values are exactly
    `{DST, QB, RB, TE, WR}` and `teamAbbreviation` values (`ARI, GB, LAC, LV, MIA, MIN, PHI, WAS`,
    etc.) already match this project's canonical vocabulary -- DK introduces no team/position
    label of its own that needs aliasing, confirming (not just assuming) team_aliases.py's and
    position_aliases.py's empty `draftkings` tables are correct.

    **Real finding from the live integration check, not assumed going in:** `draftables` is NOT
    one row per player -- a FLEX-eligible player gets one row per roster-slot he's eligible for
    (confirmed live: Justin Jefferson appeared twice, `playerDkId 485454` both times, differing
    only in `draftableId`/`rosterSlotId` -- 68 vs 70, same salary/position). A live 385-row pull
    was only 210 unique `playerDkId`s. Deduped here by `playerDkId` (first-seen wins) because
    `SourcePlayer`/`PlayerIdentity` are one-row-per-player contracts -- feeding the matcher
    un-deduped rows would silently produce duplicate `PlayerIdentity` records per player.
    """
    players: dict[str, SourcePlayer] = {}
    unexpected_positions = set()
    for d in payload.get("draftables", []):
        position = d["position"]
        if position not in CANONICAL_POSITIONS:
            unexpected_positions.add(position)
        players.setdefault(
            str(d["playerDkId"]),
            SourcePlayer(
                native_id=str(d["playerDkId"]),
                name=d["displayName"],
                team=d.get("teamAbbreviation"),
                position=position,
            ),
        )
    if unexpected_positions:
        # Not raised: DK is the anchor vocabulary, so an unexpected label here would be a real
        # upstream change worth surfacing to the Architect, not a reason to drop those players.
        warnings.warn(
            f"DraftKings draftables returned position label(s) outside the canonical "
            f"{sorted(CANONICAL_POSITIONS)} vocabulary: {sorted(unexpected_positions)} -- DK was "
            f"assumed to need no position aliasing; re-verify against team_aliases.py's note.",
            stacklevel=2,
        )
    return list(players.values())


def select_classic_slate(
    *, session: requests.Session | None = None, require_label: str | None = None
) -> DraftKingsSlate:
    """Live call: resolve the live Classic draftGroupId set to the one slate PRD Section 2 means
    by "main slate" -- not just whichever candidate has the most players (see module docstring for
    why that was wrong).

    Selection policy:
      1. Every live Classic draftGroupId is fetched and verified against its own `draftables`;
         candidates with 0 players are dropped (an already-locked or not-yet-released slate).
      2. Each remaining candidate's slate-type label is parsed from its contest names' trailing
         parenthetical. A candidate whose label matches a known non-main marker (see
         `_NON_MAIN_LABEL_MARKERS`) is excluded from "main" consideration.
      3. A remaining candidate must also be *day-coherent*: every one of its games must start on
         the same America/New_York calendar date. This catches an incoherent (or mislabeled/
         unlabeled) multi-day candidate independent of step 2's label check.
      4. Exactly one survivor -> that's the main slate, returned.
      5. Zero survivors -> `SlateSelectionError`, naming every live candidate (so "no main slate
         live right now" is loud, never a silent wrong pick).
      6. More than one survivor -> also `SlateSelectionError` (genuinely ambiguous -- this
         function does not guess between multiple main-shaped candidates).

    `require_label`, given, overrides steps 2-6 entirely: candidates are filtered to those whose
    label contains it (case-insensitive substring), and the one with the most players among those
    is returned. This is the explicit "I want a specific non-main slate on purpose" escape hatch --
    see module docstring's Product Owner flag on whether automatic-only selection is safe as the
    only path for a tool that produces real contest entries.
    """
    http = session or requests
    contests_payload = http.get(CONTESTS_URL, timeout=_TIMEOUT).json()
    all_candidates = _candidate_slates(contests_payload, http)
    live_candidates = [s for s in all_candidates if s.player_count > 0]

    if not live_candidates:
        raise SlateSelectionError(
            "no live Classic draftGroupId returned any players -- likely every slate is locked "
            "for this week, or none has been released yet."
        )

    if require_label is not None:
        matches = [s for s in live_candidates if require_label.lower() in s.slate_label.lower()]
        if not matches:
            described = "; ".join(_describe_slate(s) for s in live_candidates)
            raise SlateSelectionError(
                f"no live Classic slate has a label containing {require_label!r}. Live candidate(s): "
                f"{described}"
            )
        return max(matches, key=lambda s: s.player_count)

    main_candidates = [s for s in live_candidates if not _looks_non_main(s.slate_label)]
    coherent_main = [s for s in main_candidates if len(s.eastern_dates) <= 1]
    incoherent_main = [s for s in main_candidates if len(s.eastern_dates) > 1]

    if len(coherent_main) == 1:
        return coherent_main[0]

    described_all = "; ".join(_describe_slate(s) for s in live_candidates)
    if not coherent_main:
        extra = ""
        if incoherent_main:
            incoherent_desc = "; ".join(
                f"{_describe_slate(s)} (spans {len(s.eastern_dates)} calendar days: "
                f"{sorted(s.eastern_dates)})"
                for s in incoherent_main
            )
            extra = (
                f" Note: {incoherent_desc} looked main-like by label but spans multiple calendar "
                "days -- excluded rather than guessed at."
            )
        raise SlateSelectionError(
            "no live Classic slate looks like the Sunday main slate (PRD Section 2). Live Classic "
            f"slate(s) instead: {described_all}.{extra} Pass require_label= to target one of these "
            "deliberately, or draft_group_id= to fetch_draftkings_players directly."
        )

    ambiguous_desc = "; ".join(_describe_slate(s) for s in coherent_main)
    raise SlateSelectionError(
        "more than one live Classic slate looks like the main slate -- ambiguous, not guessing. "
        f"Candidates: {ambiguous_desc}. Pass require_label= to disambiguate."
    )


def fetch_classic_draft_group_id(
    *, session: requests.Session | None = None, require_label: str | None = None
) -> int:
    """Backward-compatible wrapper around `select_classic_slate` -- same signature/return type
    (`int`) existing callers (`fetch_draftkings_players`, and the live-check scripts) already use.
    The selection *policy* is now slate-aware (see module docstring and `select_classic_slate`),
    not the old "most players wins" heuristic. Callers that want the full slate identity (games,
    label, teams) to verify before building lineups should call `select_classic_slate` directly.
    """
    return select_classic_slate(session=session, require_label=require_label).draft_group_id


def fetch_slate_by_draft_group_id(
    draft_group_id: int, *, session: requests.Session | None = None
) -> tuple[dict, DraftKingsSlate]:
    """Fetch a SPECIFIC, already-known Classic slate directly by its `draftGroupId`, bypassing
    `select_classic_slate`'s auto-detection entirely -- the explicit-selection escape hatch for
    when a caller already knows exactly which slate to build for, rather than trusting
    ambiguity-prone auto-detection.

    **Real motivating case (2026-09-19, live):** DK started serving two overlapping, BOTH
    unlabeled, BOTH day-coherent live Classic slates for the same Sunday at once -- the real
    13-game main slate, and a smaller 8-game "early games only" slate missing the 4:05/4:25pm ET
    games. `select_classic_slate` correctly refused to guess between them (`SlateSelectionError`,
    "ambiguous, not guessing"), but `scripts/live_integration_check_output.py`'s own
    `fetch_dk_raw_for_live_slate` blindly caught that error and substituted a THIRD, unrelated
    "Primetime" slate -- silently building an entire live dashboard run against the wrong 2-game
    slate with no one noticing until the output was inspected by hand. That fallback was built for
    a genuinely different failure mode (the main slate already locked, zero live candidates at
    all -- see that function's own docstring) and was never meant to paper over ambiguity between
    multiple still-live candidates.

    Returns the SAME `(raw draftables payload, DraftKingsSlate)` shape `select_classic_slate`'s
    own callers already consume downstream, so this is a drop-in alternative entry point for a
    caller (a script's own `DRAFT_GROUP_ID` constant, set by hand from a candidate list an
    ambiguity error already prints) to use once it knows the exact id, not a parallel code path
    with its own shape.
    """
    http = session or requests
    contests_payload = http.get(CONTESTS_URL, timeout=_TIMEOUT).json()
    label = _contest_labels_by_draft_group(contests_payload).get(draft_group_id, "")
    payload = http.get(DRAFTABLES_URL.format(draft_group_id=draft_group_id), timeout=_TIMEOUT).json()
    rows = payload.get("draftables", [])
    slate = DraftKingsSlate(
        draft_group_id=draft_group_id,
        slate_label=label,
        player_count=len(rows),
        games=_slate_games_from_draftables(payload),
    )
    return payload, slate


def fetch_draftkings_players(
    draft_group_id: int | None = None,
    *,
    session: requests.Session | None = None,
    require_label: str | None = None,
) -> list[SourcePlayer]:
    """Live pull. Pass `draft_group_id` explicitly to skip the live-slate lookup entirely (e.g. a
    script that already knows the week's slate id, or the id from a `select_classic_slate` result
    Chris has already verified). Otherwise the live main slate is resolved via
    `fetch_classic_draft_group_id` -- pass `require_label` to deliberately target a specific
    non-main slate (e.g. `"Primetime"`) instead of the default main-slate-only behavior.
    """
    http = session or requests
    dg = (
        draft_group_id
        if draft_group_id is not None
        else fetch_classic_draft_group_id(session=http, require_label=require_label)
    )
    payload = http.get(DRAFTABLES_URL.format(draft_group_id=dg), timeout=_TIMEOUT).json()
    return parse_draftables(payload)
