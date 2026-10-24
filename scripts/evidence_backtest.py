"""Does the evidence packet's defense/turnover metrics persist? Rank-correlates each team's
season-to-date value through week N-1 against that same team's week-N outcome, for every week N
from FIRST_WEEK through LAST_WEEK, then pools all (team, week) pairs.

Metrics checked (build/evidence/metrics.py): def_pass_epa, def_rush_epa (EPA allowed, per dropback /
per rush), def_takeaway_rate (turnovers forced / opp play), giveaway_rate (INT + fumbles lost / play).
A pooled rho near 0 means the evidence is noise at that sample size; the packet's own prompt already
warns turnovers are high-variance. Research only -- nothing here feeds the optimizer.

Not part of pytest (pulls nflverse pbp). Run by hand:
    PYTHONPATH=. .venv/bin/python scripts/evidence_backtest.py

SEASON/LAST_WEEK must be kept current by hand, same convention as every other live script here.
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl
import pandas as pd

from nfl_dfs.build.evidence.metrics import season_to_date, team_game_metrics

warnings.filterwarnings("ignore")

SEASON = 2026
FIRST_WEEK = 3  # need >= 2 prior games for a season-to-date value to mean anything
LAST_WEEK = 6

# (pre-week metric, same-team week outcome column it should predict, label)
CHECKS = [
    ("def_pass_epa", "def_pass_epa", "EPA allowed per dropback"),
    ("def_rush_epa", "def_rush_epa", "EPA allowed per rush"),
    ("def_takeaway_rate", "def_takeaways", "defense takeaways"),
    ("giveaway_rate", "giveaways", "offense giveaways"),
]


def _outcome(row: pd.Series, col: str) -> float | None:
    if col in ("def_pass_epa", "def_rush_epa"):
        n = row[f"{col}_n"]
        return row[f"{col}_sum"] / n if n else None
    return float(row[col])


def _rho(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    """Spearman rho and its t statistic (|t| > ~2 is roughly p < .05)."""
    r = x.rank().corr(y.rank())
    n = len(x)
    return r, r * ((n - 2) / (1 - r * r)) ** 0.5


def main() -> None:
    pbp = nfl.import_pbp_data([SEASON], include_participation=False)
    tg = team_game_metrics(pbp)

    records = []
    for week in range(FIRST_WEEK, LAST_WEEK + 1):
        for _, game in tg[(tg["season"] == SEASON) & (tg["week"] == week)].iterrows():
            pre = season_to_date(tg, game["team"], SEASON, week - 1)
            rec = {"week": week, "team": game["team"]}
            for metric, outcome_col, _ in CHECKS:
                rec[metric] = pre.get(metric, (None, None))[0]
                rec[f"out_{metric}"] = _outcome(game, outcome_col)
            records.append(rec)
    df = pd.DataFrame(records)

    print(f"{SEASON} weeks {FIRST_WEEK}-{LAST_WEEK}: {len(df)} team-weeks\n")
    print(f"{'metric (through wk N-1 -> wk N outcome)':<44}" + "".join(f"wk{w:<6}" for w in range(FIRST_WEEK, LAST_WEEK + 1)) + "pooled")
    for metric, _, label in CHECKS:
        cells = []
        for week in range(FIRST_WEEK, LAST_WEEK + 1):
            w = df[df["week"] == week].dropna(subset=[metric, f"out_{metric}"])
            cells.append(f"{_rho(w[metric], w[f'out_{metric}'])[0]:+.2f}   " if len(w) > 4 else "  n/a  ")
        p = df.dropna(subset=[metric, f"out_{metric}"])
        r, t = _rho(p[metric], p[f"out_{metric}"])
        print(f"{metric + ' -> ' + label:<44}" + "".join(f"{c:<8}" for c in cells) + f"{r:+.2f} (t={t:+.1f}, n={len(p)})")


if __name__ == "__main__":
    main()
