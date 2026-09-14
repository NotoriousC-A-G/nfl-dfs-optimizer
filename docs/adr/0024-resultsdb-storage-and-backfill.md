# ADR-0024: ResultsDB raw/curated storage and the 2020-2025 backfill orchestrator

**Status:** Accepted (storage layer + orchestrator implemented and run live to completion for the full
2020-2025 window, including a real interrupted-and-resumed run proving resumability; see Consequences and
the live-run report for exact counts)
**Date:** 2026-09-13
**Owner:** Data Integration Engineer
**Related:** ADR-0023 (`docs/adr/0023-resultsdb-contest-history.md`, the data-availability investigation this
ADR builds on), `src/nfl_dfs/ingestion/rotogrinders_resultsdb.py` (discovery/fetch, unchanged by this ADR),
PRD Section 11 item 4

## Context

ADR-0023 confirmed what RotoGrinders ResultsDB actually is, built the discovery/fetch/parse functions for it
(`rotogrinders_resultsdb.py`), and explicitly scoped a backfill orchestrator out as follow-up work: "A
backfill/orchestration script (`resultsdb_backfill.py`-equivalent) walking many dates and seasons with
rate-limiting and resume support... all genuinely new, non-trivial work belonging to whichever future sprint
schedules Section 5 step 7, not a same-pass add-on to a data-availability ADR." This ADR is that follow-up:
where captured data lives on disk, how resumability actually works, and how the orchestrator walks
2020-2025 politely against a real third-party service.

**The MLB build's state-file-drift problem, cited directly as the thing to avoid:** the MLB sister project's
own historical backfill tooling tracked backfill progress in a separate state/progress file (which dates/
contests had been fetched) alongside the actual captured data files. That split source of truth drifts: a
process killed between "write data file" and "update state file" (in either order) leaves the two
disagreeing, and nothing after the fact can tell which one is right without re-deriving it from disk. The
fix adopted here is structural, not a smarter reconciliation pass: **there is no separate state file at
all.** Resumability is answered by a single filesystem check against the actual captured artifact
(`has_raw_contest_data`) — the presence of that file *is* the state, so there is nothing to drift out of
sync with.

## Decision

### 1. Storage layout (`src/nfl_dfs/storage/resultsdb_store.py`)

Two tiers, both under the repo's existing `data/` convention (`data/cache/` already used by
`normalization/registry.py` and `normalization/crosswalk.py`; `data/raw/`, `data/interim/`, `data/cache/`
already gitignored — this ADR adds `data/curated/` to that list, same treatment):

- **Raw** — `data/raw/resultsdb/nfl/<YYYY-MM-DD>.json`, one file per calendar date, written only after a
  date has been fully resolved (fetched, or definitively found to have no matching contest, or definitively
  unavailable — see "Sentinel outcomes" below). The file is an envelope, not the bare API payload:
  `{"date", "status", "fetched_at", "contest_group_id", "contest_id", "contest_name", "payload"}` —
  `payload` (the raw CloudFront `data/` response `parse_player_exposures`/`parse_contest_summary` consume)
  is present only when `status == "fetched"`. Keeping the envelope keyed **per date, not per contest**, is
  the deliberate resumability design: a single file existing or not existing answers "is this date done" in
  one `Path.exists()` call, with no need to first know which contest_id would have been chosen.
- **Curated** — `data/curated/resultsdb/nfl/season=<YYYY>/contests/<date>.parquet` and
  `.../player_exposures/<date>.parquet`, one Parquet file per date per table (not one growing file appended
  to over time) so writes are single-shot and idempotent — re-running a date just overwrites its own file,
  no read-modify-write race. Reading back queries the whole partition directory at once
  (`pd.read_parquet(directory)`, which pyarrow treats as a dataset), optionally filtered to a season.

### 2. Resumability: filesystem is the only source of truth

`has_raw_contest_data(date, base_dir=None) -> bool` does exactly one thing: check whether
`data/raw/resultsdb/nfl/<date>.json` exists. No manifest, no progress log, no separate index. The orchestrator
calls this *before* making any live request for a date (including the cheap discovery calls) — a fully
captured date costs zero HTTP requests on a resumed run, not just zero re-fetches of the expensive payload.

**Sentinel outcomes, not just success/failure.** A naive "only write the file on success" design would
re-attempt discovery forever for dates that will *never* produce a Millionaire-Maker-equivalent contest
(Thursday/Monday night single-game slates have no 150-max-entry flagship GPP) or that are permanently outside
ADR-0023's confirmed 2020 coverage floor. Both are definitive, not transient, so both get a sentinel raw file
recording the outcome (`status: "no_primary_contest"`, `"no_draft_groups"`, or `"unavailable"`) — a resumed
run skips them exactly like a successful fetch. **Transient failures (network errors, exhausted rate-limit
retries, unexpected 5xx) deliberately do *not* get a sentinel file** — those dates must remain eligible for a
future run to retry, so `has_raw_contest_data` staying `False` for them is correct, not a bug.

**Atomic writes.** `write_raw_contest_data` writes to a `.tmp` sibling and `os.replace`s it into place — the
one filesystem hazard a single-file-per-date design still has to guard is a process killed mid-write leaving
a truncated/corrupt JSON file that would satisfy `Path.exists()` but fail to parse on resume. `os.replace` is
atomic on both POSIX and Windows, so a killed process leaves either the old file (if any) or nothing, never a
half-written one.

**Write order:** curated Parquet is written *before* the raw envelope's atomic replace. If a run is killed
between the two, the raw sentinel is absent, so a resumed run re-fetches and re-derives (and overwrites) the
curated files — idempotent, so redoing it is harmless, but the reverse order (raw first) would let a killed
run mark a date "done" while its curated tables were never written, which is the actual failure mode worth
designing against.

### 3. Backfill orchestrator (`src/nfl_dfs/ingestion/resultsdb_backfill.py`)

- **Date enumeration is real, not guessed.** `enumerate_game_dates(schedule_df, seasons, game_types=("REG",))`
  is a pure function over the DataFrame shape `nfl_data_py.import_schedules()` actually returns (`season`,
  `game_type`, `gameday`) — the distinct sorted `gameday` values for the requested seasons/game types. This
  is unit-tested directly against a small fixture DataFrame; the live `import_schedules()` call itself is a
  thin, untested-by-pytest wrapper (`load_schedule_dates`), matching this project's convention of keeping the
  live network boundary thin and the logic around it pure/testable (same shape as `rotogrinders_resultsdb.py`
  separating `parse_*` from `fetch_*`). Scoped to `game_type="REG"` by default — the postseason has a
  structurally different contest field (no season-long Millionaire Maker cadence) and is out of scope for
  this pass; `game_types` is a parameter, not hardcoded, so postseason backfill is a follow-up call-site
  change, not a rewrite.
- **Sequential, no concurrency, ~1.0s delay between every live request** (not just between dates) —
  `REQUEST_DELAY_SECONDS = 1.0`, injected as a `sleep_fn` parameter for tests to assert on without actually
  sleeping. This is a one-time historical backfill against a third-party service with no published rate
  limit; the conservative posture is a deliberate choice; per ADR-0023 there is no auth to lose by being
  throttled, but being a bad citizen against a free public API is still worth avoiding.
- **Exponential backoff on retryable failures.** A retryable failure is a `requests.HTTPError` whose
  response status is `429` or `>=500`, or a `requests.exceptions.RequestException` (timeout/connection
  error). Backoff: `min(base_delay * 2**attempt, max_delay)` starting at 2s, capped at 60s, up to 5 attempts,
  then the date is left unresolved (no sentinel) and counted as failed for this run. A **non**-retryable
  failure (`NoPrimaryContestError`, or `ContestDataUnavailableError` whose message states a `403`) resolves
  immediately to the matching sentinel outcome — no point backing off from a definitive answer.
  `ContestDataUnavailableError`'s message embeds the real HTTP status (`rotogrinders_resultsdb.py`'s own
  docstring: it deliberately can't distinguish pre-2020-coverage from an unknown contest_id, both surfacing
  as a clean `403`) — the orchestrator parses that status back out of the message rather than changing the
  exception's shape, since introducing a status-code field there is a real, if small, signature change to a
  module this ADR was told not to redesign, and the message already carries what's needed.
- **Progress reporting** prints per-date outcome (`fetched`, `skipped (already captured)`,
  `no primary contest`, `unavailable`, `failed`) plus a running per-season tally, and a final summary line
  per season and overall — since a multi-season run can take a while, silent long-running loops aren't
  acceptable per the task brief.

### 4. Two real live quirks found running this against actual 2023/2024 data, neither anticipated by ADR-0023

**`contest-sources?date=X` returns roughly a whole DK "week" of draft groups, not just the one(s) whose
games are actually on `X`.** Confirmed live: querying a Thursday, the Sunday two days later, and the
following Monday for the same week all return the same group list, including a `(Thu-Mon)`/`(Mon-Thu)`
cross-week combined-slate group whose `game_count` (spanning the whole week) is bigger than the real Sunday
main slate's. This module's first implementation picked draft groups by largest `game_count` — the natural
extension of ADR-0023's own "biggest is the wrong heuristic" finding one level down (contest selection within
a group) — and that same mistake reappears one level up (group selection itself): it silently picked the
cross-week group and then found no Millionaire-Maker-equivalent contest inside it on every date tested. Fixed
by reusing this project's own existing `draftkings.py` convention (`_extract_slate_label`/`_looks_non_main`,
which `DraftGroupSource.contest_suffix` already mirrors, per that dataclass's own docstring): the real main
slate is the group with an **empty** `contest_suffix` whose own `contest_start_date` falls on the exact date
being processed. A Thursday/Monday single-game night correctly has no such group at all (there is no
season-long Millionaire-Maker-equivalent flagship on those nights) — a new `no_main_slate_group` sentinel
status, distinct from `no_draft_groups` (the endpoint returned nothing at all for that date), records this
as the real, expected outcome it is.

**A single draft group's `live-contests` can carry *two* contests that both satisfy `is_primary AND
multi_entry_max == 150`** — a stronger version of the two-`is_primary`-contests ambiguity ADR-0023 already
documented (there, only one candidate also had `multi_entry_max == 150`). Live-confirmed on 2024-09-15: a
$555-entry-cost "$3M Fantasy Football Millionaire" (6,006 entries, a high-roller edition) and a $20-entry-cost
"$3.5M Fantasy Football Millionaire" (205,882 entries, the actual mass-market flagship PRD Section 2 means)
both matched. `select_millionaire_maker_contest` (unchanged, per this ADR's constraint) correctly raises
`NoPrimaryContestError` rather than guessing between them — exactly its documented fail-loud design, working
as intended, not a bug. Across the live 2020-2025 backfill this pass actually ran, this pattern accounted for
roughly a third of all real Sunday main slates (see the live-run report) — common enough to be worth a
**flagged follow-up**, not a same-pass fix to a module this ADR was told not to redesign: PRD Section 2
describes the Millionaire Maker as "massive overall field" with no specific buy-in named, and in every live
case observed this pass the mass-market edition (entry cost in the tens of dollars, entry count in the tens
to hundreds of thousands) is the one with the much larger `contest_size`/entrant count than its high-roller
same-named counterpart -- a future pass could add "largest `contest_size` among the `is_primary`+150
matches" as a secondary disambiguator directly in `select_millionaire_maker_contest`, rather than guessing at
a specific entry-cost threshold that could shift year to year.

### 5. Why no separate `resultsdb_store` "list already-backfilled dates" cache

Considered adding an in-memory or on-disk index of already-seen dates to avoid repeated `Path.exists()`
calls across a run. Rejected: `Path.exists()` against a local filesystem is not the bottleneck (the network
calls and the mandatory 1s delay are), and any such index reintroduces exactly the second source of truth
this ADR exists to eliminate. `has_raw_contest_data` is called once per date per run — a few thousand calls
across the full six-season window — with no measurable cost.

## Alternatives considered

- **One growing Parquet/JSONL file appended to across the whole backfill**, matching the MLB build's
  "consolidated JSONL" artifact. Rejected for the curated tier: appending safely requires either a
  read-modify-write per date (races/corruption risk on interruption) or an append-only format that then needs
  its own dedup/resume logic layered on top — recreating the state-drift problem this ADR is trying to avoid,
  one layer up. One file per date per table sidesteps this entirely; `pd.read_parquet` on a directory is a
  standard pyarrow dataset read, not a hand-rolled concatenation.
- **A single `has_backfilled(date, contest_id)` keyed by contest_id instead of date.** Rejected — the whole
  point of resumability here is skipping a date *before* paying for the discovery calls that would reveal
  its contest_id; keying by contest_id would require doing that work first, defeating the purpose.
- **Retry every non-200 from `fetch_contest_data`, including 403.** Rejected — ADR-0023 already
  live-confirmed 403 from that endpoint is a stable, non-transient signal (either pre-2020 coverage or an
  unknown contest_id), not a rate-limit or transient-server condition; retrying it would just burn requests
  against a service already being asked to be treated conservatively.

## Consequences

- `data/raw/resultsdb/nfl/` and `data/curated/resultsdb/nfl/` are new, gitignored, locally-generated data
  directories — nothing under them is committed, matching `data/raw/`, `data/interim/`, `data/cache/`'s
  existing treatment in `.gitignore`.
- `rotogrinders_resultsdb.py` is unchanged — this ADR is pure orchestration/storage on top of its existing
  `fetch_*`/`parse_*`/`select_millionaire_maker_contest` functions, per the task's own constraint not to
  redesign that module without a genuine bug (none was found; see "backoff" above for why the status-parsing
  workaround was preferred over a signature change).
- The full 2020-2025 window **was** run to completion in this pass: 71 real Millionaire-Maker-equivalent
  contests captured (12/17/15/10/8/9 across 2020/2021/2022/2023/2024/2025 respectively), ~39,200
  player-exposure rows, spanning $227M in real captured prize pools across ~13.2M real contest entries. The
  2023 run was deliberately `SIGKILL`-interrupted mid-flight and re-run to prove resumability live (not just
  in the unit suite) — see the live-run report for the exact before/after state. `ProjectionAccuracyRecord`-
  style per-season calibration comparison (ADR-0023 section 5's stability test) is still real, separate
  follow-up work, not attempted here — this pass delivers the raw/curated data that work would consume.
- `pyarrow` is added as a project dependency (`pyproject.toml`) — needed for Parquet read/write; nothing in
  the codebase depended on Parquet before this ADR.
