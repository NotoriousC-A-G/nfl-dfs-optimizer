"""`GameEnvironmentScore` computation (PRD Section 6; Section 5 step 5) -- pure combination logic
over the three already-built ingestion modules' outputs (`ingestion/odds_api.py`, `ingestion/
nflverse.py`, `ingestion/weather.py`). This module does not fetch or recompute anything upstream:
it takes already-computed z-scores/shrinkage-blended values/damped weather effects as structured
input and produces the final 0-100 composite score per team-week, with each sub-component's
contribution visible separately (same transparency pattern the ingestion modules already use for
z-scores/shrinkage weights), per the task brief.

## Weights (ADR-0003)

`implied team total 44.4%, pace 22.2%, PROE 22.2%, weather 11.1%` -- kept here as exact fractions
(4/9, 2/9, 2/9, 1/9) rather than the rounded percentages, so repeated redistribution/composition
arithmetic across many team-weeks doesn't accumulate rounding drift.

## Weather weight redistribution (PRD Section 6, indoor/dome games)

"redistributed to the other three for indoor/dome games" -- implemented as a proportional
redistribution (`_redistribute_weather_weight`) that preserves the 44.4:22.2:22.2 ratio among the
other three (so implied total is still worth exactly 2x pace after redistribution, not an
arbitrary equal three-way split of the freed-up weight).

## Two real formula-behavior judgment calls in this module -- flagged for Architect confirmation

Neither is a settled part of the reviewed spec; both were necessary to make this module produce
an actual number end-to-end, and both are called out again at the point they're made below:

1. **z-score -> points mapping** (`_phi`): the PRD/ADR-0003 specify the four components' *weights*
   but never state how an individual z-scored component (implied total, pace, PROE -- unbounded,
   roughly N(0,1)) becomes "points" on the bounded 0-100 composite scale. This module uses the
   standard normal CDF (`points = weight_points * Phi(z)`), so z=0 ("league-average this week")
   lands at exactly half that component's weight. See `_phi`'s docstring for the alternative
   considered (a linear clip to +-3) and why the CDF was preferred for now.
2. **Missing implied-total behavior** (ADR-0016 tier 3 -- a team with no live line and no cached
   pre-kickoff value at all): this module marks the *entire* composite unavailable
   (`is_available=False`, `composite_score=None`) rather than redistributing implied total's
   44.4% weight the way a dome redistributes weather's 11.1%. See the judgment-call comment inside
   `compute_game_environment_score` for the full reasoning (short version: implied total is the
   single largest weight and, unlike a dome, its absence signals a real pipeline gap rather than a
   known/benign/expected condition -- silently reweighting around it would let a materially
   degraded score look like a normal one to downstream consumers, notably `StackProfile`'s
   `min()` bottleneck logic across two teams).

## Injury/role uncertainty flag -- NOW WIRED, via RotoGrinders' Situation Room injury report

PRD Section 6 describes this as "a flag, not a score... doesn't blend into the composite
numerically" (e.g. "moderate"/"high uncertainty"). The prior round found the LineupHQ payload's
`INJURY` field was dropped in ingestion with nowhere downstream to carry it (see git history for
that finding). This round replaces that dead end with a real source: RotoGrinders' separate
"Situation Room" injury report product (`ingestion/rotogrinders_injuries.py`, new this round) --
a clean CSV export with a per-player `STATUS` code and a graded 0-10 `IMPACTRTG` severity score,
joined to canonical `PlayerIdentity` records via `normalization/injury_lookup.py` (no changes to
`PlayerIdentity`'s or `SourceMatch`'s dataclass shapes were needed -- see that module's
docstring). `compute_injury_uncertainty_flag` below takes that joined per-team injury data and
rolls it up to the flag `GameEnvironmentScore.injury_uncertainty_flag` already had a slot for.

**Policy judgment call, flagged for Architect confirmation -- NOT settled spec, same pattern as
the two judgment calls already flagged in this module (`_phi` and the missing-implied-total
branch):** the PRD's own framing ("unresolved... close to lock") implies uncertainty is about
*not yet knowing* an outcome, not about a bad outcome that's already known. A confirmed `STATUS
== "O"` (Out) is resolved information -- the player is not playing, full stop -- which is a
question for the pipeline's player-pool-exclusion step, not for this uncertainty flag. Uncertainty
specifically means an outcome that's still unknown as of the pull: `Q` (Questionable) today, and
by the same reasoning any other status this module hasn't confirmed live yet (`D`/Doubtful was
flagged as "likely" by the task brief but not observed in the one live pull checked --
`rotogrinders_injuries.py`'s module docstring has the full live-pull findings). Given the status
code set isn't fully confirmed, `RESOLVED_INJURY_STATUSES` below is written as an allowlist of
*confirmed-resolved* codes (currently just `{"O"}`) rather than a denylist of unresolved ones --
so an unfamiliar future code defaults to "unresolved" (safer: surfaces as uncertainty for a human
to check) rather than silently being treated as fine. Severity (which of "moderate" vs.
"high_uncertainty") is graded off RotoGrinders' own `IMPACTRTG` (0-10) rather than treated as
binary, since that's exactly the "real graded signal" the task brief called out as the valuable
part of this source over a plain status flag. The chosen midpoint threshold (`>= 5` -> high) is a
reasonable, defensible split of RG's own scale, not derived from any backtest -- open to
recalibration once real outcomes exist to check it against.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# ADR-0003's renormalized weights, as exact fractions of 1.0 (not the rounded 44.4/22.2/22.2/11.1
# percentages) to avoid rounding drift.
WEIGHT_IMPLIED_TOTAL = 4 / 9
WEIGHT_PACE = 2 / 9
WEIGHT_PROE = 2 / 9
WEIGHT_WEATHER = 1 / 9
assert abs(WEIGHT_IMPLIED_TOTAL + WEIGHT_PACE + WEIGHT_PROE + WEIGHT_WEATHER - 1.0) < 1e-9

# ADR-0015's weather combination constants.
WEATHER_COMBINED_CAP = 0.30  # +-30% (ADR-0015: three distinct physical mechanisms, wider than
# the two-factor +-20% cap used elsewhere per ADR-0005/0009 precedent)


# --------------------------------------------------------------------------------------------
# Input shapes -- one per upstream ingestion module. These are NOT the ingestion modules' own
# dataclasses (this module must not reach into their internals); they're the small slice of each
# module's already-computed output that this computation actually needs, named to make the
# mapping from ingestion output column/field -> input field obvious at the call site.
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ImpliedTotalInput:
    """From `odds_api.py`'s `compute_implied_total_zscores` output (one row per team/week):
    `implied_total_z`. `z=None` means ADR-0016 tier 3 -- no live line and no cached pre-kickoff
    value existed for this team this week. This must never be a fabricated/imputed value
    (ADR-0016's own "flag unavailable, never impute" decision) -- callers should pass `None`
    exactly when `odds_api.py`'s pipeline itself has no value for this team-week, not a
    league-average or any other stand-in.
    """

    z: float | None
    raw_implied_total: float | None = None  # carried through for QA/display only; not used below


@dataclass(frozen=True)
class PaceProeInput:
    """One team's row from `nflverse.py`'s `compute_pace_proe_for_week` output -- already blended
    (ADR-0011 shrinkage) and z-scored (ADR-0003 cross-sectional-per-week) upstream. This module
    only consumes the final `pace_z`/`proe_z` plus the shrinkage metadata, surfaced here for
    transparency (not recomputed): `weeks_played` and `shrinkage_weight` map directly to
    `compute_pace_proe_for_week`'s own `weeks_played`/`shrinkage_weight` columns.
    """

    pace_z: float
    proe_z: float
    weeks_played: int
    shrinkage_weight: float


@dataclass(frozen=True)
class WeatherInput:
    """Derived from `weather.py`'s `WeatherReading` for a team's game. `wind_any_a_relative_drop`
    maps to `WeatherReading.wind_any_a_effect_pct`; `temperature_relative_drop` maps to
    `WeatherReading.temperature_effect_pct` -- both already damped 60% per ADR-0007 by
    `weather.py`. `is_indoor` maps to `WeatherReading.is_indoor`.

    `precipitation_relative_drop` is a KNOWN INGESTION GAP, not wired to `weather.py` today:
    ADR-0015 specifies a precipitation pp-drop magnitude (banded by rain/snow rate) and a
    baseline-completion-rate conversion (`pp / 64`) for the three-way combination, but
    `weather.py`'s `WeatherReading`/`parse_open_meteo_window` only pull Open-Meteo's combined
    `precipitation` field and surface a binary `has_precipitation` flag plus a raw
    `precipitation_in` total -- it does not pull the distinct `rain`/`snowfall` fields ADR-0015
    needs to classify rain-vs-snow, does not compute an hourly rate (only a 2-hour-window sum),
    and does not apply the ADR-0015 band table. Populating this field correctly requires a
    `weather.py` update, which is out of scope for this task (an ingestion module). Until then,
    pass `None` here: this module treats a `None` precipitation leg as neutral (multiplier 1.0)
    in the ADR-0015 log-space combination -- see `compute_weather_subscore`.
    """

    is_indoor: bool
    wind_any_a_relative_drop: float | None = None
    temperature_relative_drop: float | None = None
    precipitation_relative_drop: float | None = None


@dataclass(frozen=True)
class PlayerInjuryStatus:
    """One player's already-joined injury row for this team-week -- the minimal slice of
    `ingestion/rotogrinders_injuries.py`'s `InjuryReportEntry` this module needs, following the
    same pattern as `ImpliedTotalInput`/`PaceProeInput`/`WeatherInput` above (this module consumes
    a small typed slice of an upstream module's output, never that module's own dataclass
    directly). Callers assemble a `list[PlayerInjuryStatus]` per team via
    `normalization/injury_lookup.py`'s `team_injuries()` plus this file's own join.
    """

    status: str  # RotoGrinders' raw STATUS code, e.g. "O", "Q" -- see RESOLVED_INJURY_STATUSES
    impact_rating: int  # RotoGrinders' own 0-10 IMPACTRTG severity score


# --------------------------------------------------------------------------------------------
# Output shapes
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ComponentScore:
    """One sub-component's visible contribution to the composite. Kept separate from the final
    number (rather than folded silently into `composite_score`) so downstream consumers and QA
    can see the breakdown -- the same transparency pattern `nflverse.py`/`odds_api.py` already use
    for z-scores and shrinkage weights.
    """

    label: str
    weight_pct: float  # the weight actually applied this computation, out of 100 -- post any
    # redistribution (e.g. 50.0 for implied total on a dome game, not the base 44.4)
    z: float | None  # None for weather (not z-scored -- see compute_weather_subscore) or when
    # the underlying input is genuinely unavailable (implied total, ADR-0016 tier 3)
    points: float | None  # this component's contribution to the 0-100 composite; None exactly
    # when `z is None` and no contribution could be computed
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GameEnvironmentScore:
    """PRD Section 6's `GameEnvironmentScore`: a 0-100 composite per team per game, plus each
    sub-component's contribution and the (currently unwired -- see module docstring)
    injury/role uncertainty flag.
    """

    team: str
    season: int
    week: int
    is_available: bool  # False exactly when implied_total was unavailable (ADR-0016 tier 3) --
    # see the judgment-call comment in compute_game_environment_score
    composite_score: float | None  # 0-100; None iff not is_available
    implied_total: ComponentScore
    pace: ComponentScore
    proe: ComponentScore
    weather: ComponentScore
    # PRD Section 6: "a flag, not a score... doesn't blend into the composite numerically" --
    # "moderate"/"high_uncertainty", or None when no unresolved injury exists for this team this
    # week. Populated by compute_injury_uncertainty_flag() from RotoGrinders' Situation Room
    # injury report (see module docstring's "Injury/role uncertainty flag" section for the rollup
    # policy, flagged for Architect confirmation) when a caller passes team_injuries; stays None
    # (its original, pre-this-round default) if the caller passes nothing.
    injury_uncertainty_flag: str | None = None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------
# z-score -> points
# --------------------------------------------------------------------------------------------


def _phi(z: float) -> float:
    """Standard normal CDF -- maps an unbounded z-score to a (0, 1) favorability fraction.

    JUDGMENT CALL, flagged for Architect/Model Analytics Expert confirmation (see module
    docstring item 1): neither the PRD nor ADR-0003 specifies how a z-scored component becomes
    "points" on the 0-100 composite scale -- only the component *weights* are specified. The
    normal CDF is used here because (a) it's the standard statistical mapping from a z-score to a
    percentile/favorability fraction, consistent with this project's general statistical-rigor
    posture elsewhere (empirical-Bayes shrinkage, capped log-space combination) rather than an ad
    hoc linear clip; (b) z=0 ("league-average this week") lands at exactly half of that
    component's own weight, a clean and easily-explained midpoint; (c) it's smooth and bounded
    without an arbitrary hard saturation point. Alternative considered and rejected for now:
    linearly clip z to +-3 and rescale to [0, 1] -- rejected only because it asserts an arbitrary
    saturation point with no more justification than the CDF's own asymptotic approach. Either is
    defensible pending backtesting; this is a real formula-behavior decision, not a wiring detail.
    """
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# --------------------------------------------------------------------------------------------
# Weather sub-component (ADR-0002/0007/0015)
# --------------------------------------------------------------------------------------------


def _multiplier_from_relative_drop(relative_drop: float | None) -> float:
    """`1 - relative_drop`, or neutral (1.0) when that leg's data isn't available. `None` is
    treated as "no evidence of degradation on this leg" -- not "assume the worst" and not "assume
    a league-average penalty" -- the same "unavailable, never imputed" discipline ADR-0016
    established for implied-total data, applied here one level down to a single leg of the
    weather combination rather than to a whole component.
    """
    if relative_drop is None:
        return 1.0
    return 1.0 - relative_drop


def compute_weather_subscore(weather: WeatherInput, weight_pct: float) -> ComponentScore:
    """ADR-0015's three-way capped log-space combination:
    `weather_subscore = weight_pct * exp(clip(sum(ln(m_i)), ln(1 - cap), ln(1 + cap)))`, where
    each `m_i = 1 - relative_drop_i` for wind/temperature/precipitation. `weight_pct` is a
    parameter (rather than hardcoded to `WEIGHT_WEATHER * 100`) so this stays a pure, independently
    testable unit -- in practice it's always called with the base (non-redistributed) weather
    weight, since weather's own weight is never itself a redistribution target (only a source).

    Unlike the other three components, weather is not z-scored -- the PRD describes it as "a
    penalty function," one-sided by construction (no weather condition ever exceeds the neutral
    ceiling), so its point range is `[weight_pct * (1 - cap), weight_pct]`, not symmetric around
    `weight_pct / 2` the way the CDF-mapped components are. This asymmetry is intentional, per
    ADR-0015's own formula, not an oversight.
    """
    if weather.is_indoor:
        return ComponentScore(
            label="weather",
            weight_pct=0.0,
            z=None,
            points=None,
            notes=[
                "indoor/dome game -- weather sub-component inapplicable; weight redistributed to "
                "the other three components (PRD Section 6)"
            ],
        )

    notes: list[str] = []
    m_wind = _multiplier_from_relative_drop(weather.wind_any_a_relative_drop)
    m_temp = _multiplier_from_relative_drop(weather.temperature_relative_drop)
    if weather.precipitation_relative_drop is None:
        notes.append(
            "precipitation leg treated as neutral (multiplier 1.0): weather.py does not yet "
            "surface a precipitation pp-drop magnitude / rain-vs-snow rate classification per "
            "ADR-0015 -- see WeatherInput's docstring. Known ingestion gap, not guessed around."
        )
    m_precip = _multiplier_from_relative_drop(weather.precipitation_relative_drop)

    combined_log = math.log(m_wind) + math.log(m_temp) + math.log(m_precip)
    cap = math.log(1 + WEATHER_COMBINED_CAP)
    floor = math.log(1 - WEATHER_COMBINED_CAP)
    clipped_log = min(max(combined_log, floor), cap)
    if clipped_log != combined_log:
        notes.append(f"combined weather deviation capped at +-{WEATHER_COMBINED_CAP:.0%} (ADR-0015)")
    combined_multiplier = math.exp(clipped_log)

    return ComponentScore(
        label="weather",
        weight_pct=weight_pct,
        z=None,
        points=weight_pct * combined_multiplier,
        notes=notes,
    )


# --------------------------------------------------------------------------------------------
# Injury/role uncertainty flag (see module docstring's "Injury/role uncertainty flag" section for
# the full policy-judgment-call writeup -- flagged for Architect confirmation, not settled spec)
# --------------------------------------------------------------------------------------------

# Confirmed-resolved status codes only -- an allowlist, not a denylist of "unresolved" codes,
# since the full RotoGrinders status code set isn't confirmed live (see
# ingestion/rotogrinders_injuries.py's module docstring: only "O" and "Q" were observed in the one
# live pull checked). Anything not in this set -- "Q", an unobserved "D", or any future code --
# defaults to unresolved/uncertain rather than being silently treated as resolved.
#
# "P" (Probable) was checked for specifically, per Chris's flag, and deliberately NOT added here
# -- see ingestion/rotogrinders_injuries.py's module docstring for the full live investigation
# (no legend on the grid page, no site glossary, no historical data to check against; the page's
# own description text names exactly three tiers -- out/doubtful/questionable -- with no mention
# of probable, consistent with but not proof of RG never emitting "P"). Nothing confirms "P" is a
# real code this source uses, so there's nothing to add it for; re-verify once a later-in-the-week
# pull (more practice-report-driven statuses) is available, per that module's own note.
RESOLVED_INJURY_STATUSES: frozenset[str] = frozenset({"O"})

# Midpoint of RotoGrinders' own 0-10 IMPACTRTG scale -- the threshold between "moderate" and
# "high_uncertainty" below. A defensible split of RG's own scale, not backtested; open to
# recalibration (see module docstring).
HIGH_UNCERTAINTY_IMPACT_THRESHOLD = 5

# ADR-0017 Decision 3 (low-impact floor): below this, an unresolved status doesn't raise the
# flag at all -- a 0-1 IMPACTRTG plausibly represents a deep-bench/special-teams-only player
# whose uncertain status has no realistic bearing on the team's offensive/defensive output, and
# flagging every trivial questionable tag dilutes the signal for genuinely uncertain cases (a
# starter's questionable tag). Chosen the same way the existing `5` was -- a stated, defensible,
# round starting point on RG's 0-10 scale, not derived from a backtest.
LOW_IMPACT_FLOOR = 2


def compute_injury_uncertainty_flag(team_injuries: list[PlayerInjuryStatus]) -> str | None:
    """Roll up one team's joined injury rows to the flag PRD Section 6 describes: "moderate" /
    "high_uncertainty" when a starter's status is unresolved close to lock, `None` when it isn't.

    Policy (flagged for Architect confirmation -- see module docstring):
    - A player whose status is in `RESOLVED_INJURY_STATUSES` (currently just "O"/Out) contributes
      nothing to uncertainty -- that outcome is already known, not uncertain. It's a signal for
      the player-pool-exclusion step downstream, a different concern from this flag.
    - Any other status (e.g. "Q"/Questionable, or an unconfirmed future code) is unresolved and
      contributes to the flag.
    - If no unresolved player exists for this team this week, the flag is `None` -- not "low," to
      match `GameEnvironmentScore.injury_uncertainty_flag`'s existing `None`-means-unpopulated
      contract elsewhere in this module (a fully-resolved injury picture is a real, meaningful
      "nothing to flag," the same as the pre-this-round default).
    - Severity is graded off the *worst* (highest `IMPACTRTG`) unresolved player on the team, not
      averaged or summed across all of them -- one severely-in-doubt starter should read as
      "high_uncertainty" even if the rest of the injury report is trivial (a backup with a minor
      tweak shouldn't dilute a starting QB's real questionable tag).
    - ADR-0017 Decision 3 (low-impact floor): if the *worst* unresolved player's `IMPACTRTG` is
      below `LOW_IMPACT_FLOOR` (2), the flag is `None`, not "moderate" -- a technically-unresolved
      but functionally irrelevant deep-bench tag shouldn't read as team-level uncertainty. This is
      a three-tier rollup: `< 2` -> `None`, `2 <= worst < 5` -> "moderate", `>= 5` ->
      "high_uncertainty".
    """
    unresolved = [p for p in team_injuries if p.status not in RESOLVED_INJURY_STATUSES]
    if not unresolved:
        return None
    worst_impact = max(p.impact_rating for p in unresolved)
    if worst_impact < LOW_IMPACT_FLOOR:
        return None
    return "high_uncertainty" if worst_impact >= HIGH_UNCERTAINTY_IMPACT_THRESHOLD else "moderate"


# --------------------------------------------------------------------------------------------
# Weight redistribution (domed/indoor games -- PRD Section 6)
# --------------------------------------------------------------------------------------------


def _redistribute_weather_weight(
    weight_implied: float, weight_pace: float, weight_proe: float, weight_weather: float
) -> tuple[float, float, float]:
    """Redistribute `weight_weather` proportionally across the other three, per PRD Section 6
    ("redistributed to the other three for indoor/dome games"). Proportional, not an equal
    three-way split, so the relative balance among implied total/pace/PROE is preserved -- e.g.
    implied total is still worth exactly 2x pace after redistribution (0.5 vs. 0.25), matching the
    un-redistributed 44.4:22.2 ratio, rather than an arbitrary flat +3.7pp to each.
    """
    remaining = weight_implied + weight_pace + weight_proe
    factor = 1.0 + weight_weather / remaining
    return weight_implied * factor, weight_pace * factor, weight_proe * factor


# --------------------------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------------------------


def compute_game_environment_score(
    team: str,
    season: int,
    week: int,
    implied_total: ImpliedTotalInput,
    pace_proe: PaceProeInput,
    weather: WeatherInput,
    team_injuries: list[PlayerInjuryStatus] | None = None,
) -> GameEnvironmentScore:
    """Pure combination of the three ingestion modules' already-computed values into the final
    `GameEnvironmentScore` (PRD Section 6; Section 5 step 5). Does not fetch or recompute anything
    upstream -- see this module's docstring for the exact ingestion-output-to-input-field mapping.

    `team_injuries` is a new, optional param (default `None`, preserving every existing caller's
    behavior unchanged) -- assemble it via `normalization/injury_lookup.py`'s `team_injuries()`
    plus a small adapter to `PlayerInjuryStatus` (see that dataclass's docstring). `None`/`[]`
    both mean "no unresolved injury data for this team this week" -> `injury_uncertainty_flag`
    stays `None`, the same as this module's pre-this-round behavior.
    """
    notes: list[str] = []
    injury_uncertainty_flag = compute_injury_uncertainty_flag(team_injuries or [])

    weight_implied, weight_pace, weight_proe = WEIGHT_IMPLIED_TOTAL, WEIGHT_PACE, WEIGHT_PROE
    if weather.is_indoor:
        weight_implied, weight_pace, weight_proe = _redistribute_weather_weight(
            WEIGHT_IMPLIED_TOTAL, WEIGHT_PACE, WEIGHT_PROE, WEIGHT_WEATHER
        )

    weather_component = compute_weather_subscore(weather, WEIGHT_WEATHER * 100)

    pace_points = weight_pace * 100 * _phi(pace_proe.pace_z)
    shrinkage_note = (
        f"weeks_played={pace_proe.weeks_played}, shrinkage_weight={pace_proe.shrinkage_weight:.3f} "
        "(ADR-0011 n/(n+6) blend toward the 2024/2025 prior-season baseline, already applied "
        "upstream in nflverse.py -- this module consumes the resulting z-score as-is)"
    )
    pace_component = ComponentScore(
        label="pace",
        weight_pct=weight_pace * 100,
        z=pace_proe.pace_z,
        points=pace_points,
        notes=[shrinkage_note],
    )
    proe_points = weight_proe * 100 * _phi(pace_proe.proe_z)
    proe_component = ComponentScore(
        label="proe",
        weight_pct=weight_proe * 100,
        z=pace_proe.proe_z,
        points=proe_points,
        notes=[shrinkage_note],
    )

    if weather.is_indoor:
        notes.append(
            "indoor/dome game: weather's 11.1pt weight redistributed to implied total/pace/PROE "
            f"({weight_implied * 100:.1f}/{weight_pace * 100:.1f}/{weight_proe * 100:.1f} of 100)"
        )

    if implied_total.z is None:
        # JUDGMENT CALL -- flagged for Architect confirmation, not settled spec (see module
        # docstring item 2). ADR-0016 specifies what happens to the *z-score population* when a
        # team's implied total is missing after the live-pull + cached-pre-kickoff-line fallback,
        # but not what GameEnvironmentScore itself should do with that gap. Two options:
        #   (a) [chosen here] mark the whole composite unavailable for this team-week.
        #   (b) redistribute implied total's 44.4% weight to pace/PROE/weather, the same way a
        #       domed stadium's weather weight is redistributed above.
        # (a) was chosen because implied total is the single largest weight (44.4%, 2x-4x any
        # other component) and, per ADR-0003, is normally available unconditionally from week 1
        # onward with no early-season fallback at all -- unlike a dome (a known, benign,
        # fully-expected condition for that specific stadium every week of the season), a missing
        # implied total after ADR-0016's own caching fallback signals a real pipeline gap, not an
        # expected structural absence. Silently reweighting around it would let a materially
        # degraded score -- built almost entirely from pace/PROE, the two components ADR-0003
        # itself flagged as at risk of correlating with implied total anyway (the pending
        # correlation-check prerequisite) -- look identical in shape to a normal, fully-available
        # score to downstream consumers, notably StackProfile's `min()` bottleneck logic across
        # two teams' scores, with no visible signal that its most heavily-weighted input was
        # missing. An explicit `is_available=False` is the more honest failure mode here,
        # consistent with ADR-0016's own "flag unavailable, never impute" discipline for the
        # underlying data one level down. Revisit if the Architect prefers graceful degradation
        # (b) instead -- `_redistribute_weather_weight` is generic enough to reuse for that case.
        implied_component = ComponentScore(
            label="implied_total",
            weight_pct=weight_implied * 100,
            z=None,
            points=None,
            notes=[
                "implied total unavailable (ADR-0016 tier 3: no live line, no cached pre-kickoff "
                "value) -- composite marked unavailable rather than degraded; see "
                "compute_game_environment_score's judgment-call comment"
            ],
        )
        notes.append(
            "GameEnvironmentScore unavailable: implied-team-total input missing for this "
            "team-week."
        )
        return GameEnvironmentScore(
            team=team,
            season=season,
            week=week,
            is_available=False,
            composite_score=None,
            implied_total=implied_component,
            pace=pace_component,
            proe=proe_component,
            weather=weather_component,
            injury_uncertainty_flag=injury_uncertainty_flag,
            notes=notes,
        )

    implied_points = weight_implied * 100 * _phi(implied_total.z)
    implied_component = ComponentScore(
        label="implied_total",
        weight_pct=weight_implied * 100,
        z=implied_total.z,
        points=implied_points,
        notes=[],
    )

    composite = implied_points + pace_points + proe_points + (weather_component.points or 0.0)

    return GameEnvironmentScore(
        team=team,
        season=season,
        week=week,
        is_available=True,
        composite_score=composite,
        implied_total=implied_component,
        pace=pace_component,
        proe=proe_component,
        weather=weather_component,
        injury_uncertainty_flag=injury_uncertainty_flag,
        notes=notes,
    )
