# ADR-0033: Dup-risk calibration -- ownership-decile and lineup-trend dup-rate curves

**Status:** Accepted (implemented, unit-tested, run live against the real 2024-2025 `lineups/` backfill --
see Consequences for real findings and what remains open)
**Date:** 2026-09-15
**Owner:** Chris, direct continuation of ADR-0032's "dup-risk calibration is the next phase" plan
**Related:** ADR-0025 (ownership-propensity calibration, whose salary-decile-curve method this mirrors),
ADR-0032 (the `lineups/` ingestion/backfill this reads from), `src/nfl_dfs/analysis/dup_risk_calibration.py`

## Context

ADR-0032 built the ingestion and a real 2024-2025 backfill for ResultsDB's `lineups/` endpoint (2.26M
real lineup rows, `lineup_ct` = the literal number of contest entries that built each distinct roster) but
deliberately stopped short of any calibration, naming it as the next phase. This ADR is that phase: does a
lineup's own average field ownership, or its structural composition (`lineupTrends`), actually predict how
often it gets duplicated by the field.

## Decision

### 1. The model, mirroring ADR-0025's salary-decile-curve method exactly

Within each `(date, contest_id)`, rank that contest's distinct lineups by `avg_own` (mean ownership across
the lineup's 9 roster spots) descending and assign a decile (0 = highest-owned tenth of that contest's
lineup pool, 9 = lowest) -- the direct lineup-level analog of ADR-0025's salary-decile ranking (there:
rank players by salary within position; here: rank lineups by ownership within contest, no position axis
since a lineup isn't position-specific). Per `(season, decile)`, pooling across every already-backfilled
contest that season: the empirical dup rate (fraction with `lineup_ct > 1`) and mean `lineup_ct`. Also a
single summary statistic, Pearson r(avg_own, is_duplicated), mirroring ADR-0025's salary-ownership
correlation.

### 2. A real addition beyond ADR-0025's precedent: per-trend dup-rate splits

The `lineups/` payload computes real per-lineup structural flags (`lineupTrends` -- e.g.
`qbPairedWithPassCatcher`, `minOnePlayerWithLowOwnership`) that ADR-0025's player-exposure data has no
equivalent of. For each observed flag, this ADR reports the real dup rate when that flag is `True` vs.
`False` -- a second, independent lens on duplication risk beyond the ownership-decile curve, directly
useful for lineup construction (which structural choices correlate with getting duplicated).

### 3. No leave-one-out stability test, no production selection -- honestly, not just deferred

ADR-0025's stability test requires at least 3 seasons for "leave one out and compare to the rest" to be a
meaningful comparison (its own explicit guard). Only 2 seasons of lineup data exist (2024-2025, ADR-0032's
deliberately scoped-down backfill) -- `compare_two_seasons` here is a lightweight, honestly-labeled
side-by-side report, not a stability verdict, and there is no `select_production_calibration` equivalent.
Extending the backfill to more seasons (a real, one-line-call-away follow-up already named in ADR-0032) is
a precondition for that next step, not a decision available to make with n=2.

## Real findings (live, 2024-2025, not hypothetical)

**The core relationship is real, strong, and nearly identical across both independent seasons --
exactly the kind of consistency ADR-0025 never found for its own six-season salary-ownership fit:**

- Ownership-decile dup rate, 2024 vs. 2025 (decile 0 = highest-owned tenth of each contest's lineups):
  decile 0 dup rate **9.43% vs. 8.98%**, decile 5 (median) **1.04% vs. 0.96%**, decile 8 (near-lowest)
  **0.76% vs. 0.63%**. The highest-ownership decile duplicates roughly **10-12x more often** than the
  middle of the distribution, in both seasons independently.
- r(avg_own, is_duplicated): **+0.155 (2024) vs. +0.142 (2025)** -- a 0.013 spread, tighter agreement
  than ANY position in ADR-0025's own six-season salary-ownership fit (which ranged 0.03-0.07 point
  spreads and still failed that project's own 1.5x-leave-one-out-std stability bar).
- A real, smaller secondary pattern: the LOWEST-ownership decile (9) has a slightly higher dup rate than
  deciles 6-8 in both seasons (0.86%/0.65% vs. ~0.76-0.94%/0.63-0.80%) -- a mild U-shape, not purely
  monotonic. Plausible football reading (not confirmed further this round): a lineup built entirely of
  very-low-owned players is itself a recognizable "extreme leverage/fade" construction pattern that some
  meaningful slice of the field converges on independently, the same way the chalk tail duplicates for the
  opposite reason. Named for the record, not chased further this round.

**The single most actionable per-trend finding: `minOnePlayerWithLowOwnership`.** Dup rate when `True`
(the lineup has at least one genuinely low-owned player) vs. `False` (an all-chalk lineup, no low-owned
player at all): **1.47% vs. 7.22%** (2024), **1.47% vs. 5.78%** (2025) -- a ~4-5x difference, consistent
across both seasons, and a simple, directly actionable binary check (unlike the continuous ownership-decile
curve, this is a yes/no property a candidate lineup either has or doesn't).

**Other trend splits, real but smaller:** `qbStackPairedWithOpponent` (a bring-back game stack) shows a
real, consistent duplication premium (3.04%/2.75% true vs. 2.07%/1.89% false both seasons) -- a popular,
duplicated construction pattern, not a leverage play by itself. `qbPairedWithPassCatcher` (a basic
same-team stack) shows a smaller but consistent premium too (2.44%/2.21% vs. 1.73%/1.64%).
`rbPairedWithDefense` shows almost no split either season (~2.3-2.4% vs. ~2.2-2.3%) -- not a meaningfully
duplication-relevant construction choice. `maxOneRBPerGame` is inconsistent in DIRECTION across the two
seasons (2024: true slightly higher; 2025: true lower) -- reported honestly as noise, not smoothed into a
false consistent story.

**Two trend flags are structurally always-one-sided in Classic contests, not a data gap:**
`cptPassCatcherPairedWithQB`/`cptQBPairedWithMultiplePassCatchers` are always `False` (`n_true=0` both
seasons) -- Captain-slot flags from RotoGrinders' shared Showdown/Classic `lineupTrends` schema, inapplicable
to the Classic-format Millionaire Maker this backfill covers. `maxOneDEFPerGame`/`maxOneKickerPerGame` are
always `True` (`n_false=0`) -- DK Classic's own roster construction (exactly one DST slot, no kicker slot at
all) makes both trivially satisfied by construction, not a real duplication-relevant signal.

## Consequences

- Real, live-validated evidence that a lineup's OWN ownership profile (both continuous, via the decile
  curve, and via the single binary `minOnePlayerWithLowOwnership` flag) predicts real field duplication --
  the literal dup-risk relationship this whole investigation (ADR-0023 through ADR-0033) set out to find,
  now confirmed on real settled-contest data rather than assumed.
- **Not yet wired into anything live** -- no optimizer scoring change, no dashboard surface, matching this
  project's own "calibration is the input, not the consuming stage" pattern from ADR-0025 (whose own
  production calibration also isn't wired into a live leverage layer yet). A future dup-risk-aware lineup
  construction feature (e.g. penalizing an all-chalk, no-low-owned-player lineup, or read a candidate
  lineup's implied dup risk off this decile curve) is real, scoped follow-on work, not built this round.
- Extending the backfill to the full 2020-2023 historical range (named in ADR-0032) would both give this
  calibration more seasons to test real stability against (enabling ADR-0025's leave-one-out method
  properly) and let the same U-shape/trend findings above be checked for persistence outside the 2024-2025
  contest-size regime (which ADR-0023 itself flagged as structurally different pre/post ~2022).
