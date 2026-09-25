"""Backtest: is a team's DST fantasy output correlated with its own offense's? (ADR-0039)

Reads the curated ResultsDB `player_exposures` tables (real DK `actual_points`, one main-slate
contest per date, 2020-2025) -- no network. For every team-slate with both a QB and a DST it
correlates DST points against the team's QB, top-3 WR/TE (by salary), and full-offense points, plus
a control that shuffles DST points across teams within the same slate. Run by hand:

    PYTHONPATH=. .venv/bin/python scripts/dst_correlation_backtest.py

Note ResultsDB codes the DST position as "D". Only the same-team relationship is tested here; the
opposing-DST penalty (PRD Section 7) would additionally need a schedule join.
"""

from __future__ import annotations

import glob

import numpy as np
import pandas as pd

OFFENSE = ["QB", "RB", "WR", "TE"]


def build_team_slates() -> pd.DataFrame:
    files = sorted(glob.glob("data/curated/resultsdb/nfl/season=*/player_exposures/*.parquet"))
    d = pd.concat([pd.read_parquet(f) for f in files])
    d = d[d.actual_points.notna()]
    rows = []
    for (date, _cid), g in d.groupby(["date", "contest_id"]):
        for team, dst_pts in g[g.position == "D"].set_index("team").actual_points.items():
            s = g[g.team == team]
            qb = s[s.position == "QB"].sort_values("salary", ascending=False).head(1).actual_points
            if qb.empty:
                continue
            pc = s[s.position.isin(["WR", "TE"])].sort_values("salary", ascending=False).head(3)
            rows.append(
                {
                    "date": str(date),
                    "team": team,
                    "dst": dst_pts,
                    "qb": qb.iloc[0],
                    "pass_catchers": pc.actual_points.sum(),
                    "offense": s[s.position.isin(OFFENSE)].actual_points.sum(),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    r = build_team_slates()
    n = len(r)
    print(f"{n} team-slates across {r.date.nunique()} slates\n")
    for col, label in [("qb", "QB"), ("pass_catchers", "top-3 WR/TE"), ("offense", "team offense")]:
        c = r[col].corr(r.dst)
        ci = 1.96 * (1 - c**2) / np.sqrt(n - 3)
        print(f"  {label:14s} vs own DST: r={c:+.3f}  (95% CI +/-{ci:.3f})")
    rng = np.random.default_rng(0)
    control = [
        r.qb.corr(r.groupby("date").dst.transform(lambda s: rng.permutation(s.values)))
        for _ in range(200)
    ]
    print(f"  control (QB vs shuffled DST, same slate): r={np.mean(control):+.3f} sd={np.std(control):.3f}")


if __name__ == "__main__":
    main()
