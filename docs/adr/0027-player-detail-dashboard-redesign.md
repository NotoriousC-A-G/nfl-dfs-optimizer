# ADR-0027: Player Detail dashboard redesign -- Fantasy Football Expert / UI/UX design review

**Status:** Accepted (implemented, unit-tested, and live-verified in a browser against a real
current slate -- see Consequences)
**Date:** 2026-09-14
**Owner:** Fantasy Football Expert / UI/UX
**Related:** ADR-0022 (`PlayerDetailRecord` itself), ADR-0025/0026 (ownership/leverage, this
round's default-column citizen), `docs/PRD.md` Section 5 step 9 / Section 8,
`src/nfl_dfs/composition/player_detail.py`, `src/nfl_dfs/dashboard/renderer.py`

## Context

Chris gave direct, pointed feedback on the Player Detail dashboard: too much noise for a
300-600-player weekly pool where most players aren't viable plays, no way to sort or filter out
non-viable rows, no projection or ceiling column despite that being "the most basic information
required," and a structural question about whether QB and DST need their own view. He also made a
broader, more important point: **the dashboard's content had been driven entirely by his own
literal requests, turn by turn, not by this project's own domain agents (Product Owner, Fantasy
Football Expert, UI/UX) proactively deciding what belongs there** -- exactly the autonomy the
subagent-team pattern was supposed to provide.

This round consulted the Fantasy Football Expert and UI/UX personas directly (their own
`.claude/agents/*.md` role definitions), asked each an open question in their own lane ("what data
would you want, where's the edge" / "how do you cut the noise"), and let them disagree with each
other and with Chris's own literal framing where they had a real basis to. The two decisions this
ADR resolves came from Chris after reading both experts' real, independent output, not from either
expert unilaterally, per this project's decision-rights convention:

1. **Ceiling** -- the Fantasy Football Expert's single biggest flagged gap (nothing in this
   codebase computes it; GPP lineup construction is a variance question, not a median one) -- is
   explicitly **out of scope this round**. It needs its own Model-Analytics-Expert/Fantasy-
   Football-Expert formula-design pass, like every other Section 6 formula, not a quick add
   alongside a UI rework.
2. **`StackProfile` placement** -- the FFE wanted it as real per-player columns (primary-stack
   rank, bring-back status); UI/UX wanted it kept out of Player Detail entirely (lineup rationale
   only). Chris sided with the FFE: real columns, joined at player grain.

## Decision

### 1. Data layer: `PlayerDetailRecord` gains five new sections (ADR-0022's own extension point)

`projection`/`projection_reason` -- the FFE's "should embarrass all of us" finding: `blended_projection`
was already computed by `projection/blend.py` and simply never reached the dashboard.
`_projection` mirrors `_salary`'s existing join exactly (same `PlayerProjection` row, different
field). `value` is a derived `@property` (points per $1,000 salary), not a stored field -- fully
determined by `salary`/`projection`, no reason of its own to explain.

`stack_context`/`stack_context_reason` -- `StackContext`, this player's slice of an already-built
`StackProfile` for their own game, found by matching `home_team`/`away_team`. `is_primary_stack_candidate`/
`primary_stack_rank` only ever fire for a home-team player, `is_bring_back_candidate` only for an
away-team player, matching `StackProfile`'s own anchor-team convention rather than inventing a new
symmetric one. `home_spread` is `StackProfile.spread` untouched -- never sign-flipped for the away
team's row, labeled by `home_team` so it stays unambiguous either way.

`injury`/`injury_reason` -- this player's own RotoGrinders Situation Room row, kept deliberately
distinct from `GameEnvironmentScore.injury_uncertainty_flag` (which reflects the team's *worst
unresolved case*, not this player). Unlike every other section, a `None` here has two real,
different meanings that must not collapse into one reason: "no injury data supplied at all" vs.
"not on this week's report -- presumed healthy" (real, positive information).

`slate_window`/`slate_window_reason` -- a new `slate_window_label()` pure function bucketing a
game's kickoff (America/New_York) into DK's real early/late/snf/mnf/tnf windows, needed for
late-swap construction (PRD Section 3). Computed from data already on hand (DK's own game start
times) -- no new ingestion.

`implied_total`/`implied_total_reason` -- the raw per-team number, sourced independently of
`GameEnvironmentScore` (confirmed by reading `compute_game_environment_score`: it only ever keeps
the z-scored composite contribution, never the raw total -- `ImpliedTotalInput.raw_implied_total`
exists on the input type but no caller in this codebase has ever populated it). Passed straight
from `ingestion/odds_api.py`'s own output rather than touching the `GameEnvironmentScore` formula
module for a display-only need.

### 2. Rendering: lean default columns, click-to-expand detail, click-to-sort, adjustable viability floor

**Default-visible columns (9):** Player, Pos, Team, Opp, Salary, Projection, Value, Proj Own%
(chalk/leverage), Stack. Everything else (role share, snap share, red zone, own man/zone scheme
split, opponent coverage faced, game environment, slate window, injury) moved behind a per-row
`<details>`-equivalent expand (click a row), reusing the same "collapsed by default" pattern this
file's own legend already used -- UI/UX's specific call that a 300-600-row pool needs a lean
resting view, and the FFE's own "wrong altitude for a default column" verdict on the man/zone
scheme split.

**Sortable columns:** click-header sort (`data-sort-value` per cell, numeric values sorted as
numbers not formatted strings), toggling ascending/descending, moving each row's paired expand-row
along with it.

**Sub-tabs (Skill / QB / DST), one implementation simplification disclosed here:** UI/UX recommended
three genuinely separate server-rendered column manifests. This implementation uses one shared
9-column table with a coarse Skill/QB/DST row filter instead, because the *actual* per-position
differentiation UI/UX and the FFE were both after -- QB/DST not being cluttered with RB/WR/TE-shaped
fields -- is already achieved by every field on `PlayerDetailRecord` adapting to position on its own
(`own_scheme_splits.applicable`, the skill-only expand blocks simply omitted for QB/DST). A second,
separately-maintained column-manifest path would duplicate real rendering logic three times without
changing what a QB or DST row actually shows. Flagged as a deliberate, disclosed divergence from the
literal design recommendation, not a silent shortcut.

**Viability filter, two-tier (UI/UX's design, implemented as specified):** a hard floor (no salary
and no projection -- structurally unusable, hidden by default, one checkbox to reveal) and a soft
floor (a visible, adjustable $-per-$1,000-salary number input, pre-filled to this slate's own
bottom-quartile value on load so the page opens already scannable -- never a silent/hardcoded
cutoff, always one click ("Reset filters") from showing everyone).

### 3. A real bug caught by live-verifying in an actual browser, not just unit tests

The sort-direction CSS used raw `\21C5`/`\2191`/`\2193` escapes inside a plain (non-raw) Python
triple-quoted string -- Python's own string parser treated `\21` as an octal escape (`\ooo`) before
the text ever became CSS, silently corrupting the arrow glyphs into a literal `"C5"`/control-
character mess in the rendered page. `pytest`'s string-containment checks never caught this (the
raw text was still "in the file," just wrong). Caught by actually opening the generated dashboard
in a browser and clicking a header -- fixed by using Python's own `\uXXXX` escape (`⇅` etc.),
which resolves to the real glyph *before* the string ever reaches the CSS, so there's no second
escape layer to collide with.

## What was built this pass

- `src/nfl_dfs/composition/player_detail.py`: `StackContext`, `PlayerInjuryDetail`,
  `slate_window_label()`, five new `PlayerDetailRecord` fields, five new composer functions, all
  wired into `build_player_detail_record`'s new optional kwargs (`stack_profiles`,
  `injury_by_canonical_id`, `kickoff_utc_by_team`, `implied_total_by_team`).
- `src/nfl_dfs/dashboard/renderer.py`: lean 9-column default table, per-row expand
  (`_render_player_expand_content`), click-to-sort, Skill/QB/DST sub-tab filter, two-tier
  viability filter, snap-share trend arrow, red-zone team-implied-total context, game-environment
  raw spread/implied-total display.
- `scripts/live_integration_check_dashboard.py`: reorganized to fetch odds/injury data once,
  earlier, feeding both the new Player Detail joins and the existing Slate Overview tab (previously
  fetched twice, now fetched once and reused).
- 18 new/updated tests across `test_player_detail.py`/`test_dashboard_renderer.py`.

**Live-run confirmation, in an actual browser, not just `pytest`:** real 658-player slate, real
sort-by-salary (confirmed descending order: $8,200 → $8,100 → $8,000...), real viability floor
(658 → 277 shown on load), real Skill/QB/DST sub-tab filtering (DST tab: 26/658), real per-row
expand confirmed position-appropriate (a DST row's expand shows Coverage Faced/Game
Environment/Slate Window only -- no Role Share/Snap Share/Red Zone/Own Scheme Split blocks, by
construction, not a hardcoded per-view list).

**A real, separate finding surfaced by this live run, not fixed here:** the injury join
(`normalization/injury_lookup.py`'s `build_injury_lookup`, unchanged by this ADR) matched only 1 of
23 real live injury-report entries to this week's reconciled RotoGrinders player pool -- the two
RotoGrinders endpoints (`ingestion/rotogrinders_injuries.py`'s Situation Room export and
`ingestion/rotogrinders.py`'s LineupHQ projection grid) appear to disagree substantially on which
players are still present, likely a real staleness/timing gap between the two feeds this late on a
live game day. This is a pre-existing ingestion-layer question, unrelated to this round's join
logic (confirmed correct by inspection), and is flagged here as a genuine open item rather than
silently worked around.

## Consequences

- The Player Detail dashboard now has the data both consulted experts said mattered most
  (projection, value, stack thesis, raw spread/implied total, slate window, player-specific
  injury), the presentation UI/UX asked for (sort, adjustable viability floor, lean default view),
  and the structural split the football reasoning called for (Skill/QB/DST), implemented as one
  shared table with a disclosed simplification rather than three column manifests.
- Ceiling remains a real, named, deferred gap -- next real formula-design candidate for the Model
  Analytics Expert / Fantasy Football Expert pair, not folded into this round.
- The RotoGrinders injury-report/LineupHQ staleness gap is a new, real, separately-tracked open
  item -- worth a dedicated live re-check the same way this project already tracks
  `service.fantasylabs.com`'s WAF behavior and RotoGrinders' `token` param lifetime.
- A lineup-*set*-level ownership rollup (does the 3-lineup set actually span chalk-to-leverage, the
  FFE's own point 9) remains unbuilt -- flagged, not silently dropped.
