# ADR-0037: Dup-risk-aware lineup generation -- built

**Status:** Accepted (implemented, unit-tested, live-verified end to end against a real slate pull)
**Date:** 2026-09-15
**Owner:** Chris, direct continuation of ADR-0035's "not yet built" note
**Related:** ADR-0035 (the fully-resolved design and real backtest this implements unchanged),
ADR-0034 (the descriptive dashboard read this reuses the join logic from), ADR-0032/0033 (the
underlying dup-risk data), `src/nfl_dfs/optimizer/lineup.py`, `src/nfl_dfs/composition/lineup_dup_risk.py`

## Context

ADR-0035 resolved the full design for dup-risk-aware lineup generation -- mechanism, exact bucket
thresholds, both experts' sign-off, a real backtest against settled ResultsDB field data -- and
deliberately stopped short of implementation, leaving two named preconditions for whenever it was
picked back up (see that ADR's Consequences). Chris chose to build it next.

## Decision

**Implements ADR-0035's final resolved design unchanged -- no new design review, since that round
already fully resolved the mechanism and the exact numbers.** This round's job was translating that
design into real code, addressing the two named preconditions, and updating `plain generate_lineups`.

### 1. New entry point, `optimizer/lineup.py`'s existing `generate_lineups` left untouched

`generate_dup_risk_aware_lineups(pool, projected_ownership_by_canonical_id, dup_risk_table, *,
oversample_size=20, game_environment_scores=None)` is a new, separate function. `generate_lineups`
itself is unchanged -- still plain best-3-by-projection, no new required arguments, no behavior
change for any existing caller. The new function:

1. Calls `generate_lineups(pool, n=oversample_size)` internally -- the exact same no-good-cut
   mechanism, just run further (ADR-0035's own "15-25 candidates instead of 3"; shipped default 20).
2. Classifies each candidate's average projected ownership through the real production
   `DupRiskLookupTable` (`classify_avg_ownership`, ADR-0033/0034's own mechanism, unchanged).
3. Selects the final lineups via a pure function, `_select_dup_risk_aware_lineups`, factored out
   specifically so ADR-0035's bucket rule can be unit-tested against hand-built `Lineup` fixtures
   without an actual `pulp` solve:
   - **Lineup 1**: `candidates[0]` -- best overall, no ownership consideration (identical to
     `generate_lineups`' own first lineup).
   - **Lineup 2**: best-`total_projected_points` candidate with bucket `<= LINEUP_2_MAX_BUCKET`
     (7); falls back to `<= LINEUP_2_FALLBACK_BUCKET` (8) only if nothing qualifies at <= 7.
   - **Lineup 3**: best-`total_projected_points` candidate with bucket `<= LINEUP_3_MAX_BUCKET`
     (2) -- never forced to bucket 0 specifically, per ADR-0035's own resolved finding that
     buckets 0-2 are statistically tied on dup rate in all 3 independently-checked seasons.
   - A candidate whose average ownership can't be computed (see precondition 2 below) is excluded
     from Lineup 2/3 consideration entirely -- never guessed into a bucket. Lineup 2/3 never reuse
     a core stack already claimed by an earlier slot (all candidates are pairwise core-stack-
     distinct by construction, so this only matters when the SAME candidate would otherwise
     qualify as the best pick for two slots).
   - `candidates` arrives already ranked best-to-worst by construction: each successive no-good
     cut only adds one more constraint on top of the previous solve's feasible region (forbids one
     specific core-stack combination, relaxes nothing), so solve `i`'s optimum can never exceed
     solve `i-1`'s. No separate re-sort needed or performed.

**No hard ownership cap, no linear penalty in the ILP objective** -- both explicitly rejected by
ADR-0035's own design review (ownership distributions shift slate-to-slate; the real relationship
is a flat region followed by a sharp cliff, not a smooth gradient). This is post-hoc candidate
selection over an oversampled pool, exactly as that ADR specified.

### 2. Precondition 1 (the "strong lineup" live proxy) -- addressed as a disclosed assumption, not a proof

ADR-0035 named this as something that "needs to be checked that it actually approximates the
backtest's own definition, not assumed to transfer cleanly." The backtest itself defined "strong"
using REAL, SETTLED contest outcomes (top 1% of each contest's own real point distribution) -- an
outcome-based definition that has no live equivalent, since a not-yet-played slate has no
outcomes. The Fantasy Football Expert's own named candidate proxy -- rank candidates by
`total_projected_points` within the oversampled pool -- is what got implemented.

**This could not be validated against real historical data the way the bucket thresholds
themselves were** (ADR-0018/0025 already established this project has no period-correct historical
vendor projections to replay against past slates), so it ships as a disclosed assumption, the same
"disclosed, not fabricated" posture `composition/lineup_dup_risk.py` already uses for its own
projected-vs-actual ownership comparison, spelled out directly in `generate_dup_risk_aware_lineups`'
own docstring rather than silently assumed. If period-correct historical projections ever become
available, re-validating this specific proxy (not the bucket thresholds, which are real,
outcome-based, and unaffected) is the natural follow-up.

### 3. Precondition 2 (documented as its own ADR) -- this document

ADR-0035's Consequences explicitly required a clean, explicit ADR update alongside the actual code
change, not folding this in as an implementation detail. This is that document.

### 4. Live ownership join, factored out for reuse rather than duplicated

`generate_dup_risk_aware_lineups` needs to classify MANY candidate lineups per call, not one --
`composition/lineup_dup_risk.py`'s existing per-lineup join (`assess_lineup_dup_risk`, ADR-0034)
was built for exactly one lineup at a time. Rather than duplicate that join logic, a new function,
`build_projected_ownership_by_canonical_id(identities, leverage_by_native_id)`, builds the full
reconciled identity pool's live projected ownership as one `canonical_id -> float` map (same
`identity.sources["rotogrinders"].native_id` join, same "omit, never fabricate" gating on an
unresolved match or missing leverage row) -- computed once per slate, then reused by every
candidate lineup's average inside `optimizer/lineup.py`. `assess_lineup_dup_risk` itself is
untouched; this is new, additive, reusable join logic, not a refactor of already-shipped code.

### 5. Live wiring (`scripts/live_integration_check_dashboard.py`)

The chalk/leverage-assessment block (ADR-0025/0026) and the dup-risk table build (ADR-0032/0033)
both had to move earlier in the script -- previously built well after lineup generation (only
needed for the post-hoc dashboard read, ADR-0034), now needed BEFORE generation too. Both are now
built once, right after the projection pool, and reused twice: once to drive
`generate_dup_risk_aware_lineups`, once for the unchanged post-hoc `assess_lineup_dup_risk` read
per selected lineup (a second, independent read over the lineup's REAL selected players, not a
redundant rebuild of the table). If the dup-risk table can't be built this pull (e.g. no curated
ResultsDB lineups data on disk), the script falls back to plain `generate_lineups(pool, n=3)`
rather than blocking the whole dashboard build on it -- the same graceful-degradation posture
every other optional section of that script already follows.

## Consequences

- `optimizer/lineup.py` now depends on `analysis/dup_risk_calibration.py` (a new, one-directional
  import -- no cycle: that module only depends on `storage/resultsdb_store.py`). `generate_lineups`
  itself gained no new dependencies or behavior change.
- **A real, deliberate design gap remains open, named by ADR-0035 and not addressed by this
  round**: PRD Section 7's own "none of the 3 should be... a pure max-leverage punt" text is still
  the only written guidance on where that line sits. ADR-0035's proposed Section 7 amendment
  language was never adopted as an actual PRD edit -- this implementation round shipped the
  bucket-2 ceiling in code (`LINEUP_3_MAX_BUCKET`) without also updating the PRD document itself.
  Flagged here for the record, not silently left inconsistent: the PRD amendment is still
  outstanding.
- Unit-tested: `_select_dup_risk_aware_lineups`'s bucket rule (best-per-slot selection, the
  bucket-8 fallback, the ownership-coverage-floor exclusion, no-core-stack-reuse-across-slots) is
  tested directly against hand-built fixtures, no `pulp` solve required; `generate_dup_risk_aware_
  lineups` itself is tested with a real ILP solve against the existing synthetic pool fixture,
  including the invariant that a single-bucket (everything-is-bucket-0) table degenerates to
  exactly the same output as plain `generate_lineups`. `build_projected_ownership_by_canonical_id`
  is unit-tested for the join/omission behavior directly. All existing tests (`generate_lineups`,
  `assess_lineup_dup_risk`) pass unchanged -- no existing behavior was altered.
- **Live-verified end to end** (`scripts/live_integration_check_dashboard.py`, a real week-1 2026
  DK slate, 658 players): the dup-risk table built from 3,782,486 real curated lineup rows
  (seasons 2023-2025), 273 identities resolved a live projected-ownership read, and
  `generate_dup_risk_aware_lineups` produced 3 distinct lineups (core-stack teams LAC/CIN/JAX)
  that all 3 went on to get a real post-hoc dup-risk read from `assess_lineup_dup_risk` -- the
  whole pipeline (leverage build -> ownership join -> oversample -> bucket selection -> dashboard
  render) ran cleanly with no errors or silent fallbacks to plain `generate_lineups`.
