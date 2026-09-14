# ADR-0010: Round-2 review refinements — spread-dampener tail, RB/DST cliff, alignment-fallback weighting

**Status:** Accepted, approved by Chris
**Date:** 2026-09-13
**Owner:** Architect
**Related:** ADR-0004 (`docs/adr/0004-spread-magnitude-dampening.md`), ADR-0006 (`docs/adr/0006-alignment-coverage-confidence-gates.md`), Fantasy Football Expert re-review items 1–2

Three small, concrete fixes from the second review round, each with a recommendation already supplied by the reviewing expert and explicitly approved by Chris. Grouped into one ADR rather than three because each is a narrow parameter/spec refinement to an existing decision, not a new structural judgment call — unlike ADR-0009's DSTProjection rework, which gets its own record.

## 1. Spread-dampener unbounded tail (ADR-0004)

**Problem:** ADR-0004's `spread_dampener` bottoms out at 0.40x for any spread over 10 points — a 10.5-point spread and a 24-point spread are dampened identically, even though the latter is a far more extreme blowout risk to a bring-back thesis.

**Fix:** extend the band table with two further tiers:

| `\|spread\|` | Dampener |
|---|---|
| ≤ 3 | 1.00 |
| 3–7 | 0.85 |
| 7–10 | 0.65 |
| 10–14 | 0.40 |
| > 14 | 0.25 |

The prior ">10 → 0.40" tier becomes "10–14 → 0.40"; a new ">14" tier drops to 0.25. Not zero — garbage-time bring-back production is unreliable, not impossible, and DK GPP fields are won on lower-probability outcomes too, so fully zeroing out the thesis at any spread would overcorrect. 0.25 was chosen as roughly another one-third reduction from 0.40, continuing the same shape of decay as the earlier bands rather than introducing a new curve family.

This directly updates `StackProfile`'s `game_stack_viability` formula in PRD Section 6, and is the same band table reused by `DSTProjection`'s new `own_team_script_multiplier` (ADR-0009) and the RB/DST penalty fix below — one band table, three consumers, so it only needs updating in one place going forward.

## 2. RB/DST penalty binary favorite/underdog cliff (ADR-0004)

**Problem:** ADR-0004's original RB/DST scaling checked favorite-vs-underdog as a binary sign test on the spread. A team at -0.5 (favorite) and a team at +0.5 (underdog) — a coin-flip game either way — landed in categorically different multiplier buckets (1.0x vs. up to 1.5x) purely because of which side of zero the line fell on, when the actual risk profile between those two spreads is nearly identical.

**Fix:** replace the binary sign check with a spread-magnitude-banded **strength** term, reusing the extended band table from fix 1 above, that scales the adjustment continuously with how lopsided the spread actually is — and goes to zero near a true pick'em, regardless of which side of zero the line sits on:

```
strength(|spread| band):
    <= 3     -> 0.0   (near pick'em — no adjustment either direction)
    3–7      -> 0.4
    7–10     -> 0.7
    10–14    -> 1.0
    > 14     -> 1.2

penalty_multiplier(RB's team):
    if underdog AND implied_total in top tertile:     1.0 + 0.5 * strength(|spread| band)
    if favorite AND implied_total in bottom tertile:  1.0 - 0.5 * strength(|spread| band)
    otherwise (wrong total tertile for the direction, or near-pick'em band): 1.0
```

At the extremes this still reaches roughly the original 1.5x / 0.4x-ish endpoints (1.0 + 0.5×1.2 = 1.6; 1.0 − 0.5×1.2 = 0.4), so the intent of ADR-0004's original scaling is preserved — only the near-zero-spread cliff is smoothed out, and the escalation across bands is now graded rather than a single step.

## 3. ADR-0006 alignment-fallback weighting (ADR-0006)

**Problem:** ADR-0006 specifies that when either confidence gate fails, the formula falls back to "the team-wide overall coverage grade weighted only by the receiver's own snap-share split" — but never specified *how* the team-wide grade itself is computed across multiple defenders. An unweighted average across every defender who logged any coverage snaps would let a low-snap rotational corner pull the team number as much as the CB1 playing 90% of defensive snaps — reintroducing, at the team-aggregation level, exactly the small-sample instability the two gates exist to prevent at the individual-defender level.

**Fix:** the fallback team-wide grade is explicitly **snap-weighted** across defenders, not a simple average:

```
team_avg_coverage_grade = Σ(defender_grade_i × defender_coverage_snaps_i) / Σ(defender_coverage_snaps_i)
```

computed over the same trailing window used for the primary alignment identification (ADR-0006's 3-week window). This team-wide, snap-weighted grade is then blended by the receiver's own slot/perimeter snap-share split exactly as ADR-0001 originally specified — only the team-wide grade's own internal computation changes, not the outer blending step.

## Consequences

- PRD Section 6 (`StackProfile`'s dampener table) and Section 7 (RB/DST penalty rule) need their band tables/formulas updated to match. Both reference the same underlying band table now, reducing future drift risk between the two rules.
- `MatchupContext`'s coverage-row fallback text needs the snap-weighting formula added explicitly.
- None of these three changes affect `DSTProjection` structurally — the shared band table is reused there (ADR-0009's `own_team_script_multiplier`) but that reuse was already part of ADR-0009's design, not introduced here.
- No new re-review is strictly required for these three items on their own — both experts already supplied the concrete recommendation being implemented — but they should be checked as part of the same re-review pass triggered by ADR-0009, since `StackProfile` and Section 7 are being touched again regardless.
