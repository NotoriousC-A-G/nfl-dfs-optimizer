"""Footballguys' DFS "Percent Rostered Projections" page -- a second, independent projected-ownership
source (found 2026-10-04, when RotoGrinders' `POWN` came back a literal `0.00%` for every player in the
late-window games: DEN, LAC, MIA, MIN, SEA, SF, KC, LV).

`https://www.footballguys.com/dfs-roster-percentages?site=draftkings&week=N` returns five unlabeled
tables, in order QB, RB, WR, TE, DST, each row `[name, "12.5%"]`. No team column, and only the top of
each position is listed (17 QB, 23 RB, 47 WR, 24 TE, 24 DST on the week-4 pull) -- a player missing from
the list is "below the listing floor", not a known 0%. Fetched with the same session cookie as the
rest of `ingestion/footballguys*.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from nfl_dfs.config import config

URL = "https://www.footballguys.com/dfs-roster-percentages"
_TABLE_POSITIONS = ("QB", "RB", "WR", "TE", "DST")
_TIMEOUT = 30


@dataclass(frozen=True)
class FbgOwnershipRow:
    name: str
    position: str
    projected_ownership: float  # percent, e.g. 31.9


def parse_roster_percentages(html: str) -> list[FbgOwnershipRow]:
    """Pure parse. Raises `ValueError` if the page no longer has exactly five tables -- position is
    inferred from table order, so a layout change must fail loudly, never silently mislabel."""
    tables = BeautifulSoup(html, "html.parser").find_all("table")
    if len(tables) != len(_TABLE_POSITIONS):
        raise ValueError(
            f"expected {len(_TABLE_POSITIONS)} ownership tables (QB/RB/WR/TE/DST), found {len(tables)} -- "
            "Footballguys changed the page layout; not guessing positions."
        )
    rows: list[FbgOwnershipRow] = []
    for position, table in zip(_TABLE_POSITIONS, tables, strict=True):
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2 or not cells[1].endswith("%"):
                continue
            rows.append(FbgOwnershipRow(cells[0], position, float(cells[1].rstrip("%"))))
    return rows


def fetch_roster_percentages(
    week: int, *, site: str = "draftkings", session_cookie: str | None = None
) -> list[FbgOwnershipRow]:
    cookie = session_cookie or config.footballguys_session_cookie
    if not cookie:
        raise RuntimeError("FOOTBALLGUYS_SESSION_COOKIE is not configured")
    response = requests.get(
        URL,
        params={"site": site, "week": week},
        headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return parse_roster_percentages(response.text)
