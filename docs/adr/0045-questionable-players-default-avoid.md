# ADR-0045: Questionable players are excluded by default; cleared only by Friday-practice evidence

**Status:** Accepted (implemented, unit-tested; revised 2026-10-07 to automatic official-report clearance with Chris overrides; first live use is week 5)
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
2. **The system decides; Chris overrides (revised 2026-10-07).** Each Q player is decided from the *official NFL practice report* for the target week (nflverse `import_injuries`, every position, live in-week): **Full participation clears him** (`Q_CLEARED`, not excluded); Limited, Did Not Participate, or no official row leaves him out (a limited practice alone is not enough -- clearing him needs a positive report, which is what an override is for). Every decision prints with its basis and evidence timestamp and is saved with the run. Chris overrides in either direction via `data/overrides/q_overrides.csv` (`scripts/log_q_override.py`): `clear` (a Q or Doubtful player; never OUT/IR) or `bar` (anyone; status `BARRED`, excluded). Chris does **not** pre-fill anything before generation -- the original hand-kept "clearance list" design was dropped.
3. `overlay_injury_report_exclusions` now also maps RotoGrinders `Q` and only ever moves a status
   **up** a severity ladder (Q < D < OUT/IR). Without this, a DK `Q` would block RotoGrinders' `D`
   from applying, and a clearance could resurrect a Doubtful player (the Hall case).
   `apply_questionable_clearances` only rewrites plain `Q`, never D/OUT/IR, and returns unmatched
   clearances (typos / non-Q players) so the live script can warn.
4. The live dashboard script prints who was excluded as Q and who was cleared each run.

## Consequences

- A Q player with no clearance is gone -- including stars. That is the intended risk stance; the
  cost is real (missing a player who plays), accepted explicitly.
- Practice evidence now comes from nflverse's official report, which (checked 2026-10-07) already holds the current week for all positions -- ADR-0031's "lags" premise is out of date for in-week use. It keeps only the latest day per player, so `scripts/official_injury_capture.py` archives each pull (`storage/official_injury_snapshot_store.py`) to preserve the Wed/Thu/Fri trajectory and the exact evidence behind each run. Risks: nflverse's in-week refresh cadence is unconfirmed (an nfl.com page parser is the fallback), and only teams that have posted appear.
- **Sunday morning check (not yet built, requested by Chris):** inactives land ~90 minutes before kickoff, so a Sunday script re-pulls the report and flags any rostered player whose status changed, with a swap menu. **Follow-up:** the late-swap contingency plan (replacement + beneficiaries per lineup).
- Not run live: `scripts/live_integration_check_dashboard.py`'s week constants still point at
  week 4; first live exercise is the week 5 run.
