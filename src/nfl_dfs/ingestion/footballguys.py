"""Footballguys ingestion (PRD Section 4/5 step 1; Phase 0 confirmed live, extended here).

Phase 0 confirmed the endpoint returns server-rendered HTML, not JSON. What Phase 0 didn't check
(no sample was captured beyond one `data-playerid` grep) is that `pos=all` is accepted but
silently falls back to QB-only results -- a real discrepancy from what the URL template
(`pos=<pos|all>`) implies. Confirmed live: the position filter's actual valid values are the
five radio-button values embedded in the page's own position-select component,
`qb, rb, wr, te, td` -- note `td` ("team defense"), not `dst`/`def` as position_aliases.py's
prior placeholder guessed. This module pulls all five explicitly and dedupes by `data-playerid`.

HTML parsing uses BeautifulSoup (see pyproject.toml's dependency comment for why
`pandas.read_html` can't do this: team and position live in nested `<span>` elements inside the
name cell, not their own columns, and `data-playerid` -- this source's only native player ID --
is an element attribute `read_html` doesn't expose at all).

Cookie auth via `config.footballguys_session_cookie`, sent as the `Cookie` header -- the approach
Phase 0 verified working outside the browser.
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from nfl_dfs.config import config
from nfl_dfs.normalization.matcher import SourcePlayer

PROJECTIONS_URL = "https://www.footballguys.com/projections"

# The position-select component's real radio values (reverse-engineered from
# https://www.footballguys.com/projections/duration/draftkings's rendered `pos-option` inputs).
# "all" is accepted by the endpoint but silently returns QB-only rows -- not used here.
POSITIONS: tuple[str, ...] = ("qb", "rb", "wr", "te", "td")

_TIMEOUT = 20.0


def parse_projection_rows(html: str) -> list[SourcePlayer]:
    """Pure parse of one position pull's HTML fragment into `SourcePlayer` rows.

    Live-confirmed: team codes seen (`ARI, ATL, ..., JAX, KC, LV, NE, NO, SF, TB, WAS`, across a
    QB pull and a `td` team-defense pull) already match DK's canonical vocabulary -- this fills
    in team_aliases.py's previously-empty `footballguys` table by *confirming no aliasing is
    needed*, not leaving it unverified. Position labels are `QB/RB/WR/TE` (already canonical) and
    `TD` for team defenses, which is NOT canonical and NOT what position_aliases.py's prior
    placeholder guessed (`DEF`/`D`) -- this is the real alias this module's live pull found.
    """
    soup = BeautifulSoup(html, "html.parser")
    players = []
    for row in soup.select("tr[data-playerid]"):
        position_span = row.select_one("td span[class^='pos-']")
        team_span = row.select_one("td span.team")
        if position_span is None:
            # A malformed/header-like row with a playerid but no recognizable position cell --
            # skip rather than guess, but this is a parsing robustness skip, not a "player
            # missing from source" case (that's the matcher's job, downstream of this module).
            continue
        players.append(
            SourcePlayer(
                native_id=row["data-playerid"],
                name=row["data-playername"],
                team=team_span.get_text(strip=True) if team_span is not None else None,
                position=position_span.get_text(strip=True),
            )
        )
    return players


def fetch_footballguys_players(
    week: int,
    *,
    dfs_site: str = "draftkings",
    session_cookie: str | None = None,
    session: requests.Session | None = None,
) -> list[SourcePlayer]:
    """Live pull: one HTML request per entry in POSITIONS (the endpoint has no working
    all-positions-at-once mode -- see module docstring), deduped by native_id.
    """
    cookie = session_cookie or config.footballguys_session_cookie
    if not cookie:
        raise RuntimeError("FOOTBALLGUYS_SESSION_COOKIE is not configured")
    http = session or requests
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}

    by_id: dict[str, SourcePlayer] = {}
    for position in POSITIONS:
        response = http.get(
            PROJECTIONS_URL,
            headers=headers,
            params={
                "componentIdNum": 1,
                "week": week,
                "nflTeam": "all",
                "pos": position,
                "durationTypeKey": "weekly",
                "posGroupKey": "all",
                "dfsSite": dfs_site,
                "reload": 1,
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        for player in parse_projection_rows(response.text):
            by_id.setdefault(player.native_id, player)
    return list(by_id.values())
