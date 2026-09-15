"""Official NFL weekly injury report ingestion, via `nfl_data_py.import_injuries()` (nflverse's
own aggregation of the NFL's official weekly injury report -- a genuinely independent source from
RotoGrinders' "Situation Room" product, `ingestion/rotogrinders_injuries.py`, not a second vendor
repackaging of the same underlying feed).

Built to answer a real, standing question this project never had a committed way to check: is
RotoGrinders' Situation Room injury data actually reliable/current, or does it lag/miss what the
NFL has already officially reported? A prior investigation found real evidence of a staleness gap
(a low match rate against a second source) but that finding was never committed as a script or
number this project could reproduce -- see `docs/adr/0031-injury-report-staleness-check.md` for
the real, reproducible measurement this module was built to support.

**Confirmed live (2026-09-15):** `nfl_data_py.import_injuries([season])` returns ONE row per
(player, week) -- not a daily practice-report time series -- already collapsed to what appears to
be the final pre-game designation for that week (`report_status`: Out/Doubtful/Questionable/`NaN`;
`practice_status`: Full/Limited/Did Not Participate/`NaN`). Keyed by `gsis_id`, the same nflverse
gsis_id space this project's other trailing-stat modules (`ingestion/receiving_profile.py`,
`ingestion/qb_rushing_profile.py`, `composition/player_detail.py`) already join against via
`PlayerIdentity.nflverse_gsis_id` -- no new crosswalk needed for this source, unlike RotoGrinders'
Situation Room data (which joins via `PlayerIdentity.sources["rotogrinders"].native_id` instead).

**A real, disclosed limitation, not a data-quality bug in this module:** because this is one row
per player-week (not a time series), this source cannot by itself measure INTRA-week staleness
(how many hours does RotoGrinders lag a Wednesday practice report) -- only WEEK-level coverage
(does RotoGrinders currently show a status for a player the official report has, and does that
status roughly agree). The comparison script this module supports is explicit about that scope.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class OfficialInjuryReportEntry:
    """One player's official NFL injury report row for one (season, week). `report_status`/
    `practice_status` are `None`, never a fabricated empty string, when nflverse's own row has no
    value there (a real, common case -- most of the league isn't on the injury report most weeks,
    and even players who ARE listed often have a practice designation but no game-status
    designation yet, e.g. mid-week before the final Friday report)."""

    gsis_id: str
    season: int
    week: int
    team: str
    position: str
    full_name: str
    report_status: str | None
    practice_status: str | None
    report_primary_injury: str | None


def parse_official_injury_report(df: pd.DataFrame) -> list[OfficialInjuryReportEntry]:
    """Pure parse of `nfl_data_py.import_injuries()`'s own DataFrame shape into
    `OfficialInjuryReportEntry` rows. Rows with a null `gsis_id` (nflverse's own occasional
    unresolved-player rows, confirmed possible in this feed) are dropped -- this module only
    covers players this project can actually join against."""
    entries: list[OfficialInjuryReportEntry] = []
    for row in df.itertuples(index=False):
        gsis_id = getattr(row, "gsis_id", None)
        if gsis_id is None or pd.isna(gsis_id):
            continue
        entries.append(
            OfficialInjuryReportEntry(
                gsis_id=str(gsis_id),
                season=int(row.season),
                week=int(row.week),
                team=str(row.team),
                position=str(row.position),
                full_name=str(row.full_name),
                report_status=str(row.report_status) if pd.notna(row.report_status) else None,
                practice_status=str(row.practice_status) if pd.notna(row.practice_status) else None,
                report_primary_injury=str(row.report_primary_injury) if pd.notna(row.report_primary_injury) else None,
            )
        )
    return entries


def fetch_official_injury_report(season: int) -> list[OfficialInjuryReportEntry]:
    """Live pull for every week of `season` nflverse has aggregated so far (not just the current
    week -- callers should take the MAX available week, per this module's own docstring caveat
    about the current week's official report possibly lagging behind what's already published)."""
    import nfl_data_py as nfl  # deferred import -- see nflverse.py's identical pattern/rationale

    df = nfl.import_injuries([season])
    return parse_official_injury_report(df)


def latest_week_entries(entries: list[OfficialInjuryReportEntry]) -> tuple[int | None, list[OfficialInjuryReportEntry]]:
    """The most recent week nflverse has aggregated for this season, and just that week's rows --
    `(None, [])` if `entries` is empty (no data pulled yet this season)."""
    if not entries:
        return None, []
    latest = max(e.week for e in entries)
    return latest, [e for e in entries if e.week == latest]
