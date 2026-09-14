# Model Analytics Expert — Statistical Review of PRD Section 6 (post-Phase 0)

Reviewed: `docs/PRD.md` Section 6 and 11, `docs/phase0/data-availability.md`, ADR-0001, ADR-0002.
This is a build-time math review only — not an assessment of football validity (Fantasy Football
Expert's lane) or live performance (Performance Analytics' lane).

## Summary sign-off table

| Component | Status |
|---|---|
| `GameEnvironmentScore` weighting (40/20/20/10/10) | **Signed off with conditions** |
| `GameEnvironmentScore` z-score baselines | **Withheld** — underspecified |
| `StackProfile` game-stack viability function | **Withheld** — no formula exists to review |
| `MatchupContext` multiplier caps (0.85x–1.15x) | **Signed off with conditions** |
| `MatchupContext` protection/coverage stacking | **Withheld** — recommend capped combination, not multiplicative |
| ADR-0001 alignment-coverage approximation | **Signed off with conditions** (confidence threshold + fallback required) |
| ADR-0002 weather thresholds | **Signed off with conditions** (damped magnitude + explicit low-confidence flag required) |

---

## 1. GameEnvironmentScore weighting — signed off with conditions

Two issues beyond "needs backtesting," both fixable without redesigning the formula:

**Arithmetic problem, not just calibration.** The prose lists five weighted bullets summing to
100% (40+20+20+10+10), but the injury/role bullet is explicitly "a flag, not a score... doesn't
blend into the composite numerically." That means the four components that *actually* get blended
(implied total 40, pace 20, PROE 20, weather 10) sum to **90, not 100**. As written, the composite
either caps at 90 or needs implicit renormalization that's never stated. This needs to be resolved
before implementation: either state the four scored weights are renormalized to sum to 100
(→ ~44.4/22.2/22.2/11.1), or state explicitly the score is 0–90 with the flag layered separately.
Right now it's ambiguous, and two engineers would implement it two different ways.

**Double-counting risk between implied total (40%) and pace/PROE (40% combined) — this is real,
not just needing validation.** Vegas totals are not independent of team pace and pass rate; a book
setting a game total already prices in both teams' expected play volume and passing tendencies.
Weighting implied total at 40% and then separately weighting pace and PROE at 20% each means a
fast-paced, pass-heavy, high-total offense could get rewarded for the same underlying "high-volume
passing environment" signal three times. This is exactly the double-counting pattern flagged
generically in the PRD's own open question for pass-protection/coverage — it applies here too and
isn't currently flagged.

Condition for sign-off: before backtesting locks the weights, run a correlation check —
season-to-date implied-total z-score vs. pace z-score, and vs. PROE z-score, across all
team-weeks. If |r| is material (action threshold >0.3–0.4), either (a) residualize pace/PROE
against implied total before z-scoring so each contributes only marginal signal, or (b) collapse
pace+PROE into a single "play-volume" factor rather than two independent 20% weights. If
correlation turns out low, the current three-way split is fine as a starting point.

## 2. Z-score baselines — withheld, underspecified

"Normalized against the season's implied-total distribution" and "z-scored against league average
for the season to date" are not precise enough to implement identically twice. The ambiguity that
matters:

- **Population shape**: is this a per-week cross-sectional z-score (this week's 32 team totals vs.
  each other) or a cumulative season-pooled z-score (all team-weeks played so far, pooled)? These
  produce materially different numbers — cross-sectional isolates "is this team unusual *this
  week*," pooled blends in earlier-season noise and rule/weather drift across the season. The PRD
  needs to pick one and state why.
- **Look-ahead/cutoff**: does "season to date" include the current week's own in-progress data, or
  only completed weeks through last Sunday? Undefined cutoff risks leakage.
- **Early-season degeneracy**: week 1–2 has essentially no current-season sample for pace/PROE
  (n=0 or 1 per team) — a z-score against an empty or near-empty distribution is either undefined
  or dominated by noise. The PRD needs a stated fallback (e.g., blend with prior-season
  full-season distribution, expanding-window weight toward current season as weeks accumulate —
  the same kind of Bayesian blending referenced elsewhere for projection blending, just not
  specified here).
- **Subset**: all 32 teams pooled, or split by conference/division/some other grouping? "League
  average" implies all 32, but say so explicitly.

None of this is a criticism of the underlying idea (z-scoring against a normalized baseline is the
right approach) — it's that "z-scored, baseline: season to date" is under-specified enough that
two implementations would diverge. Recommend: Architect states (1) cross-sectional-per-week vs.
pooled-cumulative, with rationale, (2) explicit as-of cutoff, (3) an early-season fallback rule,
(4) confirmation the population is all 32 teams. Default recommendation: per-week cross-sectional
with a prior-season-blended fallback for weeks 1–4, but that's a recommendation, not something
signed off until the Architect commits to one.

## 3. MatchupContext multiplier caps and stacking — mixed

**Caps (0.85x–1.15x): signed off with conditions.** ±15% is a reasonable, conservative starting
bound — not obviously wrong on its face. But note it's applied identically to the run-game
differential, the pass-protection differential, and the coverage differential, with no stated
check that these three signal types actually produce comparable effect sizes at a given
z-differential. Grade-distribution variance likely differs across run-block, pass-block, and
coverage grades. Condition: when backtesting, fit the z-differential → realized-efficiency-delta
relationship separately per matchup type rather than assuming one shared cap fits all three.

**Stacking policy — withheld, with an actual recommendation.** The PRD's open question asks
whether pass-protection and coverage multipliers should stack multiplicatively or get capped in
combination. Recommendation: **do not stack multiplicatively without a decorrelation step; use
capped combination instead.** Reasoning: these aren't independent signals for a given receiver.
PFF coverage grades are graded on a snap-by-snap basis, and pressure materially contaminates
coverage grading — a quick, inaccurate throw forced by pressure often shows up as a "coverage win"
in the underlying stat line even when the corner did nothing unusual. So for a receiver facing a
defense with both an elite pass rush and a strong-graded corner, the QB-level pass-protection
multiplier (flowing downstream to that receiver's target value) and that receiver's individual
coverage multiplier are both partially pricing the same pass-rush effect. Multiplying two
independent-looking 0.85–1.15 multipliers together (naive range ~0.72–1.32) would systematically
overstate the combined penalty/bonus for exactly the receivers where the confound is largest.

Concrete recommendation: (a) as a v1 default, combine in log-space (sum the two log-multipliers)
and cap the combined deviation at a band narrower than naive multiplication — e.g., ±20–25%
combined, not ±32%; (b) as a specific backtest task, correlate team pass-rush win rate / pressure
rate against opposing receivers' coverage grades across the play sample — if that correlation is
material, residualize the coverage grade against pressure rate before combining. This is a
concrete, assignable task, not just "needs more data."

## 4. ADR-0001 alignment-coverage approximation — signed off with conditions

The three-step derivation (identify plurality slot/perimeter defender by snap volume → pull that
defender's overall grade → blend by receiver's own snap-share split) is statistically legitimate
as engineering-around-a-missing-field, and this is different from inventing a grade from narrative
judgment — every number in the chain is a real pulled value. But it breaks in two specific,
predictable ways that the ADR doesn't yet guard against:

- **Weak plurality margin.** If the "identified" slot defender only edges the next-most-used
  defender by a small margin (e.g., 34% vs. 30% of slot snaps), the label is close to a coin flip,
  but the formula treats that defender's grade as fully representative of "the" slot defender with
  no discount for the weak identification.
- **Small-sample grade instability.** A defender identified as "the" slot or perimeter defender
  off low snap volume (rotational corner, backup pressed into duty) has an unstable underlying
  coverage grade — PFF grades drawn from small snap samples are noisy by PFF's own guidance, and
  this approximation doesn't currently check the identified defender's snap count before trusting
  their grade.
- **Week-to-week churn.** ADR-0001's consequences section implies re-identification "per team, per
  week" — for teams with real rotation, this can flip the "identified" defender week to week on
  sampling noise alone, producing multiplier volatility for the same receiver that doesn't reflect
  an actual matchup change.

Concrete recommendation (accept with conditions, not reject): implement two explicit gates before
using the volume-identified defender's grade:
1. **Margin threshold** — require the plurality defender to hold a stated minimum lead over the
   second-most-used defender in that alignment (e.g., an absolute majority >50%, or a fixed
   percentage-point margin) before treating the identification as reliable.
2. **Snap-count floor** — require the identified defender to clear a minimum coverage-snap count
   in that alignment before their grade is used.

Fallback when either gate fails: degrade to the ADR's own rejected alternative — team-wide overall
coverage grade weighted only by the receiver's snap-share split — rather than trusting a
low-confidence single-defender identification. Separately, consider a rolling (e.g., trailing
3-week) snap-volume window for identification rather than single-week, since single-week
identification is exactly where the margin/small-sample problems above bite hardest — but that's a
recommendation for discussion with the Fantasy Football Expert (personnel-rotation reality is
their call), not a unilateral block.

## 5. ADR-0002 weather thresholds — signed off with conditions

These are public-research figures (Wharton, Advanced Football Analytics, Covers.com, PFF, Action
Network, FantasyLabs) applied wholesale, not fit to this project's own data. That's a materially
different confidence level than the rest of Section 6, which at least uses this project's own data
distributions even before weight validation is done. Position: **acceptable as a v1 starting
point, but not at full stated magnitude, and not without an explicit confidence tag.**

Reasoning: several of the cited sources are not peer-reviewed research (blog/industry pieces),
pool multiple seasons and stadiums with different rule environments (the Advanced Football
Analytics citation dates to a 2012 analysis of an earlier, less pass-friendly era), and were not
fit against DK-scoring specifically. Applying the exact cited figures (5.79→4.62 ANY/A, ~6%
FG-conversion drop, the 5%/8% temperature dips) at full strength risks miscalibration in either
direction with no way to detect it until a season of backtested data exists.

Concrete recommendation: (a) tag this sub-component distinctly as "external, unvalidated" in the
model output, separate from the "data-confirmed" language used elsewhere in Section 6 — those two
things read as similar confidence levels right now and shouldn't; (b) dampen the applied magnitude
for v1 — e.g., apply the cited effect sizes at 50–75% strength as a conservative hedge — rather
than the full cited figures, since the risk of overcorrecting on an unvalidated external curve is
asymmetric (bad lineup advice) versus underweighting it slightly; (c) make this component the
first item Performance Analytics backtests once a season of results exists, since it's
categorically different in provenance from every other Section 6 input.

## 6. Other arbitrary-threshold findings in Section 6

- **`StackProfile`'s "game-stack viability score = a direct function of `GameEnvironmentScore` for
  both teams"** has no actual formula — not an unvalidated threshold but a missing one. Sum?
  Average? Min (bottleneck logic — a stack is only as strong as the weaker team's environment)?
  Product? Prose can't be reviewed for statistical rigor. This needs an actual functional form
  from the Architect before it can be reviewed at all; right now `StackProfile` is not reviewable,
  not just unsigned.
- **Wind bands (10/15/20 mph) and temperature bands (25/55/85°F)**: same unvalidated-external-
  research status as item 5 above — flagging again here since they're literally in Section 6's
  text, not just the ADR.
- **Identical 0.85x–1.15x cap applied across run-block, pass-block, and coverage differentials**
  without a stated check that the three grade-differential distributions have comparable spread —
  covered in detail under item 3, restating here since it's a Section-6-wide pattern (one cap
  reused three times) rather than specific to coverage.

---

## What would move things from "withheld" to "signed off"

1. Architect states the exact z-score population/window for `GameEnvironmentScore`'s implied-total,
   pace, and PROE components (cross-sectional vs. pooled, cutoff, early-season fallback).
2. Architect resolves the 90%-vs-100% weighting arithmetic for the injury/role flag.
3. A correlation check (implied total vs. pace, vs. PROE) is run before the 40/20/20/10/10 weights
   are locked for backtesting.
4. Architect specifies the `StackProfile` game-stack viability function in actual formula terms.
5. Architect/Data Integration Engineer add the plurality-margin and snap-count-floor gates (with
   fallback) to the ADR-0001 approximation.
6. Architect decides the protection/coverage combination rule (capped log-space combination
   recommended over naive multiplication) and schedules the pass-rush/coverage-grade correlation
   check.
7. Weather sub-component gets an explicit "external, unvalidated" confidence tag and a damped v1
   magnitude rather than full-strength cited figures.

None of this requires redesigning the formulas from scratch — all three are structurally
reasonable starting points, consistent with the PRD's own framing that these are drafts pending
review. The gaps above are specific and actionable, not a case for going back to the drawing
board.
