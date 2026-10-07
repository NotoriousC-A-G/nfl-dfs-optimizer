"""Archive of captured official NFL injury-report pulls (nflverse `import_injuries`), every position.

Why: both nflverse and nfl.com keep only the *latest* practice status per player-week, so the
Wednesday -> Thursday -> Friday trajectory only exists if we capture it ourselves (same reasoning as
ADR-0038 for RotoGrinders). Each capture is its own file, so intra-week changes (Full on Wednesday,
Limited on Friday) are never overwritten. Also the record of exactly what practice evidence a given
run's Questionable decisions were based on.

Layout: `data/raw/official_injury_snapshots/<YYYY-MM-DDTHHMMSSZ>.json` (gitignored with the rest of
`data/raw/`); atomic writes (tmp + `os.replace`).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from nfl_dfs.ingestion.official_injury_report import OfficialInjuryReportEntry

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = _REPO_ROOT / "data" / "raw" / "official_injury_snapshots"


@dataclass(frozen=True)
class OfficialInjurySnapshot:
    fetched_at: str  # ISO-8601 UTC
    season: int
    week: int
    entries: list[OfficialInjuryReportEntry]


def _filename(fetched_at: str) -> str:
    # 2026-10-07T14:03:11.123+00:00 -> 2026-10-07T140311Z
    stamp = fetched_at.split(".")[0].split("+")[0].replace(":", "")
    return f"{stamp}Z.json"


def write_snapshot(snapshot: OfficialInjurySnapshot, *, root: Path | None = None) -> Path:
    root = root or DEFAULT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    path = root / _filename(snapshot.fetched_at)
    payload = {
        "fetched_at": snapshot.fetched_at,
        "season": snapshot.season,
        "week": snapshot.week,
        "entries": [asdict(e) for e in snapshot.entries],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)
    return path


def read_snapshots(season: int, week: int, *, root: Path | None = None) -> list[OfficialInjurySnapshot]:
    """Every captured snapshot for `(season, week)`, oldest first by embedded `fetched_at`."""
    root = root or DEFAULT_ROOT
    if not root.exists():
        return []
    out = []
    for path in root.glob("*.json"):
        d = json.loads(path.read_text())
        if d["season"] == season and d["week"] == week:
            out.append(
                OfficialInjurySnapshot(
                    fetched_at=d["fetched_at"], season=d["season"], week=d["week"],
                    entries=[OfficialInjuryReportEntry(**e) for e in d["entries"]],
                )
            )
    return sorted(out, key=lambda s: s.fetched_at)


def latest_snapshot(season: int, week: int, *, root: Path | None = None) -> OfficialInjurySnapshot | None:
    snaps = read_snapshots(season, week, root=root)
    return snaps[-1] if snaps else None
