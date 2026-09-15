# ADR-0038: Longitudinal injury-report snapshot archive -- built

**Status:** Accepted (implemented, unit-tested, live-verified end to end against a real capture)
**Date:** 2026-09-15
**Owner:** Chris, direct continuation of ADR-0031's "not built this round" note
**Related:** ADR-0031 (the investigation that named this as "the real fix, if this is worth
pursuing further"), ADR-0024 (`resultsdb_store.py`'s storage conventions, reused unchanged),
`src/nfl_dfs/storage/injury_snapshot_store.py`, `src/nfl_dfs/analysis/injury_staleness.py`,
`scripts/injury_snapshot_capture.py`, `scripts/injury_snapshot_retrospective_check.py`

## Context

ADR-0031 found a structural problem, not a fixable bug: RotoGrinders' Situation Room injury feed
exposes no history/archive endpoint (confirmed live -- no query param changes the response), and
nflverse's official weekly injury report only becomes available in arrears. A one-shot comparison
script (`scripts/injury_staleness_check.py`) can therefore never measure genuine same-week
staleness -- it can only compare RotoGrinders' CURRENT snapshot against whatever week the official
feed has already finalized, almost always a week behind. ADR-0031's own live run confirmed this in
practice: zero players landed in the "comparable" bucket, because every comparison was across two
non-overlapping weeks. ADR-0031 named the fix explicitly: start saving Situation Room's live pulls
on an ongoing basis, then retrospectively compare each saved snapshot against the official report
once that week's official data finally becomes available. Chris chose to build it.

## Decision

### 1. Storage layer -- `storage/injury_snapshot_store.py`, `resultsdb_store.py`'s conventions reused unchanged

Same filesystem-is-the-only-source-of-truth posture as `resultsdb_store.py` (ADR-0024): no
separate index/state file, `has_snapshot` answers resumability with a single `Path.exists()`
check, writes are atomic (`.tmp` sibling + `os.replace`). Layout:
`data/raw/injury_snapshots/<YYYY-MM-DD>.json` -- one envelope per CALENDAR date (mirroring
`resultsdb_store.py`'s own "one envelope per date" convention), carrying `season`/`target_week`
(Situation Room's CSV has no season/week field of its own, confirmed by ADR-0031's own
investigation, so this context has to be supplied by the caller at capture time) plus the raw
`InjuryReportEntry` rows. A second capture on the same calendar date overwrites that date's file
-- a deliberate one-snapshot-per-day resolution, not a full intra-day time series (see
Consequences for the disclosed limitation this implies).

### 2. Capture script -- `scripts/injury_snapshot_capture.py`

A small, cron-able script: fetch the live Situation Room report, write today's snapshot. Meant to
run on a recurring (daily) cadence -- a single run only ever adds one data point; the retrospective
payoff only exists once several have accumulated across a real week.

### 3. Comparison logic factored out, not duplicated -- `analysis/injury_staleness.py`

`injury_staleness_check.py`'s original bridging/classification logic (join via
`identity.sources["rotogrinders"].native_id` and `identity.nflverse_gsis_id`, classify into
only-RG/only-official/agree/disagree against the disclosed `OFFICIAL_TO_RG_STATUS` mapping) is now
a standalone, reusable `compare_injury_sources` function, moved unchanged (same behavior, same
disclosed status-mapping caveat) rather than duplicated for the new retrospective consumer.
`injury_staleness_check.py` itself was refactored to call it -- no behavior change, live-verified
against a real pull to confirm the refactor didn't alter its output shape.

### 4. Retrospective comparison -- `scripts/injury_snapshot_retrospective_check.py`

For a given `(season, target_week)`: reads every archived snapshot for that week
(`read_snapshots_for_week`), fetches the official report (now-available, since this only makes
sense to run after the fact), and runs `compare_injury_sources` once per snapshot against that
SAME week's official data -- a genuine same-week comparison for every capture, not the one-shot
script's inherent week-mismatch. Prints a match-rate-over-time table, the actual staleness signal
ADR-0031 set out to measure. Uses the CURRENT reconciled `PlayerIdentity` pool as the join-key
space for every archived snapshot -- a disclosed simplification (canonical/native/gsis id schemes
are stable per-player identifiers, not expected to vary week to week for the same underlying
players, unlike the injury STATUS values themselves, which are exactly what's being compared).

## Consequences

- **Live-verified end to end**: `scripts/injury_snapshot_capture.py` run against a real live
  RotoGrinders pull (2026-09-15, week 1) -- 41 rows fetched, STATUS codes `['D', 'O', 'Q']`,
  written to `data/raw/injury_snapshots/2026-09-15.json` and round-tripped correctly. The
  refactored `scripts/injury_staleness_check.py` was also re-run live to confirm the
  `compare_injury_sources` extraction didn't change its behavior.
- **The retrospective script itself (`injury_snapshot_retrospective_check.py`) could not be
  live-verified this round** -- it needs multiple archived snapshots across a real week plus that
  week's official report to already be published, neither of which exists yet from a single day's
  capture. It's unit-tested (via `compare_injury_sources`, which it shares with the already-
  verified live script) and will produce its first real retrospective result once
  `injury_snapshot_capture.py` has run on a recurring cadence for at least one full settled week.
- **A real, disclosed scope limitation carried forward**: one snapshot per calendar date, not an
  intra-day time series. This measures day-over-day staleness trends toward kickoff, not
  hour-over-hour lag behind a specific practice-report update. If day-level resolution turns out
  to be too coarse once real retrospective data comes back, moving to a finer capture cadence
  (multiple named snapshots per day, e.g. `<date>-<HHMM>.json`) is a small, additive change to
  this same module -- not a redesign.
- **No dashboard wiring** -- this is purely an archival/measurement tool, the same posture ADR-0031
  itself took ("shipping a staleness warning derived from a structurally invalid comparison would
  be worse than shipping nothing"). Once enough real retrospective match-rate data exists to say
  something concrete about Situation Room's actual reliability curve, whether/how to surface that
  on the dashboard is a separate, later decision.
- This module set requires nothing new operationally beyond running
  `scripts/injury_snapshot_capture.py` on a schedule (a cron job, or manually) during live weeks --
  no new credentials, no new vendor dependency.
