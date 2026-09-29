"""Longitudinal raw-response archive for `ingestion/odds_api_props.py`'s per-event player-props
pulls (ADR-0042).

**Raw API response cache, not a parsed/normalized store** -- a deliberate choice, same rationale
`storage/injury_snapshot_store.py` (ADR-0038) already established for a similarly time-sensitive
feed: player-prop lines move throughout the week (injuries, weather, line movement), so "what DK
was actually pricing this player at, as of this pull" is itself the fact worth preserving, not
just whatever `parse_dk_player_props` currently derives from it. Saving the raw per-event JSON
means a future change to `parse_dk_player_props`'s own logic (a new market, a bugfix) can be
re-run retrospectively against real historical pulls -- a parsed-only store would have already
discarded whatever the old parser didn't extract.

**Filesystem is the only source of truth, same convention as `resultsdb_store.py`/
`injury_snapshot_store.py` (ADR-0024/0038)** -- no separate index file that can drift from what's
actually on disk. `has_snapshot` answers resumability with a single `Path.exists()` check.

**Layout:** `data/raw/odds_api_props/<season>/week<week>/<YYYY-MM-DD>.json` -- one envelope per
(season, week, calendar date captured), a finer grain than `injury_snapshot_store.py`'s
per-date-only layout because these props are inherently week-scoped (a Week 5 pull and a Week 6
pull on the same calendar date, e.g. capturing Thursday's game a week apart, are not the same
data and must not overwrite each other). A second capture on the same (season, week, date)
overwrites that file -- same idempotent-per-key posture as the other snapshot stores. Each
envelope's `events` field is the raw, unparsed list of `/events/{id}/odds` response bodies (one
per event pulled that day for that week) -- exactly what the API returned, before
`parse_dk_player_props`/`match_prop_to_dk_player` run on it.

Both writes are atomic (`.tmp` sibling + `os.replace`), same mechanism the other stores use.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# repo_root: storage/odds_api_props_store.py -> nfl_dfs -> src -> repo root (same depth as
# storage/injury_snapshot_store.py's own _REPO_ROOT).
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = _REPO_ROOT / "data" / "raw" / "odds_api_props"


@dataclass(frozen=True)
class PropsSnapshot:
    """One (season, week, calendar date)'s archived raw per-event props pulls."""

    season: int
    week: int
    date: str  # calendar date this snapshot was captured, "YYYY-MM-DD"
    fetched_at: str  # ISO-8601 timestamp of the actual live fetch
    events: list[dict]  # raw, unparsed `/events/{id}/odds` response bodies, one per event


def snapshot_path(season: int, week: int, date: str, *, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    return root / str(season) / f"week{week}" / f"{date}.json"


def has_snapshot(season: int, week: int, date: str, *, base_dir: Path | None = None) -> bool:
    """The one resumability check a capture script should rely on -- no other state is consulted
    (ADR-0024's own convention, reused here)."""
    return snapshot_path(season, week, date, base_dir=base_dir).exists()


def write_snapshot(
    season: int,
    week: int,
    date: str,
    events: list[dict],
    *,
    fetched_at: str,
    base_dir: Path | None = None,
) -> Path:
    """Writes (or overwrites) one (season, week, date)'s snapshot envelope. Idempotent -- re-running
    the same key just overwrites its own file, no append/merge involved."""
    envelope = {
        "season": season,
        "week": week,
        "date": date,
        "fetched_at": fetched_at,
        "events": events,
    }
    path = snapshot_path(season, week, date, base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(envelope))
    os.replace(tmp_path, path)  # atomic on POSIX and Windows
    return path


def read_snapshot(season: int, week: int, date: str, *, base_dir: Path | None = None) -> PropsSnapshot:
    path = snapshot_path(season, week, date, base_dir=base_dir)
    envelope = json.loads(path.read_text())
    return PropsSnapshot(**envelope)


def list_snapshot_dates(season: int, week: int, *, base_dir: Path | None = None) -> list[str]:
    """Every calendar date with an archived snapshot for this (season, week) on disk, ascending --
    reads the filesystem directly (no index file to drift), same posture as `has_snapshot`."""
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    week_dir = root / str(season) / f"week{week}"
    if not week_dir.exists():
        return []
    return sorted(p.stem for p in week_dir.glob("*.json"))


def read_snapshots_for_week(season: int, week: int, *, base_dir: Path | None = None) -> list[PropsSnapshot]:
    """Every archived snapshot for this (season, week), ordered by capture date -- the real input
    a retrospective "how did this line move over the week" comparison would need."""
    dates = list_snapshot_dates(season, week, base_dir=base_dir)
    return [read_snapshot(season, week, date, base_dir=base_dir) for date in dates]
