# ADR-0009: DSTProjection rework — opportunity-shaped sacks/turnovers as the core driver, plus a return-opportunity signal

**Status:** Accepted (draft spec, supersedes ADR-0008's structure), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** ADR-0008 (`docs/adr/0008-dst-projection.md`, superseded — kept as historical record of the first draft, not rewritten in place), Fantasy Football Expert re-review, explicit product direction from Chris (Product Owner)

## Context

ADR-0008 shipped a first `DSTProjection` draft — a three-multiplier stack (opponent turnover-proneness, pass-rush-vs-protection, inverted opponent implied total) applied to a blended baseline vendor projection. Both experts signed off on the original review round with conditions, but Chris reviewed the Fantasy Football Expert's DSTProjection findings and gave explicit product direction that changes the formula's *structure*, not just a parameter:

> "I think this is a good example of where scheme and script matters. For defenses, just giving up the least points isn't necessarily the best indication of DFS scoring. We need sacks, turnovers etc. Special teams TDs also come into play so you could have a mediocre D with a great returner that should pop."
>
> On the discrete-TD-event problem: "These seem hard to predict. We want the opportunities."

Three concrete problems with ADR-0008's structure, per this direction:

1. **Opponent implied total was weighted roughly equally with sacks/turnovers.** ADR-0008's three multipliers were structurally co-equal (same 0.85–1.15 cap each, same log-space combination with no differential weighting) even though Chris's direction is explicit that "giving up the least points isn't necessarily the best indication of DFS scoring" — points-allowed-style proxies should inform the formula, not anchor it.
2. **No mechanism at all for special-teams return-TD upside.** ADR-0008 had zero signal for a team with a mediocre defense but an elite/high-volume returner — a real, distinct DK scoring path (return TDs) the formula couldn't see.
3. **Two gaps the Fantasy Football Expert's re-review flagged**, both about the same underlying "script matters" theme: (a) opposing-offense turnover-proneness used a season-aggregate INT rate with no adjustment for a confirmed backup QB starting — the season aggregate reflects the *starter's* history, not necessarily the guy on the field this week; (b) the formula only looked at the opponent's game-script inputs, never the DST's own team's spread/implied total — a blowout-favorite defense's real opportunity (late-game bench rotation risk vs. an opponent forced to pass while trailing) differs from a close-game favorite's, against the same opponent.

Chris also gave explicit philosophy for the "hard to predict" problem: don't build a discrete TD-probability model (that's Phase 4 simulation territory, out of scope per Section 12/13) — model the *opportunity rate* instead, using proxies that are more stable and defensible than trying to predict the discrete score directly (pressure rate and tipped-pass rate as turnover-opportunity proxies rather than realized turnover rate alone; returner quality and expected return volume as return-opportunity proxies rather than predicting a return-TD outright).

## Data check: does nflverse support a return-opportunity signal?

Confirmed directly against nflverse's play-by-play data dictionary (`nflreadr`'s `dictionary_pbp`, the same source already used for pace/PROE aggregation) before specifying this signal — not assumed:

- **Returner identity:** `punt_returner_player_id` / `punt_returner_player_name` and `kickoff_returner_player_id` / `kickoff_returner_player_name` are real, confirmed fields, one row per punt/kickoff play, identifying who returned it (plus lateral-returner variants for the rare lateral case).
- **Return outcome:** `return_yards` ("yards gained by the return team... on any of: interception, fumble, kickoff, punt, or blocked kicks") and `return_touchdown` (binary) are both confirmed fields.
- **Whether a play was actually returned:** `punt_fair_catch`, `punt_downed`, `punt_out_of_bounds`, `punt_in_endzone`, `punt_inside_twenty` and the kickoff equivalents (`kickoff_fair_catch`, `kickoff_downed`, `kickoff_out_of_bounds`, `kickoff_in_endzone`, `kickoff_inside_twenty`) are all confirmed fields — these let the aggregation distinguish a live return attempt from a fair catch or touchback with no real return opportunity, which matters for computing a clean "return volume" rate rather than counting non-returns as opportunities.

This is sufficient to build both halves of a return-opportunity signal (volume and explosiveness) from the same `import_pbp_data()` source and aggregation approach already used for pace/PROE — **no new data dependency, and no need to fall back to DK/PFF/RotoGrinders/Footballguys returner data**, since nflverse covers it directly at player-level granularity. One real caveat, flagged below rather than silently absorbed: the NFL's kickoff rules changed meaningfully between the 2023 and 2024/2025 seasons (the "dynamic kickoff" format), which materially changed league-wide kickoff-return volume and touchback rates — this doesn't block the signal (ADR-0003's prior-season baseline already only reaches back to 2024/2025, which postdates the rule change), but it does mean kickoff-return volume specifically should not be compared against any pre-2024 baseline if one is ever pulled in for other purposes.

## Decision

### 1. Restructure the core multiplier so sacks and turnovers are the primary driver, not one of three co-equal inputs

Replace ADR-0008's flat three-multiplier stack with a **tiered structure**: a wide-range core (sack + turnover opportunity) and two narrow-range contextual modifiers (opponent implied total, own-team script). The core is given twice the multiplicative headroom of either contextual modifier, so it structurally dominates the combined outcome even after all three are combined — this is how "sacks/turnovers are the core driver, not one input among three" gets enforced numerically, not just asserted in prose.

**Core — sack + turnover opportunity (0.85x–1.15x range each leg, combined ±20% cap):**

- `sack_opportunity_multiplier` — unchanged from ADR-0008: this DST's own pass-rush grade/win rate vs. the opposing offensive line's pass-block grade (the same matchup already computed for `MatchupContext`'s Pass protection row, consumed from the defense's side). 0.85x–1.15x cap.
- `turnover_opportunity_multiplier` — **reworked to be opportunity-shaped, not purely outcome-shaped**, per Chris's philosophy directly: a blend of (a) the opposing offense's season-to-date INT rate + fumble rate (nflverse aggregation, same as ADR-0008, but now QB-continuity-adjusted — see below) and (b) this DST's own pressure rate — the same underlying pass-rush data feeding `sack_opportunity_multiplier` — as a turnover-*opportunity* proxy, since pressure mechanically creates tipped-pass and strip-sack chances independent of whether a given week's pressure actually converts to a realized takeaway. 0.85x–1.15x cap.
- These two combine via the same capped log-space method as ADR-0005/ADR-0008, **±20% combined cap** — appropriate here because the overlap between them (both partly built from this DST's own pressure-rate data) is now a deliberate design choice, not just a risk to flag: pressure and forced turnovers are genuinely the same underlying football phenomenon at different stages, and log-space capped combination is exactly the right tool for two signals sharing a common upstream driver, same reasoning as the original pass-protection/coverage case.

**Contextual modifiers — narrower range, demoted relative to ADR-0008 (0.90x–1.10x range each, individually):**

- `opponent_implied_total_multiplier` — same as ADR-0008 (opposing team's implied total, inverted), but the cap is tightened from 0.85–1.15 to **0.90–1.10**, explicitly reducing its maximum influence relative to the core. This is the direct fix for "giving up the least points isn't necessarily the best indication" — the signal stays in the formula (a defense facing a run-it-out, low-total offense genuinely does see a friendlier long-term scoring environment), but it can no longer swing the projection as far as sack/turnover opportunity can.
- `own_team_script_multiplier` — **new**, closing the Fantasy Football Expert's own-team-game-script gap. Own-team spread magnitude cuts both ways for DST opportunity: a big favorite risks bench rotation for its defensive starters in garbage time of a blowout win; a big underdog's opponent can run the clock out rather than keep passing once comfortably ahead, reducing this DST's own sack/INT chances late. Both scenarios plausibly reduce real opportunity relative to a close game, for different reasons — so this modifier is banded on **spread magnitude alone** (not favorite/underdog direction), reusing the same band edges as `StackProfile`'s spread dampener (ADR-0004, extended per ADR-0010) for consistency, but with much milder multipliers reflecting a genuinely smaller effect on DST opportunity than on a bring-back thesis:

  | Own-team `\|spread\|` | Multiplier |
  |---|---|
  | ≤ 7 | 1.00 |
  | 7–10 | 0.97 |
  | 10–14 | 0.93 |
  | > 14 | 0.90 |

- These two contextual modifiers combine with the already-capped core via the same log-space method, **outer combined cap of ±30%** (matching ADR-0008's original outer cap — naive combination of a ±20% core with two ±10% modifiers would allow up to ±40%, so the ±30% cap still does meaningful work).

```
core_log = clip(ln(m_sack) + ln(m_turnover), ln(0.80), ln(1.20))
combined_log = core_log + ln(m_opponent_total) + ln(m_own_script)
combined_defensive_multiplier = exp(clip(combined_log, ln(0.70), ln(1.30)))
```

### 2. QB-continuity adjustment to the turnover-proneness input

When ingestion confirms a starting-QB change for the opposing offense (reusing the same starter/injury-status ingestion channel already tracked for `GameEnvironmentScore`'s injury/role uncertainty flag — a real, computable trigger, not a narrative judgment), the opposing-offense turnover-rate input switches from that team's season-aggregate INT/fumble rate to a shrinkage blend of the confirmed new starter's own turnover rate and a league-average prior:

```
w_qb = min(1, career_attempts / 150)
qb_turnover_rate_blended = w_qb * new_starter_own_rate + (1 - w_qb) * league_average_starter_rate
```

150 career pass attempts was chosen as a specific, stateable threshold — enough of a sample that a QB's own rate isn't dominated by single-game variance, without requiring a full second season before his own number counts at all. **Deliberately, this blends toward the league-average *starting* QB turnover rate, not toward an assumed "backup QB penalty."** No assumption is made here that backup QBs are categorically more turnover-prone — that's an empirical claim this project hasn't validated, and inventing an unvalidated penalty constant would violate the same "trace to a real numeric input" ground rule this whole section is built on. If backtesting later shows a real, data-supported backup-QB effect, the Model Analytics Expert can add a specific, derived adjustment at that point — this ADR only commits to the shrinkage mechanism, not to a magnitude that isn't yet justified.

### 3. Return-opportunity signal — additive, not folded into the multiplicative stack

A new signal for special-teams return-TD upside, built from the nflverse fields confirmed above:

- `returner_volume_z` — z-score of the team's expected return attempts per game (live punt returns + live kickoff returns combined — i.e., plays where `punt_fair_catch`/`punt_downed`/`punt_touchback`-equivalent and the kickoff equivalents are all false, so fair catches and touchbacks don't count as opportunities), season-to-date, cross-sectional per week across all 32 teams, same population/cutoff/early-season-blend rules as ADR-0003 (with the 2024/2025-only prior-season baseline already being the right window given the 2024 kickoff-rule change noted above).
- `returner_explosiveness_z` — z-score of the specific identified returner's yards-per-return-attempt and/or long-return rate (e.g., share of returns ≥20 yards), computed from `return_yards` grouped by `punt_returner_player_id`/`kickoff_returner_player_id`, filtered to the same live-return-only plays as above.
- `return_opportunity_multiplier` = the same capped log-space combination of the two z-scored legs (0.85x–1.15x each, ±20% combined cap) used elsewhere in this section.

**Why additive rather than a fourth multiplicative leg:** Chris's own scenario is explicit — "a mediocre D with a great returner should pop" — meaning a strong returner situation should be able to add real value *independent of* how the core defensive multiplier reads. Folding a fourth multiplier into the same capped log-space stack as the core would let a mediocre defensive multiplier (e.g., 0.90 from weak sack/turnover opportunity) suppress the returner's contribution — exactly the outcome Chris is asking the formula not to produce. Instead:

```
return_opportunity_bonus(team) = (return_opportunity_multiplier - 1.0) * 3.0   [DK points]

final_dst_projection = (baseline_dst_projection(team) * combined_defensive_multiplier)
                        + return_opportunity_bonus(team)
```

The 3.0-point anchor was chosen with reference to DK's own DST scoring: a return touchdown is worth roughly 6 DK points as a discrete event. Sizing the *continuous* opportunity-based bonus at half that value at the capped extreme (±20% × 3.0 = ±0.6 points) keeps this signal from overstating what a volume/explosiveness proxy can honestly claim — it's a probabilistic nudge toward teams more likely to produce a return-TD over a season of GPP lineups, not a prediction that a specific elite-returner team scores half a touchdown's worth of bonus value this specific week. This is precisely the "opportunity, not discrete outcome" framing Chris asked for: the model never estimates P(return TD) directly, it scores the conditions that make one more or less likely, the same way the sack/turnover core scores conditions rather than predicting a specific sack or pick-six.

Framing note for the record: DK's DST scoring doesn't reward raw return yardage, only a return that goes for a touchdown — so this signal is unambiguously a proxy for discrete return-TD upside, not a claim that return yardage itself is fantasy-relevant. That's consistent with, not a violation of, "don't build a TD-probability model": the signal proxies for the *opportunity* that makes the discrete event more likely without the model ever computing or asserting a probability for it.

### Full formula

```
final_dst_projection = (baseline_dst_projection(team) * combined_defensive_multiplier)
                        + return_opportunity_bonus(team)
```

## Backtesting prerequisites

- Same as ADR-0008: correlate the sack-opportunity multiplier against the turnover-opportunity multiplier — now expected to show real correlation by design (both partly derived from this DST's own pressure rate), so the check here is about confirming the ±20% core cap is still appropriately sized, not about deciding whether to combine them at all.
- New: once a season of DK contest results exists, Performance Analytics should check whether the 150-career-attempt QB-continuity threshold and the 3.0-point return-opportunity anchor are calibrated correctly — both are stated, specific starting points per the Architect's ground rule to make a call rather than leave it open, not backtested constants.
- New: confirm the returner-volume z-score's cross-team distribution doesn't need a longer-than-6-week early-season fallback window specifically for special-teams — return volume can be a lower-snap-count signal than pace/PROE even at a full season's depth (a team might have very few high-leverage punt situations some weeks), so the Model Analytics Expert should sanity-check whether ADR-0003's 6-week shrinkage window is adequate here or needs a return-specific adjustment.

## Alternatives considered

- **A fourth co-equal multiplier for return opportunity**, folded into the same capped stack as sack/turnover/opponent-total. Rejected — see additive-vs-multiplicative rationale above; this was the most consequential structural choice in this ADR and directly implements Chris's "should pop independent of the base defense" requirement.
- **Predicting discrete return-TD probability directly** (e.g., a small logistic model on volume/explosiveness/field position). Rejected — explicitly out of scope per Chris's direction and the PRD's Phase 4 boundary (Section 12/13: simulation/discrete-outcome modeling is a later-phase concept, not a v1 formula).
- **A hard-coded "backup QB turnover penalty."** Rejected — no validated figure exists yet; see QB-continuity section rationale.
- **Keeping opponent implied total at the same 0.85–1.15 cap as the core, just re-labeled "secondary."** Rejected — a label without a structural difference doesn't actually change how much the formula can be swayed by a points-allowed proxy, which was the whole point of Chris's direction; the tightened 0.90–1.10 cap is what actually demotes it.

## Consequences

- `DSTProjection` now needs: (a) a QB-identity/continuity check against the same starter-status ingestion `GameEnvironmentScore` already uses, (b) a new nflverse aggregation for punt/kickoff return volume and explosiveness by team and by identified returner, (c) the tiered core/contextual/additive-bonus computation structure above, replacing ADR-0008's flat three-multiplier stack.
- ADR-0008 is superseded by this ADR but left unedited as the historical record of the first draft — readers should treat ADR-0009 as the current spec for `DSTProjection`, not ADR-0008.
- This is a structural redesign, not a parameter tweak — per Chris's own framing, it needs a **fresh, full re-review from both the Model Analytics Expert and the Fantasy Football Expert**, not a check that prior sign-off conditions were met. Flagged as such in the PRD status line.

## Addendum (2026-09-13): both experts' re-review — structure confirmed sound, two narrow fixes needed

Both experts completed the fresh re-review this ADR called for. Verdict: the structural redesign (tiered core/context, additive-not-multiplicative return bonus, QB-continuity shrinkage, own-team-script multiplier) is sound and was **not** reopened. Two narrow, parameter-level issues were found instead, both resolved in follow-up ADRs rather than by editing this one:

- The return-opportunity bonus's point-scale constant had a real magnitude bug (the ×3.0 scale was applied after, not accounting for, the ±20% cap already applied to the multiplier it's scaled from) — fixed with a data-derived constant in **ADR-0012**.
- The return-opportunity z-scores' population, identification gating, prior-season baseline logic, and partial pooling were underspecified — fully specified in **ADR-0012**.
- The QB-continuity weighting's `min(1, career_attempts/150)` form is superseded by **ADR-0011**'s shared `career_attempts / (career_attempts + 150)` empirical-Bayes form (same `k=150` reference point, different curve shape).

This ADR's core Decision section (sections 1–3 above) is otherwise unchanged and still the current spec for `DSTProjection`'s structure.

