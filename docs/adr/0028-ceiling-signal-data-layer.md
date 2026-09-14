# ADR-0028: `CeilingMultiplier` design + ceiling signal data layer

**Status:** Accepted (data layer implemented, unit-tested, and live-verified against real 2026 pbp
data; Component A's live multiplier is now ALSO backtested, calibrated, and signed off by both
experts -- see "Update (2026-09-14): Component A backtest and calibration" below. Components B/C
remain data-layer-only, per Decision 4.)
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

## Update (2026-09-14): Component A backtest and calibration

Chris asked to continue this work with the actual outcome backtest both experts required before
proposing `scale_i`. Run live (`scripts/ceiling_role_share_backtest.py`), Component A only:

**Methodology:** 5 real seasons (2020-2024; 2025 skipped -- `nfl_data_py.import_weekly_data([2025])`
returns a live HTTP 404, that season's aggregate weekly-stats file isn't published upstream yet,
confirmed directly not assumed). For every `(season, target_week)` with `target_week` in `[4, 18]`,
computed each RB/WR's `shrunk_z_score` using only weeks `1..target_week-1` (the exact live no-
look-ahead window), then joined against that player's REAL actual `target_week` DK points (full DK
Classic scoring computed from `nfl_data_py.import_weekly_data()`'s real box-score columns) and
their own trailing-median DK points over the same window. 20,376 real (player, week) observations.

**What the naive decile-mean approach got wrong, and how it was fixed:** an initial decile-bucketed
analysis (n=10 points per role) showed a clean boom-rate relationship (R²=0.75-0.79) but a noisy,
partly non-monotonic relative-performance relationship (R²=0.26-0.50) -- WR's even showed an
apparent reversal in the top few deciles. The Model Analytics Expert diagnosed this as right-skew
outlier sensitivity in decile means (verified concretely: dropping one outlying RB decile alone
swung that fit's slope by 21%) and required two reruns before ruling on anything: (1) decile
**median** instead of mean (R² improved to 0.68 RB / 0.56 WR), and (2) a real **player-level**
regression (not a 10-point decile fit) of `log(relative_performance) ~ shrunk_z`, chosen
specifically because a log transform compresses outlier influence and produces a genuinely
multiplicative model consistent with this project's own ADR-0005 log-space convention.

**Result -- the player-level regression resolved the ambiguity, not just added another data point:**
both RB (n=5,608, 161 observations excluded for a real DK score of exactly 0 or negative, which
log-space can't represent) and WR (n=13,641, 966 excluded, same reason) produced a positive slope
whose 95% CI clears zero by 4+ standard errors -- RB: slope=0.1177, SE=0.0272, CI=[0.0643, 0.1710];
WR: slope=0.0798, SE=0.0152, CI=[0.0499, 0.1097]. WR's apparent decile-level "reversal" did not
survive at player-level -- exactly the outcome the Model Analytics Expert predicted a coarser
aggregation losing to a finer one on a question about aggregation-induced distortion.

**Final calibration, both experts' explicit sign-off, Component A only:**

```
m_i = max(1.0, exp(scale_i * shrunk_z_score_i))
scale_RB = 0.1177   scale_WR = 0.0798
```

The Model Analytics Expert used the fitted log-space slopes directly (undamped beyond their own
reported CIs), explicitly discarding an earlier eyeballed decile-mean estimate (~0.20 RB) as
superseded rather than blended in, and chose the exponential/log-space form over the originally-
drafted linear form specifically because that's the model the regression actually validated (the
two forms only agree to first order near `z=0` and diverge exactly where a ceiling multiplier does
its real work) and because it matches `capped_log_combine`'s existing log-space convention. The
Fantasy Football Expert signed off on the specific magnitudes (1.08x-1.27x across realistic z
ranges), the exponential shape (verified it doesn't reward a fluky thin-sample spike, since
`shrunk_z >= 1.5` is structurally hard to reach off a 3-week sample given ADR-0011's shrinkage
math), and the RB > WR asymmetry (matches real football intuition -- an RB touch converts to DK
points more mechanically than a WR target, which has an extra catch/accuracy-dependent conversion
step a role-share spike alone can't see).

**Two explicit, non-blocking watch items carried into Consequences below, not silently dropped:**
(1) WR's decile-median series still shows a soft plateau in the top few deciles even after the
outlier fix -- both experts read the player-level evidence as outweighing it for sign-off, but
flagged it as the first place to check if live WR ceiling reads under-perform at high z. (2)
Component A's boom-rate has no game-script awareness -- a trailing team's garbage-time WR target
spike is a legitimate boom-rate read here even though the same game's spread simultaneously damps
that team's `StackProfile` bring-back viability elsewhere; not a contradiction (garbage-time
production genuinely scores), but flagged for whoever eventually wires this signal alongside
`StackProfile` to confirm the interaction is intentional, not a silent double-count.

**Implemented:** `ceiling/signals.py`'s `component_a_multiplier(signal, role)` -- returns `None`
(never a fabricated neutral `1.0`) when the underlying signal didn't clear `MIN_TRAILING_WEEKS`,
same "unknown is not neutral" discipline the Fantasy Football Expert required for QB's still-
uncalibrated ceiling read. 5 new tests. Live-verified end to end against a real synthetic boom/no-
boom pair (boom-week player: real `1.034x`; steady player: correctly floors at exactly `1.0`).

**Explicitly still not done:** Component B (red-zone boom-rate) and Component C (aDOT) have no
backtest and no live multiplier -- the boom-rate fix was only extended to B "by direct structural
analogy," never independently reviewed, and the Model Analytics Expert specifically flagged that A
and B are plausibly correlated (both usage-volatility signals off overlapping trailing-week data)
and must be checked for the same double-counting ADR-0005 already resolved for pass-protection/
coverage before any combined cap is set. The combined `capped_log_combine` cap across A/B/C, and
`ceiling_projection = blended_projection × ceiling_multiplier`'s actual wiring into
`PlayerDetailRecord`/the dashboard, both remain open. Non-clustered standard errors are a named,
non-blocking caveat (both experts stress-tested a 2x SE inflation and both CIs still cleared zero,
but true `player_id`-clustered SEs should be computed before this becomes a Performance Analytics
drift-monitoring baseline).

## Update (2026-09-14): Component B design fix, backtest, and a real, surprising null/negative result

Continuing to Component B (red-zone-share boom-rate) per Chris's request. The Fantasy Football
Expert did the required independent design review (Component B had only been extended from
Component A "by direct structural analogy," never independently reviewed) and found a real bug
before any backtest ran: `aggregate_player_week_red_zone` only produces a row for a (player, week)
when that player actually recorded a red-zone touch -- a real "red-zone shutout" week (active
player, team reached the red zone, player got no touch there) is indistinguishable from "team never
reached the red zone that week," silently dropping exactly the bust weeks a boom-rate statistic
needs to be meaningful. Fixed: `red_zone_ceiling_signals`/`_zero_fill_red_zone_weekly` now zero-fill
real shutout weeks while correctly still excluding true no-red-zone-trip weeks, verified with a
dedicated regression test and live real-2024-data spot check.

With that fix in place, the same backtest methodology that calibrated Component A was run for
Component B (`scripts/ceiling_red_zone_backtest.py`, 5 seasons, 19,980 real observations), plus the
Model Analytics Expert's required Component A/B correlation check. **The result contradicts both
experts' stated priors:** A/B correlation came back essentially zero for both roles (RB r=0.042, WR
r=-0.128) rather than the "moderate positive, decoupling in identifiable archetypes" both expected.
Component B's own relationship with real outcomes: RB shows no statistically real signal (95% CI
includes zero, [-0.0958, 0.0101]); WR shows a real, statistically significant **negative**
relationship (CI=[-0.1380, -0.0824], fairly monotonic across deciles, R²=0.437 at the decile level)
-- a red-zone-share spike predicts *worse* subsequent relative performance for WR, the opposite
direction from Component A.

Sent to both experts for interpretation (in progress) rather than resolved unilaterally: is this a
remaining construction artifact (small-denominator red-zone-share noise, the zero-fill's effect on
sample composition) or a real football finding (e.g., red-zone share may be too low-frequency an
event to support a boom-rate framing the way overall role share does, or WR red-zone spikes may
reflect a one-off matchup exploit a defense adjusts to rather than a durable role signal)? No
`scale_B` proposed or implemented pending that interpretation -- this update documents the honest,
surprising result as found, not a conclusion.
