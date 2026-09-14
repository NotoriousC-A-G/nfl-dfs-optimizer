# ADR-0007: Weather sub-component magnitude damping and confidence tagging

**Status:** Accepted (draft spec), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** ADR-0002 (`docs/adr/0002-weather-impact-thresholds.md`), Model Analytics Expert review (item 5), Fantasy Football Expert review (item 4)

## Context

ADR-0002's wind and temperature curves are pulled wholesale from public research (Wharton, Advanced Football Analytics, Covers.com, PFF, Action Network, FantasyLabs) — cited figures like a QB ANY/A drop from 5.79 to 4.62 at 20mph+ wind, a ~6% field-goal-conversion drop at 20mph+, and ~5%/~8% passing-production dips in the cold/heat temperature bands. The Model Analytics Expert's review flagged this as a materially different confidence level than the rest of Section 6: several sources are not peer-reviewed, pool multiple seasons and stadiums under different rule environments (one citation dates to 2012, a meaningfully less pass-friendly era), and none were fit against DK scoring specifically. Applying the cited figures at full strength risks miscalibration with no way to detect it until a season of this project's own backtested data exists.

## Decision

**Dampen the applied magnitude to 60% of the cited research figures for v1.** This lands inside the Model Analytics Expert's recommended 50–75% range, closer to the conservative end. Reasoning for 60% specifically rather than the range's midpoint (62.5%) or its minimum (50%): the cited sources are heterogeneous in quality (blog/industry pieces alongside more rigorous ones) but multiple independent sources converge on similar effect directions and roughly similar magnitudes for both wind and temperature — that convergence is real corroboration, which argues against the most conservative (50%) damping. At the same time, the era-mismatch concern (the 2012 pass-friendliness baseline) and the total absence of DK-scoring-specific fitting argue against anything close to full strength. 60% reflects "meaningfully discounted, but not discounted to the point of near-irrelevance."

Applied concretely:

| Effect | Cited figure | Damped (60%) figure used in v1 |
|---|---|---|
| Wind, QB ANY/A (below 10mph vs. 20mph+) | 5.79 → 4.62 (Δ1.17, ~20% relative drop) | 5.79 → ~5.09 (Δ0.70, ~12% relative drop) |
| Wind, FG conversion rate at 20mph+ | ~6% drop | ~3.6% drop |
| Temperature, 25–55°F passing dip | ~5% | ~3% |
| Temperature, below 25°F / above 85°F passing dip | ~8% | ~4.8% (~5%) |

The wind curve's piecewise shape (negligible below ~10mph; mild 10–15mph; moderate 15–20mph; steep above 20mph) and the temperature curve's band edges (25/55/85°F) are unchanged — only the applied magnitude within each band is scaled down. Precipitation (unchanged, binary) is not a magnitude-scaled effect and is unaffected by this ADR.

**Confidence tag — distinct from "data-confirmed."** Section 6 currently uses "data-confirmed" to describe every input, which up to now has meant "the specific field/endpoint exists and returns real values" — a statement about data *availability*, not about formula *calibration*. Applied to the weather sub-component, that phrasing reads as more confidence than is warranted: the underlying wind/temperature/precipitation data feed (Open-Meteo) is genuinely data-confirmed in that sense, but the curve shape and magnitude applied to that data is not — it's sourced from public research this project hasn't validated. Section 6's weather sub-component text is updated to carry both tags explicitly and distinctly:

- **Data source: data-confirmed** (Open-Meteo/NWS wind, temperature, precipitation fields are real and pulled) — same meaning as everywhere else in Section 6.
- **Curve calibration: external, unvalidated — sourced from public research, not this project's backtested data** — a new, separate tag, specific to this sub-component, that should not be inferred from or confused with the data-source tag.

## Alternatives considered

- **Full-strength cited figures (0% damping).** Rejected per Model Analytics Expert — see Context.
- **50% damping (the conservative floor of the recommended range).** Considered; rejected in favor of 60% given multiple-source convergence on direction and rough magnitude, per reasoning above. Revisit toward 50% if Performance Analytics' first-season backtest shows the damped curve is still overstating the effect.
- **75% damping (the permissive ceiling).** Rejected — the era-mismatch and non-peer-reviewed-source concerns argue for a figure closer to the conservative end, not the permissive one, until backtested.
- **A single "unvalidated" tag applied to all of Section 6** rather than singling out weather. Rejected — every other Section 6 formula at least uses this project's own data distributions (team grades, this season's play-by-play, this week's Vegas lines) even before weight validation is complete; weather's curve shape is uniquely sourced from entirely external, cross-context research with no project-specific fitting at all. Treating it identically to the rest of Section 6 would understate that real difference in provenance.

## Consequences

- Section 6's weather sub-component text needs the damped figures substituted in place of the raw cited ones, plus the explicit dual-tag (data-source vs. curve-calibration) language, so a reader doesn't conflate "the wind field is real" with "the 8%-decrease-at-20°F number is trustworthy."
- Per ADR-0002's own consequences section, this remains the first item Performance Analytics should backtest once a season of results exists, since it's categorically different in provenance from every other Section 6 input — this ADR doesn't change that priority, only the interim applied magnitude.
- No change to the component's overall composite weight (11.1% of `GameEnvironmentScore` post-ADR-0003 renormalization, redistributed to the other three for domed/indoor games) — only the internal magnitude of the wind/temperature sub-components changed.

## Addendum (2026-09-13): damping convention extended to precipitation by ADR-0015

This ADR's 60% damping convention and "external, unvalidated" confidence-tag pattern is **extended to precipitation's now-specified bands in ADR-0015** (`docs/adr/0015-weather-subcomponent-completion.md`), for the same reasoning given here — precipitation's cited/derived figures are equally external-research-sourced, not fit to this project's own data. This addendum does not change the wind/temperature damping specified above; it confirms the same convention now also governs precipitation.
