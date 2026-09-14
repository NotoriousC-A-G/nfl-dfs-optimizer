# Fantasy Football Expert — Section 6 Domain Review

Reviewed: `docs/PRD.md` (Sections 3, 6, 7, 11), `docs/adr/0001-matchup-context-alignment-coverage.md`,
`docs/adr/0002-weather-impact-thresholds.md`. Sign-off report only — no files touched, per role.

## Summary table

| # | Item | Status |
|---|---|---|
| 1 | ADR-0001 alignment-coverage approximation | **Signed off with conditions** |
| 2 | Run-game grade-differential multiplier | **Signed off with conditions** |
| 3 | Coverage man/zone split ("zone" as one bucket) | **Signed off with conditions** |
| 4 | Weather team-agnostic curves / dome-travel deferral | **Signed off** (deferral correct) — recommend re-prioritizing, not blocking |
| 5 | Section 7 game-stack favoring by GameEnvironmentScore | **Withheld** |
| 6 | Section 7 RB/DST pairing flat penalty | **Withheld** |
| 7 | DST projection in Section 6 | **Withheld — not a formula issue, a missing formula** |

No item is a hard blocker on the whole PRD (nothing here is "this will actively lose money if
shipped as-is"), but items 5, 6, and 7 need concrete changes before this reviewer would sign off
Section 6/7 as a package, since they touch lineup construction directly, not just a matchup-grade
nuance.

---

### 1. ADR-0001 alignment-coverage approximation

**Not fatal for v1, but signed off only with a guardrail added.** The plurality-by-snap-volume
approach breaks in exactly the situations that matter most for DFS:

- **Shadow coverage.** A defense that shadows its CB1 onto the opponent's #1 WR (common vs. true
  alpha receivers) will show that CB1 taking most of his snaps at perimeter on base downs, but
  he's the one who travels into the slot on passing downs against the receiver whose target share
  actually drives the stack thesis. Plurality-by-total-snaps would misidentify "the slot
  defender" as whoever else fills that spot on early downs — assigning the *wrong* grade on
  exactly the passing-down snaps the multiplier is supposed to price.
- **Situational dime/big-nickel.** A 5th/6th DB who only plays obvious-passing-down snaps
  (3rd-and-long, 2-minute) can take over the slot precisely when it matters but never accumulate
  plurality snap volume across the whole game, so the formula defaults to a base-package defender
  who isn't even on the field for the routes that count.
- **Zone-match schemes** (Cover 3/quarters teams) don't have a fixed man-assigned "slot defender"
  at all — the slot is whoever's zone the route enters, which can be a safety or backer on a given
  down. Averaging that into one defender's grade for the week smooths away real variance the
  offense is actually exploiting.

**Recommendation:** don't drop the approximation (ADR-0001's own alternatives-considered section is
right that it's still better than nothing), but add a suppression/confidence-downgrade condition
beyond the near-even-snap-split check the ADR already flags for the Model Analytics Expert: also
downgrade confidence when a team's primary corner's snap-location pattern correlates with the
opponent's #1 receiver's alignment (a computable shadow-detection heuristic, not a narrative
judgment) rather than with a fixed slot/perimeter role. That keeps it in the "real numeric input"
category the Architect's ground rule requires.

### 2. Run-game grade-differential multiplier

**Signed off with conditions.** A team-level run-block-vs-run-defense grade differential is a
reasonable floor but misses situational box count — the single biggest lever on RB efficiency
play-to-play.

Concrete scenario: a run-block/run-defense grade differential favors the RB's team, so the formula
bumps RB efficiency — but the RB's team is a big underdog everyone expects to lean run to control
clock, so the defense loads the box (extra safety/LB down) specifically to take the run away
regardless of blocking-grade quality. The multiplier reads bullish on efficiency and especially
explosive-run rate; the actual outcome is a stuffed box. Explosive-run rate specifically depends on
second-level blocks (a puller reaching a linebacker, a receiver springing a screen/edge run) more
than point-of-attack grade, which the season-aggregate differential doesn't see either.

**Recommendation:** not a blocker for v1 given the box-count data isn't in Section 6's confirmed
inputs, but flag this explicitly as a known limitation on the explosive-run-rate piece specifically
(efficiency/YPC is a more defensible use of this input than explosive-run-rate is), and note
nflverse's play-by-play already has the fields to derive box counts/personnel groupings as a v1.1
cross-check — same treatment as the gap/zone split the Architect already deferred.

### 3. Coverage — "zone" as a single bucket

**Signed off with conditions — this is a real oversimplification, flag it rather than let it pass
as resolved.** Cover 1, Cover 2, Cover 3, and quarters are all "zone" in PFF's man/zone split, but
they create very different exploitable voids by route concept. A seam route against Cover 3
(single-high, deep thirds) attacks a real hole between the flat defender and the deep third; the
same route against Cover 2 (two deep safeties) faces a totally different picture — Cover 2
specifically exists to take away exactly that shot to a deep-threat X receiver, even though a
team's *aggregate* zone grade might be mediocre because Cover 2 corners get beaten underneath a
lot, dragging the average down. A defense with a "bad" aggregate zone grade could still be running
the single best zone shell against your specific receiver's skill set that week, and the formula
would read it as a good matchup.

**Recommendation:** acceptable for v1 given Phase 0 didn't confirm shell-level (Cover 1/2/3/4)
grade splits exist in PFF's API — but this should be documented in Section 6 as an explicit blind
spot, not implied to be resolved just because man/zone is data-confirmed. Worth a quick Data
Integration Engineer check on whether `coverage_scheme` or another endpoint exposes shell-level
granularity before treating it as out of reach.

### 4. Weather — team-agnostic curves, dome-travel deferral

**Signed off — the architectural split (game-level score vs. a future MatchupContext adjustment)
is the right call, don't fold it into GameEnvironmentScore.** But push back on leaving it a
low-priority "candidate" rather than fast-tracking it: dome-team-travels-to-cold-outdoor-game is
one of the most well-established DFS fade signals in the industry, not a speculative nuance.
Concrete scenario: a dome team's skill players traveling to a 20°F, 20mph-wind outdoor game — the
team-level weather penalty in GameEnvironmentScore applies identically to both the traveling dome
team and the home team, whose personnel and offensive scheme are built for exactly that
environment. Treating them as equally penalized structurally underrates the split, and the
underlying data (home stadium type: dome vs. outdoor) is trivially available with no new
dependency.

**Recommendation:** keep it out of v1's team-agnostic score as decided, but recommend it be
prioritized as a near-term (v1.1, not "someday") MatchupContext addition given how cheap the data
is and how well-established the effect is.

### 5. Section 7 — game stacks favored in highest-GameEnvironmentScore games

**Withheld.** GameEnvironmentScore is purely pre-game (implied total, pace, PROE, weather) and,
per Section 6's own output line, is scored per team per game — good, that avoids the worst version
of this problem (a single shared game-level number hiding a lopsided split). But Section 7's rule
as written ("game stacks... favored in the highest-GameEnvironmentScore games") doesn't reference
the *spread* between the two teams' scores or the point spread itself as a dampener on the
bring-back thesis specifically.

Concrete scenario: Team A implied 30, Team B implied 17, combined total 47, spread -13. Team A's
individual GameEnvironmentScore reads strong (good total, plausibly good pace/PROE), so the game
clears the bar for "favor a game stack." But a 13-point spread means the realistic outcome is Team
A builds a lead, Team B abandons the game plan, and by the second half it's one-sided — the
bring-back piece of the stack (opposing pass-catcher) has no path to work because the trailing
team's passing volume, while sometimes inflated in garbage time, is inconsistent and often shifted
to checkdowns/RBs rather than the bring-back WR the lineup was built around. Section 7 as written
has no mechanism to catch this at lineup-construction time — it's a pre-game filter with no
in-construction check on spread magnitude.

**Recommendation:** add an explicit dampener to the game-stack-favorability rule using data Section
6 already computes — cap or discount game-stack (bring-back) viability when the pre-game spread
exceeds a threshold (e.g., double digits), independent of how good the combined/individual
GameEnvironmentScores look. This is implementable now, not a new data dependency.

### 6. Section 7 — RB/DST pairing penalty (flat, not constraint)

**Withheld.** A flat penalty treats a "bad matchup" identically regardless of game script, but the
actual risk is highly script-dependent, and Section 6 already computes the inputs (spread, each
team's implied total) needed to modulate it.

Concrete scenario A (low actual risk, currently over-penalized): RB's team is a solid favorite in
a lower-total game (run-funnel script) — even against a good run-defense DST, that RB is likely to
see heavy garbage-time-adjacent, clock-control volume in the second half because his team is
protecting a lead, and the opposing DST isn't in a shootout that inflates its own upside either.
The flat penalty dings this pairing the same as scenario B.

Concrete scenario B (high actual risk, currently under-penalized): RB's team is a slight underdog
in a high-total shootout. If they fall behind, the RB gets pulled from the game plan
(negative-script fade) while the DST opposite him picks up sack/turnover value off a broken game
plan forced into obvious passing downs — this is the textbook case the "avoid RB/DST pairing" rule
exists for, and a flat penalty may actually be too soft here relative to scenario A.

**Recommendation:** scale the penalty by the RB's team's implied total and spread (both already in
GameEnvironmentScore) — amplify it when the RB's team is an underdog in a high-total game, dampen
it when the RB's team is a favorite in a lower-total, run-funnel spot. No new data required.

### 7. DST as a separate projection problem

**Withheld — not because a DST formula is wrong, but because there isn't one.** Confirmed: nothing
in Section 6's `MatchupContext` table folds DST into the offensive-skill-position machinery — all
four rows (run game, pass protection, coverage, scheme/game flow) feed RB/QB/pass-catcher
projections only, so Section 3's instruction not to scale down the offensive model for DST is
being honored in the negative sense (no accidental folding-in).

But the positive requirement — DST gets its own projection treatment — is simply absent from
Section 6. There's no `DSTProjection` construct alongside `GameEnvironmentScore`/`StackProfile`/
`MatchupContext`, even though DST is a mandatory roster slot in every single lineup (Section 3) and
Section 3 explicitly flags DST scoring as volatile and TD/sack-driven. This is a gap that blocks
lineup construction entirely, not a refinement to defer — the optimizer has no scored basis for one
of nine roster spots.

**Recommendation:** this needs to come back to the Architect as a missing Section 6 subsection
before Fantasy Football Expert/Model Analytics Expert sign-off on Section 6 can be considered
complete. Even a lightweight v1 formula would work — e.g., a `DSTProjection` built from: opposing
offense's turnover-proneness (INT rate, fumble rate via nflverse), pass-block grade differential
already computed for the pass-rush row (inverted — DST sack upside vs. that same opposing OL), and
opponent implied team total (inverse-correlated with DST scoring ceiling, since garbage-time/
shootout scripts suppress DST value while a defense facing a low-implied-total offense has a better
shot at points-allowed tiers and short fields). This isn't asking for new data sources — it's
assembling inputs Section 6 already pulls elsewhere into a formula that doesn't yet exist.

---

## Net for the Architect

Items 1–4 are workable approximations with documented conditions — sign off on moving them toward
implementation as long as the noted guardrails/flags are added to the PRD text. Items 5 and 6 are
Section 7 construction-rule gaps that need a concrete formula change (spread-based dampening in
both cases) before sign-off, not just documentation. Item 7 isn't a review of a formula at all —
it's flagging that Section 6 is incomplete for a mandatory roster slot and needs a new subsection
before the section as a whole can be called reviewed.
