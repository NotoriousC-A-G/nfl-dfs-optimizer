# ADR-0039: Remove the same-team DST bonus from the lineup objective

**Status:** Accepted (implemented, unit-tested, backtested)
**Date:** 2026-09-25
**Owner:** Chris
**Related:** PRD Section 7, ADR-0019/0020 (draft-magnitude convention),
`src/nfl_dfs/optimizer/lineup.py` (`_dst_correlation_terms`), `scripts/dst_correlation_backtest.py`

## Context

On 2026-09-20 a generated lineup rostered a JAX QB+WR stack with the Broncos DST (DEN being JAX's
opponent). PRD Section 7's opposing-DST penalty was implemented in response, and, following the
remark "it would make sense to use JAX own D in that stack", a complementary same-team bonus was
added: 0.2 x the pool's projection IQR per (DST, same-team player) pair, summed over every player
in the lineup on that team.

In the week 3 run all six agents rostered the DST of a team they had already stacked, including
Explosion/Shootout, whose thesis (high-scoring games) cuts against pairing a stack with its own
defense. The bonus scaled with stack size (a 4-player stack earned 4 x 0.2 x IQR), close to a
hard preference. Chris confirmed the original remark was specific to that one lineup.

## Decision

Remove the same-team bonus. Keep the opposing-DST penalty unchanged.

## Evidence

`scripts/dst_correlation_backtest.py` on ResultsDB `player_exposures` (real DK `actual_points`,
71 main slates, 2020-2025, 1,593 team-slates):

| Pair | r | 95% CI |
|---|---|---|
| QB vs own DST | -0.013 | +/-0.049 |
| Top-3 WR/TE vs own DST | -0.054 | +/-0.049 |
| Team offense vs own DST | -0.044 | +/-0.049 |
| Control: QB vs DST shuffled within slate | -0.022 | sd 0.025 |

Own-DST output is indistinguishable from zero correlation with the offense, and per-season signs
are unstable (QB r from -0.115 to +0.096). In QB top-quartile games the own DST averaged 5.6
points vs 6.5 otherwise.

## Consequences

- The solver picks the DST on projection and salary, so it can land on a team unrelated to the
  stack. Stack DST pairing will drop sharply from "every lineup".
- Not tested: the opposing-DST penalty itself (needs a schedule join). It remains a draft
  magnitude, unbacktested.
- Scope: DK main-slate contests only.
