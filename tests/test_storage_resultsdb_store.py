import json

import pandas as pd
import pytest

from nfl_dfs.ingestion.rotogrinders_resultsdb import ContestSummary, LineupRow, PlayerExposureRow, UserExposureRow
from nfl_dfs.storage.resultsdb_store import (
    STATUS_FETCHED,
    STATUS_NO_PRIMARY_CONTEST,
    curated_lineups_path,
    has_curated_lineups,
    has_raw_contest_data,
    read_curated_contests,
    read_curated_lineups,
    read_curated_player_exposures,
    read_curated_user_exposures,
    read_raw_contest_data,
    write_curated_contest,
    write_curated_lineups,
    write_raw_contest_data,
)


def _summary(contest_id: int = 1) -> ContestSummary:
    return ContestSummary(
        contest_id=contest_id,
        contest_name="NFL $2.5M Fantasy Football Millionaire",
        contest_date="2024-09-08",
        entry_cost=20.0,
        contest_size=28029,
        cash_line=100,
        duplicate_lineups=5,
        unique_lineups=27000,
        total_prizes=2_500_000.0,
    )


def _player_row() -> PlayerExposureRow:
    return PlayerExposureRow(
        player_key="1:0",
        player_id=1,
        full_name="Test Player",
        position="WR",
        team="KC",
        salary=8000,
        projected_points=18.5,
        actual_points=22.1,
        stat_details="7 Rec, 90 RecYds",
        made_cut=1,
        ownership_overall=12.3,
        ownership_top20=15.0,
        ownership_top10=18.0,
        ownership_top1=25.0,
    )


def _user_row() -> UserExposureRow:
    return UserExposureRow(
        username="tester",
        total_rosters=10,
        unique_rosters=9,
        total_players=40,
        max_exposure=30.0,
        roi=-50.0,
    )


# ---------------------------------------------------------------------------
# Raw storage: resumability is a pure filesystem check
# ---------------------------------------------------------------------------


def test_has_raw_contest_data_false_when_nothing_written(tmp_path):
    assert has_raw_contest_data("2024-09-08", base_dir=tmp_path) is False


def test_write_then_has_raw_contest_data_true(tmp_path):
    write_raw_contest_data(
        "2024-09-08",
        STATUS_FETCHED,
        contest_id=1,
        contest_name="Millionaire",
        payload={"players": {}, "users": {}, "contest": {}},
        fetched_at="2026-09-13T00:00:00",
        base_dir=tmp_path,
    )
    assert has_raw_contest_data("2024-09-08", base_dir=tmp_path) is True


def test_write_raw_contest_data_round_trips(tmp_path):
    payload = {"players": {"1:0": {"fullName": "X"}}, "users": {}, "contest": {}}
    write_raw_contest_data(
        "2024-09-08", STATUS_FETCHED, contest_id=42, contest_name="Millionaire", payload=payload, base_dir=tmp_path
    )
    envelope = read_raw_contest_data("2024-09-08", base_dir=tmp_path)
    assert envelope["status"] == STATUS_FETCHED
    assert envelope["contest_id"] == 42
    assert envelope["payload"] == payload


def test_write_raw_contest_data_no_primary_contest_needs_no_payload(tmp_path):
    write_raw_contest_data("2024-09-12", STATUS_NO_PRIMARY_CONTEST, base_dir=tmp_path)
    assert has_raw_contest_data("2024-09-12", base_dir=tmp_path) is True
    envelope = read_raw_contest_data("2024-09-12", base_dir=tmp_path)
    assert envelope["payload"] is None


def test_write_raw_contest_data_rejects_unknown_status(tmp_path):
    with pytest.raises(ValueError, match="status must be one of"):
        write_raw_contest_data("2024-09-08", "bogus", base_dir=tmp_path)


def test_write_raw_contest_data_fetched_requires_payload(tmp_path):
    with pytest.raises(ValueError, match="requires a payload"):
        write_raw_contest_data("2024-09-08", STATUS_FETCHED, contest_id=1, base_dir=tmp_path)


def test_raw_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    write_raw_contest_data(
        "2024-09-08", STATUS_FETCHED, contest_id=1, payload={"players": {}, "users": {}, "contest": {}}, base_dir=tmp_path
    )
    leftover_tmp = list(tmp_path.glob("*.tmp"))
    assert leftover_tmp == []
    # the file that does exist must actually parse -- guards against a half-written file being mistaken
    # for a valid resumability marker.
    final = list(tmp_path.glob("*.json"))
    assert len(final) == 1
    json.loads(final[0].read_text())


# ---------------------------------------------------------------------------
# Curated storage
# ---------------------------------------------------------------------------


def test_write_curated_contest_writes_all_three_tables_and_reads_back(tmp_path):
    written = write_curated_contest(
        "2024-09-08", 2024, 1, _summary(), [_player_row()], [_user_row()], base_dir=tmp_path
    )
    assert set(written) == {"contests", "player_exposures", "user_exposures"}
    for path in written.values():
        assert path.exists()

    contests_df = read_curated_contests(base_dir=tmp_path)
    assert len(contests_df) == 1
    assert contests_df.iloc[0]["contest_id"] == 1
    assert contests_df.iloc[0]["season"] == 2024

    players_df = read_curated_player_exposures(base_dir=tmp_path)
    assert len(players_df) == 1
    assert players_df.iloc[0]["full_name"] == "Test Player"

    users_df = read_curated_user_exposures(base_dir=tmp_path)
    assert len(users_df) == 1
    assert users_df.iloc[0]["username"] == "tester"


def test_write_curated_contest_without_user_exposures_omits_that_table(tmp_path):
    written = write_curated_contest("2024-09-08", 2024, 1, _summary(), [_player_row()], base_dir=tmp_path)
    assert set(written) == {"contests", "player_exposures"}


def test_write_curated_contest_is_idempotent_on_rerun(tmp_path):
    write_curated_contest("2024-09-08", 2024, 1, _summary(), [_player_row(), _player_row()], base_dir=tmp_path)
    write_curated_contest("2024-09-08", 2024, 1, _summary(), [_player_row()], base_dir=tmp_path)

    players_df = read_curated_player_exposures(base_dir=tmp_path)
    # Re-running the same date overwrites its own file rather than appending -- exactly one player row
    # survives, not three.
    assert len(players_df) == 1


def test_read_curated_player_exposures_concatenates_across_dates_and_filters_by_season(tmp_path):
    write_curated_contest("2024-09-08", 2024, 1, _summary(1), [_player_row()], base_dir=tmp_path)
    write_curated_contest("2024-09-15", 2024, 2, _summary(2), [_player_row()], base_dir=tmp_path)
    write_curated_contest("2023-09-10", 2023, 3, _summary(3), [_player_row()], base_dir=tmp_path)

    all_rows = read_curated_player_exposures(base_dir=tmp_path)
    assert len(all_rows) == 3

    only_2024 = read_curated_player_exposures(season=2024, base_dir=tmp_path)
    assert len(only_2024) == 2
    assert set(only_2024["date"]) == {"2024-09-08", "2024-09-15"}


def test_read_curated_player_exposures_empty_when_nothing_written(tmp_path):
    df = read_curated_player_exposures(base_dir=tmp_path)
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def _lineup_row(lineup_ct: int = 1) -> LineupRow:
    return LineupRow(
        lineup_hash="1:2:3:4:5:6:7:8:9",
        lineup_ct=lineup_ct,
        lineup_user_ct=1,
        lineup_players={"QB1": 1, "RB1": 2, "RB2": 3, "WR1": 4, "WR2": 5, "WR3": 6, "TE1": 7, "FLEX1": 8, "DST1": 9},
        points=200.0,
        total_salary=50000,
        total_own=50.0,
        min_own=5.0,
        max_own=15.0,
        avg_own=10.0,
        lineup_rank=1,
        is_cashing=True,
        payout=100.0,
        lineup_percentile=99.9,
        favorite_ct=5,
        underdog_ct=4,
        home_ct=5,
        visitor_ct=4,
        correlated_players=3,
        team_stacks={"2237": ["1:0", "4:0"]},
        game_stacks={"9999": [1, 4, 5]},
        lineup_trends={"qbPairedWithPassCatcher": True, "rbPairedWithDefense": False},
        entry_name_list=["tester1", "tester2"],
    )


def test_has_curated_lineups_false_when_nothing_written(tmp_path):
    assert has_curated_lineups("2024-09-08", 2024, base_dir=tmp_path) is False


def test_write_curated_lineups_round_trips_nested_fields_as_real_dicts(tmp_path):
    write_curated_lineups("2024-09-08", 2024, 1, [_lineup_row(), _lineup_row(lineup_ct=3)], base_dir=tmp_path)
    assert has_curated_lineups("2024-09-08", 2024, base_dir=tmp_path) is True

    df = read_curated_lineups(base_dir=tmp_path)
    assert len(df) == 2
    assert set(df["contest_id"]) == {1}
    assert sorted(df["lineup_ct"]) == [1, 3]

    row = df.iloc[0]
    # Nested fields are JSON-string-encoded on disk (pyarrow struct-schema-union risk, see module
    # docstring) but must come back as real Python dicts/lists from read_curated_lineups, not
    # left as raw JSON strings for every caller to decode themselves.
    assert isinstance(row["lineup_players"], dict)
    assert row["lineup_players"] == {
        "QB1": 1, "RB1": 2, "RB2": 3, "WR1": 4, "WR2": 5, "WR3": 6, "TE1": 7, "FLEX1": 8, "DST1": 9,
    }
    assert isinstance(row["team_stacks"], dict)
    assert row["team_stacks"] == {"2237": ["1:0", "4:0"]}
    assert isinstance(row["lineup_trends"], dict)
    assert row["lineup_trends"]["qbPairedWithPassCatcher"] is True
    assert list(row["entry_name_list"]) == ["tester1", "tester2"]


def test_write_curated_lineups_is_idempotent_on_rerun(tmp_path):
    write_curated_lineups("2024-09-08", 2024, 1, [_lineup_row(), _lineup_row(), _lineup_row()], base_dir=tmp_path)
    write_curated_lineups("2024-09-08", 2024, 1, [_lineup_row()], base_dir=tmp_path)
    df = read_curated_lineups(base_dir=tmp_path)
    assert len(df) == 1


def test_read_curated_lineups_concatenates_across_dates_and_filters_by_season(tmp_path):
    write_curated_lineups("2024-09-08", 2024, 1, [_lineup_row()], base_dir=tmp_path)
    write_curated_lineups("2023-09-10", 2023, 2, [_lineup_row()], base_dir=tmp_path)

    assert len(read_curated_lineups(base_dir=tmp_path)) == 2
    only_2024 = read_curated_lineups(season=2024, base_dir=tmp_path)
    assert len(only_2024) == 1
    assert only_2024.iloc[0]["date"] == "2024-09-08"


def test_read_curated_lineups_empty_when_nothing_written(tmp_path):
    df = read_curated_lineups(base_dir=tmp_path)
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_curated_lineups_path_shape(tmp_path):
    path = curated_lineups_path("2024-09-08", 2024, base_dir=tmp_path)
    assert path == tmp_path / "season=2024" / "lineups" / "2024-09-08.parquet"
