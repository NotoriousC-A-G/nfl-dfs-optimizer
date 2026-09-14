import json
from pathlib import Path

import pytest

from nfl_dfs.ingestion.rotogrinders import (
    extract_user_and_token,
    parse_available_grids,
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
