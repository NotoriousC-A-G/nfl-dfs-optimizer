"""Longitudinal RotoGrinders Situation Room injury-report snapshot archive (ADR-0031's named
follow-on, built ADR-0038).

RotoGrinders exposes no history/archive endpoint for this feed -- confirmed live,
`ingestion/rotogrinders_injuries.py`'s own module docstring ("the CSV endpoint has no week/date
param that changes the response") -- and nflverse's official report only becomes available in
arrears (see `ingestion/official_injury_report.py`). ADR-0031 found this makes a genuine same-week
staleness comparison impossible with a one-shot live pull: `scripts/injury_staleness_check.py`
can only ever compare RotoGrinders' CURRENT snapshot against whatever week the official feed has
already finalized, almost always a week behind. **The only way to ever retrospectively validate
"was Situation Room's read correct, as of when it mattered" is to start saving its live pulls
ourselves, on an ongoing basis, and compare each saved snapshot against the official report once
that week's official data finally becomes available.** This module is that archive.

**Filesystem is the only source of truth, same convention as `storage/resultsdb_store.py`
(ADR-0024)** -- no separate index/state file that can drift out of sync with what's actually on
disk. `has_snapshot` answers resumability with a single `Path.exists()` check.

**Layout:** `data/raw/injury_snapshots/<YYYY-MM-DD>.json` -- one envelope per CALENDAR date (the
date the snapshot was captured), mirroring `resultsdb_store.py`'s own "one envelope per date, not
per contest" convention. A deliberate one-snapshot-per-day resolution, not a full intra-day time
series -- `scripts/injury_snapshot_capture.py` is meant to run on a recurring (e.g. daily) cadence,
and a second capture on the same calendar date simply overwrites that date's file (atomic write,
same idempotent-per-date posture `resultsdb_store.py` already established). Each envelope carries
`season`/`target_week` (which week these injury reads are FOR -- Situation Room shows whatever
week it currently considers "upcoming," so this context has to be supplied by the caller at
capture time, not inferred later) alongside the raw entries.

Both writes are atomic (write to a `.tmp` sibling, then `os.replace`) so a process killed mid-write
never leaves a file that exists but fails to parse -- same mechanism `resultsdb_store.py` uses.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from nfl_dfs.ingestion.rotogrinders_injuries import InjuryReportEntry

# repo_root: storage/injury_snapshot_store.py -> nfl_dfs -> src -> repo root (same depth as
# storage/resultsdb_store.py's own _REPO_ROOT).
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = _REPO_ROOT / "data" / "raw" / "injury_snapshots"


@dataclass(frozen=True)
class InjurySnapshot:
    """One calendar date's archived RotoGrinders Situation Room pull."""

    date: str  # calendar date this snapshot was captured, "YYYY-MM-DD"
    season: int
    target_week: int  # which week these injury reads describe -- supplied by the caller at
    # capture time (Situation Room's own CSV carries no season/week field, confirmed,
    # rotogrinders_injuries.py's own module docstring)
    fetched_at: str  # ISO-8601 timestamp of the actual live fetch (may differ slightly from
    # `date` -- e.g. a run just after local midnight UTC)
    entries: list[InjuryReportEntry]


def snapshot_path(date: str, *, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    return root / f"{date}.json"


def has_snapshot(date: str, *, base_dir: Path | None = None) -> bool:
    """The one resumability check `injury_snapshot_capture.py` relies on -- no other state is
    consulted (ADR-0024's own convention, reused here)."""
    return snapshot_path(date, base_dir=base_dir).exists()


def write_snapshot(
    date: str,
    season: int,
    target_week: int,
    entries: list[InjuryReportEntry],
    *,
    fetched_at: str,
    base_dir: Path | None = None,
) -> Path:
    """Writes (or overwrites) one calendar date's snapshot envelope. Idempotent -- re-running the
    same date just overwrites its own file, no append/merge involved, matching `resultsdb_store.py`'s
    own curated-write posture."""
    envelope = {
        "date": date,
        "season": season,
        "target_week": target_week,
        "fetched_at": fetched_at,
        "entries": [asdict(e) for e in entries],
    }
    path = snapshot_path(date, base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(envelope))
    os.replace(tmp_path, path)  # atomic on POSIX and Windows
    return path


def read_snapshot(date: str, *, base_dir: Path | None = None) -> InjurySnapshot:
    path = snapshot_path(date, base_dir=base_dir)
    envelope = json.loads(path.read_text())
    return InjurySnapshot(
        date=envelope["date"],
        season=envelope["season"],
        target_week=envelope["target_week"],
        fetched_at=envelope["fetched_at"],
        entries=[InjuryReportEntry(**row) for row in envelope["entries"]],
    )


def list_snapshot_dates(*, base_dir: Path | None = None) -> list[str]:
    """Every calendar date with an archived snapshot on disk, ascending -- reads the filesystem
    directly (no index file to drift), same posture as `has_snapshot`."""
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    if not root.exists():
        return []
    return sorted(p.stem for p in root.glob("*.json"))


def read_snapshots_for_week(season: int, target_week: int, *, base_dir: Path | None = None) -> list[InjurySnapshot]:
    """Every archived snapshot whose `(season, target_week)` matches, ordered by capture date --
    the real input the retrospective staleness-over-time comparison needs
    (`scripts/injury_snapshot_retrospective_check.py`)."""
    snapshots = [read_snapshot(date, base_dir=base_dir) for date in list_snapshot_dates(base_dir=base_dir)]
    return [s for s in snapshots if s.season == season and s.target_week == target_week]
