"""Player-props ingestion from **the same vendor already in production for game-level lines**
(`ingestion/odds_api.py` -- The Odds API, `config.odds_api_key`). This is new endpoints/markets on
that existing API, not a new vendor integration: same key, same account, same `TEAM_NAME_TO_ABBR`
table, same `DK_BOOKMAKER_KEY` filter, same "confirmed live, not assumed" documentation posture.

Chris was explicit (Section 6, ceiling/volatility work): "the odds themselves, not just the number,
are the point." A DK player-prop line (e.g. "DeVonta Smith 71.5 receiving yards") plus its priced
Over/Under is a market-implied *distribution* signal, not just a median projection -- this module's
whole job is to preserve both sides' prices (and the line), not collapse them into a single number.

## Two real endpoints, confirmed live this session (2026-09-28)

1. `GET /v4/sports/americanfootball_nfl/events?apiKey=...` -- returns upcoming events (`id`,
   `commence_time`, `home_team`, `away_team`, full franchise names -- same names
   `odds_api.py`'s `TEAM_NAME_TO_ABBR` already covers). **Confirmed live: costs 0 credits**
   (`x-requests-last: 0` on a real pull that returned 17 upcoming events for 2026).
2. `GET /v4/sports/americanfootball_nfl/events/{eventId}/odds?apiKey=...&regions=us&markets=<csv>
   &oddsFormat=american` -- per-event player props. **Confirmed live** against a real event
   (Eagles @ Bears, 2026-09-29): 7 requested markets x 1 region = **7 credits charged**
   (`x-requests-last: 7`), matching the documented cost model (markets x regions, charged
   regardless of how many of those markets actually return outcomes for that event). DraftKings
   was one of 8 bookmakers present (`williamhill_us`, `fanduel`, `betrivers`, `betonlineag`,
   `betmgm`, `bovada`, `fanatics` also returned) -- this module filters to DK only, same as
   `odds_api.py`'s game-level pull (`DK_BOOKMAKER_KEY`, reused here, not redefined).

## Outcome shape: two real shapes, not one -- confirmed live, not assumed

The over/under markets (`player_pass_yds`, `player_receptions`, etc.) return one outcome per side:
`{"name": "Over"|"Under", "description": "<player name>", "price": <American odds int>,
"point": <float line>}` -- confirmed live, e.g. `{"name": "Over", "description": "Jalen Hurts",
"price": 123, "point": 1.5}` (player_pass_tds).

`player_anytime_td` is genuinely **single-sided** -- confirmed live, DraftKings offers only a
`"Yes"` outcome per player, no `"No"` side at all: `{"name": "Yes", "description": "Saquon
Barkley", "price": -115}` -- note **no `"point"` key whatsoever** on this outcome shape, not a
`null` value. This module treats "does this outcome have a `point` key" as the live signal for
which of the two shapes it's looking at, rather than hard-coding it per market name, since a future
single-sided market (`player_1st_td`, `player_last_td` -- confirmed available per DK per this
project's earlier live spot-check, not yet exercised by this module) would need the same handling.

**A real, live-confirmed difference from `odds_api.py`'s game-level endpoint**: on this
per-event `/events/{id}/odds` endpoint, the bookmaker dict itself carries no `"last_update"` field
(`dk.keys() == {"key", "title", "markets"}`, confirmed live) -- only each individual *market* does
(`market["last_update"]`, confirmed present). `odds_api.py`'s `GameOdds.bookmaker_last_update`
reads `dk.get("last_update")` on the *different* `/sports/.../odds` (all-events) endpoint; this
module reads `market["last_update"]` per market instead, since that's what's actually present here.

## Real finding from the first live pull against an actual DK anchor pool (2026-09-28, week 3)

`player_anytime_td` includes **team defenses**, not just offensive skill players -- confirmed
live: `{"name": "Yes", "description": "Philadelphia Eagles D/ST", "price": 550}` and the Chicago
equivalent, both real outcomes on the one live week-3 event pulled this session (Eagles @ Bears).
DK's own draftables list that same defense as `SourcePlayer(name="Eagles", team="PHI",
position="DST", ...)` -- no name-normalization bridges `"philadelphia eagles dst"` and `"eagles"`,
so a plain name-only match would permanently misreport these two real, resolvable rows as
`UNRESOLVED`. `match_prop_to_dk_player` special-cases the literal `" D/ST"` suffix (confirmed
live, both rows end in exactly that string) and resolves by team abbreviation + `position ==
"DST"` instead -- the same reasoning `normalization/matcher.py`'s own `_resolve_dst` already uses
for exactly this reason (a team defense isn't a person, name-matching it is a category error).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from enum import Enum

import pandas as pd

from nfl_dfs.ingestion.odds_api import DK_BOOKMAKER_KEY, team_abbr
from nfl_dfs.normalization.matcher import SourcePlayer
from nfl_dfs.normalization.name_utils import normalize_name

EVENTS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events"
EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/{event_id}/odds"
_TIMEOUT = 20.0

# The real DFS-relevant subset of the 18 markets DraftKings is confirmed (this session) to offer
# for NFL player props. Chosen against DK Classic's actual scoring table (docs/PRD.md Section 3,
# "DK Classic scoring rules") -- one market per scored offensive-skill-position category, plus
# `player_anytime_td` for the single highest-value scoring event (+6, any TD type) as an
# independent cross-check on the yardage lines' own implied paths to the end zone.
#
# Deliberately EXCLUDED, with reasons (Chris's "use your own judgment on the real subset" framing):
#   - player_pass_attempts / player_pass_completions / player_rush_attempts: volume proxies, not
#     themselves a DK-scored category -- the yardage/TD lines below already price the outcome that
#     actually matters; adding these would be redundant signal for real credit cost, not new
#     information about DK points.
#   - player_rush_longest / player_reception_longest: not a DK scoring input at all (DK scores
#     total yards and yardage-threshold bonuses, never a single longest play).
#   - player_1st_td / player_last_td: strictly narrower information than player_anytime_td (a
#     player who scores 2+ TDs can be neither first nor last scorer on his own team) -- redundant
#     given anytime_td is already included, not worth the extra credits for this project's scoring.
#   - player_kicking_points / player_field_goals / player_sacks / player_tackles_assists: kicker/
#     DST/IDP-facing. This project is DK Classic OFFENSE + DST (PRD Section 2); DST is already
#     projected from settled box-score pbp (`ingestion/dst_actual_scoring.py`), not player props,
#     and there is no IDP contest format in scope at all.
DEFAULT_PROP_MARKETS: tuple[str, ...] = (
    "player_pass_yds",
    "player_pass_tds",
    "player_pass_interceptions",
    "player_rush_yds",
    "player_receptions",
    "player_reception_yds",
    "player_anytime_td",
)


def american_to_implied_probability(odds: int) -> float:
    """Standard, disclosed conversion (not invented here) -- positive American odds:
    `100 / (odds + 100)`; negative: `-odds / (-odds + 100)`. Raises on `odds == 0`, which is not a
    real American-odds value (no sportsbook prices a true coin flip as `0`; that's `+100`/`-100`)."""
    if odds == 0:
        raise ValueError("0 is not a valid American odds value")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


@dataclass(frozen=True)
class PlayerProp:
    """One DraftKings player-prop line, one row per (event, market, player) -- both sides of the
    market are kept (Chris: "the odds themselves ... are the point"), never collapsed to a single
    number. `point` and `under_price`/`under_prob` are `None` for a single-sided market
    (`player_anytime_td` today) -- see module docstring on the two real outcome shapes."""

    event_id: str
    market: str
    player_name: str  # raw, as returned in the outcome's "description" field -- not yet
    # normalized/matched; see `match_prop_to_dk_player` for the matching step.
    point: float | None
    over_price: int | None  # for a single-sided market, this is that market's one priced side
    # ("Yes" for player_anytime_td), not literally an "Over" -- see module docstring.
    under_price: int | None
    over_prob: float | None
    under_prob: float | None
    last_update: str | None  # this *market's* own last_update (see module docstring on why this
    # is read per-market, not per-bookmaker, on this endpoint).


def parse_dk_player_props(payload: dict, markets: tuple[str, ...] = DEFAULT_PROP_MARKETS) -> list[PlayerProp]:
    """Pure parse of one `/events/{id}/odds` response into `PlayerProp` rows, DraftKings only.
    Warns (never raises) on: no DK bookmaker in the response at all; a requested market DK simply
    didn't return for this event (real and expected -- not every market has action on every
    player every week); an outcome with no `"description"` (can't identify the player); an
    Over/Under-shaped market missing one of its two sides; or Over/Under disagreeing on `point`
    (a real parse anomaly, not something to silently pick a side on) -- each is a per-row skip, not
    a whole-response failure."""
    event_id = payload.get("id", "<unknown event id>")
    dk = next((b for b in payload.get("bookmakers", []) if b.get("key") == DK_BOOKMAKER_KEY), None)
    if dk is None:
        warnings.warn(f"no DraftKings bookmaker in props response for event {event_id}; skipping", stacklevel=2)
        return []

    dk_markets = dk.get("markets", [])
    available = {m["key"] for m in dk_markets}
    missing = set(markets) - available
    if missing:
        warnings.warn(
            f"DraftKings did not return market(s) {sorted(missing)} for event {event_id} -- not "
            f"every requested market has action on every event; this is expected, not necessarily an error",
            stacklevel=2,
        )

    props: list[PlayerProp] = []
    for market in dk_markets:
        market_key = market.get("key")
        if market_key not in markets:
            continue
        by_player: dict[str, dict[str, dict]] = {}
        for outcome in market.get("outcomes", []):
            player_name = outcome.get("description")
            if not player_name:
                warnings.warn(
                    f"{market_key} outcome in event {event_id} has no player name "
                    f"(\"description\") -- skipping: {outcome}",
                    stacklevel=2,
                )
                continue
            by_player.setdefault(player_name, {})[outcome.get("name")] = outcome

        for player_name, sides in by_player.items():
            if "Over" in sides or "Under" in sides:
                over, under = sides.get("Over"), sides.get("Under")
                if over is None or under is None:
                    warnings.warn(
                        f"{market_key} for {player_name!r} in event {event_id} is missing one "
                        f"side (have {sorted(sides)}, need both Over and Under) -- skipping",
                        stacklevel=2,
                    )
                    continue
                if over.get("point") != under.get("point"):
                    warnings.warn(
                        f"{market_key} for {player_name!r} in event {event_id}: Over point "
                        f"({over.get('point')}) != Under point ({under.get('point')}) -- skipping "
                        f"rather than guessing which is correct",
                        stacklevel=2,
                    )
                    continue
                props.append(
                    PlayerProp(
                        event_id=event_id,
                        market=market_key,
                        player_name=player_name,
                        point=over.get("point"),
                        over_price=over["price"],
                        under_price=under["price"],
                        over_prob=american_to_implied_probability(over["price"]),
                        under_prob=american_to_implied_probability(under["price"]),
                        last_update=market.get("last_update"),
                    )
                )
            elif "Yes" in sides:
                yes = sides["Yes"]
                props.append(
                    PlayerProp(
                        event_id=event_id,
                        market=market_key,
                        player_name=player_name,
                        point=None,
                        over_price=yes["price"],
                        under_price=None,
                        over_prob=american_to_implied_probability(yes["price"]),
                        under_prob=None,
                        last_update=market.get("last_update"),
                    )
                )
            else:
                warnings.warn(
                    f"{market_key} for {player_name!r} in event {event_id} has unrecognized "
                    f"outcome side(s) {sorted(sides)} -- neither Over/Under nor a single Yes side; skipping",
                    stacklevel=2,
                )
    return props


class PropMatchMethod(Enum):
    """How a prop's raw `player_name` was resolved to a DraftKings player this week.

    Deliberately a SEPARATE, narrower enum from `normalization/identity.py`'s `MatchMethod`, not a
    reuse of it -- that enum's `NAME_TEAM_POSITION` asserts a position match too, and the Odds
    API's player-props payload carries no position field for the player at all (confirmed live:
    an outcome is only `{name, description, price, [point]}` -- no team, no position). Claiming
    `NAME_TEAM_POSITION` here would assert an input this module never actually received. What IS
    available and used: the player's team is constrained to the event's own two teams (a prop for
    a player in this event is definitionally for a player on one of its two rosters -- not
    fabricated, just the event context itself), narrowing the DK pool before the name comparison.
    """

    NAME_TEAM = "name_team"  # normalized name matched exactly one DK player restricted to the
    # event's two teams.
    TEAM_DST = "team_dst"  # a "<Full Franchise Name> D/ST" entry (see module docstring's live
    # finding) resolved by team abbreviation + position="DST", not by name comparison at all --
    # DK's own DST rows are named just the mascot ("Bears"), never "Chicago Bears D/ST", so no
    # name-normalization would ever bridge these two strings; mirrors `matcher.py`'s own
    # `_resolve_dst` team-abbreviation-only approach for the same underlying reason (a team
    # defense isn't a person and has no meaningful "name match").
    UNRESOLVED = "unresolved"  # no DK player on either team of this event has this normalized name.
    AMBIGUOUS = "ambiguous"  # 2+ DK players on the event's two teams share this normalized name --
    # never auto-picked, same "surface for QA" posture as identity.py's own AMBIGUOUS.


@dataclass(frozen=True)
class MatchedPlayerProp:
    """A `PlayerProp` plus its DraftKings-anchor match. `dk_native_id` is DK's own `playerDkId`
    (`SourcePlayer.native_id`) -- the anchor ID, per ADR-0013 decision 3 -- NOT this project's
    full `PlayerIdentity.canonical_id` (gsis_id / local UUID). Resolving from `dk_native_id` to a
    full canonical_id requires that week's already-computed `PlayerIdentity` table
    (`normalization/matcher.reconcile_week`, which needs the nflverse crosswalk) -- out of scope
    for this module; a caller that needs the full canonical_id should join this output against
    that table on `dk_native_id`."""

    prop: PlayerProp
    home_team: str  # canonical DK abbreviation
    away_team: str
    dk_native_id: str | None
    dk_team: str | None
    dk_position: str | None
    match_method: PropMatchMethod
    match_note: str | None = None


_DST_SUFFIX = " D/ST"


def _match_dst_prop(
    prop: PlayerProp,
    home_team: str,
    away_team: str,
    dk_pool: list[SourcePlayer],
) -> MatchedPlayerProp:
    full_team_name = prop.player_name[: -len(_DST_SUFFIX)]
    dst_abbr = team_abbr(full_team_name)
    if dst_abbr is None:
        return MatchedPlayerProp(
            prop=prop,
            home_team=home_team,
            away_team=away_team,
            dk_native_id=None,
            dk_team=None,
            dk_position=None,
            match_method=PropMatchMethod.UNRESOLVED,
            match_note=(
                f"{prop.player_name!r} looked like a D/ST entry but {full_team_name!r} is not a "
                f"recognized team name (TEAM_NAME_TO_ABBR)"
            ),
        )
    candidates = [p for p in dk_pool if p.team == dst_abbr and p.position == "DST"]
    if len(candidates) != 1:
        return MatchedPlayerProp(
            prop=prop,
            home_team=home_team,
            away_team=away_team,
            dk_native_id=None,
            dk_team=None,
            dk_position=None,
            match_method=PropMatchMethod.UNRESOLVED,
            match_note=(
                f"{len(candidates)} DraftKings DST rows found for team {dst_abbr} "
                f"(expected exactly 1) while resolving {prop.player_name!r}"
            ),
        )
    match = candidates[0]
    return MatchedPlayerProp(
        prop=prop,
        home_team=home_team,
        away_team=away_team,
        dk_native_id=match.native_id,
        dk_team=match.team,
        dk_position=match.position,
        match_method=PropMatchMethod.TEAM_DST,
    )


def match_prop_to_dk_player(
    prop: PlayerProp,
    home_team: str,
    away_team: str,
    dk_pool: list[SourcePlayer],
) -> MatchedPlayerProp:
    """Reuses `normalization/name_utils.normalize_name` -- the same normalization primitive
    `normalization/matcher.py`'s own fallback matcher uses -- rather than inventing a second
    name-comparison routine. Candidate pool is narrowed to DK players on the event's home/away
    team before comparing names, which is the one piece of "team" context this payload actually
    supports (see `PropMatchMethod`'s docstring for why this stops short of a full
    name+team+position match).

    A `"<Full Franchise Name> D/ST"` entry (confirmed live on `player_anytime_td` -- see module
    docstring) is special-cased BEFORE the name-based path: resolved by team abbreviation +
    `position == "DST"`, since no amount of name-normalization bridges DK's own `"Eagles"` DST row
    against `"philadelphia eagles dst"`."""
    if prop.player_name.endswith(_DST_SUFFIX):
        return _match_dst_prop(prop, home_team, away_team, dk_pool)

    norm_name = normalize_name(prop.player_name)
    candidates = [
        p for p in dk_pool if p.team in (home_team, away_team) and normalize_name(p.name) == norm_name
    ]
    if not candidates:
        return MatchedPlayerProp(
            prop=prop,
            home_team=home_team,
            away_team=away_team,
            dk_native_id=None,
            dk_team=None,
            dk_position=None,
            match_method=PropMatchMethod.UNRESOLVED,
            match_note=(
                f"no DraftKings player named {prop.player_name!r} found on {home_team} or {away_team} "
                f"this week"
            ),
        )
    if len(candidates) > 1:
        return MatchedPlayerProp(
            prop=prop,
            home_team=home_team,
            away_team=away_team,
            dk_native_id=None,
            dk_team=None,
            dk_position=None,
            match_method=PropMatchMethod.AMBIGUOUS,
            match_note=(
                f"name_collision: {len(candidates)} DraftKings players named {prop.player_name!r} "
                f"on {home_team}/{away_team} ({[(c.native_id, c.position) for c in candidates]}) -- "
                f"never auto-picked, needs QA review"
            ),
        )
    match = candidates[0]
    return MatchedPlayerProp(
        prop=prop,
        home_team=home_team,
        away_team=away_team,
        dk_native_id=match.native_id,
        dk_team=match.team,
        dk_position=match.position,
        match_method=PropMatchMethod.NAME_TEAM,
    )


def events_in_week(
    events: list[dict],
    week_map: dict[tuple[str, str, int], int],
    season: int,
    target_week: int,
) -> list[dict]:
    """Filters the (free) `/events` pull down to just the events belonging to `target_week`, per
    nflverse's own schedule (`odds_api.py`'s `schedule_week_map`, reused not reimplemented) -- so
    the paid per-event props pull only ever runs for the week actually being built, not every
    upcoming event The Odds API happens to have listed. An event whose team names aren't in
    `TEAM_NAME_TO_ABBR`, or whose `(away, home, season)` isn't in `week_map` (preseason/exhibition,
    or a schedule pulled for the wrong season), is dropped with a warning, same posture as
    `odds_api.py`'s `implied_totals_long`."""
    out = []
    for event in events:
        home_abbr = team_abbr(event.get("home_team"))
        away_abbr = team_abbr(event.get("away_team"))
        if home_abbr is None or away_abbr is None:
            warnings.warn(
                f"unrecognized team name(s) in props event -- home={event.get('home_team')!r} "
                f"away={event.get('away_team')!r} not in TEAM_NAME_TO_ABBR; skipping this event",
                stacklevel=2,
            )
            continue
        week = week_map.get((away_abbr, home_abbr, season))
        if week != target_week:
            continue
        out.append(event)
    return out


def player_props_to_dataframe(matched: list[MatchedPlayerProp]) -> pd.DataFrame:
    """Flattens `MatchedPlayerProp` rows into the DataFrame shape this project's other ingestion
    modules end on (`odds_api.py`'s `implied_totals_long`/`fetch_dk_implied_totals` convention)."""
    rows = [
        {
            "event_id": m.prop.event_id,
            "home_team": m.home_team,
            "away_team": m.away_team,
            "market": m.prop.market,
            "player_name": m.prop.player_name,
            "dk_native_id": m.dk_native_id,
            "dk_team": m.dk_team,
            "dk_position": m.dk_position,
            "match_method": m.match_method.value,
            "match_note": m.match_note,
            "point": m.prop.point,
            "over_price": m.prop.over_price,
            "under_price": m.prop.under_price,
            "over_prob": m.prop.over_prob,
            "under_prob": m.prop.under_prob,
            "last_update": m.prop.last_update,
        }
        for m in matched
    ]
    columns = [
        "event_id", "home_team", "away_team", "market", "player_name", "dk_native_id", "dk_team",
        "dk_position", "match_method", "match_note", "point", "over_price", "under_price",
        "over_prob", "under_prob", "last_update",
    ]
    return pd.DataFrame(rows, columns=columns)


def fetch_dk_player_props_for_week(
    season: int,
    target_week: int,
    dk_pool: list[SourcePlayer],
    markets: tuple[str, ...] = DEFAULT_PROP_MARKETS,
    *,
    session=None,
) -> pd.DataFrame:
    """Live pull + full pipeline: the free `/events` list -> filtered to `target_week` via
    nflverse's schedule -> one paid `/events/{id}/odds` pull per event in that week (real cost:
    `len(markets) * len(events_this_week)` credits, per the confirmed cost model) -> DK-only
    parse -> matched against `dk_pool` (that week's `SourcePlayer` list, e.g.
    `ingestion.draftkings.parse_draftables`'s output) -> one flat DataFrame.

    `dk_pool` is a required parameter, not fetched internally -- the caller almost always already
    has that week's DK draftables pulled for the main projection pipeline, and re-fetching it here
    would just be a second live DK call for data the caller already has."""
    import nfl_data_py as nfl  # deferred import, same rationale as odds_api.py/nflverse.py
    import requests

    from nfl_dfs.config import config

    if not config.odds_api_key:
        raise RuntimeError("ODDS_API_KEY is not configured")
    http = session or requests

    events_response = http.get(EVENTS_URL, params={"apiKey": config.odds_api_key}, timeout=_TIMEOUT)
    events_response.raise_for_status()
    all_events = events_response.json()

    schedule = nfl.import_schedules([season])
    from nfl_dfs.ingestion.odds_api import schedule_week_map

    week_map = schedule_week_map(schedule)
    this_weeks_events = events_in_week(all_events, week_map, season, target_week)

    matched: list[MatchedPlayerProp] = []
    for event in this_weeks_events:
        home_abbr = team_abbr(event["home_team"])
        away_abbr = team_abbr(event["away_team"])
        odds_response = http.get(
            EVENT_ODDS_URL.format(event_id=event["id"]),
            params={
                "apiKey": config.odds_api_key,
                "regions": "us",
                "markets": ",".join(markets),
                "oddsFormat": "american",
            },
            timeout=_TIMEOUT,
        )
        odds_response.raise_for_status()
        props = parse_dk_player_props(odds_response.json(), markets)
        matched.extend(match_prop_to_dk_player(p, home_abbr, away_abbr, dk_pool) for p in props)

    return player_props_to_dataframe(matched)
