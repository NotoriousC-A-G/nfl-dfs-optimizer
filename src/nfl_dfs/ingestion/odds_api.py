"""Odds API ingestion for `GameEnvironmentScore`'s implied-team-total component (PRD Section 6,
44.4% weight; population/window spec ADR-0003). Phase 0 confirmed the endpoint, auth, and that
DraftKings' own line is present per event (`docs/phase0/data-availability.md`, Odds API section);
this module is the actual pull + parse + implied-total + z-score pipeline built against that.

`GET https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds?regions=us&markets=spreads,
totals&oddsFormat=american`, `apiKey` via `config.odds_api_key` -- re-confirmed live this pass
(2026-09-13, week 1): 14 events returned, every one carrying a `"draftkings"` bookmaker with both
`spreads` and `totals` markets.

## Team names: full names, not abbreviations -- confirmed live, mapped here

The API returns full franchise names (`"Jacksonville Jaguars"`, `"Cleveland Browns"`, etc.), not
any abbreviation scheme -- confirmed for 28 of the 32 teams directly from this pass's live pull
(the remaining 4 -- Patriots, Seahawks, 49ers, Rams -- weren't in this particular pull because
those two games had already kicked off by the time it ran and the API stopped listing them; see
the "known limitation" note below). Those 4 are filled in using the identical naming convention
observed on the other 28 (full official franchise name, no city/mascot abbreviation) -- high
confidence, but flagged here as *not* individually live-confirmed the way the other 28 are, since
"the pattern holds" is a weaker claim than "this exact string was seen in a live response."
`TEAM_NAME_TO_ABBR` maps every full name to this project's canonical DK abbreviation
(`normalization/team_aliases.py`'s `CANONICAL_TEAMS`) -- no such table existed anywhere in the
codebase before this module.

## Known limitation: the odds feed only lists games that haven't started yet

Live-observed, not assumed: this pass's pull returned 14 of week 1's 16 games -- the 2 games
already underway at pull time (New England @ Seattle, San Francisco @ LA Rams, both kicked off
before this ran) were simply absent from the response, not present-with-stale-data. This means a
cross-sectional z-score population pulled close to or after an early game's kickoff will be
smaller than "all 32 teams" (ADR-0003's stated population) -- teams in an in-progress or completed
game that week drop out of the population entirely, not just out of that one team's own row. This
is a real, reportable gap against ADR-0003's population spec, not a bug in this module: flagged
for the Architect, since the fix (if any) is a spec decision (pull before any game that week has
kicked off; or backfill in-progress/completed games' lines from another source; or accept a
sub-32 population on partial-slate pulls) rather than something this ingestion layer can resolve
on its own.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import pandas as pd

from nfl_dfs.config import config
from nfl_dfs.ingestion.game_environment_stats import cross_sectional_zscore_by_group
from nfl_dfs.normalization.team_aliases import normalize_team

ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
DK_BOOKMAKER_KEY = "draftkings"
_TIMEOUT = 20.0

# 28 of these were observed verbatim in a live pull (2026-09-13, week 1); the other 4 (marked
# below) follow the identical naming convention but weren't individually observed this pass --
# see module docstring.
TEAM_NAME_TO_ABBR: dict[str, str] = {
    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV",
    "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR",  # not observed live this pass -- see docstring
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",  # not observed live this pass -- see docstring
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF",  # not observed live this pass -- see docstring
    "Seattle Seahawks": "SEA",  # not observed live this pass -- see docstring
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS",
}
assert len(TEAM_NAME_TO_ABBR) == 32


def team_abbr(full_name: str) -> str | None:
    return TEAM_NAME_TO_ABBR.get(full_name)


@dataclass(frozen=True)
class GameOdds:
    home_team: str  # canonical DK abbreviation
    away_team: str
    commence_time: str  # ISO8601 UTC, as returned by the API (e.g. "2026-09-13T17:03:55Z")
    home_spread: float | None  # DK's signed spread outcome for the home team (negative = favorite)
    away_spread: float | None
    total: float | None  # DK's game total line
    bookmaker_last_update: str | None


def parse_dk_odds_events(payload: list[dict]) -> list[GameOdds]:
    """Pure parse of the `/v4/sports/americanfootball_nfl/odds` response: one `GameOdds` per
    event that (a) has both team names recognized in `TEAM_NAME_TO_ABBR` and (b) has a
    `"draftkings"` bookmaker with both `spreads` and `totals` markets present. An event failing
    either check is skipped with a `warnings.warn` (not raised -- a single missing DK line
    shouldn't take down the whole week's pull) rather than silently dropped; callers that need to
    know what was skipped should run with warnings surfaced (e.g. `pytest -W error` in tests, or
    `warnings.catch_warnings(record=True)` in the live check script)."""
    games = []
    for event in payload:
        home_name, away_name = event.get("home_team"), event.get("away_team")
        home_abbr, away_abbr = team_abbr(home_name), team_abbr(away_name)
        if home_abbr is None or away_abbr is None:
            warnings.warn(
                f"unrecognized team name(s) in odds event -- home={home_name!r} away={away_name!r} "
                f"not in TEAM_NAME_TO_ABBR; skipping this event",
                stacklevel=2,
            )
            continue
        dk = next((b for b in event.get("bookmakers", []) if b.get("key") == DK_BOOKMAKER_KEY), None)
        if dk is None:
            warnings.warn(
                f"no DraftKings bookmaker line for {away_abbr} @ {home_abbr}; skipping this event",
                stacklevel=2,
            )
            continue
        markets = {m["key"]: m for m in dk.get("markets", [])}
        spreads, totals = markets.get("spreads"), markets.get("totals")
        if spreads is None or totals is None:
            warnings.warn(
                f"DraftKings line for {away_abbr} @ {home_abbr} is missing a spreads or totals "
                f"market; skipping this event",
                stacklevel=2,
            )
            continue
        home_point = next((o["point"] for o in spreads["outcomes"] if o["name"] == home_name), None)
        away_point = next((o["point"] for o in spreads["outcomes"] if o["name"] == away_name), None)
        total_point = totals["outcomes"][0]["point"] if totals.get("outcomes") else None
        games.append(
            GameOdds(
                home_team=home_abbr,
                away_team=away_abbr,
                commence_time=event.get("commence_time"),
                home_spread=home_point,
                away_spread=away_point,
                total=total_point,
                bookmaker_last_update=dk.get("last_update"),
            )
        )
    return games


def implied_team_totals(game: GameOdds) -> dict[str, float]:
    """`implied_total(team) = total / 2 - team's_own_signed_spread / 2` -- e.g. a -7 favorite in a
    47-point total: `47/2 - (-7)/2 = 23.5 + 3.5 = 27.0`; the +7 underdog: `23.5 - 3.5 = 20.0`
    (sums back to the 47-point total, as it must). Raises if any of `total`/`home_spread`/
    `away_spread` is `None` (an incomplete DK line -- `parse_dk_odds_events` already filters most
    of these out, but a caller constructing `GameOdds` some other way should get a clear error,
    not a silent `None - None` crash three lines later)."""
    if game.total is None or game.home_spread is None or game.away_spread is None:
        raise ValueError(f"incomplete odds line for {game.away_team} @ {game.home_team}: {game}")
    return {
        game.home_team: game.total / 2 - game.home_spread / 2,
        game.away_team: game.total / 2 - game.away_spread / 2,
    }


def schedule_week_map(schedule: pd.DataFrame) -> dict[tuple[str, str, int], int]:
    """`(away_abbr, home_abbr, season) -> week`, from an `nfl_data_py.import_schedules()` frame.
    Directional (away, home) rather than an unordered team pair because two division rivals can
    play each other twice in a season at different weeks with home/away swapped -- the directional
    pair is unique per meeting, an unordered pair would not be."""
    out: dict[tuple[str, str, int], int] = {}
    for _, row in schedule.iterrows():
        away = normalize_team("nflverse_schedule", row["away_team"])
        home = normalize_team("nflverse_schedule", row["home_team"])
        out[(away, home, int(row["season"]))] = int(row["week"])
    return out


def implied_totals_long(games: list[GameOdds], week_map: dict[tuple[str, str, int], int], season: int) -> pd.DataFrame:
    """Games -> a long-format (one row per team per game) DataFrame with columns `team`, `week`,
    `implied_total`. A game whose `(away, home, season)` isn't found in `week_map` gets a
    `warnings.warn` and is dropped -- can happen for a preseason/exhibition game the odds feed
    includes but nflverse's schedule (regular season + playoffs) doesn't, or a schedule pulled for
    the wrong season."""
    rows = []
    for game in games:
        key = (game.away_team, game.home_team, season)
        week = week_map.get(key)
        if week is None:
            warnings.warn(
                f"no schedule week found for {game.away_team} @ {game.home_team} (season {season}) "
                f"-- skipping this event's implied totals",
                stacklevel=2,
            )
            continue
        for team, total in implied_team_totals(game).items():
            rows.append({"team": team, "week": week, "implied_total": total})
    return pd.DataFrame(rows, columns=["team", "week", "implied_total"])


def compute_implied_total_zscores(implied_totals: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional-per-week z-score (ADR-0003) -- no shrinkage/fallback needed here, since a
    full Vegas line exists from week 1 (ADR-0003's own stated rationale). See the module docstring
    for the known caveat that the per-week population can be smaller than 32 teams on a partial
    pull (games already underway drop out of the odds feed entirely)."""
    out = implied_totals.copy()
    out["implied_total_z"] = cross_sectional_zscore_by_group(out, "implied_total", "week")
    return out


def fetch_dk_implied_totals(season: int, *, session=None) -> pd.DataFrame:
    """Live pull + full pipeline: Odds API events -> DK-line-only `GameOdds` -> implied totals ->
    week-mapped via nflverse's schedule -> cross-sectional z-scored. Returns columns `team`,
    `week`, `implied_total`, `implied_total_z`."""
    import nfl_data_py as nfl  # deferred import, same rationale as nflverse.py
    import requests

    if not config.odds_api_key:
        raise RuntimeError("ODDS_API_KEY is not configured")
    http = session or requests
    response = http.get(
        ODDS_URL,
        params={
            "regions": "us",
            "markets": "spreads,totals",
            "oddsFormat": "american",
            "apiKey": config.odds_api_key,
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    games = parse_dk_odds_events(response.json())

    schedule = nfl.import_schedules([season])
    week_map = schedule_week_map(schedule)

    long_df = implied_totals_long(games, week_map, season)
    return compute_implied_total_zscores(long_df)
