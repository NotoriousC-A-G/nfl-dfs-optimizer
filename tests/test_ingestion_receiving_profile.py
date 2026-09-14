import pandas as pd
import pytest

from nfl_dfs.ingestion.receiving_profile import aggregate_trailing_receiving_profile, trailing_receiving_profiles


def _pbp_row(**kwargs) -> dict:
    row = {
        "season": 2026,
        "week": 1,
        "season_type": "REG",
        "posteam": "GB",
        "play_type": "pass",
        "receiver_player_id": None,
        "receiver_player_name": None,
        "complete_pass": 0,
        "air_yards": None,
        "yards_after_catch": None,
        "play_id": 1,
    }
    row.update(kwargs)
    return row


def _target(
    week: int, team: str, player_id: str, name: str, play_id: int, *, air_yards: float, complete: bool,
    yards_after_catch: float | None = None,
) -> dict:
    return _pbp_row(
        week=week, posteam=team, receiver_player_id=player_id, receiver_player_name=name, play_id=play_id,
        air_yards=air_yards, complete_pass=1 if complete else 0,
        yards_after_catch=yards_after_catch if complete else None,
    )


def test_aggregates_targets_air_yards_and_adot():
    rows = [
        _target(1, "GB", "WR1", "Wide One", 1, air_yards=10.0, complete=True, yards_after_catch=5.0),
        _target(1, "GB", "WR1", "Wide One", 2, air_yards=20.0, complete=False),
        _target(2, "GB", "WR1", "Wide One", 3, air_yards=6.0, complete=True, yards_after_catch=2.0),
    ]
    profiles = trailing_receiving_profiles(pd.DataFrame(rows), target_week=3)
    p = profiles["WR1"]
    assert p.trailing_targets == 3
    assert p.trailing_air_yards == 36  # 10 + 20 + 6
    assert p.trailing_adot == pytest.approx(12.0)  # mean(10, 20, 6)
    assert p.trailing_receptions == 2
    assert p.trailing_yac_per_reception == pytest.approx(3.5)  # mean(5, 2)


def test_incomplete_targets_have_no_yac_but_still_count_toward_air_yards():
    rows = [_target(1, "GB", "WR2", "Wide Two", 1, air_yards=15.0, complete=False)]
    profiles = trailing_receiving_profiles(pd.DataFrame(rows), target_week=2)
    p = profiles["WR2"]
    assert p.trailing_targets == 1
    assert p.trailing_receptions == 0
    assert p.trailing_air_yards == 15
    assert p.trailing_adot == pytest.approx(15.0)
    assert p.trailing_yac_per_reception is None


def test_excludes_future_weeks():
    rows = [
        _target(1, "GB", "WR3", "Wide Three", 1, air_yards=5.0, complete=True, yards_after_catch=1.0),
        _target(3, "GB", "WR3", "Wide Three", 2, air_yards=99.0, complete=True, yards_after_catch=99.0),
    ]
    profiles = trailing_receiving_profiles(pd.DataFrame(rows), target_week=3)
    p = profiles["WR3"]
    assert p.trailing_targets == 1
    assert p.trailing_air_yards == 5


def test_player_with_no_targets_at_all_is_absent_from_the_lookup():
    rows = [_target(1, "GB", "WR4", "Wide Four", 1, air_yards=5.0, complete=True, yards_after_catch=1.0)]
    profiles = trailing_receiving_profiles(pd.DataFrame(rows), target_week=2)
    assert "GHOST" not in profiles


def test_aggregate_trailing_receiving_profile_returns_a_dataframe():
    rows = [_target(1, "GB", "WR5", "Wide Five", 1, air_yards=5.0, complete=True, yards_after_catch=1.0)]
    df = aggregate_trailing_receiving_profile(pd.DataFrame(rows), target_week=2)
    assert set(df["player_id"]) == {"WR5"}
    assert "trailing_adot" in df.columns
