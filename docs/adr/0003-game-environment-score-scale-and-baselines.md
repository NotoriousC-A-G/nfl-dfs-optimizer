# ADR-0003: GameEnvironmentScore composite scale and z-score baseline specification

**Status:** Accepted (draft spec), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 6 (`GameEnvironmentScore`), Model Analytics Expert review (`docs/reviews/0001-model-analytics-expert-section6.md`, items 1–3, 6)

## Context

The Model Analytics Expert's review of Section 6 flagged two build-blocking gaps in `GameEnvironmentScore`, plus one calibration prerequisite:

1. The four scored components (implied total 40, pace 20, PROE 20, weather 10) sum to 90, not 100 — the injury/role flag was carved out as non-numeric, but the remaining weights were never renormalized. Two engineers would implement this two different ways.
2. "Z-scored against the season's implied-total distribution" / "league average for the season to date" is not precise enough to implement identically twice: population shape (per-week cross-sectional vs. pooled-cumulative), as-of cutoff, and early-season fallback are all unstated.
3. Before the resulting weights are treated as final for backtesting, implied total's independence from pace/PROE needs to be checked — a book's total already prices in expected play volume and pass rate, so weighting all three separately risks rewarding one underlying signal multiple times.

## Decision

### 1. Renormalize to a 0–100 scale; the injury/role flag stays outside it

The four scored components are renormalized so they sum to 100: **implied team total 44.4%, pace 22.2%, PROE 22.2%, weather 11.1%** (i.e., each original weight ÷ 0.9). The injury/role uncertainty flag remains a non-numeric confidence tag attached to the score — it does not consume any of the 100 points and is never blended in.

Rationale: a clean 0–100 numeric scale is simpler for every downstream consumer (`StackProfile`'s bottleneck logic in ADR-0004 takes a `min()` across two teams' scores — that only behaves sensibly if both scores share the same real scale) than a "score is actually 0–90, but is reported alongside a separate flag" convention that every consumer would need to know about. The alternative (leave it 0–90 and document the flag separately) was considered and rejected only because it pushes an easy-to-forget asterisk onto every future reader of the score, for no benefit — the relative weighting between the four scored components is unchanged either way.

### 2. Z-score population, cutoff, and early-season fallback

**Population shape — cross-sectional per week, not pooled-cumulative**, for all three z-scored components (implied total, pace, PROE). At a given week `W`, each team's current metric value is z-scored against the distribution of all 32 teams' current values for that same week, not against a pool of every team-week played so far this season. Rationale: cross-sectional isolates "is this team unusual *this week*," which is what the score is supposed to answer; pooling blends in earlier-season noise, bye-week gaps, and any in-season rule or weather drift, which would make a week-12 5.0 z-score and a week-3 5.0 z-score mean different things even though the score should be comparable across weeks by design.

**Population size — all 32 teams**, no subsetting by conference/division. No stated reason exists to subset, and subsetting would shrink an already-thin weekly sample further.

**As-of cutoff — completed weeks only, no current-week leakage.** Implied total is a legitimate pre-game number (this week's own Vegas line), so it is used as-of the current week with no leakage concern. Pace and PROE are different: they are team-level rates computed from the team's own played games, so a team's pace/PROE value going into week `W`'s score is computed from weeks `1..W-1` only — never including any in-progress or projected data from week `W` itself.

**Early-season fallback — shrinkage blend toward a prior-season baseline, pace/PROE only.** Implied total doesn't suffer early-season degeneracy — Vegas sets a full 32-team line every week starting week 1, so no fallback is needed there. Pace and PROE do: a team has zero or one game of current-season sample in weeks 1–2, which makes a z-score against an empty or near-empty within-season distribution either undefined or dominated by noise.

Fallback formula, applied before z-scoring:

```
metric_blended(team, week W) = w(W) * metric_current_season_to_date(team, through week W-1)
                              + (1 - w(W)) * metric_prior_season_baseline(team)

w(W) = min(1, weeks_played_through(W-1) / 6)
```

- `metric_prior_season_baseline(team)` = the simple average of that team's full-season 2024 and 2025 values for the metric (or the single most recent season's value if only one is available — e.g., an expansion of sample coverage happens over time, not a blocking condition).
- `w(W)` ramps linearly from 0 (week 1, zero games played, fully prior-season) to 1 (week 7 onward, fully current-season). 6 weeks was picked as a specific, stateable cutoff — roughly a third of a season, enough games that a team's own current pace/PROE sample is no longer dominated by single-game noise (one unusual game-script outlier is ~17% of a 6-game sample vs. 50%+ of a 1–2 game sample), while still resolving to fully-current by mid-season rather than dragging prior-season weight out longer than necessary.
- `metric_blended` is then z-scored **cross-sectionally against the same blended value computed for all 32 teams that week** (not against a mix of blended and unblended values) — this keeps the population internally consistent at every week, including in the target athlete's own week.

This mirrors the shrinkage/Bayesian-style blending philosophy already used for projection blending in Section 5 (vendor + in-house model, weighted by confidence/sample), applied here to the baseline distribution instead of to a single player's number.

### 3. Correlation-check backtesting prerequisite

Before the 44.4/22.2/22.2/11.1 weights are treated as final for backtesting or live use, run a correlation check across all team-weeks in the backtest window: season-to-date implied-total z-score vs. pace z-score, and vs. PROE z-score (using the same blended, cross-sectional definitions above).

- **Action threshold:** `|r| > 0.3–0.4` (Model Analytics Expert's recommended range).
- **If exceeded:** either (a) residualize pace and PROE against implied total before z-scoring, so each contributes only the marginal signal not already captured by the total, or (b) collapse pace + PROE into a single "play-volume" factor rather than two independent weights, and re-derive a two-factor (implied total + play-volume, still summing to 100 with weather/flag) weighting.
- **If not exceeded:** the current three-way split stands as specified above with no structural change.

This check is a scheduled Model Analytics Expert task before weight-locking, not a Phase 0 blocker — it's listed here so the prerequisite is explicit rather than assumed.

## Alternatives considered

- **Pooled-cumulative z-score population.** Rejected as primary — see population-shape rationale above. Not fully discarded: if the correlation check or later backtesting shows cross-sectional-per-week is too noisy week-to-week (e.g., a week with several teams on bye shrinks `n` and destabilizes the distribution), a pooled or rolling-window hybrid is a fallback worth revisiting, but that's a Model Analytics Expert backtesting call, not a default.
- **Leave the score 0–90 with the flag "layered separately."** Rejected — see section 1 rationale.
- **A fixed early-season fallback duration tied to calendar weeks 1–4** (Model Analytics Expert's own stated default recommendation). Considered and replaced with the weeks-played-based ramp above so the fallback behaves correctly around bye weeks (a team on a week-3 bye has only 2 games of sample at week 4, not 3) rather than keying off the calendar week number directly.

## Consequences

- `StackProfile`'s bottleneck logic (ADR-0004) depends on `GameEnvironmentScore` being on a consistent 0–100 scale across both teams in a game — this ADR's renormalization decision is a direct prerequisite for that formula being well-defined.
- Implementation needs access to each team's full 2024 and 2025 season pace/PROE values (not just current-season play-by-play) to compute `metric_prior_season_baseline` — this is a Data Integration Engineer scope note, not a new external data dependency (same `nflverse` `import_pbp_data()` source, just pulled for two additional prior seasons).
- The correlation-check task should be tracked as an explicit Model Analytics Expert backtesting to-do (Section 11 candidate or a QA/backtesting checklist item) so it isn't silently skipped once real data exists.

## Addendum (2026-09-13): shrinkage functional form updated by ADR-0011

The early-season fallback's `w = min(1, weeks_played / 6)` capped linear ramp, as specified above, is **superseded by ADR-0011**'s shared `w = weeks_played / (weeks_played + 6)` empirical-Bayes form (same `k=6` reference point, different curve shape — see ADR-0011 for the full comparison and rationale). This addendum does not rewrite the original decision above; it points forward to the current mechanism. Implement against ADR-0011, not the formula in this ADR's Decision section.

## Addendum (2026-09-13): "all 32 teams" population statement updated by ADR-0016

Live Odds API testing found the feed drops markets entirely for games already in progress at pull time (not a stale-line problem — the team is simply absent from that pull). **ADR-0016** (`docs/adr/0016-odds-api-missing-inplay-fallback.md`) updates this ADR's "all 32 teams" population statement for the implied-total component: it now means all 32 teams' most recently available pre-kickoff line, live if the game hasn't started at pull time, else the most recently cached pre-kickoff value from an earlier pull that week. This addendum does not change the cross-sectional/cutoff design above; it specifies what happens when a live pull can't reach every team.

