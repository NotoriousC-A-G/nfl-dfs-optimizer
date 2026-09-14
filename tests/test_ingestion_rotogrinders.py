import json
from pathlib import Path

import pytest

from nfl_dfs.ingestion.rotogrinders import (
    LineupHqOwnershipRow,
    extract_user_and_token,
    filter_to_main_slate,
    parse_available_grids,
    parse_projected_ownership,
    parse_user_projections,
    select_grid_id,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_user_and_token_from_embedded_iframe():
    html = (FIXTURES / "rotogrinders_lineuphq_page.html").read_text()
    user, token = extract_user_and_token(html)
    assert user == "12345"
    assert token == "abcdef0123456789abcdef0123456789"


def test_extract_user_and_token_raises_if_iframe_missing():
    with pytest.raises(RuntimeError, match="could not find"):
        extract_user_and_token("<html><body>no iframe here</body></html>")


def test_select_grid_id_prefers_house_grid_over_inaccessible_third_party():
    payload = json.loads((FIXTURES / "rotogrinders_grids.json").read_text())
    grids = parse_available_grids(payload)
    assert select_grid_id(grids) == "3350867"


def test_select_grid_id_raises_when_no_grid_is_accessible():
    grids = {"1": {"id": 1, "has_access": False, "is_rg": True, "order": 0}}
    with pytest.raises(RuntimeError, match="no accessible"):
        select_grid_id(grids)


def test_parse_user_projections_shapes_source_players_and_shows_crosswalk_style_team_codes():
    payload = json.loads((FIXTURES / "rotogrinders_projections.json").read_text())
    players = parse_user_projections(payload)

    assert len(players) == 18
    maye = next(p for p in players if p.name == "Drake Maye")
    assert maye.native_id == "6228327"
    # Raw RG label -- New England as "NEP", not DK's "NE". Normalization is the matcher's job.
    assert maye.team == "NEP"
    assert maye.position == "QB"

    patriots_dst = next(p for p in players if p.position == "DST")
    assert patriots_dst.team == "NEP"


def test_parse_projected_ownership_shapes_rows_and_handles_null_salary():
    payload = json.loads((FIXTURES / "rotogrinders_projections.json").read_text())
    rows = parse_projected_ownership(payload)

    assert len(rows) == 18
    maye = next(r for r in rows if r.name == "Drake Maye")
    assert maye.native_id == "6228327"
    assert maye.salary == 6000
    assert maye.projected_ownership == pytest.approx(0.0)

    # This fixture's kickers carry SALARY: null -- must not crash, defaults to 0 rather than raising.
    kicker = next(r for r in rows if r.name == "Andres Borregales")
    assert kicker.salary == 0

    # This fixture is a Wednesday off-day pull -- every row carries the real SLATE label, not "MAIN".
    assert maye.slate == "WED"


def test_filter_to_main_slate_keeps_only_main_and_drops_single_game_windows():
    # Live-confirmed real bug (ADR-0026): a healthy, actively-projected player genuinely not part of the
    # main slate (a Wednesday/Thursday/Sunday-night/Monday-night game) correctly shows 0% ownership for the
    # main slate -- that's not a data error, it's exclusion from the contest, and must be filtered out
    # rather than misread as "underowned."
    rows = [
        LineupHqOwnershipRow(native_id="1", name="Main Slate Player", position="WR", team="KC", salary=7000, projected_ownership=15.0, slate="MAIN"),
        LineupHqOwnershipRow(native_id="2", name="Wednesday Player", position="RB", team="LAR", salary=7500, projected_ownership=0.0, slate="WED"),
        LineupHqOwnershipRow(native_id="3", name="Thursday Player", position="WR", team="PHI", salary=7100, projected_ownership=0.0, slate="THU"),
        LineupHqOwnershipRow(native_id="4", name="Sunday Night Player", position="WR", team="DAL", salary=7400, projected_ownership=0.0, slate="SNF"),
        LineupHqOwnershipRow(native_id="5", name="Monday Night Player", position="TE", team="KC", salary=4000, projected_ownership=0.0, slate="MNF"),
    ]
    filtered = filter_to_main_slate(rows)
    assert [row.name for row in filtered] == ["Main Slate Player"]


def test_parse_projected_ownership_parses_real_nonzero_percent_and_bare_number():
    payload = {
        "data": {
            "source": {
                "1": {"PLAYERID": "1", "PLAYER": "Chalk Player", "POS": "RB", "TEAM": "DET", "SALARY": "8000", "POWN": "41.48%"},
                "2": {"PLAYERID": "2", "PLAYER": "Zero Owned", "POS": "WR", "TEAM": "SEA", "SALARY": "3000", "POWN": 0},
            }
        }
    }
    rows = {row.name: row for row in parse_projected_ownership(payload)}
    assert rows["Chalk Player"].projected_ownership == pytest.approx(41.48)
    assert rows["Zero Owned"].projected_ownership == pytest.approx(0.0)
