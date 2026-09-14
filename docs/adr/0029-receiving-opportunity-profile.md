# ADR-0029: Trailing receiving-opportunity profile -- descriptive, not predictive

**Status:** Accepted (implemented, unit-tested, live-verified)
**Date:** 2026-09-14
**Owner:** Chris, implemented per his own direct framing
**Related:** ADR-0028 (Component C's aDOT backtest, the null result this ADR responds to),
`src/nfl_dfs/ingestion/receiving_profile.py`, `src/nfl_dfs/composition/player_detail.py`

## Context

After Component C (`CeilingMultiplier`'s trailing-aDOT signal) came back a clean, thoroughly-
verified null against real DK outcomes (ADR-0028), Chris drew a distinction this project hadn't
made explicit before: **a metric failing to predict ceiling in aggregate, across thousands of
player-weeks, says nothing about whether it's useful for a completely different job** -- helping a
person choose between two specific, similarly-priced players, especially the "have to reach for
salary-cap value" case. His own framing: "you want to know what opportunities they are getting and
what kind of opportunities they are," naming aDOT, air yards, YAC, and red-zone targets together as
this category.

Red-zone usage already has its own section (`RedZoneUsage`, ADR-0022) -- this ADR covers the other
three: aDOT, air yards, and yards-after-catch, none of which existed as a visible, real number
anywhere in this pipeline before now (aDOT was computed internally for Component C's now-abandoned
backtest; air yards and YAC were never aggregated at all).

## Decision

**These are explicitly descriptive facts, not a predictive score.** No z-scoring, no shrinkage, no
backtested claim, no ranking. `ingestion/receiving_profile.py`'s `trailing_receiving_profiles`
computes real trailing (no-look-ahead) numbers straight from `nfl_data_py.import_pbp_data()`
(the same `air_yards`/`yards_after_catch` pbp columns Component C's own aggregation already
confirmed present and reliable) -- one row per player: `trailing_targets`, `trailing_receptions`,
`trailing_air_yards` (sum), `trailing_adot` (mean air yards per target), `trailing_yac_per_reception`
(mean yards after catch per completed reception). Every rate field is `None`, never a fabricated
zero, when its own denominator (targets/receptions) is zero.

Deliberately **not** gated behind a minimum-sample floor the way Component C's calibrated signal
was -- the whole point is to let Chris judge sample size himself when comparing two players (a
"12 targets, 3.2 aDOT" read next to "4 targets, 11.5 aDOT" is itself the useful comparison; hiding
the thin one behind a gate works against the stated use case).

Wired into `PlayerDetailRecord` (`receiving_profile`/`receiving_profile_reason`) for RB/WR/TE
(`SCHEME_SPLIT_POSITIONS`, the same scope the existing man/zone scheme-split section already uses)
and surfaced in the Player Detail dashboard's per-row expand, right next to Red Zone -- both are
"what kind of opportunity is this player getting" context, grouped together.

## Consequences

- Chris gets the real underlying inputs (targets, air yards, aDOT, YAC) for the exact tie-breaking
  decision he named, independent of Component C's null result -- the backtest answered "does this
  predict ceiling at scale," not "is this useful information," and those are different questions.
- No new ingestion: `air_yards`/`yards_after_catch` are the same pbp columns already confirmed live
  and reliable for Component C's work.
- Live-verified: real trailing numbers render correctly for real players once trailing weeks exist;
  honestly shows "no trailing data yet" for week 1, matching every other trailing-stat section's
  behavior this early in a season.
