# ADR-0032: ResultsDB `lineups/` endpoint -- ingestion, storage, and a real 2024-2025 backfill

**Status:** Accepted (ingestion + storage implemented and unit-tested; a real 2024-2025 backfill run live
to completion, 2.26M real lineup rows; dup-risk calibration itself is the next, not-yet-built phase --
see Consequences)
**Date:** 2026-09-15
**Owner:** Chris, per his explicit direction ("dup-risk calibration," then "start with just 1-2 seasons, raw")
**Related:** ADR-0023 (`docs/adr/0023-resultsdb-contest-history.md`, first confirmed this endpoint exists
and roughly shaped it), ADR-0024 (the original contest/player-exposure backfill this one extends),
ADR-0025 (ownership-propensity calibration, which explicitly scoped dup-risk out as needing this endpoint),
`src/nfl_dfs/ingestion/rotogrinders_resultsdb.py`, `src/nfl_dfs/storage/resultsdb_store.py`

## Context

The `lineups/` endpoint (one row per distinct roster the field actually built, with `lineupCt` -- the
literal dup count) was confirmed to exist and roughly shaped back in ADR-0023, then explicitly named as
"still uningested" in every subsequent round (ADR-0025's ownership calibration, ADR-0026's leverage layer)
-- real dup-risk calibration (predicted vs. actual field duplication, by ownership/stack profile) needs it
and nothing else in this codebase currently touches it. Chris chose to close that gap this round.

## Decision

### 1. Ingestion (`rotogrinders_resultsdb.py`)

`fetch_lineups`/`parse_lineups`/`LineupRow`, mirroring the module's existing fetch/parse conventions
exactly (same CloudFront host, same no-cookie-needed auth, same `ContestDataUnavailableError` reused
rather than a parallel error type). **Real shape correction, found via a live pull (2020-09-20, contest
91962454, 244,757 distinct lineups) that ADR-0023's original description got wrong in one respect:** the
payload is `{"lineups": {<lineupHash>: {...}}}` -- a DICT keyed by `lineupHash`, not a flat list. Also
confirmed several real fields beyond what ADR-0023 originally named: `favoriteCt`, `underdogCt`, `homeCt`,
`visitorCt`, `correlatedPlayers`.

### 2. Storage (`resultsdb_store.py`)

`write_curated_lineups`/`read_curated_lineups`, extending the existing raw/curated Parquet layout with a
fourth curated table (`.../lineups/<date>.parquet`). **A real, live-tested storage decision, not an
assumption:** `LineupRow`'s nested fields (`lineup_players`, `team_stacks`, `game_stacks`,
`lineup_trends`) are JSON-string-encoded before writing, not stored as native pyarrow struct/map columns.
Confirmed live that pandas/pyarrow CAN round-trip a dict-valued column directly (a small test case worked
cleanly) -- but its struct-type inference unions every distinct key seen across ALL rows in the column
into one permanent schema. Harmless for `lineup_trends` (a small, fixed set of ~10 boolean flags) but
unbounded for `team_stacks`/`game_stacks`, whose keys are team/game ids that vary essentially every row
across a 200K+-row contest. JSON-string columns sidestep that risk entirely, at the cost of a
`json.loads()` on read -- which `read_curated_lineups` does automatically, so callers still get real
Python dicts back.

**Backfilled as a separate, later pass, not folded into `run_backfill`'s original 2020-2025 walk.**
`run_lineups_backfill`/`process_lineups_date` walk the ALREADY-resolved `(date, season, contest_id)`
triples in the existing curated `contests` table (no re-discovery of draft groups/contests needed -- that
work is done) and pull just the `lineups/` endpoint for each, with the same resumability (`has_curated_
lineups`) and backoff/rate-limit discipline `run_backfill` already established.

### 3. The 2024-2025 backfill -- real, run live to completion

Per Chris's explicit direction ("start with just 1-2 seasons, raw... prove out the calibration approach
before committing to the full historical range"), not the full 2020-2025 window. **Real volume finding
that justified not defaulting to the full historical range:** the smallest 2024 contest alone (27,777
entries) has 27,469 distinct lineups; the larger 2024/2025 contests (130K-200K entries) each have
100K-200K distinct lineups. Full live run, 17 already-resolved contests, zero failures:

- 2024 (8 contests): 1,102,149 total lineup rows.
- 2025 (9 contests, through the most recent already-resolved date): 2,256,583 lineup rows combined with
  2024.
- **97.7% of lineups league-wide (both seasons combined) have `lineup_ct == 1`** (a genuinely unique
  roster) -- consistent with the single-contest sample that motivated the backfill-scope question in the
  first place (97.8%). The duplicated tail reaches as high as `lineup_ct == 151` in a single contest.
- **Real storage cost, disclosed rather than assumed:** 105MB (2024) + 109MB (2025) = 214MB total for
  2.26M rows -- Parquet compression makes this a genuinely small, manageable footprint, not the
  multi-gigabyte concern the raw JSON payload sizes (27.5MB+ per contest, uncompressed) might have
  suggested going in.

## Consequences

**This is the data layer, not the calibration.** Matching this project's own established "data layer
first, then calibration" staging (Component A's own precedent, ADR-0025's ownership-propensity round),
this ADR covers ingestion, storage, and a real backfill -- the actual dup-risk calibration (what
ownership/stack/salary-usage profile predicts a lineup's real `lineup_ct`, the way ADR-0025 calibrated
ownership propensity by salary decile) is the next phase, not built this round.

**Real data now exists to build that calibration on.** `read_curated_lineups(season=2024)` /
`read_curated_lineups(season=2025)` return real, joinable per-lineup rows (`lineup_players`, `total_own`/
`avg_own`, `team_stacks`, `lineup_trends`) alongside the real `lineup_ct` outcome -- exactly the shape a
predicted-vs-actual duplicate-count model needs, with no further ingestion required to start that work.

**The full 2020-2023 historical range remains a real, scoped-out follow-up**, not abandoned -- extending
`run_lineups_backfill([2020, 2021, 2022, 2023])` is a one-line call away given the infrastructure already
built here; deferred per Chris's explicit "prove it out on 1-2 seasons first" direction, not a technical
limitation.
