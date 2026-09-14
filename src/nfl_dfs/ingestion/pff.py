"""PFF ingestion (PRD Section 4/5 step 1; Phase 0 + ADR-0013 confirmed live; grade-facet
population per ADR-0014).

Generalizes the live smoke-test pull against `/v1/facet/rushing/summary` performed while
building the matcher (ADR-0013) into real ingestion: pull the three facet endpoints that between
them cover every skill-position player DK rosters (passing -> QB, rushing -> RB/QB, receiving ->
WR/TE/RB), dedupe by `player_id` across facets, and shape into `SourcePlayer`.

PFF carries no DST-equivalent row on any facet endpoint (team defenses aren't "offense/defense
players" in PFF's schema, per position_aliases.py's existing note) -- confirmed again here, not
just assumed: this module only ever returns skill-position players, so PFF is expected to be
UNRESOLVED for every DST in the matcher's output, by design, not a bug.

Bearer auth via `config.pff_api_key`. Per Phase 0: an unentitled/view-only key gets `200` with
fields stripped and a `restricted` key, not a `403` -- `_check_entitlement` below is the
defensive check Phase 0 flagged as needed so that silent downgrade isn't mistaken for real data.

**MatchupContext grade facets (ADR-0014):** the section below (`GRADE_FACETS` on down) is a
separate pull for `MatchupContext`'s four PFF-grade inputs -- `offense/run_blocking`,
`offense/pass_blocking`, `defense/run`, `defense/coverage_scheme`. These are OL/DL/DB grades, a
different population from the skill-position `FACETS` pull above (which exists only to resolve
player identity for the matcher) and are never single-current-week snapshots: per ADR-0014, PFF
grades are trailing-by-construction (film-graded after plays happen), so `MatchupContext` needs
season-to-date cumulative grades through the last *completed* week, built as one API call per
endpoint with `week=1,2,...,W-1` (PFF aggregates server-side across that list -- confirmed live,
one row per player carrying a `player_game_count` field, not one row per week to merge
client-side). Week 1 has zero completed current-season weeks, so cumulative is undefined, not
just noisy -- ADR-0014's fallback for that case is that same player's own prior-season
(`season=2024,2025`) grade by `player_id`, or a league-average-by-position grade if no
prior-season record exists at all (e.g. a rookie).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests

from nfl_dfs.config import config
from nfl_dfs.normalization.matcher import SourcePlayer

BASE_URL = "https://api.pff.com"

# facet path -> the response's top-level list key. All three confirmed live to share the same
# per-row shape for the fields this module needs: player_id, player, team, position,
# jersey_number (ADR-0013's Gibbs/Gibbens jersey-number tiebreak path depends on this last one
# being populated, so it's carried through here even though matching itself lives in matcher.py).
FACETS: dict[str, str] = {
    "passing/summary": "passing_summary",
    "rushing/summary": "rushing_summary",
    "receiving/summary": "receiving_summary",
}

_TIMEOUT = 30.0
_REQUIRED_ROW_FIELDS = ("player_id", "player", "team", "position")


def _check_entitlement(payload: dict, facet: str) -> None:
    if "restricted" in payload:
        raise RuntimeError(
            f"PFF facet {facet!r} response contains a 'restricted' key -- entitlement downgrade "
            "suspected (200 OK with fields stripped, per Phase 0's finding), not a real empty "
            "result. Check PFF_API_KEY's tier via /v1/auth/whoami before trusting this pull."
        )


def parse_pff_facet(payload: dict, facet: str) -> list[SourcePlayer]:
    """Pure parse of one facet response into `SourcePlayer` rows."""
    response_key = FACETS[facet]
    if response_key not in payload:
        raise ValueError(f"PFF facet {facet!r} response missing expected key {response_key!r}: {sorted(payload)}")

    players = []
    for row in payload[response_key]:
        missing = [f for f in _REQUIRED_ROW_FIELDS if row.get(f) in (None, "")]
        if missing:
            raise ValueError(f"PFF facet {facet!r} row missing required field(s) {missing}: {row}")
        jersey = row.get("jersey_number")
        players.append(
            SourcePlayer(
                native_id=str(row["player_id"]),
                name=row["player"],
                team=row["team"],
                position=row["position"],
                jersey_number=str(jersey) if jersey not in (None, "") else None,
            )
        )
    return players


def fetch_pff_players(
    season: int, week: int, *, api_key: str | None = None, session: requests.Session | None = None
) -> list[SourcePlayer]:
    """Live pull across all facets in FACETS, deduped by `player_id` (first-seen wins -- a player
    can legitimately appear on more than one facet, e.g. a pass-catching RB on both rushing and
    receiving; PFF's own `player_id`/`player`/`team`/`position` for that player are consistent
    across facets on every spot check performed, so first-seen is not expected to lose data).
    """
    key = api_key or config.pff_api_key
    if not key:
        raise RuntimeError("PFF_API_KEY is not configured (nfl_dfs.config.config.pff_api_key)")
    http = session or requests
    headers = {"Authorization": f"Bearer {key}"}

    by_id: dict[str, SourcePlayer] = {}
    for facet in FACETS:
        response = http.get(
            f"{BASE_URL}/v1/facet/{facet}",
            headers=headers,
            params={"league": "nfl", "season": season, "week": week},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        _check_entitlement(payload, facet)
        for player in parse_pff_facet(payload, facet):
            by_id.setdefault(player.native_id, player)
    return list(by_id.values())


# --------------------------------------------------------------------------------------------
# MatchupContext grade facets (ADR-0014)
# --------------------------------------------------------------------------------------------

# facet path -> response envelope key. Confirmed live against both the OpenAPI spec's example
# payloads and real `/v1/facet/...` responses (2026 wk1, see ADR-0014) -- note `defense/run`'s
# envelope key is `run_defense_summary`, not `defense_run`; the other three follow the facet
# path's last segment.
GRADE_FACETS: dict[str, str] = {
    "offense/run_blocking": "run_blocking",
    "offense/pass_blocking": "pass_blocking",
    "defense/run": "run_defense_summary",
    "defense/coverage_scheme": "coverage_scheme",
    # ADR-0022 Round A additions -- both live-confirmed (this session, season=2025&week=1,2,3) to
    # match this exact facet-grade shape unchanged (metadata fields + a flat numeric `grades`
    # dict), so both reuse parse_pff_grade_facet/fetch_matchup_grades/resolve_grade as-is, no new
    # parsing code.
    #
    # "receiving/scheme" -- player-level man vs. zone performance split (69 fields total: parallel
    # man_*/zone_* pairs -- targets, targets_percent, yprr, grades_pass_route, epa, caught_percent,
    # avg_depth_of_target, etc). This is the "does Player X perform better against man or zone"
    # signal ADR-0022 confirmed was a real, previously-unfilled gap.
    "receiving/scheme": "receiving_scheme",
    # "defense/pass_rush" -- per-defender pass-rush grade/win-rate. Confirmed missing even though
    # MatchupContext's own PRD text (Section 6, pass-protection-vs-pass-rush row) names "PFF team
    # pass-rush win rate" as an input -- the offensive side (offense/pass_blocking) has been
    # ingested since ADR-0014, but this defensive side was never added. ADR-0022 flags this as a
    # real, previously-undocumented gap, not new scope.
    "defense/pass_rush": "pass_rush_summary",
}

# Row fields that identify/describe the player rather than grade the player -- excluded from
# `PffGradeRow.grades` so that dict is exactly "whatever grade/volume fields this facet carries,"
# generic across all four facets' differing field names (grades_run_block, pbe,
# man_grades_coverage_defense, grades_run_defense, ...) without hardcoding each one here.
_GRADE_METADATA_FIELDS = {
    "player_id",
    "player",
    "team",
    "team_name",
    "position",
    "jersey_number",
    "franchise_id",
    "draft_season",
    "eligible_season",
    "player_game_count",
}

# ADR-0014 section 2/3: week 1's prior-season fallback pulls these two seasons via the
# already-confirmed comma-separated `season` list syntax.
PRIOR_SEASONS: tuple[int, ...] = (2024, 2025)


@dataclass(frozen=True)
class PffGradeRow:
    """One player's row from a `MatchupContext` grade facet, already aggregated by PFF across
    whatever `week`/`season` list was requested (confirmed live, ADR-0014: one row per player
    carrying `player_game_count`, not one row per week/season to merge client-side).
    """

    native_id: str
    name: str
    team: str | None
    position: str
    jersey_number: str | None
    player_game_count: int | None
    grades: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PffFacetGrades:
    """Result of one grade-facet pull, shaped per ADR-0014's population rule.

    `population` is `"cumulative_through_last_completed_week"` for week >= 2 (the normal case --
    a single `week=1,...,W-1` call, PFF aggregates server-side) or `"prior_season_fallback"` for
    week 1 specifically (zero completed current-season weeks -- cumulative is undefined, not just
    noisy, so this pulls `season=2024,2025` instead, matched by PFF's own stable `player_id`).
    `league_average_by_position` is only populated for the fallback case -- it backstops a player
    with no prior-season PFF record at all (e.g. a rookie); see `resolve_grade`.
    """

    population: str
    weeks_requested: str | None
    seasons_requested: str
    by_player_id: dict[str, PffGradeRow]
    league_average_by_position: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedGrade:
    """One player's grade for a `MatchupContext` input, after ADR-0014's fallback chain has been
    applied by `resolve_grade`. `population` names which tier of the chain actually supplied
    `grades` (see `PffFacetGrades.population`, plus `"league_average_by_position"` and
    `"unmatched"` for the two additional outcomes `resolve_grade` can produce).
    """

    native_id: str
    position: str
    grades: dict[str, float]
    player_game_count: int | None
    population: str


def completed_weeks_param(current_week: int) -> str | None:
    """Build the `week=1,2,...,W-1` comma-separated list ADR-0014 specifies for the
    season-to-date cumulative pull -- generated programmatically since PFF's `week` parameter
    accepts only explicit comma-separated lists, no range/dash shorthand.

    Returns `None` for week 1: zero completed current-season weeks means cumulative is undefined,
    not just a noisy small sample -- callers should skip the current-season call entirely and use
    the prior-season fallback (`fetch_matchup_grades` does this automatically).
    """
    if current_week < 1:
        raise ValueError(f"current_week must be >= 1, got {current_week}")
    if current_week == 1:
        return None
    return ",".join(str(w) for w in range(1, current_week))


def parse_pff_grade_facet(payload: dict, facet: str) -> list[PffGradeRow]:
    """Pure parse of one grade-facet response into `PffGradeRow` rows. Mirrors `parse_pff_facet`
    above (same required-field/entitlement discipline) but keyed off `GRADE_FACETS` and carrying
    `player_game_count` plus a generic `grades` dict instead of the fixed skill-position shape.
    """
    response_key = GRADE_FACETS[facet]
    if response_key not in payload:
        raise ValueError(f"PFF grade facet {facet!r} response missing expected key {response_key!r}: {sorted(payload)}")

    rows = []
    for row in payload[response_key]:
        missing = [f for f in _REQUIRED_ROW_FIELDS if row.get(f) in (None, "")]
        if missing:
            raise ValueError(f"PFF grade facet {facet!r} row missing required field(s) {missing}: {row}")
        jersey = row.get("jersey_number")
        grades = {
            k: v
            for k, v in row.items()
            if k not in _GRADE_METADATA_FIELDS and isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        rows.append(
            PffGradeRow(
                native_id=str(row["player_id"]),
                name=row["player"],
                team=row.get("team"),
                position=row["position"],
                jersey_number=str(jersey) if jersey not in (None, "") else None,
                player_game_count=row.get("player_game_count"),
                grades=grades,
            )
        )
    return rows


def _league_average_by_position(rows: list[PffGradeRow]) -> dict[str, dict[str, float]]:
    """Position -> {grade field: mean value across every row at that position}. Only meaningful
    (and only computed by `fetch_matchup_grades`) for the week-1 prior-season pull -- this is the
    league-average-by-position backstop ADR-0014 specifies for a player with no prior-season PFF
    record at all (e.g. a rookie who has no `season=2024,2025` row to fall back to individually).
    """
    sums: dict[str, dict[str, float]] = {}
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        pos_sums = sums.setdefault(row.position, {})
        pos_counts = counts.setdefault(row.position, {})
        for grade_field, value in row.grades.items():
            pos_sums[grade_field] = pos_sums.get(grade_field, 0.0) + value
            pos_counts[grade_field] = pos_counts.get(grade_field, 0) + 1
    return {
        position: {
            grade_field: pos_sums[grade_field] / counts[position][grade_field] for grade_field in pos_sums
        }
        for position, pos_sums in sums.items()
    }


def _get_grade_facet_payload(
    facet: str,
    params: dict[str, object],
    *,
    api_key: str | None,
    session: requests.Session | None,
) -> dict:
    key = api_key or config.pff_api_key
    if not key:
        raise RuntimeError("PFF_API_KEY is not configured (nfl_dfs.config.config.pff_api_key)")
    http = session or requests
    headers = {"Authorization": f"Bearer {key}"}
    response = http.get(
        f"{BASE_URL}/v1/facet/{facet}",
        headers=headers,
        params={"league": "nfl", **params},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    _check_entitlement(payload, facet)
    return payload


def fetch_matchup_grades(
    facet: str,
    current_week: int,
    season: int,
    *,
    prior_seasons: tuple[int, ...] = PRIOR_SEASONS,
    api_key: str | None = None,
    session: requests.Session | None = None,
) -> PffFacetGrades:
    """One-call-per-endpoint pull of one of `MatchupContext`'s four trailing PFF grade inputs,
    per ADR-0014.

    Week >= 2: a single `week=1,...,W-1` call against `season` -- PFF aggregates server-side, so
    this is one row per player already covering every completed week, not one row per week to
    merge here.

    Week 1: the current-season call is skipped entirely (zero completed weeks -- cumulative is
    undefined) in favor of a `season=2024,2025` (`prior_seasons`) pull matched by `player_id`,
    plus a league-average-by-position table for players with no prior-season record at all (a
    rookie) -- see `resolve_grade`.
    """
    if facet not in GRADE_FACETS:
        raise ValueError(f"{facet!r} is not one of MatchupContext's grade facets: {sorted(GRADE_FACETS)}")

    weeks = completed_weeks_param(current_week)
    if weeks is not None:
        payload = _get_grade_facet_payload(facet, {"season": season, "week": weeks}, api_key=api_key, session=session)
        rows = parse_pff_grade_facet(payload, facet)
        return PffFacetGrades(
            population="cumulative_through_last_completed_week",
            weeks_requested=weeks,
            seasons_requested=str(season),
            by_player_id={row.native_id: row for row in rows},
        )

    season_list = ",".join(str(s) for s in prior_seasons)
    payload = _get_grade_facet_payload(facet, {"season": season_list}, api_key=api_key, session=session)
    rows = parse_pff_grade_facet(payload, facet)
    return PffFacetGrades(
        population="prior_season_fallback",
        weeks_requested=None,
        seasons_requested=season_list,
        by_player_id={row.native_id: row for row in rows},
        league_average_by_position=_league_average_by_position(rows),
    )


def resolve_grade(native_id: str, position: str, facet_grades: PffFacetGrades) -> ResolvedGrade:
    """Apply ADR-0014's fallback chain for one player against one already-fetched
    `PffFacetGrades` pull:

    1. That player's own row in `facet_grades` (current-season cumulative for week >= 2, or that
       same player's prior-season row for the week-1 fallback pull) -- a legitimate same-identity
       reference either way, matched by PFF's own stable `player_id`.
    2. Week-1 fallback only: no prior-season row for this player at all (e.g. a rookie) -- use
       the league-average grade for their position instead of dropping them.
    3. Otherwise `"unmatched"` -- e.g. a player with no current-season row yet in week >= 2, for
       which ADR-0014 specifies no fallback (accepted v1 limitation, not a bug to route around
       here).
    """
    row = facet_grades.by_player_id.get(native_id)
    if row is not None:
        return ResolvedGrade(
            native_id=native_id,
            position=row.position,
            grades=row.grades,
            player_game_count=row.player_game_count,
            population=facet_grades.population,
        )
    if facet_grades.population == "prior_season_fallback":
        league_average = facet_grades.league_average_by_position.get(position)
        if league_average is not None:
            return ResolvedGrade(
                native_id=native_id,
                position=position,
                grades=league_average,
                player_game_count=None,
                population="league_average_by_position",
            )
    return ResolvedGrade(
        native_id=native_id,
        position=position,
        grades={},
        player_game_count=None,
        population="unmatched",
    )


# --------------------------------------------------------------------------------------------
# Man/zone defensive tendency rollup (ADR-0022 Round A) -- pure aggregation over ALREADY-INGESTED
# defense/coverage_scheme grade rows. No new PFF call: `defense/coverage_scheme` has been in
# GRADE_FACETS since ADR-0014; this session's live pull confirmed it already returns
# `man_snap_counts_coverage`/`zone_snap_counts_coverage` per defender (real per-snap volume, not
# just the two grade fields the PRD table names), and `parse_pff_grade_facet`'s existing generic
# capture already carries them into every PffGradeRow.grades dict today, unchanged. "How often
# does this defense play man vs. zone" is therefore a pure sum-and-divide over data already
# flowing through the pipeline -- this is the cheapest item in ADR-0022's whole scope.
# --------------------------------------------------------------------------------------------

_MAN_SNAP_COUNT_FIELD = "man_snap_counts_coverage"
_ZONE_SNAP_COUNT_FIELD = "zone_snap_counts_coverage"


@dataclass(frozen=True)
class TeamCoverageTendency:
    """One team's man-vs-zone coverage-scheme tendency, rolled up from its defenders' individual
    `defense/coverage_scheme` snap counts (already-ingested `PffGradeRow.grades`). `man_rate`/
    `zone_rate` are `None` only when the team has zero combined man+zone coverage snaps recorded
    in the pull (e.g. a bye week, or every defender on the team missing this specific pair of
    fields) -- never silently coerced to 0.0, matching this project's general fallback discipline
    (ADR-0006/ADR-0012/ADR-0019's "no data" -> `None`, not a guessed number).
    """

    team: str
    man_snaps: int
    zone_snaps: int
    man_rate: float | None
    zone_rate: float | None
    defender_count: int  # distinct defenders on this team contributing man/zone snap data


def team_coverage_tendency(facet_grades: PffFacetGrades) -> dict[str, TeamCoverageTendency]:
    """Aggregate every defender row in `facet_grades` (expected to be a `defense/coverage_scheme`
    pull from `fetch_matchup_grades` -- this function does not itself validate the facet, it just
    sums whatever `man_snap_counts_coverage`/`zone_snap_counts_coverage` fields are present) up to
    one `TeamCoverageTendency` per team. A defender row with neither field present (e.g. missing
    from PFF's response for that player, or a non-coverage position with no snaps in the pull) is
    skipped for that row's contribution, not counted as a 0-snap defender -- consistent with
    `_league_average_by_position`'s existing "only average within a field a row actually has"
    behavior elsewhere in this module.
    """
    man_totals: dict[str, float] = {}
    zone_totals: dict[str, float] = {}
    defender_counts: dict[str, int] = {}

    for row in facet_grades.by_player_id.values():
        if row.team is None:
            continue
        man = row.grades.get(_MAN_SNAP_COUNT_FIELD)
        zone = row.grades.get(_ZONE_SNAP_COUNT_FIELD)
        if man is None and zone is None:
            continue
        man_totals[row.team] = man_totals.get(row.team, 0.0) + (man or 0.0)
        zone_totals[row.team] = zone_totals.get(row.team, 0.0) + (zone or 0.0)
        defender_counts[row.team] = defender_counts.get(row.team, 0) + 1

    result: dict[str, TeamCoverageTendency] = {}
    for team, defender_count in defender_counts.items():
        man_snaps = int(man_totals.get(team, 0.0))
        zone_snaps = int(zone_totals.get(team, 0.0))
        total_snaps = man_snaps + zone_snaps
        result[team] = TeamCoverageTendency(
            team=team,
            man_snaps=man_snaps,
            zone_snaps=zone_snaps,
            man_rate=man_snaps / total_snaps if total_snaps else None,
            zone_rate=zone_snaps / total_snaps if total_snaps else None,
            defender_count=defender_count,
        )
    return result
