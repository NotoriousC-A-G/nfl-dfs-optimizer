import json
import warnings
from pathlib import Path

import pytest

from nfl_dfs.ingestion.odds_api_props import (
    DEFAULT_PROP_MARKETS,
    MatchedPlayerProp,
    PlayerProp,
    PropMatchMethod,
    american_to_implied_probability,
    events_in_week,
    match_prop_to_dk_player,
    parse_dk_player_props,
    player_props_to_dataframe,
)
from nfl_dfs.normalization.matcher import SourcePlayer

FIXTURES = Path(__file__).parent / "fixtures"

DK_POOL = [
    SourcePlayer(native_id="1", name="Jalen Hurts", team="PHI", position="QB"),
    SourcePlayer(native_id="2", name="Case Keenum", team="CHI", position="QB"),
    SourcePlayer(native_id="3", name="DeVonta Smith", team="PHI", position="WR"),
    SourcePlayer(native_id="4", name="Saquon Barkley", team="PHI", position="RB"),
]


def _payload() -> dict:
    return json.loads((FIXTURES / "odds_api_props_event.json").read_text())


# --- american_to_implied_probability -----------------------------------------------------------


def test_implied_probability_positive_odds():
    assert american_to_implied_probability(150) == pytest.approx(100 / 250)


def test_implied_probability_negative_odds():
    assert american_to_implied_probability(-150) == pytest.approx(150 / 250)


def test_implied_probability_even_money():
    assert american_to_implied_probability(100) == pytest.approx(0.5)
    assert american_to_implied_probability(-100) == pytest.approx(0.5)


def test_implied_probability_raises_on_zero():
    with pytest.raises(ValueError, match="not a valid American odds"):
        american_to_implied_probability(0)


# --- parse_dk_player_props -----------------------------------------------------------------------


def test_parse_extracts_clean_over_under_rows():
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        props = parse_dk_player_props(_payload())

    pass_yds = [p for p in props if p.market == "player_pass_yds"]
    assert {p.player_name for p in pass_yds} == {"Jalen Hurts", "Case Keenum"}

    hurts = next(p for p in pass_yds if p.player_name == "Jalen Hurts")
    assert hurts.point == 217.5
    assert hurts.over_price == -112
    assert hurts.under_price == -112
    assert hurts.over_prob == pytest.approx(american_to_implied_probability(-112))
    assert hurts.under_prob == pytest.approx(american_to_implied_probability(-112))
    assert hurts.last_update == "2026-09-28T22:09:55Z"


def test_parse_single_sided_market_has_no_point_or_under():
    props = parse_dk_player_props(_payload())
    anytime = {p.player_name: p for p in props if p.market == "player_anytime_td"}
    assert "Saquon Barkley" in anytime
    barkley = anytime["Saquon Barkley"]
    assert barkley.point is None
    assert barkley.under_price is None
    assert barkley.under_prob is None
    assert barkley.over_price == -115
    assert barkley.over_prob == pytest.approx(american_to_implied_probability(-115))


def test_parse_skips_outcome_with_no_player_name():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        props = parse_dk_player_props(_payload())
    assert all(p.player_name for p in props)
    assert any("no player name" in str(w.message) for w in caught)


def test_parse_skips_market_missing_one_side():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        props = parse_dk_player_props(_payload())
    # "Ghost Receiver" only has an Over outcome in the fixture -- must not appear.
    assert not any(p.player_name == "Ghost Receiver" for p in props)
    assert any("missing one side" in str(w.message) for w in caught)


def test_parse_skips_over_under_point_mismatch():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        props = parse_dk_player_props(_payload())
    assert not any(p.market == "player_pass_interceptions" for p in props)
    assert any("Over point" in str(w.message) and "!=" in str(w.message) for w in caught)


def test_parse_warns_on_requested_market_dk_did_not_return():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        parse_dk_player_props(_payload(), markets=DEFAULT_PROP_MARKETS)
    # player_rush_yds is only offered by FanDuel in the fixture, not DraftKings.
    assert any("player_rush_yds" in str(w.message) for w in caught)


def test_parse_only_reads_draftkings_bookmaker():
    props = parse_dk_player_props(_payload())
    # FanDuel's player_rush_yds line for Saquon Barkley must not leak in.
    assert not any(p.market == "player_rush_yds" for p in props)


def test_parse_warns_and_returns_empty_when_no_draftkings_bookmaker():
    payload = {"id": "evt_no_dk", "bookmakers": [{"key": "fanduel", "markets": []}]}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        props = parse_dk_player_props(payload)
    assert props == []
    assert any("no DraftKings bookmaker" in str(w.message) for w in caught)


def test_parse_total_row_count():
    props = parse_dk_player_props(_payload())
    # 2 (pass_yds) + 1 (receptions, Ghost Receiver dropped) + 2 (anytime_td, blank-name dropped)
    # + 0 (pass_interceptions, point mismatch dropped) = 5.
    assert len(props) == 5


# --- match_prop_to_dk_player ----------------------------------------------------------------------


def _prop(player_name: str, market: str = "player_receptions") -> PlayerProp:
    return PlayerProp(
        event_id="evt1", market=market, player_name=player_name, point=5.5, over_price=-120,
        under_price=-106, over_prob=0.5, under_prob=0.5, last_update=None,
    )


def test_match_resolves_unique_name_team_hit():
    matched = match_prop_to_dk_player(_prop("DeVonta Smith"), "CHI", "PHI", DK_POOL)
    assert matched.match_method == PropMatchMethod.NAME_TEAM
    assert matched.dk_native_id == "3"
    assert matched.dk_team == "PHI"
    assert matched.dk_position == "WR"


def test_match_unresolved_when_no_candidate():
    matched = match_prop_to_dk_player(_prop("Nobody Real"), "CHI", "PHI", DK_POOL)
    assert matched.match_method == PropMatchMethod.UNRESOLVED
    assert matched.dk_native_id is None
    assert "no DraftKings player named" in matched.match_note


def test_match_team_scoping_excludes_players_on_other_teams():
    pool = [SourcePlayer(native_id="9", name="Some Guy", team="DAL", position="WR")]
    matched = match_prop_to_dk_player(_prop("Some Guy"), "CHI", "PHI", pool)
    # "Some Guy" exists in the pool but on a team not in this event -- must not match.
    assert matched.match_method == PropMatchMethod.UNRESOLVED


def test_match_ambiguous_on_name_collision_within_event_teams():
    pool = [
        SourcePlayer(native_id="10", name="Mike Williams", team="PHI", position="WR"),
        SourcePlayer(native_id="11", name="Mike Williams", team="CHI", position="TE"),
    ]
    matched = match_prop_to_dk_player(_prop("Mike Williams"), "CHI", "PHI", pool)
    assert matched.match_method == PropMatchMethod.AMBIGUOUS
    assert matched.dk_native_id is None
    assert "name_collision" in matched.match_note


def test_match_resolves_dst_anytime_td_entry_by_team_not_name():
    pool = DK_POOL + [
        SourcePlayer(native_id="20", name="Eagles", team="PHI", position="DST"),
        SourcePlayer(native_id="21", name="Bears", team="CHI", position="DST"),
    ]
    prop = _prop("Philadelphia Eagles D/ST", market="player_anytime_td")
    matched = match_prop_to_dk_player(prop, "CHI", "PHI", pool)
    assert matched.match_method == PropMatchMethod.TEAM_DST
    assert matched.dk_native_id == "20"
    assert matched.dk_team == "PHI"
    assert matched.dk_position == "DST"


def test_match_dst_entry_unrecognized_team_name_stays_unresolved():
    prop = _prop("Some Made Up Team D/ST", market="player_anytime_td")
    matched = match_prop_to_dk_player(prop, "CHI", "PHI", DK_POOL)
    assert matched.match_method == PropMatchMethod.UNRESOLVED
    assert "not a recognized team name" in matched.match_note


def test_match_dst_entry_missing_from_pool_stays_unresolved():
    prop = _prop("Philadelphia Eagles D/ST", market="player_anytime_td")
    matched = match_prop_to_dk_player(prop, "CHI", "PHI", DK_POOL)  # DK_POOL has no DST rows
    assert matched.match_method == PropMatchMethod.UNRESOLVED
    assert "DraftKings DST rows found" in matched.match_note


def test_match_is_name_normalized():
    pool = [SourcePlayer(native_id="5", name="A.J. Brown Jr.", team="PHI", position="WR")]
    matched = match_prop_to_dk_player(_prop("AJ Brown"), "CHI", "PHI", pool)
    assert matched.match_method == PropMatchMethod.NAME_TEAM
    assert matched.dk_native_id == "5"


# --- events_in_week --------------------------------------------------------------------------------


def test_events_in_week_filters_by_schedule_week():
    events = [
        {"id": "e1", "home_team": "Chicago Bears", "away_team": "Philadelphia Eagles"},
        {"id": "e2", "home_team": "Kansas City Chiefs", "away_team": "Denver Broncos"},
    ]
    week_map = {("PHI", "CHI", 2026): 5, ("DEN", "KC", 2026): 6}
    result = events_in_week(events, week_map, season=2026, target_week=5)
    assert [e["id"] for e in result] == ["e1"]


def test_events_in_week_warns_and_skips_unrecognized_team():
    events = [{"id": "e1", "home_team": "AFC All-Stars", "away_team": "NFC All-Stars"}]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = events_in_week(events, week_map={}, season=2026, target_week=5)
    assert result == []
    assert any("unrecognized team name" in str(w.message) for w in caught)


def test_events_in_week_drops_event_not_in_target_week_silently():
    events = [{"id": "e1", "home_team": "Chicago Bears", "away_team": "Philadelphia Eagles"}]
    week_map = {("PHI", "CHI", 2026): 5}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = events_in_week(events, week_map, season=2026, target_week=6)
    assert result == []
    assert caught == []  # a real, different week is not a warning-worthy condition


# --- player_props_to_dataframe --------------------------------------------------------------------


def test_player_props_to_dataframe_shape():
    matched = [
        MatchedPlayerProp(
            prop=_prop("DeVonta Smith"),
            home_team="CHI",
            away_team="PHI",
            dk_native_id="3",
            dk_team="PHI",
            dk_position="WR",
            match_method=PropMatchMethod.NAME_TEAM,
        )
    ]
    df = player_props_to_dataframe(matched)
    assert list(df.columns) == [
        "event_id", "home_team", "away_team", "market", "player_name", "dk_native_id", "dk_team",
        "dk_position", "match_method", "match_note", "point", "over_price", "under_price",
        "over_prob", "under_prob", "last_update",
    ]
    row = df.iloc[0]
    assert row["player_name"] == "DeVonta Smith"
    assert row["dk_native_id"] == "3"
    assert row["match_method"] == "name_team"


def test_player_props_to_dataframe_empty_input():
    df = player_props_to_dataframe([])
    assert df.empty
    assert "player_name" in df.columns
