import json
from pathlib import Path

import pytest

from nfl_dfs.ingestion.pff import (
    GRADE_FACETS,
    PffFacetGrades,
    PffGradeRow,
    _check_entitlement,
    _league_average_by_position,
    completed_weeks_param,
    fetch_matchup_grades,
    parse_pff_facet,
    parse_pff_grade_facet,
    resolve_grade,
    team_coverage_tendency,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(facet_filename: str) -> dict:
    return json.loads((FIXTURES / facet_filename).read_text())


def test_parse_rushing_facet_shapes_source_players_and_keeps_hb_label():
    payload = _load("pff_rushing_summary.json")
    players = parse_pff_facet(payload, "rushing/summary")

    assert len(players) == 5
    gibbs = next(p for p in players if p.name == "Jahmyr Gibbs")
    assert gibbs.team == "DET"
    # Raw PFF label, not yet normalized -- normalization happens in the matcher, not here.
    assert gibbs.position == "HB"
    assert gibbs.jersey_number == "00"


def test_parse_passing_and_receiving_facets():
    passing = parse_pff_facet(_load("pff_passing_summary.json"), "passing/summary")
    receiving = parse_pff_facet(_load("pff_receiving_summary.json"), "receiving/summary")
    assert all(p.position == "QB" for p in passing)
    assert all(p.position in {"WR", "TE", "RB", "HB"} for p in receiving)


def test_parse_pff_facet_raises_on_missing_response_key():
    with pytest.raises(ValueError, match="missing expected key"):
        parse_pff_facet({"unexpected": []}, "rushing/summary")


def test_parse_pff_facet_raises_on_row_missing_required_field():
    payload = {"rushing_summary": [{"player_id": 1, "player": "No Team Guy", "position": "HB"}]}
    with pytest.raises(ValueError, match="missing required field"):
        parse_pff_facet(payload, "rushing/summary")


def test_check_entitlement_raises_on_restricted_key():
    with pytest.raises(RuntimeError, match="entitlement downgrade"):
        _check_entitlement({"restricted": True, "rushing_summary": []}, "rushing/summary")


def test_check_entitlement_passes_on_normal_payload():
    _check_entitlement({"rushing_summary": []}, "rushing/summary")


# --------------------------------------------------------------------------------------------
# MatchupContext grade facets (ADR-0014)
# --------------------------------------------------------------------------------------------


def test_completed_weeks_param_week_one_is_none():
    # Zero completed current-season weeks -- cumulative undefined, callers use the prior-season
    # fallback instead of calling the current-season endpoint at all.
    assert completed_weeks_param(1) is None


def test_completed_weeks_param_builds_comma_separated_list():
    assert completed_weeks_param(2) == "1"
    assert completed_weeks_param(5) == "1,2,3,4"


def test_completed_weeks_param_rejects_week_below_one():
    with pytest.raises(ValueError, match="current_week must be >= 1"):
        completed_weeks_param(0)


def test_parse_pff_grade_facet_shapes_rows_with_player_game_count_not_per_week_rows():
    # Fixture reflects the live-confirmed server-side-aggregated shape: one row per player for a
    # multi-week request, carrying player_game_count -- not one row per player per week.
    payload = _load("pff_run_blocking_cumulative.json")
    rows = parse_pff_grade_facet(payload, "offense/run_blocking")

    assert len(rows) == 2
    walker = next(r for r in rows if r.name == "Rasheed Walker")
    assert walker.native_id == "81798"
    assert walker.team == "GB"
    assert walker.position == "T"
    assert walker.player_game_count == 3
    assert walker.grades["grades_run_block"] == 68.4
    # Metadata fields must not leak into the grades dict.
    for metadata_field in ("player_id", "player", "team", "position", "player_game_count"):
        assert metadata_field not in walker.grades


def test_parse_pff_grade_facet_raises_on_missing_response_key():
    with pytest.raises(ValueError, match="missing expected key"):
        parse_pff_grade_facet({"unexpected": []}, "offense/run_blocking")


def test_parse_pff_grade_facet_raises_on_row_missing_required_field():
    payload = {"run_blocking": [{"player_id": 1, "player": "No Team Guy", "position": "T"}]}
    with pytest.raises(ValueError, match="missing required field"):
        parse_pff_grade_facet(payload, "offense/run_blocking")


def test_league_average_by_position_averages_only_within_position():
    rows = [
        PffGradeRow("1", "A", "GB", "T", None, 10, grades={"grades_run_block": 60.0}),
        PffGradeRow("2", "B", "PHI", "T", None, 10, grades={"grades_run_block": 80.0}),
        PffGradeRow("3", "C", "BUF", "G", None, 10, grades={"grades_run_block": 50.0}),
    ]
    averages = _league_average_by_position(rows)
    assert averages["T"]["grades_run_block"] == 70.0
    assert averages["G"]["grades_run_block"] == 50.0


class _FakeSession:
    """Captures the params sent per call and returns the fixture matching whether a `week` param
    was included, so both branches of `fetch_matchup_grades` can be exercised against real
    payload shapes without a network call.
    """

    def __init__(self, cumulative_payload: dict, prior_season_payload: dict):
        self.cumulative_payload = cumulative_payload
        self.prior_season_payload = prior_season_payload
        self.calls: list[dict] = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(params)
        payload = self.cumulative_payload if "week" in params else self.prior_season_payload
        return _FakeResponse(payload)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_matchup_grades_week_ge_2_builds_week_list_and_one_call():
    cumulative = _load("pff_run_blocking_cumulative.json")
    prior = _load("pff_run_blocking_prior_season.json")
    session = _FakeSession(cumulative, prior)

    result = fetch_matchup_grades(
        "offense/run_blocking", current_week=4, season=2026, api_key="test-key", session=session
    )

    assert len(session.calls) == 1
    assert session.calls[0]["week"] == "1,2,3"
    assert session.calls[0]["season"] == 2026
    assert result.population == "cumulative_through_last_completed_week"
    assert result.weeks_requested == "1,2,3"
    assert "81798" in result.by_player_id
    assert result.league_average_by_position == {}


def test_fetch_matchup_grades_week_one_uses_prior_season_fallback():
    cumulative = _load("pff_run_blocking_cumulative.json")
    prior = _load("pff_run_blocking_prior_season.json")
    session = _FakeSession(cumulative, prior)

    result = fetch_matchup_grades(
        "offense/run_blocking", current_week=1, season=2026, api_key="test-key", session=session
    )

    assert len(session.calls) == 1
    assert "week" not in session.calls[0]
    assert session.calls[0]["season"] == "2024,2025"
    assert result.population == "prior_season_fallback"
    assert result.weeks_requested is None
    assert "81798" in result.by_player_id
    # League-average-by-position backstop is only computed for the fallback branch.
    assert result.league_average_by_position["G"]["grades_run_block"] == 55.0


def test_fetch_matchup_grades_rejects_unknown_facet():
    with pytest.raises(ValueError, match="not one of MatchupContext's grade facets"):
        fetch_matchup_grades("offense/nonsense", current_week=2, season=2026, api_key="k", session=_FakeSession({}, {}))


def test_resolve_grade_returns_players_own_row_when_present():
    facet_grades = PffFacetGrades(
        population="cumulative_through_last_completed_week",
        weeks_requested="1,2,3",
        seasons_requested="2026",
        by_player_id={"81798": PffGradeRow("81798", "Rasheed Walker", "GB", "T", "63", 3, {"grades_run_block": 68.4})},
    )
    resolved = resolve_grade("81798", "T", facet_grades)
    assert resolved.population == "cumulative_through_last_completed_week"
    assert resolved.grades["grades_run_block"] == 68.4
    assert resolved.player_game_count == 3


def test_resolve_grade_week_one_fallback_uses_league_average_for_rookie_with_no_prior_row():
    facet_grades = PffFacetGrades(
        population="prior_season_fallback",
        weeks_requested=None,
        seasons_requested="2024,2025",
        by_player_id={"84014": PffGradeRow("84014", "Cam Jurgens", "PHI", "C", "51", 20, {"grades_run_block": 67.2})},
        league_average_by_position={"T": {"grades_run_block": 61.5}},
    )
    # A rookie tackle with no prior-season PFF record at all.
    resolved = resolve_grade("999999-rookie", "T", facet_grades)
    assert resolved.population == "league_average_by_position"
    assert resolved.grades["grades_run_block"] == 61.5
    assert resolved.player_game_count is None


def test_resolve_grade_week_ge_2_no_fallback_for_unmatched_player():
    # ADR-0014: no fallback is specified for weeks >= 2 when a player has no current-season row
    # yet (e.g. debuted mid-season) -- accepted v1 limitation, not routed to league average.
    facet_grades = PffFacetGrades(
        population="cumulative_through_last_completed_week",
        weeks_requested="1,2,3",
        seasons_requested="2026",
        by_player_id={},
    )
    resolved = resolve_grade("some-id", "T", facet_grades)
    assert resolved.population == "unmatched"
    assert resolved.grades == {}


def test_resolve_grade_week_one_fallback_unmatched_when_no_position_average_either():
    facet_grades = PffFacetGrades(
        population="prior_season_fallback",
        weeks_requested=None,
        seasons_requested="2024,2025",
        by_player_id={},
        league_average_by_position={},
    )
    resolved = resolve_grade("some-id", "T", facet_grades)
    assert resolved.population == "unmatched"


# --------------------------------------------------------------------------------------------
# ADR-0022 Round A -- receiving/scheme and defense/pass_rush GRADE_FACETS additions
# --------------------------------------------------------------------------------------------


def test_receiving_scheme_and_pass_rush_are_registered_grade_facets():
    assert GRADE_FACETS["receiving/scheme"] == "receiving_scheme"
    assert GRADE_FACETS["defense/pass_rush"] == "pass_rush_summary"


def test_parse_pff_grade_facet_handles_receiving_scheme_man_zone_fields_unchanged():
    # Confirms ADR-0022's claim that receiving/scheme's response shape matches the existing
    # facets exactly -- reuses parse_pff_grade_facet with zero new parsing code.
    payload = _load("pff_receiving_scheme_cumulative.json")
    rows = parse_pff_grade_facet(payload, "receiving/scheme")

    assert len(rows) == 2
    ferguson = next(r for r in rows if r.name == "Jake Ferguson")
    assert ferguson.native_id == "91234"
    assert ferguson.team == "DAL"
    assert ferguson.position == "TE"
    assert ferguson.player_game_count == 3
    assert ferguson.grades["man_targets"] == 10
    assert ferguson.grades["zone_targets"] == 16
    assert ferguson.grades["man_grades_pass_route"] == 58.9
    assert ferguson.grades["zone_grades_pass_route"] == 71.3
    for metadata_field in ("player_id", "player", "team", "position", "player_game_count"):
        assert metadata_field not in ferguson.grades


def test_parse_pff_grade_facet_handles_defense_pass_rush_unchanged():
    payload = _load("pff_pass_rush_cumulative.json")
    rows = parse_pff_grade_facet(payload, "defense/pass_rush")

    assert len(rows) == 2
    campbell = next(r for r in rows if r.name == "Calais Campbell")
    assert campbell.native_id == "4364"
    assert campbell.team == "ARZ"
    assert campbell.position == "DI"
    assert campbell.grades["grades_pass_rush_defense"] == 77.6
    assert campbell.grades["pass_rush_win_rate"] == 14.7
    assert "player_id" not in campbell.grades


def test_fetch_matchup_grades_works_for_receiving_scheme_facet():
    # fetch_matchup_grades/resolve_grade are reused unchanged (ADR-0022) -- exercised end to end
    # for the new facet the same way the existing four facets are tested above.
    payload = _load("pff_receiving_scheme_cumulative.json")
    session = _FakeSession(payload, payload)
    result = fetch_matchup_grades(
        "receiving/scheme", current_week=4, season=2026, api_key="test-key", session=session
    )
    assert result.population == "cumulative_through_last_completed_week"
    resolved = resolve_grade("91234", "TE", result)
    assert resolved.grades["man_yprr"] == 1.10
    assert resolved.grades["zone_yprr"] == 1.85


# --------------------------------------------------------------------------------------------
# ADR-0022 Round A -- man/zone defensive tendency team-level rollup
# --------------------------------------------------------------------------------------------


def _coverage_scheme_facet_grades() -> PffFacetGrades:
    payload = _load("pff_coverage_scheme_cumulative.json")
    rows = parse_pff_grade_facet(payload, "defense/coverage_scheme")
    return PffFacetGrades(
        population="cumulative_through_last_completed_week",
        weeks_requested="1,2,3",
        seasons_requested="2026",
        by_player_id={row.native_id: row for row in rows},
    )


def test_team_coverage_tendency_sums_defenders_to_team_level():
    facet_grades = _coverage_scheme_facet_grades()
    tendency = team_coverage_tendency(facet_grades)

    # SEA: two zone-heavy corners, man 30+20=50, zone 150+130=280 -> total 330.
    sea = tendency["SEA"]
    assert sea.man_snaps == 50
    assert sea.zone_snaps == 280
    assert sea.man_rate == pytest.approx(50 / 330)
    assert sea.zone_rate == pytest.approx(280 / 330)
    assert sea.defender_count == 2


def test_team_coverage_tendency_reflects_man_heavy_defense_distinctly():
    facet_grades = _coverage_scheme_facet_grades()
    tendency = team_coverage_tendency(facet_grades)

    # MIA: one heavily-man corner (140 man / 40 zone) + one safety with no snap-count fields at
    # all (must be skipped from the sum, not counted as a 0-snap contributor).
    mia = tendency["MIA"]
    assert mia.man_snaps == 140
    assert mia.zone_snaps == 40
    assert mia.man_rate == pytest.approx(140 / 180)
    assert mia.defender_count == 1  # the no-snap-data safety does not count as a contributor

    # Sanity: MIA is clearly more man-heavy than SEA, the real signal this rollup exists for.
    sea = tendency["SEA"]
    assert mia.man_rate > sea.man_rate


def test_team_coverage_tendency_handles_no_data_without_crashing():
    facet_grades = PffFacetGrades(
        population="cumulative_through_last_completed_week",
        weeks_requested="1",
        seasons_requested="2026",
        by_player_id={
            "1": PffGradeRow("1", "No Data Guy", "NYJ", "S", None, 3, grades={"man_grades_coverage_defense": 50.0})
        },
    )
    tendency = team_coverage_tendency(facet_grades)
    assert tendency == {}
