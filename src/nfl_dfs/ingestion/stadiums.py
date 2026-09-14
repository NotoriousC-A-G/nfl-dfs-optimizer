"""Static reference table: each of the 32 teams' home stadium location and roof type.

Built for `weather.py` (PRD Section 6, `GameEnvironmentScore` weather sub-component) -- Open-Meteo
and NWS both need a latitude/longitude per game, and domed/retractable-roof stadiums need to
short-circuit to neutral weather without an API call (Section 6 weather-redistribution rule).

Nothing in the codebase already had this (checked: no `stadium`/`lat`/`lon`/`roof` reference
existed anywhere under `src/` before this file). This is public, stable data -- stadiums don't
move and roof type changes only on the rare multi-year renovation -- but coordinates were *not*
guessed: every lat/lon below was pulled live (2026-09-13) from each stadium's own Wikipedia
infobox (the `{{Infobox stadium}}` `coordinates` field, which Wikipedia itself sources from
GNIS/OSM-derived geodata), not typed from memory. Two teams share a stadium (NYG/NYJ at MetLife;
LAR/LAC at SoFi) so there are 32 team entries over 30 physical stadiums.

## Roof-type classification and the retractable-roof assumption

Three values are used:

- `"outdoor"` -- open-air, no roof at all.
- `"dome"` -- fully enclosed, fixed (non-retractable) roof. Includes two edge cases worth stating
  explicitly rather than leaving implicit:
  - **SoFi Stadium** (LAR/LAC): its roof is a fixed (non-retractable) translucent ETFE canopy,
    but the building is open-sided (no walls) -- Wikipedia's own infobox labels this "Skylight"
    rather than "Dome" and the SoFi article states it "remains an open-air facility" despite the
    fixed roof, since wind can still enter through the open sides even though the roof blocks
    precipitation overhead. No data source in this project (Open-Meteo, NWS, PFF, or nflverse)
    distinguishes "open-sided fixed roof" from a true sealed dome, so SoFi is classified `"dome"`
    here for the short-circuit (matching the practical effect the fixed roof has on precipitation
    and, materially, on rain-driven game impact) -- **flagged as a known simplification**: a
    genuinely gusty day outside SoFi could still matter for a kicker/deep-ball game in a way a
    sealed dome never would, and this table can't currently tell the two apart.
  - **Allegiant Stadium** (LV): also a fixed ETFE roof, but fully walled and climate-controlled
    (air conditioning runs during games) -- a true sealed dome, no ambiguity.
- `"retractable"` -- a roof that can open or close (AT&T, NRG/Houston, Lucas Oil, Mercedes-Benz,
  State Farm). **Stated assumption, not confirmed by any live data check:** no source found in
  Phase 0 or this pass reports a given week's roof *open/closed* status ahead of an automated
  weekly pull (that's a game-day decision, usually announced only hours before kickoff based on
  forecast). Per the task's own framing, this table therefore treats every retractable-roof
  stadium as **closed by default** -- i.e. `weather.py` short-circuits retractable roofs to the
  same neutral, no-API-call path as a true dome. This is conservative in the sense that a closed
  assumption is right most cold/wet weeks (the scenario that would otherwise most distort the
  score) and wrong only when a team opens the roof on a nice-weather day, which costs nothing
  (the game would have scored near-neutral either way). Flagged for the Architect: if a live
  roof-status source turns up later (some team beat writers/local media report it pre-kickoff on
  Twitter/X, but that's not a structured feed), this assumption should be revisited.

Source: Wikipedia infobox `coordinates` field per stadium article, fetched live via WebFetch on
2026-09-13 (article title used for each team is the current stadium name at that date; several
have been renamed by naming-rights deals within the last two years -- e.g. Houston's is still
titled "NRG Stadium" on Wikipedia even though Phase 0's Odds/PFF samples may reference other
names). City field is carried along for human sanity-checking only, not used in any computation.
"""

from __future__ import annotations

from dataclasses import dataclass

ROOF_TYPES = frozenset({"outdoor", "dome", "retractable"})


@dataclass(frozen=True)
class Stadium:
    team: str  # canonical DK abbreviation (team_aliases.CANONICAL_TEAMS)
    name: str
    city: str
    latitude: float
    longitude: float
    roof_type: str  # one of ROOF_TYPES

    def __post_init__(self) -> None:
        if self.roof_type not in ROOF_TYPES:
            raise ValueError(f"{self.team}: unknown roof_type {self.roof_type!r}, expected one of {ROOF_TYPES}")

    @property
    def is_indoor(self) -> bool:
        """True for both true domes and retractable roofs -- see module docstring for the
        stated closed-by-default assumption on retractable roofs."""
        return self.roof_type in ("dome", "retractable")


STADIUMS: dict[str, Stadium] = {
    s.team: s
    for s in (
        Stadium("ARI", "State Farm Stadium", "Glendale, AZ", 33.52800, -112.26300, "retractable"),
        Stadium("ATL", "Mercedes-Benz Stadium", "Atlanta, GA", 33.75556, -84.40000, "retractable"),
        Stadium("BAL", "M&T Bank Stadium", "Baltimore, MD", 39.27806, -76.62278, "outdoor"),
        Stadium("BUF", "Highmark Stadium", "Orchard Park, NY", 42.77306, -78.79222, "outdoor"),
        Stadium("CAR", "Bank of America Stadium", "Charlotte, NC", 35.22583, -80.85278, "outdoor"),
        Stadium("CHI", "Soldier Field", "Chicago, IL", 41.86230, -87.61670, "outdoor"),
        Stadium("CIN", "Paycor Stadium", "Cincinnati, OH", 39.09500, -84.51600, "outdoor"),
        Stadium("CLE", "Huntington Bank Field", "Cleveland, OH", 41.50611, -81.69944, "outdoor"),
        Stadium("DAL", "AT&T Stadium", "Arlington, TX", 32.74778, -97.09278, "retractable"),
        Stadium("DEN", "Empower Field at Mile High", "Denver, CO", 39.74389, -105.02000, "outdoor"),
        Stadium("DET", "Ford Field", "Detroit, MI", 42.34000, -83.04556, "dome"),
        Stadium("GB", "Lambeau Field", "Green Bay, WI", 44.50139, -88.06222, "outdoor"),
        Stadium("HOU", "NRG Stadium", "Houston, TX", 29.68472, -95.41083, "retractable"),
        Stadium("IND", "Lucas Oil Stadium", "Indianapolis, IN", 39.76006, -86.16381, "retractable"),
        Stadium("JAX", "EverBank Stadium", "Jacksonville, FL", 30.32389, -81.63750, "outdoor"),
        Stadium("KC", "Arrowhead Stadium", "Kansas City, MO", 39.04889, -94.48389, "outdoor"),
        Stadium("LAC", "SoFi Stadium", "Inglewood, CA", 33.95300, -118.33900, "dome"),
        Stadium("LAR", "SoFi Stadium", "Inglewood, CA", 33.95300, -118.33900, "dome"),
        Stadium("LV", "Allegiant Stadium", "Paradise, NV", 36.09056, -115.18389, "dome"),
        Stadium("MIA", "Hard Rock Stadium", "Miami Gardens, FL", 25.95806, -80.23889, "outdoor"),
        Stadium("MIN", "U.S. Bank Stadium", "Minneapolis, MN", 44.97400, -93.25800, "dome"),
        Stadium("NE", "Gillette Stadium", "Foxborough, MA", 42.09100, -71.26400, "outdoor"),
        Stadium("NO", "Caesars Superdome", "New Orleans, LA", 29.95083, -90.08111, "dome"),
        Stadium("NYG", "MetLife Stadium", "East Rutherford, NJ", 40.81353, -74.07436, "outdoor"),
        Stadium("NYJ", "MetLife Stadium", "East Rutherford, NJ", 40.81353, -74.07436, "outdoor"),
        Stadium("PHI", "Lincoln Financial Field", "Philadelphia, PA", 39.90083, -75.16750, "outdoor"),
        Stadium("PIT", "Acrisure Stadium", "Pittsburgh, PA", 40.44667, -80.01583, "outdoor"),
        Stadium("SEA", "Lumen Field", "Seattle, WA", 47.59520, -122.33160, "outdoor"),
        Stadium("SF", "Levi's Stadium", "Santa Clara, CA", 37.40300, -121.97000, "outdoor"),
        Stadium("TB", "Raymond James Stadium", "Tampa, FL", 27.97583, -82.50333, "outdoor"),
        Stadium("TEN", "Nissan Stadium", "Nashville, TN", 36.16639, -86.77139, "outdoor"),
        Stadium("WAS", "Northwest Stadium", "Landover, MD", 38.90778, -76.86444, "outdoor"),
    )
}

assert len(STADIUMS) == 32, f"expected 32 team entries, got {len(STADIUMS)}"


def get_stadium(team: str) -> Stadium:
    try:
        return STADIUMS[team]
    except KeyError:
        raise ValueError(
            f"no stadium entry for team {team!r} -- expected a canonical DK abbreviation "
            f"(see normalization/team_aliases.py)"
        ) from None
