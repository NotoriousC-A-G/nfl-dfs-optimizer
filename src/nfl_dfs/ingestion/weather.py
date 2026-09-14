"""Weather ingestion for `GameEnvironmentScore`'s weather-impact component (PRD Section 6,
11.1% weight, outdoor games only; wind/temperature curve shape ADR-0002; 60% magnitude damping
ADR-0007). Open-Meteo (primary, keyless) + NWS (secondary storm cross-check, keyless, US-only).

## Stadium reference data

`stadiums.py` (new this pass -- nothing like it existed in the codebase before) supplies each
team's home stadium lat/lon and roof type. See that module's own docstring for sourcing (live
Wikipedia infobox pulls, 2026-09-13) and the stated closed-by-default assumption for retractable
roofs.

## Kickoff time source: DraftKings' schedule, not the Odds API -- decided live, not assumed

The task asked which of the Odds API's `commence_time` or DK's schedule data is more reliable for
kickoff time. Checked live: DK's `draftables` payload (`competition.startTime`, e.g.
`"2026-09-13T20:25:00.0000000Z"`) carries the same UTC ISO8601 precision as the Odds API's
`commence_time`, but attaches directly to DK's own canonical team abbreviations (`competition.name
== "GB @ MIN"`) with **no name-matching step needed** -- whereas the Odds API returns full team
names requiring `odds_api.py`'s `TEAM_NAME_TO_ABBR` table, and (per that module's own live
finding) drops a game from its listing entirely once that game has kicked off, which DK's slate
data does not do the same way. Since this module's whole point is per-game kickoff time for the
*actual DK slate* being built against, DK's own schedule data is the more reliable and more
directly relevant source -- used here as primary. `parse_dk_game_schedule` is a small, independent
parse of the same public `draftables` endpoint `ingestion/draftkings.py` already uses (not an
import from or edit to that module -- it's out of scope for this task -- just an independent read
of `competition`/`competitionId`/`startTime`, fields `draftkings.py`'s own `parse_draftables`
doesn't currently extract).

## What this module computes, and what it deliberately does not

Per PRD Section 6's weather sub-component text, ADR-0007's damping table, and ADR-0015 (the
precipitation-magnitude / three-way-combination / wind-curve-shape completion pass), this module
computes, per game: the damped wind effect (three-point piecewise-linear, negligible <10mph /
steep >=20mph) on QB ANY/A and on FG-conversion rate; the damped temperature effect (three-band
step function: neutral 55-85F, mid cold-dip 25-55F, extreme dip <25F or >85F); and, as of this
pass, the damped precipitation effect (rain/snow classified separately by hourly rate into
light/moderate/heavy bands per ADR-0015 section 1, expressed as a relative completion-rate drop
via the 64%-baseline conversion ADR-0015 section 2 specifies for the three-way combination).

**Precipitation, closing the gap flagged in ADR-0015's handoff note:** Open-Meteo's hourly
response separates `precipitation` (combined liquid-equivalent, already pulled), `rain` (liquid
rain only), and `snowfall` (snow depth) -- this module now requests all three and rate-thresholds
`rain`/`snowfall` directly (see `precipitation_effect` below), rather than only surfacing the
binary `has_precipitation` flag / raw `precipitation_in` total the prior pass left in place (those
two fields are kept, unchanged, for backward-compatible raw-data transparency).

Still **not** computed here: the final composite `GameEnvironmentScore` weather sub-component score
(the 0-100-scale contribution, combined with implied total/pace/PROE). That's the actual formula
implementation (`game_environment/score.py`'s `compute_weather_subscore`, Section 5 step 5) --
this module's job stops at damped per-effect relative drops and raw pulled fields, which
`WeatherReading.precipitation_relative_drop` (this pass's new field) is shaped to plug directly
into `WeatherInput.precipitation_relative_drop` once that adapter is written.

## Wind curve shape between the two cited anchor points: ADR-0015 section 3, three-point piecewise-linear

ADR-0002/ADR-0007 gave two anchors (negligible below ~10mph; the full damped effect at 20mph+) and
said the curve "accelerates rather than stepping once," but specified no numeric shape for the
10-20mph range. An earlier pass filled that gap with a quadratic ramp as its own unreviewed
engineering judgment call -- flagged in this module's own prior docstring as not sourced from
either ADR. ADR-0015 section 3 replaced that with reviewed spec: a derived 15mph anchor (from
ADR-0002's own stated "15->20mph step is ~1.5-2x steeper than 10->15mph" ratio, using the
range's 1.75x midpoint to split the total drop into two segments), then straight-line
interpolation across the three points (10, 15, 20mph) instead of a curve-fit across two. This
module now implements that three-point piecewise-linear shape (`_piecewise_linear_wind_drop`),
not the quadratic.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta

from nfl_dfs.ingestion.stadiums import get_stadium

DK_DRAFTABLES_URL = "https://api.draftkings.com/draftgroups/v1/draftgroups/{draft_group_id}/draftables"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
NWS_POINTS_URL = "https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
NWS_USER_AGENT = "nfl-dfs-optimizer (contact: chris.gasparro@gmail.com)"
_TIMEOUT = 20.0

# --- Damped effect constants (ADR-0007's 60%-of-cited-magnitude table) ---
WIND_NEGLIGIBLE_MPH = 10.0
WIND_MID_MPH = 15.0  # ADR-0015 section 3's derived interior anchor
WIND_STEEP_MPH = 20.0
WIND_ANYA_MAX_RELATIVE_DROP = 0.12  # ~12% relative ANY/A drop at 20mph+, damped from ~20% (5.79 -> 4.62)
WIND_FG_MAX_DROP = 0.036  # ~3.6% FG-conversion drop at 20mph+, damped from ~6%

# ADR-0015 section 3: the 15mph anchor is derived by splitting the total 10->20mph drop into two
# segments, D1 (10->15) and D2 (15->20), using D2 = 1.75 * D1 -- the midpoint of ADR-0002's own
# stated "15->20mph step is ~1.5-2x steeper than 10->15mph" range. Since damping (ADR-0007) is a
# scalar multiply, it commutes with this ratio split: the damped drop at 15mph is just the damped
# *total* drop divided by (1 + ratio), with no need to separately track raw/damped figures here.
WIND_MID_SEGMENT_RATIO = 1.75


def _piecewise_linear_wind_drop(wind_mph: float, max_drop: float) -> float:
    """Three-point piecewise-linear interpolation (10, 15, 20mph) per ADR-0015 section 3,
    replacing the earlier quadratic. `max_drop` is the already-damped magnitude at 20mph+ (e.g.
    `WIND_ANYA_MAX_RELATIVE_DROP`); the 15mph anchor is `max_drop / (1 + WIND_MID_SEGMENT_RATIO)`
    (the D1 segment), reproducing the ADR's stated accelerating shape exactly -- the second
    segment's slope is, by construction, `WIND_MID_SEGMENT_RATIO`x the first's."""
    if wind_mph < WIND_NEGLIGIBLE_MPH:
        return 0.0
    if wind_mph >= WIND_STEEP_MPH:
        return max_drop
    mid_drop = max_drop / (1 + WIND_MID_SEGMENT_RATIO)
    if wind_mph <= WIND_MID_MPH:
        fraction = (wind_mph - WIND_NEGLIGIBLE_MPH) / (WIND_MID_MPH - WIND_NEGLIGIBLE_MPH)
        return mid_drop * fraction
    fraction = (wind_mph - WIND_MID_MPH) / (WIND_STEEP_MPH - WIND_MID_MPH)
    return mid_drop + (max_drop - mid_drop) * fraction


TEMP_NEUTRAL_LOW_F = 55.0
TEMP_NEUTRAL_HIGH_F = 85.0
TEMP_EXTREME_LOW_F = 25.0
TEMP_MID_COLD_DROP = 0.03  # ~3% dip, 25-55F, damped from ~5%
TEMP_EXTREME_DROP = 0.048  # ~4.8% (~5%) dip, <25F or >85F, damped from ~8%

_STORM_KEYWORDS = ("thunderstorm", "severe", "tornado", "hail", "blizzard")


def wind_effect(wind_mph: float) -> tuple[float, float]:
    """`(any_a_relative_drop, fg_conversion_drop)`, both non-negative magnitudes (0.0 = no
    effect). Three-point piecewise-linear shape between 10-20mph, per ADR-0015 section 3 -- see
    `_piecewise_linear_wind_drop` and the module docstring."""
    any_a = _piecewise_linear_wind_drop(wind_mph, WIND_ANYA_MAX_RELATIVE_DROP)
    fg = _piecewise_linear_wind_drop(wind_mph, WIND_FG_MAX_DROP)
    return any_a, fg


# --- Precipitation bands and damped effect constants (ADR-0015 section 1) ---
# Rate thresholds per standard NWS-style meteorological convention (band *boundaries*); the pp-drop
# magnitudes at each band come from the DFS/football research ADR-0015 cites (band *magnitudes*).
RAIN_LIGHT_MAX_IN_HR = 0.10  # light: <0.10in/hr
RAIN_MODERATE_MAX_IN_HR = 0.30  # moderate: 0.10-0.30in/hr; heavy: >0.30in/hr
SNOW_LIGHT_MAX_IN_HR = 1.0  # light: <1in/hr
SNOW_MODERATE_MAX_IN_HR = 2.0  # moderate: 1-2in/hr; heavy: >2in/hr

# Damped (60%, ADR-0007 convention applied per ADR-0015 section 1) completion-percentage-point
# (pp) drop, by band. Full-strength/source figures are in ADR-0015's Decision 1 table; only the
# already-damped magnitudes are stored here, consistent with the wind/temperature constants above.
RAIN_LIGHT_PP_DROP = 1.4  # -2.3pp cited (PFF) x 60%
RAIN_MODERATE_PP_DROP = 2.0  # -3.4pp cited (PFF) x 60%
RAIN_HEAVY_PP_DROP = 3.0  # -5.0pp Architect-extrapolated x 60%
SNOW_LIGHT_PP_DROP = 1.8  # -3pp Architect-extrapolated x 60%
SNOW_MODERATE_PP_DROP = 4.2  # -7pp cited (The Fantasy Footballers) x 60%
SNOW_HEAVY_PP_DROP = 7.2  # -12pp Architect-extrapolated x 60%

# ADR-0015 section 2: baseline completion rate used to convert a pp drop into a relative fraction
# for the three-way (wind/temp/precip) log-space combination -- for this module's own purposes
# only, matching what compute_weather_subscore consumes as WeatherInput.precipitation_relative_drop.
PRECIP_BASELINE_COMPLETION_PCT = 64.0


def precipitation_effect(rain_in_hr: float, snowfall_in_hr: float) -> tuple[float, str]:
    """`(relative_completion_drop, band_label)`. Classifies rain and snow independently against
    ADR-0015's rate bands (different mechanisms, different magnitudes -- not a shared curve),
    then converts the resulting damped pp drop to a relative fraction via
    `PRECIP_BASELINE_COMPLETION_PCT`. `band_label` is one of `"none"`, `"light_rain"`,
    `"moderate_rain"`, `"heavy_rain"`, `"light_snow"`, `"moderate_snow"`, `"heavy_snow"`.

    **Mixed rain+snow, an edge case ADR-0015 doesn't address** (it specifies rain and snow as two
    separate curves, not a combined one): when both rates are simultaneously positive, this
    function takes whichever leg produces the larger pp drop as the representative effect, rather
    than adding them -- this module's own engineering judgment call for that specific case (no
    source establishes the two mechanisms stack additively), flagged the same way the wind-curve
    interior shape was before ADR-0015 confirmed it.
    """

    def _rain_band(rate: float) -> tuple[float, str]:
        if rate <= 0:
            return 0.0, "none"
        if rate < RAIN_LIGHT_MAX_IN_HR:
            return RAIN_LIGHT_PP_DROP, "light_rain"
        if rate <= RAIN_MODERATE_MAX_IN_HR:
            return RAIN_MODERATE_PP_DROP, "moderate_rain"
        return RAIN_HEAVY_PP_DROP, "heavy_rain"

    def _snow_band(rate: float) -> tuple[float, str]:
        if rate <= 0:
            return 0.0, "none"
        if rate < SNOW_LIGHT_MAX_IN_HR:
            return SNOW_LIGHT_PP_DROP, "light_snow"
        if rate <= SNOW_MODERATE_MAX_IN_HR:
            return SNOW_MODERATE_PP_DROP, "moderate_snow"
        return SNOW_HEAVY_PP_DROP, "heavy_snow"

    rain_pp, rain_band = _rain_band(rain_in_hr)
    snow_pp, snow_band = _snow_band(snowfall_in_hr)
    pp, band = (snow_pp, snow_band) if snow_pp >= rain_pp else (rain_pp, rain_band)
    return pp / PRECIP_BASELINE_COMPLETION_PCT, band


def temperature_effect(temp_f: float) -> float:
    """Non-negative passing-production dip magnitude (0.0 = neutral). Step bands, not a curve --
    ADR-0002/ADR-0007 specify these as flat bands with no interpolation needed."""
    if TEMP_NEUTRAL_LOW_F <= temp_f <= TEMP_NEUTRAL_HIGH_F:
        return 0.0
    if TEMP_EXTREME_LOW_F <= temp_f < TEMP_NEUTRAL_LOW_F:
        return TEMP_MID_COLD_DROP
    return TEMP_EXTREME_DROP  # temp_f < 25 or temp_f > 85


def _parse_iso8601_utc(ts: str) -> datetime:
    """Handles both the Odds API's plain form (`"...T17:03:55Z"`) and DK's 7-digit-fraction form
    (`"...T20:25:00.0000000Z"`) -- `datetime.fromisoformat` doesn't accept the latter directly."""
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    if "." in ts:
        date_part, rest = ts.split(".", 1)
        for i, ch in enumerate(rest):
            if ch in "+-":
                frac, tz = rest[:i], rest[i:]
                break
        else:
            frac, tz = rest, ""
        ts = f"{date_part}.{(frac + '000000')[:6]}{tz}"
    return datetime.fromisoformat(ts)


@dataclass(frozen=True)
class GameSchedule:
    home_team: str
    away_team: str
    kickoff_utc: str


def parse_dk_game_schedule(draftables_payload: dict) -> list[GameSchedule]:
    """Pure parse of a DK `draftables` payload's `competition` field into one `GameSchedule` per
    distinct `competitionId`. Live-confirmed shape (2026 wk1, draftGroupId 153070):
    `competition.name` is `"<AWAY> @ <HOME>"` (e.g. `"GB @ MIN"`) using DK's own canonical team
    abbreviations directly, and `competition.startTime` is UTC ISO8601."""
    seen: dict[int, GameSchedule] = {}
    for d in draftables_payload.get("draftables", []):
        comp = d.get("competition") or {}
        comp_id, name, start = comp.get("competitionId"), comp.get("name"), comp.get("startTime")
        if comp_id is None or not name or not start or comp_id in seen:
            continue
        if " @ " not in name:
            warnings.warn(f"unexpected DK competition name format: {name!r} -- skipping", stacklevel=2)
            continue
        away, home = name.split(" @ ", 1)
        seen[comp_id] = GameSchedule(home_team=home.strip(), away_team=away.strip(), kickoff_utc=start)
    return list(seen.values())


def _parse_wind_speed_mph(text: str | None) -> float | None:
    """NWS `windSpeed` is a free-text string, e.g. `"12 mph"` or a range `"10 to 15 mph"` --
    parsed here as the mean of every number found (a single number for the common case)."""
    if not text:
        return None
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


@dataclass(frozen=True)
class WeatherWindow:
    """Aggregated reading over the 2-hour kickoff window, from one source."""

    temperature_f: float | None
    wind_mph: float | None
    precipitation_in: float | None  # total over the window; Open-Meteo only (NWS has no field)
    rain_rate_in_hr: float | None  # ADR-0015: peak single-hour rain rate within the window,
    # Open-Meteo `rain` only. NOT summed like precipitation_in -- ADR-0015's bands classify by
    # rate (in/hr), and Open-Meteo's hourly bucket size (1hr) already makes each entry a de facto
    # in/hr rate; the window's *peak* hour (not the average) is used as the representative
    # intensity, matching this project's established "grade off the worst, not the average"
    # pattern (ADR-0017's injury-uncertainty rollup) rather than diluting one severe hour with a
    # calm one.
    snowfall_rate_in_hr: float | None  # same construction, Open-Meteo `snowfall` only -- snow
    # depth (inches of snow, not liquid-equivalent), matching ADR-0015's snow band units directly.
    precipitation_probability_pct: float | None  # NWS only
    short_forecast: str | None  # NWS only, free text
    storm_keyword_flag: bool | None  # NWS only -- shortForecast matched a storm keyword


def _select_hourly_indices(times: list[str], kickoff: datetime, window_hours: int = 2) -> list[int]:
    start = kickoff.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=window_hours)
    indices = []
    for i, t in enumerate(times):
        ts = datetime.fromisoformat(t)
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        if start <= ts < end:
            indices.append(i)
    return indices


def _hourly_values(hourly: dict, key: str, idx: list[int]) -> list[float]:
    """Values at `idx` for `hourly[key]`, skipping `None`s and tolerating a missing key entirely
    (returns `[]`) -- keeps this pure-parse function backward-compatible with older/smaller
    Open-Meteo payloads (e.g. this module's own pre-ADR-0015 test fixtures) that don't carry the
    `rain`/`snowfall` fields at all, rather than raising a `KeyError`."""
    values = hourly.get(key, [])
    return [values[i] for i in idx if i < len(values) and values[i] is not None]


def parse_open_meteo_window(payload: dict, kickoff_utc: datetime) -> WeatherWindow:
    """Pure parse: Open-Meteo's `hourly` block (requested with `timezone=UTC`, so its `time`
    strings are naive-but-UTC) -> the 2-hour window starting at kickoff. Temperature/wind are
    averaged over the window; `precipitation_in` is summed (cumulative rainfall across the
    window, not an instantaneous rate) -- kept as-is for backward-compatible raw-total exposure.
    `rain_rate_in_hr`/`snowfall_rate_in_hr` are the window's *peak* hourly value (see
    `WeatherWindow`'s docstring for why peak, not average/sum)."""
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    idx = _select_hourly_indices(times, kickoff_utc)
    if not idx:
        raise ValueError(
            f"no Open-Meteo hourly entries fall within the 2-hour window starting at "
            f"{kickoff_utc.isoformat()} -- forecast may not extend far enough ahead"
        )
    temps = _hourly_values(hourly, "temperature_2m", idx)
    winds = _hourly_values(hourly, "wind_speed_10m", idx)
    precips = _hourly_values(hourly, "precipitation", idx)
    rains = _hourly_values(hourly, "rain", idx)
    snows = _hourly_values(hourly, "snowfall", idx)
    return WeatherWindow(
        temperature_f=sum(temps) / len(temps) if temps else None,
        wind_mph=sum(winds) / len(winds) if winds else None,
        precipitation_in=sum(precips) if precips else None,
        rain_rate_in_hr=max(rains) if rains else None,
        snowfall_rate_in_hr=max(snows) if snows else None,
        precipitation_probability_pct=None,
        short_forecast=None,
        storm_keyword_flag=None,
    )


def parse_nws_hourly_window(periods: list[dict], kickoff_utc: datetime, window_hours: int = 2) -> WeatherWindow:
    """Pure parse of an `api.weather.gov` `.../forecast/hourly` response's `periods` list -> the
    2-hour kickoff window. `startTime` on each period carries its own UTC offset (e.g.
    `"...T14:00:00-05:00"`), converted to UTC before window matching."""
    start = kickoff_utc.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=window_hours)
    matched = []
    for p in periods:
        # NWS times carry an explicit UTC offset (e.g. "...T14:00:00-05:00"); convert to naive UTC.
        raw = datetime.fromisoformat(p["startTime"])
        ts = (raw - raw.utcoffset()).replace(tzinfo=None) if raw.utcoffset() else raw.replace(tzinfo=None)
        if start <= ts < end:
            matched.append(p)
    if not matched:
        raise ValueError(f"no NWS hourly periods fall within the 2-hour window starting at {kickoff_utc.isoformat()}")
    temps = [p["temperature"] for p in matched if p.get("temperature") is not None]
    winds = [_parse_wind_speed_mph(p.get("windSpeed")) for p in matched]
    winds = [w for w in winds if w is not None]
    precip_probs = [
        p["probabilityOfPrecipitation"]["value"]
        for p in matched
        if p.get("probabilityOfPrecipitation", {}).get("value") is not None
    ]
    forecasts = [p.get("shortForecast", "") for p in matched if p.get("shortForecast")]
    combined_forecast = "; ".join(dict.fromkeys(forecasts)) if forecasts else None
    storm_flag = any(
        kw in (f or "").lower() for f in forecasts for kw in _STORM_KEYWORDS
    )
    return WeatherWindow(
        temperature_f=sum(temps) / len(temps) if temps else None,
        wind_mph=sum(winds) / len(winds) if winds else None,
        precipitation_in=None,
        rain_rate_in_hr=None,  # NWS has no distinct rain/snowfall-rate field
        snowfall_rate_in_hr=None,
        precipitation_probability_pct=max(precip_probs) if precip_probs else None,
        short_forecast=combined_forecast,
        storm_keyword_flag=storm_flag if forecasts else None,
    )


@dataclass(frozen=True)
class WeatherReading:
    team: str  # home team -- the stadium/field this reading applies to; same reading for the visitor
    is_indoor: bool
    kickoff_utc: str | None
    temperature_f: float | None
    wind_mph: float | None
    precipitation_in: float | None
    has_precipitation: bool | None
    wind_any_a_effect_pct: float | None
    wind_fg_effect_pct: float | None
    temperature_effect_pct: float | None
    rain_rate_in_hr: float | None  # raw peak-hour rate within the window, ADR-0015 -- see
    # WeatherWindow's docstring; None when Open-Meteo's `rain` field wasn't present/covered.
    snowfall_rate_in_hr: float | None  # same, for `snowfall`
    precipitation_band: str | None  # `precipitation_effect`'s band label ("none"/"light_rain"/
    # "heavy_snow"/etc), for transparency -- None only when neither rain_rate_in_hr nor
    # snowfall_rate_in_hr was available (leg genuinely uncomputed, not "zero effect").
    precipitation_relative_drop: float | None  # ADR-0015's damped, baseline-converted relative
    # completion-rate drop -- shaped to plug directly into
    # `game_environment/score.py`'s `WeatherInput.precipitation_relative_drop` once that adapter
    # is written. None has the same "leg genuinely unavailable, not zero" meaning as above.
    nws_precipitation_probability_pct: float | None
    nws_short_forecast: str | None
    nws_storm_flag: bool | None
    source_notes: str


def neutral_reading(team: str, kickoff_utc: str | None, *, reason: str) -> WeatherReading:
    """Indoor short-circuit -- no API call. See `stadiums.py` for the dome/retractable-roof
    classification and the stated closed-by-default assumption on retractable roofs."""
    return WeatherReading(
        team=team,
        is_indoor=True,
        kickoff_utc=kickoff_utc,
        temperature_f=None,
        wind_mph=None,
        precipitation_in=None,
        has_precipitation=None,
        wind_any_a_effect_pct=0.0,
        wind_fg_effect_pct=0.0,
        temperature_effect_pct=0.0,
        rain_rate_in_hr=None,
        snowfall_rate_in_hr=None,
        precipitation_band=None,
        precipitation_relative_drop=0.0,
        nws_precipitation_probability_pct=None,
        nws_short_forecast=None,
        nws_storm_flag=None,
        source_notes=reason,
    )


def build_weather_reading(
    team: str,
    kickoff_utc: str,
    open_meteo_window: WeatherWindow,
    nws_window: WeatherWindow | None,
) -> WeatherReading:
    """Combines a parsed Open-Meteo window (primary) with an optional NWS window (secondary
    cross-check) into the final `WeatherReading` -- applies `wind_effect`/`temperature_effect`/
    `precipitation_effect`, and surfaces the raw precipitation flag/total alongside the ADR-0015
    banded relative drop. NWS's `storm_flag` is exposed as its own field, not merged into
    `has_precipitation`, since it's specifically the "Open-Meteo under-reads thunderstorm
    probability" cross-check Phase 0 flagged -- a caller should treat `nws_storm_flag=True` with
    `has_precipitation=False` as a real discrepancy worth surfacing, not resolve it silently in
    this module.

    Precipitation: when Open-Meteo's window has neither a rain nor a snowfall rate (both `None`
    -- the field(s) weren't in the payload or didn't cover the window), the relative drop and
    band are both left `None` ("genuinely uncomputed," matching `score.py`'s "unavailable, never
    imputed" discipline) rather than assumed zero. When at least one of the two is present, the
    other missing one is treated as 0 (Open-Meteo returns both fields together in practice; this
    only matters for a partial/malformed payload)."""
    any_a, fg = (0.0, 0.0) if open_meteo_window.wind_mph is None else wind_effect(open_meteo_window.wind_mph)
    temp_effect = None if open_meteo_window.temperature_f is None else temperature_effect(open_meteo_window.temperature_f)
    has_precip = None if open_meteo_window.precipitation_in is None else open_meteo_window.precipitation_in > 0

    rain_rate = open_meteo_window.rain_rate_in_hr
    snow_rate = open_meteo_window.snowfall_rate_in_hr
    if rain_rate is None and snow_rate is None:
        precip_relative_drop, precip_band = None, None
    else:
        precip_relative_drop, precip_band = precipitation_effect(rain_rate or 0.0, snow_rate or 0.0)

    return WeatherReading(
        team=team,
        is_indoor=False,
        kickoff_utc=kickoff_utc,
        temperature_f=open_meteo_window.temperature_f,
        wind_mph=open_meteo_window.wind_mph,
        precipitation_in=open_meteo_window.precipitation_in,
        has_precipitation=has_precip,
        wind_any_a_effect_pct=any_a,
        wind_fg_effect_pct=fg,
        temperature_effect_pct=temp_effect,
        rain_rate_in_hr=rain_rate,
        snowfall_rate_in_hr=snow_rate,
        precipitation_band=precip_band,
        precipitation_relative_drop=precip_relative_drop,
        nws_precipitation_probability_pct=nws_window.precipitation_probability_pct if nws_window else None,
        nws_short_forecast=nws_window.short_forecast if nws_window else None,
        nws_storm_flag=nws_window.storm_keyword_flag if nws_window else None,
        source_notes="Open-Meteo primary" + (", NWS cross-check" if nws_window else ", NWS unavailable"),
    )


def fetch_dk_game_schedule(draft_group_id: int, *, session=None) -> list[GameSchedule]:
    import requests

    http = session or requests
    payload = http.get(DK_DRAFTABLES_URL.format(draft_group_id=draft_group_id), timeout=_TIMEOUT).json()
    return parse_dk_game_schedule(payload)


def fetch_open_meteo_window(latitude: float, longitude: float, kickoff_utc: datetime, *, session=None) -> WeatherWindow:
    import requests

    http = session or requests
    response = http.get(
        OPEN_METEO_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m,wind_speed_10m,precipitation,rain,snowfall",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "UTC",
            "forecast_days": 16,
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return parse_open_meteo_window(response.json(), kickoff_utc)


def fetch_nws_window(latitude: float, longitude: float, kickoff_utc: datetime, *, session=None) -> WeatherWindow | None:
    """Two-call NWS flow (`/points/{lat},{lon}` -> the gridpoint's own `forecastHourly` URL),
    per Phase 0's note that NWS requires a descriptive `User-Agent`. Returns `None` (rather than
    raising) on any failure -- NWS is the secondary cross-check per PRD Section 4/6, so a pull
    that only has Open-Meteo should still proceed, just without the storm cross-check, with that
    absence visible in `WeatherReading.source_notes`."""
    import requests

    http = session or requests
    headers = {"User-Agent": NWS_USER_AGENT}
    try:
        points = http.get(NWS_POINTS_URL.format(lat=latitude, lon=longitude), headers=headers, timeout=_TIMEOUT)
        points.raise_for_status()
        hourly_url = points.json()["properties"]["forecastHourly"]
        hourly = http.get(hourly_url, headers=headers, timeout=_TIMEOUT)
        hourly.raise_for_status()
        periods = hourly.json()["properties"]["periods"]
        return parse_nws_hourly_window(periods, kickoff_utc)
    except Exception as exc:  # noqa: BLE001 -- secondary source, degrade gracefully
        warnings.warn(f"NWS cross-check unavailable ({exc!r}) -- proceeding with Open-Meteo only", stacklevel=2)
        return None


def fetch_weather_reading(team: str, kickoff_utc_str: str, *, session=None) -> WeatherReading:
    """Full pipeline for one game, keyed by the home team (whose stadium the game is played at --
    the same reading applies to the visiting team too). Short-circuits to `neutral_reading` for a
    dome or retractable-roof stadium (see `stadiums.py`) with no API call at all."""
    stadium = get_stadium(team)
    if stadium.is_indoor:
        return neutral_reading(team, kickoff_utc_str, reason=f"indoor ({stadium.roof_type}) -- no API call")

    kickoff = _parse_iso8601_utc(kickoff_utc_str).replace(tzinfo=None)
    open_meteo = fetch_open_meteo_window(stadium.latitude, stadium.longitude, kickoff, session=session)
    nws = fetch_nws_window(stadium.latitude, stadium.longitude, kickoff, session=session)
    return build_weather_reading(team, kickoff_utc_str, open_meteo, nws)
