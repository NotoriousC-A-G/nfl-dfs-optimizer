# ADR-0008: DSTProjection — a new Section 6 construct for DST scoring

**Status:** Accepted (draft formula, new construct), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 3 (DST as a separate projection problem), PRD Section 6 (`GameEnvironmentScore`, `MatchupContext`), Fantasy Football Expert review (`docs/reviews/0002-fantasy-football-expert-section6.md`, item 7)

## Context

DST is a mandatory roster slot in every DK Classic lineup (Section 3), and Section 3 already establishes it needs its own treatment — "volatile, TD/sack-driven... not a scaled-down offensive model." Section 6's `MatchupContext` table honors the negative half of that instruction (none of its four rows fold DST into the offensive-skill-position machinery), but the positive half — DST getting its own projection formula — was simply missing. There is currently no scored basis for one of nine roster spots. This ADR adds `DSTProjection` as a fourth Section 6 construct, alongside `GameEnvironmentScore`, `StackProfile`, and `MatchupContext`.

## Decision

`DSTProjection` is a multiplier stack applied to a blended baseline DST point projection, built entirely from inputs already available elsewhere in Section 4/6 — no new data dependency.

### Baseline

`baseline_dst_projection(team)` — a blended vendor projection (PFF/RotoGrinders/Footballguys DST-specific point projections where available), combined the same way offensive player projections are blended in Section 5 step 3 (weighted blend with a defensible rationale, not an arbitrary average). Where a source doesn't publish a DST-specific projection, that source is simply excluded from that team's blend rather than substituted with an offensive-model number — consistent with Section 3's instruction not to scale down the offensive model for DST.

### Three multiplicative adjustments

Each of the three signals below is z-scored the same way as `GameEnvironmentScore`'s components (cross-sectional per week across all 32 teams, per ADR-0003's population/cutoff/early-season-blend rules — reused here rather than re-derived), then mapped to the same capped-multiplier range used throughout Section 6 (0.85x–1.15x), for consistency and because there's no stated reason yet to treat DST's grade-differential-to-multiplier relationship as differently shaped from the offensive rows:

| Signal | Data input | What it prices |
|---|---|---|
| **Opponent turnover-proneness** | Opposing offense's INT rate (INTs/pass attempt) and fumble rate (fumbles lost/play), from nflverse `import_pbp_data()` aggregation — same source and aggregation approach already used for pace/PROE | Turnover/defensive-and-special-teams-TD upside — a defense facing a turnover-prone offense has a better shot at the high-variance scoring events (pick-sixes, fumble recoveries/returns) that drive DST's biggest weeks |
| **Pass-rush vs. opposing pass protection** | The same PFF pass-block-grade-vs-pass-rush-grade differential already computed for `MatchupContext`'s Pass protection row (Section 6), consumed from the *defense's* side instead of the offense's — i.e., this DST's own pass-rush grade/win rate vs. the opposing offensive line's pass-block grade | Sack upside — the inverse framing of the exact matchup already priced for QB efficiency, reused rather than recomputed |
| **Opponent implied team total (inverted)** | The same implied-total z-score already computed for `GameEnvironmentScore`, for the *opposing* team, sign-flipped | Points-allowed-tier and short-field upside — a defense facing a low-implied-total opponent has a better shot at a scoring-friendly game script; a defense facing a high-implied-total opponent is more likely to be in a shootout/garbage-time script that suppresses DST value even when the underlying unit is good |

### Combination

The three multipliers are combined using the same **capped log-space method decided in ADR-0005**, extended to three terms instead of two, with a wider cap band to reflect the additional stacking: combined deviation capped at **±30%** rather than the ±20% used for the two-multiplier protection/coverage case.

```
combined_log = ln(m_turnover) + ln(m_pass_rush) + ln(m_opponent_total)
combined_log_capped = clip(combined_log, ln(0.70), ln(1.30))
combined_multiplier = exp(combined_log_capped)

final_dst_projection = baseline_dst_projection(team) * combined_multiplier
```

Naive multiplication of three independent 0.85–1.15 multipliers would produce a combined range of roughly 0.614–1.520 (±~38–52%) — even further from defensible than the two-multiplier case ADR-0005 addressed, so a cap matters at least as much here. ±30% (wider than ADR-0005's ±20%) reflects that these three signals are less obviously overlapping than the protection/coverage pair was: opponent turnover-proneness, this DST's own pass-rush-vs-opposing-protection matchup, and opponent implied total are conceptually distinct channels (turnover opportunities, sack opportunities, scoring-environment ceiling), not three measurements of the same underlying pressure signal. That said, pass rush and turnovers are not fully independent either — pressure forces some fraction of INTs and fumbles — which is why this isn't left uncapped; see the backtesting prerequisite below.

## Backtesting prerequisite

Before this combination is treated as final: correlate the pass-rush multiplier against the opponent-turnover-proneness multiplier across team-weeks, the same category of check as ADR-0003 (implied total vs. pace/PROE) and ADR-0005 (pressure rate vs. coverage grade). Action threshold and response are the same pattern used elsewhere in this revision: `|r| > 0.3–0.4` triggers residualizing the turnover-proneness signal against pass-rush pressure rate before combining (since some share of forced turnovers is mechanically downstream of pressure, not a fully separate skill/matchup signal), or narrowing the combined cap further if residualizing isn't practical for v1.

## Status

This is a brand-new formula, drafted in this revision pass — it carries the same "data-confirmed inputs, pending Model Analytics Expert + Fantasy Football Expert review" status as every other Section 6 construct, not "implemented." Data-confirmed here means every individual input (nflverse turnover rates, PFF pass-rush/pass-block grades, Odds API implied totals) is already established as available elsewhere in Section 4/6 — it does not mean this specific combination has been validated.

## Alternatives considered

- **Scale down the offensive `MatchupContext`/projection model for DST** (e.g., treat DST like a ninth skill position fed by the same pipeline). Rejected — directly contradicts Section 3's explicit instruction that DST is a separate projection problem, not a scaled-down offensive model.
- **A simpler single-factor model** (e.g., opponent implied total alone, the most commonly cited public DFS heuristic for DST). Rejected as the primary formula — it's a reasonable single input but discards two other signals (turnover-proneness, direct pass-rush matchup) that are already computed elsewhere in the pipeline for free; not using them would leave real signal on the table for no cost savings.
- **Naive multiplication of the three multipliers.** Rejected — see combination-method rationale above.
- **A wider or narrower cap than ±30%.** ±25% (matching ADR-0005's upper bound) was considered; ±30% was chosen instead specifically because a three-way stack has more room for genuinely additive, non-overlapping signal than the two-way protection/coverage case, and the backtesting prerequisite above exists precisely to correct the cap downward if that assumption doesn't hold.

## Consequences

- Implementation needs a `DSTProjection` module that consumes: (a) blended vendor DST projections (new blending logic, parallel to but separate from the offensive-player blend in Section 5 step 3), (b) nflverse turnover-rate aggregation (new aggregation, same `import_pbp_data()` source), (c) the existing pass-protection grade differential (reused, not recomputed, just consumed from the defense's side), (d) the existing implied-total z-score (reused directly, sign-flipped for the opponent).
- This closes the Fantasy Football Expert's highest-priority open item from this review round — Section 6 no longer has a mandatory roster slot with zero scored basis.
- Needs both Model Analytics Expert (is the multiplier-mapping and cap defensible) and Fantasy Football Expert (does this capture what actually drives DST scoring, or is something football-relevant still missing — e.g., special-teams TD rate, which isn't included here and may be worth a follow-up) sign-off before moving from draft to implemented, same process as every other Section 6 formula.
