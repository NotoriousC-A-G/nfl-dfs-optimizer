# ADR-0044: Real per-player detail in the weekly postmortem (Tiers 0-3)

**Status:** Accepted (implemented, unit-tested, live-verified against week 3)
**Date:** 2026-09-29
**Owner:** Chris
**Related:** ADR-0041 (the reusable weekly postmortem), ADR-0042 (player-props ingestion),
`architect`/`ui-ux` review of the sister MLB project's postmortem (2026-09-29, this session)

## Context

Chris asked for more real per-player detail in the postmortem after seeing how thin it was: the
page showed only totals per lineup, plus one table of players rostered in 3+ lineups. Two agents
independently reviewed the sister MLB project's postmortem and this project's own real, already-
computed data to propose a tiered plan (see the session transcript for the full write-ups):

- `architect` found that `PostMortemReport.top_performers`/`.missed_players` were already computed
  by `replay.py` and never rendered -- a free win, not new work.
- `architect` found 8 real per-player signal fields (`PlayerDetailRecord.ownership`,
  `game_environment`, `stack_context`, `ceiling_multiplier`, `red_zone_role_security_discount`,
  `injury`, `implied_total`, `slate_window`, `circumstance_assessment`) already serialized into
  every slate snapshot's `player_pool` and already trusted by `retrospective.compute_signal_
  verdicts` for the slate-wide grade -- just never surfaced per player.
- `architect` found real box-score detail (yards/TDs/completions/attempts/carries/targets) is
  pulled from nflverse and reduced to one DK-point scalar in `offense_actual_scoring.dk_points_row`,
  discarding everything else.
- `ui-ux` proposed a two-level disclosure (existing totals table unchanged; a per-lineup roster
  table behind a native `<details>` toggle, collapsed by default; richer detail as tooltips/badges
  on that same row rather than new columns) so the page stays visually unchanged until a reader
  opts in, with zero new JavaScript.
- Both agents independently recommended shipping real box-score detail before player-props
  comparison (ADR-0042), since props matching is disclosed as validated on exactly one event and a
  half-populated badge column on a real Sunday slate would read worse than not having it yet.

## Decision

Implement Tiers 0-3 of the proposal now; defer Tier 4 (props comparison) and Tier 5 (multi-week
trend/sparkline) as separate follow-ups.

- **Tier 0:** render the already-computed `top_performers`/`missed_players` tables.
- **Tier 1:** add a per-lineup roster table (`<details>`, collapsed by default, zero JS) using
  `LineupOutcome.players`, which was already fully computed and simply never rendered.
- **Tier 2:** a new `PlayerContext` dataclass carries the 8 real signal fields per player, built
  once per postmortem run (`player_context.build_player_context_by_id`) from the same slate
  snapshot `retrospective.py` already reads, keyed by `canonical_id` (an O(1) lookup, same pattern
  `actual_points.py` already established) -- rendered as a compact tag strip (CHALK/LEVERAGE/
  STACK/injury status/ceiling multiplier/game-environment score/implied total/red-zone discount/
  circumstance note), each tag conditional on the field actually being populated.
- **Tier 3:** a new `settled_offensive_box_scores_by_player`/`format_box_score_line` pair in
  `offense_actual_scoring.py` -- a SEPARATE function from `settled_offensive_points_by_player`
  (not a return-shape change to it), so that function's several existing callers are untouched.
  Rendered as a `title=` tooltip on the player's name, not a new column.

`PlayerOutcome` gained one new optional field, `context: PlayerContext | None = None`, added at
the end with a default so every existing positional construction (tests, other callers) is
unaffected.

## Verification

- Full suite: 1080 passed (18 new tests: box-score formatting/join, `PlayerContext` construction,
  renderer sections).
- Ran live against the real week 3 slate snapshot and real nflverse settled data. Confirmed real
  tag output (e.g. `CHALK`, `LEVERAGE`, `STACK`, `Q` injury designation, real Game-Environment
  scores) and real box-score tooltips matching the DK GameCenter screenshots exactly (e.g. Tyler
  Shough: "29/42, 255 pass yds, 4 TD, 1 INT · 1 car, 36 rush yds, 0 TD"; Brock Purdy: "15/27, 297
  pass yds, 4 TD, 0 INT · 2 car, 34 rush yds, 0 TD").
- No `CEIL`/`RZ` tags appeared this week -- expected, not a bug: `ceiling_multiplier` is dormant
  for weeks 1-3 (ADR-0028/0040's `MIN_TRAILING_WEEKS=3` gate), same known gap already documented
  elsewhere.
- Parsed the generated HTML with `html.parser` to confirm no malformed markup from the new nested
  tags/tooltips.

## Consequences

- Tiers 0-3 are pure display of data this project's pipeline already computes and trusts -- no new
  formula, no `model-analytics-expert`/`fantasy-football-expert` sign-off needed (per `architect`'s
  own assessment).
- **Deferred, not forgotten:** Tier 4 (player prop line + implied probability next to the real
  result) needs a real join (`dk_native_id` -> `canonical_id`) that doesn't exist yet, plus a
  "which captured snapshot" policy decision (props move through the week) -- real, scoped work,
  and `architect` recommends an informal `fantasy-football-expert` check on "how stale a line is
  worth showing" before it ships. Tier 5 (a true multi-week DK-points trend/sparkline, matching
  MLB's boom%/P50 visual) needs a trailing-points-history structure this project doesn't have yet;
  a lighter opportunity-trend version (targets/aDOT/rush-share) could ride along cheaply using
  `receiving_profile.py`/`qb_rushing_profile.py`, which are already computed but not yet joined
  into the postmortem path.
