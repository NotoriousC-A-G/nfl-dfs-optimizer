from nfl_dfs.storage.odds_api_props_store import (
    has_snapshot,
    list_snapshot_dates,
    read_snapshot,
    read_snapshots_for_week,
    snapshot_path,
    write_snapshot,
)


def _event(event_id: str) -> dict:
    return {"id": event_id, "bookmakers": []}


def test_has_snapshot_false_when_nothing_written(tmp_path):
    assert has_snapshot(2026, 5, "2026-09-30", base_dir=tmp_path) is False


def test_write_then_has_snapshot_true(tmp_path):
    write_snapshot(2026, 5, "2026-09-30", [_event("e1")], fetched_at="2026-09-30T12:00:00Z", base_dir=tmp_path)
    assert has_snapshot(2026, 5, "2026-09-30", base_dir=tmp_path) is True


def test_write_snapshot_round_trips(tmp_path):
    events = [_event("e1"), _event("e2")]
    write_snapshot(2026, 5, "2026-09-30", events, fetched_at="2026-09-30T12:00:00Z", base_dir=tmp_path)

    snapshot = read_snapshot(2026, 5, "2026-09-30", base_dir=tmp_path)
    assert snapshot.season == 2026
    assert snapshot.week == 5
    assert snapshot.date == "2026-09-30"
    assert snapshot.fetched_at == "2026-09-30T12:00:00Z"
    assert snapshot.events == events


def test_write_snapshot_same_key_overwrites_not_appends(tmp_path):
    write_snapshot(2026, 5, "2026-09-30", [_event("e1")], fetched_at="t1", base_dir=tmp_path)
    write_snapshot(2026, 5, "2026-09-30", [_event("e2")], fetched_at="t2", base_dir=tmp_path)

    snapshot = read_snapshot(2026, 5, "2026-09-30", base_dir=tmp_path)
    assert [e["id"] for e in snapshot.events] == ["e2"]
    assert snapshot.fetched_at == "t2"


def test_different_week_same_date_does_not_collide(tmp_path):
    write_snapshot(2026, 5, "2026-09-30", [_event("wk5")], fetched_at="t", base_dir=tmp_path)
    write_snapshot(2026, 6, "2026-09-30", [_event("wk6")], fetched_at="t", base_dir=tmp_path)

    assert [e["id"] for e in read_snapshot(2026, 5, "2026-09-30", base_dir=tmp_path).events] == ["wk5"]
    assert [e["id"] for e in read_snapshot(2026, 6, "2026-09-30", base_dir=tmp_path).events] == ["wk6"]


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    write_snapshot(2026, 5, "2026-09-30", [_event("e1")], fetched_at="t", base_dir=tmp_path)
    leftover_tmp = list(tmp_path.rglob("*.tmp"))
    assert leftover_tmp == []
    final = list(tmp_path.rglob("*.json"))
    assert [p.name for p in final] == ["2026-09-30.json"]


def test_list_snapshot_dates_empty_when_no_directory(tmp_path):
    assert list_snapshot_dates(2026, 5, base_dir=tmp_path) == []


def test_list_snapshot_dates_sorted(tmp_path):
    write_snapshot(2026, 5, "2026-09-30", [_event("e1")], fetched_at="t", base_dir=tmp_path)
    write_snapshot(2026, 5, "2026-09-28", [_event("e1")], fetched_at="t", base_dir=tmp_path)
    write_snapshot(2026, 5, "2026-09-29", [_event("e1")], fetched_at="t", base_dir=tmp_path)
    assert list_snapshot_dates(2026, 5, base_dir=tmp_path) == ["2026-09-28", "2026-09-29", "2026-09-30"]


def test_read_snapshots_for_week_filters_by_season_and_week(tmp_path):
    write_snapshot(2026, 5, "2026-09-28", [_event("a")], fetched_at="t", base_dir=tmp_path)
    write_snapshot(2026, 5, "2026-09-30", [_event("b")], fetched_at="t", base_dir=tmp_path)
    write_snapshot(2026, 6, "2026-10-05", [_event("c")], fetched_at="t", base_dir=tmp_path)  # different week
    write_snapshot(2025, 5, "2025-09-28", [_event("d")], fetched_at="t", base_dir=tmp_path)  # different season

    result = read_snapshots_for_week(2026, 5, base_dir=tmp_path)
    assert [s.date for s in result] == ["2026-09-28", "2026-09-30"]


def test_read_snapshots_for_week_empty_when_none_match(tmp_path):
    write_snapshot(2026, 5, "2026-09-28", [_event("a")], fetched_at="t", base_dir=tmp_path)
    assert read_snapshots_for_week(2026, 9, base_dir=tmp_path) == []


def test_snapshot_path_uses_default_root_when_base_dir_omitted():
    path = snapshot_path(2026, 5, "2026-09-30")
    assert path.name == "2026-09-30.json"
    assert "odds_api_props" in path.parts
    assert "2026" in path.parts
    assert "week5" in path.parts
