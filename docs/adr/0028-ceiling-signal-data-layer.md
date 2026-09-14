# ADR-0028: `CeilingMultiplier` design + ceiling signal data layer

**Status:** Accepted (data layer implemented, unit-tested, and live-verified against real 2026 pbp
data; the live multiplier itself is explicitly NOT built this round -- see Decision 4)
**Date:** 2026-09-14
**Owner:** Model Analytics Expert / Fantasy Football Expert, scoped by Chris
**Related:** ADR-0011 (shared shrinkage form), ADR-0005 (capped log-space combination), ADR-0012
(the precedent for backtest-before-calibrate), ADR-0019/0020 (`RoleShare`), ADR-0001 (alignment
approximation, still not ingested), `docs/PRD.md` Section 6, `src/nfl_dfs/ceiling/signals.py`

## Context

During the Player Detail dashboard review (ADR-0027), the Fantasy Football Expert named "ceiling"
(a per-player GPP upside/variance metric) as the single biggest real gap in this build -- nothing
in this codebase computes it, and this project is GPP-only (PRD Section 18) with the same 3-lineup
set required to span single-entry through the Millionaire Maker (Section 2). Chris agreed this
needs the same rigor as every other Section 6 formula: a Model-Analytics-Expert draft, a
Fantasy-Football-Expert football-plausibility review, explicit sign-off from both before anything
is implemented.

## Decision

### 1. Model Analytics Expert's draft

Proposed `CeilingMultiplier` as a new, fifth Section 6 construct (peer to `GameEnvironmentScore`/
`StackProfile`/`MatchupContext`/`DSTProjection`), output as `ceiling_projection = blended_projection
× ceiling_multiplier` -- a new field, never overwriting `blended_projection`. Critical structural
call: **one-sided, floored at 1.0** (`m_i = max(1.0, 1 + z_i * scale_i)`) -- unlike every other
Section 6 multiplier, which is two-sided. Three components: (A) RB/WR role-share volatility
(coefficient of variation of trailing weekly share), (B) RB/WR/TE red-zone-share volatility (same
CV construction), (C) WR/TE trailing depth-of-target (aDOT), combined via the existing
`capped_log_combine` method. QB rushing explicitly recommended out of scope (nothing in this
codebase computes QB rushing at all -- comparable in scope to the original `RoleShare` research
pass, not a footnote). No scale/cap constants proposed -- explicitly flagged as needing a live
outcome backtest first, the same rigor ADR-0012's return-TD-rate correction used.

### 2. Fantasy Football Expert's review -- one real, concrete correction, not a rubber stamp

Withheld sign-off on Component A as drafted: **symmetric CV can't distinguish an upside spike from
a `BlowoutVolumeDiscount`-shaped downside collapse** (a bell-cow RB pulled in blowout garbage time).
Since `CeilingMultiplier` is one-sided (floored at 1.0, can only ever raise a read), feeding it a
symmetric variance statistic is a real, concrete sign-error risk for exactly the blowout-prone-
offense back that discount already exists to penalize -- not a benign overlap to check later, per
the FFE's own words. Fix: an upside-only "boom rate" (fraction of trailing weeks a player's value
spiked above their own trailing median), not raw dispersion.

Other named corrections, all incorporated: red-zone-share **level** stays explicitly excluded from
`CeilingMultiplier` (it's a mean-shift signal, belongs in a future `RoleShare`-adjacent construct,
not a variance one); aDOT population should split by route-tree/alignment role, not position label;
aDOT's volume floor should pair a low threshold with shrinkage, not a hard exclusionary gate; QB
deferral (Option A) approved, but a degraded QB fallback (Option B) explicitly rejected outright --
given the one-sided floor design, an unshrunk/ungated QB leg can only ever inflate a QB's ceiling
with no self-correcting mechanism, the wrong place in the whole codebase to introduce that risk.
Also required: a visible leg-count/confidence flag (a thin 1-leg read from a rookie shouldn't look
as confident as a mature 3-leg read) and a named future requirement that any lineup-construction
layer consuming this signal look at a lineup's *distribution* of ceiling reads, not just per-player
values, to catch an all-1.0x-floor build.

### 3. Two judgment calls made in synthesis, disclosed here rather than silently assumed

- **Component B (red-zone volatility) gets the same boom-rate fix as Component A**, by direct
  structural analogy -- the FFE's objection to symmetric CV is general (a one-sided output fed a
  symmetric statistic), not specific to role-share. **Not independently reviewed by the FFE.**
- **The aDOT alignment split degrades to a position-label split (WR pool, TE pool).** The FFE's
  requested true alignment split (joker/flex TE pooled with WR, in-line Y separate) needs PFF's
  `slot_coverage` endpoint (ADR-0001) -- confirmed, by reading `matchup/coverage.py` directly, that
  this is still **not ingested anywhere in this pipeline**, a known, pre-existing, separately
  tracked gap. Not something this round invents a workaround for.

### 4. Chris's scope call: data layer now, live multiplier later

Neither expert would propose the actual `scale_i`/cap constants without a live outcome backtest.
Writing one down anyway would be exactly the unbacked-constant pattern this project's review
process exists to catch. Chris's call: **build the real, inspectable, testable data layer this
round** (retained per-week series, boom-rate/aDOT computation, cross-sectional z-scoring, ADR-0011
shrinkage) and leave the actual multiplier as an explicit, backtest-blocked follow-up -- not a
placeholder number, a genuinely missing step.

## What was built this pass

`src/nfl_dfs/ceiling/signals.py` -- `CeilingSignal` (player_id, sample_size, raw_value, z_score,
shrinkage_weight, shrunk_z_score; every field `None` with no fabricated value below its gate):

- `role_share_ceiling_signals(pbp, target_week, role)` -- Component A. RB role excludes QB
  scramblers by reusing ADR-0020 Decision 1c's exact `_trailing_qb_ids` mechanism, not a new one.
- `red_zone_ceiling_signals(pbp, target_week, role)` -- Component B. Disclosed limitation: this
  pipeline's red-zone aggregation buckets every pass-catcher (WR and TE alike) into one `ROLE_WR`
  pool with no position split -- a TE's signal is z-scored against the combined pool, not a
  TE-specific one, until a position join is added here too.
- `adot_ceiling_signals(pbp, target_week, position_by_player_id)` -- Component C. Needed a genuinely
  new aggregation (trailing mean `air_yards` per target) -- zero new ingestion, `air_yards` already
  rides the same `import_pbp_data()` pull `usage_share.py` consumes.
- No new `PlayerRoleShare`/`RoleShareResult` fields, no changes to `usage_share.py` itself -- all
  three functions read the same already-public per-week aggregation functions
  (`aggregate_player_week`, `aggregate_player_week_red_zone`) those existing entry points already
  call internally and then discard the week-by-week detail from. This avoids widening a heavily-
  consumed existing type for this construct's sake, matching this codebase's "read-model over an
  existing fact" convention.

Constants (`BOOM_THRESHOLD=1.35`, `MIN_TRAILING_WEEKS=3`, `CEILING_SHRINKAGE_K=6.0`,
`ADOT_MIN_TARGETS=8`) are explicit, named, unvalidated starting placeholders -- flagged as such in
both experts' review, not backtested fact.

**Live-run confirmation, not just synthetic fixtures:** ran against real live 2026 Week 1 pbp data
(2,596 real rows). 86 real RB role-share signals, 237 real WR, 43 real red-zone RB signals, real
aDOT values for real players (A. St. Brown 5.0, T. McBride 6.85, C. Olave 18.0, correctly gated
`None` for every player with only 1 trailing week so far, since only Week 1 exists right now).

**A found and fixed bug during live verification, unit tests alone didn't catch:** assigning
`None` into a gated-out `raw_value` on a float64 pandas column silently becomes `NaN`, not Python
`None` -- `is None` checks downstream missed it and let a `nan` leak through as if it were a real
value. Fixed with `pd.isna()` throughout `_z_score_and_shrink`, confirmed by a dedicated regression
test (`test_adot_ceiling_signals_gates_below_min_targets_without_dropping_the_row`).

## What was NOT built this pass -- explicit, not silently deferred

- The actual `ceiling_multiplier`/`ceiling_projection` live number -- blocked on a real outcome
  backtest (comparable scope to ADR-0012's own return-TD-rate research), Chris's explicit call.
- QB rushing/ceiling entirely -- Option A (defer), tracked as a high-priority follow-up given QB
  anchors every mandatory stack (Section 7).
- The leg-count/confidence flag the FFE required before the eventual multiplier ships.
- Wiring these signals into `PlayerDetailRecord`/the dashboard -- these are raw, unshrunk-toward-a-
  real-prior-population signals at this stage (the z-score population itself is drawn from whatever
  the live pbp pull happens to contain this week, not a researched league-wide baseline the way
  ADR-0019/0020's own priors were) -- not yet a number a person should read off a dashboard row.
- Component B's TE position split, and Component C's true alignment split (both blocked on
  ADR-0001's still-unringested `slot_coverage` data).

## Consequences

- Section 6 has a sixth (fifth-plus-DST) construct in active development, with real, disciplined
  disagreement resolved before any code shipped -- the process worked as designed, not just as
  documentation.
- The eventual live `ceiling_multiplier` backtest has real, already-computed inputs to backtest
  against (boom-rate/aDOT z-scores for real 2026 players) rather than starting from nothing.
- Two extension points are named for the FFE's next pass, not silently dropped: TE-specific
  red-zone population, true-alignment aDOT population once `slot_coverage` lands.
