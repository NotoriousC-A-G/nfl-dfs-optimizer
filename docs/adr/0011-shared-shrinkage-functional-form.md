# ADR-0011: Empirical-Bayes shrinkage as a shared project pattern

**Status:** Accepted, pending Model Analytics Expert confirmation (this is the Model Analytics Expert's own repeated recommendation, being formally adopted)
**Date:** 2026-09-13
**Owner:** Architect
**Related:** ADR-0003 (`GameEnvironmentScore` early-season fallback), ADR-0009 (`DSTProjection` QB-continuity weight), ADR-0012 (`DSTProjection` return-opportunity cold-start, `docs/adr/0012-return-opportunity-signal.md`)

## Context

Three separate places in Section 6 needed a "how much do we trust a thin current-season sample vs. a prior/league baseline" weighting, and each was specified independently as a capped linear ramp of the form `w = min(1, n / threshold)`:

- `GameEnvironmentScore`'s pace/PROE early-season fallback (ADR-0003): `w = min(1, weeks_played / 6)`.
- `DSTProjection`'s QB-continuity turnover-rate blend (ADR-0009): `w = min(1, career_attempts / 150)`.
- `DSTProjection`'s new return-opportunity cold-start blend (ADR-0012): the same class of problem, about to be specified independently a third time.

The Model Analytics Expert flagged the same critique against the first two independently, and flagged it again against the third before it was even written: a linear ramp to a hard cutoff asserts **zero residual uncertainty** at an arbitrary threshold (at `weeks_played = 6`, the formula claims full confidence in a team's current-season number, with no acknowledgment that six games is still a real sample-size limitation, just a smaller one than one game). The statistically standard shape for this class of problem — blending a noisy small-sample estimate with a stable prior — is asymptotic empirical-Bayes shrinkage, which approaches full trust in the sample as it grows but never asserts it's reached with certainty.

## Decision

Adopt one shared functional form, used everywhere in Section 6 that blends a current, thin sample against a prior/league baseline:

```
w(n) = n / (n + k)
blended_value = w(n) * current_sample_value + (1 - w(n)) * prior_baseline_value
```

`n` is the size of the current sample, in whatever unit is meaningful for that specific metric (not forced to be the same unit — e.g., weeks played for a metric that accrues roughly evenly across weeks vs. raw attempt count for a metric that doesn't). `k` is a per-metric calibration constant, chosen so the curve's inflection sits at a sample size that reflects real confidence in that specific metric, and is stated explicitly wherever the form is used rather than assumed to transfer.

### Applied k values

| Metric | `n` definition | `k` | Rationale |
|---|---|---|---|
| `GameEnvironmentScore` pace/PROE (ADR-0003) | weeks played (completed weeks) | **6** | Matches the original linear ramp's threshold — chosen as a starting point that keeps behavior in the same neighborhood as the already-reasoned-through original, not re-derived from scratch. |
| `DSTProjection` QB-continuity (ADR-0009) | QB career pass attempts | **150** | Matches the original threshold's value, for the same reason. |
| `DSTProjection` return-opportunity, current-season-to-date blend (ADR-0012) | current-season live-return attempts, in that specific role (punt or kickoff) | **10** | New metric, no prior threshold to anchor to — set directly per ADR-0012's own data-driven reasoning (see that ADR). |
| `DSTProjection` return-opportunity, 2-season prior-baseline pooling (ADR-0012) | that individual's (or team's) own live-return attempts across the 2024+2025 window | **25** | New metric — see ADR-0012. |

### A real, non-cosmetic behavior change — stated plainly, not buried

Switching from `min(1, n/threshold)` to `n/(n+k)` with the *same* threshold value as `k` is not a drop-in replacement that happens to look different — it changes the actual blend weight at every point, and the asymptotic form never reaches full weight on the current sample, which the old capped ramp did. Two worked comparisons, so this is visible rather than asserted:

**Pace/PROE (`k=6`, matching `weeks_played=6` threshold):**

| Weeks played | Old: `min(1, n/6)` | New: `n/(n+6)` |
|---|---|---|
| 1 | 0.167 | 0.143 |
| 3 | 0.500 | 0.333 |
| 6 | 1.000 | 0.500 |
| 12 | 1.000 (capped) | 0.667 |
| 17 (full season) | 1.000 (capped) | 0.739 |

Under the old ramp, a team's pace/PROE number was treated as fully current-season by week 7 and stayed that way the rest of the season. Under the new form, even at the end of a full 17-week season, roughly a quarter of the weight (0.261) still sits on the prior-season baseline. This is the intended, statistically preferred behavior — no single season, even a complete one, fully resolves uncertainty about a team's true underlying rate — but it's a materially more conservative posture than the original spec, and the Model Analytics Expert should treat that as an explicit, visible change to confirm, not an implementation detail.

**QB-continuity (`k=150`, matching the `career_attempts=150` threshold):** the shift is even larger in relative terms — at exactly 150 attempts, the old ramp already trusted the QB's own rate fully (`w=1.0`); the new form is only at theform's own defined halfway point (`w=0.5`). This is because `n/(n+k)` reaches `w=0.5` exactly at `n=k` by construction — `k=150` was carried over as the new curve's *midpoint*, not its point of full confidence, which is a meaningfully more conservative stance on how much a single season-and-change of pass attempts should be trusted. Flagged here explicitly for the same reason as above.

Both changes lean toward *more* conservative (more prior-weighted) blending than the original specs, consistent with the general v1 posture already established elsewhere (e.g., the 60% weather-magnitude damping in ADR-0007) — erring toward underweighting a new or unvalidated signal rather than overweighting it.

## Alternatives considered

- **Keep the three ramps as independently-specified capped linear functions**, each documented inline where used. Rejected — this is exactly what produced the same critique landing three separate times; a shared, named pattern with one canonical derivation is easier for the Model Analytics Expert to review once and apply consistently, and prevents a fourth ad hoc ramp from appearing the next time this class of problem comes up.
- **Re-derive `k` from scratch for pace/PROE and QB-continuity** rather than reusing the old threshold value as the new `k`. Rejected for this pass — reusing the old threshold keeps the change legible (same reference point, different curve shape) rather than compounding "new functional form" with "new calibration" in one step; the Model Analytics Expert can retune either `k` independently once backtested data exists, without needing to also relitigate the functional form at the same time.

## Consequences

- PRD Section 6 text for `GameEnvironmentScore`'s early-season fallback and `DSTProjection`'s QB-continuity blend should reference this ADR's `n/(n+k)` form rather than restating the original `min(1, n/threshold)` formulas inline.
- ADR-0003 and ADR-0009 are not rewritten in place — both are left as the historical record of the original ramp design — but each gets a short addendum note pointing to this ADR as the current mechanism, the same pattern already used for ADR-0008 → ADR-0009.
- Any future shrinkage-style blend added to Section 6 should default to this form and add its `k` to the table above, rather than inventing a new ad hoc ramp.
- This changes real, live behavior (see the worked comparisons above) — not just documentation — so it needs Model Analytics Expert confirmation that the new curves, not just the shared form, are acceptable before implementation, even though the *pattern itself* is the Model Analytics Expert's own recommendation.
