"""RotoGrinders "ResultsDB" contest-history ingestion (PRD Section 11 item 4; ADR-0023).

**What this actually is (ADR-0023, `docs/adr/0023-resultsdb-contest-history.md`):** `resultsdb/nfl` on
rotogrinders.com is a thin RotoGrinders-branded wrapper around a FantasyLabs "Contests Dashboard" app
(`terminal.fantasylabs.com/contests?brand=rotogrinders&sportid=1&date=<date>`) -- confirmed live via the
page's own `<iframe src=...>`. It is per-*contest* post-contest field data (real settled DK GPPs: player
ownership/actuals, entrant rosters/ROI), not a lineup-projection archive and not an aggregate across a user's
own contest history.

**Real finding beyond the MLB sister project's own ResultsDB work:** the MLB build (see
`~/Developer/mlb-dfs-optimizer/mass-multi/claude-code-kickoff-resultsdb-scraper.md`) only ever DOM/React-fiber
-scraped this UI via Chrome MCP. Live investigation this pass found the public JSON API underneath it instead
-- confirmed with a bare `curl`/`requests` call, **no RotoGrinders session cookie required at all** (unlike
every other reverse-engineered source in this codebase):

    GET https://service.fantasylabs.com/contest-sources/?sport_id=1&date=<YYYY-MM-DD>
    GET https://service.fantasylabs.com/live-contests/?sport=NFL&contest_group_id=<id>
    GET https://dh5nxc6yx3kwy.cloudfront.net/contests/nfl/<YYYYMMDD>/<contest_id>/data/

The one real auth-shaped gotcha (ADR-0023 section 2): `service.fantasylabs.com` 403s Python's default
`requests` User-Agent (basic bot-filtering, not session auth) -- same fix already used for RotoGrinders
LineupHQ/Situation Room, a browser-shaped `User-Agent` header.

**Coverage, live-verified (ADR-0023 section 3):** the `data/` payload is populated for the 2020 season
through today; every 2017-2019 contest tried 403s. Six real seasons (2020-2025, plus in-progress 2026), not
"however far RotoGrinders' UI happens to render."

**Scope of this module (ADR-0023 "What was built this pass"):** contest discovery (`contest-sources` ->
`live-contests` -> pick the `is_primary` flagship contest, e.g. the Millionaire/MEGA Millionaire PRD Section 2
already names) and the player-exposure/actuals + user-exposure halves of the `data/` payload.

**`lineups/` endpoint (ADR-0031, added this pass):** per-distinct-roster dup counts, `lineupTrends`,
team/game-stack tiers -- the literal dup-risk data the earlier ADR-0023/0025/0026 rounds all deliberately
scoped out as follow-up. Confirmed live shape (2020-09-20, contest 91962454, 244,757 distinct lineups):
`{"lineups": {<lineupHash>: {...}}}` -- a DICT keyed by `lineupHash`, not a list (ADR-0023's original section
2 description implied a flat row list; the real payload nests one level deeper). Real fields beyond what
ADR-0023 originally named: `favoriteCt`, `underdogCt`, `homeCt`, `visitorCt`, `correlatedPlayers` (all real,
confirmed live, not previously documented). **Real volume finding, not a guess:** this single contest alone
has 244,757 distinct lineups (97.8% with `lineupCt == 1`, a long tail up to `lineupCt == 161`) -- a full
71-contest historical backfill of every lineup row would be a genuinely large pull (plausibly several million
rows total), a real storage/scope decision this pass deliberately did NOT make unilaterally -- see
`docs/adr/0031-injury-report-staleness-check.md`'s sibling ADR for the lineups-specific one,
`docs/adr/0032-resultsdb-lineups-dup-risk.md`, for how that was resolved.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

CONTEST_SOURCES_URL = "https://service.fantasylabs.com/contest-sources/"
LIVE_CONTESTS_URL = "https://service.fantasylabs.com/live-contests/"
CONTEST_DATA_URL_TEMPLATE = "https://dh5nxc6yx3kwy.cloudfront.net/contests/nfl/{yyyymmdd}/{contest_id}/data/"
LINEUPS_URL_TEMPLATE = "https://dh5nxc6yx3kwy.cloudfront.net/contests/nfl/{yyyymmdd}/{contest_id}/lineups/"

SPORT_ID_NFL = 1

_TIMEOUT = 20.0
# service.fantasylabs.com 403s Python's default `python-requests/x.y` User-Agent -- confirmed live to be
# basic bot-filtering (curl and a browser-shaped UA both succeed), not session auth. See module docstring.
_HEADERS = {"User-Agent": "Mozilla/5.0"}


class NoPrimaryContestError(RuntimeError):
    """Raised when a draft group's live contests don't resolve to exactly one contest matching the
    selection policy (see `select_millionaire_maker_contest`) -- never guess between candidates or fall
    back to "biggest by entry count" (confirmed live, ADR-0023, to be a *different* contest from the
    flagship one in every season checked), same "fail loudly, name every candidate" convention as
    `draftkings.py`'s `SlateSelectionError`.
    """


class ContestDataUnavailableError(RuntimeError):
    """Raised when the CloudFront `data/` payload for a given date/contest returns a non-200. Confirmed
    live (ADR-0023) that an unknown/pre-2020 contest_id returns a clean S3 `AccessDenied` 403 -- but that
    response is NOT distinguishable from "this date/contest genuinely isn't covered" (no 404-vs-403
    semantic split), so this error deliberately doesn't claim to know which case it is.
    """


# ---------------------------------------------------------------------------
# contest-sources: which DK draft groups are live for a date
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DraftGroupSource:
    """One DK draft group live for a given date, per the `contest-sources` response."""

    contest_group_id: int
    contest_start_date: str  # raw ISO8601 string from the payload, unmodified
    game_count: int
    contest_suffix: str  # e.g. "", "(Early Only)", "(Afternoon Only)" -- same DK slate-labeling pattern
    # `draftkings.py`'s `_extract_slate_label` already handles for the `getcontests` side of DK's API.


def parse_dk_draft_groups(payload: dict) -> list[DraftGroupSource]:
    """Pure parse of a `contest-sources` response, filtered to the `"dk"` (DraftKings) source only --
    the payload also carries `"draftkings.com showdown"` and other non-Classic sources this project
    doesn't use.
    """
    sources = payload.get("contest-sources")
    if sources is None:
        raise ValueError(f"contest-sources response missing expected key 'contest-sources': {sorted(payload)}")

    groups: list[DraftGroupSource] = []
    for source in sources:
        if source.get("short_name") != "dk":
            continue
        for group in source.get("draft_groups", []):
            groups.append(
                DraftGroupSource(
                    contest_group_id=group["id"],
                    contest_start_date=group["contest_start_date"],
                    game_count=group["game_count"],
                    contest_suffix=group.get("contest_suffix") or "",
                )
            )
    return groups


def fetch_contest_sources(
    date: str, *, sport_id: int = SPORT_ID_NFL, session: requests.Session | None = None
) -> list[DraftGroupSource]:
    """Live call. `date` is `YYYY-MM-DD`. No auth needed (ADR-0023) -- `session` exists for test
    injection only.
    """
    http = session or requests
    response = http.get(
        CONTEST_SOURCES_URL, headers=_HEADERS, params={"sport_id": sport_id, "date": date}, timeout=_TIMEOUT
    )
    response.raise_for_status()
    return parse_dk_draft_groups(response.json())


# ---------------------------------------------------------------------------
# live-contests: which actual DK contests exist within a draft group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveContest:
    """One real DK contest within a draft group, per the `live-contests` response."""

    contest_id: int
    contest_name: str
    contest_size: int
    entry_cost: float
    total_prizes: float
    multi_entry_max: int
    is_primary: bool
    is_largest_by_size: bool
    cash_line: int


def parse_live_contests(payload: dict) -> list[LiveContest]:
    """Pure parse of a `live-contests` response."""
    contests = payload.get("live_contests")
    if contests is None:
        raise ValueError(f"live-contests response missing expected key 'live_contests': {sorted(payload)}")
    return [
        LiveContest(
            contest_id=c["contest_id"],
            contest_name=c["contest_name"],
            contest_size=c["contest_size"],
            entry_cost=c["entry_cost"],
            total_prizes=c["total_prizes"],
            multi_entry_max=c["multi_entry_max"],
            is_primary=c["is_primary"],
            is_largest_by_size=c["is_largest_by_size"],
            cash_line=c["cash_line"],
        )
        for c in contests
    ]


def select_millionaire_maker_contest(contests: list[LiveContest]) -> LiveContest:
    """Picks the specific contest PRD Section 2 means by "the Millionaire Maker (150-max entries per
    user, massive overall field)" -- **not** simply "the `is_primary` contest".

    **Real, live-confirmed finding (ADR-0023) that reshaped this function:** a single draft group can
    carry *more than one* `is_primary` contest at once -- a real 2023-09-10 pull found both a $100-entry,
    28,029-entry "$2.5M Fantasy Football Millionaire" (`multi_entry_max=150`) AND a $4,444-entry,
    768-entry "$3M MEGA Millionaire" (`multi_entry_max=23`) both flagged `is_primary=True` and both
    flagged `is_largest_by_size=True` on the same date -- a genuinely ambiguous pair of fields whose exact
    semantics (largest/primary *within what bracket?*) this project has not fully reverse-engineered.
    Rather than guess at that semantics, this function keys directly off the one field PRD Section 2
    itself states literally -- `multi_entry_max == 150` -- among the `is_primary` candidates, which
    unambiguously picked the real mass-market Millionaire Maker over the high-roller "MEGA Millionaire" in
    the confirmed live case above.
    """
    primaries = [c for c in contests if c.is_primary]
    matches = [c for c in primaries if c.multi_entry_max == 150]
    if len(matches) == 1:
        return matches[0]
    named = "; ".join(
        f"{c.contest_id} {c.contest_name!r} (primary={c.is_primary}, multi_entry_max={c.multi_entry_max})"
        for c in contests
    )
    raise NoPrimaryContestError(
        f"expected exactly one is_primary contest with multi_entry_max=150 (the Millionaire Maker, PRD "
        f"Section 2), found {len(matches)}. Candidates: {named}"
    )


def fetch_live_contests(contest_group_id: int, *, session: requests.Session | None = None) -> list[LiveContest]:
    """Live call. No auth needed (ADR-0023)."""
    http = session or requests
    response = http.get(
        LIVE_CONTESTS_URL, headers=_HEADERS, params={"sport": "NFL", "contest_group_id": contest_group_id}, timeout=_TIMEOUT
    )
    response.raise_for_status()
    return parse_live_contests(response.json())


# ---------------------------------------------------------------------------
# contest data: the real per-contest payload (player exposures/actuals, user exposures)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContestSummary:
    contest_id: int
    contest_name: str
    contest_date: str  # raw ISO8601 string, unmodified
    entry_cost: float
    contest_size: int
    cash_line: int
    duplicate_lineups: int
    unique_lineups: int
    total_prizes: float


def parse_contest_summary(payload: dict) -> ContestSummary:
    c = payload["contest"]
    return ContestSummary(
        contest_id=c["contestId"],
        contest_name=c["contestName"],
        contest_date=c["contestDate"],
        entry_cost=c["entryCost"],
        contest_size=c["contestSize"],
        cash_line=c["cashLine"],
        duplicate_lineups=c["duplicateLineups"],
        unique_lineups=c["uniqueLineups"],
        total_prizes=c["totalPrizes"],
    )


@dataclass(frozen=True)
class PlayerExposureRow:
    """One rostered player's real post-contest ownership + actual result, for one specific DK contest.

    `player_key` is the payload's own `"<playerId>:<rosterSlot>"` composite key (e.g. a player eligible
    at multiple roster slots gets one row per slot, same shape DK's own `draftables` payload has --
    `draftkings.py`'s `parse_draftables` docstring notes the identical pattern there).

    The `ownership_top20`/`ownership_top10`/`ownership_top1` fields default to `0.0`, not `None` --
    confirmed live (ADR-0023) that a player absent from a percentile tier's `exposureCounts` genuinely had
    0% ownership in that tier, not a missing/undefined value (unlike the MLB sister project's own finding
    that pre-cutoff-era contests could lack the tier column entirely -- a different case this ADR did not
    find evidence of within the confirmed 2020+ coverage window).
    """

    player_key: str
    player_id: int
    full_name: str
    position: str
    team: str
    salary: int
    projected_points: float | None
    actual_points: float | None
    stat_details: str
    made_cut: int
    ownership_overall: float
    ownership_top20: float = 0.0
    ownership_top10: float = 0.0
    ownership_top1: float = 0.0


def parse_player_exposures(payload: dict) -> list[PlayerExposureRow]:
    """Parses the `players` + `exposures` halves of a contest `data/` payload and merges them into one
    row per player. These are NOT already merged in the raw payload (ADR-0023 section 2) -- the AG-Grid UI
    does this merge client-side; `players` carries identity/salary/actuals/overall ownership only, while
    the top-20%/10%/1% breakdown lives in the separate `exposures["20"|"10"|"1"].exposureCounts`,
    keyed the same `player_key` way.
    """
    players = payload.get("players")
    if players is None:
        raise ValueError(f"contest data payload missing expected key 'players': {sorted(payload)}")
    exposures = payload.get("exposures", {})

    tier20 = exposures.get("20", {}).get("exposureCounts", {})
    tier10 = exposures.get("10", {}).get("exposureCounts", {})
    tier1 = exposures.get("1", {}).get("exposureCounts", {})

    rows = []
    for player_key, row in players.items():
        rows.append(
            PlayerExposureRow(
                player_key=player_key,
                player_id=row["playerId"],
                full_name=row["fullName"],
                position=row["position"],
                team=row["currentTeam"],
                salary=row["salary"],
                projected_points=row.get("projPoints"),
                actual_points=row.get("actualPoints"),
                stat_details=row.get("statDetails") or "",
                made_cut=row["madeCut"],
                ownership_overall=row["ownership"],
                ownership_top20=tier20.get(player_key, {}).get("exposurePerc", 0.0),
                ownership_top10=tier10.get(player_key, {}).get("exposurePerc", 0.0),
                ownership_top1=tier1.get(player_key, {}).get("exposurePerc", 0.0),
            )
        )
    return rows


@dataclass(frozen=True)
class UserExposureRow:
    """One real entrant's roster-count/exposure/ROI summary for one specific DK contest -- the "Contest
    Users" data Chris's own exploratory check surfaced (real usernames with roster/unique/player counts).
    """

    username: str
    total_rosters: int
    unique_rosters: int
    total_players: int
    max_exposure: float
    roi: float


def parse_user_exposures(payload: dict) -> list[UserExposureRow]:
    users = payload.get("users")
    if users is None:
        raise ValueError(f"contest data payload missing expected key 'users': {sorted(payload)}")
    return [
        UserExposureRow(
            username=username,
            total_rosters=row["totalRosters"],
            unique_rosters=row["uniqueRosters"],
            total_players=row["totalPlayers"],
            max_exposure=row["maxExposure"],
            roi=row["roi"],
        )
        for username, row in users.items()
    ]


def fetch_contest_data(date: str, contest_id: int, *, session: requests.Session | None = None) -> dict:
    """Live call. `date` is `YYYY-MM-DD`; internally reshaped to DK's `YYYYMMDD` path segment. No auth
    needed (ADR-0023) -- gzip-encoded response, but `requests` decompresses it transparently, so
    `response.json()` just works with no manual `gunzip` step.

    Raises `ContestDataUnavailableError` on any non-200 -- confirmed live (ADR-0023) that this covers both
    "date/contest genuinely predates the 2020 archive floor" and "unknown contest_id", indistinguishably
    (a clean S3 `AccessDenied` XML body either way, no 404-vs-403 semantic split to key off).
    """
    http = session or requests
    yyyymmdd = date.replace("-", "")
    url = CONTEST_DATA_URL_TEMPLATE.format(yyyymmdd=yyyymmdd, contest_id=contest_id)
    response = http.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    if response.status_code != 200:
        raise ContestDataUnavailableError(
            f"ResultsDB contest data unavailable for date={date} contest_id={contest_id} "
            f"(status={response.status_code}) -- either genuinely uncovered (pre-2020 season, per "
            f"ADR-0023) or an unknown contest_id; this endpoint doesn't distinguish the two cases."
        )
    return response.json()


# ---------------------------------------------------------------------------
# lineups: one row per DISTINCT roster (not per entry) -- the real dup-count data, ADR-0031
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineupRow:
    """One distinct roster actually built by the field in one real DK contest. `lineup_ct` is the
    literal number of contest entries that used this exact roster -- the raw dup-risk observation
    this whole endpoint exists for; `lineup_ct == 1` means a genuinely unique roster, anything
    higher is a real, confirmed duplicate group. `lineup_user_ct` is the number of distinct
    entrants who used it at least once (can be lower than `lineup_ct` -- the same single user
    multi-entering the same roster more than once, common under a high `multi_entry_max`).

    `team_stacks`/`game_stacks` are kept as the raw payload's own nested shape (team-id/game-id ->
    list of `"<playerId>:<rosterSlot>"` strings) rather than reshaped here -- this is a pure-parse
    ingestion layer, same posture as `parse_player_exposures`/`parse_user_exposures` above.
    """

    lineup_hash: str
    lineup_ct: int
    lineup_user_ct: int
    lineup_players: dict[str, int]  # roster slot -> playerId, e.g. {"QB1": 26479, "RB1": 33090, ...}
    points: float
    total_salary: int
    total_own: float
    min_own: float
    max_own: float
    avg_own: float
    lineup_rank: int
    is_cashing: bool
    payout: float
    lineup_percentile: float
    favorite_ct: int
    underdog_ct: int
    home_ct: int
    visitor_ct: int
    correlated_players: int
    team_stacks: dict
    game_stacks: dict
    lineup_trends: dict[str, bool]
    entry_name_list: list[str]


def parse_lineups(payload: dict) -> list[LineupRow]:
    """Pure parse of a `lineups/` response. **Real shape, confirmed live (ADR-0031) --
    NOT a flat list**: `payload["lineups"]` is a DICT keyed by `lineupHash`, one entry per
    distinct roster. `lineupHash` itself is redundant with the dict key (both carry the same
    value in every row observed live) -- kept as its own field anyway rather than reconstructed
    from the key, so a `LineupRow` is a complete, self-describing record on its own.
    """
    lineups = payload.get("lineups")
    if lineups is None:
        raise ValueError(f"lineups response missing expected key 'lineups': {sorted(payload)}")
    rows = []
    for row in lineups.values():
        rows.append(
            LineupRow(
                lineup_hash=row["lineupHash"],
                lineup_ct=row["lineupCt"],
                lineup_user_ct=row["lineupUserCt"],
                lineup_players=row["lineupPlayers"],
                points=row["points"],
                total_salary=row["totalSalary"],
                total_own=row["totalOwn"],
                min_own=row["minOwn"],
                max_own=row["maxOwn"],
                avg_own=row["avgOwn"],
                lineup_rank=row["lineupRank"],
                is_cashing=row["isCashing"],
                payout=row["payout"],
                lineup_percentile=row["lineupPercentile"],
                favorite_ct=row.get("favoriteCt", 0),
                underdog_ct=row.get("underdogCt", 0),
                home_ct=row.get("homeCt", 0),
                visitor_ct=row.get("visitorCt", 0),
                correlated_players=row.get("correlatedPlayers", 0),
                team_stacks=row.get("teamStacks", {}),
                game_stacks=row.get("gameStacks", {}),
                lineup_trends=row.get("lineupTrends", {}),
                entry_name_list=row.get("entryNameList", []),
            )
        )
    return rows


def fetch_lineups(date: str, contest_id: int, *, session: requests.Session | None = None) -> list[LineupRow]:
    """Live call. Same URL/auth/error shape as `fetch_contest_data` -- no cookie needed, gzip
    handled transparently, a non-200 raises `ContestDataUnavailableError` (shared with
    `fetch_contest_data` rather than a parallel error type, since the failure semantics -- "either
    genuinely uncovered or an unknown contest_id, indistinguishably" -- are identical for both
    CloudFront endpoints)."""
    http = session or requests
    yyyymmdd = date.replace("-", "")
    url = LINEUPS_URL_TEMPLATE.format(yyyymmdd=yyyymmdd, contest_id=contest_id)
    response = http.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    if response.status_code != 200:
        raise ContestDataUnavailableError(
            f"ResultsDB lineups data unavailable for date={date} contest_id={contest_id} "
            f"(status={response.status_code}) -- either genuinely uncovered (pre-2020 season, per "
            f"ADR-0023) or an unknown contest_id; this endpoint doesn't distinguish the two cases."
        )
    return parse_lineups(response.json())
