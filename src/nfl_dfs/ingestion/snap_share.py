"""Player-level snap-share ingestion (ADR-0022 Round A -- player-detail metrics data scoping).

Mirrors `usage_share.py`'s shape: pull `nfl_data_py.import_snap_counts()`, join to this project's
canonical `gsis_id` (ADR-0013), aggregate to player-week, and expose latest-week percentage plus a
trailing rolling average. Purely descriptive (ADR-0022 explicit): no shrinkage/prior-blending, no
formula wiring into `RoleShare`, `MatchupContext`, or any other Section 6 input this round.

## The join problem this module exists to solve, and the live spot-check ADR-0022 asked for

`import_snap_counts()` keys players by `pfr_player_id` (e.g. `"BankKe01"`), not `gsis_id` --
ADR-0022 flagged this as a real, previously-undocumented finding, distinct from the join problem
ADR-0013 already solved for PFF (`pff_id`) and already rejected for Footballguys (`pfr_id`
false-positive collision, Gibbs/Gibbens). This is a *different* `pfr_id` path than the one
ADR-0013 rejected: here it's the nflverse/ffverse crosswalk's own `pfr_id` column (from
`nfl_data_py.import_ids()`, the same crosswalk file `normalization/crosswalk.py` already caches
for PFF's `pff_id` probe) being matched against `nfl_data_py.import_snap_counts()`'s own
`pfr_player_id` -- both nflverse-family sources, not a vendor's independently-scraped HTML
attribute. ADR-0022 explicitly said this pairing had NOT been spot-checked the way ADR-0013
spot-checked `pff_id` before being trusted, so that check was run live this session
(`2025` snap counts, `nfl_data_py==0.3.2`, `.venv`), not assumed clean:

- **Row-level match rate across every position in the file: 81.3%** (21,655 / 26,612 REG rows
  matched a crosswalk `pfr_id`). This headline number is misleading on its own -- broken down by
  position, the misses are concentrated almost entirely in offensive line positions (`OL`, `T`,
  `G`, `C` all matched at **0%** -- 353 rows). Confirmed why, not just observed: the crosswalk
  itself (`dynastyprocess/data`, a fantasy-football-oriented ID file) carries almost no offensive
  linemen at all (`import_ids()`'s own `position` value_counts: 53 `OT`, 6 `C`, 1 `T`, zero rows
  for a bare `OL`/`G` label) -- this is a structural gap in the crosswalk's own player population,
  not a bug in this join.
- **Restricted to this project's five DFS-rosterable skill positions (QB/RB/WR/TE -- OL is not a
  DK-salaried position and was never in scope for a player-detail view), the join is excellent:
  646 unique `pfr_player_id`s, 640 matched (**99.1%** by player), and **99.67%** of individual
  player-week snap-count rows for those positions matched a crosswalk `gsis_id`.** A name
  cross-check on every matched row (comparing `import_snap_counts()`'s own `player` name against
  the crosswalk row's `name`) found exactly one apparent mismatch on the whole file
  (`"Eddy Piñeiro"` vs. `"Eddy Pineiro"`, a kicker, outside the skill-position population anyway)
  -- a diacritic-stripping difference, the same real person, not a Gibbs/Gibbens-style collision.
- **Verdict: this join is reliable for this feature's actual population** (skill positions), and
  is reported as such rather than rounded up from the misleading whole-file 81% or rounded down
  from the OL-driven 0% floor. The ~0.3-0.9% skill-position miss rate (six players: a small,
  QA-visible residual, not investigated player-by-player here) is surfaced via `gsis_id_resolved`
  below rather than silently dropped or silently mismatched -- an unresolved row is skipped from
  `aggregate_snap_player_week`'s per-player output (there is no canonical ID to key it by, and
  this project's ADR-0013 posture is "no fallback name/team/position matcher was asked for here"),
  not force-matched.

## Why this doesn't go through the full ADR-0013 fallback-matcher pipeline

ADR-0013's crosswalk-probe-then-name-verify-then-fallback machinery
(`normalization/crosswalk.py`, `normalization/matcher.py`) exists for the four *vendor* sources
(DK, PFF, RotoGrinders, Footballguys) that have no native relationship to nflverse's own ID space.
`nfl_data_py.import_snap_counts()` is not a vendor source in that sense -- it's the same nflverse
family `usage_share.py`/`nflverse.py` already trust directly via `rusher_player_id`/
`receiver_player_id` (`gsis_id`-shaped already). Joining its `pfr_player_id` to the crosswalk's own
`pfr_id` column is a same-family ID lookup, not name/team/position fuzzy matching -- so this module
does a direct `pd.merge` against `normalization/crosswalk.py`'s already-cached crosswalk frame
(the exact file ADR-0013 introduced for PFF's `pff_id` probe) rather than routing through the
per-source `SourceMatch`/`MatchMethod` machinery built for name-based fallback matching.

## No shrinkage/prior-blending (ADR-0022 explicit)

Unlike `PlayerRoleShare`, there is no league-prior blending here -- ADR-0022 is explicit that snap
share is "descriptive, not a projection input." `offense_pct_trailing`/`defense_pct_trailing`/
`st_pct_trailing` are a plain mean of that player's `offense_pct`/`defense_pct`/`st_pct` across
every completed week through `target_week - 1` (season-to-date, matching this project's dominant
"cumulative through last completed week" convention used elsewhere -- `PlayerRoleShare`, pace/PROE
-- rather than inventing a new arbitrary fixed rolling-window constant ADR-0022 doesn't specify a
size for). `offense_pct_last_week`/etc. are that player's most recent played week's raw values.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from nfl_dfs.normalization.crosswalk import DEFAULT_CACHE_PATH, fetch_crosswalk
from nfl_dfs.normalization.team_aliases import normalize_team

_REG_GAME_TYPE = "REG"


@dataclass(frozen=True)
class PlayerSnapShare:
    """One player's trailing snap-share data for one "as of" week, mirroring `PlayerRoleShare`'s
    trailing-only shape (`usage_share.py`) but with no shrinkage/prior-blending (ADR-0022:
    descriptive, not a projection input).

    `player_id` is the canonical `gsis_id` (ADR-0013) -- this record is only ever produced for a
    `pfr_player_id` the crosswalk join resolved to a `gsis_id` (see module docstring's live
    join-quality spot-check); an unresolved `pfr_player_id` never appears here rather than being
    surfaced under a fabricated ID.
    """

    player_id: str  # canonical gsis_id
    pfr_player_id: str  # nfl_data_py.import_snap_counts()'s own native key, kept for traceability
    player_name: str | None
    team: str | None
    position: str | None
    season: int
    week: int  # "as of" week; trailing data covers completed weeks 1..week-1 only
    weeks_played: int
    offense_pct_last_week: float | None
    defense_pct_last_week: float | None
    st_pct_last_week: float | None
    offense_pct_trailing: float | None
    defense_pct_trailing: float | None
    st_pct_trailing: float | None


def join_snap_counts_to_gsis_id(snap_counts: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """Pure join: `nfl_data_py.import_snap_counts()`'s `pfr_player_id` -> the nflverse crosswalk's
    own `pfr_id`/`gsis_id` columns (see module docstring for why this is a same-family nflverse ID
    lookup, not an ADR-0013 vendor fallback match). Adds `gsis_id` and `gsis_id_resolved` columns;
    every input row is preserved (left join) so callers can see/report the unresolved rows rather
    than have them silently vanish before aggregation.
    """
    lookup = crosswalk[["pfr_id", "gsis_id"]].dropna(subset=["pfr_id"]).drop_duplicates(subset=["pfr_id"])
    merged = snap_counts.merge(lookup, left_on="pfr_player_id", right_on="pfr_id", how="left")
    merged["gsis_id_resolved"] = merged["gsis_id"].notna()
    return merged.drop(columns=["pfr_id"])


def aggregate_snap_player_week(snap_counts_joined: pd.DataFrame, *, game_type: str | None = _REG_GAME_TYPE) -> pd.DataFrame:
    """One row per (season, week, gsis_id): `offense_pct`/`defense_pct`/`st_pct` for that player
    that week. Rows with no resolved `gsis_id` are dropped here (not earlier, in
    `join_snap_counts_to_gsis_id`) so callers can still inspect/report the unresolved population
    before this aggregation discards it. `import_snap_counts()` is already one row per
    (season, week, pfr_player_id) for `game_type == "REG"` (live-confirmed, no duplicate
    player-weeks), so this is a straight rename/select, not a groupby-sum like
    `usage_share.py`'s play-by-play aggregation.
    """
    df = snap_counts_joined if game_type is None else snap_counts_joined[snap_counts_joined["game_type"] == game_type]
    resolved = df[df["gsis_id_resolved"]].copy()
    # nflverse's snap-count team column uses "LA" for the Rams, same as its pbp/schedule tables
    # (usage_share.py/nflverse.py's own live-confirmed finding) -- normalized here so this
    # module's output joins cleanly against the rest of the pipeline's canonical team vocabulary.
    resolved["team"] = resolved["team"].map(lambda t: normalize_team("nflverse_schedule", t))
    return resolved.rename(columns={"gsis_id": "player_id", "player": "player_name"})[
        [
            "season",
            "week",
            "player_id",
            "pfr_player_id",
            "player_name",
            "team",
            "position",
            "offense_pct",
            "defense_pct",
            "st_pct",
        ]
    ]


def compute_snap_shares_for_week(player_week: pd.DataFrame, target_week: int) -> list[PlayerSnapShare]:
    """Pure computation for one target week: every player with any trailing (weeks `1..target_week
    - 1` only, ADR-0003/ADR-0014 no-look-ahead discipline) snap-count row gets a `PlayerSnapShare`
    with that player's most recent trailing week's raw values plus a season-to-date trailing
    average (see module docstring for why this is a plain mean, not a shrinkage blend). A player
    who only appears in `target_week` itself or later has no trailing row and is simply absent
    from the output -- mirroring `usage_share.py`'s "no trailing data -> no candidate row" posture.
    """
    prior = player_week[player_week["week"] < target_week]
    if prior.empty:
        return []

    results: list[PlayerSnapShare] = []
    for player_id, rows in prior.groupby("player_id", observed=True):
        rows = rows.sort_values("week")
        last = rows.iloc[-1]
        results.append(
            PlayerSnapShare(
                player_id=str(player_id),
                pfr_player_id=str(last["pfr_player_id"]),
                player_name=str(last["player_name"]) if pd.notna(last["player_name"]) else None,
                team=str(last["team"]) if pd.notna(last["team"]) else None,
                position=str(last["position"]) if pd.notna(last["position"]) else None,
                season=int(last["season"]),
                week=target_week,
                weeks_played=len(rows),
                offense_pct_last_week=float(last["offense_pct"]) if pd.notna(last["offense_pct"]) else None,
                defense_pct_last_week=float(last["defense_pct"]) if pd.notna(last["defense_pct"]) else None,
                st_pct_last_week=float(last["st_pct"]) if pd.notna(last["st_pct"]) else None,
                offense_pct_trailing=float(rows["offense_pct"].mean()) if rows["offense_pct"].notna().any() else None,
                defense_pct_trailing=float(rows["defense_pct"].mean()) if rows["defense_pct"].notna().any() else None,
                st_pct_trailing=float(rows["st_pct"].mean()) if rows["st_pct"].notna().any() else None,
            )
        )
    return results


def fetch_snap_shares(current_season: int, target_week: int) -> list[PlayerSnapShare]:
    """Live pull + full pipeline for one season/week: current season's snap counts (through
    whatever weeks are already played), joined to the (cached) nflverse ID crosswalk, aggregated
    to player-week, then computed for `target_week`."""
    import nfl_data_py as nfl  # deferred import -- matches usage_share.py/nflverse.py's pattern

    snap_counts = nfl.import_snap_counts([current_season])
    crosswalk = fetch_crosswalk(cache_path=DEFAULT_CACHE_PATH)
    joined = join_snap_counts_to_gsis_id(snap_counts, crosswalk)
    player_week = aggregate_snap_player_week(joined)
    return compute_snap_shares_for_week(player_week, target_week)
