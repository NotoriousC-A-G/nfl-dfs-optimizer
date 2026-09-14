# ADR-0022: Wiring PFF slot-coverage and receiving-alignment facets into MatchupContext

**Status:** Accepted

## Context

[ADR-0001](0001-pff-data-availability.md) confirmed both PFF facets exist and mapped them to `DefenderAlignmentSnaps`/`ReceiverAlignmentShare`. [ADR-0006](0006-matchup-context-coverage-gating.md) defined the confidence-gating logic against those two dataclasses. But nothing fed them: `matchup/context.py`'s `build_matchup_context_pool` (added independently, see below) computes a WR/TE's multiplier from a team-wide coverage-scheme grade pair and never called into the alignment-gating logic at all. The `confident` path existed and was unit-tested in isolation, but could never be reached through the actual pipeline.

Note: `matchup/context.py` and `matchup/coverage.py` were built on separate branches from the same PRD in parallel, and landed in the opposite order — `context.py`'s general per-position `MatchupContext` (RB/QB/WR/TE, own-vs-opponent PFF grades, already wired into the Player Detail dashboard via `composition/player_detail.py`) merged to `main` first. `context.py`'s own `resolve_own_opponent_unit_grades` docstring already named the exact gap this ADR closes: its WR/TE branch reads "an opponent-defense aggregate, not the specific alignment split... not available at this layer yet." This ADR's original text described a standalone `build_matchup_context_pool`/`PassCatcherInput` pair; that was superseded before merging by integrating directly into the existing general pool builder instead, to avoid two competing `MatchupContext`-shaped APIs.

## Decision

- `src/nfl_dfs/ingestion/pff.py` adds `PFFClient.fetch_defender_alignment_snaps(league, season, week)` and `PFFClient.fetch_receiver_alignment_share(league, season, week)`, implementing the two-endpoint-per-fetch shape from ADR-0001.
- `build_matchup_context_pool` (in `matchup/context.py`) gains two optional parameters, `defender_alignment_snaps` and `receiver_alignment_share`, defaulting to `None` so existing callers (e.g. `composition/player_detail.py`, and every RB/QB/DST player) are unaffected. When both are supplied, a WR/TE's multiplier is computed by `compute_coverage_multiplier` instead of the plain z-score-to-multiplier mapping — fed the *same* own-vs-opponent z-score differential as `grade_differential`, so the multiplier magnitude is unchanged either way. What changes is that the pool entry also carries a `CoverageConfidence` and, when confident, the matched defender's id. The two multiplier mappings' span-per-z constants are kept equal (`MULTIPLIER_SPAN_PER_Z = 0.10` in both modules) specifically so the magnitude never depends on whether alignment data happened to be available.
- `scripts/live_integration_check_matchup.py` exercises this end-to-end against live PFF data (given a `PFF_API_KEY`), reporting the `coverage_confidence` distribution across a week's receivers so the confident path's real-world hit rate is visible, not just its existence.

## Consequences

- The `matchup/coverage.py` module docstring's "Known ingestion gap" section is updated: alignment snap/share ingestion is no longer the gap. The remaining gap is alignment-*specific* coverage grades — the multiplier still comes from the team-wide grade differential even when confidence is `confident`; only the confidence label reflects the alignment match today. Wiring a matched defender's own coverage grade into the multiplier itself is the next increment, not part of this change.
- `build_matchup_context_pool` trusts the caller to supply each player's `opponent_team` correctly (it has no schedule/game data of its own); the live-integration script resolves opponents itself via PFF's `/v1/games` for that week, since that's schedule data outside this ADR's ingestion scope.
- RB/QB/DST are entirely unaffected — the alignment inputs are only ever consulted for WR/TE.
