# ADR-0043: DST points-allowed excludes opponent defensive scores; DNP players score 0 in postmortem lineups

**Status:** Accepted (implemented, unit-tested, live-verified against week 4)
**Date:** 2026-10-07
**Owner:** Chris
**Related:** ADR-0008/0009 (DST projection), ADR-0041 (reusable postmortem),
`src/nfl_dfs/ingestion/dst_actual_scoring.py`, `src/nfl_dfs/tracking/postmortem/replay.py`

## Context

The week 4 postmortem cross-validated its recomputed lineup totals against DK GameCenter and found
two real defects:

1. **DST points allowed.** L1 and L3 (both holding the Cardinals) recomputed 3.00 below DK's totals.
   ARI allowed 36 on the scoreboard, but 6 of those were a last-minute pick-six NYG's *defense*
   scored. DK's points-allowed bracket excludes points the opponent's defense/special teams score,
   so DK used the 28-34 bracket (-1); `aggregate_team_week_dst_points` used the raw 36 (35+, -4).
   Cardinals: 6.00 recomputed vs 9.00 on DK.
2. **Players who didn't play.** Breece Hall (NYJ) had no week 4 row. Five of six agent lineups held
   him, so all five rendered as "not fully scored" instead of scoring 0 for him as DK would.

## Decision

1. `points_allowed` is now DK-basis: opponent's final score minus what the opponent's
   defense/special teams scored against this team -- each defensive/ST TD (6), the extra point or
   two-point try on the next play by the scoring team, and each safety (2). The subtracted amount is
   exposed as `DstWeekScoring.opponent_defensive_points_excluded`. Missing conversion columns in a
   trimmed pbp frame are treated as "no conversion".
2. In postmortem **lineup scoring only** (`replay.py`), a rostered offensive player scores a real
   0.0, flagged `PlayerOutcome.did_not_play`, when his team's game is settled (a teammate has a
   week row) AND his gsis id has no row that week. A name-match miss (id present) and an unsettled
   game both stay unscored (`None`) -- a 0 is never fabricated for a real miss or an unfinished game.
   `actual_points_by_canonical_id`'s "absence, not zero" contract is deliberately unchanged, so
   signal verdicts, top performers and chalk comparison are unaffected.

## Consequences

- Historical DST actuals were understated whenever the opposing defense/ST scored (10 of 128
  team-weeks so far in 2026; 9 changed bracket). Checked for impact: no DST-projection calibration
  consumes these actuals (ADR-0008/0009's backtests are still pending), and no stored
  `agent_results.csv` lineup from weeks 1-3 holds an affected DST -- week 4's Cardinals was the only
  case, now corrected. Any future DST backtest should use the corrected scoring.
- Week 4 totals now reconcile with DK to the cent for all three played lineups.
- `tracking/agent_results_collector.py` (the CSV backfill) gets the same DNP rule, adapted to its
  id-less name tokens: a missing player scores 0 only if his team's game is settled this week AND
  that exact (name, team) has a real row in another week of the season; anyone else stays unresolved.
- Unverified convention: whether DK also excludes the extra point after a defensive TD (the week 4
  case had none). The implementation excludes it, the standard reading of "points scored by the
  defense/ST".
