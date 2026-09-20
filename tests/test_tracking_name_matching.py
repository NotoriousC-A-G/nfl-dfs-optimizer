import pytest

from nfl_dfs.tracking.name_matching import normalize_player_name, parse_player_token


def test_normalize_player_name_strips_periods_and_lowercases():
    assert normalize_player_name("T.J. Hockenson") == "tj hockenson"


def test_normalize_player_name_strips_generational_suffix():
    assert normalize_player_name("K.C. Concepcion Jr.") == "kc concepcion"
    assert normalize_player_name("Aaron Jones Sr.") == "aaron jones"
    assert normalize_player_name("Luther Burden III") == "luther burden"


def test_normalize_player_name_strips_apostrophes_and_hyphens():
    assert normalize_player_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_player_name("Amon-Ra St. Brown") == "amon ra st brown"


def test_normalize_player_name_matches_across_equivalent_spellings():
    assert normalize_player_name("Justin Jefferson") == normalize_player_name("justin  jefferson")


def test_parse_player_token_offensive_player():
    assert parse_player_token("Trevor Lawrence (QB-JAX)") == ("Trevor Lawrence", "QB", "JAX")


def test_parse_player_token_dst():
    assert parse_player_token("Jaguars (DST-JAX)") == ("Jaguars", "DST", "JAX")


def test_parse_player_token_flex_slot_label():
    assert parse_player_token("Darren Waller (FLEX-CAR)") == ("Darren Waller", "FLEX", "CAR")


def test_parse_player_token_raises_on_bad_shape():
    with pytest.raises(ValueError):
        parse_player_token("Trevor Lawrence QB JAX")
