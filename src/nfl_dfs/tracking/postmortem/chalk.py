"""Chalk-proxy lineup comparison -- direct port of the sister MLB project's own `build_chalk_
lineup` (`mlb_dfs/tracking/postmortem/outcomes.py`, read directly before porting): "greedy top-
own-per-position fails salary cap on a real fraction of slates; an ownership-weighted MILP gives
the feasible lineup the real field actually gravitates toward." Same approach here, over this
project's own real roster shape and its own real, already-built ownership data (ADR-0023/0024/
0025/0026's live chalk/leverage layer) -- NOT a scope MLB had to invent for this port, since NFL
already had the ownership signal MLB's own `proj_own` field needed.

Reuses the exact real roster-shape constants `optimizer/lineup.py` already enforces on every
generated lineup (`SALARY_CAP`, `ROSTER_SIZE`, the RB/WR/TE min-max bands, `FLEX_GROUP_TOTAL`) --
a same-layer import, not a cross-layer duplication (unlike `_pool_iqr`'s justified duplication
between `optimizer/lineup.py` and `agents/scoring.py`, `tracking/` sits ABOVE `optimizer/`, so
importing its real constants here can't create a circular or layering violation).
"""

from __future__ import annotations

import pulp

from nfl_dfs.optimizer.lineup import (
    FLEX_GROUP_TOTAL,
    MAX_RB,
    MAX_TE,
    MAX_WR,
    MIN_RB,
    MIN_TE,
    MIN_WR,
    ROSTER_SIZE,
    SALARY_CAP,
)
from nfl_dfs.tracking.postmortem.models import ChalkComparison, LineupOutcome, PlayerOutcome

_MIN_OWNERSHIP_COVERAGE_FRACTION = 0.3  # same threshold MLB's own build_chalk_lineup uses


def build_chalk_lineup(
    player_pool: list[dict],
    actual_points_by_canonical_id: dict[str, float],
    *,
    our_best: LineupOutcome | None = None,
) -> ChalkComparison:
    viable_rows = []
    for row in player_pool:
        identity = row.get("identity") or {}
        canonical_id = identity.get("canonical_id")
        salary = row.get("salary")
        position = row.get("position")
        if not canonical_id or not salary or position not in ("QB", "RB", "WR", "TE", "DST"):
            continue
        ownership = row.get("ownership") or {}
        viable_rows.append(
            {
                "canonical_id": canonical_id,
                "name": identity.get("display_name", canonical_id),
                "team": row.get("team", ""),
                "position": position,
                "salary": salary,
                "projected_ownership": ownership.get("projected_ownership") or 0.0,
                "projected": row.get("projection") or 0.0,
            }
        )

    if not viable_rows:
        return ChalkComparison(infeasible=True, reason="No viable players in the snapshot's player pool")

    with_ownership = sum(1 for r in viable_rows if r["projected_ownership"] > 0)
    if with_ownership < len(viable_rows) * _MIN_OWNERSHIP_COVERAGE_FRACTION:
        return ChalkComparison(
            infeasible=True,
            reason=f"Ownership data too sparse ({with_ownership}/{len(viable_rows)} players have real ownership)",
        )

    prob = pulp.LpProblem("ChalkProxy", pulp.LpMaximize)
    x = {r["canonical_id"]: pulp.LpVariable(f"x_{i}", cat="Binary") for i, r in enumerate(viable_rows)}

    prob += pulp.lpSum(r["projected_ownership"] * x[r["canonical_id"]] for r in viable_rows)
    prob += pulp.lpSum(x[pid] for pid in x) == ROSTER_SIZE
    prob += pulp.lpSum(r["salary"] * x[r["canonical_id"]] for r in viable_rows) <= SALARY_CAP

    def count(position: str):
        return pulp.lpSum(x[r["canonical_id"]] for r in viable_rows if r["position"] == position)

    prob += count("QB") == 1
    prob += count("DST") == 1
    prob += count("RB") >= MIN_RB
    prob += count("RB") <= MAX_RB
    prob += count("WR") >= MIN_WR
    prob += count("WR") <= MAX_WR
    prob += count("TE") >= MIN_TE
    prob += count("TE") <= MAX_TE
    prob += count("RB") + count("WR") + count("TE") == FLEX_GROUP_TOTAL

    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    if pulp.LpStatus[prob.status] != "Optimal":
        return ChalkComparison(infeasible=True, reason=f"Chalk MILP solve failed (status={pulp.LpStatus[prob.status]})")

    selected_rows = [r for r in viable_rows if x[r["canonical_id"]].varValue and x[r["canonical_id"]].varValue > 0.5]

    players = tuple(
        PlayerOutcome(
            canonical_id=r["canonical_id"],
            display_name=r["name"],
            team=r["team"],
            position=r["position"],
            salary=r["salary"],
            projected=r["projected"],
            actual=actual_points_by_canonical_id.get(r["canonical_id"]),
            delta=(
                actual_points_by_canonical_id[r["canonical_id"]] - r["projected"]
                if r["canonical_id"] in actual_points_by_canonical_id
                else None
            ),
        )
        for r in selected_rows
    )
    unresolved = tuple(p.display_name for p in players if p.actual is None)
    projected_total = sum(p.projected for p in players)
    actual_total = None if unresolved else sum(p.actual for p in players)
    chalk_lineup = LineupOutcome(
        agent_id="chalk_proxy",
        label="Chalk Proxy",
        players=players,
        projected_total=round(projected_total, 2),
        actual_total=round(actual_total, 2) if actual_total is not None else None,
        delta=round(actual_total - projected_total, 2) if actual_total is not None else None,
        unresolved_players=unresolved,
    )

    our_actual = our_best.actual_total if our_best is not None else None
    differentiators: list[tuple[str, str, float]] = []
    if our_best is not None and chalk_lineup.actual_total is not None and our_best.actual_total is not None:
        chalk_by_position: dict[str, list[PlayerOutcome]] = {}
        for p in players:
            chalk_by_position.setdefault(p.position, []).append(p)
        chalk_ids = {p.canonical_id for p in players}
        for our_p in our_best.players:
            if our_p.canonical_id in chalk_ids or our_p.actual is None:
                continue
            candidates = chalk_by_position.get(our_p.position, [])
            if not candidates or candidates[0].actual is None:
                continue
            chalk_p = candidates[0]
            differentiators.append((our_p.display_name, chalk_p.display_name, round(our_p.actual - chalk_p.actual, 2)))

    delta = None
    beat_chalk = None
    if our_actual is not None and chalk_lineup.actual_total is not None:
        delta = round(our_actual - chalk_lineup.actual_total, 2)
        beat_chalk = delta > 0

    return ChalkComparison(
        chalk_lineup=chalk_lineup,
        chalk_actual=chalk_lineup.actual_total,
        our_actual=our_actual,
        delta=delta,
        beat_chalk=beat_chalk,
        differentiators=differentiators,
    )
