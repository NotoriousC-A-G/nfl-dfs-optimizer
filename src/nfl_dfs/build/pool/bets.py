"""Can the declared bets all be in ONE lineup? An exact roster-shape check (Chris, 2026-10-09: bets are required in the lineup, so the expert
must not declare more than a DraftKings roster can hold).

DraftKings classic: 1 QB, 2-3 RB, 3-4 WR, 1-2 TE, 1 DST, one FLEX from RB/WR/TE, nine players, $50,000. The union of a variation's bet players has to
fit that shape and leave salary for the rest.
"""

from __future__ import annotations

from collections import Counter

from nfl_dfs.build.pool.contracts import PlayerRef
from nfl_dfs.optimizer.lineup import SALARY_CAP

MAX_BY_POSITION = {"QB": 1, "RB": 3, "WR": 4, "TE": 2, "DST": 1}
ROSTER = 9
MIN_FILL_SALARY = 2_000  # nobody on a DK slate costs less than this per remaining slot (a conservative floor)


def roster_shape_problems(players: list[PlayerRef]) -> list[str]:
    """Human-readable reasons these players cannot all be in one legal lineup (empty = they can)."""
    out = []
    counts = Counter(p.position for p in players)
    for pos, cap in MAX_BY_POSITION.items():
        if counts.get(pos, 0) > cap:
            names = ", ".join(p.name for p in players if p.position == pos)
            out.append(f"{counts[pos]} {pos} declared but a lineup holds at most {cap}: {names}")
    if len(players) > ROSTER:
        out.append(f"{len(players)} players declared but a lineup holds {ROSTER}")
    flex_pool = counts.get("RB", 0) + counts.get("WR", 0) + counts.get("TE", 0)
    if flex_pool > 7:
        out.append(f"{flex_pool} RB/WR/TE declared but a lineup holds at most 7 (RB+WR+TE incl. FLEX)")
    spent = sum(p.salary or 0 for p in players)
    floor = spent + max(0, ROSTER - len(players)) * MIN_FILL_SALARY
    if floor > SALARY_CAP:
        out.append(f"the declared players cost ${spent:,} and the {max(0, ROSTER - len(players))} remaining slots need at least ${max(0, ROSTER - len(players)) * MIN_FILL_SALARY:,} -- over the ${SALARY_CAP:,} cap")
    return out
