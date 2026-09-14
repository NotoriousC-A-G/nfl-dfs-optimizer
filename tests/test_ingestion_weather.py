import json
import math
from datetime import datetime
from pathlib import Path

import pytest

from nfl_dfs.game_environment.score import WeatherInput, compute_weather_subscore
from nfl_dfs.ingestion.weather import (
    WIND_ANYA_MAX_RELATIVE_DROP,
    WIND_FG_MAX_DROP,
    WIND_MID_SEGMENT_RATIO,
    WeatherWindow,
    build_weather_reading,
    fetch_weather_reading,
    neutral_reading,
    parse_dk_game_schedule,
    parse_nws_hourly_window,
    parse_open_meteo_window,
    precipitation_effect,
    temperature_effect,
    wind_effect,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_wind_effect_below_negligible_threshold_is_zero():
    assert wind_effect(5.0) == (0.0, 0.0)
    assert wind_effect(9.9) == (0.0, 0.0)


def test_wind_effect_at_and_above_steep_threshold_is_full_damped_magnitude():
    any_a, fg = wind_effect(20.0)
    assert any_a == pytest.approx(0.12)
    assert fg == pytest.approx(0.036)
    # Above 20mph stays capped at the same figure, not extrapolated further.
    assert wind_effect(35.0) == pytest.approx((0.12, 0.036))


def test_wind_effect_interpolates_monotonically_between_anchors():
    lo, _ = wind_effect(12.0)
    mid, _ = wind_effect(15.0)
    hi, _ = wind_effect(18.0)
    assert 0.0 < lo < mid < hi < 0.12


def test_wind_effect_15mph_anchor_matches_adr0015_derived_midpoint():
    # ADR-0015 section 3: the 15mph anchor is max_drop / (1 + 1.75) -- not the quadratic curve's
    # arbitrary midpoint value. Exact for ANY/A and FG-conversion both.
    any_a, fg = wind_effect(15.0)
    assert any_a == pytest.approx(WIND_ANYA_MAX_RELATIVE_DROP / (1 + WIND_MID_SEGMENT_RATIO))
    assert fg == pytest.approx(WIND_FG_MAX_DROP / (1 + WIND_MID_SEGMENT_RATIO))


def test_wind_effect_second_segment_is_1_75x_steeper_than_first():
    # The whole point of ADR-0015 section 3's fix: the 15->20mph segment's slope must be exactly
    # WIND_MID_SEGMENT_RATIO (1.75) times the 10->15mph segment's slope -- a property the old
    # quadratic interpolation had no way to guarantee.
    lo, _ = wind_effect(10.0)
    mid, _ = wind_effect(15.0)
    hi, _ = wind_effect(20.0)
    slope1 = (mid - lo) / 5.0
    slope2 = (hi - mid) / 5.0
    assert slope2 == pytest.approx(WIND_MID_SEGMENT_RATIO * slope1)


def test_wind_effect_piecewise_linear_not_quadratic():
    # A quadratic ramp is strictly convex everywhere; piecewise-linear is flat (zero second
    # difference) within each of its two segments. Sampling three points inside the first
    # segment (10-15mph) and checking for exact linearity distinguishes the two shapes.
    a, _ = wind_effect(11.0)
    b, _ = wind_effect(12.0)
    c, _ = wind_effect(13.0)
    assert (b - a) == pytest.approx(c - b)


def test_temperature_effect_bands():
    assert temperature_effect(70.0) == 0.0  # neutral band
    assert temperature_effect(55.0) == 0.0  # inclusive lower edge
    assert temperature_effect(85.0) == 0.0  # inclusive upper edge
    assert temperature_effect(40.0) == pytest.approx(0.03)  # 25-55F mid-cold band
    assert temperature_effect(25.0) == pytest.approx(0.03)  # inclusive lower edge of mid band
    assert temperature_effect(10.0) == pytest.approx(0.048)  # below 25F, extreme
    assert temperature_effect(95.0) == pytest.approx(0.048)  # above 85F, extreme


def test_precipitation_effect_no_precipitation_is_neutral():
    assert precipitation_effect(0.0, 0.0) == (0.0, "none")


def test_precipitation_effect_rain_bands():
    # Light: <0.10in/hr, moderate: 0.10-0.30in/hr, heavy: >0.30in/hr (ADR-0015 section 1).
    drop, band = precipitation_effect(0.05, 0.0)
    assert band == "light_rain"
    assert drop == pytest.approx(1.4 / 64.0)

    drop, band = precipitation_effect(0.10, 0.0)  # inclusive lower edge -> moderate
    assert band == "moderate_rain"
    assert drop == pytest.approx(2.0 / 64.0)

    drop, band = precipitation_effect(0.30, 0.0)  # inclusive upper edge -> still moderate
    assert band == "moderate_rain"
    assert drop == pytest.approx(2.0 / 64.0)

    drop, band = precipitation_effect(0.31, 0.0)
    assert band == "heavy_rain"
    assert drop == pytest.approx(3.0 / 64.0)


def test_precipitation_effect_snow_bands():
    # Light: <1in/hr, moderate: 1-2in/hr, heavy: >2in/hr (ADR-0015 section 1).
    drop, band = precipitation_effect(0.0, 0.5)
    assert band == "light_snow"
    assert drop == pytest.approx(1.8 / 64.0)

    drop, band = precipitation_effect(0.0, 1.0)  # inclusive lower edge -> moderate
    assert band == "moderate_snow"
    assert drop == pytest.approx(4.2 / 64.0)

    drop, band = precipitation_effect(0.0, 2.0)  # inclusive upper edge -> still moderate
    assert band == "moderate_snow"
    assert drop == pytest.approx(4.2 / 64.0)

    drop, band = precipitation_effect(0.0, 2.5)
    assert band == "heavy_snow"
    assert drop == pytest.approx(7.2 / 64.0)


def test_precipitation_effect_mixed_rain_and_snow_takes_the_more_severe_leg():
    # Both legs nonzero -- ADR-0015 doesn't specify this edge case; this module takes whichever
    # produces the larger pp drop, not an additive combination.
    drop, band = precipitation_effect(0.05, 2.5)  # light rain vs. heavy snow
    assert band == "heavy_snow"
    assert drop == pytest.approx(7.2 / 64.0)

    drop, band = precipitation_effect(0.31, 0.5)  # heavy rain vs. light snow
    assert band == "heavy_rain"
    assert drop == pytest.approx(3.0 / 64.0)


def test_parse_open_meteo_window_rain_only_game():
    payload = json.loads((FIXTURES / "open_meteo_forecast_rain.json").read_text())
    kickoff = datetime(2026, 11, 15, 18, 0)
    window = parse_open_meteo_window(payload, kickoff)
    # Window covers 18:00 (rain 0.35) and 19:00 (rain 0.05) -- peak, not average/sum, is used.
    assert window.rain_rate_in_hr == pytest.approx(0.35)
    assert window.snowfall_rate_in_hr == pytest.approx(0.0)

    reading = build_weather_reading("PHI", "2026-11-15T18:00:00Z", window, None)
    assert reading.precipitation_band == "heavy_rain"
    assert reading.precipitation_relative_drop == pytest.approx(3.0 / 64.0)


def test_parse_open_meteo_window_snow_only_game():
    payload = json.loads((FIXTURES / "open_meteo_forecast_snow.json").read_text())
    kickoff = datetime(2026, 12, 20, 18, 0)
    window = parse_open_meteo_window(payload, kickoff)
    # Window covers 18:00 (snowfall 1.5) and 19:00 (snowfall 0.5) -- peak is 1.5, moderate band.
    assert window.rain_rate_in_hr == pytest.approx(0.0)
    assert window.snowfall_rate_in_hr == pytest.approx(1.5)

    reading = build_weather_reading("GB", "2026-12-20T18:00:00Z", window, None)
    assert reading.precipitation_band == "moderate_snow"
    assert reading.precipitation_relative_drop == pytest.approx(4.2 / 64.0)


def test_parse_open_meteo_window_no_precipitation_game():
    payload = json.loads((FIXTURES / "open_meteo_forecast_no_precip.json").read_text())
    kickoff = datetime(2026, 10, 5, 18, 0)
    window = parse_open_meteo_window(payload, kickoff)
    assert window.rain_rate_in_hr == pytest.approx(0.0)
    assert window.snowfall_rate_in_hr == pytest.approx(0.0)

    reading = build_weather_reading("KC", "2026-10-05T18:00:00Z", window, None)
    assert reading.precipitation_band == "none"
    assert reading.precipitation_relative_drop == pytest.approx(0.0)


def test_parse_open_meteo_window_missing_rain_snowfall_fields_leaves_rate_none():
    # Backward-compat: a payload without "rain"/"snowfall" keys at all (e.g. the pre-ADR-0015
    # fixture below) should not raise -- the rate fields are just None, distinct from a real 0.0.
    payload = json.loads((FIXTURES / "open_meteo_forecast.json").read_text())
    kickoff = datetime(2026, 11, 15, 18, 0)
    window = parse_open_meteo_window(payload, kickoff)
    assert window.rain_rate_in_hr is None
    assert window.snowfall_rate_in_hr is None

    reading = build_weather_reading("GB", "2026-11-15T18:00:00Z", window, None)
    assert reading.precipitation_band is None
    assert reading.precipitation_relative_drop is None


def test_neutral_reading_precipitation_leg_is_neutral_not_unavailable():
    reading = neutral_reading("DET", "2026-11-15T18:00:00Z", reason="indoor (dome) -- no API call")
    assert reading.precipitation_relative_drop == 0.0
    assert reading.precipitation_band is None


def test_three_way_combination_end_to_end_realistic_worst_case_stays_inside_the_cap():
    # Integration across the ingestion/scoring boundary: a real weather.py-computed heavy-snow
    # reading, combined with the steepest damped wind/temperature legs, should reproduce
    # ADR-0015 section 2's own worked "realistic worst case" figure: "steep wind + extreme
    # temperature + heavy snow, all damped... computes to roughly -26% combined -- inside the cap
    # without needing to bind." This is the honest end-to-end result (no artificially inflated
    # precipitation value); test_game_environment_score.py separately exercises the cap actually
    # *binding* with a hypothetical precipitation_relative_drop, since no legitimate ADR-0015 band
    # combination reaches -30% -- that non-binding is itself the property ADR-0015 calls out as a
    # reasonable sign the cap isn't overly tight.
    open_meteo = WeatherWindow(
        temperature_f=10.0, wind_mph=25.0, precipitation_in=2.5,
        rain_rate_in_hr=0.0, snowfall_rate_in_hr=2.5,  # >2in/hr -> heavy_snow
        precipitation_probability_pct=None, short_forecast=None, storm_keyword_flag=None,
    )
    reading = build_weather_reading("GB", "2026-12-20T18:00:00Z", open_meteo, None)
    assert reading.precipitation_band == "heavy_snow"
    assert reading.wind_any_a_effect_pct == pytest.approx(WIND_ANYA_MAX_RELATIVE_DROP)  # 25mph -> steepest
    assert reading.temperature_effect_pct == pytest.approx(0.048)  # 10F -> extreme band

    weather_input = WeatherInput(
        is_indoor=False,
        wind_any_a_relative_drop=reading.wind_any_a_effect_pct,
        temperature_relative_drop=reading.temperature_effect_pct,
        precipitation_relative_drop=reading.precipitation_relative_drop,
    )
    component = compute_weather_subscore(weather_input, weight_pct=100 / 9)

    uncapped_log = (
        math.log(1 - reading.wind_any_a_effect_pct)
        + math.log(1 - reading.temperature_effect_pct)
        + math.log(1 - reading.precipitation_relative_drop)
    )
    assert uncapped_log > math.log(0.70)  # confirms the cap does NOT bind here
    combined_multiplier = math.exp(uncapped_log)
    assert combined_multiplier == pytest.approx(0.744, abs=0.005)  # ADR-0015's own "~-26%" figure
    assert component.points == pytest.approx((100 / 9) * combined_multiplier)
    assert not any("capped" in note for note in component.notes)


def test_parse_dk_game_schedule_from_real_shaped_fixture():
    payload = json.loads((FIXTURES / "draftkings_draftables.json").read_text())
    games = parse_dk_game_schedule(payload)
    names = {(g.away_team, g.home_team) for g in games}
    assert ("GB", "MIN") in names
    assert ("MIA", "LV") in names
    gb_min = next(g for g in games if g.away_team == "GB")
    assert gb_min.kickoff_utc == "2026-09-13T20:25:00.0000000Z"
    # 4 distinct competitions in the fixture, deduplicated across its many player rows.
    assert len(games) == 4


def test_parse_open_meteo_window_averages_temp_and_wind_sums_precip():
    payload = json.loads((FIXTURES / "open_meteo_forecast.json").read_text())
    kickoff = datetime(2026, 11, 15, 18, 0)
    window = parse_open_meteo_window(payload, kickoff)
    # Window covers 18:00 and 19:00 entries: temps [29, 28], winds [22, 24], precip [0.02, 0.05].
    assert window.temperature_f == pytest.approx(28.5)
    assert window.wind_mph == pytest.approx(23.0)
    assert window.precipitation_in == pytest.approx(0.07)


def test_parse_open_meteo_window_raises_when_forecast_does_not_cover_kickoff():
    payload = json.loads((FIXTURES / "open_meteo_forecast.json").read_text())
    kickoff = datetime(2027, 1, 1, 0, 0)
    with pytest.raises(ValueError, match="no Open-Meteo hourly entries"):
        parse_open_meteo_window(payload, kickoff)


def test_parse_nws_hourly_window_converts_offsets_and_flags_storm_keyword():
    payload = json.loads((FIXTURES / "nws_hourly_forecast.json").read_text())
    kickoff = datetime(2026, 11, 15, 18, 0)  # 13:00 US/Eastern (-05:00) that day
    window = parse_nws_hourly_window(payload["properties"]["periods"], kickoff)
    # Matches periods #2 (13:00-05:00 -> 18:00 UTC) and #3 (14:00-05:00 -> 19:00 UTC).
    assert window.temperature_f == pytest.approx(29.5)
    assert window.wind_mph == pytest.approx((17.5 + 18.0) / 2)
    assert window.precipitation_probability_pct == 60
    assert window.storm_keyword_flag is True
    assert "Thunderstorm" in window.short_forecast


def test_neutral_reading_short_circuits_with_zero_effects():
    reading = neutral_reading("DET", "2026-11-15T18:00:00Z", reason="indoor (dome) -- no API call")
    assert reading.is_indoor is True
    assert reading.wind_any_a_effect_pct == 0.0
    assert reading.temperature_effect_pct == 0.0
    assert reading.temperature_f is None


def test_build_weather_reading_combines_open_meteo_and_nws():
    open_meteo = WeatherWindow(
        temperature_f=28.5, wind_mph=23.0, precipitation_in=0.07,
        rain_rate_in_hr=0.07, snowfall_rate_in_hr=0.0,
        precipitation_probability_pct=None, short_forecast=None, storm_keyword_flag=None,
    )
    nws = WeatherWindow(
        temperature_f=29.5, wind_mph=17.75, precipitation_in=None,
        rain_rate_in_hr=None, snowfall_rate_in_hr=None,
        precipitation_probability_pct=60, short_forecast="Thunderstorm Likely; Rain", storm_keyword_flag=True,
    )
    reading = build_weather_reading("GB", "2026-11-15T18:00:00Z", open_meteo, nws)
    assert reading.is_indoor is False
    assert reading.has_precipitation is True
    assert reading.wind_any_a_effect_pct > 0.0
    assert reading.temperature_effect_pct == pytest.approx(0.03)  # 28.5F falls in the mid-cold band
    assert reading.precipitation_band == "light_rain"  # 0.07in/hr < 0.10 rain threshold
    assert reading.precipitation_relative_drop == pytest.approx(1.4 / 64.0)
    assert reading.nws_storm_flag is True
    assert "NWS cross-check" in reading.source_notes


def test_build_weather_reading_without_nws_still_works():
    open_meteo = WeatherWindow(
        temperature_f=70.0, wind_mph=5.0, precipitation_in=0.0,
        rain_rate_in_hr=0.0, snowfall_rate_in_hr=0.0,
        precipitation_probability_pct=None, short_forecast=None, storm_keyword_flag=None,
    )
    reading = build_weather_reading("GB", "2026-11-15T18:00:00Z", open_meteo, None)
    assert reading.has_precipitation is False
    assert reading.wind_any_a_effect_pct == 0.0
    assert reading.temperature_effect_pct == 0.0
    assert reading.precipitation_band == "none"
    assert reading.precipitation_relative_drop == pytest.approx(0.0)
    assert reading.nws_storm_flag is None
    assert "NWS unavailable" in reading.source_notes


def test_fetch_weather_reading_short_circuits_domes_without_any_network_call():
    class ExplodingSession:
        def get(self, *args, **kwargs):
            raise AssertionError("no HTTP call should be made for an indoor stadium")

    reading = fetch_weather_reading("DET", "2026-11-15T18:00:00Z", session=ExplodingSession())
    assert reading.is_indoor is True
    assert reading.wind_any_a_effect_pct == 0.0
