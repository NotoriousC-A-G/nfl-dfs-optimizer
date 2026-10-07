"""Team proxy metrics from play-by-play: the numbers a pivotal question's threshold refers to.

Computed per (season, week, game, team) so the same table serves two jobs: a team's season-to-date
value (the evidence the analyst sees) and the *league distribution of single-game values* (what a
threshold's difficulty and a question's base rate are measured against). Only fields that exist in
the 2026 play-by-play are used -- no participation data (time to throw, box counts, pressure flags),
which does not exist for 2026.

Offense-side metrics are named exactly as `build.thesis.contracts.PROXY_METRICS`; defense-side
counterparts (`def_sack_rate`, `def_qb_hit_rate`: pass rush *generated*) are evidence only, not valid
question proxies. Pace is a draft proxy (seconds between consecutive offensive plays in a drive).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# metric -> (numerator column, denominator column): pooled season-to-date value = sum(num) / sum(den).
# Rates and averages are both pooled this way, so `n` is always a real play/dropback count.
RATE_PARTS: dict[str, tuple[str, str]] = {
    "sack_rate": ("sacks", "dropbacks"),
    "qb_hit_rate": ("qb_hits", "dropbacks"),
    "explosive_pass_rate": ("explosive_passes", "pass_attempts"),
    "def_sack_rate": ("def_sacks", "def_dropbacks"),
    "def_qb_hit_rate": ("def_qb_hits", "def_dropbacks"),
    "pass_epa": ("pass_epa_sum", "pass_epa_n"),
    "rush_epa": ("rush_epa_sum", "rush_epa_n"),
    "pass_rate_over_expected": ("oe_sum", "oe_n"),
    "pass_rate_over_expected_leading": ("oe_lead_sum", "oe_lead_n"),
    "pace_seconds_per_play": ("pace_sum", "pace_n"),
}
SUM_METRICS = ("total_plays", "rush_attempts_leading", "combined_pass_attempts")  # per-game averages
FLAG_METRICS = ("lead_at_q3_start", "lead_at_q4")  # share of games
DEFENSE_METRICS_ALL = ("def_sack_rate", "def_qb_hit_rate")
OFFENSE_METRICS = tuple(m for m in RATE_PARTS if m not in DEFENSE_METRICS_ALL) + SUM_METRICS + FLAG_METRICS
DEFENSE_METRICS = ("def_sack_rate", "def_qb_hit_rate")
_PACE_MAX_SECONDS = 45.0


def _pace(game_df: pd.DataFrame) -> tuple[float, int]:
    """(sum of seconds, number of timed gaps) between consecutive offensive plays in the same drive."""
    g = game_df.sort_values("play_id")
    same_drive = (g["drive"] == g["drive"].shift(1)) & (g["posteam"] == g["posteam"].shift(1))
    gap = g["game_seconds_remaining"].shift(1) - g["game_seconds_remaining"]
    gap = gap[same_drive & (gap > 0) & (gap <= _PACE_MAX_SECONDS)]
    return float(gap.sum()), int(len(gap))


def team_game_metrics(pbp: pd.DataFrame, *, season_type: str | None = "REG") -> pd.DataFrame:
    """One row per (season, week, game_id, team)."""
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]
    df = df[df["posteam"].notna() & df["play_type"].isin(["pass", "run"])].copy()
    if df.empty:
        return pd.DataFrame()
    rows = []
    for (season, week, game_id, team), g in df.groupby(["season", "week", "game_id", "posteam"]):
        db = g["qb_dropback"] == 1
        passes = g["pass_attempt"] == 1
        rush = g["rush"] == 1
        leading = g["score_differential"] > 0
        oe = g["pass_oe"]
        pass_epa = g.loc[db, "epa"].dropna()
        rush_epa = g.loc[rush, "epa"].dropna()
        oe_all = oe.dropna()
        oe_lead = oe[leading].dropna()
        pace_sum, pace_n = _pace(g)
        rows.append(
            {
                "season": season, "week": week, "game_id": game_id, "team": team,
                "dropbacks": int(db.sum()), "sacks": int((g["sack"] == 1).sum()), "qb_hits": int((g["qb_hit"] == 1).sum()),
                "pass_attempts": int(passes.sum()), "explosive_passes": int((passes & (g["yards_gained"] >= 20)).sum()),
                "pass_epa_sum": float(pass_epa.sum()), "pass_epa_n": int(len(pass_epa)),
                "rush_epa_sum": float(rush_epa.sum()), "rush_epa_n": int(len(rush_epa)),
                "oe_sum": float(oe_all.sum()), "oe_n": int(len(oe_all)),
                "oe_lead_sum": float(oe_lead.sum()), "oe_lead_n": int(len(oe_lead)),
                "pace_sum": pace_sum, "pace_n": pace_n,
                "total_plays": len(g),
                "rush_attempts_leading": int((rush & (g["score_differential"] >= 4) & (g["qtr"] >= 3)).sum()),
                "combined_pass_attempts": int(passes.sum()),  # this team's attempts
            }
        )
    out = pd.DataFrame(rows)
    # Defense: pass rush generated = what the opponent's offense suffered in the same game.
    opp = out.rename(columns={"team": "opp_team", "sacks": "def_sacks", "qb_hits": "def_qb_hits", "dropbacks": "def_dropbacks"})[
        ["game_id", "opp_team", "def_sacks", "def_qb_hits", "def_dropbacks"]
    ]
    out = out.merge(opp, on="game_id")
    out = out[out["team"] != out["opp_team"]].drop(columns="opp_team").reset_index(drop=True)
    out["sack_rate"] = out["sacks"] / out["dropbacks"].replace(0, np.nan)
    out["qb_hit_rate"] = out["qb_hits"] / out["dropbacks"].replace(0, np.nan)
    out["explosive_pass_rate"] = out["explosive_passes"] / out["pass_attempts"].replace(0, np.nan)
    out["def_sack_rate"] = out["def_sacks"] / out["def_dropbacks"].replace(0, np.nan)
    out["def_qb_hit_rate"] = out["def_qb_hits"] / out["def_dropbacks"].replace(0, np.nan)
    for metric, (num, den) in RATE_PARTS.items():  # per-game values (league distribution of single games)
        if metric not in out.columns:
            out[metric] = out[num] / out[den].replace(0, np.nan)
    out["lead_at_q3_start"] = np.nan
    out["lead_at_q4"] = np.nan
    first = df.sort_values("play_id")
    for qtr, col in ((3, "lead_at_q3_start"), (4, "lead_at_q4")):
        starts = first[first["qtr"] == qtr].groupby("game_id").first()
        for game_id, r in starts.iterrows():
            margin = r["total_home_score"] - r["total_away_score"]
            mask = out["game_id"] == game_id
            home = out.loc[mask, "team"] == r["home_team"]
            out.loc[mask, col] = np.where(home, margin > 0, margin < 0).astype(float)
    return out


def season_to_date(tg: pd.DataFrame, team: str, season: int, through_week: int) -> dict[str, tuple[float | None, int | None]]:
    """`{metric: (value, n)}` for one team, pooled over its games with week <= through_week."""
    g = tg[(tg["team"] == team) & (tg["season"] == season) & (tg["week"] <= through_week)]
    out: dict[str, tuple[float | None, int | None]] = {}
    if g.empty:
        return out
    for metric, (num, den) in RATE_PARTS.items():
        d = g[den].sum()
        out[metric] = (float(g[num].sum() / d) if d else None, int(d))
    for metric in SUM_METRICS:
        out[metric] = (float(g[metric].mean()), int(len(g)))  # per-game average
    for metric in FLAG_METRICS:
        vals = g[metric].dropna()
        out[metric] = (float(vals.mean()) if len(vals) else None, int(len(vals)))  # share of games
    return out


def league_values(tg: pd.DataFrame, metric: str) -> np.ndarray:
    """Every team-game value of `metric` in `tg` (NaNs dropped) -- the league distribution."""
    if metric not in tg.columns:
        return np.array([])
    return tg[metric].dropna().to_numpy(dtype=float)
