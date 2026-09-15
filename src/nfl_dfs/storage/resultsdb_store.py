"""Raw/curated Parquet storage for RotoGrinders ResultsDB contest data (ADR-0024, building on the
discovery/fetch/parse layer in `nfl_dfs.ingestion.rotogrinders_resultsdb`, ADR-0023).

**Filesystem is the only source of truth for resumability (ADR-0024).** The MLB sister project's own
historical-backfill tooling tracked progress in a separate state file alongside the actual captured data,
which can drift out of sync with what's really on disk (a process killed mid-run leaves the two disagreeing,
with nothing to reconcile them against). This module deliberately keeps no such index: `has_raw_contest_data`
answers resumability with a single `Path.exists()` check against the real captured artifact for that date.
There is nothing else to drift.

**Layout:**
- Raw: `data/raw/resultsdb/nfl/<YYYY-MM-DD>.json` -- one envelope per calendar date (not per contest), so a
  date's resumability can be answered before its contest_id is even known. `status` is one of `"fetched"`,
  `"no_primary_contest"` (a real slate with no 150-max-entry flagship GPP, e.g. most Thu/Mon night dates),
  `"no_draft_groups"` (contest-sources returned nothing at all for this date), or `"unavailable"` (the
  CloudFront `data/` payload 403'd -- pre-2020 coverage floor or an unknown contest_id, ADR-0023's own
  documented ambiguity). `payload` (the raw CloudFront response) is present only when `status == "fetched"`.
  Transient failures (network errors, exhausted rate-limit retries) are never written here -- see the
  backfill orchestrator (`nfl_dfs.ingestion.resultsdb_backfill`) for why that distinction matters.
- Curated: `data/curated/resultsdb/nfl/season=<YYYY>/contests/<date>.parquet`,
  `.../player_exposures/<date>.parquet`, `.../user_exposures/<date>.parquet`, and (ADR-0032)
  `.../lineups/<date>.parquet` -- one Parquet file per date per table, not one file appended to
  over time, so a write is single-shot and idempotent (re-running a date just overwrites its own
  file). `lineups` is backfilled as a separate, later pass over dates a contest was already
  resolved for (see `has_curated_lineups`), not part of the original per-date raw envelope --
  a single contest's lineup table can be 200K+ rows, a genuinely different volume/scope decision
  than the other three tables, made deliberately at a later date (ADR-0032).

Both raw and curated writes are atomic (write to a `.tmp` sibling, then `os.replace`) so a process killed
mid-write never leaves a file that exists but fails to parse.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from nfl_dfs.ingestion.rotogrinders_resultsdb import ContestSummary, LineupRow, PlayerExposureRow, UserExposureRow

# repo_root: storage/resultsdb_store.py -> nfl_dfs -> src -> repo root (same depth as
# normalization/registry.py's DEFAULT_REGISTRY_PATH and normalization/crosswalk.py's DEFAULT_CACHE_PATH).
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RAW_ROOT = _REPO_ROOT / "data" / "raw" / "resultsdb" / "nfl"
DEFAULT_CURATED_ROOT = _REPO_ROOT / "data" / "curated" / "resultsdb" / "nfl"

STATUS_FETCHED = "fetched"
STATUS_NO_PRIMARY_CONTEST = "no_primary_contest"
STATUS_NO_DRAFT_GROUPS = "no_draft_groups"
STATUS_NO_MAIN_SLATE_GROUP = "no_main_slate_group"
STATUS_UNAVAILABLE = "unavailable"

_TERMINAL_STATUSES = frozenset(
    {
        STATUS_FETCHED,
        STATUS_NO_PRIMARY_CONTEST,
        STATUS_NO_DRAFT_GROUPS,
        STATUS_NO_MAIN_SLATE_GROUP,
        STATUS_UNAVAILABLE,
    }
)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)  # atomic on POSIX and Windows


def _atomic_write_parquet(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp_path, index=False)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Raw storage
# ---------------------------------------------------------------------------


def raw_contest_path(date: str, *, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else DEFAULT_RAW_ROOT
    return root / f"{date}.json"


def has_raw_contest_data(date: str, *, base_dir: Path | None = None) -> bool:
    """The one resumability check the backfill orchestrator relies on (ADR-0024): does a terminal-outcome
    envelope already exist on disk for this date? No other state is consulted.
    """
    return raw_contest_path(date, base_dir=base_dir).exists()


def write_raw_contest_data(
    date: str,
    status: str,
    *,
    contest_group_id: int | None = None,
    contest_id: int | None = None,
    contest_name: str | None = None,
    payload: dict | None = None,
    fetched_at: str | None = None,
    base_dir: Path | None = None,
) -> Path:
    """Writes the per-date raw envelope. Only call this with a terminal status (ADR-0024) -- never for a
    transient failure, which must leave `has_raw_contest_data` returning False so a future run retries it.
    """
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"status must be one of {sorted(_TERMINAL_STATUSES)}, got {status!r}")
    if status == STATUS_FETCHED and payload is None:
        raise ValueError("status='fetched' requires a payload")

    envelope = {
        "date": date,
        "status": status,
        "fetched_at": fetched_at,
        "contest_group_id": contest_group_id,
        "contest_id": contest_id,
        "contest_name": contest_name,
        "payload": payload,
    }
    path = raw_contest_path(date, base_dir=base_dir)
    _atomic_write_text(path, json.dumps(envelope))
    return path


def read_raw_contest_data(date: str, *, base_dir: Path | None = None) -> dict:
    path = raw_contest_path(date, base_dir=base_dir)
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Curated storage
# ---------------------------------------------------------------------------


def _season_dir(season: int, table: str, *, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else DEFAULT_CURATED_ROOT
    return root / f"season={season}" / table


def curated_contest_path(date: str, season: int, *, base_dir: Path | None = None) -> Path:
    return _season_dir(season, "contests", base_dir=base_dir) / f"{date}.parquet"


def curated_player_exposures_path(date: str, season: int, *, base_dir: Path | None = None) -> Path:
    return _season_dir(season, "player_exposures", base_dir=base_dir) / f"{date}.parquet"


def curated_user_exposures_path(date: str, season: int, *, base_dir: Path | None = None) -> Path:
    return _season_dir(season, "user_exposures", base_dir=base_dir) / f"{date}.parquet"


def curated_lineups_path(date: str, season: int, *, base_dir: Path | None = None) -> Path:
    return _season_dir(season, "lineups", base_dir=base_dir) / f"{date}.parquet"


def has_curated_lineups(date: str, season: int, *, base_dir: Path | None = None) -> bool:
    """Resumability check for the lineups backfill (ADR-0032) -- separate from
    `has_raw_contest_data` since lineups are backfilled as a later, distinct pass over dates a
    contest was already resolved for, not part of the original per-date raw envelope."""
    return curated_lineups_path(date, season, base_dir=base_dir).exists()


def write_curated_contest(
    date: str,
    season: int,
    contest_id: int,
    summary: ContestSummary,
    player_exposures: list[PlayerExposureRow],
    user_exposures: list[UserExposureRow] | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Path]:
    """Writes the curated Parquet tables for one date's captured contest. Idempotent -- re-running the
    same date overwrites its own files, no append/merge involved.
    """
    written: dict[str, Path] = {}

    contest_row = {"date": date, "season": season, **asdict(summary)}
    contest_df = pd.DataFrame([contest_row])
    contest_path = curated_contest_path(date, season, base_dir=base_dir)
    _atomic_write_parquet(contest_path, contest_df)
    written["contests"] = contest_path

    exposure_rows = [{"date": date, "season": season, "contest_id": contest_id, **asdict(row)} for row in player_exposures]
    # Always write the table, even if empty, so `has` semantics stay simple and downstream reads never
    # need to special-case a missing file vs. a contest with zero rostered players (shouldn't happen, but
    # an empty DataFrame with the right schema is safer than no file at all).
    exposures_df = pd.DataFrame(exposure_rows)
    exposures_path = curated_player_exposures_path(date, season, base_dir=base_dir)
    _atomic_write_parquet(exposures_path, exposures_df)
    written["player_exposures"] = exposures_path

    if user_exposures is not None:
        user_rows = [{"date": date, "season": season, "contest_id": contest_id, **asdict(row)} for row in user_exposures]
        users_df = pd.DataFrame(user_rows)
        users_path = curated_user_exposures_path(date, season, base_dir=base_dir)
        _atomic_write_parquet(users_path, users_df)
        written["user_exposures"] = users_path

    return written


def write_curated_lineups(
    date: str, season: int, contest_id: int, lineups: list[LineupRow], *, base_dir: Path | None = None
) -> Path:
    """Writes one date's full distinct-roster lineup table (ADR-0032). **Nested fields
    (`lineup_players`, `team_stacks`, `game_stacks`, `lineup_trends`) are JSON-string-encoded, not
    stored as native struct/map columns** -- confirmed live that pyarrow's struct-type inference
    for a dict-valued column unions every distinct key seen across ALL rows into a permanent
    column-wide schema field (harmless for `lineup_trends`'s small fixed key set, but `team_stacks`
    /`game_stacks` have effectively unbounded keys across a 200K+-row contest -- team/game ids vary
    every row). JSON-string columns sidestep that entirely and are simple to reason about, at the
    cost of a `json.loads()` on read -- `read_curated_lineups` does that decode automatically, so
    callers get real Python dicts back either way. `entry_name_list` (a plain list of strings, no
    variable-key schema risk) is kept as a native list column."""
    rows = []
    for row in lineups:
        d = asdict(row)
        for key in ("lineup_players", "team_stacks", "game_stacks", "lineup_trends"):
            d[key] = json.dumps(d[key])
        rows.append({"date": date, "season": season, "contest_id": contest_id, **d})
    df = pd.DataFrame(rows)
    path = curated_lineups_path(date, season, base_dir=base_dir)
    _atomic_write_parquet(path, df)
    return path


_LINEUP_JSON_COLUMNS = ("lineup_players", "team_stacks", "game_stacks", "lineup_trends")


def read_curated_lineups(*, season: int | None = None, base_dir: Path | None = None) -> pd.DataFrame:
    """Reads back every curated lineups table (optionally scoped to one season), decoding the
    JSON-string-encoded nested columns back into real Python dicts (see `write_curated_lineups`).
    """
    df = _read_table("lineups", season=season, base_dir=base_dir)
    if df.empty:
        return df
    for col in _LINEUP_JSON_COLUMNS:
        if col in df.columns:
            df[col] = df[col].map(json.loads)
    return df


def _read_table(table: str, *, season: int | None = None, base_dir: Path | None = None) -> pd.DataFrame:
    root = base_dir if base_dir is not None else DEFAULT_CURATED_ROOT
    if season is not None:
        paths = sorted((root / f"season={season}" / table).glob("*.parquet"))
    else:
        paths = sorted(root.glob(f"season=*/{table}/*.parquet"))
    if not paths:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


def read_curated_contests(*, season: int | None = None, base_dir: Path | None = None) -> pd.DataFrame:
    return _read_table("contests", season=season, base_dir=base_dir)


def read_curated_player_exposures(*, season: int | None = None, base_dir: Path | None = None) -> pd.DataFrame:
    return _read_table("player_exposures", season=season, base_dir=base_dir)


def read_curated_user_exposures(*, season: int | None = None, base_dir: Path | None = None) -> pd.DataFrame:
    return _read_table("user_exposures", season=season, base_dir=base_dir)
