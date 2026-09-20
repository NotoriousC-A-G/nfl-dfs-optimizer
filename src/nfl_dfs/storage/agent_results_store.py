"""Agent performance archive -- the real build of Phase C's "track performance for each [agent]"
ask (2026-09-20, Chris), and the direct NFL port of the sister MLB project's own precedent
(`mlb_dfs/tracking/agent_results.py`, read directly this round to confirm the real schema/
mechanics rather than assume them).

**One CSV, one schema, every agent (including Chris himself).** MLB's own key finding, reused
verbatim here: `collect_operator_agent_result()` writes the operator's actually-played lineup into
the SAME `agent_results.csv`, same fields, under `agent_id="operator"` -- "Lets the operator's
actual play get ranked/percentiled alongside the [N] AgentConstructor agents." Chris's request
("These should [be] logged under the operator with the agents") is exactly that pattern: this
store is the one place both the 6 `NflAgentConstructor` agents' generated lineups AND Chris's own
played lineups live, so they can be compared directly.

**NFL adaptation of MLB's grouping key.** MLB groups rows by `(date, slate_id)` since a slate is
identified by a vendor slate ID and multiple slates can exist per day. NFL's live pipeline has no
persisted slate_id today (confirmed -- `dashboard_output/` holds only rendered HTML, no slate
metadata artifact), and Chris's own convention is one main slate per week, so `(season, week)` is
the grouping key here instead. `agent_id` stays the real `NflAgentConstructor.agent_id` slug
(`"chalk_anchor"`, ..., `"volatility_engine"`) for the 6 agents, or the literal `"operator"` for
Chris. `strategy_name` carries the human-readable label -- the agent's `display_name` for the 6, or
Chris's own L1/L2/L3 label for the operator (NFL, unlike MLB's single daily lock, allows multiple
real entries per week, so `strategy_name` is what disambiguates operator rows, not `agent_id`).

**Deliberately NOT built here (disclosed, not silently dropped):** `total_dk_score`, `boom`, and
`lineup_rank` are written as blank/`None` at forward-log time -- there is no NFL box-score join
mechanism yet analogous to MLB's `box_scores.compute_dk_points` (`_compute_dk_score` in MLB's own
module), so nothing here can compute a real settled score before games finish. A future
results-collection step (this project's own real ResultsDB, once a week's games settle) is the
right place to backfill those three fields -- the same "forward log now, join later" split MLB
itself uses (`--collect-results` runs after `journal.attach_outcome()`), not designed in this round.

**Filesystem/CSV is the source of truth, append-only, idempotent on `(season, week, agent_id,
strategy_name)`** -- same posture as MLB's own `save_agent_results`, adapted for the NFL grouping
key above.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_RESULTS_DIR = _REPO_ROOT / "data" / "agent-performance"
AGENT_RESULTS_PATH = AGENT_RESULTS_DIR / "agent_results.csv"

FIELDNAMES = [
    "season",
    "week",
    "agent_id",
    "strategy_name",
    "total_dk_score",  # settled actual score -- blank until a future results-collection step
    "proj_total",
    "salary",
    "boom",  # 1/0 -- blank until total_dk_score is known (see module docstring)
    "lineup_rank",  # rank by total_dk_score within (season, week) -- blank until scored
    "players",  # pipe-separated "Name (POS-TEAM)"
]


@dataclass(frozen=True)
class AgentResultRow:
    """One agent's (or the operator's own) lineup for one week, forward-logged at generation/
    entry time. `total_dk_score`/`boom`/`lineup_rank` are `None` until a later results-collection
    step backfills them from settled scores."""

    season: int
    week: int
    agent_id: str  # a real NflAgentConstructor.agent_id slug, or "operator"
    strategy_name: str  # agent display_name, or the operator's own L1/L2/L3 label
    proj_total: float
    salary: int
    players: tuple[str, ...]  # "Name (POS-TEAM)" per player, in roster order
    total_dk_score: float | None = None
    boom: bool | None = None
    lineup_rank: int | None = None


def _row_to_csv_dict(row: AgentResultRow) -> dict[str, str]:
    return {
        "season": str(row.season),
        "week": str(row.week),
        "agent_id": row.agent_id,
        "strategy_name": row.strategy_name,
        "total_dk_score": "" if row.total_dk_score is None else str(row.total_dk_score),
        "proj_total": str(row.proj_total),
        "salary": str(row.salary),
        "boom": "" if row.boom is None else str(int(row.boom)),
        "lineup_rank": "" if row.lineup_rank is None else str(row.lineup_rank),
        "players": "|".join(row.players),
    }


def _csv_dict_to_row(d: dict[str, str]) -> AgentResultRow:
    return AgentResultRow(
        season=int(d["season"]),
        week=int(d["week"]),
        agent_id=d["agent_id"],
        strategy_name=d["strategy_name"],
        proj_total=float(d["proj_total"]),
        salary=int(d["salary"]),
        players=tuple(d["players"].split("|")) if d["players"] else (),
        total_dk_score=float(d["total_dk_score"]) if d["total_dk_score"] else None,
        boom=bool(int(d["boom"])) if d["boom"] else None,
        lineup_rank=int(d["lineup_rank"]) if d["lineup_rank"] else None,
    )


def save_agent_results(rows: list[AgentResultRow], *, path: Path | None = None) -> Path | None:
    """Append rows to agent_results.csv. Idempotent on `(season, week, agent_id, strategy_name)`
    -- same posture as MLB's own `save_agent_results`. Returns `None` (writes nothing) if `rows`
    is empty."""
    if not rows:
        return None

    results_path = path if path is not None else AGENT_RESULTS_PATH
    results_path.parent.mkdir(parents=True, exist_ok=True)

    existing_keys: set[tuple[str, str, str, str]] = set()
    if results_path.exists():
        with open(results_path, newline="") as f:
            for existing in csv.DictReader(f):
                existing_keys.add(
                    (existing["season"], existing["week"], existing["agent_id"], existing["strategy_name"])
                )

    fresh = [
        row
        for row in rows
        if (str(row.season), str(row.week), row.agent_id, row.strategy_name) not in existing_keys
    ]
    if not fresh:
        return results_path

    write_header = not results_path.exists()
    with open(results_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in fresh:
            writer.writerow(_row_to_csv_dict(row))

    return results_path


def read_agent_results(
    *, season: int | None = None, week: int | None = None, path: Path | None = None
) -> list[AgentResultRow]:
    """Every row on disk, optionally filtered by season and/or week. Reads the filesystem directly
    -- no separate index to drift, same posture as this project's other storage modules."""
    results_path = path if path is not None else AGENT_RESULTS_PATH
    if not results_path.exists():
        return []

    with open(results_path, newline="") as f:
        rows = [_csv_dict_to_row(d) for d in csv.DictReader(f)]

    if season is not None:
        rows = [r for r in rows if r.season == season]
    if week is not None:
        rows = [r for r in rows if r.week == week]
    return rows
