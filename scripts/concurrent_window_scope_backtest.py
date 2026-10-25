"""Backtest for ADR-0020 Amendment 1: should the ConcurrentActivityWindow drop a blanked RB's missed
weeks from the WHOLE backfield (original, `team` scope) or only from that player's own share and week
count (`player` scope)?

**Methodology:** for every (season, target_week) and team where the RB ConcurrentActivityWindow
fires (an established RB blanked in some trailing week), compute each RB candidate's blended
`RoleShare` under both scopes using only weeks `1..target_week-1`, and compare it to that candidate's
REAL carry share in `target_week` itself (only candidates who carried the ball that week are scored).
Errors are reported separately for (a) the blanked player himself vs. (b) every other RB, and split
by whether the blanked player was still out in `target_week` (the Bam Knight / Jeremiyah Love case:
the absent lead is still absent, so the absence week is exactly the informative one) or back.

NOT part of `pytest` -- public nflverse data only (no vendor credentials), run by hand:

    PYTHONPATH=. .venv/bin/python scripts/concurrent_window_scope_backtest.py
"""

from __future__ import annotations

import warnings

import nfl_data_py as nfl
import numpy as np
import pandas as pd

from nfl_dfs.ingestion.usage_share import (
    CONCURRENT_WINDOW_SCOPE_PLAYER,
    CONCURRENT_WINDOW_SCOPE_TEAM,
    ROLE_RB,
    _rb_concurrent_activity_blanked_weeks,
    aggregate_passer_week,
    aggregate_player_week,
    aggregate_team_week_volume,
    compute_role_shares_for_week,
)

SEASONS = [2021, 2022, 2023, 2024, 2025]
MIN_TARGET_WEEK = 4
MIN_ACTUAL_TEAM_RUSHES = 10


def main() -> None:
    warnings.filterwarnings("ignore")
    records = []
    for season in SEASONS:
        pbp = nfl.import_pbp_data([season], include_participation=False)
        player_week = aggregate_player_week(pbp)
        team_week = aggregate_team_week_volume(pbp)
        passer_week = aggregate_passer_week(pbp)
        max_week = int(player_week["week"].max())
        for target in range(MIN_TARGET_WEEK, max_week + 1):
            trailing = player_week[player_week["week"] < target]
            trailing_tw = team_week[team_week["week"] < target]
            actual = player_week[(player_week["week"] == target) & (player_week["role"] == ROLE_RB)]
            by_scope = {
                scope: {
                    r.team: r
                    for r in compute_role_shares_for_week(
                        player_week, team_week, passer_week, season, target, concurrent_window_scope=scope
                    )
                    if r.role == ROLE_RB
                }
                for scope in (CONCURRENT_WINDOW_SCOPE_TEAM, CONCURRENT_WINDOW_SCOPE_PLAYER)
            }
            for team in sorted(by_scope[CONCURRENT_WINDOW_SCOPE_TEAM]):
                team_rows = trailing[trailing["team"] == team]
                weeks = sorted(trailing_tw.loc[trailing_tw["team"] == team, "week"].unique().tolist())
                blanked = _rb_concurrent_activity_blanked_weeks(team_rows, weeks)
                if not blanked:
                    continue
                act = actual[actual["team"] == team].set_index("player_id")["share"]
                if act.empty or team_week[(team_week["week"] == target) & (team_week["team"] == team)].empty:
                    continue
                target_week_rows = player_week[(player_week["week"] == target) & (player_week["team"] == team)]
                active_ids = set(target_week_rows["player_id"])
                cand_t = {c.player_id: c for c in by_scope[CONCURRENT_WINDOW_SCOPE_TEAM][team].candidates}
                cand_p = {c.player_id: c for c in by_scope[CONCURRENT_WINDOW_SCOPE_PLAYER][team].candidates}
                for pid, share in act.items():
                    if pid not in cand_t or pid not in cand_p:
                        continue
                    # blanked player still absent at target = none of the blanked ids appear that week
                    still_out = all(b not in active_ids for b in blanked)
                    records.append(
                        {
                            "season": season,
                            "week": target,
                            "team": team,
                            "player": pid,
                            "is_blanked_player": pid in blanked,
                            "blanked_still_out": still_out,
                            "actual": share,
                            "pred_team": cand_t[pid].role_share_blended,
                            "pred_player": cand_p[pid].role_share_blended,
                        }
                    )
    df = pd.DataFrame(records)
    df["err_team"] = (df["pred_team"] - df["actual"]).abs()
    df["err_player"] = (df["pred_player"] - df["actual"]).abs()
    df["bias_team"] = df["pred_team"] - df["actual"]
    df["bias_player"] = df["pred_player"] - df["actual"]

    def report(name: str, sub: pd.DataFrame) -> None:
        if sub.empty:
            print(f"{name}: n=0")
            return
        diff = sub["err_team"] - sub["err_player"]
        print(
            f"{name}: n={len(sub)}  MAE team-scope={sub['err_team'].mean():.4f}  "
            f"player-scope={sub['err_player'].mean():.4f}  bias team={sub['bias_team'].mean():+.4f} "
            f"player={sub['bias_player'].mean():+.4f}  player-scope better in {(diff > 0).mean():.0%}"
            f" (paired diff mean {diff.mean():+.4f}, se {diff.std() / np.sqrt(len(sub)):.4f})"
        )

    print(f"seasons {SEASONS}, team-weeks with an RB exclusion event scored: {len(df)} player-rows")
    report("ALL", df)
    report("blanked player himself", df[df["is_blanked_player"]])
    report("OTHER RBs", df[~df["is_blanked_player"]])
    for still_out in (True, False):
        tag = "blanked player STILL OUT at target" if still_out else "blanked player BACK at target"
        sub = df[df["blanked_still_out"] == still_out]
        report(f"[{tag}] ALL", sub)
        report(f"[{tag}] blanked player himself", sub[sub["is_blanked_player"]])
        report(f"[{tag}] OTHER RBs", sub[~sub["is_blanked_player"]])
    # RBs with enough meat only (the ones lineups care about)
    report("OTHER RBs, actual share >= 0.25", df[(~df["is_blanked_player"]) & (df["actual"] >= 0.25)])
    report(
        "OTHER RBs STILL OUT, actual share >= 0.25",
        df[(~df["is_blanked_player"]) & df["blanked_still_out"] & (df["actual"] >= 0.25)],
    )


if __name__ == "__main__":
    main()
