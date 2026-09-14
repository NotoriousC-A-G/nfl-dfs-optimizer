# ADR-0002: Weather-impact sub-component thresholds (temperature curve, wind curve)

**Status:** Accepted (draft thresholds), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 6 (`GameEnvironmentScore` — weather-impact component), Phase 0 weather-source confirmation (`docs/phase0/data-availability.md`, Weather section)

## Context

The PRD's draft `GameEnvironmentScore` weather-impact component (Section 6) originally covered only two inputs: a flat "wind above roughly 15 mph reduces the score" threshold, and a binary "any measurable precipitation reduces the score" rule. It had no temperature input at all.

Two gaps in that draft surfaced on review against public research on NFL weather effects:

1. **Missing temperature effect.** Temperature has a documented, non-linear (U-shaped) effect on passing offense that is independent of wind — both cold and hot extremes suppress passing production, and Section 6 had no way to represent that.
2. **Wind treatment too coarse.** A single "above 15 mph" cutoff treats 16 mph and 30 mph identically. Research shows the wind penalty accelerates above ~20 mph rather than stepping once at 15 mph — a flat threshold understates how much worse 20 mph+ actually is relative to the 15–20 mph band.

## Decision

**Temperature:** add a new sub-component to the weather-impact score, applied only to open-air games (same redistribution-to-the-other-three-components pattern Section 6 already uses for domed/indoor games), using a three-band U-shaped curve:

| Band | Effect on passing production |
|---|---|
| 55–85°F | Neutral — no meaningful effect |
| 25–55°F | ~5% dip |
| Below 25°F or above 85°F | ~8% decrease (cold and heat converge to roughly the same penalty) |

Additionally documented, but *not* folded into this game-level score for v1: heat degrades grip/ball security (a fumble/accuracy risk more than a scoring-rate one), and RBs specifically underperform in heat more than other positions. This reads as a team-specific acclimatization effect (a cold-climate team traveling to a hot game is a worse spot than the reverse) rather than a team-agnostic game score — Section 6 flags it as a candidate `MatchupContext` adjustment for the Fantasy Football Expert to evaluate rather than deciding it here.

**Wind:** replace the flat 15 mph cutoff with a piecewise curve — negligible below ~10 mph, mild 10–15 mph, moderate 15–20 mph, steep above 20 mph. This is a draft piecewise shape, not a fitted curve; Section 6 notes explicitly that exact curve-fitting is Model Analytics Expert's job at sign-off, not something finalized here.

**Precipitation:** left unchanged (binary — any measurable precipitation reduces the score). No research surfaced in this pass suggested it needed reshaping, and reshaping it wasn't asked for.

## Data availability

Confirmed directly (not just assumed from Phase 0's general "Open-Meteo covers weather" note): a live test pull against `https://api.open-meteo.com/v1/forecast` with `hourly=temperature_2m,wind_speed_10m,precipitation&temperature_unit=fahrenheit` returned populated `temperature_2m` values in °F alongside wind and precipitation. Temperature is not a new data dependency — it comes from the same already-confirmed Open-Meteo primary source as wind and precipitation, just a field the original Section 6 draft didn't ask for.

## Numeric sources

All thresholds and figures above are pulled from public research, not derived from this project's own data:

- Wharton Sports Business — "2022 Football, Parks & Weather": https://wsb.wharton.upenn.edu/wp-content/uploads/2022/09/2022_Football_Parks_Weather.pdf
- Advanced Football Analytics — "Weather Effects on Passing": http://www.advancedfootballanalytics.com/2012/01/weather-effects-on-passing.html
- Covers.com — "How Weather Affects Betting": https://www.covers.com/nfl/how-weather-affects-betting
- PFF — "Fantasy Football: The Factors, Week 14 2017": https://www.pff.com/news/fantasy-football-the-factors-week-14-2017
- Action Network — "Feeling the Heat: How the Weather Impacts NFL Outcomes": https://www.actionnetwork.com/nfl/feeling-the-heat-how-the-weather-impacts-nfl-outcomes
- FantasyLabs — "NFL DFS Weather Trends: Wind, Temperature, DraftKings & FanDuel": https://www.fantasylabs.com/articles/nfl-dfs-weather-trends-wind-temperature-draftkings-fanduel/

Specific figures cited in Section 6 and above (QB ANY/A 5.79 below 10mph vs. 4.62 at 20mph+; ~6% FG-conversion drop at 20mph+ alongside a ~7-yard shortening in average attempt distance; the 55–85°F neutral band; the ~5%/~8% passing-production dips) trace to this research set, not to a computation this project has run itself.

## Consequences

- These are **draft thresholds pulled from public, cross-context research (multiple seasons, all NFL stadiums), not yet validated against this project's own backtested data.** They carry the same status as every other Section 6 formula: data-confirmed input, pending Model Analytics Expert sign-off. The Model Analytics Expert should treat the exact band edges (25°F, 55°F, 85°F; 10/15/20 mph) and magnitudes (5%, 8%, the ANY/A and FG figures) as starting points to check against backtested data, not settled constants.
- The Fantasy Football Expert should separately evaluate whether the flagged heat-acclimatization effect (traveling cold-weather team vs. hot game) is worth a future `MatchupContext` adjustment, and whether RB-specific heat underperformance should factor into positional weighting anywhere in the pipeline.
- No change to the component's overall weight (10% of `GameEnvironmentScore`, redistributed to the other three for domed/indoor games) — only the internal shape of the wind/temperature/precipitation sub-components changed.

## Addendum (2026-09-13): precipitation magnitude, wind-curve interior shape, and three-way combination specified by ADR-0015

This ADR left precipitation as a bare binary flag ("any measurable precipitation reduces the score") with no magnitude, and never specified how wind, temperature, and precipitation combine into the single weather sub-score Section 6 describes. **Both are closed by ADR-0015** (`docs/adr/0015-weather-subcomponent-completion.md`): precipitation gets cited/derived rain and snow intensity bands (same 60% damping convention as this ADR), the wind curve's 10–20mph interior gets a derived three-point piecewise-linear shape (replacing an unreviewed quadratic interpolation), and all three sub-effects combine via a capped log-space method. This addendum does not rewrite the wind/temperature curves above; it points forward to where precipitation and the combination rule now live.
