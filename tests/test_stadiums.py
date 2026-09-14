import pytest

from nfl_dfs.ingestion.stadiums import STADIUMS, get_stadium
from nfl_dfs.normalization.team_aliases import CANONICAL_TEAMS


def test_every_canonical_team_has_a_stadium_entry():
    assert set(STADIUMS.keys()) == set(CANONICAL_TEAMS)


def test_shared_stadiums_have_matching_coordinates():
    metlife_ny = STADIUMS["NYG"]
    metlife_nj = STADIUMS["NYJ"]
    assert metlife_ny.latitude == metlife_nj.latitude
    assert metlife_ny.longitude == metlife_nj.longitude

    sofi_lar = STADIUMS["LAR"]
    sofi_lac = STADIUMS["LAC"]
    assert sofi_lar.latitude == sofi_lac.latitude


def test_is_indoor_true_for_dome_and_retractable():
    assert STADIUMS["DET"].is_indoor is True  # dome
    assert STADIUMS["DAL"].is_indoor is True  # retractable (closed-by-default assumption)
    assert STADIUMS["GB"].is_indoor is False  # outdoor


def test_get_stadium_raises_on_unknown_team():
    with pytest.raises(ValueError, match="no stadium entry"):
        get_stadium("XXX")
