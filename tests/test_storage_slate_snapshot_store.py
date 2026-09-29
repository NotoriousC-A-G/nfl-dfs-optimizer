from dataclasses import dataclass
from datetime import datetime, timezone

from nfl_dfs.storage.slate_snapshot_store import (
    list_slate_snapshots,
    load_latest_slate_snapshot,
    load_slate_snapshot,
    save_slate_snapshot,
)


@dataclass(frozen=True)
class _FakePlayerDetail:
    canonical_id: str
    display_name: str
    salary: int
    team_set: frozenset


@dataclass(frozen=True)
class _FakeAgentResult:
    agent_id: str
    lineup_salary: int


@dataclass(frozen=True)
class _FakeStackProfile:
    home_team: str
    away_team: str


def _player(name="Justin Jefferson", canonical_id="jj1") -> _FakePlayerDetail:
    return _FakePlayerDetail(canonical_id=canonical_id, display_name=name, salary=7800, team_set=frozenset({"MIN", "CHI"}))


def _agent(agent_id="chalk_anchor") -> _FakeAgentResult:
    return _FakeAgentResult(agent_id=agent_id, lineup_salary=49900)


def _profile(home="KC", away="BUF") -> _FakeStackProfile:
    return _FakeStackProfile(home_team=home, away_team=away)


def test_load_latest_returns_none_when_nothing_saved(tmp_path):
    assert load_latest_slate_snapshot(2026, 2, base_dir=tmp_path) is None


def test_save_then_load_latest_round_trips_all_three_sections(tmp_path):
    save_slate_snapshot(
        2026, 2,
        player_details=[_player()],
        agent_results=[_agent()],
        stack_profiles=[_profile()],
        timestamp="120000",
        base_dir=tmp_path,
    )

    snapshot = load_latest_slate_snapshot(2026, 2, base_dir=tmp_path)
    assert snapshot["season"] == 2026
    assert snapshot["week"] == 2
    assert snapshot["player_pool"] == [
        {"canonical_id": "jj1", "display_name": "Justin Jefferson", "salary": 7800, "team_set": ["CHI", "MIN"]}
    ]
    assert snapshot["agent_lineups"] == [{"agent_id": "chalk_anchor", "lineup_salary": 49900}]
    assert snapshot["stack_profiles"] == [{"home_team": "KC", "away_team": "BUF"}]


def test_frozenset_field_serializes_as_sorted_list_not_a_crash(tmp_path):
    save_slate_snapshot(
        2026, 2,
        player_details=[_player()],
        agent_results=[],
        stack_profiles=[],
        timestamp="120000",
        base_dir=tmp_path,
    )
    snapshot = load_latest_slate_snapshot(2026, 2, base_dir=tmp_path)
    assert snapshot["player_pool"][0]["team_set"] == ["CHI", "MIN"]


def test_multiple_runs_same_week_each_get_their_own_file(tmp_path):
    save_slate_snapshot(2026, 2, player_details=[], agent_results=[], stack_profiles=[], timestamp="090000", base_dir=tmp_path)
    save_slate_snapshot(2026, 2, player_details=[_player()], agent_results=[], stack_profiles=[], timestamp="153000", base_dir=tmp_path)

    files = list_slate_snapshots(2026, 2, base_dir=tmp_path)
    assert [f.name for f in files] == ["2026-02_090000.json", "2026-02_153000.json"]


def test_load_latest_picks_the_most_recent_run(tmp_path):
    save_slate_snapshot(2026, 2, player_details=[], agent_results=[], stack_profiles=[], timestamp="090000", base_dir=tmp_path)
    save_slate_snapshot(2026, 2, player_details=[_player()], agent_results=[], stack_profiles=[], timestamp="153000", base_dir=tmp_path)

    latest = load_latest_slate_snapshot(2026, 2, base_dir=tmp_path)
    assert len(latest["player_pool"]) == 1


def test_load_latest_survives_a_run_from_an_earlier_day_with_a_later_clock_time(tmp_path, monkeypatch):
    """Regression for a real, live bug (2026-09-29): a run from an EARLIER calendar day at a LATER
    clock time (day 1, 16:45 -> filename suffix "164516") must not outrank a run from a LATER
    calendar day at an EARLIER clock time (day 2, 09:49 -> "094953") just because "164516" sorts
    as a bigger string than "094953". The real bug shipped a full day-old snapshot into a real
    postmortem's per-player ownership tags -- this pins the fix (sort by the embedded real
    timestamp, not the filename)."""
    import nfl_dfs.storage.slate_snapshot_store as mod

    class _FakeDateTime:
        _now: datetime

        @classmethod
        def now(cls, tz):
            return cls._now

    monkeypatch.setattr(mod, "datetime", _FakeDateTime)

    _FakeDateTime._now = datetime(2026, 9, 26, 16, 45, 16, tzinfo=timezone.utc)
    save_slate_snapshot(
        2026, 3, player_details=[_player("Day1 Guy")], agent_results=[], stack_profiles=[], timestamp="164516", base_dir=tmp_path
    )

    _FakeDateTime._now = datetime(2026, 9, 27, 9, 49, 53, tzinfo=timezone.utc)
    save_slate_snapshot(
        2026, 3, player_details=[_player("Day2 Guy")], agent_results=[], stack_profiles=[], timestamp="094953", base_dir=tmp_path
    )

    latest = load_latest_slate_snapshot(2026, 3, base_dir=tmp_path)
    assert latest["player_pool"][0]["display_name"] == "Day2 Guy"


def test_different_weeks_do_not_collide(tmp_path):
    save_slate_snapshot(2026, 1, player_details=[_player("Week 1 Guy")], agent_results=[], stack_profiles=[], timestamp="090000", base_dir=tmp_path)
    save_slate_snapshot(2026, 2, player_details=[_player("Week 2 Guy")], agent_results=[], stack_profiles=[], timestamp="090000", base_dir=tmp_path)

    week_1 = load_latest_slate_snapshot(2026, 1, base_dir=tmp_path)
    week_2 = load_latest_slate_snapshot(2026, 2, base_dir=tmp_path)
    assert week_1["player_pool"][0]["display_name"] == "Week 1 Guy"
    assert week_2["player_pool"][0]["display_name"] == "Week 2 Guy"


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    save_slate_snapshot(2026, 2, player_details=[], agent_results=[], stack_profiles=[], timestamp="120000", base_dir=tmp_path)
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("*.json")) != []


def test_load_slate_snapshot_reads_one_specific_file_by_path(tmp_path):
    path = save_slate_snapshot(2026, 2, player_details=[_player()], agent_results=[], stack_profiles=[], timestamp="120000", base_dir=tmp_path)
    snapshot = load_slate_snapshot(path)
    assert snapshot["snapshot_filename"] == "2026-02_120000.json"


def test_non_dataclass_records_pass_through_as_plain_dicts(tmp_path):
    # A caller might already have plain dicts (e.g. re-loading from another snapshot) -- must
    # not require every record to be a dataclass instance.
    save_slate_snapshot(
        2026, 2,
        player_details=[{"already": "a dict"}],
        agent_results=[],
        stack_profiles=[],
        timestamp="120000",
        base_dir=tmp_path,
    )
    snapshot = load_latest_slate_snapshot(2026, 2, base_dir=tmp_path)
    assert snapshot["player_pool"] == [{"already": "a dict"}]
