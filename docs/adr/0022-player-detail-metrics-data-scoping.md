# ADR-0022: Player-detail metrics view — data scoping (snap shares, red zone, matchups, man/zone splits)

**Status:** Accepted (data-scoping spec — no formulas, no UI; live-verified against real API responses)
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 4 (Data Sources), Section 6 `MatchupContext` (coverage/run/pass-protection rows),
`StackProfile`'s `RoleShare` (ADR-0019, ADR-0020), ADR-0001/ADR-0006 (alignment-coverage approximation and
confidence gates), ADR-0013 (player ID reconciliation), ADR-0014 (trailing grade population),
`docs/phase0/data-availability.md`, `src/nfl_dfs/ingestion/pff.py`, `src/nfl_dfs/ingestion/usage_share.py`

## Context

Chris asked for a detailed, browsable player-metrics view for the eventual dashboard — the NFL analogue of the
MLB build's Hitters/Pitchers tabs — distinct from the optimizer's own pick-transparency UI (separate, already
in progress). His verbatim ask: *"snap shares, red zone targets, matchups (receivers, TEs, o vs D line etc), man
vs zone tendencies and player performance against each."*

This ADR is data scoping only, per the task brief: map each requested metric to a real, live-verified data
source and this pipeline's current ingestion state, size the gap honestly, and hand the Data Integration
Engineer concrete next-round direction. No formula changes, no UI, no Phase 4 concepts. Every claim below was
checked against this session's own live API calls or this repository's actual current code — not carried over
from memory of what a prior ADR said should be true.

## Live verification performed this session

- **PFF OpenAPI spec** (`https://developer.pff.com/openapi.json`, 70 endpoints) pulled fresh and inspected
  directly, not assumed from Phase 0's summary.
- **`GET /v1/facet/receiving/scheme`** — live-called with a real `PFF_API_KEY` (`season=2025&week=1,2,3`).
  `200`, no `restricted` key (fully entitled), **318 real rows**, positions `{FB, HB, QB, TE, WR}` (83 TE rows
  confirmed, e.g. Jake Ferguson: 10 man targets / 16 zone targets through week 3). Row shape carries
  `player_game_count` (server-side aggregation across the requested week list, identical to the pattern
  ADR-0014 already confirmed for the four `GRADE_FACETS` `pff.py` currently pulls) and a full `man_*`/`zone_*`
  parallel field set per receiver: `man_targets`/`zone_targets`, `man_targets_percent`/`zone_targets_percent`,
  `man_yprr`/`zone_yprr`, `man_grades_pass_route`/`zone_grades_pass_route`, `man_epa`/`zone_epa`,
  `man_caught_percent`/`zone_caught_percent`, `man_avg_depth_of_target`/`zone_avg_depth_of_target`, and more
  (69 fields total). **This is exactly the "does Player X actually perform better against man or zone
  specifically" signal the task brief flagged as the believed real gap — confirmed to exist, confirmed
  entitled, confirmed not currently pulled anywhere in `pff.py`.**
- **`GET /v1/facet/defense/pass_rush`** — live-called, `200`, entitled, per-defender `grades_pass_rush_defense`,
  `pass_rush_win_rate`, `pass_rush_percent`, server-side aggregated (`player_game_count` present). **Not
  currently in `pff.py`'s `GRADE_FACETS`** (which only pulls `offense/run_blocking`, `offense/pass_blocking`,
  `defense/run`, `defense/coverage_scheme`) — a real, previously-unflagged gap: `MatchupContext`'s own
  pass-protection-vs-pass-rush row (PRD Section 6) names "PFF team pass-rush win rate" as an input, but the
  facet that would supply the defense's side of that row has never actually been added to the ingestion code.
- **`GET /v1/facet/defense/coverage_scheme`** (already in `GRADE_FACETS`, already ingested) — live-called and
  the *full* field list inspected, not just the two grade fields the PRD table names. Confirmed it already
  returns `man_snap_counts_coverage`, `zone_snap_counts_coverage`, `man_snap_counts_coverage_percent`,
  `man_coverage_percent`, `zone_coverage_percent` per defender — real man/zone **snap-volume** fields, not just
  grades. `pff.py`'s `parse_pff_grade_facet` (line 239-243) already captures every non-metadata numeric field
  generically into `PffGradeRow.grades`, so **these fields are already flowing through the existing pull today,
  just never aggregated to team level or surfaced.** Team-level "how often does this defense play man vs.
  zone" is a pure downstream aggregation of already-ingested data — zero new PFF calls needed.
- **`nfl_data_py.import_snap_counts([season])`** (project venv, `.venv`) — live-called for 2025. Returns real
  per-player-per-game rows: `game_id`, `player`, `pfr_player_id`, `position`, `team`, `opponent`,
  `offense_snaps`/`offense_pct`, `defense_snaps`/`defense_pct`, `st_snaps`/`st_pct`. Confirmed this is the same
  function ADR-0020 used once, ad hoc, for the Hampton research — never built as a standing ingestion module.
  **Real finding, not previously documented:** this function keys players by `pfr_player_id` (e.g.
  `"BankKe01"`), not `gsis_id` — the same ID family ADR-0013 flagged as unreliable when read off a *vendor's own*
  scraped `data-playerid` attribute (Footballguys' Gibbs/Gibbens collision). The nflverse/ffverse crosswalk
  (`nfl_data_py.import_ids()`) does carry its own `pfr_id` column alongside `gsis_id` (confirmed live: e.g.
  `pfr_id="MendFe00"` ↔ `gsis_id="00-0041562"`), so joining snap counts to this project's canonical ID is
  plausible via the crosswalk's own `pfr_id` field (a different, presumably-reliable path than the one ADR-0013
  rejected) — but this has **not been spot-checked for match quality** the way ADR-0013 spot-checked PFF's
  `pff_id` before trusting it. Flagged for the Data Integration Engineer as a real verification step, not an
  assumed-safe join.
- **`nfl_data_py.import_pbp_data([season])`** — confirmed `yardline_100` and `goal_to_go` are populated,
  standard fields (`yardline_100` count 45,223 non-null of the season sample checked, range 1-99). Red zone is
  the standard `yardline_100 <= 20` filter on the exact same play-by-play frame `usage_share.py` and
  `nflverse.py` already pull — no new data source, no new API call, pure aggregation.
- Also confirmed present in the OpenAPI spec but not live-tested in depth (lower priority, noted for
  completeness): `/v1/facet/receiving/coverage` (receiver-vs-covering-defender matchup leaderboard — "serves
  the same report as `facet-defense-coverage-matchup`," per its own description) and `/v1/facet/receiving/concept`
  (splits by play concept — screen, slot, etc.). Neither was named directly in Chris's ask; both are optional
  future enrichments, not part of this round's core scope.

## Metric-to-source map

| Chris's ask | Data source (confirmed live) | Ingestion status today | Gap to close |
|---|---|---|---|
| **Snap shares** | `nfl_data_py.import_snap_counts()` — per-player-per-game `offense_pct`/`defense_pct`/`st_pct` | **Not built.** Used once, ad hoc, in ADR-0020's Hampton research; no standing module. | New ingestion module (mirrors `usage_share.py`'s shape): pull, join `pfr_player_id` → canonical `gsis_id` via the nflverse crosswalk's `pfr_id` field (needs a spot-check of match quality, not yet done), aggregate to player-week, expose latest-week % plus a trailing rolling average. No shrinkage/prior-blending needed — this is descriptive, not a projection input. |
| **Red zone targets/carries** | `nfl_data_py.import_pbp_data()`, filtered `yardline_100 <= 20` | **Not built.** No red-zone-specific aggregation exists anywhere in the pipeline. | Extend `usage_share.py`'s exact aggregation pattern (`aggregate_player_week`, `aggregate_team_week_volume`) with a red-zone-filtered variant — same `rusher_player_id`/`receiver_player_id` grouping, same team-normalization, just pre-filtered rows. Small, additive; no new data dependency. |
| **Man vs. zone defensive tendencies** (how often a defense plays each) | PFF `defense/coverage_scheme` — **already ingested** (`pff.py` `GRADE_FACETS`); `man_snap_counts_coverage`/`zone_snap_counts_coverage`/`*_coverage_percent` fields confirmed live and already flowing through the generic `grades` dict. | **Data already flowing; team-level rollup not built.** Nothing today sums per-defender coverage snaps to a team-level man-rate/zone-rate. | Pure aggregation over already-ingested `PffGradeRow.grades` — no new PFF call. Cheapest item in this whole scope. |
| **Player performance against man vs. zone specifically** (the believed real gap) | PFF `receiving/scheme` — **confirmed live this session**, entitled, real data, server-side aggregated across a week list exactly like `GRADE_FACETS`' existing four facets. | **Not ingested at all.** Not in `pff.py`'s `FACETS` or `GRADE_FACETS`; not referenced anywhere in the current codebase. | Add `"receiving/scheme": "receiving_scheme"` to a `GRADE_FACETS`-shaped dict and reuse `parse_pff_grade_facet`/`fetch_matchup_grades`/`resolve_grade` **unchanged** — this facet's response shape (metadata fields + a flat numeric `grades` dict) matches the existing pattern exactly. Smallest-effort, highest-value item in this scope. |
| **O-line vs. D-line matchup grades** — run side | PFF `offense/run_blocking` vs. `defense/run` — **both already ingested** (`GRADE_FACETS`). | **Built.** This is already `MatchupContext`'s "Run game" row (PRD Section 6), data-confirmed and ingested; only the differential-to-multiplier *formula* and the identification-and-display layer for a browsable view don't exist yet. | No new ingestion. Presentation-layer work: expose the two already-resolved grades side by side for a given player's likely opponent. |
| **O-line vs. D-line matchup grades** — pass side | PFF `offense/pass_blocking` (ingested) vs. `defense/pass_rush` (**confirmed live this session, NOT currently in `GRADE_FACETS`**) | **Half-built.** The offensive-lineman side has been ingested since ADR-0014; the defensive pass-rush side that `MatchupContext`'s own PRD text names ("PFF team pass-rush win rate") has never actually been added to `pff.py`. | Add `"defense/pass_rush": "pass_rush_summary"` to `GRADE_FACETS`, same one-line pattern as above. |
| **WR/TE matchup grades** (this week's specific opposing coverage) | PFF `defense/coverage_scheme` (man/zone grade split, ingested) + `signature/defense/slot_coverage` (alignment volume, **referenced in the PRD's `MatchupContext` table but not present in `pff.py`'s fetch code at all** — checked directly, it is absent from both `FACETS` and `GRADE_FACETS`) | **Data-confirmed per Phase 0/ADR-0001, but the alignment-volume facet itself was never actually added to the ingestion module**, and the defender-identification/confidence-gate logic (ADR-0001/ADR-0006 — margin threshold, snap floor, shadow-coverage guardrail) that turns raw coverage grades into a specific "this receiver vs. that identified defender" number **has not been implemented as code anywhere in this pipeline** (confirmed via `stack_profile.py`'s own explicit notes: *"MatchupContext favorability is not yet implemented anywhere in this pipeline"*). | Two independent gaps, not one: (1) add `signature/defense/slot_coverage` to ingestion — small; (2) implement the actual defender-identification/gating logic ADR-0001/ADR-0006 already specify — this is real `MatchupContext` implementation work, already scoped elsewhere in the project, not something this ADR should duplicate or shortcut. **A player-detail "matchups" section that shows an opponent-specific identified-defender grade is blocked on that implementation existing**, not on any new data. |

## Player-detail record — schema design

**Grain: per-player, per-week, trailing-through-last-completed-week — reusing the exact discipline `RoleShare`
(ADR-0019) and `MatchupContext`'s grade population (ADR-0014) already use, not inventing a second one.**
Concretely, `season`/`week` here means the same thing it means in `PlayerRoleShare`/`RoleShareResult`
(`ingestion/usage_share.py`) and `PffFacetGrades` (`ingestion/pff.py`): "as of week `W`," built from completed
weeks `1..W-1` only. A browsable detail view is naturally a "current week" read of this same trailing-cumulative
shape — there is no reason for it to carry its own season-to-date/trailing-window policy distinct from what the
rest of the pipeline already committed to.

**Composition, not a parallel model.** The record below is a join over outputs that either already exist or
are directly-analogous extensions of existing modules — it does not restate their computation:

```
PlayerDetailRecord (season, week, canonical_player_id):
  identity: PlayerIdentity                     # from normalization/identity.py — the existing canonical-ID
                                                # join key (gsis_id where resolved), not a new ID scheme
  team, position, opponent_team_this_week

  usage:
    role_share: PlayerRoleShare | None          # ingestion/usage_share.py, UNCHANGED — reused directly for
                                                 # RB carry-share / WR target-share + role_tier
    snap_share:                                 # NEW module, same shape discipline as usage_share.py
      offense_pct_trailing, defense_pct_trailing, st_pct_trailing
      offense_pct_last_week, defense_pct_last_week, st_pct_last_week
    red_zone:                                   # NEW: usage_share.py's aggregation pattern, red-zone-filtered
      rz_carries_trailing, rz_targets_trailing, rz_share_trailing   # share of team's own RZ volume

  matchup_this_week:                            # only ever populated once MatchupContext itself is
                                                 # implemented (see Scope section) -- NOT a new formula here
    own_unit_grade: ResolvedGrade | None         # e.g. this player's OL's grades_pass_block / grades_run_block
    opponent_unit_grade: ResolvedGrade | None    # e.g. opponent DL's grades_run_defense / pass_rush_win_rate
    coverage_tendency_faced:                     # team-level rollup, NEW pure aggregation over already-
      opponent_man_rate, opponent_zone_rate      # ingested defense/coverage_scheme snap-count fields

  own_scheme_splits:                             # NEW facet pull (receiving/scheme), WR/TE/receiving-back only
    man_grades_pass_route, zone_grades_pass_route
    man_yprr, zone_yprr
    man_targets_percent, zone_targets_percent
    # full field set per the live-confirmed facet response, not narrowed here
```

Three composition decisions, stated explicitly rather than left implicit:

1. **No new canonical player ID.** Every new source (snap counts, red zone, `receiving/scheme`) joins against
   the existing `PlayerIdentity`/`gsis_id` (ADR-0013), not a fifth parallel ID space. `receiving/scheme` and
   `defense/pass_rush` use PFF's own `player_id`, already the crosswalk's directly-verified path (ADR-0013);
   `import_snap_counts()`'s `pfr_player_id` is the one join in this scope that needs its own verification pass
   (see the live-verification section above) before being trusted the way `pff_id` already is.
2. **`matchup_this_week` is the one section this ADR cannot fully spec data-wise**, because it depends on
   `MatchupContext`'s defender-identification logic being real code, not just an accepted formula. Everything
   else in the record is independently buildable today.
3. **This is a read-model over existing per-player-week facts, not a new source of truth.** `role_share`,
   `snap_share`, `red_zone`, and `own_scheme_splits` are each computed once by their owning module and simply
   joined here by `(season, week, canonical_player_id)` — exactly the same relationship `stack_profile.py`
   already has to `usage_share.py`'s output. If a future consumer needs `snap_share` for a formula (not just
   display), it reads the same module `PlayerDetailRecord` reads from — there is no second copy of the number.

## Scope size — honest assessment, not undersold

This splits cleanly into three rounds of very different size and risk:

**Round A — small, no dependencies, can start immediately:**
- Add `receiving/scheme` and `defense/pass_rush` to `pff.py`'s grade-facet pattern (each is a ~2-line dict
  addition plus test coverage; both confirmed live this session to fit the existing `parse_pff_grade_facet`
  shape unchanged).
- Team-level man/zone tendency rollup — pure aggregation over data already flowing through `pff.py`, no new
  API call.
- Red-zone carry/target aggregation — a red-zone-filtered sibling to `usage_share.py`'s existing
  `aggregate_player_week`, same module family.
- New standing snap-share ingestion module — the one item in this round with a real, if contained, risk: the
  `pfr_player_id` → `gsis_id` join needs the same live spot-check rigor ADR-0013 applied to `pff_id` before
  it's trusted for a real player-facing view (a wrong join here shows Chris the wrong player's snap counts,
  which is worse than showing nothing).

This round alone plausibly closes four of Chris's five named items (snap shares, red zone, man/zone tendencies,
player-level man/zone performance) with no dependency on any other in-flight work.

**Round B — blocked on other already-scoped work, not new work this ADR can shortcut:**
- The "matchups (receivers, TEs, o vs D line)" item, at the fidelity Chris's phrasing implies (a specific
  opponent, a specific identified defender or unit, a real projected-matchup number) is **the same
  `MatchupContext` implementation this project has already spec'd and repeatedly flagged as "not yet
  implemented anywhere in this pipeline"** (`stack_profile.py`'s own words). This ADR does not re-scope or
  duplicate that work. Until `MatchupContext` is real code, the honest interim version of a "matchups" section
  is team-level and descriptive (opponent's aggregate coverage grades and man/zone tendency, opponent's OL/DL
  grades) rather than player-vs.-identified-defender — genuinely useful, but a visibly lower-fidelity version of
  what Chris asked for, and should be labeled as such rather than implied to be the full thing.

**Round C — new plumbing, genuinely underestimated if treated as "just wire it up":**
- Composing Round A's outputs (plus Round B once available) into one `PlayerDetailRecord` per the schema above
  touches four-plus modules that don't currently talk to each other (`usage_share.py`, `pff.py` ×2 new facets,
  a new `snap_share.py`, a new `red_zone.py`) and needs its own join/assembly layer. This is real, non-trivial
  integration work — closer to a full sprint than a "small addition," even though none of its individual pieces
  is large on its own. Framing this as "2-3 PFF pulls, done" would undersell it.

**Bottom line for Chris/Product Owner:** this is not one small addition. It is a small-to-medium Round A that
can ship now and closes 4 of 5 named items, plus a larger, honestly-uncertain Round C assembly layer, plus a
Round B item that is genuinely gated on a separate, already-larger piece of work (`MatchupContext`
implementation) rather than on anything new this ADR introduces.

## Flags for the Product Owner (not decided here)

1. **Sequencing call: does Round A's player-detail work proceed now, ahead of `MatchupContext` implementation,
   or wait?** Round A is fully independent and could start immediately. But if the goal is a coherent "detail
   metrics" view rather than four disconnected data points, shipping Round A alone produces a dashboard that's
   visibly missing its most-requested section (a real "matchups" grade) until `MatchupContext` lands — worth
   Chris/Product Owner deciding whether a partial view now is worth it, matching this project's walking-skeleton
   discipline of sequencing real, complete pieces rather than partial ones. This mirrors the same open question
   Section 11 items 5 and 10 already raise for other Section 6 work — not a new pattern.
2. **Is the team-level-descriptive interim version of "matchups" (Round B's honest fallback until
   `MatchupContext` ships) worth building at all, or should that section simply say "coming once `MatchupContext`
   is implemented" and not be half-built twice?** A real design/UX call, not a data-availability one.
3. **`receiving/coverage` and `receiving/concept`** (confirmed live to exist, not requested by Chris directly)
   — worth a explicit in/out call before Round A scope is locked, so the Data Integration Engineer isn't left
   guessing whether "while you're in there" scope creep is wanted.

## Consequences

- **Data Integration Engineer, Round A, ready to start:** (1) add `receiving/scheme` and `defense/pass_rush` to
  `pff.py`'s `GRADE_FACETS` dict, following ADR-0014's existing trailing-population discipline exactly (both
  confirmed live this session to aggregate server-side across a `week=1,...,W-1` list the same way the existing
  four facets do); (2) new `snap_share.py` module mirroring `usage_share.py`'s shape, with the `pfr_player_id`→
  `gsis_id` crosswalk join spot-checked for match quality before being trusted, the same rigor ADR-0013 applied
  to `pff_id`; (3) a red-zone-filtered sibling to `usage_share.py`'s `aggregate_player_week`; (4) a pure
  team-level aggregation over `defense/coverage_scheme`'s already-ingested snap-count fields for man/zone
  tendency — no new API call for this one.
- **Architect follow-up, once Round A lands:** design the actual `PlayerDetailRecord` assembly/join layer
  (Round C) as its own reviewed piece of work, not folded silently into Round A's per-source modules.
- **No Section 6 formula changes.** Nothing here is a new scoring input — `snap_share`, `red_zone`, and
  `receiving/scheme`'s own man/zone splits are descriptive/display data for this round, not wired into
  `RoleShare`, `MatchupContext`, or any other formula. If a future pass wants to use, say, red-zone share as a
  projection input, that is a new Architect-reviewed decision, not an automatic consequence of this ADR.
- **`MatchupContext` implementation status is unchanged by this ADR** — it remains spec'd, data-confirmed, and
  not yet implemented as code, exactly as `stack_profile.py`'s existing notes already state. This ADR does not
  pull that work forward or duplicate it; it only clarifies that the player-detail "matchups" section depends on
  it.
