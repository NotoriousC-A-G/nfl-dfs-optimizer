from nfl_dfs.ingestion.rotogrinders_injuries import InjuryReportEntry
from nfl_dfs.storage.injury_snapshot_store import (
    has_snapshot,
    list_snapshot_dates,
    read_snapshot,
    read_snapshots_for_week,
    snapshot_path,
    write_snapshot,
)


def _entry(player_id: str, status: str = "Q") -> InjuryReportEntry:
    return InjuryReportEntry(
        rotogrinders_player_id=player_id,
        name=f"Player {player_id}",
        team="GB",
        position="WR",
        status=status,
        body_part="Knee",
        impact_rating=5,
    )


def test_has_snapshot_false_when_nothing_written(tmp_path):
    assert has_snapshot("2026-09-15", base_dir=tmp_path) is False


def test_write_then_has_snapshot_true(tmp_path):
    write_snapshot("2026-09-15", 2026, 2, [_entry("p1")], fetched_at="2026-09-15T12:00:00Z", base_dir=tmp_path)
    assert has_snapshot("2026-09-15", base_dir=tmp_path) is True


def test_write_snapshot_round_trips(tmp_path):
    entries = [_entry("p1", "O"), _entry("p2", "Q")]
    write_snapshot("2026-09-15", 2026, 2, entries, fetched_at="2026-09-15T12:00:00Z", base_dir=tmp_path)

    snapshot = read_snapshot("2026-09-15", base_dir=tmp_path)
    assert snapshot.date == "2026-09-15"
    assert snapshot.season == 2026
    assert snapshot.target_week == 2
    assert snapshot.fetched_at == "2026-09-15T12:00:00Z"
    assert snapshot.entries == entries


def test_write_snapshot_same_date_overwrites_not_appends(tmp_path):
    write_snapshot("2026-09-15", 2026, 2, [_entry("p1")], fetched_at="2026-09-15T08:00:00Z", base_dir=tmp_path)
    write_snapshot("2026-09-15", 2026, 2, [_entry("p2")], fetched_at="2026-09-15T20:00:00Z", base_dir=tmp_path)

    snapshot = read_snapshot("2026-09-15", base_dir=tmp_path)
    assert [e.rotogrinders_player_id for e in snapshot.entries] == ["p2"]
    assert snapshot.fetched_at == "2026-09-15T20:00:00Z"


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    write_snapshot("2026-09-15", 2026, 2, [_entry("p1")], fetched_at="2026-09-15T12:00:00Z", base_dir=tmp_path)
    leftover_tmp = list(tmp_path.glob("*.tmp"))
    assert leftover_tmp == []
    final = list(tmp_path.glob("*.json"))
    assert [p.name for p in final] == ["2026-09-15.json"]


def test_list_snapshot_dates_empty_directory(tmp_path):
    assert list_snapshot_dates(base_dir=tmp_path) == []


def test_list_snapshot_dates_sorted(tmp_path):
    write_snapshot("2026-09-17", 2026, 2, [_entry("p1")], fetched_at="t", base_dir=tmp_path)
    write_snapshot("2026-09-15", 2026, 2, [_entry("p1")], fetched_at="t", base_dir=tmp_path)
    write_snapshot("2026-09-16", 2026, 2, [_entry("p1")], fetched_at="t", base_dir=tmp_path)
    assert list_snapshot_dates(base_dir=tmp_path) == ["2026-09-15", "2026-09-16", "2026-09-17"]


def test_read_snapshots_for_week_filters_by_season_and_week(tmp_path):
    write_snapshot("2026-09-15", 2026, 2, [_entry("p1")], fetched_at="t", base_dir=tmp_path)
    write_snapshot("2026-09-16", 2026, 2, [_entry("p1")], fetched_at="t", base_dir=tmp_path)
    write_snapshot("2026-09-08", 2026, 1, [_entry("p1")], fetched_at="t", base_dir=tmp_path)  # different week
    write_snapshot("2025-09-15", 2025, 2, [_entry("p1")], fetched_at="t", base_dir=tmp_path)  # different season

    result = read_snapshots_for_week(2026, 2, base_dir=tmp_path)
    assert [s.date for s in result] == ["2026-09-15", "2026-09-16"]


def test_read_snapshots_for_week_empty_when_none_match(tmp_path):
    write_snapshot("2026-09-08", 2026, 1, [_entry("p1")], fetched_at="t", base_dir=tmp_path)
    assert read_snapshots_for_week(2026, 5, base_dir=tmp_path) == []


def test_snapshot_path_uses_default_root_when_base_dir_omitted():
    path = snapshot_path("2026-09-15")
    assert path.name == "2026-09-15.json"
    assert "injury_snapshots" in path.parts
