# ADR-0005: Capped log-space combination for stacked MatchupContext multipliers

**Status:** Accepted (draft spec), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 6 (`MatchupContext` — Pass protection and Coverage rows), Model Analytics Expert review (`docs/reviews/0001-model-analytics-expert-section6.md`, item 3)

## Context

A receiver's final blended projection can be touched by two `MatchupContext` multipliers that both trace back, in part, to the same underlying signal: the QB-level pass-protection multiplier (flows downstream from pass-block-vs-pass-rush grade differential into every pass-catcher's target value) and that receiver's own individual coverage multiplier (from the man/zone scheme differential, blended per ADR-0001's alignment approximation). These aren't independent. PFF coverage grades are assigned play-by-play, and pressure materially contaminates coverage grading — a quick, inaccurate throw forced by pressure often reads as a "coverage win" in the underlying grade even when the corner did nothing unusual. For a receiver facing a defense with both a strong pass rush and a strong-graded corner, naively multiplying the two independent-looking 0.85–1.15 multipliers together produces a combined range of roughly 0.72–1.32 (±~28–32%) — overstating the combined penalty/bonus specifically where the confound is largest, since part of both multipliers is pricing the same pass-rush effect twice.

The Section 6 open-questions list already flagged this as unresolved ("whether pass-protection and coverage adjustments should stack multiplicatively or get capped in combination"). This ADR resolves it.

## Decision

Combine the pass-protection multiplier and the coverage multiplier in **log-space, with the combined deviation capped at ±20%** rather than multiplying them directly.

```
combined_log = ln(m_protection) + ln(m_coverage)
combined_log_capped = clip(combined_log, ln(0.80), ln(1.20))
combined_multiplier = exp(combined_log_capped)
```

This applies specifically where both multipliers land on the same receiver's projection (i.e., a pass-catcher whose QB's pass-protection multiplier and whose own coverage multiplier are both active in the same week). It does not change how either multiplier is computed individually, and it does not change the run-block multiplier (a different signal chain, applied to RB efficiency, with no comparable second multiplier stacking onto it in the current formula set).

**Cap choice — ±20%, not the ±20–25% range's midpoint or top.** The Model Analytics Expert's recommendation was a "band narrower than naive multiplication (~±32%) — e.g., ±20–25%." ±20% (the tighter end of that range) was chosen because the confound described above (pressure contaminating coverage grading) is flagged as "real, not just needing validation" — i.e., a known bias direction, not a hypothetical one — so the more conservative bound is the safer default until the backtest task below quantifies how much of the coverage grade the pressure signal actually explains. If that backtest shows the overlap is smaller than assumed, the cap can widen toward 25%; it's easier to relax a conservative default than to walk back an aggressive one that's already shaping live lineups.

## Backtesting prerequisite

Before this cap is treated as final: correlate team pass-rush win rate / pressure rate against opposing receivers' coverage grades across the play sample. If the correlation is material, residualize the coverage grade against pressure rate before combining (i.e., strip out the portion of the coverage grade already explained by pressure rate, so the two multipliers are measuring more genuinely separate things before they're combined at all) — at that point the log-space cap could potentially widen back toward ±25% or even toward naive combination, since the double-counting risk that motivates the cap would have already been addressed upstream. This is a concrete, assignable Model Analytics Expert task, not a general "needs more data" note.

## Alternatives considered

- **Naive multiplication** (current implicit default from "same 0.85x–1.15x capped-multiplier mapping otherwise" language in the coverage row). Rejected — see contamination argument above.
- **Take the more extreme of the two multipliers rather than combining them** (i.e., `max(deviation)` logic). Rejected — this would fully discard real, non-overlapping signal from whichever multiplier is smaller in magnitude, understating cases where pass rush and coverage are both genuinely strong for reasons beyond the shared pressure confound (e.g., a defense with both an elite edge rusher and a lockdown corner on a *different* receiver than the one facing the pressure most directly).
- **Residualize first, then combine naively**, skipping the log-space cap entirely. Rejected for v1 — residualizing requires the backtest correlation check to already be done and a residualization method chosen; the log-space cap is a reasonable interim safeguard that doesn't require that analysis to exist first, and can be relaxed once residualization is in place rather than blocking implementation on it now.

## Consequences

- Implementation needs a combination step downstream of both the pass-protection and coverage multiplier calculations, before either is applied to the receiver's blended projection — this is a small but real addition to the `MatchupContext` computation graph, not just a formula-text change.
- The backtest correlation check (pass-rush/pressure rate vs. opposing coverage grade) should be tracked as an explicit Model Analytics Expert to-do, same status as the `GameEnvironmentScore` correlation check in ADR-0003 — both are prerequisites for treating current weights/caps as final, not blockers on shipping the v1 draft formula.
- If a third multiplier is ever added to a receiver's chain (e.g., a future situational adjustment), the same log-space-sum-then-cap pattern should be reused rather than each new multiplier bilaterally deciding how to combine with what's already there — see ADR-0008 (`DSTProjection`) for the same pattern applied to a three-multiplier case.
