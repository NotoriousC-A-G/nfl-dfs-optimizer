# ADR-0001: PFF field-level data availability — coverage-by-alignment

**Status:** Accepted
**Scope:** This ADR covers only the coverage-by-alignment question flagged in `docs/PRD.md` (Section 4's "Note on PFF field-level availability" and Section 6's open formula question about fallback behavior when alignment-level splits aren't available). Other Section 6 inputs (pass-block efficiency by lineman, run-block grades, etc.) are separate Phase 0 findings and out of scope here.

## Context

PRD Section 6's `MatchupContext` coverage row calls for "PFF coverage grade split by scheme (man rate vs. zone rate) and by alignment (slot vs. perimeter)... matched to the specific receiver's own alignment split." At PRD-authoring time this was unconfirmed against a live authenticated pull — the PFF Developer API's OpenAPI spec is public to browse, but the exact granularity wasn't verified.

## Findings

Pulled directly from PFF's live OpenAPI spec (`https://developer.pff.com/openapi.json`) and confirmed against the endpoint's documented response shape.

**Defense side — confirmed:**

`GET /v1/facet/signature/defense/slot_coverage` (`league`, `season`, `week` all required — an incomplete request short-circuits to an empty `200` rather than an error, which is why this API enforces all three as required params instead). Returns one row per defender who logged slot-coverage snaps that week:

```
player_id, team, position, coverage_snaps, coverage_snaps_per_target,
coverage_snaps_per_reception, targets, receptions, yards, yards_after_catch,
touchdowns, interceptions, qb_rating_against, yards_per_coverage_snap, ...
```

`coverage_snaps` here is the defender's **slot** coverage snap count. There is no equivalent `slot_coverage`-style signature endpoint for perimeter alignment. Perimeter snaps are derived instead from `GET /v1/facet/defense/coverage` (same `league`/`season`/`week`), which returns each defender's **total** coverage snaps as `snap_counts_coverage`:

```
perimeter_snaps = snap_counts_coverage - coverage_snaps
```

**Offense side — the endpoint this ADR originally left open:**

PRD-authoring time referenced only "the offense side of the same volume data" without a confirmed endpoint. Confirmed now: `GET /v1/facet/receiving/summary` (same required params) returns, per receiver:

```
player_id, team, position, slot_snaps, slot_rate, wide_snaps, wide_rate,
inline_snaps, inline_rate, routes, targets, ...
```

PFF's own alignment vocabulary here is `slot` / `wide` / `inline`, not `slot` / `perimeter`. "Wide" is PFF's term for the outside/perimeter alignment; "inline" (in-line tight end) is not used by this ingestion. `wide_snaps`/`wide_rate` map to `ReceiverAlignmentShare.perimeter_share`.

**Entitlement gating:**

Every report envelope can carry a top-level `restricted` array naming columns withheld from a caller whose PFF plan doesn't include them (e.g. `{"restricted": ["grades_coverage_defense"]}`). Its *absence* means nothing was withheld — it is not emitted for a fully-entitled caller. Ingestion treats a required column's presence in `restricted` as "no data for this report" rather than attempting to parse a response that's missing the field.

## Decision

`src/nfl_dfs/ingestion/pff.py` ingests both endpoints above and shapes them to `matchup/coverage.py`'s `DefenderAlignmentSnaps` and `ReceiverAlignmentShare`. See [ADR-0022](0022-pff-slot-coverage-ingestion.md) for the wiring into `MatchupContext`, and [ADR-0006](0006-matchup-context-coverage-gating.md) for how the resulting per-alignment snaps are turned into a confidence-gated defender match.

## Consequences

- Deriving perimeter snaps from two separate report calls means a defender present in `slot_coverage` but absent from `defense/coverage` that week (rare, but possible under partial-week data) can't have `perimeter_snaps` computed — that defender is skipped rather than assumed to have zero perimeter snaps.
- If PFF changes `slot_coverage`'s definition of "coverage snap" versus `defense/coverage`'s `snap_counts_coverage`, the subtraction could go negative; ingestion clamps the derived value at zero rather than emitting a negative snap count.
