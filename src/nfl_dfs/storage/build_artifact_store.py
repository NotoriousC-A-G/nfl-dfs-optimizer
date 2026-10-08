"""Persist what the redesigned build produced for a slate, so the post-mortem can grade it (ADR-0046, decision 11).

One JSON file per run, next to the legacy slate snapshots but never inside them: `data/snapshots/redesign/<season>-<week>_<HHMMSS>.json`.
It holds the game theses, the expert's pools (tiers, build theses, unavailable agents), each pool agent's lineups with their floor
lean and any expert repair, and every failure with its diagnosis -- including an agent that could NOT be built, because "no lineup
and why" is itself a result to grade. Atomic writes; a later run never overwrites an earlier one.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIR = _REPO_ROOT / "data" / "snapshots" / "redesign"


def save_build_artifact(season: int, week: int, payload: dict[str, Any], *, base_dir: Path | None = None) -> Path:
    now = datetime.now(timezone.utc)
    root = base_dir or DEFAULT_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{season}-{week:02d}_{now.strftime('%H%M%S')}.json"
    n = 1
    while path.exists():  # two runs inside one second must not overwrite each other
        path = root / f"{season}-{week:02d}_{now.strftime('%H%M%S')}-{n}.json"
        n += 1
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"season": season, "week": week, "created_at": now.isoformat(), **payload}, indent=2, default=str))
    os.replace(tmp, path)
    return path


def load_latest_build_artifact(season: int, week: int, *, base_dir: Path | None = None) -> dict | None:
    root = base_dir or DEFAULT_DIR
    files = sorted(root.glob(f"{season}-{week:02d}_*.json")) if root.exists() else []
    if not files:
        return None
    return max((json.loads(f.read_text()) for f in files), key=lambda d: d["created_at"])
