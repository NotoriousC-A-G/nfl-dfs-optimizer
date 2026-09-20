"""Slate snapshot persistence -- the actual missing prerequisite for anything like the sister MLB
project's real postmortem system (2026-09-20, Chris: "well, build the persistence layer", after
review of `mlb_dfs/tracking/snapshots.py` + `tracking/postmortem/{outcomes,retrospective,replay}.py`
found that MLB's process-grade/chalk-comparison/ceiling-pattern/retrospective machinery is ALL
built on top of one thing: a full, durable capture of the optimizer's own state after each run.
NFL's live script builds an equally rich in-memory state (`player_details`, `agent_results`,
`stack_profiles`) and today just renders it to HTML and discards it -- nothing survives the
process exiting. This module is that missing capture step, nothing more; the retrospective/
chalk-comparison/renderer layers MLB builds on top of its own snapshot are real, separate,
NFL-signal-specific design work, not attempted here (see the review this module followed from).

**Deliberately NOT a port of `mlb_dfs/tracking/snapshots.py`'s full feature set.** That module
carries real complexity earned over many iterations that doesn't match NFL's actual usage shape:
- **Multiple slates per day, multiple builder A/B arms** (`legacy`/`signal_first`/`stance_first`/
  `agents`) -- MLB runs several different slates and construction strategies daily. NFL runs ONE
  live script, ONE construction path (the unified 6-agent system, ADR-per this session's own
  agent/dashboard-unification work), against ONE main slate, roughly once a week. No slate_id/
  team_filter/build_mode grouping is needed here -- `(season, week)` is already the real, unique
  key, same grouping key `storage/agent_results_store.py` already established this session.
- **3-day retention + archive-and-move.** MLB's snapshots roll over fast (a new slate most days);
  NFL's cadence is weekly and the total volume is tiny in comparison. Every snapshot is kept in
  place indefinitely here -- no archiving pass, until real volume ever makes that a problem.
- **Weather/vegas carry-over for a re-run against a past date.** MLB's `--collect-results` re-runs
  its whole pipeline against historical dates whose forecast/odds APIs no longer serve real data,
  so it patches those fields back in from an earlier live snapshot. NFL has no such re-run path
  today (there is no NFL `--collect-results` equivalent yet) -- not built until one exists to
  patch for.

**Serialization: `dataclasses.asdict()`, not a hand-picked field list.** `PlayerDetailRecord`,
`AgentLineupResult`, and `StackProfile` are all plain (nested) frozen dataclasses -- `asdict()`
recurses through every nested section automatically, so this module captures the FULL real state
those three types carry today without re-listing every field (and automatically gains any field
added to them later, the same "resilient by construction" property MLB's own `lineup_set_to_dict`
reuse was chosen for). `frozenset`/`set` fields (e.g. `Lineup.core_stack`) aren't natively
JSON-serializable -- `_json_default` below converts them to a sorted list; anything else
`json.dump` can't handle falls back to `str(obj)`, the same permissive fallback MLB's own
`save_snapshot` uses (`json.dump(..., default=str)`).

**Filesystem is the only source of truth**, same posture as every other storage module in this
project. **Layout:** `data/snapshots/<season>-<week:02d>_<HHMMSS>.json` -- multiple runs in the
same week each get their own timestamped file (a re-run isn't overwritten, matching MLB's own
"each run gets its own file" choice, which is what lets `load_latest_slate_snapshot` pick the
final run of the week while still preserving earlier ones on disk for inspection).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = _REPO_ROOT / "data" / "snapshots"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (frozenset, set)):
        return sorted(obj)
    return str(obj)


def _as_dict_list(records: list[Any]) -> list[dict]:
    return [asdict(r) if is_dataclass(r) else r for r in records]


def snapshot_dir(*, base_dir: Path | None = None) -> Path:
    """The directory all snapshot files live in flat -- filenames themselves carry the
    `<season>-<week>` key (see module docstring), so there's no per-week subdirectory to
    parameterize here."""
    return base_dir if base_dir is not None else DEFAULT_ROOT


def save_slate_snapshot(
    season: int,
    week: int,
    *,
    player_details: list[Any],
    agent_results: list[Any],
    stack_profiles: list[Any],
    timestamp: str | None = None,
    base_dir: Path | None = None,
) -> Path:
    """Writes one full snapshot of the live script's real computed state for `(season, week)`.
    `player_details`/`agent_results`/`stack_profiles` are the exact in-memory lists the live
    script already builds (`PlayerDetailRecord`, `AgentLineupResult`, `StackProfile`) -- passed
    in rather than recomputed, so this module never becomes a second source of truth for any of
    them. Does NOT overwrite a prior run's file for the same week -- each call gets its own
    `HHMMSS`-suffixed file (see module docstring)."""
    now = datetime.now(timezone.utc)
    ts = timestamp if timestamp is not None else now.strftime("%H%M%S")
    filename = f"{season}-{week:02d}_{ts}.json"

    envelope = {
        "season": season,
        "week": week,
        "timestamp": now.isoformat(),
        "snapshot_filename": filename,
        "player_pool": _as_dict_list(player_details),
        "agent_lineups": _as_dict_list(agent_results),
        "stack_profiles": _as_dict_list(stack_profiles),
    }

    root = snapshot_dir(base_dir=base_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(envelope, indent=2, default=_json_default))
    os.replace(tmp_path, path)  # atomic on POSIX and Windows
    return path


def list_slate_snapshots(season: int, week: int, *, base_dir: Path | None = None) -> list[Path]:
    """Every snapshot file for `(season, week)`, oldest-first (filename sorts chronologically --
    same `HHMMSS` convention as MLB's own snapshot filenames)."""
    root = snapshot_dir(base_dir=base_dir)
    if not root.exists():
        return []
    return sorted(root.glob(f"{season}-{week:02d}_*.json"))


def load_latest_slate_snapshot(season: int, week: int, *, base_dir: Path | None = None) -> dict | None:
    """The most recent snapshot for `(season, week)` -- `None` if no run has been snapshotted yet
    (never fabricated as an empty envelope, so a caller can tell "not run yet" apart from "ran
    with zero players/agents")."""
    files = list_slate_snapshots(season, week, base_dir=base_dir)
    if not files:
        return None
    return json.loads(files[-1].read_text())


def load_slate_snapshot(path: Path) -> dict:
    return json.loads(Path(path).read_text())
