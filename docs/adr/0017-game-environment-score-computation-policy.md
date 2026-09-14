# ADR-0017: GameEnvironmentScore computation policy — z-score mapping, missing-implied-total handling, injury-uncertainty rollup

**Status:** Accepted — two confirmed as-implemented, one revised
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 6 (`GameEnvironmentScore`), ADR-0003, ADR-0016, `src/nfl_dfs/game_environment/score.py` (implementation, not modified by this ADR)

## Context

The `GameEnvironmentScore` implementation round found three real formula-behavior decisions the PRD and prior ADRs never actually settled — only component weights, redistribution policy, and the injury flag's *existence* were specified, not these mechanics. The implementation made a reasoned, explicitly-flagged default for each (documented in `score.py`'s module docstring and inline at each decision point) rather than silently guessing, and surfaced all three for Architect confirmation. This ADR reviews each in turn.

## Decision 1: z-score → points mapping — standard normal CDF, confirmed

**Confirmed as reviewed spec, no change.** `points = weight_pct * Φ(z)` (the standard normal CDF), applied uniformly to all three z-scored components (implied total, pace, PROE).

Reasoning for ratifying this over the considered alternative (linear clip to ±3, rescaled to [0,1]): the CDF is the standard statistical mapping from a z-score to a percentile/favorability fraction — consistent with this project's general posture elsewhere (empirical-Bayes shrinkage in ADR-0011, capped log-space combination throughout Section 6) of reaching for the principled statistical tool rather than an ad hoc piecewise construction. z=0 landing at exactly half a component's weight is a clean, easily-explained anchor. Critically, a linear clip asserts a specific, arbitrary saturation point (why ±3 and not ±2.5 or ±4?) with no more justification than the CDF's own smooth asymptotic approach to the same practical effect — both approaches are already very close to saturated by z=±3 (Φ(3) = 0.9987), so the linear clip doesn't actually buy more sensitivity in the tails, only a harder, less-defensible corner. The CDF is preferred for having one fewer arbitrary constant, not for a large practical difference in output.

This applies identically to all three z-scored components — no reason to map implied total, pace, and PROE differently, since all three are z-scored against comparable roughly-normal cross-sectional populations (ADR-0003).

**Backtesting note (not a blocker):** whether the underlying z-score distributions are actually close enough to standard normal for Φ to be well-calibrated (rather than just a reasonable functional shape) is a Model Analytics Expert question once real data exists — same status as every other Section 6 calibration constant.

## Decision 2: missing implied-total behavior — whole composite unavailable, confirmed

**Confirmed as reviewed spec, no change.** When a team has no live Odds API line and no cached pre-kickoff fallback (ADR-0016 tier 3), the entire `GameEnvironmentScore` composite is marked unavailable (`is_available=False`, `composite_score=None`) — implied total's 44.4% weight is **not** redistributed to pace/PROE/weather the way a dome redistributes weather's 11.1%.

Reasoning for ratifying: a dome is a known, benign, fully-expected structural condition for a specific stadium, present every week of the season — redistributing around it is recovering full information from a predictable, harmless gap. A missing implied total after ADR-0016's own live-plus-cached fallback is categorically different: ADR-0003 established that implied total is available unconditionally from week 1 onward with no early-season fallback at all, so its absence — even after the caching fallback — signals a real pipeline failure, not an expected structural absence. Silently reweighting around it would let a materially degraded score (built almost entirely from pace and PROE — the same two components ADR-0003 already flagged as at-risk of correlating with implied total, pending that correlation-check prerequisite) present as shape-identical to a normal, fully-available score to downstream consumers. That specifically threatens `StackProfile`'s `min()` bottleneck logic across two teams' scores (ADR-0004): a silently-degraded number would be trusted as a real bottleneck input with no visible signal anything was wrong. An explicit `is_available=False` is the more honest failure mode, consistent with ADR-0016's own "flag unavailable, never impute" discipline applied one level up from a single data point to the whole composite.

**Explicit downstream consequence, stated here since it wasn't previously:** `StackProfile` and lineup construction should treat an `is_available=False` team-week as excluded from that week's stack-thesis consideration entirely — not zeroed out, not treated as league-average, not silently dropped from a `min()` comparison in a way that could be misread as "this team cleared the bar." A team with no computable `GameEnvironmentScore` this week has no basis for a single-team or game-stack thesis, full stop, and the absence should be visible in whatever output/QA surface reports on stack candidates that week.

## Decision 3: injury-uncertainty rollup — confirmed with one revision (a low-impact floor)

**Confirmed:** the allowlist-of-resolved-codes design (`RESOLVED_INJURY_STATUSES = {"O"}`, defaulting any unrecognized future code to "unresolved" rather than silently treating it as fine), the worst-player (not averaged) severity grading, and the `None`-when-no-unresolved-player contract are all sound and stay as implemented. The allowlist-over-denylist choice in particular is the correct fail-safe direction, consistent with this project's standing practice elsewhere (e.g. ADR-0006's identification gates defaulting to a conservative fallback rather than trusting an unverified case).

**Revised: add a low-impact floor below which an unresolved status doesn't raise the flag at all.** As implemented, *any* unresolved status — including a technically-questionable but functionally irrelevant deep-bench player with a low `IMPACTRTG` — sets the flag to at least `"moderate"`, because the rollup only checks whether the unresolved list is non-empty, not whether its worst member actually matters. Given most NFL teams have *some* player carrying a questionable tag in most weeks, this risks the flag reading `"moderate"` on a large fraction of team-weeks regardless of whether anything fantasy-relevant is actually in doubt — diluting the signal for the genuinely uncertain cases (a starting QB or WR1's questionable tag) that the PRD's own framing ("a starter's status is unresolved close to lock") is actually about.

**Fix:** a three-tier rollup instead of two, using the same worst-unresolved-player logic, with an added low floor:

```
worst_impact = max(p.impact_rating for p in unresolved_players)  # unchanged
if worst_impact < 2:      flag = None            # negligible impact -- not worth surfacing
elif worst_impact < 5:    flag = "moderate"       # unchanged threshold
else:                      flag = "high_uncertainty"  # unchanged threshold
```

`2` is chosen the same way the existing `5` was — a stated, defensible, round starting point on RotoGrinders' own 0–10 scale, not derived from a backtest. Reasoning: a rating of 0–1 plausibly represents a deep-bench or special-teams-only player whose uncertain status has no realistic bearing on the team's offensive/defensive output the way `GameEnvironmentScore`'s injury flag is meant to capture; `2` draws the floor just above that, leaving the existing `5` midpoint untouched as the moderate/high split. This doesn't change the "worst player, not averaged" logic or the resolved-status allowlist — only adds a floor beneath the existing two tiers.

## Alternatives considered

- **Linear clip to ±3 for z-score mapping.** Rejected — see Decision 1.
- **Redistribute implied total's weight on a missing value, mirroring dome redistribution.** Rejected — see Decision 2. `_redistribute_weather_weight` remains generic enough to reuse if this is revisited later, but isn't adopted now.
- **Leave the injury-uncertainty rollup as a flat two-tier (any unresolved status → at least "moderate").** Rejected — see Decision 3's dilution concern. A stricter floor (e.g., 3) or no floor at all (current behavior) were both considered; `2` was chosen as a minimal, targeted fix for the clearest failure case (near-zero-impact players) without being aggressive enough to also suppress genuinely borderline-relevant questionable tags.

## Consequences

- No code changes required for Decisions 1 and 2 — both ratify `score.py` as already implemented.
- Decision 3 requires a small change to `compute_injury_uncertainty_flag`'s threshold logic (add the `< 2` → `None` branch) — a Data Integration Engineer / implementation follow-up, not made by this ADR.
- All three numeric constants now confirmed as v1 policy (the CDF mapping's implicit calibration, and the `2`/`5` injury-impact thresholds) join the standing backtesting queue alongside every other Section 6 constant sourced this way (the 60% weather damping, the ±20%/±30% combination caps, the shrinkage `k` values) — stated starting points, not settled constants, per this project's consistent practice of making an explicit call rather than leaving a gap open.
