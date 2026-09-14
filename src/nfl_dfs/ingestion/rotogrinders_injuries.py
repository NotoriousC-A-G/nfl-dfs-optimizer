"""RotoGrinders "Situation Room" injury report ingestion (PRD Section 6, injury/role uncertainty
flag). A second, independent RotoGrinders product from LineupHQ (`ingestion/rotogrinders.py`) --
reuses that module's cookie-auth pattern but is a completely different endpoint/payload shape, so
it lives in its own file rather than being bolted onto `rotogrinders.py`.

## Why this exists, not the LineupHQ `INJURY` field

The prior `GameEnvironmentScore` round found RotoGrinders' LineupHQ projections payload
(`tests/fixtures/rotogrinders_projections.json`) carries a raw `INJURY` field per player, but
`rotogrinders.py`'s `parse_user_projections` drops it, and neither `SourcePlayer`
(`normalization/matcher.py`) nor `PlayerIdentity` (`normalization/identity.py`) had anywhere to
carry it even if it were parsed -- a real gap, not just an unwired field. Live investigation this
round found a materially better source instead: RotoGrinders' separate "Situation Room" injury
report product exposes a clean CSV export with a graded 0-10 `IMPACTRTG` severity score
RotoGrinders itself computes -- a real signal, not just a raw status string. Confirmed live:

    GET https://rotogrinders.com/grids/3102500.csv?site=draftkings

same cookie auth as `rotogrinders.py` (`config.rotogrinders_session_cookie`, sent as the `Cookie`
header), `200`, `content-type: text/csv`, real current (2026 Week 1) data.

## Live pull findings (2026-09-13, real data, not a fixture guess)

- **`STATUS` codes actually observed across a full pull: `O` (Out) and `Q` (Questionable) only**
  -- 23 real rows. The task brief flagged `D` (Doubtful) as "likely" but that is NOT confirmed
  live this pass -- no `D` or any other code appeared. Because the full code set isn't known,
  `RESOLVED_INJURY_STATUSES` below (used by `game_environment/score.py`'s rollup) is written as
  an allowlist of *confirmed-resolved* codes (currently just `{"O"}`), with everything else --
  including codes not yet observed live -- treated as unresolved/uncertain by default. That's a
  deliberate "don't assume a closed set" choice, not an oversight.

- **Checked specifically for `"P"` (Probable), per Chris's flag -- NOT confirmed to exist for
  this source, and one piece of evidence points the other way.** Investigated three ways:
  1. **Legend on the grid page itself:** the live HTML around the `<table>` (headers `PLAYER /
     TEAM / POS / STATUS / PART / IMPACTRTG`, no title/tooltip attributes on any of them) carries
     no status-code legend or glossary at all -- not "hidden and hard to find," genuinely absent
     from the markup.
  2. **Site-wide glossary/help pages:** `rotogrinders.com/glossary`, `/pages/glossary`, `/help`,
     `/pages/help`, `/pages/faq` all 404 live. The `/daily-fantasy-football` hub page (which links
     to this exact grid, both as "NFL Injury Report" and via a second alternate-slug link,
     `nfl-dfs-injury-report-the-situation-room-3102500` -- one more independent confirmation this
     is the same stable id referenced from multiple places on the site) has no glossary section
     and no mention of "Probable"/"Questionable"/"Doubtful"/"legend" anywhere in its markup.
  3. **Historical/archive data:** the CSV endpoint was probed live with `week`, `date`, `season`,
     `archive`, and `history` query params -- every one returned the exact same 24-row current
     snapshot, unchanged. There is no archive/history mechanism on this endpoint to check a prior
     week's status distribution against. The parallel NBA "Situation Room" product
     (`/grids/2703084.csv`) was also checked as a cross-reference of RG's general Situation Room
     template -- only `Q` appeared, on a 2-row sample, which doesn't confirm or rule out `P`
     either way.
  - **One suggestive (not conclusive) piece of evidence against `"P"`:** the grid page's own
    `<meta name="description">` copy reads *"...giving details on players who are out, doubtful,
    or questionable..."* -- naming exactly three tiers (Out/Doubtful/Questionable) and omitting
    "probable" entirely. That also happens to match the NFL's actual current official injury
    report, which dropped the "Probable" tag league-wide in 2016 (three tiers: Out/Doubtful/
    Questionable) -- so this isn't just marketing-copy imprecision, it's consistent with what the
    real official injury report this source is built from would contain. Still not proof RG's own
    `STATUS` column can never emit `"P"` (e.g. a source-specific "probable to play" tag some DFS
    aggregators add on top of the official three tiers), so this is presented as suggestive
    evidence, not a confirmed negative.
  - **Decision: `"P"` is NOT added to `RESOLVED_INJURY_STATUSES`.** Nothing here *confirms* `P`
    exists for this source, so there's nothing to add it for; if it turns out to exist and get
    swept into "unresolved" by the current default, that's the correct fail-safe behavior for an
    unconfirmed code, not a bug. **Re-verify this once real data from a week closer to game day
    is available** -- this check was run on an early-week pull (mostly `O`, one `Q`); practice-
    report-driven `Questionable`/`Doubtful`/(if it exists) `Probable`-type tags tend to proliferate
    later in the week as Wed/Thu/Fri practice reports come in, which is a much better test of
    whether `P` ever actually appears in this `STATUS` column.
- The raw CSV has a malformed trailing row (`,1,2,3,4,5,6,7,` -- empty `PLAYERID`, `PLAYER` field
  literally `"1"`). Looks like a stray column-index/debug artifact from RotoGrinders' own CSV
  generator, not real player data. `parse_injury_report_csv` drops any row with an empty/missing
  `PLAYERID` rather than assuming the malformed row always has one fixed shape.
- `TEAM` values match the same crosswalk-style vendor codes already confirmed for RotoGrinders'
  LineupHQ pull (`GBP`, `LVR`, `NEP`, `NOS`, `TBB`, `SFO`, ...) -- reuses the existing
  `team_aliases.py` `"rotogrinders"` table as-is; no new alias needed.
- `POS` values observed (`RB`, `TE`, `QB`, `WR`) are already canonical; no position alias needed
  either.
- `PLAYERID` is the same RG player-ID scheme as `ingestion/rotogrinders.py`'s `PLAYERID`/`RGID`
  (confirmed prior to this implementation pass, per the task brief) -- joins directly against
  `PlayerIdentity.sources["rotogrinders"].native_id`, no fuzzy name matching needed for this
  source. See `normalization/injury_lookup.py`.

## Grid ID stability -- checked live, not assumed

The brief asked whether the grid id `3102500` in the URL is a stable, season-long identifier, or
whether (like LineupHQ's per-account projection grid) it needs weekly re-discovery the way
`rotogrinders.py`'s `select_grid_id`/`parse_available_grids` re-discover *that* grid id every
pull. Checked live:

- `GET /grids/3102500` (no `.csv`) redirects to a slug'd HTML page
  (`/grids/nfl-dfs-injury-report-analysis-from-the-situation-room-3102500`) whose markup carries
  RotoGrinders' own editorial CMS admin controls (`Edit` / `Unpublish` / `Duplicate`,
  `data-auth="editor"`) all pointing at **the same numeric id, 3102500, as a CMS "post"** -- i.e.
  this is one persistent editorial post RotoGrinders' staff edit in place. Its own
  `<meta property="og:title">` literally reads "NFL Injury Report for Week 1" *this specific
  week*, which only makes sense if the same post/id gets its title (and presumably its
  underlying grid data) updated in place week to week, not recreated fresh with a new id.
- The already-relied-upon LineupHQ house grid id (`3350867`, see `rotogrinders.py`'s
  `select_grid_id` and its test asserting exactly that id) resolves at the exact same kind of
  stable slug'd URL (`/grids/nfl-dfs-projections-3350867`) on RotoGrinders' own `/grids` hub
  page -- the same ID class/pattern (one evergreen numeric id per RG "grid" product, content
  edited in place) already proven stable elsewhere in this codebase, not something specific to
  the injury report.
- No live, per-account/self-discovery endpoint exists for this specific grid the way LineupHQ has
  `user-projections?list=1` -- `/grids` (RotoGrinders' own public hub/index page) does **not**
  currently list the NFL injury grid at all (only NBA's equivalent product,
  `nba-dfs-injury-report-analysis-from-the-situation-room-2703084`, appeared live), so there is
  no automatic-discovery fallback to build against even if we wanted one. The featured/unfeatured
  admin toggle visible on the injury grid's own page suggests that hub listing is editor-curated,
  not a reliable index of "every grid that currently exists."
- **Conclusion -- moderate-to-high confidence, not certain:** treat `3102500` as a stable,
  hardcoded, season-long constant (see `INJURY_GRID_ID` below), the same way `3350867` is
  effectively treated as stable elsewhere in this codebase, rather than re-discovering it per
  pull. This is inferred from one live snapshot's CMS structure (an evergreen "post" being
  edited in place, not a per-week-generated grid), not verified against a second week's data --
  this session cannot observe next week's pull. `fetch_injury_report`'s `raise_for_status()` plus
  `parse_injury_report_csv`'s column-shape check (`_EXPECTED_COLUMNS`) will fail loudly rather
  than silently return stale/empty data if RotoGrinders ever restructures this product --
  re-check this finding if that ever happens, same "verify against a fresh live pull, don't
  assume" discipline used to reach this conclusion in the first place.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import requests

from nfl_dfs.config import config

# See "Grid ID stability" above -- treated as a stable, season-long constant, not re-discovered.
INJURY_GRID_ID = "3102500"
INJURY_REPORT_CSV_URL = f"https://rotogrinders.com/grids/{INJURY_GRID_ID}.csv"

_TIMEOUT = 20.0
_EXPECTED_COLUMNS = {"PLAYERID", "PLAYER", "TEAM", "POS", "STATUS", "PART", "IMPACTRTG"}


@dataclass(frozen=True)
class InjuryReportEntry:
    """One player's row from RotoGrinders' Situation Room injury export."""

    rotogrinders_player_id: str  # RG's own PLAYERID -- joins directly to
    # PlayerIdentity.sources["rotogrinders"].native_id, see normalization/injury_lookup.py
    name: str
    team: str  # raw RG team code (crosswalk-style, e.g. "GBP") -- normalize via team_aliases.py
    # the same way rotogrinders.py's SourcePlayer.team is normalized; not normalized here since
    # this module, like rotogrinders.py, is a pure-parse ingestion layer.
    position: str  # raw RG position label; normalize via position_aliases.py if needed
    status: str  # raw single-letter status code -- see module docstring; DO NOT assume a closed
    # set, only "O" is confirmed to mean "resolved" (see RESOLVED_INJURY_STATUSES in
    # game_environment/score.py)
    body_part: str  # free-text injured body part / reason, e.g. "Knee", "Personal", "Undisclosed"
    impact_rating: int  # RotoGrinders' own 0-10 severity/impact score


def parse_injury_report_csv(csv_text: str) -> list[InjuryReportEntry]:
    """Pure parse of the injury report CSV export into `InjuryReportEntry` rows. Drops the
    malformed trailing row confirmed live (empty `PLAYERID`) -- see module docstring.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = set(reader.fieldnames or [])
    missing = _EXPECTED_COLUMNS - fieldnames
    if missing:
        raise RuntimeError(
            f"RotoGrinders injury report CSV is missing expected column(s) {sorted(missing)} -- "
            f"the export's shape may have changed (got columns {sorted(fieldnames)})"
        )

    entries: list[InjuryReportEntry] = []
    for row in reader:
        player_id = (row.get("PLAYERID") or "").strip()
        if not player_id:
            # Malformed trailing row (or any other blank/garbage line) -- see module docstring's
            # live-pull findings. Real rows always carry a non-empty PLAYERID.
            continue
        entries.append(
            InjuryReportEntry(
                rotogrinders_player_id=player_id,
                name=row["PLAYER"],
                team=row.get("TEAM") or "",
                position=row.get("POS") or "",
                status=(row.get("STATUS") or "").strip(),
                body_part=row.get("PART") or "",
                impact_rating=int((row.get("IMPACTRTG") or "0").strip()),
            )
        )
    return entries


def fetch_injury_report(
    *,
    site: str = "draftkings",
    session_cookie: str | None = None,
    session: requests.Session | None = None,
) -> list[InjuryReportEntry]:
    """Live pull: same cookie-auth pattern as `rotogrinders.py`'s `fetch_rotogrinders_players`."""
    cookie = session_cookie or config.rotogrinders_session_cookie
    if not cookie:
        raise RuntimeError("ROTOGRINDERS_SESSION_COOKIE is not configured")
    http = session or requests
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}

    response = http.get(
        INJURY_REPORT_CSV_URL, headers=headers, params={"site": site}, timeout=_TIMEOUT
    )
    response.raise_for_status()
    return parse_injury_report_csv(response.text)
