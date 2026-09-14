# ADR-0030: Trailing QB rushing-opportunity profile -- descriptive, not predictive

**Status:** Accepted (implemented, unit-tested, live-verified)
**Date:** 2026-09-14
**Owner:** Chris, per his explicit direction ("QB rushing usage module," then "descriptive first,
then backtest")
**Related:** ADR-0028 (`CeilingMultiplier`'s data layer -- explicitly deferred QB rushing rather
than ship an unsafe fallback), ADR-0029 (the receiving-opportunity precedent this module mirrors),
`src/nfl_dfs/ingestion/qb_rushing_profile.py`, `src/nfl_dfs/composition/player_detail.py`

## Context

QB rushing has been a real, named gap in this project since ADR-0028's `CeilingMultiplier`
investigation: *"QB rushing explicitly recommended out of scope (nothing in this codebase computes
QB rushing at all -- comparable in scope to the original `RoleShare` research pass, not a
footnote)"* -- and a shortcut "degraded QB fallback" was explicitly **rejected** there as
architecturally unsafe, since an unshrunk/ungated QB rushing leg could only ever inflate a QB's
ceiling with no self-correcting mechanism. Elsewhere in the codebase, QB rushing only ever appears
as **contamination to filter out** of the RB role (`usage_share.py`'s `_trailing_qb_ids`/
`QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS`) -- never as a signal in its own right. Today, QB
projections carry whatever rushing total the vendor's blended number bakes in, with zero
first-party signal or adjustment layered on top, and QB has no Player Detail dashboard section of
its own at all (every existing section -- role share, snap share, red zone, receiving profile,
own-scheme splits -- is receiving/RB-carry focused and explicitly excludes QB).

Chris chose to build this in two stages: a real, descriptive data layer first (this ADR), with a
proper backtested `CeilingMultiplier` component as separate follow-on work later -- the same order
Component A followed (data layer, then calibration), and explicitly the safer path given ADR-0028's
"degraded fallback" warning.

## Decision

**These are explicitly descriptive facts, not a predictive score** -- no z-scoring, no shrinkage,
no backtested claim, no ranking, same posture as `receiving_profile.py` (ADR-0029).
`ingestion/qb_rushing_profile.py`'s `trailing_qb_rushing_profiles` computes real trailing
(no-look-ahead) numbers straight from `nfl_data_py.import_pbp_data()`, one row per identified
trailing passer:

- `trailing_rush_attempts`, `trailing_designed_runs`, `trailing_scrambles` -- pbp's own
  `qb_scramble` flag splits designed runs from scrambles (confirmed live against real 2025 pbp:
  reliably populated on every QB rush row; e.g. J.Hurts logged 99 trailing rush attempts, 41
  scrambles / 58 designed runs -- a real, sizeable split, not noise).
- `designed_run_rate` -- `trailing_designed_runs / trailing_rush_attempts`, `None` (never a
  fabricated 0.0) when `trailing_rush_attempts` is zero.
- `trailing_rushing_yards`, `trailing_rush_tds` -- straight sums of pbp's `rushing_yards`/
  `rush_touchdown`.
- `trailing_redzone_rush_attempts` (`yardline_100 <= 20`, `usage_share.py`'s existing red-zone
  definition) and `trailing_goalline_rush_attempts` (`yardline_100 <= 5`, the standard "inside the
  5" goal-line definition) -- raw counts, not shares (no team-share denominator computed here,
  same choice `receiving_profile.py` already made: report rate stats, not team-share stats).

**"QB" means "identified trailing passer," not a roster-position join** -- reuses
`usage_share.py`'s existing `QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS` (>= 5 trailing pass attempts)
and `aggregate_passer_week` directly, rather than duplicating the threshold or adding a new roster-
position dependency. A player with rush volume but who has never cleared that passer threshold
(e.g. a jet-sweep WR) is correctly absent from this module's output.

Wired into `PlayerDetailRecord` (`qb_rushing_profile`/`qb_rushing_profile_reason`), gated on
`position == "QB"` directly (not a shared position-set constant like `SCHEME_SPLIT_POSITIONS`,
since this is the only section scoped to just one position) and surfaced in the Player Detail
dashboard as a new "QB Rushing" expand block -- the QB-only mirror of the skill-position-only
Role Share/Snap Share/Red Zone/Receiving Opportunity blocks (each already omitted for QB; this one
is omitted for every other position).

## Consequences

- Closes ADR-0028's explicitly-named QB rushing gap without touching `CeilingMultiplier` at all --
  no risk of the "ungated leg can only ever inflate a QB's ceiling" failure mode that ADR-0028
  rejected, since this module ships no live number into any formula.
- Chris gets real designed-run rate, scramble rate, and red-zone/goal-line rushing volume for the
  exact tie-breaking use case his earlier framing named (ADR-0029) -- e.g. distinguishing a QB
  whose rushing floor comes from a real, repeatable designed package (tush-push short-yardage
  runs, read-option keepers) from one whose rushing is mostly scramble variance.
- No new ingestion: `qb_scramble`/`rushing_yards`/`rush_touchdown`/`yardline_100` are all confirmed-
  present, already-relied-upon pbp columns (`yardline_100` already backs `usage_share.py`'s
  red-zone work; the others are newly read by this module but live-confirmed reliable).
- Live-verified: runs end-to-end against a real 658-player slate pull with no errors; the "QB
  Rushing" block renders correctly for all 89 QB rows on the slate, correctly showing "no trailing
  QB rushing-opportunity profile for this player ... haven't cleared usage_share.py's trailing-
  passer identification threshold yet this season" for week 1 (no trailing weeks exist yet) --
  same expected characteristic as every other trailing-stat section this early in a season.
- A future backtested `CeilingMultiplier` QB rushing component, if pursued, is separate follow-on
  work building on top of this module's already-real designed-run/scramble split -- not blocked or
  pre-empted by anything decided here.

## Update (2026-09-14): the backtested CeilingMultiplier component this ADR left open (Component D) is closed -- a clean null, this descriptive layer unaffected

Chris directed exactly the two-stage plan this ADR's "Consequences" section anticipated:
descriptive layer first (this ADR, unchanged), then a real backtested `CeilingMultiplier`
component. That backtest -- full design review by both experts, a 6-season live outcome backtest,
an out-of-sample holdout check (new to this project's methodology), and both experts' interpretation
of the results -- is fully recorded in **`docs/adr/0028-ceiling-signal-data-layer.md`'s own
"Update (2026-09-14): Component D (QB rushing)" section**, not restated here.

**Short version:** designed-run boom-rate and scramble-rate both came back clean nulls -- most
decisively via a train/holdout sign flip on both legs, a failure mode this project's methodology
had never checked for before this component. No live `CeilingMultiplier` leg ships for QB rushing.
Both experts explicitly confirmed this null does NOT affect this ADR's descriptive data: "doesn't
predict ceiling in aggregate across a pooled cross-section of QBs" and "useful context for a human
comparing two specific QBs this week" are separate questions, not two readings of the same fact --
the same distinction this project already settled for Component C/aDOT (ADR-0029). The Fantasy
Football Expert's own words: the quantization finding behind the null (most qualifying QBs sit at
a trailing median of 1-2 designed runs) is actually a small, concrete argument *for* trusting the
raw numbers as-is -- knowing a QB's baseline sits at 2 tells a person eyeballing the dashboard that
a 3-run week isn't a real usage change worth weighting heavily, exactly the judgment call a human
applies to raw counts that a mechanical score can't replicate without inventing false precision.

A genuinely different future candidate was named (not a retry of what was tested here): **explosive-
rush rate**, a boom-shaped statistic on yards-per-rush-attempt over pooled designed+scramble
attempts (not gated to designed runs alone) -- would need its own independent design review before
any future backtest, per this project's standing discipline.

## Update (2026-09-14): explosive-rush rate (Component E) also closed as a clean null -- the QB-rushing CeilingMultiplier line of work is done, this descriptive layer is the permanent answer

Chris directed pursuing explosive-rush rate next. Both experts jointly designed it as a LEVEL
signal (trailing pooled designed+scramble explosive-rush rate, 15+ yard threshold, a floor DERIVED
from a target standard error rather than asserted), backtested it against 6 real seasons with the
same rigor Component D established (cluster-robust SEs, a train/holdout split, plus two required
scramble-share diagnostics and a goal-line-share diagnostic). Full record:
`docs/adr/0028-ceiling-signal-data-layer.md`'s "Update (2026-09-14): Component E (QB explosive-rush
rate)" section.

**Result:** a clean null, if anything more immediately decisive than Component D's -- both the
15-yard primary test and the required 10-yard sensitivity check came back null in both the train
and holdout splits, and a residualized regression showed neither the explosive-rush signal nor
scramble share carries any independent relationship to ceiling outcomes once the other is
controlled for. Both experts signed off on closing it out.

**Both experts explicitly recommend stopping here, not naming a fourth QB-rushing variant.** Three
real hypotheses (Component D's designed-run-count and scramble-rate legs, Component E's
explosive-rush rate) have now been tested against real DK outcomes with this project's strongest
available methodology, and all three nulled. The Fantasy Football Expert's closing framing, worth
keeping as the permanent read on this whole line of work: nothing about these nulls undermines the
real football fact that mobile QBs (Lamar Jackson/Hurts/Fields-type) have a genuinely different
rushing ceiling than pocket passers -- that's visible on tape and in season-long totals, and is
presumably already priced into their vendor baseline projections. What actually got falsified is
the narrower, more specific claim that any of these usage/production statistics adds detectable
WEEK-TO-WEEK predictive signal ON TOP OF that already-priced-in baseline.

**This module's descriptive data is the permanent, final answer for the QB-rushing axis of the
Player Detail dashboard, not a placeholder awaiting a future backtested multiplier.** If anything,
the null strengthens the case for trusting the raw numbers as-is: there's no hidden mechanical
ceiling edge a formula could have extracted instead, so the real designed-run rate, scramble split,
and red-zone/goal-line counts this module already surfaces are doing the real, non-redundant work
of the tie-breaking use case Chris originally asked for.
