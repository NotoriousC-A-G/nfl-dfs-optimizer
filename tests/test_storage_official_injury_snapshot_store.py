from nfl_dfs.ingestion.official_injury_report import OfficialInjuryReportEntry
from nfl_dfs.storage.official_injury_snapshot_store import OfficialInjurySnapshot, latest_snapshot, read_snapshots, write_snapshot


def _e(gsis="00-1", practice="Full Participation in Practice", week=5):
    return OfficialInjuryReportEntry(gsis, 2026, week, "NYJ", "T", "Some Tackle", None, practice, None)


def test_each_capture_is_its_own_file_so_the_weekly_trajectory_is_kept(tmp_path):
    write_snapshot(OfficialInjurySnapshot("2026-10-08T14:00:00+00:00", 2026, 5, [_e(practice="Full Participation in Practice")]), root=tmp_path)
    write_snapshot(OfficialInjurySnapshot("2026-10-09T18:30:00+00:00", 2026, 5, [_e(practice="Limited Participation in Practice")]), root=tmp_path)
    snaps = read_snapshots(2026, 5, root=tmp_path)
    assert [s.entries[0].practice_status for s in snaps] == ["Full Participation in Practice", "Limited Participation in Practice"]
    assert latest_snapshot(2026, 5, root=tmp_path).fetched_at == "2026-10-09T18:30:00+00:00"


def test_snapshots_for_other_weeks_are_excluded_and_missing_dir_is_empty(tmp_path):
    write_snapshot(OfficialInjurySnapshot("2026-10-01T14:00:00+00:00", 2026, 4, [_e(week=4)]), root=tmp_path)
    assert read_snapshots(2026, 5, root=tmp_path) == []
    assert latest_snapshot(2026, 5, root=tmp_path) is None
    assert read_snapshots(2026, 5, root=tmp_path / "missing") == []
