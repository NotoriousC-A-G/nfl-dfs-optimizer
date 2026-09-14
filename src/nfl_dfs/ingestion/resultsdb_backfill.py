"""Backfill orchestrator for RotoGrinders ResultsDB (ADR-0024), walking real NFL game dates across
2020-2025 and writing raw/curated data via `nfl_dfs.storage.resultsdb_store`.

**Date enumeration is real, not guessed (ADR-0024).** `enumerate_game_dates` works off the actual DataFrame
shape `nfl_data_py.import_schedules()` returns (`season`, `game_type`, `gameday`) -- no assumption about
which weekdays NFL games fall on, no fixed week-to-date arithmetic. Scoped to regular season (`game_type=
"REG"`) by default; the postseason has a structurally different contest field (no season-long Millionaire
Maker cadence) and is left as a follow-up `game_types` argument, not hardcoded away.

**Real live quirk found running this against 2024 (not anticipated by ADR-0023): `contest-sources?date=X`
returns roughly a whole DK "week" of draft groups, not just the one(s) whose games are actually on `X`** --
confirmed by querying a Thursday, the Sunday two days later, and the following Monday for the same week and
getting back the same group list each time, including a `(Thu-Mon)`/`(Mon-Thu)` cross-week combined-slate
group with a bigger `game_count` than the real Sunday main slate. Disambiguating by `game_count` alone (the
natural first instinct, and this module's own first implementation) picks that cross-week group and then
finds no Millionaire-Maker-equivalent contest inside it. The fix, reusing this project's own existing
`draftkings.py` convention (`_extract_slate_label`/`_looks_non_main`, which `DraftGroupSource.contest_suffix`
already mirrors): the true main slate is the group with an **empty** `contest_suffix` whose own
`contest_start_date` falls on the exact date being processed. See `process_date`'s `same_day_main` filter.

**Resumability, rate limiting, and backoff are this module's job -- not `rotogrinders_resultsdb.py`'s.**
That module's `fetch_*` functions are unchanged (ADR-0024 constraint); this module wraps them with:
- a `has_raw_contest_data` check *before* any live call for a date, so a fully-captured date costs zero
  requests on a resumed run;
- a fixed ~1.0s delay between every live request (sequential, no concurrency -- a one-time historical
  backfill against a free public API, not a latency-sensitive path);
- exponential backoff on retryable failures (HTTP 429/5xx, or a network-level exception), and immediate
  resolution to a terminal sentinel for definitive failures (no primary contest this date; the contest
  data payload is confirmed unavailable).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
import requests

from nfl_dfs.ingestion.rotogrinders_resultsdb import (
    ContestDataUnavailableError,
    NoPrimaryContestError,
    fetch_contest_data,
    fetch_contest_sources,
    fetch_live_contests,
    parse_contest_summary,
    parse_player_exposures,
    parse_user_exposures,
    select_millionaire_maker_contest,
)
from nfl_dfs.storage.resultsdb_store import (
    STATUS_FETCHED,
    STATUS_NO_DRAFT_GROUPS,
    STATUS_NO_MAIN_SLATE_GROUP,
    STATUS_NO_PRIMARY_CONTEST,
    STATUS_UNAVAILABLE,
    has_raw_contest_data,
    write_curated_contest,
    write_raw_contest_data,
)

REQUEST_DELAY_SECONDS = 1.0
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0
MAX_RETRIES = 5

_STATUS_IN_MESSAGE_RE = re.compile(r"status=(\d+)")


# ---------------------------------------------------------------------------
# Date enumeration -- pure, unit-tested against a fixture schedule DataFrame
# ---------------------------------------------------------------------------


def enumerate_game_dates(
    schedule: pd.DataFrame, seasons: list[int], game_types: tuple[str, ...] = ("REG",)
) -> list[str]:
    """Distinct sorted `gameday` (ISO `YYYY-MM-DD`) values for the given seasons/game types, from a
    DataFrame shaped like `nfl_data_py.import_schedules()`'s real return value (`season`, `game_type`,
    `gameday` columns). Pure function -- no network -- so this is fully unit-testable.
    """
    mask = schedule["season"].isin(seasons) & schedule["game_type"].isin(game_types)
    dates = schedule.loc[mask, "gameday"].astype(str).unique().tolist()
    return sorted(dates)


def load_schedule_dates(
    seasons: list[int],
    *,
    game_types: tuple[str, ...] = ("REG",),
    schedule_loader: Callable[[list[int]], pd.DataFrame] | None = None,
) -> list[str]:
    """Thin live wrapper: pulls `nfl_data_py.import_schedules(seasons)` and enumerates real game dates.
    `schedule_loader` exists for test injection (avoids a network call in pytest); production code should
    call this with no override so it uses the live nflverse schedule release.
    """
    if schedule_loader is None:
        import nfl_data_py as nfl

        schedule_loader = nfl.import_schedules
    schedule = schedule_loader(seasons)
    return enumerate_game_dates(schedule, seasons, game_types)


# ---------------------------------------------------------------------------
# Retry/backoff wrapper
# ---------------------------------------------------------------------------


class _TransientFailure(Exception):
    """Internal signal: a retryable failure that exhausted its retries. Never written to raw storage --
    the date must remain eligible for a future run (ADR-0024)."""


def _http_status_from_error(exc: Exception) -> int | None:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code
    if isinstance(exc, ContestDataUnavailableError):
        match = _STATUS_IN_MESSAGE_RE.search(str(exc))
        if match:
            return int(match.group(1))
    return None


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.RequestException) and not isinstance(exc, requests.HTTPError):
        return True  # connection error, timeout, etc.
    status = _http_status_from_error(exc)
    if status is None:
        return False
    return status == 429 or status >= 500


def _call_with_backoff(fn: Callable, *args, sleep_fn: Callable[[float], None], **kwargs):
    """Runs `fn`, retrying with exponential backoff on retryable failures. Raises `_TransientFailure` if
    retries are exhausted; re-raises immediately (no retry) for anything non-retryable, including
    `NoPrimaryContestError` and a `ContestDataUnavailableError` carrying a non-retryable status (e.g. 403).
    """
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, re-raised below when non-retryable
            if not _is_retryable(exc):
                raise
            attempt += 1
            if attempt > MAX_RETRIES:
                raise _TransientFailure(f"exhausted {MAX_RETRIES} retries calling {fn.__name__}: {exc}") from exc
            delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
            sleep_fn(delay)


# ---------------------------------------------------------------------------
# Per-date processing
# ---------------------------------------------------------------------------


@dataclass
class DateOutcome:
    date: str
    status: str  # "fetched" | "skipped" | "no_primary_contest" | "no_draft_groups" | "no_main_slate_group"
    # | "unavailable" | "failed"
    contest_id: int | None = None
    contest_name: str | None = None
    player_count: int = 0
    detail: str | None = None


def process_date(
    date: str,
    season: int,
    *,
    session: requests.Session | None = None,
    base_dir=None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
) -> DateOutcome:
    """Resolves one calendar date: skip if already captured, else discover + fetch + write raw/curated,
    else write the matching terminal sentinel, else (transient failure) leave it unresolved for a future
    run. Sleeps `REQUEST_DELAY_SECONDS` after every live request it makes, win or lose.

    Any exception not already anticipated below (an unexpected non-retryable HTTP status, a parsing
    error against a malformed payload, etc.) is caught at this outer level and reported as `"failed"`
    with no sentinel written -- a multi-season unattended run must not crash outright over one bad date,
    and per ADR-0024 a non-terminal outcome always stays eligible for a future retry.
    """
    try:
        return _process_date_inner(date, season, session=session, base_dir=base_dir, sleep_fn=sleep_fn, now_fn=now_fn)
    except Exception as exc:  # noqa: BLE001 -- deliberate top-level safety net, see docstring
        return DateOutcome(date=date, status="failed", detail=f"unexpected error: {exc!r}")


def _process_date_inner(
    date: str,
    season: int,
    *,
    session: requests.Session | None = None,
    base_dir=None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
) -> DateOutcome:
    if has_raw_contest_data(date, base_dir=base_dir):
        return DateOutcome(date=date, status="skipped")

    def _sleep_after(fn, *args, **kwargs):
        result = _call_with_backoff(fn, *args, sleep_fn=sleep_fn, **kwargs)
        sleep_fn(REQUEST_DELAY_SECONDS)
        return result

    try:
        groups = _sleep_after(fetch_contest_sources, date, session=session)
    except _TransientFailure as exc:
        return DateOutcome(date=date, status="failed", detail=str(exc))

    if not groups:
        write_raw_contest_data(date, STATUS_NO_DRAFT_GROUPS, fetched_at=now_fn(), base_dir=base_dir)
        return DateOutcome(date=date, status="no_draft_groups")

    # Real live quirk, not anticipated by ADR-0023: `contest-sources?date=<date>` does not return only
    # the draft group(s) whose games are actually on `date` -- it returns roughly a whole DK "week" of
    # groups regardless of which single date within that week was queried (confirmed live: querying a
    # Thursday, the Sunday two days later, and the following Monday for the same week all return the
    # same group list). "Biggest game_count" is therefore the wrong disambiguator one level up from the
    # mistake ADR-0023 already found and fixed for *contest* selection -- it picks a `(Thu-Mon)` or
    # `(Mon-Thu)` cross-week combined-slate group (game_count spanning the whole week) over the actual
    # Sunday main slate. The correct selector, reusing this project's own existing convention
    # (`draftkings.py`'s `_extract_slate_label`/`_looks_non_main`, which this endpoint's `contest_suffix`
    # field already mirrors per `DraftGroupSource`'s docstring): the true main slate is the group with an
    # EMPTY `contest_suffix` whose own `contest_start_date` falls on the exact date being processed.
    # Thursday/Monday night dates correctly have no such group (there is no season-long Millionaire-Maker
    # -equivalent flagship on single-game nights) -- that's a real, expected `no_main_slate_group`
    # outcome, not a bug to work around.
    same_day_main = [g for g in groups if g.contest_suffix == "" and g.contest_start_date[:10] == date]
    if not same_day_main:
        write_raw_contest_data(date, STATUS_NO_MAIN_SLATE_GROUP, fetched_at=now_fn(), base_dir=base_dir)
        return DateOutcome(date=date, status="no_main_slate_group")
    primary_group = max(same_day_main, key=lambda g: g.game_count)

    try:
        contests = _sleep_after(fetch_live_contests, primary_group.contest_group_id, session=session)
    except _TransientFailure as exc:
        return DateOutcome(date=date, status="failed", detail=str(exc))

    try:
        contest = select_millionaire_maker_contest(contests)
    except NoPrimaryContestError as exc:
        write_raw_contest_data(
            date,
            STATUS_NO_PRIMARY_CONTEST,
            contest_group_id=primary_group.contest_group_id,
            fetched_at=now_fn(),
            base_dir=base_dir,
        )
        return DateOutcome(date=date, status="no_primary_contest", detail=str(exc))

    try:
        payload = _sleep_after(fetch_contest_data, date, contest.contest_id, session=session)
    except ContestDataUnavailableError as exc:
        status = _http_status_from_error(exc)
        if status is not None and (status == 429 or status >= 500):
            # Shouldn't reach here (retried inside _sleep_after), but guard defensively rather than
            # silently mis-classify a retryable status as a permanent sentinel.
            return DateOutcome(date=date, status="failed", detail=str(exc))
        write_raw_contest_data(
            date,
            STATUS_UNAVAILABLE,
            contest_group_id=primary_group.contest_group_id,
            contest_id=contest.contest_id,
            contest_name=contest.contest_name,
            fetched_at=now_fn(),
            base_dir=base_dir,
        )
        return DateOutcome(date=date, status="unavailable", contest_id=contest.contest_id, detail=str(exc))
    except _TransientFailure as exc:
        return DateOutcome(date=date, status="failed", detail=str(exc))

    summary = parse_contest_summary(payload)
    players = parse_player_exposures(payload)
    users = parse_user_exposures(payload)

    write_curated_contest(date, season, contest.contest_id, summary, players, users, base_dir=base_dir)
    write_raw_contest_data(
        date,
        STATUS_FETCHED,
        contest_group_id=primary_group.contest_group_id,
        contest_id=contest.contest_id,
        contest_name=contest.contest_name,
        payload=payload,
        fetched_at=now_fn(),
        base_dir=base_dir,
    )
    return DateOutcome(date=date, status="fetched", contest_id=contest.contest_id, contest_name=contest.contest_name, player_count=len(players))


# ---------------------------------------------------------------------------
# Season/multi-season orchestration
# ---------------------------------------------------------------------------


@dataclass
class BackfillStats:
    fetched: int = 0
    skipped: int = 0
    no_primary_contest: int = 0
    no_draft_groups: int = 0
    no_main_slate_group: int = 0
    unavailable: int = 0
    failed: int = 0
    player_rows: int = 0
    outcomes: list[DateOutcome] = field(default_factory=list)

    def record(self, outcome: DateOutcome) -> None:
        self.outcomes.append(outcome)
        if outcome.status == "fetched":
            self.fetched += 1
            self.player_rows += outcome.player_count
        elif outcome.status == "skipped":
            self.skipped += 1
        elif outcome.status == "no_primary_contest":
            self.no_primary_contest += 1
        elif outcome.status == "no_draft_groups":
            self.no_draft_groups += 1
        elif outcome.status == "no_main_slate_group":
            self.no_main_slate_group += 1
        elif outcome.status == "unavailable":
            self.unavailable += 1
        elif outcome.status == "failed":
            self.failed += 1

    @property
    def total(self) -> int:
        return len(self.outcomes)


def run_backfill(
    seasons: list[int],
    *,
    dates_by_season: dict[int, list[str]] | None = None,
    schedule_loader: Callable[[list[int]], pd.DataFrame] | None = None,
    session: requests.Session | None = None,
    base_dir=None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    progress: Callable[[str], None] = print,
) -> BackfillStats:
    """Walks every real regular-season game date across `seasons`, in order, writing raw/curated data for
    each. Resumable by construction (ADR-0024): re-running this with the same `base_dir` after an
    interruption skips every date already captured with zero live calls.

    `dates_by_season` lets a caller (or a test) supply pre-enumerated dates directly instead of pulling the
    live nflverse schedule; when omitted, dates are loaded live per season via `load_schedule_dates`.
    """
    stats = BackfillStats()
    for season in seasons:
        dates = (dates_by_season or {}).get(season) if dates_by_season else None
        if dates is None:
            dates = load_schedule_dates([season], schedule_loader=schedule_loader)
        progress(f"=== Season {season}: {len(dates)} real game date(s) to process ===")
        for date in dates:
            outcome = process_date(date, season, session=session, base_dir=base_dir, sleep_fn=sleep_fn, now_fn=now_fn)
            stats.record(outcome)
            if outcome.status == "fetched":
                progress(f"  {date}: fetched {outcome.contest_name!r} ({outcome.player_count} players)")
            elif outcome.status == "skipped":
                progress(f"  {date}: skipped (already captured)")
            elif outcome.status == "no_primary_contest":
                progress(f"  {date}: no Millionaire-Maker-equivalent contest this date")
            elif outcome.status == "no_draft_groups":
                progress(f"  {date}: no DK draft groups at all")
            elif outcome.status == "no_main_slate_group":
                progress(f"  {date}: no main-slate draft group this date (likely a Thu/Mon single-game night)")
            elif outcome.status == "unavailable":
                progress(f"  {date}: contest data unavailable ({outcome.detail})")
            else:
                progress(f"  {date}: FAILED -- {outcome.detail}")
        progress(
            f"=== Season {season} done: {stats.fetched} fetched, {stats.skipped} skipped, "
            f"{stats.no_primary_contest} no-primary-contest, {stats.no_main_slate_group} no-main-slate-group, "
            f"{stats.unavailable} unavailable, {stats.failed} failed (running totals across all seasons so far) ==="
        )
    return stats
