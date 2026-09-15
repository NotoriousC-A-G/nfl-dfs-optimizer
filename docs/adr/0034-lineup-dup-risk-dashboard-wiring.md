# ADR-0034: Wire dup-risk calibration into the dashboard's Lineups tab

**Status:** Accepted (implemented, unit-tested, live-verified in the browser against a real slate pull)
**Date:** 2026-09-15
**Owner:** Chris, direct continuation of ADR-0033's "not yet wired into anything live" note
**Related:** ADR-0032 (`lineups/` backfill), ADR-0033 (the calibration this reads), ADR-0026 (the live
projected-ownership source this joins against), `src/nfl_dfs/composition/lineup_dup_risk.py`,
`src/nfl_dfs/dashboard/renderer.py`

## Context

ADR-0033 built a real, live-validated dup-risk calibration (ownership-decile dup-rate curve, per-trend
dup-rate splits) but explicitly stopped at "calibration only, not wired into anything live." Chris chose
to close that gap next: surface a real dup-risk read for each of the 3 lineups this project's optimizer
actually generates.

## Decision

### 1. A new absolute-value lookup, not a reuse of ADR-0033's per-contest-relative-rank curve

ADR-0033's `OwnershipDupCurve` decile-ranks each lineup relative only to its own historical contest --
meaningless for a brand-new candidate lineup that was never part of any settled contest (there is nothing
to rank it against). `analysis/dup_risk_calibration.py` gained `DupRiskLookupTable`/
`build_dup_risk_lookup_table`/`classify_avg_ownership`: global quantile cutpoints over every pooled
historical lineup's `avg_own`, so an arbitrary ownership value can be classified against real historical
breakpoints in isolation. **A real, disclosed convention difference, not an inconsistency overlooked**:
this table's buckets are ascending (bucket 0 = lowest `avg_own`, `pandas.qcut`'s own natural bin order),
the opposite of `OwnershipDupCurve`'s descending decile-0-is-highest convention -- stated plainly in both
structures' own docstrings rather than silently reconciled.

### 2. `composition/lineup_dup_risk.py` -- joins a generated `Lineup`'s real players to live ownership

`assess_lineup_dup_risk(lineup, identities, leverage_by_native_id, table)` averages the LIVE PROJECTED
ownership (`ownership/leverage.py`, ADR-0026) of a generated lineup's own 9 players (joined via
`identity.sources["rotogrinders"].native_id`, the same join `composition/player_detail.py`'s
`_ownership_leverage` already uses -- no new join scheme), then classifies that average against the
historical lookup table. Gated at `MIN_PLAYERS_COVERED=5`: below that many of the 9 players resolving to
a real projected-ownership number, the assessment carries a `reason` and no fabricated average -- never a
number computed over 1-2 players standing in for the whole lineup.

**A real, disclosed assumption stated explicitly in the module docstring, not a new leap of faith:** the
historical lookup table's `avg_own` axis is real POST-CONTEST ACTUAL ownership; this module classifies
using this week's LIVE PROJECTED ownership (no other source exists pre-game). This is the exact same
projected-vs-actual comparison `ownership/leverage.py` already makes for its own chalk/leverage flags
(ADR-0026) -- not a new methodological risk this ADR introduces.

**Deliberately does not touch lineup generation or scoring.** `optimizer/lineup.py` is completely
untouched -- this is a read AFTER generation, not a change to what gets generated. Folding a dup-risk
penalty into the actual optimization objective (trading projected points for lower duplication) is a real,
separate design decision this ADR does not make.

### 3. Dashboard wiring

`dashboard/renderer.py`'s `render_dashboard_html`/`write_dashboard_html` gained a new optional
`dup_risk_by_lineup: dict[int, LineupDupRiskAssessment] | None` parameter (keyed by the lineup's 0-based
index into `weekly_output.lineups`, the same index the existing rationale-pairing loop already uses) --
every existing caller keeps working unchanged with no dup-risk section, same "absent input, absent
section" pattern used throughout this dashboard. Renders as a `.dup-risk` block in each lineup card,
between the roster table and the rationale text.

## Consequences

- **Live-verified, not just unit-tested:** ran the full `live_integration_check_dashboard.py` pipeline
  against a real slate pull -- all 3 generated lineups got a real dup-risk read (e.g. "1.0% historical
  field-duplication rate (13.8% avg proj. ownership across 7/9 players)"), rendered correctly and
  confirmed visually in the browser.
- Chris now sees, for each of the 3 generated lineups, a real historical read on how often a lineup with
  this ownership profile actually gets duplicated by the field -- the concrete question this whole
  investigation (ADR-0023 through ADR-0034) set out to answer, now visible where lineup decisions
  actually get made.
- **Still descriptive only.** The optimizer's own lineup selection is unaffected -- a genuinely chalky,
  high-dup-risk lineup can still be generated and shown as such, exactly as it was before this ADR. Using
  this signal to actually change which lineups get generated (e.g. a soft penalty trading some projected
  points for lower dup risk) remains real, separate, not-yet-scoped follow-on work.
- Partial player coverage (5-7 of 9 players resolving to a live projected-ownership number was the real
  live-run experience, not the full 9) is disclosed directly in the rendered text, not hidden behind a
  rounded average that looks more complete than it is.
