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

Sent to both experts for interpretation. **Result: both experts converged on "no live multiplier
ships from Component B this round," from two different angles.** The Fantasy Football Expert read
the negative WR result as real and football-explicable (a red-zone-share spike is usually a
single-game matchup exploit a defense scouts and shuts down the next week -- role insecurity, not
growth -- while RB's role is more scripted and less matchup-dependent). The Model Analytics Expert
found a second, real construction bug independent of that football question: `_boom_rate_per_player`'s
zero-median branch was flatly pinning `raw_value=0.0` for any player whose trailing median share
was exactly 0 -- common post-zero-fill for a sparse statistic like red-zone share -- discarding real
spike weeks for exactly the boom/bust players this component exists to catch. They also flagged
that a negative `scale_i` is architecturally impossible in `CeilingMultiplier`'s one-sided,
floored-at-1.0 form regardless of whether the WR finding is real (it would just floor back to 1.0
for the only players it'd ever fire on) -- so WR ships nothing either way, settling that half
immediately.

### The two required reruns, and what they changed

Fixed the zero-median branch (a zero-median player's real nonzero weeks now count as booms against
their own zero baseline, `_boom_rate_per_player`, regression-tested) and reran the full 5-season
backtest with a new required diagnostic: a WR volume-tier split (tercile of trailing team red-zone
plays) to test whether the negative finding was small-denominator noise.

**RB flipped from null to a real, corroborated (if still borderline) positive result:**
slope -0.0429→+0.0519, 95% CI [-0.0958,0.0101]→[-0.0024,0.1062] (barely clears zero), decile R²
0.015→0.523, and -- the most telling change -- **A/B correlation jumped from 0.042 to 0.248**,
landing much closer to both experts' original stated prior ("moderate positive, most players move
together") than the pre-fix near-zero result did. The zero-pinning bug was genuinely suppressing a
real RB signal.

**WR's negative finding survived the volume-tier check cleanly:** slope -0.1102→-0.0784 (magnitude
shrank, direction/significance held, CI=[-0.1099,-0.0469]), and critically the negative slope holds
in ALL THREE volume tiers including the high-volume/least-quantized one (slope=-0.0684,
CI=[-0.1246,-0.0121], mean 6.0 trailing red-zone plays) -- ruling out the Model Analytics Expert's
own construction-artifact hypothesis as the primary driver and corroborating the Fantasy Football
Expert's football-mechanism read instead. WR's A/B correlation stayed near zero even post-fix
(-0.0175), unlike RB's now-real 0.248 -- these really do look like two different kinds of signal
for the two roles.

### Final verdict: Component B ships no live multiplier, for either role, in this round

The Fantasy Football Expert signed off on RB proceeding toward calibration (their read: 0.248 is
"close to exactly" their predicted magnitude for real-but-not-total role/red-zone-trust overlap,
and the WR finding surviving the volume-tier check "firms up, doesn't just fail to kill" the
role-insecurity mechanism -- the negative slope holding evenly across volume tiers is what the
football story predicts and the small-sample-noise story specifically does not). Their one
condition: any eventual `scale_RB` must explicitly discount the confirmed A/B shared variance
(r=0.248, the bell-cow-takeover archetype moves both signals together) rather than treating the two
components as independent -- and treat a barely-clearing CI with real conservatism, the same
discipline Component A's comfortably-significant result didn't need.

The Model Analytics Expert withheld sign-off pending one specific, decisive check: cluster-robust
standard errors (Cameron-Miller sandwich estimator, `player_id` clusters, Stata's small-sample
correction) on the RB regression -- required because the non-clustered SE assumes independence
across a player's own repeated weekly observations, and RB's non-clustered CI ([-0.0024, 0.1062])
was already too close to the line for decile-level/A-B-correlation corroboration to substitute for
that specific robustness check (per their own stated reasoning: `t = 0.0519/0.0277 = 1.87`, short
of the ±1.96 threshold).

**That check is now done.** `scripts/ceiling_red_zone_backtest.py`'s `_linear_fit_clustered_se`
(a from-scratch sandwich-estimator implementation, no statsmodels dependency) on the full
5-season, n=5,439, G=344-player-cluster sample: **slope=0.0519, clustered SE=0.0274, 95%
CI=[-0.0018, 0.1057] -- still includes zero**, by an even tighter margin than the non-clustered
version. Clustering barely moved the SE here (0.0277→0.0274), meaning within-player correlation
across weeks wasn't the dominant source of uncertainty this bordered on -- the result is genuinely,
not artifactually, borderline.

**Final decision, per the Model Analytics Expert's own pre-stated rule ("if the clustered CI
includes zero, RB joins WR as no-live-multiplier this round"): Component B ships nothing for
either role.** RB is a real, corroborated, but statistically insufficient signal -- not
implemented, not because the football story is wrong (the Fantasy Football Expert's football read
and the A/B correlation both suggest it's real), but because it doesn't clear this project's own
bar for a live constant even under the more rigorous check. WR is a real, non-artifact finding
(survived the volume-tier check cleanly) that is architecturally impossible to express in
`CeilingMultiplier`'s one-sided, floored-at-1.0 form -- both experts explicitly want it carried
forward as a named candidate for a future role-security/downside-risk construct, not left as a
dead end.

**What this round leaves behind, concretely:** two real bugs found and fixed in shared ceiling
infrastructure (the red-zone zero-fill, and the zero-median boom-rate pinning -- the latter also
benefits any future component built on `_boom_rate_per_player`), a full backtest+interpretation
methodology now proven out twice (Component A shipped, Component B honestly didn't), and a named,
scoped future construct (WR red-zone role-security) with real preliminary evidence already
gathered rather than starting from nothing.

## Update (2026-09-14): Component C (aDOT) backtested -- a clean, thoroughly-verified null

Completed the three-component set. Component C's design (population split by position label, a
disclosed degradation from the Fantasy Football Expert's requested true-alignment split; low volume
floor + shrinkage rather than a hard gate) was already independently reviewed in the original round
-- this pass was backtest-and-calibrate only, run with the cluster-robust SE check built in from the
start rather than as a follow-up rerun (`scripts/ceiling_adot_backtest.py`, 5 seasons, same
player-level `log(relative_performance) ~ shrunk_z` methodology as A/B).

**Result:** WR (n=6,328, G=301): slope=0.0236, cluster-robust CI=[-0.0096, 0.0567] -- positive
direction, matching the original hypothesis, but doesn't clear zero. TE (n=2,824, G=154):
slope=-0.0236, CI=[-0.0637, 0.0166] -- flips slightly negative, also doesn't clear zero. Decile-level
R² is far weaker than anything this project has produced for a real-but-underpowered signal (WR
0.013, TE 0.030, vs. Component B RB's 0.523 after its own fix) -- the specific pattern this
project's own track record associates with "no relationship," not "suppressed relationship."

**Both experts signed off on closing this out as a real null, no live multiplier for either
position.** The Fantasy Football Expert's read: aDOT conflates role (does this player run deep
routes) with opportunity/quality (does he win them), and is structurally blind to the YAC-driven
half of DK ceiling that scores identically to a genuine deep completion -- a conceptual mismatch
between the metric and the mechanism it was meant to proxy, not a data or population problem. They
explicitly do not think the still-missing true-alignment split would flip the result (it would
mainly clean up TE noise, not fix the underlying conflation) and named a better-specified future
candidate instead: **explosive-target rate** (a boom-shaped statistic matching Components A/B's own
convention, rather than a level stat), possibly combined with a YAC-per-catch signal -- a
genuinely different hypothesis worth its own future round, not a reason to keep Component C open.

The Model Analytics Expert signed off contingent on one cheap due-diligence rerun (already-computed
fields, no new data pull, in the spirit of Component B's own required checks): does `CEILING_SHRINKAGE_K
=6.0` -- borrowed from a weeks-scale signal, never re-derived for aDOT's targets-scale axis --
mask a real signal specifically in the low-trailing-target subgroup where shrinkage bites hardest
(worked out by hand: `w(8)=0.57` vs. `w(100)=0.94`, meaning most of the sample is barely shrunk at
all, but the low end is real)? **Rerun confirms it does not:** refit on the unshrunk `z_score`
(WR slope=0.0198, CI=[-0.0078,0.0475]; TE slope=-0.0199, CI=[-0.0517,0.0118] -- both still include
zero, similar magnitude to the shrunk version) and a trailing-target tercile split (six cells
across both positions, every single one straddles zero, including the low-target tier the original
"low-target-share deep threat" hypothesis was specifically about). This is exactly what the Model
Analytics Expert predicted going in ("my expectation... is that it corroborates rather than
overturns the null") -- confirmed, not just asserted.

**Final state of `CeilingMultiplier`, all three components resolved:** Component A shipped
(`scale_RB=0.1177`, `scale_WR=0.0798`, live in `component_a_multiplier`). Component B ships nothing
(RB real but statistically insufficient even under cluster-robust SEs; WR real but architecturally
inexpressible in the one-sided floor -- named as a future role-security/downside-risk construct).
Component C ships nothing (a clean, thoroughly-verified null for both WR and TE -- named as a
future explosive-target-rate candidate, a different hypothesis from what was tested here). Two real
bugs fixed in shared infrastructure along the way, benefiting any future component. The whole
investigation -- draft, football correction, backtest, bug-hunting, re-interpretation, final
statistical sign-off -- is the process this project's review discipline was built for, run in full,
twice over, on components that ultimately didn't ship as much as the one that did.

## Update (2026-09-14): Component D (QB rushing) -- designed, backtested, closed as a clean null

QB rushing was named and explicitly deferred at this ADR's original round (Decision 1 above: "QB
rushing explicitly recommended out of scope") and a shortcut "degraded QB fallback" was separately
rejected as architecturally unsafe (an unshrunk/ungated leg can only ever inflate a QB's ceiling
with no self-correcting mechanism -- unlike A/B/C, whose one-sided floor makes a null or negative
result self-limiting). After Chris chose to pursue QB rushing as a new construct and directed a
"descriptive layer first, then backtest" staging (matching Component A's own data-layer-then-
calibration order), the descriptive layer shipped first as `ingestion/qb_rushing_profile.py`
(ADR-0030, not gated by this backtest). This update covers the backtest that followed.

**Design review (both experts, before any backtest code was written)** -- the Model Analytics
Expert required a materially stricter process than A/B/C got, specifically because of this
component's asymmetric-downside risk: `raw_value` = boom-rate on trailing DESIGNED-RUN COUNT only
(`qb_scramble == 0`) -- rushing points were rejected outright as a candidate `raw_value` for
smuggling the outcome's own noisiest component back in as the predictor; total attempts
(designed+scramble) was scoped as a secondary diagnostic only. `QB_DESIGNED_RUN_MIN_TRAILING_VOLUME
=8` (`ADOT_MIN_TARGETS`'s value, reused directly) gates a player's own `raw_value` to `None` AND
removes them from the cross-sectional z-scoring reference population (not just their own output) --
a new requirement, since a population dominated by structurally-near-zero pocket passers would
distort the reference distribution for real rushers in a way none of A/B/C's populations risked.
Cluster-robust (player_id) SEs were required from the FIRST run, not as a reactive follow-up to a
borderline result the way Component B needed. A NEW requirement not used by any prior component: an
out-of-sample holdout (fit on 2020-2022, independently check sign/magnitude on 2023-2025) --
explicitly because a false-positive positive slope here has no self-correcting backstop the way a
null or negative A/B/C result did. The Fantasy Football Expert gave conditional sign-off requiring
two real, equally-rigorous additions (not footnotes): scramble RATE as a second confirmatory-grade
test (arguing scramble yardage is plausibly the dominant real ceiling mechanism for the Lamar
Jackson/Hurts/Fields archetype, not noise to be excluded by assumption), and a goal-line-share
diagnostic split on the designed-run population (the same level-vs-variance distinction this ADR
already drew for Component B's red-zone finding -- does a short-yardage-sneak-specialist role get
conflated with a genuine open-field/broken-pocket rushing ceiling).

**Backtest** (`scripts/ceiling_qb_rushing_backtest.py`, `qb_rushing_ceiling_signals` in
`ceiling/signals.py`, same 6-season pull, player-level log-space regression, and DK scoring as
A/B/C) ran every one of the above checks up front, per the Model Analytics Expert's explicit
instruction not to wait for a close call. One real bug was found and fixed during smoke-testing
before the live run: a mid-season QB trade gives `aggregate_trailing_qb_rushing_profile` two rows
for the same `player_id` (one per team), which broke a scalar comparison downstream until the
diagnostic join was changed to sum across teams first -- caught before the full run, not after.

**Result -- a clean null, more decisively demonstrated than Component C's:**

- **Primary test (designed-run boom-rate)**: TRAIN (2020-2022, n=500) cluster-robust 95%
  CI=[-0.2282, 0.1178], slope=-0.0552 (negative). HOLDOUT (2023-2024, n=331) cluster-robust 95%
  CI=[-0.0278, 0.2350], slope=+0.1036 (positive, closer to significance but still crosses zero).
  **The sign flips between train and holdout** -- both experts independently called this the single
  most decisive piece of evidence in the whole result, stronger than any in-sample diagnostic A/B/C
  ever produced, because it's exactly the failure mode the holdout requirement was built to catch.
  Full-sample decile fit: slope=-0.0069, R²=0.003 -- weaker than even Component C's weakest fit.
- **Goal-line-share tercile split**: low/mid/high tiers all flat (R²=0.000-0.001), no monotonic
  pattern, high-share tier's point estimate smaller in magnitude than low-share's -- rules out a
  hidden subpopulation effect the way Component B's WR tier split confirmed a real one existed.
- **Quantization check**: 71.0% of the 186 qualifying player-season windows have a trailing median
  designed-run count <=2, meaning `BOOM_THRESHOLD=1.35` is tripped by the difference between 2 and
  3 designed runs for most of the qualifying population -- both experts flagged this as a real,
  independent problem with applying a boom-rate template to this specific axis, regardless of
  whatever the regression showed.
- **QB-identity-continuity check**: confirmed clean by construction and by direct spot-check (max 3
  distinct qualifying player_ids on one team-season, 2024 Cleveland, matching that team's real QB
  carousel) -- no team-slot leakage between a benched starter and a replacement.
- **Game-script watch item**: Pearson r(residual, trailing avg \|score_differential\|)=0.0547 --
  negligible, not a meaningful confound.
- **Secondary test (scramble rate, a LEVEL signal)**: TRAIN (n=1191) looked real un-clustered (95%
  CI=[0.0857, 0.2642]) but NOT once clustered (95% CI=[-0.0439, 0.3939]) -- the same
  naive-SE-overstates-significance pattern Component B's original RB result showed. HOLDOUT (n=799)
  **flips sign entirely** (slope=-0.0727, cluster-robust CI=[-0.2275, 0.0821]). Both experts read
  this as weaker evidence for a real effect than Component B's RB result ever had, not comparable
  evidence -- B's point estimate never reversed direction across any diagnostic; this one did.

**Both experts signed off on closing this out as a real null, no live multiplier for either leg.**
The Model Analytics Expert's read: this is more decisive than Component C's null specifically
because the sign-instability evidence is direct and dispositive on its own, not one contributing
factor among several ambiguous ones -- and flagged that this is the first component in the whole
investigation checked against genuine out-of-sample replication, catching something no in-sample
diagnostic (decile R², cluster-robust SEs, tercile splits) would have caught alone; recommended
making a train/holdout split a standard part of this project's backtest methodology going forward,
and treating cluster-robust SEs as mandatory-by-default given this is the second component (after
Component B's RB leg) where naive SEs made a usage-regression result look real. The Fantasy
Football Expert's read: conceded their own prior framing (scramble YARDAGE as the dominant real
ceiling mechanism) wasn't what got tested here (scramble RATE was) -- the un-clustered/clustered
gap plus the holdout sign-flip together look like real box-score variance driven by a handful of
correlated player-weeks, not a stable per-player trait, closer to the Model Analytics Expert's
original athletic-variance warning than to their own prior read. Named a genuinely different,
better-specified future candidate (parallel to Component C's aDOT-null → explosive-target-rate
naming): **explosive-rush rate** -- a boom-shaped statistic on yards-per-rush-attempt, computed
over POOLED designed+scramble attempts (not gated to designed runs alone, which nearly halves the
trailing sample for exactly the mobile-QB archetype this component exists to serve -- e.g. Hurts:
58 designed / 41 scramble) -- a different hypothesis (explosiveness conditional on rushing, not
rushing-attempt volume) from anything tested this round, needing its own independent design review
before any future backtest.

Both experts also confirmed this null does not touch ADR-0030's already-shipped descriptive QB
rushing dashboard data -- "doesn't predict ceiling in aggregate" and "useful context for a human
comparing two specific QBs" remain separate questions, the same distinction already settled for
Component C/aDOT (ADR-0029). See ADR-0030's own Update section for that framing applied here.

**Final state of `CeilingMultiplier`, four components resolved:** Component A shipped. Components
B, C, and D all ship nothing live -- B for statistical insufficiency (RB) and architectural
inexpressibility (WR's real negative effect), C and D for clean, thoroughly-verified nulls. Three
named future candidates now on record for a possible future round: WR red-zone role-security
(Component B), explosive-target rate (Component C), explosive-rush rate (Component D) -- each a
genuinely different hypothesis from what was tested, not a retry of the same one.
