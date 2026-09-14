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

## Addendum: red-zone weekly share sequence (same posture, Component B's data)

Chris's own framing named red-zone targets alongside aDOT/air yards/YAC as this same "what
opportunity are they getting" category. Red-zone usage already had a single trailing-share number
(`RedZoneUsage.carry_share_trailing`/`target_share_trailing`, ADR-0022) built from Component B's
real, bug-fixed weekly data (the zero-fill and QB-exclusion fixes found during the `CeilingMultiplier`
red-zone backtest, ADR-0028) -- but a single number collapses exactly the consistency-vs-spikiness
distinction the Fantasy Football Expert's Component B review named directly ("is this a bell-cow
red-zone back or one spike week carrying the average").

`ceiling.signals.trailing_red_zone_share_by_week(pbp, target_week, role)` returns the real, ordered
`[(week, share), ...]` sequence behind that number -- same zero-fill/QB-exclusion/no-look-ahead
treatment `red_zone_ceiling_signals` already uses, just not collapsed into a boom-rate summary.
Wired onto `RedZoneUsage.carry_share_by_week`/`target_share_by_week` (`PlayerDetailRecord`'s
existing red-zone section, not a new block -- keeps the dashboard lean, groups it with the number
it explains) and rendered as a "Trend: W3 60% &middot; W4 0% &middot; W5 50%"-style sub-line under
each existing carries/targets line in the Red Zone expand block.

Deliberately independent of `red_zone_trailing` (the pre-aggregated summary DataFrame `carries_
trailing`/`target_share_trailing` are sourced from) -- the weekly sequence is sourced straight from
`pbp`, so it can populate even on a week the summary DataFrame wasn't supplied. No new backtest, no
new claim: this is Component B's already-real, already-bug-fixed data, shown as raw facts rather
than resurrected as a scored signal (Component B still ships no live multiplier, per ADR-0028's
final verdict -- RB fell short of significance even under a clustered-SE check, and WR's real
negative finding can't be expressed in `CeilingMultiplier`'s one-sided formula).

Live-verified: runs end-to-end without error against a real slate pull; correctly shows "no
red-zone trailing data supplied" for week 1 (no trailing weeks exist yet), same expected behavior
as every other trailing-stat section this early in a season.
