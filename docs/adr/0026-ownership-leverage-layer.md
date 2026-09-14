# ADR-0026: Live ownership/leverage layer (PRD Section 5 step 7)

**Status:** Accepted (implemented, live-verified against a real Sunday main slate; a real bug was found and
fixed during that verification, not just unit-tested -- see Consequences)
**Date:** 2026-09-14
**Owner:** Data Integration Engineer / Model Analytics Expert
**Related:** ADR-0023 (ResultsDB investigation), ADR-0025 (per-season ownership calibration, this ADR's main
input), `src/nfl_dfs/ingestion/rotogrinders.py`, `src/nfl_dfs/ownership/leverage.py`,
`src/nfl_dfs/normalization/position_aliases.py`, PRD Section 5 step 7

## Context

ADR-0023 section 6 named the eventual target for Section 5 step 7 but explicitly deferred it. ADR-0025 built
the historical half (a real field-ownership-by-salary baseline per position). This ADR builds the live half
and joins them: pull this week's live LineupHQ projected ownership, compare it against ADR-0025's baseline
for that salary tier, and flag real chalk and leverage spots.

## Decision

### 1. A real bug found and fixed along the way: the raw ResultsDB position code never matched RotoGrinders'

ADR-0025's `CORE_POSITIONS` used the raw ResultsDB payload's defense code, `"D"` -- but every other source in
this codebase (including RotoGrinders, which tags defenses `"DST"` natively) uses this project's canonical
vocabulary (`normalization/position_aliases.py`'s `CANONICAL_POSITIONS = {"QB","RB","WR","TE","DST"}`). Left
unfixed, this would have silently produced a calibration whose `"D"` key never matched anything this ADR's
live join needed. Fixed: added a `"resultsdb"` entry to `position_aliases.py`'s alias table, and
`ownership_calibration.py`'s `fit_season_calibration` now normalizes every row's position before filtering.
`CORE_POSITIONS` is now `("QB", "RB", "WR", "TE", "DST")`. Re-verified against the real 6-season backfill --
same 39,239 rows, same per-position correlations as ADR-0025 reported, just keyed correctly.

### 2. `POWN` was never actually extracted -- it exists in the payload, unused until now

ADR-0023 section 6 asserted projected ownership was "already sourced live from RotoGrinders LineupHQ,
`rotogrinders.py`." **That claim did not hold up under verification.** `rotogrinders.py`'s only parser,
`parse_user_projections`, builds `SourcePlayer` rows (identity fields only) -- it discards every other field
in the raw payload, including a `POWN` field (a percent string, e.g. `"41.48%"`) that is genuinely present on
every row, live-confirmed against a real slate pull. Fixed by adding a second parser over the same raw
payload (matching this codebase's established identity-vs-stats split, e.g. `pff.py`'s `SourcePlayer`/
`PffGradeRow`): `parse_projected_ownership` → `LineupHqOwnershipRow` (native_id, position, team, salary,
`projected_ownership`), and `fetch_rotogrinders_ownership`. The shared page/user-info/grid-selection auth
flow was extracted into `_fetch_projections_payload` so both parsers reuse one live call.

### 3. A second real bug, caught by live-verifying rather than trusting synthetic tests, then confirmed by Chris directly

Running the leverage layer against a real live slate (not just fixtures) surfaced something synthetic tests
never would have: several elite, healthy, actively-projected players -- Christian McCaffrey (21.4 projected
points, no injury flag), A.J. Brown, Puka Nacua, CeeDee Lamb, Travis Kelce, Dak Prescott -- all showed **0.00%
projected ownership**. That is not a plausible real number for any of them.

**Root cause, confirmed directly by Chris:** none of those six players are part of the Sunday main slate at
all -- McCaffrey and Nacua played Wednesday, A.J. Brown played Thursday, Dak and Lamb played the Sunday night
game, Kelce plays Monday night. A single LineupHQ grid pull returns **every slate window's players in one
response** (live-confirmed real `SLATE` values: `MAIN` 344 players, `WED` 30, `THU` 30, `SNF` 29, `MNF` 29) --
a player genuinely not eligible for the main-slate contest correctly shows 0% ownership *for that contest*,
which is indistinguishable from a real data bug unless the `SLATE` field is checked. This is the same "don't
silently mix slates" lesson `draftkings.py`'s `select_classic_slate` already learned and fixed for DK's own
API, recurring one source over -- a real, load-bearing project-wide pattern, not a one-off.

**Fixed:** `LineupHqOwnershipRow` now carries `slate`, and `filter_to_main_slate(rows)` keeps only `SLATE ==
"MAIN"` rows. Every caller of the ownership pull (the leverage report script, and any future consumer) must
filter before using the data -- `parse_projected_ownership` itself stays a complete, unfiltered parse (this
codebase's convention: parsers are pure, filtering is a separate, explicit, tested step) so a future need for
a specific single-game slate's own data isn't silently precluded.

### 4. The leverage model itself (ADR-0025's baseline joined against live POWN)

`src/nfl_dfs/ownership/leverage.py`, `build_leverage_assessments(rows, production)`:

- Salary decile and ownership percentile are both computed **within this week's own slate**, per position --
  never against a fixed historical player pool.
- `is_chalk`: top-decile ownership percentile within the position's own slate pool. **Relative to the slate,
  never a fixed absolute percentage** -- Chris's explicit stated convention this session ("no caps, always
  relative to slate size and circumstances").
- `is_leverage`: a top-half-salary-decile, non-chalk player whose (projected ownership - ADR-0025 baseline
  for that salary/position) falls in the bottom quartile of *this slate's own* distribution of that same
  deviation, among comparably-priced players. Also slate-relative, not a fixed point-gap constant.
- Every player gets a `note` field when flagged, stating the concrete why (salary decile, projected vs.
  baseline, point gap) -- not just a boolean, so a lineup decision made from this has a stated thesis behind
  it, matching this session's differentiation-by-thesis principle rather than mechanical flagging.

`scripts/ownership_leverage_report.py` -- live CLI: fetches, filters to main slate, loads the ADR-0025
production calibration, prints chalk and leverage lists.

**Live-run confirmation, post-fix:** real Sunday main slate, 344 players. 35 flagged chalk, all genuine
main-slate rostered-heavy players (Gibbs 41.5%, Chase 26.7%, St. Brown 19.3%, ...). 31 flagged leverage, all
real main-slate players with a modest, plausible under-baseline gap (Josh Allen -1.1pt, David Montgomery
-0.6pt, Chase Brown -0.7pt, ...) -- no more of the pre-fix false positives (no elite off-slate player appears
anywhere in either list now).

## Alternatives considered

- **Trust the top-level `POWN`/`legacy` field as-is.** Rejected -- confirmed live to be wrong for any player
  outside whatever slate `legacy` happens to default to, which this pass confirmed is not reliably the main
  slate. Silently shipping this would have produced false chalk/leverage signals for real lineup decisions.
- **Guess which `OWNERSHIP` sub-key corresponds to the main slate contest, from the numeric keys alone.**
  Considered mid-investigation (the numeric keys, e.g. `151307`, looked like real DK draft-group/contest IDs)
  but abandoned once the actual, simpler explanation -- `SLATE` -- was confirmed directly by Chris. Guessing
  at an unconfirmed key mapping would have been exactly the kind of unverified claim this project's
  discipline exists to avoid.

## Consequences

- Section 5 step 7 has a real, live-verified implementation for the first time -- not a placeholder.
- ADR-0023's own "already sourced live" claim about ownership is corrected: it required real new work
  (`parse_projected_ownership`), not just a wiring pass.
- ADR-0025's `CORE_POSITIONS` position-code bug is fixed; anything reading its output before this ADR should
  be re-run (nothing outside this ADR consumed it yet, so no other fix needed).
- **The main-slate-filtering lesson now applies to two independent sources** (`draftkings.py` and
  `rotogrinders.py`) -- a strong signal that any *future* source ingestion in this project should check for
  the same failure mode by default, not assume it's a DK-specific quirk.
- Not built this pass: dup-risk calibration (needs the still-uningested ResultsDB `lineups/` endpoint) and
  wiring this leverage output into the optimizer/dashboard itself -- this ADR produces the signal, not yet a
  consumer of it in the live weekly pipeline.
