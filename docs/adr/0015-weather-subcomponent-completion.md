# ADR-0015: Weather sub-component completion — precipitation magnitude, wind curve shape, three-way combination, roof-classification policy

**Status:** Accepted (draft spec), pending Model Analytics Expert light-touch review (flagged, not blocking — see Consequences); direction for Data Integration Engineer's next weather ingestion pass
**Date:** 2026-09-13
**Owner:** Architect
**Related:** ADR-0002 (`docs/adr/0002-weather-impact-thresholds.md`, wind/temperature curves — not rewritten, addendum added), ADR-0007 (`docs/adr/0007-weather-magnitude-damping.md`, 60% damping convention — not rewritten, addendum added), PRD Section 6 (`GameEnvironmentScore` weather sub-component), Data Integration Engineer's live weather ingestion pass

## Context

ADR-0002/0007 fully specified wind and temperature (bands, cited magnitudes, 60% v1 damping, "external, unvalidated" confidence tag) but left precipitation as a bare binary flag — "any measurable precipitation reduces the score," no magnitude, no bands. The Data Integration Engineer's `weather.py` outputs the binary flag plus raw inches but cannot produce an actual score contribution from that alone. Two smaller items surfaced in the same round: the wind curve's 10–20mph interior shape was implemented as an unreviewed quadratic interpolation (DIE's own engineering judgment call, flagged in code as such, since neither ADR ever specified the shape between tier boundaries); and retractable-roof/SoFi Stadium handling was documented only in code comments, not reviewed spec.

Closing precipitation the same way wind/temperature were closed also exposed a real, adjacent gap: Section 6 describes weather as **one** sub-component ("a penalty function on wind speed, temperature, and precipitation") worth 11.1% of `GameEnvironmentScore`, but ADR-0002/0007 never specified how the three inputs *combine* into that single number — each was described independently. Precipitation's magnitude can't produce "an actual score contribution" (the Data Integration Engineer's stated blocker) without that combination rule existing, so this ADR closes both gaps together rather than leaving the second one to block the first a second time.

## Research: precipitation's quantified effect on NFL passing

Rain and snow are treated separately — the underlying mechanisms are genuinely different (rain degrades grip and accuracy; snow additionally degrades footing, visibility, and ball security), and the coordinator's instinct that they might warrant different treatment held up under research, not just as a plausible guess.

**Rain — completion-percentage-point (pp) drop, by intensity (PFF, "The Factors: Rain, ball security, and efficiency," Week 12 2017, https://www.pff.com/news/fantasy-football-the-factors-week-12-2017 — the most intensity-granular source found):**

| Band | Completion % drop | Source |
|---|---|---|
| Light rain (<0.10 in/hr) | −2.3pp | Cited directly (PFF) |
| Moderate rain (0.10–0.30 in/hr) | −3.4pp | Cited directly (PFF) |
| Heavy rain (>0.30 in/hr) | **−5.0pp** | **Architect-extrapolated** — derived by applying the moderate/light ratio PFF's own data shows (3.4/2.3 ≈ 1.478x) one step further: `3.4 × 1.478 ≈ 5.0`. **Correction (2026-09-13, Model Analytics Expert review):** the original text here additionally claimed this was "cross-validated" against Sharp Football Analysis's light/moderate/heavy points-scored progression (2/4/6 points) as "a consistent ~1.5x-per-step escalation" — that characterization was wrong. 2/4/6 is an *additive* progression (+2 points each step), not multiplicative; its actual step ratios are 2.0x (light→moderate) then 1.5x (moderate→heavy), which is not internally consistent as a multiplicative rate and doesn't validate PFF's 1.478x ratio the way the original text implied. This doesn't undermine the 5.0pp figure itself — it's derived independently from PFF's own light/moderate data, not from the Sharp comparison — but the "cross-validation" framing overstated the supporting evidence. Worth flagging plainly: applying the *additive* shape Sharp's points-scored data actually shows (a constant per-step increment, not a constant ratio) to PFF's own light→moderate step size (`3.4 − 2.3 = 1.1`) would instead extrapolate heavy rain to `3.4 + 1.1 = 4.5pp`, not 5.0pp. This additive-vs-multiplicative ambiguity is real and unresolved by the sources at hand — flagged in the validation queue below for backtesting to settle, not decided by further reasoning from research alone. |

A second, intensity-*undifferentiated* source (The Fantasy Footballers, "The Fantasy Football Mythbusters," analyzing 2015+ NFL data, https://www.thefantasyfootballers.com/articles/the-fantasy-football-mythbusters-whether-weather-really-matters/) gives rain's average completion-% drop as "nearly 3%" — consistent with PFF's light/moderate average (≈2.85pp), a real cross-validation between two independent sources, not just one citation taken on faith.

**Snow — completion-percentage-point (pp) drop, by intensity:**

| Band | Completion % drop | Source |
|---|---|---|
| Light snow (<1 in/hr) | **−3pp** | **Architect-extrapolated** — anchored to Sharp Football Analysis's qualitative framing of light snow as only marginally worse than no precipitation (their light-snow points-scored figure, ~2%, is close to negligible) |
| Moderate snow (1–2 in/hr) | −7pp | Cited directly (The Fantasy Footballers' intensity-undifferentiated snow average, used here as the moderate/most-common-case anchor — most snow games are light-to-moderate, not blizzard conditions, so an undifferentiated average should land closest to the moderate band) |
| Heavy snow (>2 in/hr) | **−12pp** | **Architect-extrapolated** — snow's additional visibility/traction/ball-security mechanism (distinct from rain's grip/accuracy-only mechanism) justifies a steeper heavy-tier escalation than rain's; corroborated qualitatively by Sharp Football Analysis's heavy-snow points-scored figure (~25% decrease, roughly double the relative severity of heavy rain's ~6-point figure on a similar game-total base) |

**Extrapolated figures are flagged as such, not presented with the same confidence as the two directly-cited bands (light/moderate rain, moderate snow)** — consistent with this project's standing practice of never silently inventing a number (the same discipline already applied to the QB-continuity "no invented backup penalty" decision in ADR-0009 and the returner cold-start fix in ADR-0012).

**Secondary effects, documented but not folded into the score (same treatment ADR-0002 gave heat's grip/RB effect):** PFF's rain data also shows fumbles-per-carry rising sharply and non-linearly with intensity (+0.003/carry light rain, +0.015/carry moderate rain — a 5x jump, not a smooth scale-up), and multiple sources note drop rates rising 1–6% in precipitation generally. These are receiver/RB-specific, not team-agnostic — flagged as a candidate `MatchupContext`-level adjustment for the Fantasy Football Expert to evaluate, not decided here.

## Decision

### 1. Precipitation bands and damping

Precipitation intensity is classified using standard meteorological rate thresholds (not project-invented cutoffs): rain bands per NWS convention (light <0.10 in/hr, moderate 0.10–0.30 in/hr, heavy >0.30 in/hr); snow bands per common NWS-adjacent convention (light <1 in/hr, moderate 1–2 in/hr, heavy >2 in/hr). Per ADR-0007's already-established convention, the cited/derived pp figures above are applied at **60% strength** for v1, with the same **"external, unvalidated — sourced from public research, not this project's backtested data"** confidence tag, distinct from "data-confirmed" (which describes the input field being real and pulled, not the calibration):

| Band | Full-strength | Damped (60%) |
|---|---|---|
| Light rain | −2.3pp | −1.4pp |
| Moderate rain | −3.4pp | −2.0pp |
| Heavy rain | −5.0pp | −3.0pp |
| Light snow | −3pp | −1.8pp |
| Moderate snow | −7pp | −4.2pp |
| Heavy snow | −12pp | −7.2pp |

**Data check, done live, not assumed:** confirmed against a live Open-Meteo pull (`GET https://api.open-meteo.com/v1/forecast?...&hourly=precipitation,rain,snowfall`) that Open-Meteo's hourly response separates `precipitation` (mm, combined liquid-equivalent), `rain` (mm, liquid rain only), and `snowfall` (cm) as three distinct fields — rain and snow can be classified and rate-thresholded directly from data already being pulled, no new dependency, no need to infer precipitation type from temperature alone.

### 2. Three-way combination — wind, temperature, and precipitation into one weather sub-score

Each of wind, temperature, and precipitation was specified against its own best-available research in that research's own native unit (wind: ANY/A and FG-conversion%; temperature: passing-production %; precipitation: completion pp) — reasonable individually, but Section 6 presents weather as **one** 11.1%-weighted sub-component, and no combination rule existed. Rather than re-deriving each sub-effect in a new unit (which would mean overwriting ADR-0002's own citation-facing figures), each is converted to a **relative passing-efficiency degradation fraction** for combination purposes only, using a stated baseline for whichever sub-effect isn't already expressed as a percentage:

- Wind: `1 − (damped ANY/A ÷ baseline ANY/A 5.79)` — e.g., the steep-tier damped ANY/A (~5.09, from ADR-0007) gives a ~12.1% relative fraction, matching ADR-0007's own already-stated "~12% relative drop" framing.
- Temperature: already expressed as a relative % in ADR-0007 (~3% / ~4.8% damped) — used directly.
- Precipitation: `pp drop ÷ baseline completion rate (64%, a stated round approximation)` — e.g., damped heavy snow (−7.2pp) gives an ~11.3% relative fraction.

These three fractions become multipliers (`1 − fraction`) and combine via the same **capped log-space method used throughout Section 6** (ADR-0005/0009/0012 precedent): sum the log-multipliers, cap the combined deviation at **±30%** (matching `DSTProjection`'s three-factor cap, ADR-0009 — chosen for the same reason: three genuinely distinct physical mechanisms with only incidental real-world co-occurrence, not a shared confound the way pass-protection/coverage was, so a wider cap than the two-factor ±20% is appropriate). A realistic worst case (steep wind + extreme temperature + heavy snow, all damped) computes to roughly −26% combined — inside the cap without needing to bind, a reasonable sign the ±30% ceiling isn't either arbitrarily tight or so loose it does no work.

```
m_wind = 1 - (1 - damped_anya / 5.79)          # from ADR-0007's wind curve
m_temp = 1 - damped_temp_fraction               # from ADR-0007's temperature curve
m_precip = 1 - (damped_pp / 64)                 # from section 1 above

combined_log = clip(ln(m_wind) + ln(m_temp) + ln(m_precip), ln(0.70), ln(1.30))
weather_subscore = 11.1 * exp(combined_log)     # out of GameEnvironmentScore's 100-point scale (ADR-0003 renormalization)
```

This is the piece that actually unblocks `weather.py` producing "an actual score contribution" — precipitation's magnitude alone wasn't sufficient without this.

### 3. Wind curve 10–20mph interior shape — replace the unreviewed quadratic with a derived three-point piecewise-linear curve

DIE's quadratic interpolation between just the 10mph and 20mph endpoints was a reasonable instinct (the qualitative research does describe an accelerating effect) but is underdetermined by only two points — a generic quadratic through two endpoints doesn't necessarily reproduce ADR-0002's own already-stated shape claim ("the 15→20mph step is roughly 1.5–2x steeper than the 10→15mph step"), since that ratio depends on where the curve's vertex falls, which two points alone don't fix.

**Fix: derive an explicit 15mph anchor from ADR-0002's own stated ratio, then use piecewise-linear interpolation across three points (10, 15, 20mph) instead of a curve-fit across two.** Using the ratio's midpoint (1.75x, the center of ADR-0002's stated 1.5–2x range) to split the total ANY/A drop (5.79 → 4.62, a 1.17 total drop) into two segments:

```
D1 (10->15mph) + D2 (15->20mph) = 1.17
D2 = 1.75 * D1
=> D1 = 0.4255, D2 = 0.7445
ANY/A(15mph) = 5.79 - 0.4255 = 5.365
```

Giving three concrete anchor points — (10, 5.79), (15, 5.365), (20, 4.62) — with straight-line interpolation between consecutive pairs. This reproduces the stated accelerating shape exactly (the second segment's slope is, by construction, 1.75x the first's), is fully auditable from ADR-0002's own already-published ratio rather than an arbitrary new curve family, and is simpler to implement and verify than a quadratic fit. The same construction applies to the FG-conversion curve using its own endpoints (0% at ≤10mph, −6% cited at 20mph+, pre-damping): `D1=2.18%, D2=3.82%`, giving a 15mph anchor of −2.18%. This becomes reviewed spec, replacing the engineer's in-code judgment call — DIE's next pass should implement three-point piecewise-linear, not the quadratic currently in place.

### 4. Retractable-roof and SoFi Stadium classification — confirmed as v1 policy

Both defaults already implemented in `stadiums.py` are confirmed as acceptable v1 policy, promoted from code comment to reviewed spec:

- **Retractable-roof stadiums default to closed/dome** for the automated weekly pull, since no live roof-status feed exists to check ahead of an unattended run. This is a known, one-directional conservatism: on a day a retractable roof is actually open in good weather, the model will (harmlessly) assume no weather effect applies, which is usually correct anyway (roofs are typically closed specifically *because* of bad weather) — the failure mode is not "wrongly penalizing" but "very occasionally missing a real effect on a rare open-roof-in-bad-weather day," accepted as a v1 limitation rather than worth building a live roof-status source for.
- **SoFi Stadium (open-sided, fixed-roof design) is classified as a dome** for weather short-circuit purposes. The right criterion for this model is functional, not architectural: does weather actually reach the field. SoFi's translucent canopy fully covers the field and stands, so wind/precipitation/temperature effects on play are structurally closer to a sealed dome than to a genuinely open-air stadium, even though it isn't a sealed dome in the conventional sense.

## Alternatives considered

- **A single blended "precipitation" magnitude, not split by rain vs. snow.** Rejected — the research base genuinely differs by mechanism (grip/accuracy for rain vs. footing/visibility/ball-security for snow), and the two available direct citations (PFF for rain, The Fantasy Footballers for snow) point to materially different magnitudes at comparable intensity, not a single shared curve.
- **Deriving intensity bands from DFS-industry sources rather than NWS meteorological convention.** Rejected — unlike the wind-speed and temperature bands (which came from the DFS-research citations directly, since those sources defined their own bands), no precipitation-effect source gave usable rate-based band boundaries; NWS-style light/moderate/heavy thresholds are the standard, citable way to classify precipitation rate, so band *boundaries* come from meteorological convention while band *magnitudes* come from the DFS/football research above — a reasonable and clearly-stated division of labor between the two source types.
- **Quadratic (or other smooth) interpolation across the full 10–20mph wind range.** Rejected in favor of the three-point piecewise-linear construction — see section 3 rationale.
- **A tighter or looser combination cap than ±30%.** ±20% (matching the two-factor pass-protection/coverage case) was considered and rejected as too tight given wind/temp/precip are mechanistically distinct rather than sharing a confound; naive multiplication (no cap at all) was rejected for the same reason every other multi-factor combination in this project rejects it — see ADR-0005.

## Consequences

- Section 6's weather sub-component text needs the precipitation bands, the three-way combination formula, the corrected wind-curve interior shape, and the roof-classification confirmation added — a real but bounded addition, consistent with how wind/temperature were originally written up.
- `weather.py` (Data Integration Engineer implementation, not touched by this ADR) needs: (a) precipitation-type classification from Open-Meteo's already-available `rain`/`snowfall` fields, rate-thresholded per section 1; (b) the combination formula in section 2, replacing whatever ad hoc combination (if any) currently exists; (c) the three-point piecewise-linear wind curve from section 3, replacing the quadratic.
- ADR-0002 and ADR-0007 are not rewritten in place — both get a short addendum pointing to this ADR, same pattern as ADR-0008→0009 and ADR-0003/0009→0011.
- **Flagged, not blocking:** given the "external, unvalidated" confidence tag this sub-component already carries, a lightweight Model Analytics Expert look at the extrapolated bands (heavy rain, light snow, heavy snow — the three not directly cited) and the ±30% combination cap would be worthwhile before this is treated as fully reviewed, consistent with how wind/temperature got their scrutiny as part of the broader Section 6 review rounds rather than a per-component gate. Not a blocker for this round.

## Validation / backtesting queue (Model Analytics Expert)

Two items surfaced by the Model Analytics Expert's review of the bands above, both explicitly deferred to backtesting rather than resolved by further reasoning from public research alone:

1. **Heavy-rain extrapolation shape — additive vs. multiplicative.** As corrected above, whether heavy rain's completion-% drop should be extrapolated multiplicatively from PFF's own light/moderate ratio (→ 5.0pp, the value currently specified) or additively from PFF's own light→moderate step size (→ 4.5pp) is genuinely ambiguous from the cited sources — they don't agree on which shape is correct, and neither directly measures heavy rain. Once a season of this project's own backtested data exists, check actual heavy-rain-game passing efficiency against both candidate figures and adopt whichever the data supports; until then, 5.0pp (the multiplicative extrapolation) stands as the v1 default per the Decision section above, not because it's been shown more correct.
2. **Wind × precipitation interaction.** The ±30% combination cap's justification ("three distinct physical mechanisms, not a shared confound") correctly rules out the specific failure mode that motivated the *tighter* ±20% two-factor caps elsewhere in Section 6 (a measurement-attribution confound, like pressure contaminating coverage grades) — that reasoning is sound and isn't being revisited. But it doesn't address a different, real possibility: storm systems physically correlate high wind with heavy precipitation (wind-driven rain or snow), and the log-space combination's independence assumption has no way to represent an interaction effect — wind-driven precipitation could plausibly degrade passing more than the product of each factor's independent effect predicts, a genuine physical interaction rather than a measurement-attribution artifact. **Backtest task:** once game-level weather and passing-efficiency data exists, isolate games with simultaneously top-quartile wind and heavy precipitation, and check for a systematic residual beyond what the capped combination already predicts for those conditions. **If a residual appears, the fix is a wind×precipitation interaction term specifically** (an additional adjustment applied only when both legs are severe simultaneously), not a blanket retuning of the ±30% cap — temperature's effect operates through a mechanistically separate channel (grip/dexterity/muscle performance rather than wind's ball-flight-and-handling channel) and has no comparable physical reason to co-vary with wind the way precipitation does, so this specific concern doesn't extend to the temperature leg.

## Handoff note for the Data Integration Engineer's next ingestion pass

**`weather.py` does not yet implement this ADR.** Discovered during the `GameEnvironmentScore` implementation round (`src/nfl_dfs/game_environment/score.py`): the ingestion-code follow-up described in this ADR's Consequences section (precipitation-type classification, the three-way combination formula, the corrected wind curve) was never actually built — this Architect round only updated the PRD/ADR text. Concretely, `weather.py`'s `WeatherReading`/`parse_open_meteo_window` today only pull Open-Meteo's combined `precipitation` field and surface a binary flag plus a raw inches total; they do not pull the distinct `rain`/`snowfall` fields, do not compute an hourly rate, and do not apply the band table above. `GameEnvironmentScore`'s computation currently treats a missing precipitation leg as neutral (multiplier 1.0) rather than guessing around the gap — which is the right stopgap behavior, but it means **the weather sub-component silently understates its true effect on every game with meaningful precipitation** until `weather.py` is updated. This is the next, concrete, already-fully-specified task for the Data Integration Engineer's next weather ingestion pass — nothing further to design, just implement against sections 1–3 above.
