"""Calibrated tail value inputs, fitted from six seasons of real contest history.

RotoGrinders ResultsDB (ADR-0023/0024) stores, for ~70 slates in 2020-2025, every player's position,
salary, a vendor projection and the ACTUAL DK points. That is thousands of observations per position,
so a *cell* statistic -- not a per-player one built from ~17 noisy games -- is estimable: for each
position and projection tier we measure how actual points relate to the projection:

- `mean_ratio` = mean(actual) / mean(projection)  -- calibrates the mean (and is where regression to the
  mean for high-projection players shows up as a ratio below 1);
- `q90_ratio`  = q90(actual)  / mean(projection)  -- the right tail, as an empirical quantile of a large
  cell (the model-analytics review's objection was to q90 from ~17 games, not to q90 from thousands).

A player's calibrated mean is `projection x mean_ratio` and his q90 is `projection x q90_ratio`. These
are disclosed-draft, vendor-projection-based calibrations applied to our blended projection: validated
out of sample by `holdout_check` (fit on earlier seasons, tested on the latest), not backtested against
our own projections (which are not in ResultsDB).
"""

from __future__ import annotations

import glob
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CURATED_ROOT = _REPO_ROOT / "data" / "curated" / "resultsdb" / "nfl"
POSITIONS = ("QB", "RB", "WR", "TE", "DST")
MIN_PROJECTION = 3.0  # below this a "projection" is a placeholder row, not a real expectation
N_BINS = 5
MIN_CELL_N = 150


@dataclass(frozen=True)
class CellStat:
    lo: float  # projection tier lower bound (inclusive); the first cell is open below
    hi: float  # upper bound (exclusive); the last cell is open above
    n: int
    mean_ratio: float
    q90_ratio: float


@dataclass(frozen=True)
class CalibrationTable:
    cells: dict[str, tuple[CellStat, ...]]
    fitted_seasons: tuple[int, ...] = ()

    def lookup(self, position: str, projection: float) -> CellStat | None:
        cells = self.cells.get(position)
        if not cells:
            return None
        for i, c in enumerate(cells):
            last = i == len(cells) - 1
            if (i == 0 or projection >= c.lo) and (last or projection < c.hi):
                return c
        return cells[-1]

    def to_json(self) -> str:
        return json.dumps({"fitted_seasons": list(self.fitted_seasons), "cells": {p: [asdict(c) for c in cs] for p, cs in self.cells.items()}})

    @staticmethod
    def from_json(blob: str) -> "CalibrationTable":
        d = json.loads(blob)
        return CalibrationTable({p: tuple(CellStat(**c) for c in cs) for p, cs in d["cells"].items()}, tuple(d.get("fitted_seasons", ())))


def load_resultsdb_player_rows(seasons: list[int] | None = None, root: Path | None = None) -> pd.DataFrame:
    """Every ResultsDB player-exposure row (one primary contest per slate date) with the columns this
    module needs; empty frame if nothing is on disk."""
    root = root or DEFAULT_CURATED_ROOT
    frames = []
    for path in sorted(glob.glob(str(root / "season=*" / "player_exposures" / "*.parquet"))):
        season = int(path.split("season=")[1].split("/")[0])
        if seasons is not None and season not in seasons:
            continue
        df = pd.read_parquet(path, columns=["date", "position", "salary", "projected_points", "actual_points"])
        df["season"] = season
        df["position"] = df["position"].replace({"D": "DST"})  # ResultsDB labels the defense "D"
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "position", "salary", "projected_points", "actual_points", "season"])


def _usable(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["position"].isin(POSITIONS) & (df["projected_points"] >= MIN_PROJECTION) & df["actual_points"].notna()]
    return d


def fit_cells(df: pd.DataFrame, *, n_bins: int = N_BINS, min_cell_n: int = MIN_CELL_N) -> CalibrationTable:
    d = _usable(df)
    cells: dict[str, tuple[CellStat, ...]] = {}
    for pos in POSITIONS:
        g = d[d["position"] == pos]
        if len(g) < min_cell_n:
            continue
        bins = max(1, min(n_bins, len(g) // min_cell_n))
        edges = np.unique(np.quantile(g["projected_points"], np.linspace(0, 1, bins + 1)[1:-1])) if bins > 1 else np.array([])
        idx = np.searchsorted(edges, g["projected_points"].to_numpy(), side="right")
        out = []
        for b in range(len(edges) + 1):
            m = idx == b
            if m.sum() == 0:
                continue
            proj_mean = float(g["projected_points"].to_numpy()[m].mean())
            act = g["actual_points"].to_numpy()[m]
            lo = -np.inf if b == 0 else float(edges[b - 1])
            hi = np.inf if b == len(edges) else float(edges[b])
            out.append(CellStat(lo, hi, int(m.sum()), float(act.mean() / proj_mean), float(np.quantile(act, 0.9) / proj_mean)))
        cells[pos] = tuple(out)
    seasons = tuple(sorted(int(s) for s in d["season"].unique())) if "season" in d.columns else ()
    return CalibrationTable(cells, seasons)


def holdout_check(df: pd.DataFrame, *, test_season: int) -> dict:
    """Fit on every season BEFORE `test_season`, test on `test_season`. Reports: raw projection bias
    (sum actual / sum projected), calibrated-mean bias (should be ~1.0), and the share of actuals that
    exceed the predicted q90 (should be ~10%), overall and by position."""
    train = df[df["season"] < test_season]
    test = _usable(df[df["season"] == test_season])
    table = fit_cells(train)
    rows = []
    for r in test.itertuples(index=False):
        c = table.lookup(r.position, r.projected_points)
        if c is None:
            continue
        rows.append((r.position, r.projected_points, r.actual_points, r.projected_points * c.mean_ratio, r.projected_points * c.q90_ratio))
    t = pd.DataFrame(rows, columns=["position", "proj", "act", "mu", "q90"])
    if t.empty:
        return {"n": 0}
    out = {
        "n": len(t), "train_seasons": sorted(int(s) for s in train["season"].unique()), "test_season": test_season,
        "raw_bias": float(t["act"].sum() / t["proj"].sum()), "calibrated_bias": float(t["act"].sum() / t["mu"].sum()),
        "share_above_q90": float((t["act"] >= t["q90"]).mean()), "by_position": {},
    }
    for pos, g in t.groupby("position"):
        out["by_position"][pos] = {
            "n": len(g), "raw_bias": float(g["act"].sum() / g["proj"].sum()),
            "calibrated_bias": float(g["act"].sum() / g["mu"].sum()), "share_above_q90": float((g["act"] >= g["q90"]).mean()),
        }
    return out
