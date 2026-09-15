import json
from pathlib import Path

import pandas as pd
import pytest
import requests

from nfl_dfs.ingestion.resultsdb_backfill import (
    BACKOFF_BASE_SECONDS,
    MAX_RETRIES,
    REQUEST_DELAY_SECONDS,
    DateOutcome,
    enumerate_game_dates,
    process_date,
    process_lineups_date,
    run_backfill,
    run_lineups_backfill,
)
from nfl_dfs.ingestion.rotogrinders_resultsdb import ContestDataUnavailableError, DraftGroupSource, LineupRow, LiveContest
from nfl_dfs.storage.resultsdb_store import has_curated_lineups, has_raw_contest_data, read_curated_lineups, read_curated_player_exposures

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class _RecordingSleep:
    """Fake `sleep_fn` that records delays instead of actually sleeping -- keeps the unit suite fast."""

    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# ---------------------------------------------------------------------------
# enumerate_game_dates -- pure, no network
# ---------------------------------------------------------------------------


def _fixture_schedule() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"season": 2023, "game_type": "REG", "gameday": "2023-09-10", "week": 1},
            {"season": 2023, "game_type": "REG", "gameday": "2023-09-10", "week": 1},  # dup, same slate
            {"season": 2023, "game_type": "REG", "gameday": "2023-09-14", "week": 2},
            {"season": 2023, "game_type": "POST", "gameday": "2024-01-13", "week": 19},
            {"season": 2024, "game_type": "REG", "gameday": "2024-09-08", "week": 1},
            {"season": 2024, "game_type": "REG", "gameday": "2024-09-05", "week": 1},
        ]
    )


def test_enumerate_game_dates_dedupes_and_sorts_within_a_season():
    dates = enumerate_game_dates(_fixture_schedule(), [2023])
    assert dates == ["2023-09-10", "2023-09-14"]


def test_enumerate_game_dates_excludes_postseason_by_default():
    dates = enumerate_game_dates(_fixture_schedule(), [2023])
    assert "2024-01-13" not in dates


def test_enumerate_game_dates_can_include_postseason_explicitly():
    dates = enumerate_game_dates(_fixture_schedule(), [2023], game_types=("REG", "POST"))
    assert "2024-01-13" in dates


def test_enumerate_game_dates_filters_by_requested_seasons_only():
    dates = enumerate_game_dates(_fixture_schedule(), [2024])
    assert dates == ["2024-09-05", "2024-09-08"]


def test_enumerate_game_dates_multiple_seasons_combined_and_sorted():
    dates = enumerate_game_dates(_fixture_schedule(), [2023, 2024])
    assert dates == ["2023-09-10", "2023-09-14", "2024-09-05", "2024-09-08"]


# ---------------------------------------------------------------------------
# process_date: resumability, success path, sentinel outcomes, retry/backoff
# ---------------------------------------------------------------------------


def _draft_group(date: str = "2024-09-08", game_count: int = 15, contest_suffix: str = "") -> DraftGroupSource:
    # `contest_start_date` deliberately mirrors `date` -- the real API returns a whole week's worth of
    # draft groups regardless of which date is queried (see the module docstring/comment above
    # `same_day_main` in resultsdb_backfill.py), so tests exercise the same day-matching filter the real
    # orchestrator relies on rather than assuming the first returned group is always the right one.
    return DraftGroupSource(contest_group_id=95302, contest_start_date=f"{date}T13:00:00", game_count=game_count, contest_suffix=contest_suffix)


def _millionaire_contest() -> LiveContest:
    return LiveContest(
        contest_id=147325139,
        contest_name="NFL $2.5M Fantasy Football Millionaire",
        contest_size=28029,
        entry_cost=20.0,
        total_prizes=2_500_000.0,
        multi_entry_max=150,
        is_primary=True,
        is_largest_by_size=False,
        cash_line=100,
    )


def _non_primary_contest() -> LiveContest:
    return LiveContest(
        contest_id=999,
        contest_name="NFL $5 Double Up",
        contest_size=100,
        entry_cost=5.0,
        total_prizes=500.0,
        multi_entry_max=1,
        is_primary=False,
        is_largest_by_size=False,
        cash_line=50,
    )


def test_process_date_skips_when_already_captured(tmp_path, monkeypatch):
    from nfl_dfs.storage import resultsdb_store

    resultsdb_store.write_raw_contest_data("2024-09-08", resultsdb_store.STATUS_FETCHED, contest_id=1, payload={"players": {}, "users": {}, "contest": {}}, base_dir=tmp_path)

    def _boom(*a, **k):
        raise AssertionError("should not make a live call for an already-captured date")

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", _boom)

    outcome = process_date("2024-09-08", 2024, base_dir=tmp_path, sleep_fn=_RecordingSleep())
    assert outcome.status == "skipped"


def test_process_date_fetches_writes_raw_and_curated_and_sleeps_between_calls(tmp_path, monkeypatch):
    contest_payload = _load("resultsdb_contest_data.json")
    sleeper = _RecordingSleep()

    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", lambda date, session=None: [_draft_group()]
    )
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_live_contests",
        lambda group_id, session=None: [_millionaire_contest(), _non_primary_contest()],
    )
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_data",
        lambda date, contest_id, session=None: contest_payload,
    )

    outcome = process_date("2024-09-08", 2024, base_dir=tmp_path, sleep_fn=sleeper)

    assert outcome.status == "fetched"
    assert outcome.contest_id == 147325139
    assert outcome.player_count > 0
    assert has_raw_contest_data("2024-09-08", base_dir=tmp_path) is True

    players_df = read_curated_player_exposures(base_dir=tmp_path)
    assert len(players_df) == outcome.player_count

    # Three successful live calls (contest-sources, live-contests, contest-data) -> three
    # good-citizen delays of REQUEST_DELAY_SECONDS, no backoff delays mixed in.
    assert sleeper.calls == [REQUEST_DELAY_SECONDS] * 3


def test_process_date_no_primary_contest_writes_sentinel_and_future_run_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", lambda date, session=None: [_draft_group(date)]
    )
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_live_contests",
        lambda group_id, session=None: [_non_primary_contest()],  # no is_primary/150 match at all
    )

    outcome = process_date("2024-09-12", 2024, base_dir=tmp_path, sleep_fn=_RecordingSleep())
    assert outcome.status == "no_primary_contest"
    assert has_raw_contest_data("2024-09-12", base_dir=tmp_path) is True

    # Resumed run must skip without calling fetch_contest_sources again.
    def _boom(*a, **k):
        raise AssertionError("should not re-attempt a definitively-resolved date")

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", _boom)
    resumed = process_date("2024-09-12", 2024, base_dir=tmp_path, sleep_fn=_RecordingSleep())
    assert resumed.status == "skipped"


def test_process_date_no_draft_groups_writes_sentinel(tmp_path, monkeypatch):
    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", lambda date, session=None: [])

    outcome = process_date("2024-07-04", 2024, base_dir=tmp_path, sleep_fn=_RecordingSleep())
    assert outcome.status == "no_draft_groups"
    assert has_raw_contest_data("2024-07-04", base_dir=tmp_path) is True


def test_process_date_prefers_same_day_empty_suffix_group_over_bigger_cross_week_group(tmp_path, monkeypatch):
    """Real live quirk (found running this orchestrator against 2024): `contest-sources` returns a whole
    week's worth of draft groups regardless of which date is queried, and a `(Thu-Mon)`/`(Mon-Thu)`
    cross-week combined-slate group can have a much larger `game_count` than the actual Sunday main
    slate. "Biggest game_count" alone would wrongly select the cross-week group and then fail to find a
    Millionaire Maker in it. The fix: only consider groups with an empty `contest_suffix` whose own
    `contest_start_date` falls on the exact date being processed.
    """
    sunday = "2024-09-08"
    main_slate = _draft_group(sunday, game_count=12, contest_suffix="")
    cross_week = DraftGroupSource(
        contest_group_id=112863, contest_start_date="2024-09-12T20:15:00", game_count=16, contest_suffix="(Thu-Mon)"
    )
    seen_group_ids = []

    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources",
        lambda date, session=None: [cross_week, main_slate],
    )

    def _contests(group_id, session=None):
        seen_group_ids.append(group_id)
        return [_millionaire_contest()]

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_live_contests", _contests)
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_data",
        lambda date, contest_id, session=None: _load("resultsdb_contest_data.json"),
    )

    outcome = process_date(sunday, 2024, base_dir=tmp_path, sleep_fn=_RecordingSleep())

    assert outcome.status == "fetched"
    assert seen_group_ids == [main_slate.contest_group_id]  # never queried the bigger cross-week group


def test_process_date_thursday_night_has_no_main_slate_group(tmp_path, monkeypatch):
    """A Thursday-night-only date has no empty-suffix group starting that same day (the real main slate
    is Sunday's) -- this is a real, expected outcome, not an error, and gets its own sentinel status so
    it's distinguishable from `no_draft_groups` (contest-sources returned literally nothing).
    """
    thursday = "2024-09-05"
    week_groups = [
        DraftGroupSource(contest_group_id=1, contest_start_date="2024-09-05T20:20:00", game_count=16, contest_suffix="(Thu-Mon)"),
        _draft_group("2024-09-08", game_count=12, contest_suffix=""),
    ]
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", lambda date, session=None: week_groups
    )

    outcome = process_date(thursday, 2024, base_dir=tmp_path, sleep_fn=_RecordingSleep())

    assert outcome.status == "no_main_slate_group"
    assert has_raw_contest_data(thursday, base_dir=tmp_path) is True


def test_process_date_unavailable_contest_data_writes_sentinel_no_retry(tmp_path, monkeypatch):
    sleeper = _RecordingSleep()
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", lambda date, session=None: [_draft_group(date)]
    )
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_live_contests",
        lambda group_id, session=None: [_millionaire_contest()],
    )

    def _unavailable(date, contest_id, session=None):
        raise ContestDataUnavailableError(f"ResultsDB contest data unavailable for date={date} contest_id={contest_id} (status=403)")

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_data", _unavailable)

    outcome = process_date("2018-09-09", 2018, base_dir=tmp_path, sleep_fn=sleeper)
    assert outcome.status == "unavailable"
    assert has_raw_contest_data("2018-09-09", base_dir=tmp_path) is True
    # 403 is non-retryable -- no backoff delays, just the two normal per-request delays that preceded it.
    assert sleeper.calls == [REQUEST_DELAY_SECONDS, REQUEST_DELAY_SECONDS]


def _http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(f"{status_code} error", response=response)


def test_process_date_retries_transient_failure_then_succeeds(tmp_path, monkeypatch):
    contest_payload = _load("resultsdb_contest_data.json")
    sleeper = _RecordingSleep()
    calls = {"n": 0}

    def _flaky_sources(date, session=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(503)
        return [_draft_group()]

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", _flaky_sources)
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_live_contests",
        lambda group_id, session=None: [_millionaire_contest()],
    )
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_data",
        lambda date, contest_id, session=None: contest_payload,
    )

    outcome = process_date("2024-09-08", 2024, base_dir=tmp_path, sleep_fn=sleeper)

    assert outcome.status == "fetched"
    assert calls["n"] == 2
    # First delay is the backoff wait (base delay, attempt 1), then normal per-request delays follow.
    assert sleeper.calls[0] == BACKOFF_BASE_SECONDS
    assert REQUEST_DELAY_SECONDS in sleeper.calls


def test_process_date_exhausts_retries_marks_failed_with_no_sentinel(tmp_path, monkeypatch):
    def _always_503(date, session=None):
        raise _http_error(503)

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", _always_503)

    outcome = process_date("2024-09-08", 2024, base_dir=tmp_path, sleep_fn=_RecordingSleep())

    assert outcome.status == "failed"
    # Critically: no sentinel written -- a future run must retry this date, not skip it forever.
    assert has_raw_contest_data("2024-09-08", base_dir=tmp_path) is False


def test_process_date_non_retryable_http_error_fails_without_exhausting_retries(tmp_path, monkeypatch):
    call_count = {"n": 0}

    def _always_400(date, session=None):
        call_count["n"] += 1
        raise _http_error(400)

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", _always_400)

    outcome = process_date("2024-09-08", 2024, base_dir=tmp_path, sleep_fn=_RecordingSleep())

    assert outcome.status == "failed"
    assert call_count["n"] == 1  # not retried at all -- a plain 400 isn't 429/5xx
    assert has_raw_contest_data("2024-09-08", base_dir=tmp_path) is False


def test_process_date_unexpected_exception_does_not_crash_the_run(tmp_path, monkeypatch):
    def _boom(date, session=None):
        raise KeyError("some malformed payload field")

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", _boom)

    outcome = process_date("2024-09-08", 2024, base_dir=tmp_path, sleep_fn=_RecordingSleep())
    assert outcome.status == "failed"
    assert "unexpected error" in outcome.detail


# ---------------------------------------------------------------------------
# run_backfill: multi-date orchestration + resumability across two separate runs
# ---------------------------------------------------------------------------


def test_run_backfill_resumability_across_interrupted_and_resumed_runs(tmp_path, monkeypatch):
    contest_payload = _load("resultsdb_contest_data.json")
    dates = ["2024-09-05", "2024-09-08", "2024-09-15"]
    call_log: list[str] = []

    def _sources(date, session=None):
        call_log.append(f"sources:{date}")
        return [_draft_group(date)]

    def _contests(group_id, session=None):
        return [_millionaire_contest()]

    def _data(date, contest_id, session=None):
        # Simulate the process being killed partway through the second date.
        if date == "2024-09-08":
            raise KeyboardInterrupt("simulated kill mid-fetch")
        return contest_payload

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", _sources)
    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_live_contests", _contests)
    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_data", _data)

    with pytest.raises(KeyboardInterrupt):
        run_backfill([2024], dates_by_season={2024: dates}, base_dir=tmp_path, sleep_fn=_RecordingSleep(), progress=lambda msg: None)

    # First date captured, second killed mid-flight (no sentinel), third never reached.
    assert has_raw_contest_data("2024-09-05", base_dir=tmp_path) is True
    assert has_raw_contest_data("2024-09-08", base_dir=tmp_path) is False
    assert has_raw_contest_data("2024-09-15", base_dir=tmp_path) is False
    assert call_log == ["sources:2024-09-05", "sources:2024-09-08"]

    # Resume: fix the flakiness and re-run the same call. Must not re-fetch 2024-09-05.
    call_log.clear()

    def _data_fixed(date, contest_id, session=None):
        return contest_payload

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_data", _data_fixed)

    stats = run_backfill([2024], dates_by_season={2024: dates}, base_dir=tmp_path, sleep_fn=_RecordingSleep(), progress=lambda msg: None)

    assert call_log == ["sources:2024-09-08", "sources:2024-09-15"]  # 09-05 skipped entirely, no live call
    assert stats.skipped == 1
    assert stats.fetched == 2
    assert has_raw_contest_data("2024-09-08", base_dir=tmp_path) is True
    assert has_raw_contest_data("2024-09-15", base_dir=tmp_path) is True

    players_df = read_curated_player_exposures(season=2024, base_dir=tmp_path)
    assert set(players_df["date"]) == {"2024-09-05", "2024-09-08", "2024-09-15"}


def test_run_backfill_reports_progress_lines(tmp_path, monkeypatch):
    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_contest_sources", lambda date, session=None: [])
    messages = []

    run_backfill([2024], dates_by_season={2024: ["2024-07-04"]}, base_dir=tmp_path, sleep_fn=_RecordingSleep(), progress=messages.append)

    assert any("2024-07-04" in m for m in messages)
    assert any("Season 2024" in m for m in messages)


# ---------------------------------------------------------------------------
# lineups backfill (ADR-0032) -- a separate, later pass over already-resolved contests
# ---------------------------------------------------------------------------


def _lineup_row(lineup_ct: int = 1) -> LineupRow:
    return LineupRow(
        lineup_hash="1:2:3", lineup_ct=lineup_ct, lineup_user_ct=1,
        lineup_players={"QB1": 1}, points=200.0, total_salary=50000, total_own=50.0,
        min_own=5.0, max_own=15.0, avg_own=10.0, lineup_rank=1, is_cashing=True, payout=0.0,
        lineup_percentile=0.0, favorite_ct=1, underdog_ct=1, home_ct=1, visitor_ct=1,
        correlated_players=1, team_stacks={}, game_stacks={}, lineup_trends={}, entry_name_list=["x"],
    )


def _contests_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": "2024-09-08", "season": 2024, "contest_id": 1},
            {"date": "2024-09-15", "season": 2024, "contest_id": 2},
            {"date": "2023-09-10", "season": 2023, "contest_id": 3},
        ]
    )


def test_process_lineups_date_skips_when_already_captured(tmp_path, monkeypatch):
    from nfl_dfs.storage import resultsdb_store

    resultsdb_store.write_curated_lineups("2024-09-08", 2024, 1, [_lineup_row()], base_dir=tmp_path)

    def _boom(*a, **k):
        raise AssertionError("should not make a live call for an already-captured date")

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_lineups", _boom)

    outcome = process_lineups_date("2024-09-08", 2024, 1, base_dir=tmp_path, sleep_fn=_RecordingSleep())
    assert outcome.status == "skipped"


def test_process_lineups_date_fetches_and_writes_curated(tmp_path, monkeypatch):
    sleeper = _RecordingSleep()
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_lineups",
        lambda date, contest_id, session=None: [_lineup_row(), _lineup_row(lineup_ct=3)],
    )

    outcome = process_lineups_date("2024-09-08", 2024, 1, base_dir=tmp_path, sleep_fn=sleeper)

    assert outcome.status == "fetched"
    assert outcome.player_count == 2
    assert has_curated_lineups("2024-09-08", 2024, base_dir=tmp_path) is True
    df = read_curated_lineups(base_dir=tmp_path)
    assert len(df) == 2
    assert sleeper.calls == [REQUEST_DELAY_SECONDS]


def test_process_lineups_date_unavailable_writes_no_curated_table(tmp_path, monkeypatch):
    def _unavailable(date, contest_id, session=None):
        raise ContestDataUnavailableError(f"status=403 for date={date} contest_id={contest_id}")

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_lineups", _unavailable)

    outcome = process_lineups_date("2024-09-08", 2024, 1, base_dir=tmp_path, sleep_fn=_RecordingSleep())
    assert outcome.status == "unavailable"
    assert has_curated_lineups("2024-09-08", 2024, base_dir=tmp_path) is False


def test_run_lineups_backfill_walks_only_the_requested_season_and_skips_captured_dates(tmp_path, monkeypatch):
    from nfl_dfs.storage import resultsdb_store

    # 2024-09-08 already captured -- must not be re-fetched.
    resultsdb_store.write_curated_lineups("2024-09-08", 2024, 1, [_lineup_row()], base_dir=tmp_path)

    calls = []

    def _fake_fetch(date, contest_id, session=None):
        calls.append(date)
        return [_lineup_row()]

    monkeypatch.setattr("nfl_dfs.ingestion.resultsdb_backfill.fetch_lineups", _fake_fetch)

    stats = run_lineups_backfill(
        [2024], contests=_contests_df(), base_dir=tmp_path, sleep_fn=_RecordingSleep(), progress=lambda msg: None
    )

    # Only 2024-09-15 is new -- 2024-09-08 skipped, 2023-09-10 excluded (different season entirely).
    assert calls == ["2024-09-15"]
    assert stats.fetched == 1
    assert stats.skipped == 1
    assert has_curated_lineups("2024-09-15", 2024, base_dir=tmp_path) is True
    assert has_curated_lineups("2023-09-10", 2023, base_dir=tmp_path) is False


def test_run_lineups_backfill_reports_progress_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "nfl_dfs.ingestion.resultsdb_backfill.fetch_lineups", lambda date, contest_id, session=None: [_lineup_row()]
    )
    messages = []
    run_lineups_backfill(
        [2024], contests=_contests_df(), base_dir=tmp_path, sleep_fn=_RecordingSleep(), progress=messages.append
    )
    assert any("2024-09-08" in m for m in messages)
    assert any("Season 2024" in m for m in messages)
