from nfl_dfs.normalization.position_aliases import normalize_position
from nfl_dfs.normalization.team_aliases import normalize_team


def test_crosswalk_team_aliases_match_adr0013_live_findings():
    assert normalize_team("crosswalk", "GBP") == "GB"
    assert normalize_team("crosswalk", "LVR") == "LV"
    assert normalize_team("crosswalk", "KCC") == "KC"
    assert normalize_team("crosswalk", "JAC") == "JAX"
    assert normalize_team("crosswalk", "NEP") == "NE"
    assert normalize_team("crosswalk", "NOS") == "NO"
    assert normalize_team("crosswalk", "SFO") == "SF"
    assert normalize_team("crosswalk", "TBB") == "TB"


def test_crosswalk_retired_franchise_codes_map_to_current_team():
    assert normalize_team("crosswalk", "OAK") == "LV"
    assert normalize_team("crosswalk", "SDC") == "LAC"
    assert normalize_team("crosswalk", "STL") == "LAR"
    assert normalize_team("crosswalk", "RAM") == "LAR"


def test_crosswalk_free_agent_codes_map_to_none_not_guessed():
    assert normalize_team("crosswalk", "FA") is None
    assert normalize_team("crosswalk", "FA*") is None


def test_pff_team_aliases_found_live_in_this_implementation_pass():
    assert normalize_team("pff", "ARZ") == "ARI"
    assert normalize_team("pff", "BLT") == "BAL"
    assert normalize_team("pff", "CLV") == "CLE"
    assert normalize_team("pff", "HST") == "HOU"
    assert normalize_team("pff", "LA") == "LAR"


def test_draftkings_team_codes_need_no_aliasing():
    assert normalize_team("draftkings", "GB") == "GB"
    assert normalize_team("draftkings", "LV") == "LV"
    assert normalize_team("draftkings", "WAS") == "WAS"


def test_pff_hb_aliases_to_rb():
    assert normalize_position("pff", "HB") == "RB"


def test_draftkings_positions_need_no_aliasing():
    assert normalize_position("draftkings", "RB") == "RB"
    assert normalize_position("draftkings", "DST") == "DST"


def test_rotogrinders_team_aliases_found_live_building_ingestion():
    # RotoGrinders uses the exact same non-canonical codes as the nflverse crosswalk for these
    # eight franchises (ingestion/rotogrinders.py's live pull, 2026 wk1).
    assert normalize_team("rotogrinders", "GBP") == "GB"
    assert normalize_team("rotogrinders", "JAC") == "JAX"
    assert normalize_team("rotogrinders", "KCC") == "KC"
    assert normalize_team("rotogrinders", "LVR") == "LV"
    assert normalize_team("rotogrinders", "NEP") == "NE"
    assert normalize_team("rotogrinders", "NOS") == "NO"
    assert normalize_team("rotogrinders", "SFO") == "SF"
    assert normalize_team("rotogrinders", "TBB") == "TB"


def test_footballguys_team_codes_confirmed_live_to_need_no_aliasing():
    assert normalize_team("footballguys", "JAX") == "JAX"
    assert normalize_team("footballguys", "LV") == "LV"
    assert normalize_team("footballguys", "WAS") == "WAS"


def test_footballguys_team_defense_position_aliases_to_dst():
    # ingestion/footballguys.py's live pull found team defenses labeled "TD", not "DEF"/"D" as
    # this table previously guessed.
    assert normalize_position("footballguys", "TD") == "DST"


def test_rotogrinders_positions_already_canonical_or_out_of_scope():
    assert normalize_position("rotogrinders", "DST") == "DST"
    assert normalize_position("rotogrinders", "QB") == "QB"
    # "K" is out of this project's five-position vocabulary, same as the crosswalk's non-skill
    # positions -- passed through unchanged rather than forced into a canonical bucket.
    assert normalize_position("rotogrinders", "K") == "K"
