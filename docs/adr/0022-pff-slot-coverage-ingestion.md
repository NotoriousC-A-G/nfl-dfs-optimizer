# ADR-0022: Wiring PFF slot-coverage and receiving-alignment facets into MatchupContext

**Status:** Accepted

## Context

[ADR-0001](0001-pff-data-availability.md) confirmed both PFF facets exist and mapped them to `DefenderAlignmentSnaps`/`ReceiverAlignmentShare`. [ADR-0006](0006-matchup-context-coverage-gating.md) defined the confidence-gating logic against those two dataclasses. But `matchup/context.py`'s `build_matchup_context_pool` never actually populated them — every call to `compute_coverage_multiplier` passed `defender_alignment_snaps=None, receiver_alignment_share=None`. As a result the `confident` path existed and was unit-tested, but could never be reached through the actual pipeline; only `team_wide_fallback` and `no_data` were ever observed.

## Decision

- `src/nfl_dfs/ingestion/pff.py` adds `PFFClient.fetch_defender_alignment_snaps(league, season, week)` and `PFFClient.fetch_receiver_alignment_share(league, season, week)`, implementing the two-endpoint-per-fetch shape from ADR-0001.
- `build_matchup_context_pool` now accepts `defender_alignment_snaps` and `receiver_alignment_share` as pool-level inputs (still optional, defaulting to `None` so callers without ingestion wired up degrade to the pre-existing `team_wide_fallback`/`no_data` behavior rather than erroring). It groups the defenders by `team` and indexes the receiver shares by `player_id`, then for each pass-catcher looks up its opponent's defender group and its own share before calling `compute_coverage_multiplier` — replacing the hardcoded `None, None`.
- `scripts/live_integration_check_matchup.py` exercises this end-to-end against live PFF data (given a `PFF_API_KEY`), reporting the `coverage_confidence` distribution across a week's receivers so the confident path's real-world hit rate is visible, not just its existence.

## Consequences

- The `matchup/coverage.py` module docstring's "Known ingestion gap" section is updated: alignment snap/share ingestion is no longer the gap. The remaining gap is alignment-*specific* coverage grades — `compute_coverage_multiplier`'s multiplier magnitude still comes from the team-wide grade differential even when confidence is `confident`; only the confidence label reflects the alignment match today. Wiring a matched defender's own coverage grade into the multiplier itself is the next increment, not part of this change.
- `build_matchup_context_pool` trusts the caller to supply `opponent` correctly per pass-catcher (it has no schedule/game data of its own); the live-integration script resolves opponents itself via PFF's `/v1/games` for that week, since that's schedule data outside this ADR's ingestion scope.
