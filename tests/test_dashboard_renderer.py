import re

from nfl_dfs.composition.player_detail import build_player_detail_record
from nfl_dfs.dashboard.renderer import _render_player_detail_tab
from nfl_dfs.matchup.context import MatchupFacetInputs

_FACETS = MatchupFacetInputs(
    offense_run_blocking={"BUF": 78.0, "MIA": 65.0},
    defense_run={"BUF": 70.0, "MIA": 60.0},
    offense_pass_blocking={"BUF": 74.0, "MIA": 68.0},
    defense_pass_rush={"BUF": 72.0, "MIA": 66.0},
    defense_coverage_scheme={"BUF": 69.0, "MIA": 71.0},
    receiving_scheme={"BUF": 73.0, "MIA": 70.0},
)


def test_player_detail_tab_renders_real_unit_grades_when_facets_supplied():
    rb = build_player_detail_record(
        player_id="1",
        name="Sample RB",
        position="RB",
        team="BUF",
        opponent="MIA",
        salary=7600,
        projection=18.0,
        matchup_facets=_FACETS,
    )

    table = _render_player_detail_tab([rb])
    lines = table.splitlines()

    assert "Own Grade" in lines[0]
    assert "Opp Grade" in lines[0]
    cells = re.split(r"\s{2,}", lines[1].strip())
    assert cells[-2:] == ["78.0", "60.0"]


def test_player_detail_tab_falls_back_to_placeholder_without_facets():
    rb = build_player_detail_record(
        player_id="1",
        name="Sample RB",
        position="RB",
        team="BUF",
        opponent="MIA",
        salary=7600,
        projection=18.0,
    )

    table = _render_player_detail_tab([rb])
    data_row = table.splitlines()[1]
    cells = re.split(r"\s{2,}", data_row.strip())

    assert cells[-2:] == ["--", "--"]


def test_player_detail_tab_dst_has_no_unit_grade_pair_even_with_facets():
    dst = build_player_detail_record(
        player_id="5",
        name="Sample DST",
        position="DST",
        team="BUF",
        opponent="MIA",
        salary=2800,
        projection=8.0,
        matchup_facets=_FACETS,
    )

    assert dst.matchup_this_week.own_unit_grade is None
    assert dst.matchup_this_week.opponent_unit_grade is None

    table = _render_player_detail_tab([dst])
    data_row = table.splitlines()[1]
    assert "--" in data_row


def test_player_detail_tab_renders_multiple_positions_together():
    facets = _FACETS
    records = [
        build_player_detail_record("1", "Sample QB", "QB", "BUF", "MIA", 8200, 22.5, facets),
        build_player_detail_record("2", "Sample RB", "RB", "BUF", "MIA", 7600, 18.0, facets),
        build_player_detail_record("3", "Sample WR", "WR", "MIA", "BUF", 7000, 15.5, facets),
    ]

    table = _render_player_detail_tab(records)
    lines = table.splitlines()

    assert len(lines) == 1 + len(records)
    # QB: own = offense_pass_blocking[BUF] = 74.0, opponent = defense_pass_rush[MIA] = 66.0
    assert "74.0" in lines[1] and "66.0" in lines[1]
    # WR: own = receiving_scheme[MIA] = 70.0, opponent = defense_coverage_scheme[BUF] = 69.0
    assert "70.0" in lines[3] and "69.0" in lines[3]
