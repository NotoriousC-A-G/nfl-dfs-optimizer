# ADR-0012: Return-opportunity signal — population gating, cold-start baseline fix, and bonus recalibration

**Status:** Accepted (draft spec, closes the final open gaps from the DSTProjection re-review), pending Model Analytics Expert + Fantasy Football Expert confirmation
**Date:** 2026-09-13
**Owner:** Architect
**Related:** ADR-0009 (`docs/adr/0009-dst-projection-rework.md`, structure unchanged by this ADR), ADR-0011 (`docs/adr/0011-shared-shrinkage-functional-form.md`), Model Analytics Expert + Fantasy Football Expert second-round re-review

## Context

Both experts confirmed `DSTProjection`'s reworked structure (ADR-0009) is sound — tiered core/context, additive return-opportunity bonus, QB-continuity shrinkage, own-team-script multiplier — and explicitly said not to reopen any of it. Two concrete, narrow issues remained:

1. **A real bug, not a calibration nitpick (Fantasy Football Expert):** ADR-0009's return-opportunity bonus applied its ×3.0-point scale *after* the multiplier was already capped at ±20%, so the realized maximum bonus was `(1.20 − 1.0) × 3.0 = 0.6` points — not the ~3.0-point figure the original "half of a return TD's ~6-point value" reasoning implied at a skim read. Walked through with numbers: a mediocre defense (weak core multiplier, e.g. ≈0.81 combined) plus the maximum possible return bonus (+0.6) still landed below an average, returner-less defense at the same baseline. That fails Chris's explicit "mediocre D with a great returner should pop" requirement on magnitude, not structure.

2. **An underspecified population for the return-opportunity z-scores (Model Analytics Expert):** ADR-0009 said `returner_volume_z` / `returner_explosiveness_z` followed "the same rules as ADR-0003," but ADR-0003 was written for team-level metrics with a clean 32-observation weekly population (pace, PROE). Return production is partly a *player*-level metric (a specific returner's volume and explosiveness), and three sub-gaps followed from not specifying that distinction: (a) no stated population (team? identified primary returner? anyone with 1+ attempt?); (b) a latent bug in the prior-season baseline logic — blending a *team's* current-season thin sample toward that *team's* prior-season number is wrong when the actual returner changed, since it silently attributes a different person's history to the current player; (c) no partial pooling on the already-thin 2024/2025-only baseline window.

## Data check and bonus recalibration (issue 1)

Per Chris's explicit direction, computed `P(return TD | top-decile return-volume/explosiveness) × 6 DK points` directly from real 2024–2025 nflverse play-by-play, rather than widening the cap on an arbitrary guess. Live pull, not assumed: downloaded `play_by_play_2024.csv.gz` and `play_by_play_2025.csv.gz` from `nflverse-data`'s GitHub releases (the same source `import_pbp_data()` wraps) and computed directly.

**Method:** built a team-week table of live punt + kickoff return attempts (excluding fair catches, downs, touchbacks, and blocked punts — same exclusion logic already specified for the volume signal), each team-week's mean return yardage (explosiveness), and whether that team scored a return TD that week. Cross-sectionally z-scored volume and explosiveness within each week (32-ish teams), averaged the two z's into a combined score, and split team-weeks into the top decile (≥90th percentile) vs. the rest.

**Results (1,082 team-weeks across the full 2024–2025 regular + postseason window, 32 return TDs total):**

| Population | P(team has ≥1 return TD that week) | Implied expected points (`P(TD) × 6`) |
|---|---|---|
| Top-decile combined return z-score | **17.4%** | 1.046 pts |
| Bottom-decile | 0.0% | 0.000 pts |
| Everything outside the top decile | 1.1% | 0.068 pts |
| League-wide average (all team-weeks) | 2.8% | 0.166 pts |

The formula's bonus is meant to represent the **marginal** lift above an average, `z=0` situation (since the multiplier structure already returns 1.0 — and therefore a $0 bonus — at `z=0`), so the calibration target is the *delta*, not the raw top-decile figure: **top-decile minus league-average ≈ 0.88 points; top-decile minus "everything else" ≈ 0.98 points.** Both land in a tight band around **~0.9 points** as the real, data-derived marginal value of being in a top-decile return-opportunity situation.

## Decision

### 1. Fix the bonus scale — 4.5 points, not 3.0, applied after the existing cap (no cap widening needed)

```
return_opportunity_bonus = (return_opportunity_multiplier - 1.0) × 4.5   [DK points]
```

At the existing ±20% combined multiplier cap (unchanged — see below), this produces a maximum bonus of `0.20 × 4.5 = 0.90` points, matching the computed ~0.88–0.98-point marginal target above. **The existing ±20% cap on `return_opportunity_multiplier` does not need widening** — the bug was in the point-scale constant applied after the cap, not in the cap itself; correcting the constant alone reaches the data-derived target. This is a direct answer to Chris's conditional instruction ("if per-leg caps also need widening... widen them with that number as the target") — they don't, once the scale constant is corrected.

**Caveat, stated rather than smoothed over:** the underlying sample is thin — only 32 total return TDs across two seasons, and the top-decile bucket is only 109 team-weeks — so this is a legitimate historical-data-derived starting calibration per Chris's explicit request ("doable now, not blocked on live-season backtesting"), not a converged, low-variance estimate. It should be an early, concrete Performance Analytics backtest item once a season of this project's own results exists, same treatment as every other Section 6 constant sourced this way.

**Known remaining tension, flagged rather than fixed here (out of scope for this ADR):** because the core defensive multiplier and the vendor baseline projection may both already reflect a team's known defensive quality to some degree, a "mediocre defense" case can see its baseline discounted twice (once by the vendor's own lower baseline number, again by a sub-1.0 core multiplier) before the return bonus is even added — which can make the corrected +0.9-point bonus still insufficient to fully overtake an average team's projection in an extreme case, depending on how large that baseline gap already is. Fixing this would mean touching the core/context tiering or the baseline-vs-multiplier interaction, which both experts confirmed should not be reopened this round. Noted here for the backtesting queue, not resolved.

### 2. Return-opportunity population and gating (issue 2a)

**Identify separately per return role (punt, kickoff) — not one blended "the returner."** The same live nflverse data used for the bonus calibration above shows why this matters: punt-return duty is heavily concentrated (median primary-returner share of a team's punt-return attempts across a season: **81.7%**; a >50% majority holder exists for **89%** of team-seasons) but kickoff-return duty is not (median primary-returner share: **44.1%**; a >50% majority holder exists for only **31%** of team-seasons) — a real, data-confirmed consequence of the post-2024 dynamic-kickoff rules spreading kickoff-return work across more players. Treating "the returner" as a single undifferentiated concept would misrepresent kickoff-return reality for roughly two-thirds of teams.

**Gates, reusing ADR-0006's margin-threshold + snap-floor pattern:**
- **Margin threshold:** the plurality returner in that role must hold an absolute majority (>50%) of the team's live-return attempts in that role, matching ADR-0006's own threshold choice for consistency.
- **Attempt-count floor:** at least **8** live-return attempts in that role, within the identification window — chosen directly from the data above (25th-percentile full-season attempt count for an identified primary returner: 20 for punt, 8 for kickoff; 8 sets the floor at the more conservative, lower-volume role's own 25th percentile, rather than picking a number that would routinely fail to clear for kickoff returners specifically).
- **Identification window:** **season-to-date**, not ADR-0006's trailing 3-week window. Return attempts are far rarer than coverage snaps (full-season medians of 15–20 attempts per role, vs. the 15-coverage-snap floor ADR-0006 sets for a single 3-week window) — a 3-week window would routinely produce single-digit attempt counts, undermining the floor before it can do any work. This is a deliberate divergence from ADR-0006's window, not an oversight; the underlying event is simply much rarer.
- **Fallback when the gate fails for a given role:** team-level return production for that role, unweighted by individual identity — i.e., exactly the team-week aggregation methodology already used for the bonus-scale calibration above, which is why that calibration stays valid as the fallback case's own basis, not just the primary-identification case's.

**Combining the two roles:** `punt_return_opportunity` and `kickoff_return_opportunity` (each its own capped log-space combination of volume-z and explosiveness-z, per role) combine into the overall `return_opportunity_multiplier` via the same capped log-space method used throughout Section 6, weighted equally for v1. Weighting by each role's share of total return attempts is a reasonable refinement but not adopted now — flagged as a Model Analytics Expert backtesting candidate rather than decided unilaterally here.

### 3. Prior-season baseline logic — fix the identity-blending bug (issue 2b)

**The bug, concretely:** blending a thin current-season sample toward "the team's" prior-season number is only correct if the *same person* is the one whose current-season thin sample is being stabilized. Checked directly against the same nflverse data: a team's identified primary punt returner recurs as the same individual across 2024 and 2025 in only **31.2%** of cases; for kickoff returners, only **15.6%**. In the large majority of cases, blending toward "the team's" prior-season baseline would silently attribute a *different player's* history to whoever is returning this season — exactly the rookie/newly-traded-returner cold-start failure both experts flagged, now confirmed as the median case, not an edge case.

**Fix:**
- If the currently-identified primary returner for a role (per the gating in section 2 above) is confirmed, by matching player ID, to be the same individual identified as that team's primary returner in the prior season(s) — blend the current-season-to-date sample toward **that specific player's own** prior-season rate.
- Otherwise (new player, rookie with no prior-season NFL return record, in-season trade, or the team-level fallback case from section 2) — blend toward a **league-average returner baseline** (computed across all identified primary returners league-wide, prior seasons), not the team's own historical number.
- The team-level fallback path (when no individual clears the identification gates at all) blends toward the team's own prior-season *team-level* number, since that path is already using team-level aggregation on both sides and has no identity-mismatch problem to begin with.

### 4. Partial pooling on the thin 2-season baseline (issue 2c)

The 2024/2025-only prior-season window (correctly restricted per ADR-0003's own reasoning, given the 2024 kickoff-rule change) is thin — especially for a specific individual returner rather than a full team. Rather than using a returner's raw 2-season mean directly as the "prior baseline" referenced above, shrink it toward the league-wide 2-season average first, using ADR-0011's shared `n/(n+k)` form:

```
prior_baseline_pooled = w_pool * individual_raw_2season_mean + (1 - w_pool) * league_wide_2season_average
w_pool = n_individual / (n_individual + 25)
```

`n_individual` = that specific player's own live-return attempts across the 2024+2025 window (0 for a player with no prior-season record — which correctly collapses `w_pool` to 0, folding the cold-start case in section 3 into the same mechanism rather than needing a separate branch). `k = 25` — the middle of the Fantasy Football Expert's suggested 20–30 range, chosen over the endpoints because the observed per-season attempt counts for identified primary returners (punt: median 20, 25th-percentile 16; kickoff: median 15, 25th-percentile 8) are meaningfully lower than pass-attempt volume (where `k=150` was set), justifying a much smaller `k`, but not so small that a single noisy season fully dominates the league prior.

This is a second, distinct shrinkage stage from the current-season-to-date blend (`k=10`, per ADR-0011's table) — the two are chained: the current season's thin sample blends toward `prior_baseline_pooled`, which is itself already a shrunk quantity, not the raw 2-season mean.

## Alternatives considered

- **Widen the return-opportunity multiplier's per-leg or combined cap to reach the target bonus, instead of fixing the scale constant.** Rejected per Chris's explicit instruction — the scale-constant fix alone reaches the computed target within the existing ±20% cap, so widening the cap would be an unjustified additional change.
- **A single blended "the returner" identification across punt and kickoff.** Rejected — the real concentration data (81.7% vs. 44.1% median share) shows these are different problems with different answers for a majority of teams, particularly kickoff.
- **Keep blending toward "the team's" prior-season number unconditionally**, accepting the identity mismatch as a minor approximation. Rejected once the 31.2%/15.6% recurrence data made clear this is the median case, not a rare one — an approximation that's wrong more often than it's right isn't a reasonable approximation.
- **A single-stage shrinkage** (skip partial pooling of the prior baseline itself, blend current season directly against the raw 2-season mean). Rejected per the Model Analytics Expert's specific request for partial pooling on the thin baseline — with per-player 2-season samples this small, the "prior baseline" itself needs stabilizing before it's trustworthy enough to blend against.

## Consequences

- `DSTProjection`'s return-opportunity signal now needs: per-role (punt/kickoff) primary-returner identification with the stated gates, a team-level fallback aggregation path, player-ID-matched prior-season lookups (not just team-keyed), a league-average-returner reference table, and the two-stage shrinkage computation — a real but bounded addition to what ADR-0009 already required.
- The bonus-scale fix (4.5, not 3.0) is a one-line change to the existing formula, no structural impact.
- This ADR does not touch ADR-0009's core/context tiering, the additive-vs-multiplicative decision, the QB-continuity mechanism, or the own-team-script multiplier — all confirmed sound by both experts and left as-is.
- Given both experts' stated position that this should be the final fix round for `DSTProjection` specifically, and that this ADR resolves both of their concrete remaining items with real computed data rather than another round of estimation, no further re-review round is anticipated for `DSTProjection` — remaining items (backtesting the 4.5-point scale and the `k=25`/`k=10` constants against live results, weighting punt/kickoff roles by attempt share) are backtest-queue entries, not open spec gaps.
