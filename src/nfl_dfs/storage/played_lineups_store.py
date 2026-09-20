"""Archive of the lineups Chris actually entered into a real contest (2026-09-20, Chris: "I want
to directly log the lineups I play and label them as L1, L2, L3").

This is distinct from the still-unbuilt agent forward-logging store (the "track performance for
each [agent]" ask) -- that one will log what each of the 6 `NflAgentConstructor` agents *generated*.
This one logs what Chris *actually submitted*, which may be an agent's lineup verbatim, a hand-edit
of one (see `PlayedLineup.edits`), or something built outside the tool entirely. The two stores are
expected to eventually be joined (a played lineup that started from an agent's output can name that
agent via `source_agent_label`), but neither depends on the other existing.

**Filesystem is the only source of truth, same convention as `storage/injury_snapshot_store.py` /
`storage/resultsdb_store.py`** -- no separate index file that can drift out of sync with what's on
disk. **Layout:** `data/played_lineups/<season>-<week>.json`, one envelope per (season, week),
whole-envelope overwrite on write (same idempotent posture as those two stores) -- a week's played
lineups are expected to be logged in one sitting once Chris shares them, not incrementally streamed.

**Player identity note:** players are recorded by `display_name`/`position`/`team` as Chris states
them, NOT by `canonical_id`. The normalization stage's `PlayerIdentity` table (`normalization/
identity.py`) is the real canonical-id source, but no per-week snapshot of it is persisted to disk
today (confirmed -- `dashboard_output/` holds only the rendered HTML) for this store to join
against at write time. A future join to real settled scores (ResultsDB) will need to resolve these
names the same NAME_TEAM_POSITION fallback way `identity.py` already resolves DK/RotoGrinders --
this is a disclosed, deliberate deferral, not an oversight.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = _REPO_ROOT / "data" / "played_lineups"


@dataclass(frozen=True)
class PlayedPlayer:
    """One roster slot in a played lineup, as Chris named it."""

    display_name: str
    position: str
    team: str
    salary: int | None = None


@dataclass(frozen=True)
class LineupEdit:
    """One hand-edit Chris made to a lineup before submitting it -- e.g. swapping out an agent's
    pick. `note` is free text for why (injury news, a gut call, etc.); `None` when no reason was
    given."""

    player_out: str
    player_in: str
    note: str | None = None


@dataclass(frozen=True)
class PlayedLineup:
    """One lineup Chris actually entered into a real contest."""

    label: str  # Chris's own label, e.g. "L1" -- not re-derived or validated against any
    # agent-index scheme; whatever he calls it is what's stored.
    players: tuple[PlayedPlayer, ...]
    source_agent_label: str | None = None  # which of the 6 NflAgentConstructor agents this
    # lineup started from, if any (e.g. "Volatility Engine") -- None if hand-built or unknown.
    edits: tuple[LineupEdit, ...] = field(default_factory=tuple)
    total_salary: int | None = None


@dataclass(frozen=True)
class PlayedLineupsWeek:
    """One (season, week)'s full set of played lineups."""

    season: int
    week: int
    logged_at: str  # ISO-8601 timestamp of when this envelope was written
    lineups: tuple[PlayedLineup, ...]


def played_lineups_path(season: int, week: int, *, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    return root / f"{season}-{week}.json"


def has_played_lineups(season: int, week: int, *, base_dir: Path | None = None) -> bool:
    return played_lineups_path(season, week, base_dir=base_dir).exists()


def write_played_lineups(
    season: int,
    week: int,
    lineups: list[PlayedLineup],
    *,
    logged_at: str,
    base_dir: Path | None = None,
) -> Path:
    """Writes (or overwrites) one week's full set of played lineups. Idempotent -- re-running the
    same (season, week) just overwrites its own file, same posture as `injury_snapshot_store.py`."""
    envelope = {
        "season": season,
        "week": week,
        "logged_at": logged_at,
        "lineups": [
            {
                "label": lineup.label,
                "players": [asdict(p) for p in lineup.players],
                "source_agent_label": lineup.source_agent_label,
                "edits": [asdict(e) for e in lineup.edits],
                "total_salary": lineup.total_salary,
            }
            for lineup in lineups
        ],
    }
    path = played_lineups_path(season, week, base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(envelope, indent=2))
    os.replace(tmp_path, path)  # atomic on POSIX and Windows
    return path


def read_played_lineups(season: int, week: int, *, base_dir: Path | None = None) -> PlayedLineupsWeek:
    path = played_lineups_path(season, week, base_dir=base_dir)
    envelope = json.loads(path.read_text())
    return PlayedLineupsWeek(
        season=envelope["season"],
        week=envelope["week"],
        logged_at=envelope["logged_at"],
        lineups=tuple(
            PlayedLineup(
                label=row["label"],
                players=tuple(PlayedPlayer(**p) for p in row["players"]),
                source_agent_label=row["source_agent_label"],
                edits=tuple(LineupEdit(**e) for e in row["edits"]),
                total_salary=row["total_salary"],
            )
            for row in envelope["lineups"]
        ),
    )


def list_played_lineup_weeks(*, base_dir: Path | None = None) -> list[str]:
    """Every archived `<season>-<week>` file stem, ascending -- reads the filesystem directly (no
    index file to drift), same posture as `injury_snapshot_store.py`'s `list_snapshot_dates`."""
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    if not root.exists():
        return []
    return sorted(p.stem for p in root.glob("*.json"))
