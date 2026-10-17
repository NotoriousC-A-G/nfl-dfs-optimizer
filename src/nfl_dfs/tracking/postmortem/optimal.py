"""The best lineup that was possible this week -- perfect hindsight under the real DK roster shape
and $50K cap -- and how close each of our lineups got to it (MLB's post-mortem has the same
"optimal" view; this is the NFL port, over `optimizer/lineup.py`'s own roster constants like
`chalk.py`).

Candidates are players with a real settled score. A player with none (inactive/no stat line)
would score 0 and so never belongs in an optimal lineup; a name-match miss would only make the
optimum conservative, and `unresolved_count` surfaces how many pool players were left out.
Ties in points break toward the lower salary, so the result is deterministic.
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
from nfl_dfs.tracking.postmortem.models import LineupOutcome, OptimalComparison, PlayerOutcome

_SALARY_TIEBREAK = 1e-6  # per dollar; far below the 0.02-pt granularity of DK scoring


def compute_optimal_lineup(
    player_pool: list[dict], actual_by_id: dict[str, float], our_lineups: tuple[LineupOutcome, ...] = ()
) -> OptimalComparison:
    rows = []
    for row in player_pool:
        identity = row.get("identity") or {}
        cid = identity.get("canonical_id")
        if not cid or not row.get("salary") or row.get("position") not in ("QB", "RB", "WR", "TE", "DST"):
            continue
        if cid not in actual_by_id:
            continue
        rows.append(
            {
                "id": cid,
                "name": identity.get("display_name", cid),
                "team": row.get("team", ""),
                "position": row["position"],
                "salary": row["salary"],
                "projected": row.get("projection") or 0.0,
                "actual": actual_by_id[cid],
            }
        )
    unresolved_count = sum(1 for r in player_pool if (r.get("identity") or {}).get("canonical_id") not in actual_by_id)
    if not rows:
        return OptimalComparison(infeasible=True, reason="No players with a real settled score")

    prob = pulp.LpProblem("OptimalHindsight", pulp.LpMaximize)
    x = {r["id"]: pulp.LpVariable(f"x_{i}", cat="Binary") for i, r in enumerate(rows)}
    prob += pulp.lpSum((r["actual"] - _SALARY_TIEBREAK * r["salary"]) * x[r["id"]] for r in rows)
    prob += pulp.lpSum(x.values()) == ROSTER_SIZE
    prob += pulp.lpSum(r["salary"] * x[r["id"]] for r in rows) <= SALARY_CAP

    def count(position: str):
        return pulp.lpSum(x[r["id"]] for r in rows if r["position"] == position)

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
        return OptimalComparison(infeasible=True, reason=f"Optimal MILP solve failed (status={pulp.LpStatus[prob.status]})")

    chosen = [r for r in rows if x[r["id"]].varValue and x[r["id"]].varValue > 0.5]
    players = tuple(
        PlayerOutcome(
            canonical_id=r["id"], display_name=r["name"], team=r["team"], position=r["position"],
            salary=r["salary"], projected=r["projected"], actual=r["actual"], delta=r["actual"] - r["projected"],
        )
        for r in sorted(chosen, key=lambda r: (("QB", "RB", "WR", "TE", "DST").index(r["position"]), -r["actual"]))
    )
    total = round(sum(p.actual for p in players), 2)
    projected_total = round(sum(p.projected for p in players), 2)
    lineup = LineupOutcome(
        agent_id="optimal", label="Optimal Lineup", players=players,
        projected_total=projected_total, actual_total=total, delta=round(total - projected_total, 2),
    )
    fractions = tuple(
        (lo.label, lo.actual_total, round(lo.actual_total / total, 4))
        for lo in sorted((lo for lo in our_lineups if lo.actual_total is not None), key=lambda lo: -lo.actual_total)
    ) if total > 0 else ()
    return OptimalComparison(lineup=lineup, optimal_total=total, fractions=fractions, unresolved_count=unresolved_count)
