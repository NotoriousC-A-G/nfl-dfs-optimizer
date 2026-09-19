"""Real, live NFL depth-chart data (`nfl_data_py.import_depth_charts`) -- **live-verified
2026-09-19** against the real week 2 pool before this module was written (per this project's
"verify the real schema before building on it" discipline, not an assumed shape):

- **Fresh, genuinely in-season-updated, not preseason-frozen**: MIN's real RB depth chart carried
  the SAME order (Jones #1, Mason #2, Scott #3) unchanged from late March through early September,
  then correctly re-sorted the same week Jordan Mason was ruled IR -- the most recent real snapshot
  (`dt="2026-09-19T11:56:08Z"`) shows Jones #1, Claiborne #2, Dallas #3, **Mason demoted to #4**.
  New snapshots land multiple times per day (confirmed: two distinct `dt` values on several recent
  calendar dates), not once a week.
- **gsis_id-keyed**: `gsis_id` is the SAME id space `PlayerRoleShare.player_id`/
  `PlayerIdentity.nflverse_gsis_id` already use -- no new crosswalk hop needed.
- **`team` uses nflverse's own vocabulary** -- confirmed live to match the SAME `"nflverse_schedule"`
  alias table `ingestion/usage_share.py`'s `aggregate_team_week_volume` already normalizes against
  (`normalization/team_aliases.py`): every franchise already canonical except the Rams (`"LA"` ->
  `"LAR"`), reused here rather than a new alias table (ADR-0011 "reuse before inventing").
- **A small number of rows have no `gsis_id`** (confirmed live: 4 of 2200 in one real snapshot,
  mostly special-teams/deep-bench defensive spots) -- skipped here, never a fabricated id.

This module fetches nflverse's own FULL historical depth-chart archive (`import_depth_charts`
returns every daily snapshot going back to the preseason -- hundreds of thousands of rows) and
filters down to the single most recent snapshot, the real "as of right now" depth chart -- this
module does not build or need a longitudinal archive of its own the way `injury_snapshot_store.py`
does for RotoGrinders (nflverse already keeps that history on its own end).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from nfl_dfs.normalization.team_aliases import normalize_team

# Real, live-confirmed 2026-09-19 skill-position labels in `pos_abb` -- the only positions this
# module's real consumer (analysis/circumstance/depth_chart.py) needs; every other pos_abb value
# (offensive line, defensive front/secondary, special teams) is real data too but out of scope here.
SKILL_POSITIONS: frozenset[str] = frozenset({"QB", "RB", "WR", "TE"})


@dataclass(frozen=True)
class DepthChartEntry:
    """One player's real, current depth-chart slot at one position group, for one team."""

    team: str  # canonical (DK-shaped) abbreviation, post-normalization
    player_id: str  # nflverse gsis_id -- same id space PlayerRoleShare.player_id/
    # PlayerIdentity.nflverse_gsis_id already use
    player_name: str
    position: str  # pos_abb, e.g. "RB", "WR", "QB", "TE"
    depth_rank: int  # pos_rank -- 1 is the CURRENT real starter/lead at this position, per
    # nflverse's own in-season-updated data (confirmed live: this changes with real roster news,
    # not frozen at the preseason depth chart)
    snapshot_at: str  # the real `dt` this specific depth-chart pull was captured at (ISO-8601 UTC)


def parse_latest_depth_chart(df: pd.DataFrame, *, positions: frozenset[str] = SKILL_POSITIONS) -> list[DepthChartEntry]:
    """Pure transform: `nfl_data_py.import_depth_charts`'s full historical DataFrame (every daily
    snapshot back to the preseason) -> just the single MOST RECENT snapshot (by `dt`), filtered to
    `positions` (skill positions only by default). Separated from the live-fetching
    `fetch_current_depth_chart` so this logic is unit-testable against a synthetic DataFrame, same
    split every other `ingestion/*.py` module in this project already uses (parse vs. fetch).

    Rows with no real `gsis_id` are skipped, never fabricated -- confirmed live these are a small,
    real minority (special-teams/deep-bench spots), not a sign of a broken pull. Rows whose `team`
    doesn't normalize to a canonical abbreviation (`normalize_team("nflverse_schedule", ...)`
    returning `None`) are also skipped.
    """
    if df.empty:
        return []
    latest_dt = df["dt"].max()
    latest = df[(df["dt"] == latest_dt) & df["pos_abb"].isin(positions)]

    entries = []
    for row in latest.itertuples():
        if pd.isna(row.gsis_id):
            continue
        team = normalize_team("nflverse_schedule", row.team)
        if team is None:
            continue
        entries.append(
            DepthChartEntry(
                team=team,
                player_id=row.gsis_id,
                player_name=row.player_name,
                position=row.pos_abb,
                depth_rank=int(row.pos_rank),
                snapshot_at=latest_dt,
            )
        )
    return entries


def fetch_current_depth_chart(season: int, *, positions: frozenset[str] = SKILL_POSITIONS) -> list[DepthChartEntry]:
    """Live pull: `nfl_data_py.import_depth_charts` for `season`, reduced to the real "as of right
    now" depth chart by `parse_latest_depth_chart`."""
    import nfl_data_py as nfl

    df = nfl.import_depth_charts(years=[season])
    return parse_latest_depth_chart(df, positions=positions)
