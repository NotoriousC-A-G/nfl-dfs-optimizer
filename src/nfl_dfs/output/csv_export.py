"""Stage 9: DraftKings bulk-upload CSV export (PRD Section 5 step 9; Section 8: "CSV export in
DraftKings' bulk-upload format").

## What was actually verified, and how (confidence level, stated plainly per the task brief)

DraftKings' own bulk-upload help article (`support.draftkings.com`) is a JavaScript-rendered
single-page app -- both `WebFetch` and the Browser pane's `navigate` tool were tried against it
this round and neither could retrieve real content (`WebFetch` got only the loading shell;
`navigate` was blocked outright by this environment's browsing safety policy for that host). So
this module's format is **not confirmed against a DK-authored primary source directly** -- it is
confirmed by triangulating multiple independent secondary sources plus one piece of *real, raw
DK-exported data*:

1. A real `DKSalaries.csv` slate export, fetched verbatim from a public GitHub repo
   (`longenbach/Fantasy-Golf-DraftKings`, `DKSalaries_PGA.csv`), starting:
   `Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame`
   `G,Rory McIlroy (17527360),Rory McIlroy,17527360,G,11500,PGA Championship,Golf,73.35`
   This is real DK-generated data, not a description of it -- it directly confirms DK's player-
   identification convention is exactly `"{Name} ({native DK player ID})"` (e.g.
   `"Rory McIlroy (17527360)"`), one string per player, no other separator or encoding.
2. The `dfs-with-r/coach` R package's `read_dk()` source (a maintained, real-world DK-CSV parser)
   independently expects the identical column set for that same file shape: `Position`,
   `Name + ID`, `Name`, `ID`, `Roster Position`, `Salary`, `Game Info`, `TeamAbbrev`,
   `AvgPointsPerGame` -- corroborating (1) from a second, independent, code-level source rather
   than a marketing description.
3. Several independent DFS-tooling help sites (Stokastic, Run The Sims, RotoQL, FantasyLabs,
   DraftDime -- all cited in this round's report) describe the *upload* file (as opposed to the
   *salary/pool* file above) consistently: DK's own "Edit Entries" page hands you a per-contest
   template whose leading columns are `Entry ID, Contest Name, Contest ID, Entry Fee`, followed
   by one column per roster slot; you don't type players in by hand, you paste your own lineup
   rows over the placeholder players already in that template, leaving the leading contest-
   identity columns untouched; and the roster-slot columns for DK Classic NFL are `QB, RB, RB,
   WR, WR, WR, TE, FLEX, DST` -- the same 9-slot order this project's own
   `optimizer/lineup.py` already independently derived and verified against DK's real roster
   rules (PRD Section 3). No description found gave an exact byte-for-byte template dump of this
   specific upload file the way (1) above gave for the salary file, so the roster-slot column
   order/header names are corroborated by convergent secondary description, not by a second raw
   sample -- **stated explicitly as the one piece of this module still one step short of (1)'s
   confidence level.**

**Net confidence: high for the player-identification format (`"Name (ID)"`, directly confirmed
against real exported DK data) and for the 9-slot roster column order (matches this project's own
independently-verified DK roster rules); moderate-high, not fully primary-sourced, for the exact
upload-template header spelling (`Entry ID` / `Contest Name` / `Contest ID` / `Entry Fee`).** If
this ever needs to move to "fully confirmed," the fix is mechanical: have Chris download one real
`DKEntries.csv` template from his own DK account's Edit Entries page for a registered contest and
diff it against this module's assumptions -- not guessable from outside an authenticated session.

## The real, structural reason this module cannot produce a directly-uploadable file end to end

DK's bulk-upload file is **not** a fresh lineup submission format -- every source checked this
round agrees the leading `Entry ID`/`Contest Name`/`Contest ID`/`Entry Fee` columns identify a
contest entry *Chris has already registered for on DK's own site*, and the upload only overwrites
players on entries that already exist; it cannot create a new entry or target a contest ID this
pipeline was never told about. Those four values live entirely inside Chris's authenticated DK
account state -- `ingestion/draftkings.py`'s `getcontests`/`draftables` calls (PRD Section 4: "DK
public API") are unauthenticated and only ever return slate/salary data, never Chris's own
registered entries, and no ingestion module in this pipeline pulls that authenticated data. This
is not a gap this module can close by trying harder; it is a real scope boundary, and it is
exactly the same boundary every third-party DFS tool operates under too (Stokastic's Sims,
RotoQL, FantasyLabs, etc. all export *player rows only* for this same reason -- see this round's
report for the citations) -- Chris pastes those rows over the placeholder players in his own
downloaded, contest-specific DK template, the same workflow he'd use with any of those tools.

`export_lineups_to_dk_csv` below therefore produces exactly that: the 9 DK roster-slot columns,
correctly headered and ordered, one row per generated lineup, each cell in DK's confirmed
`"Name (ID)"` format -- the "player rows" Chris pastes into his own downloaded template. A caller
who wants a closer visual match to DK's full template shape can pass
`include_contest_columns=True` to prepend 4 empty `Entry ID`/`Contest Name`/`Contest ID`/
`Entry Fee` columns for Chris to fill in by hand or paste his own already-downloaded values into
-- this module never fabricates values for those four columns.

## The player-ID interface gap, flagged for the Architect

DK's format needs each player's **native DraftKings player ID** (`playerDkId`,
`ingestion/draftkings.py`), not just a name. That ID exists in `PlayerIdentity.sources
["draftkings"].native_id` (`normalization/identity.py`) -- but `projection/blend.py`'s
`PlayerProjection` (what `optimizer/lineup.py`'s `Lineup` actually carries per player) reads that
same `identity.sources["draftkings"]` entry once, *only* to look up DK salary
(`_salary_for`), and then drops the native ID string itself on the floor -- `PlayerProjection`
has no field for it at all. So `Lineup` (this module's actual input) cannot supply DK's own
player ID on its own; this module has to reach one stage further upstream than `Lineup` and
accept the normalization stage's `PlayerIdentity` list as a second, explicit input, then re-join
by `canonical_id` (`dk_ids_from_identities` below) to recover the one field `PlayerProjection`
silently discarded. This works today because `canonical_id` is preserved end to end, but it is a
real, awkward extra hop a caller must know to do -- **recommended fix for the Architect:** add a
`dk_native_id: str | None` field to `PlayerProjection` itself (populated the same way
`salary` already is, in `blend.py`'s `_salary_for`-equivalent lookup) so the output stage doesn't
need to reach two stages upstream past a module it isn't allowed to touch.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from nfl_dfs.normalization.identity import PlayerIdentity
from nfl_dfs.optimizer.lineup import Lineup

# DK's confirmed NFL Classic roster-slot column order (see module docstring, source (3)) -- matches
# PRD Section 3's roster and this project's own `optimizer/lineup.py` slot vocabulary exactly.
# Deliberately contains repeated header names ("RB" twice, "WR" three times) -- this is DK's own
# real convention (a positional format, not a name-keyed one), not a mistake to "fix" here.
DK_ROSTER_COLUMNS: tuple[str, ...] = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")

# `Lineup.slots` (optimizer/lineup.py) keys, in the exact order `DK_ROSTER_COLUMNS` above expects
# them filled. `Lineup.slots` always has exactly these 9 keys for any legally-solved lineup (see
# `_assign_slots`'s docstring) -- no defensive fallback is attempted for a missing key, since a
# `Lineup` missing one would itself be a broken upstream invariant, not a normal input this module
# should paper over.
_SLOT_KEYS_IN_DK_ORDER: tuple[str, ...] = (
    "QB",
    "RB1",
    "RB2",
    "WR1",
    "WR2",
    "WR3",
    "TE",
    "FLEX",
    "DST",
)

# DK's real upload template's leading contest-identity columns (see module docstring, source (3)).
# This module never fills these with real values -- see `include_contest_columns` below.
_CONTEST_IDENTITY_COLUMNS: tuple[str, ...] = ("Entry ID", "Contest Name", "Contest ID", "Entry Fee")


class MissingDraftKingsIdError(RuntimeError):
    """Raised when a lineup contains a player with no resolvable DraftKings native player ID.

    DK's bulk-upload format requires `"Name (ID)"` per roster cell (module docstring, confirmed
    against real exported DK data) -- a lineup can't be exported at all without it for every one
    of its 9 players. Raised rather than silently emitting a name-only or ID-less cell, since a
    malformed cell would produce a CSV that looks plausible but DK's own upload parser would
    likely reject or mis-map -- the worst possible failure mode for "the first thing Chris could
    genuinely try to upload to DraftKings."
    """


def dk_ids_from_identities(identities: list[PlayerIdentity]) -> dict[str, str]:
    """`canonical_id -> DraftKings native player ID`, read from each `PlayerIdentity`'s
    `sources["draftkings"]` (ADR-0013's join shape). See module docstring's "interface gap" note
    for why this has to come from `PlayerIdentity` (normalization stage) rather than `Lineup` or
    `PlayerProjection` (both stages downstream of where this ID gets dropped).

    A `PlayerIdentity` with no resolved DraftKings match (`sources.get("draftkings") is None`, or
    a resolved match with `native_id is None` -- both real, possible `PlayerIdentity` states per
    `normalization/identity.py`) simply has no entry in the returned map; callers must not assume
    every `canonical_id` a `Lineup` references resolves here.
    """
    result: dict[str, str] = {}
    for identity in identities:
        match = identity.sources.get("draftkings")
        if match is not None and match.native_id is not None:
            result[identity.canonical_id] = match.native_id
    return result


def format_player_cell(display_name: str, dk_native_id: str) -> str:
    """DK's confirmed player-identification format -- `"{Name} ({native DK player ID})"` (module
    docstring, source (1): a real exported `DKSalaries.csv` row, e.g. `"Rory McIlroy (17527360)"`).
    """
    return f"{display_name} ({dk_native_id})"


def lineup_to_dk_row(lineup: Lineup, dk_ids_by_canonical_id: dict[str, str]) -> list[str]:
    """One lineup's 9 roster cells, in `DK_ROSTER_COLUMNS` order, each formatted per
    `format_player_cell`.

    Raises `MissingDraftKingsIdError`, naming the specific player and slot, if any of the 9
    players has no entry in `dk_ids_by_canonical_id` (see `dk_ids_from_identities`) -- never
    emits a partial or name-only cell.
    """
    cells: list[str] = []
    for slot_key in _SLOT_KEYS_IN_DK_ORDER:
        player = lineup.slots[slot_key]
        native_id = dk_ids_by_canonical_id.get(player.canonical_id)
        if native_id is None:
            raise MissingDraftKingsIdError(
                f"No DraftKings native player ID found for {player.display_name!r} "
                f"(canonical_id={player.canonical_id!r}, slot={slot_key!r}) -- cannot build a "
                "DK-format cell without it. Check that this player's PlayerIdentity has a "
                "resolved 'draftkings' source (normalization stage) and that the identities list "
                "passed to this export includes this week's full DK-eligible pool."
            )
        cells.append(format_player_cell(player.display_name, native_id))
    return cells


@dataclass(frozen=True)
class DkCsvExport:
    """The DK bulk-upload CSV export result, plus the confidence/scope notes a consumer (Chris,
    or a report built on top of this) should see alongside the raw text -- see module docstring.
    """

    csv_text: str
    lineup_count: int
    notes: list[str] = field(default_factory=list)


_DEFAULT_NOTES: tuple[str, ...] = (
    "Player-identification format ('Name (ID)') is confirmed against a real exported DK "
    "DKSalaries.csv row -- high confidence. The 9-slot roster column order/headers are "
    "corroborated by convergent third-party documentation, not a byte-for-byte DK-authored "
    "primary source -- moderate-high confidence (see csv_export.py module docstring).",
    "This file is the 'player rows' portion only -- it does NOT include DK's real Entry ID/"
    "Contest ID/Contest Name/Entry Fee columns, because those values live in Chris's "
    "authenticated DK account state (his own already-registered contest entries), which this "
    "pipeline never ingests. Paste these rows over the placeholder players in a template Chris "
    "downloads himself from DK's Edit Entries page for the specific contest(s) he's entering -- "
    "the same workflow every third-party DFS tool (Stokastic, RotoQL, FantasyLabs, etc.) uses "
    "for the identical reason.",
)


def export_lineups_to_dk_csv(
    lineups: list[Lineup],
    identities: list[PlayerIdentity],
    *,
    include_contest_columns: bool = False,
) -> DkCsvExport:
    """Build the DK bulk-upload CSV for a full set of generated lineups.

    `identities` should be the same week's full `PlayerIdentity` list the normalization stage
    produced (PRD Section 5 step 2) -- see `dk_ids_from_identities`'s docstring for why this
    extra input (beyond `lineups` alone) is required.

    `include_contest_columns=True` prepends DK's real leading template columns (`Entry ID`,
    `Contest Name`, `Contest ID`, `Entry Fee`) with **empty** values, for a caller who wants a
    file shaped closer to DK's actual template to fill in or paste over by hand -- this module
    never fabricates values for those four columns (module docstring).

    Raises `MissingDraftKingsIdError` (via `lineup_to_dk_row`) if any player in any lineup has no
    resolvable DraftKings native ID in `identities`.
    """
    dk_ids = dk_ids_from_identities(identities)

    header = list(DK_ROSTER_COLUMNS)
    if include_contest_columns:
        header = list(_CONTEST_IDENTITY_COLUMNS) + header

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for lineup in lineups:
        row = lineup_to_dk_row(lineup, dk_ids)
        if include_contest_columns:
            row = ["", "", "", ""] + row
        writer.writerow(row)

    return DkCsvExport(csv_text=buf.getvalue(), lineup_count=len(lineups), notes=list(_DEFAULT_NOTES))


def write_dk_csv_file(export: DkCsvExport, path: str) -> None:
    """Convenience: write an already-built `DkCsvExport.csv_text` to a real file on disk. Kept
    separate from `export_lineups_to_dk_csv` so callers/tests that only need the in-memory text
    (e.g. to assert on its exact content) never have to touch the filesystem.
    """
    with open(path, "w", newline="") as f:
        f.write(export.csv_text)
