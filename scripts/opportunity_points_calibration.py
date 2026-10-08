"""Fit DK points per carry (RB) and per target (RB/WR/TE) from real box scores and save them for the build.

    PYTHONPATH=. .venv/bin/python scripts/opportunity_points_calibration.py

Writes data/cache/opportunity_points.json. Reads nflverse's `stats_player` weekly files for the seasons below (network). NOT part of pytest.
"""

from __future__ import annotations

from pathlib import Path

from nfl_dfs.build.value.opportunity_points import fit_rates
from nfl_dfs.ingestion.offense_actual_scoring import fetch_weekly_player_stats
import pandas as pd

SEASONS = (2022, 2023, 2024, 2025)
OUT = Path("data/cache/opportunity_points.json")


def main() -> None:
    weekly = pd.concat([fetch_weekly_player_stats(s) for s in SEASONS], ignore_index=True)
    rates = fit_rates(weekly, seasons=SEASONS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(rates.to_json())
    print(f"Fitted on {SEASONS}: {rates.n_carries_rb:,} RB carries, targets {rates.n_targets}")
    print(f"  DK points per RB carry: {rates.per_carry_rb:.3f}")
    for pos, v in rates.per_target.items():
        print(f"  DK points per {pos} target: {v:.3f}")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
