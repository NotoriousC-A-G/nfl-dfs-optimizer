# ADR-0004: Spread-magnitude dampening for game-stack viability and RB/DST pairing penalty

**Status:** Accepted (draft spec), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 6 (`StackProfile`), PRD Section 7 (lineup construction rules), Fantasy Football Expert review (`docs/reviews/0002-fantasy-football-expert-section6.md`, items 5–6), Model Analytics Expert review (`docs/reviews/0001-model-analytics-expert-section6.md`, item 6/StackProfile)

## Context

Two related gaps, both raised by the Fantasy Football Expert and both solvable with data Section 6 already computes (spread and implied total from the Odds API, already feeding `GameEnvironmentScore`):

1. **`StackProfile`'s game-stack viability score had no actual formula.** "A direct function of `GameEnvironmentScore` for both teams" isn't implementable or reviewable as written (Model Analytics Expert). Separately, even a well-defined combination of the two teams' scores doesn't capture that a lopsided spread undermines the bring-back thesis specifically — a 30/17 implied split with a 13-point spread can still produce two decent-looking individual `GameEnvironmentScore`s while the actual expected game script (Team A builds a lead, Team B abandons the plan) makes the bring-back piece of a game stack unreliable, since a trailing team's passing volume in that state skews to checkdowns rather than the vertical/intermediate routes a bring-back thesis is usually built on.
2. **Section 7's RB/DST pairing rule is a flat scoring penalty**, applied identically regardless of game script, when the actual risk is highly script-dependent: an underdog RB in a high-total game carries real negative-script risk (pulled from the game plan if the team falls behind, opposing DST feasts on a broken plan); a favorite RB in a low-total run-funnel game carries much less (clock-control volume, and the opposing DST isn't in a shootout that inflates its own upside either).

## Decision

### 1. `StackProfile.game_stack_viability` — bottleneck logic + spread dampener

```
single_team_stack_viability(team) = GameEnvironmentScore(team)

game_stack_viability(game) = min(GameEnvironmentScore(team_A), GameEnvironmentScore(team_B))
                              * spread_dampener(abs(spread))

spread_dampener(abs_spread):
    abs_spread <=  3   -> 1.00   # pick'em / one-score game, full bring-back support
    3  < abs_spread <=  7   -> 0.85   # standard one-score-to-touchdown game
    7  < abs_spread <= 10   -> 0.65   # meaningful lean, bring-back thesis weakens
    abs_spread > 10   -> 0.40   # double-digit spread, blowout risk dominates
```

`single_team_stack_viability` (QB + own-team pass-catcher, no bring-back) uses the team's own `GameEnvironmentScore` directly — a lopsided spread doesn't undermine a plain single-team stack the way it undermines a bring-back, so no dampener applies there. `game_stack_viability` (the bring-back / full game-stack build) uses `min()` across both teams — a stack is only as strong as the weaker team's environment, since a bring-back needs both halves of the game to produce — multiplied by the spread dampener.

Band edges (3/7/10 points) and multipliers (1.00/0.85/0.65/0.40) are a specific starting proposal, not backtested constants — flagged for Model Analytics Expert validation like every other Section 6 threshold. Rationale for the shape: 3 points roughly brackets a one-possession/coin-flip game where game-script risk to the trailing team is minimal; 7–10 covers the range where a lead is very plausible but not close to certain; above 10 is squarely blowout territory where DFS consensus already treats the trailing team's passing volume as unreliable garbage-time production. The dampener reduces rather than zeroes out viability above 10 points because garbage-time volume is a real, if unreliable, source of production — not a case for excluding the bring-back thesis outright, just discounting it.

### 2. Section 7 RB/DST pairing penalty — scaled by implied total and spread

Replace the flat penalty with a multiplier on the existing base penalty, keyed to the same implied-total and spread buckets already computed for `GameEnvironmentScore`:

```
penalty_multiplier(RB's team):
    underdog (positive spread) AND implied_total in top tertile (league-wide, that week)  -> 1.5x base penalty
    favorite (negative spread) AND implied_total in bottom tertile (league-wide, that week) -> 0.5x base penalty
    all other spread/total combinations                                                    -> 1.0x base penalty  (unchanged)

final_penalty = base_penalty * penalty_multiplier(RB's team)
```

"Top tertile" / "bottom tertile" are computed the same way as `GameEnvironmentScore`'s cross-sectional population (ADR-0003) — all 32 teams' implied totals that week, split into thirds. A simple three-bucket multiplier (amplify / dampen / unchanged) was chosen over a continuous function because the underlying risk driver (negative game script) is itself closer to a threshold effect than a smooth gradient — a team is either plausibly playing from behind in a shootout or it isn't — and a three-bucket rule is trivial to state, implement, and later recalibrate from backtest data, consistent with the "doesn't need to be complex" bar for a v1 construction rule.

### 3. Section 7 text change

Section 7's game-stack-favoring bullet is updated to reference `StackProfile.game_stack_viability` (which already includes the spread dampener above) rather than restating "favored in the highest-`GameEnvironmentScore` games" — the spread logic lives once, in Section 6, and Section 7 just consumes it. This avoids the two sections drifting out of sync if the dampener bands change later.

## Alternatives considered

- **Sum or average of the two teams' `GameEnvironmentScore`s** instead of `min()`. Rejected — averaging lets a very strong team's score mask a genuinely weak environment on the other side, which is exactly the bottleneck the Model Analytics Expert flagged as needing an explicit answer; a bring-back thesis specifically depends on the *weaker* team's environment being viable, not the average.
- **A continuous (e.g., linear or logistic) spread dampener** instead of banded steps. Considered for smoothness, but rejected for v1 in favor of the simpler banded version — a continuous function implies a precision in the spread → bring-back-reliability relationship that doesn't exist yet without backtested data; banded steps are easier to reason about, easier to recalibrate band-by-band from results, and the review explicitly didn't require more than this.
- **A single flat "high spread" cutoff (double digits) with a binary allow/suppress**, closer to the Fantasy Football Expert's literal example. Replaced with the 4-band version above because a strict binary at exactly 10 points creates a cliff (9.5 vs. 10.5 point spreads treated as categorically different) that a banded dampener avoids while still landing on "double digits = the steep drop," matching the Fantasy Football Expert's own framing.

## Consequences

- `game_stack_viability` now requires both teams' `GameEnvironmentScore` (already required) plus the game's spread magnitude (already pulled from the Odds API for `GameEnvironmentScore`'s implied-total component) — no new data dependency.
- The Section 7 RB/DST penalty scaling requires the same league-wide weekly implied-total tertile split used elsewhere — implementation should compute this once per week and reuse it, not recompute per-formula.
- Both formulas are new judgment calls needing Model Analytics Expert (are the specific band edges/multipliers defensible pending backtesting) and Fantasy Football Expert (does the banding match how game script actually plays out) sign-off — flagged as such, consistent with every other Section 6/7 change in this pass.
