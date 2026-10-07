# ADR-0045: Questionable players are excluded by default; cleared only by Friday-practice evidence

**Status:** Accepted (implemented, unit-tested; not yet run live -- first live use is week 5)
**Date:** 2026-10-07
**Owner:** Chris
**Related:** ADR-0031/0038 (injury feeds), `optimizer/lineup.py`, `normalization/injury_lookup.py`,
`storage/injury_clearance_store.py`, `scripts/log_q_clearance.py`

## Context

Lineups are generated before final inactives are announced. In week 4 a Doubtful Breece Hall
(Q on DK, D on RotoGrinders) reached lineups before the Doubtful exclusion existed. Chris's
direction after the week 4 post-mortem: do **not** weight Questionable (Q) players by how often
they play ("I don't expect there is a trend"); expect a Q player not to play unless there are
reports he practiced Friday, stay risk-averse, and treat "will test it out pre-game" as an avoid.
(For reference, only 13 Q-tagged players appeared in weeks 3-4, 8 of whom played -- too thin to
model, which agrees with not trying.)

## Decision

1. `EXCLUDED_INJURY_STATUSES` gains `"Q"` -- an uncleared Q player never enters the solver pool.
   This also makes the availability-aware stacks and the injury circumstance detector treat an
   uncleared Q player as out, so his teammates' role change (the beneficiary read) now fires for Q
   players, consistent with expecting them not to play.
2. **Clearance, not probability:** `data/overrides/q_clearances.csv` (hand-kept, written via
   `scripts/log_q_clearance.py`) lists Q players cleared to roster. `practice` must be `Full`, or
   `Limited` **with a note** carrying the positive report (enforced in the dataclass, so a limited
   practice can never clear a player alone). A cleared player is rewritten to status `Q_CLEARED`
   (not excluded). A game-time decision is simply not logged.
3. `overlay_injury_report_exclusions` now also maps RotoGrinders `Q` and only ever moves a status
   **up** a severity ladder (Q < D < OUT/IR). Without this, a DK `Q` would block RotoGrinders' `D`
   from applying, and a clearance could resurrect a Doubtful player (the Hall case).
   `apply_questionable_clearances` only rewrites plain `Q`, never D/OUT/IR, and returns unmatched
   clearances (typos / non-Q players) so the live script can warn.
4. The live dashboard script prints who was excluded as Q and who was cleared each run.

## Consequences

- A Q player with no clearance is gone -- including stars. That is the intended risk stance; the
  cost is real (missing a player who plays), accepted explicitly.
- Source of Friday practice data is manual. nflverse's report lags and RotoGrinders' `PART`
  column is the body part, not participation (ADR-0031). **Follow-ups, not built:** a scraper of
  the official NFL injury report that pre-fills the clearance file; a late-swap contingency plan
  (replacement + beneficiaries per lineup) for players who were cleared but are ruled out Sunday.
- Not run live: `scripts/live_integration_check_dashboard.py`'s week constants still point at
  week 4; first live exercise is the week 5 run.
