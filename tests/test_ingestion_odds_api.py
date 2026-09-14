import json
import warnings
from pathlib import Path

import pandas as pd
import pytest

from nfl_dfs.ingestion.odds_api import (
    GameOdds,
    compute_implied_total_zscores,
    implied_team_totals,
    implied_totals_long,
    parse_dk_odds_events,
    schedule_week_map,
    team_abbr,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _events() -> list[dict]:
    return json.loads((FIXTURES / "odds_api_events.json").read_text())


def test_team_abbr_maps_full_names_to_canonical_codes():
    assert team_abbr("Jacksonville Jaguars") == "JAX"
    assert team_abbr("Los Angeles Rams") == "LAR"
    assert team_abbr("Not A Real Team") is None


def test_parse_dk_odds_events_extracts_only_the_draftkings_line():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        games = parse_dk_odds_events(_events())

    # evt1 has a DK line (kept); evt2 has no DK bookmaker (skipped); evt3 has unrecognized team
    # names (skipped) -- both skips should warn, not raise.
    assert len(games) == 1
    game = games[0]
    assert game.home_team == "JAX"
    assert game.away_team == "CLE"
    assert game.home_spread == -3.5
    assert game.away_spread == 3.5
    assert game.total == 44.0

    warning_messages = " ".join(str(w.message) for w in caught)
    assert "no DraftKings bookmaker line" in warning_messages
    assert "unrecognized team name" in warning_messages


def test_implied_team_totals_splits_total_by_spread():
    game = GameOdds(
        home_team="JAX", away_team="CLE", commence_time="x", home_spread=-3.5, away_spread=3.5, total=44.0,
        bookmaker_last_update=None,
    )
    totals = implied_team_totals(game)
    assert totals["JAX"] == pytest.approx(23.75)
    assert totals["CLE"] == pytest.approx(20.25)
    assert totals["JAX"] + totals["CLE"] == pytest.approx(44.0)


def test_implied_team_totals_raises_on_incomplete_line():
    game = GameOdds(
        home_team="JAX", away_team="CLE", commence_time="x", home_spread=None, away_spread=3.5, total=44.0,
        bookmaker_last_update=None,
    )
    with pytest.raises(ValueError, match="incomplete odds line"):
        implied_team_totals(game)


def test_schedule_week_map_normalizes_la_and_keys_by_direction():
    schedule = pd.DataFrame(
        [
            {"season": 2026, "week": 1, "home_team": "LA", "away_team": "SF"},
            {"season": 2026, "week": 5, "home_team": "SF", "away_team": "LA"},
        ]
    )
    week_map = schedule_week_map(schedule)
    assert week_map[("SF", "LAR", 2026)] == 1
    assert week_map[("LAR", "SF", 2026)] == 5


def test_implied_totals_long_warns_and_skips_unmatched_game():
    game = GameOdds(
        home_team="JAX", away_team="CLE", commence_time="x", home_spread=-3.5, away_spread=3.5, total=44.0,
        bookmaker_last_update=None,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = implied_totals_long([game], week_map={}, season=2026)
    assert out.empty
    assert any("no schedule week found" in str(w.message) for w in caught)


def test_implied_totals_long_and_zscore_end_to_end():
    games = [
        GameOdds("JAX", "CLE", "x", -3.5, 3.5, 44.0, None),
        GameOdds("KC", "DEN", "x", -7.0, 7.0, 48.0, None),
    ]
    week_map = {("CLE", "JAX", 2026): 1, ("DEN", "KC", 2026): 1}
    long_df = implied_totals_long(games, week_map, season=2026)
    assert set(long_df["team"]) == {"JAX", "CLE", "KC", "DEN"}

    scored = compute_implied_total_zscores(long_df)
    assert scored["implied_total_z"].sum() == pytest.approx(0.0, abs=1e-9)
    # KC (implied 27.5) has the highest total in this 4-team population -> highest z.
    assert scored.set_index("team")["implied_total_z"]["KC"] == scored["implied_total_z"].max()
