"""RotoGrinders LineupHQ ingestion (PRD Section 4/5 step 1; Phase 0 confirmed live, extended here).

Phase 0 confirmed the two-call shape (`user-info` then `user-projections`) but did not document
how the projections call's `source` (grid_id) parameter is obtained -- live investigation this
pass found it: the LineupHQ single-page app calls the *same* `user-projections` endpoint with
`list=1` instead of `source=<id>` to get back the account's available projection grids
(reverse-engineered from `lineuphqIndex.js`'s `og()`/`ig()` functions, not documented anywhere
Phase 0 looked). A grid must then be picked from that list -- this module prefers RG's own
house grid (`is_rg: true`) over a third-party shared grid when the account has access to more
than one, since the house grid is the one Section 4 actually asked for ("Ownership projections,
expert rankings"), not a specific named third-party product.

Cookie auth via `config.rotogrinders_session_cookie`, sent as the `Cookie` header -- same
approach Phase 0 verified working outside the browser.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import requests

from nfl_dfs.config import config
from nfl_dfs.normalization.matcher import SourcePlayer

LINEUPHQ_PAGE_URL = "https://rotogrinders.com/lineuphq/nfl"
USER_INFO_URL = "https://rotogrinders.com/api/user-info"
PROJECTIONS_URL = "https://rotogrinders.com/api/user-projections"

_TIMEOUT = 20.0
# The LineupHQ page embeds the account's user id + a session token as the src of an <iframe>
# pointing at the SPA itself -- there's no separate "give me my token" endpoint (Phase 0 flagged
# the token as "page-embedded", confirmed here to mean literally this iframe attribute).
_IFRAME_RE = re.compile(r"lineuphq\.rotogrinders\.com/nfl\?user=(?P<user>\d+)&token=(?P<token>[0-9a-f]+)")


def extract_user_and_token(lineuphq_page_html: str) -> tuple[str, str]:
    match = _IFRAME_RE.search(lineuphq_page_html)
    if not match:
        raise RuntimeError(
            "could not find the embedded LineupHQ iframe user/token in the page -- RotoGrinders "
            "may have changed the page's structure, or the session cookie is no longer logged in"
        )
    return match.group("user"), match.group("token")


def parse_available_grids(payload: dict) -> dict[str, dict]:
    """Pure parse of a `list=1` response: grid_id (string) -> grid metadata."""
    grids = payload.get("data", {}).get("grids")
    if not grids:
        raise RuntimeError("RotoGrinders user-projections?list=1 returned no grids for this account")
    return grids


def select_grid_id(grids: dict[str, dict]) -> str:
    accessible = [g for g in grids.values() if g.get("has_access")]
    if not accessible:
        raise RuntimeError(
            f"account has no accessible RotoGrinders projection grid (grids seen: "
            f"{[g.get('name') for g in grids.values()]})"
        )
    accessible.sort(key=lambda g: (not g.get("is_rg", False), g.get("order", 0)))
    return str(accessible[0]["id"])


def parse_user_projections(payload: dict) -> list[SourcePlayer]:
    """Pure parse of one `source=<grid_id>` response into `SourcePlayer` rows.

    Live-confirmed field names (a real "NFL DFS Projections" grid pull): `PLAYERID` and `RGID`
    carry the same value (ADR-0013's assumption, re-confirmed) -- `PLAYERID` used as native_id.
    `TEAM` uses crosswalk-style codes for the same 8 franchises the nflverse crosswalk does
    (`GBP, JAC, KCC, LVR, NEP, NOS, SFO, TBB`), not DK's `GB/JAX/KC/LV/NE/NO/SF/TB` -- this is
    the real finding that fills in team_aliases.py's previously-empty `rotogrinders` table (see
    that module). `POS` values seen live: `QB, RB, WR, TE, DST, K` -- `DST` is already canonical
    and `K` isn't in this project's five-position vocabulary at all (same as the crosswalk's
    out-of-scope positions), so position_aliases.py's empty `rotogrinders` table is confirmed
    correct as-is, not just left unverified.
    """
    source = payload.get("data", {}).get("source", {})
    players = []
    for player_id, row in source.items():
        players.append(
            SourcePlayer(
                native_id=str(row.get("PLAYERID") or player_id),
                name=row["PLAYER"],
                team=row.get("TEAM"),
                position=row["POS"],
            )
        )
    return players


def _fetch_projections_payload(
    *, site: str = "draftkings", sport: str = "nfl", session_cookie: str | None = None,
    session: requests.Session | None = None,
) -> dict:
    """The shared page -> user-info -> grid-list -> projections auth dance. Both `fetch_rotogrinders_players`
    (identity) and `fetch_rotogrinders_ownership` (POWN) parse this same raw payload two different ways --
    it carries both (see `parse_projected_ownership`'s docstring for the live-confirmed field).
    """
    cookie = session_cookie or config.rotogrinders_session_cookie
    if not cookie:
        raise RuntimeError("ROTOGRINDERS_SESSION_COOKIE is not configured")
    http = session or requests
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}

    page = http.get(LINEUPHQ_PAGE_URL, headers=headers, timeout=_TIMEOUT)
    page.raise_for_status()
    user, token = extract_user_and_token(page.text)

    info = http.get(USER_INFO_URL, headers=headers, params={"user": user, "token": token}, timeout=_TIMEOUT)
    info.raise_for_status()
    info_data = info.json()["data"]
    account_user_id, storage = info_data["id"], info_data["cloud_storage_key"]

    grids_response = http.get(
        PROJECTIONS_URL,
        headers=headers,
        params={
            "sport": sport,
            "site": site,
            "user_id": account_user_id,
            "storage": storage,
            "timestamp": int(time.time() * 1000),
            "list": 1,
        },
        timeout=_TIMEOUT,
    )
    grids_response.raise_for_status()
    grid_id = select_grid_id(parse_available_grids(grids_response.json()))

    projections_response = http.get(
        PROJECTIONS_URL,
        headers=headers,
        params={
            "sport": sport,
            "site": site,
            "user_id": account_user_id,
            "storage": storage,
            "timestamp": int(time.time() * 1000),
            "source": grid_id,
        },
        timeout=_TIMEOUT,
    )
    projections_response.raise_for_status()
    return projections_response.json()


def fetch_rotogrinders_players(
    *, site: str = "draftkings", sport: str = "nfl", session_cookie: str | None = None,
    session: requests.Session | None = None,
) -> list[SourcePlayer]:
    payload = _fetch_projections_payload(site=site, sport=sport, session_cookie=session_cookie, session=session)
    return parse_user_projections(payload)


@dataclass(frozen=True)
class LineupHqOwnershipRow:
    """One player's live projected-ownership + salary from the same LineupHQ grid `fetch_rotogrinders_players`
    reads identity from -- kept separate from `SourcePlayer` (this codebase's identity-matching input shape,
    e.g. `pff.py`'s own `PffGradeRow`/`SourcePlayer` split) since ownership/salary are payload data, not
    identity fields.

    `slate` matters a lot here (ADR-0026): a single grid pull mixes every live slate window at once (live-
    confirmed real values: `MAIN`, `WED`, `THU`, `SNF`, `MNF`) -- a player whose game isn't part of the
    slate you actually care about correctly shows 0% ownership for that contest, which looks identical to a
    real data bug until `slate` is checked. `filter_to_main_slate` below is the fix -- the same "don't
    silently mix slates" lesson `draftkings.py`'s `select_classic_slate` already applied to DK's own API.
    """

    native_id: str
    name: str
    position: str
    team: str | None
    salary: int
    projected_ownership: float
    slate: str


def _parse_percent(raw: str | float | int | None) -> float:
    """`POWN` is a percent string like `"41.48%"` (live-confirmed, ADR-0026) -- this returns the number on a
    0-100 scale, matching `PlayerExposureRow.ownership_overall`'s own scale in `rotogrinders_resultsdb.py`.
    """
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    return float(raw.rstrip("%") or 0.0)


def parse_projected_ownership(payload: dict) -> list[LineupHqOwnershipRow]:
    """Pure parse of the `POWN` field already present in every `user-projections` row (live-confirmed,
    ADR-0026) but never extracted until now -- `parse_user_projections` above only reads identity fields.
    Returns every slate window's rows unfiltered -- callers who specifically want the main Classic slate
    must call `filter_to_main_slate` (see `LineupHqOwnershipRow`'s docstring for why that matters).
    """
    source = payload.get("data", {}).get("source", {})
    rows = []
    for player_id, row in source.items():
        salary_raw = row.get("SALARY")
        # Live-confirmed: a handful of non-rosterable-this-slate players (e.g. a kicker with no DK salary
        # line) carry SALARY: null, not a numeric string like every real rosterable player does.
        salary = int(float(salary_raw)) if salary_raw not in (None, "") else 0
        rows.append(
            LineupHqOwnershipRow(
                native_id=str(row.get("PLAYERID") or player_id),
                name=row["PLAYER"],
                position=row["POS"],
                team=row.get("TEAM"),
                salary=salary,
                projected_ownership=_parse_percent(row.get("POWN")),
                slate=row.get("SLATE") or "",
            )
        )
    return rows


MAIN_SLATE_LABEL = "MAIN"


def filter_to_main_slate(rows: list[LineupHqOwnershipRow]) -> list[LineupHqOwnershipRow]:
    """Keeps only the combined Sunday main-slate rows (live-confirmed real `SLATE` value `"MAIN"`),
    dropping the separate single-game slate windows (`WED`/`THU`/`SNF`/`MNF`) the same grid pull mixes in.
    A player genuinely not part of the main slate correctly shows 0% ownership for it -- that is not the
    same thing as a main-slate player being mispriced or underowned (ADR-0026).
    """
    return [row for row in rows if row.slate == MAIN_SLATE_LABEL]


def fetch_rotogrinders_ownership(
    *, site: str = "draftkings", sport: str = "nfl", session_cookie: str | None = None,
    session: requests.Session | None = None,
) -> list[LineupHqOwnershipRow]:
    payload = _fetch_projections_payload(site=site, sport=sport, session_cookie=session_cookie, session=session)
    return parse_projected_ownership(payload)
