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
