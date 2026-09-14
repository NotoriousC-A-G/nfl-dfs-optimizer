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


def fetch_rotogrinders_players(
    *, site: str = "draftkings", sport: str = "nfl", session_cookie: str | None = None,
    session: requests.Session | None = None,
) -> list[SourcePlayer]:
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
    return parse_user_projections(projections_response.json())
