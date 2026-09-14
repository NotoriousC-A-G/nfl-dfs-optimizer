from nfl_dfs.matchup.context import (
    MatchupFacetInputs,
    build_matchup_context_pool,
    resolve_own_opponent_unit_grades,
)

_FACETS = MatchupFacetInputs(
    offense_run_blocking={"BUF": 78.0, "MIA": 65.0},
    defense_run={"BUF": 70.0, "MIA": 60.0},
    offense_pass_blocking={"BUF": 74.0, "MIA": 68.0},
    defense_pass_rush={"BUF": 72.0, "MIA": 66.0},
    defense_coverage_scheme={"BUF": 69.0, "MIA": 71.0},
    receiving_scheme={"BUF": 73.0, "MIA": 70.0},
)


def test_rb_resolves_own_run_block_vs_opponent_run_defense():
    grades = resolve_own_opponent_unit_grades("RB", "BUF", "MIA", _FACETS)
    assert grades.own_unit_grade == 78.0
    assert grades.opponent_unit_grade == 60.0


def test_qb_resolves_own_pass_block_vs_opponent_pass_rush():
    grades = resolve_own_opponent_unit_grades("QB", "BUF", "MIA", _FACETS)
    assert grades.own_unit_grade == 74.0
    assert grades.opponent_unit_grade == 66.0


def test_wr_resolves_own_receiving_scheme_vs_opponent_coverage_scheme():
    grades = resolve_own_opponent_unit_grades("WR", "MIA", "BUF", _FACETS)
    assert grades.own_unit_grade == 70.0
    assert grades.opponent_unit_grade == 69.0


def test_dst_has_no_defined_unit_grade_pair():
    grades = resolve_own_opponent_unit_grades("DST", "BUF", "MIA", _FACETS)
    assert grades.own_unit_grade is None
    assert grades.opponent_unit_grade is None


def test_missing_team_in_facet_map_resolves_to_none_not_an_error():
    grades = resolve_own_opponent_unit_grades("RB", "BUF", "NYJ", _FACETS)
    assert grades.own_unit_grade == 78.0
    assert grades.opponent_unit_grade is None


def test_build_matchup_context_pool_favors_stronger_run_block_over_weaker_defense():
    players = [("rb1", "RB", "BUF", "MIA")]
    pool = build_matchup_context_pool(players, _FACETS)

    context = pool["rb1"]
    assert context.own_unit_grade == 78.0
    assert context.opponent_unit_grade == 60.0
    assert context.multiplier > 1.0
    assert context.multiplier <= 1.15


def test_build_matchup_context_pool_caps_multiplier_within_bounds():
    lopsided_facets = MatchupFacetInputs(
        offense_run_blocking={"BUF": 99.0, "MIA": 40.0},
        defense_run={"BUF": 90.0, "MIA": 20.0},
        offense_pass_blocking={},
        defense_pass_rush={},
        defense_coverage_scheme={},
        receiving_scheme={},
    )
    players = [("rb1", "RB", "BUF", "MIA")]
    pool = build_matchup_context_pool(players, lopsided_facets)

    assert 0.85 <= pool["rb1"].multiplier <= 1.15


def test_build_matchup_context_pool_neutral_for_position_without_unit_grade_pair():
    players = [("dst1", "DST", "BUF", "MIA")]
    pool = build_matchup_context_pool(players, _FACETS)

    context = pool["dst1"]
    assert context.own_unit_grade is None
    assert context.opponent_unit_grade is None
    assert context.multiplier == 1.0
