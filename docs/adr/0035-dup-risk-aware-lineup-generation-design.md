# ADR-0035: Dup-risk-aware lineup generation -- design resolved, real thresholds set, not yet built

**Status:** Accepted (design review complete, both experts signed off with conditions, backtest run
live against real settled-contest data, disagreement between experts resolved empirically -- the
optimizer implementation itself is NOT built this round, see Consequences)
**Date:** 2026-09-15
**Owner:** Chris, direct continuation of ADR-0034's "deserves its own review" note
**Related:** ADR-0032 (`lineups/` backfill), ADR-0033 (the calibration), ADR-0034 (the dashboard-only
read this design would act on), `src/nfl_dfs/analysis/dup_risk_lineup_backtest.py`,
`src/nfl_dfs/optimizer/lineup.py` (untouched by this round)

## Context

ADR-0034 wired real dup-risk data into the dashboard as a read-only display and explicitly named
the obvious next question: should the optimizer actually ACT on this, trading some projected points
for lower predicted field duplication across the 3 generated lineups? Chris chose to explore it.
Because this would be the first change to `optimizer/lineup.py`'s actual selection behavior driven
by a Section 6/7-adjacent construct, this project's standing two-expert design-review discipline
applied in full, run exactly as for every prior formula (CeilingMultiplier's components, the
ownership/dup-risk calibrations themselves) -- design review, then a real backtest, then
interpretation, then (when a disagreement surfaced) a further real check to resolve it.

## Decision

### 1. Mechanism (Model Analytics Expert's design review)

**Post-hoc candidate selection, not a hard ownership cap and not a linear penalty term in the ILP
objective.** Generate an oversampled pool of distinct-core-stack candidates (the existing no-good-cut
mechanism `optimizer/lineup.py` already uses, unchanged, just run further -- e.g. 15-25 candidates
instead of 3), classify each candidate's average projected ownership through the real historical
dup-rate lookup (`analysis/dup_risk_calibration.DupRiskLookupTable`/`classify_avg_ownership`, the
same production table ADR-0034's dashboard wiring already uses), then select the final 3 by a rule
over that real candidate set. Explicitly rejected: a hard cap (ownership distributions shift
slate-to-slate, risks infeasibility) and a linear ownership penalty in the objective (the real
relationship isn't linear -- confirmed directly by the backtest below, which found a flat region
followed by a sharp cliff, not a smooth gradient a single linear coefficient could represent without
either being too weak to matter or over-penalizing the harmless flat region).

### 2. Original 3-lineup proposal and the Fantasy Football Expert's pushback

Original proposal: Lineup 1 unchanged (highest projection, no dup consideration); Lineup 2 =
best-projection candidate avoiding the top ownership deciles; Lineup 3 = explicit max-leverage,
lowest-ownership-bucket build, "accepting more projected-points sacrifice by design."

The Fantasy Football Expert gave conditional support to Lineup 2's mechanism but explicitly
objected to Lineup 3 as specified, for two real reasons: **(a)** it conflicts with PRD Section 7's
own verbatim text, *"none of the 3 should be... a pure max-leverage punt"* -- a real, documented
conflict, not a minor phrasing gap; **(b)** ADR-0033's own 2024-2025-only findings showed a mild
U-shape at the extreme low-ownership end (the lowest decile had a HIGHER dup rate than the deciles
just above it, not the lowest) -- raising the concern that pushing to the literal ownership floor
might itself be a recognizable, convergent "fade everything" pattern other sharps also land on,
defeating the purpose. Required a real backtest before trusting either lineup's design.

### 3. The backtest: does the relationship hold once conditioned on GENUINELY STRONG lineups?

Built `analysis/dup_risk_lineup_backtest.py` (`fit_strong_lineup_dup_analysis`/
`fit_strong_lineup_dup_analysis_pooled`) -- uses ONLY real, already-settled ResultsDB field data
(no period-correct historical vendor projections needed, which this project doesn't have,
ADR-0018/ADR-0025). "Strong" is defined in-sample: within each real contest, the lineups scoring in
the top 1% of THAT CONTEST's own real point distribution. Classifies each strong lineup's real
`avg_own` through the exact production `DupRiskLookupTable` (2023-2025 window) the live optimizer
would use, then reports REAL mean points and REAL dup rate per bucket among strong lineups only --
not the calibration's own whole-field estimates.

**Real result, pooled 2023-2025 (n=37,906 strong lineups, 27 real contests):**

| bucket (0=lowest own) | n | mean points | dup rate |
|---|---|---|---|
| 0 | 1,906 | 186.89 | 0.42% |
| 1 | 2,552 | 189.10 | 0.67% |
| 2 | 3,062 | 191.06 | 0.52% |
| 3 | 3,589 | 192.68 | 0.98% |
| 4 | 3,905 | 193.28 | 1.23% |
| 5 | 4,132 | 194.23 | 1.69% |
| 6 | 4,520 | 195.44 | 2.23% |
| 7 | 4,653 | 197.10 | 2.82% |
| 8 | 4,669 | 197.44 | 5.12% |
| 9 (highest own) | 4,918 | 195.58 | 11.47% |

### 4. Both experts' interpretation of the pooled result

**Both agreed bucket 0 was the wrong original target for Lineup 3.** The Model Analytics Expert's
statistical read: bucket 0 gives up ~10.5 real points versus bucket 8 for a dup-rate "benefit" that
isn't statistically real (bucket 0's 0.42% vs. bucket 1's 0.67%/bucket 2's 0.52% are within ~1.1
combined standard errors of each other -- not a clean monotonic win for going lower). The Fantasy
Football Expert's football read, independently: the ~10.5-point gap is "the signature of a bad
process bet... the floor bucket isn't maximally differentiated, it's degenerate." Both also noted the
U-shape that originally worried the Fantasy Football Expert did NOT replicate once conditioned on
strong lineups (bucket 0 has the single lowest dup rate of all 10 buckets among strong lineups) --
reassuring on safety, but not evidence FOR bucket 0 as the actual target, since buckets 1-2 are
statistically tied with it on dup rate while scoring more real points.

**Where the two experts disagreed: bucket 7 or bucket 8 as Lineup 2's ceiling.** The Model Analytics
Expert's statistical read: the bucket 7→8 point gain (197.10→197.44, 0.34 points) is not
distinguishable from noise given real lineup-score variance at this scale, while the bucket 7→8
dup-rate cost (2.82%→5.12%, ~5.75 combined standard errors) is a real, well-powered effect --
recommended bucket ≤7. The Fantasy Football Expert's football read: bucket 8 uniquely combines the
highest real points AND a real jump in duplication, the signature of genuinely correctly-priced elite
chalk (worth the extra duplication), not lazy chalk -- recommended bucket ≤8, and flagged that only
27 pooled contests (~9/season) meant this should be checked season-by-season before trusting the
pooled bucket-8 peak, the same "checked across every season" discipline ADR-0025/0033 already
established for their own calibrations.

### 5. The per-season consistency check that resolved the disagreement

Ran `fit_strong_lineup_dup_analysis` separately for 2023, 2024, and 2025 (not pooled) -- the exact
check the Model Analytics Expert required before hardcoding a bucket-7/8 boundary:

- **2023** (n=15,282 strong lineups, 10 contests): bucket 7 PEAKS (204.43 pts, 2.79% dup); bucket 8
  is WORSE on points (203.30) with much higher dup (4.18%).
- **2024** (n=11,056, 8 contests): bucket 8 beats bucket 7 (193.92 vs. 191.31 pts) -- but bucket 9
  beats BOTH (202.39 pts, the season's single highest bucket-mean), an outlier pattern not seen in
  either other season.
- **2025** (n=11,568, 9 contests): bucket 7 PEAKS again (192.63 pts, 2.55% dup); bucket 8 is worse
  (191.97) with much higher dup (5.64%).

**Two of three independent seasons show bucket 8 offering NO points benefit over bucket 7 while
costing real, substantial extra duplication -- the pooled "bucket 8 is the sweet spot" reading was
being driven by 2024 alone, itself an anomalous season (even bucket 9 outperformed there, a pattern
absent from 2023/2025).** This is precisely the instability the Model Analytics Expert's own required
check existed to catch, and it resolves the disagreement in their favor. Bucket-0-2 remained
statistically interchangeable in all three seasons independently, confirming both experts' shared
conclusion for Lineup 3.

## Final resolved design

- **Lineup 1**: unchanged -- best-overall candidate from the oversampled pool, no ownership
  consideration. The single-entry-safe build, matching current behavior exactly.
- **Lineup 2**: best real-points candidate with ownership bucket **≤ 7** (production
  `DupRiskLookupTable`, 2023-2025 window). Bucket 8 is explicitly excluded as the primary target
  (real dup-rate cost, unreliable points benefit) -- usable only as a fallback if the candidate pool
  has nothing at bucket ≤7.
- **Lineup 3**: best real-points candidate within ownership buckets **0-2** (not forced to bucket 0
  specifically -- these three buckets are statistically tied on dup rate; take whichever scores best
  among them).

## Consequences

**Not built this round, deliberately.** Chris chose to stop at a fully resolved, data-validated
design rather than proceed straight to implementation, given the real remaining scope: an oversample
mechanism in `optimizer/lineup.py`, live projected-ownership wiring into `generate_lineups` (joined
via the same `PlayerIdentity.sources["rotogrinders"].native_id` path `composition/player_detail.py`
already uses), the bucket-based selection rule itself, and -- per the Fantasy Football Expert's
explicit requirement -- a PRD Section 7 amendment (their proposed language: *"a leverage build is not
defined by ownership floor-seeking; it must clear [bucket ≤3 in the production dup-risk lookup]
while remaining among that slate's real point-optimal candidates at that ownership level"* --
Section 7's current "none should be a pure max-leverage punt" language is otherwise left ambiguous
about where that line sits, which this backtest is the first thing to give a real, data-backed
number for).

**Two real preconditions the Fantasy Football Expert named for whenever this IS built, not
optional:** (1) the "strong" filter this backtest used (top 1% of a REAL settled contest's own point
distribution) has no direct equivalent at live selection time (the optimizer doesn't know final
contest outcomes) -- whatever operational proxy for "strong" feeds the live oversample-and-select
step (most plausibly: rank by `blended_projection` within the candidate pool itself) needs to be
checked that it actually approximates the backtest's own definition, not assumed to transfer
cleanly; (2) this is a bigger step than a descriptive dashboard read -- it should be documented as
its own ADR when built (this one, extended, or a fresh one), not folded in as an implementation
detail, so ADR-0034's "not yet wired into anything live" language gets a clean, explicit update
alongside the actual code change.

**Real infrastructure delivered this round, independent of the deferred implementation:**
`analysis/dup_risk_lineup_backtest.py` (unit-tested, live-verified against real 2023-2025 data) is
a real, reusable tool -- re-running it against an extended backfill (the 2020-2022 seasons, already
on disk per ADR-0032's addendum) or against a different `top_pct` threshold is a one-line change,
not new engineering, whenever this design is picked back up.
