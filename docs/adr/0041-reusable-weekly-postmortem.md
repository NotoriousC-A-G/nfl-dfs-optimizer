# ADR-0041: A real, reusable weekly postmortem (operator results logging + full retrospective analysis)

**Status:** Accepted (implemented, unit-tested, live-verified against week 3)
**Date:** 2026-09-28
**Owner:** Chris
**Related:** week 3 handoff (`/tmp/nfl-dfs-week3-handoff.md`), `scripts/reconstruct_week2_postmortem.py`,
`src/nfl_dfs/tracking/postmortem/`, `src/nfl_dfs/storage/{agent_results,contest_results}_store.py`

## Context

The week 3 handoff flagged two real gaps:

1. **No mechanism to log Chris's own manually-built lineups' outcomes.** `agent_results.csv` and
   `contest_results.csv` support an `agent_id="operator"` row (used for week 2), but nothing wrote
   one for week 3's 3 manually-built entries or the 23 real DK contest entries they were played
   into -- there was no reusable entry point, only ad hoc logging done once, by hand, for week 2.
2. **`scripts/reconstruct_week2_postmortem.py`'s own docstring explicitly disclaims itself:**
   "NOT a reusable pipeline piece -- the regexes here are tuned to this one HTML file's exact text
   layout." It existed only because week 2 predated the slate-snapshot persistence layer. The
   "real," reusable path (`tracking/postmortem/replay.py` against a real snapshot) has existed
   since week 3's own live run produced one -- but `postmortem_renderer.py` never grew the sections
   (contest results, season record, player exposure, positional bias, stack-thesis-hit review)
   that made week 2's one-off version actually useful; it only ever rendered lineup
   outcomes/process-grade/chalk-comparison/ceiling-patterns.

Both gaps were closed in the same session, on the real week 3 slate.

## Decision

1. **`scripts/log_operator_contest_results.py`** (new, reusable): forward-logs L1/L2/L3 (or
   however many are live a given week) into `agent_results.csv` under `agent_id="operator"` --
   `proj_total`/`salary` looked up live from the real slate snapshot, not hardcoded -- plus every
   real DK contest entry (transcribed by hand from GameCenter, DK exposes no results API) into
   `contest_results.csv`. `OPERATOR_LINEUPS`/`CONTEST_RESULTS` are the only things edited by hand
   each week, same convention as this project's other live scripts' `SEASON`/`WEEK` constants.
2. **`tracking/postmortem/exposure.py`** (new): real, tested ports of week 2's one-off
   `compute_player_exposure`/`compute_positional_delta`/`compute_stack_thesis_review` functions,
   operating on `LineupOutcome`/`PlayerOutcome` objects from `replay.py` instead of parsed HTML.
   The stack-thesis review reads each agent's real, disclosed `core_stack` (canonical_ids) straight
   from the slate snapshot -- not a name regex-parsed out of rationale text, which is strictly more
   reliable and was already sitting unused in the snapshot.
3. **`postmortem_renderer.py`** grows the same 3 sections, plus a contest-results table and a
   season-record table. The latter two are passed to `render_postmortem_html` as optional
   render-time arguments (`contest_results`, `season_records`), not `PostMortemReport` fields --
   unlike everything else the report carries, they aren't derivable from a slate snapshot; they're
   Chris's own real DK history and season-to-date record, read separately by the calling script.
   Passing neither still renders a complete, valid page.
4. **`scripts/run_postmortem.py`** now reads `contest_results_store`/`season_record` itself and
   passes them through. This script is the one real weekly path from here on -- only `SEASON`/
   `WEEK` change; `reconstruct_week2_postmortem.py` is retired as dead-end tooling for a slate that
   predates the snapshot layer, not deleted (it's the only record of how week 2 was handled).

## Verification

Live, against the real week 3 slate snapshot and real nflverse settled data:

- `log_operator_contest_results.py` wrote 3 operator rows (L1/L2/L3) and 23 contest rows. Printed
  totals (23 entries, 11 cashed, $120.00) matched the hand tally from 22 DK GameCenter screenshots
  exactly.
- `run_postmortem.py` independently recomputed L1=156.2, L2=94.06, L3=148.48 via the snapshot +
  nflverse join -- a completely different code path from the DK screenshots those numbers were
  originally transcribed from. Real cross-validation, not a restated number.
- Full suite: 1025 passed (14 new tests: `test_postmortem_exposure.py` plus additions to
  `test_postmortem_replay.py`/`test_postmortem_renderer.py`).

## Consequences

- **Real finding this surfaced:** L1 (156.2) and L3 (148.48) both outscored all 6
  `NflAgentConstructor` agents this week (best agent: Explosion/Shootout, 136.6); every agent
  overshot its own projection on the downside, while L1/L3 both beat theirs. Only 1 of the 6
  agents' named core stacks (Explosion/Shootout's Olave+Shough) actually hit.
- `log_operator_contest_results.py`'s roster/contest-result transcription is still manual, by
  design -- DK has no results API. What's now real code instead of a one-off: the projection/
  salary lookup, the idempotent CSV writes, and the entire retrospective analysis layer.
- Scope: DK main-slate contests only, one main slate per week (unchanged from the rest of this
  package's own scoping).
