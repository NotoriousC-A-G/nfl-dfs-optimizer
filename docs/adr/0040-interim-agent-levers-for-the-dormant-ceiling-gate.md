# ADR-0040: Interim second lever for Explosion/Shootout and Volatility Engine while the ceiling gate is dormant

**Status:** Accepted (implemented, unit-tested)
**Date:** 2026-09-27
**Owner:** Chris
**Related:** ADR-0028 (ceiling signal data layer, `MIN_TRAILING_WEEKS`), `src/nfl_dfs/agents/registry.py`,
`src/nfl_dfs/agents/scoring.py`, `docs/adr/0039-remove-same-team-dst-bonus.md` (same discipline:
don't ship a lever that isn't real, in either direction)

## Context

Reviewing week 3's real generated lineups (as written-up narrative, not just numbers) surfaced that
Explosion/Shootout and Volatility Engine were both producing an all-zero objective delta —
identical to Chalk Anchor and a plain ownership-fade duplicate of Arbitrageur, respectively. Root
cause: both agents' distinguishing lever is `ceiling_lean`, which only has an effect when
`ceiling_multiplier` (ADR-0028, Component A) is non-`None`. That signal requires
`MIN_TRAILING_WEEKS = 3` real in-season completed weeks — weeks 1-3 of every season, this signal
is `None` for all 671 players, by design, not a bug.

Chris asked to "fix the ceiling multiplier now," with kickoff ~4 hours out. Investigation found
this can't be done responsibly same-day: Component A's own backtest
(`docs/adr/0028-ceiling-signal-data-layer.md`, Update section) explicitly restricted itself to
`target_week in [4, 18]` — week 3 was excluded from calibration on purpose, since a 2-week sample
was judged too noisy to trust. Lowering `MIN_TRAILING_WEEKS` to 2 today would let a number populate,
but it would carry the "backtested, both-experts-signed-off" credibility of a value that was never
actually validated at that sample size — the same category of problem ADR-0039 (same-team DST
bonus) removed two days earlier, in the opposite direction (there, an unbacktested lever was live;
here, fixing it live would make a second one live).

Chris chose, given three options (reconfigure the two agents / ship them as-is and label it /
formally investigate lowering `MIN_TRAILING_WEEKS`, which needs its own backtest and expert review,
not a same-day change): reconfigure.

## Decision

Give each agent one additional, already-live, already-calibrated-as-a-slider lever that doesn't
depend on in-season trailing history, chosen to fit each agent's own existing design intent rather
than duplicate an existing single-axis agent:

- **Explosion/Shootout**: `stack_conviction = 0.6` (game_stack_viability-driven). Fits this agent's
  existing `bring_back_allowed=True`/`edge_condition="high_total"` framing — real stack/game-
  environment correlation, not gated on trailing weeks. Kept below Game Script Architect's
  dedicated `0.8` since this agent's edge condition is narrower.
- **Volatility Engine**: `matchup_conviction = 0.4` (own vs. opponent unit grade, single-player-
  level). Consistent with this agent's own `bring_back_allowed=False` "pure single-team, not a
  game-stack play" framing — unlike `stack_conviction`, it needs no stack/bring-back candidacy at
  all. Kept at half of Matchup Purist's dedicated `0.8` so this agent reads as "contrarian pick that
  also wants a live matchup edge," not a clone.

Both additions are additive to `ceiling_lean`, not a replacement — once `MIN_TRAILING_WEEKS` clears
at week 4 of every season, both agents keep their originally-intended ceiling behavior alongside the
new lever.

## Evidence

None — same "disclosed draft, not backtested" posture as every other magnitude in `registry.py`
(ADR-0019/0020's convention). This is a same-day interim fix, not a validated retune.

## Consequences

- Weeks 1-3 every season, Explosion/Shootout and Volatility Engine now produce a real, distinct
  delta instead of an empty one — but their identity for those three weeks leans more on
  stack-environment/matchup than on ceiling, which is what their names actually promise.
- `MIN_TRAILING_WEEKS`/Component A's calibration range are unchanged. Whether to extend the
  backtest down to week 3 (lower sample size, more noise) remains open, tracked separately — not
  attempted here under time pressure.
- Once ceiling signals do populate (week 4+), watch whether `stack_conviction`/`matchup_conviction`
  now double-count against `ceiling_lean` for the same players (e.g., a real stack candidate in a
  high-total game may also show a real boom-rate) — not evaluated here, flagged for whoever revisits
  this after week 4 data exists.
