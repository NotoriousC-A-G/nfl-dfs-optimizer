"""Turn opportunity into points (Chris, 2026-10-09: the volume plays carried a 5-point projection under a 56% carry-share thesis).

The vendor projections assume a Questionable player plays; our rule treats him as OUT, so the carries and targets he leaves behind are in
nobody's projection. `evidence/opportunity.py` already says who inherits how much SHARE. This module prices that share:

    extra points = (carry-share gain x the team's carries per game) x DK points per carry
                 + (target-share gain x the team's targets per game) x DK points per target,   x a pass-through factor

- **Rates** are fitted from real box scores (`fit_rates`): DK points per carry (rushing yards + touchdowns, RB) and per target (receptions, receiving
  yards + touchdowns) by position. Averages, not marginal rates -- an inherited carry is plausibly a bit below average, which `PASS_THROUGH` covers.
- **Team volume** is the team's own recent carries and targets per game (`opportunity.team_volume`).
- **`PASS_THROUGH` = 0.75** (draft, not backtested): not every vacated touch arrives (committees, game script, a returning starter), and the vendor
  number may already carry part of an announced absence.

Only positive gains are priced here (someone inheriting work). The adjustment is ADDED to the vendor projection before the tail-value calibration
and the analysts' multipliers, so the thesis then scales a base that already reflects the role.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import pandas as pd

PASS_THROUGH = 0.75
MIN_OPPORTUNITIES = 500  # a position's rate needs at least this many carries/targets behind it


@dataclass(frozen=True)
class PointsPerOpportunity:
    per_carry_rb: float
    per_target: dict[str, float]  # "RB" | "WR" | "TE" -> DK points per target
    n_carries_rb: int
    n_targets: dict[str, int]
    seasons: tuple[int, ...] = ()

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(blob: str) -> "PointsPerOpportunity":
        d = json.loads(blob)
        return PointsPerOpportunity(d["per_carry_rb"], d["per_target"], d["n_carries_rb"], d["n_targets"], tuple(d.get("seasons", ())))


def fit_rates(weekly: pd.DataFrame, *, seasons: tuple[int, ...] = ()) -> PointsPerOpportunity:
    """`weekly`: per-player-week box scores (columns position, season_type, carries, rushing_yards, rushing_tds, targets, receptions,
    receiving_yards, receiving_tds). Regular season only. Raises if a position has too few opportunities to fit."""
    d = weekly[weekly["season_type"] == "REG"] if "season_type" in weekly.columns else weekly
    rb = d[d["position"] == "RB"]
    carries = float(rb["carries"].sum())
    if carries < MIN_OPPORTUNITIES:
        raise ValueError(f"only {carries:.0f} RB carries to fit points per carry (need {MIN_OPPORTUNITIES})")
    per_carry = float((0.1 * rb["rushing_yards"] + 6.0 * rb["rushing_tds"]).sum() / carries)
    per_target, n_targets = {}, {}
    for pos in ("RB", "WR", "TE"):
        g = d[d["position"] == pos]
        t = float(g["targets"].sum())
        if t < MIN_OPPORTUNITIES:
            raise ValueError(f"only {t:.0f} {pos} targets to fit points per target (need {MIN_OPPORTUNITIES})")
        per_target[pos] = float((g["receptions"] + 0.1 * g["receiving_yards"] + 6.0 * g["receiving_tds"]).sum() / t)
        n_targets[pos] = int(t)
    return PointsPerOpportunity(per_carry, per_target, int(carries), n_targets, seasons)


@dataclass(frozen=True)
class OpportunityAdjustment:
    canonical_id: str
    name: str
    position: str
    vendor_projection: float
    carry_gain: float  # share points
    target_gain: float
    extra_carries: float
    extra_targets: float
    extra_points: float  # after PASS_THROUGH
    adjusted_projection: float


def opportunity_adjustments(
    players: list,  # PlayerEvidence-like: canonical_id, name, team, position, projection, carry_share_l4/_expected, target_share_l4/_expected
    team_volume: dict[str, tuple[float, float]],  # team -> (carries per game, targets per game)
    rates: PointsPerOpportunity,
    *,
    pass_through: float = PASS_THROUGH,
) -> dict[str, OpportunityAdjustment]:
    out: dict[str, OpportunityAdjustment] = {}
    for p in players:
        if p.projection is None or p.position not in ("RB", "WR", "TE"):
            continue
        cg = max((p.carry_share_expected or 0.0) - (p.carry_share_l4 or 0.0), 0.0) if p.carry_share_expected is not None else 0.0
        tg = max((p.target_share_expected or 0.0) - (p.target_share_l4 or 0.0), 0.0) if p.target_share_expected is not None else 0.0
        if cg <= 0.0 and tg <= 0.0:
            continue
        carries_pg, targets_pg = team_volume.get(p.team, (0.0, 0.0))
        extra_c = cg * carries_pg if p.position == "RB" else 0.0
        extra_t = tg * targets_pg
        pts = (extra_c * rates.per_carry_rb + extra_t * rates.per_target[p.position]) * pass_through
        if pts <= 0.0:
            continue
        out[p.canonical_id] = OpportunityAdjustment(
            p.canonical_id, p.name, p.position, p.projection, cg, tg, extra_c, extra_t, pts, p.projection + pts,
        )
    return out
