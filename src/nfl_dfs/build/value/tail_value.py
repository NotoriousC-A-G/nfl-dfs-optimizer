"""Per-player tail value for the pool-constrained solve (plan D4, Revision 2 #4).

`TV = mu + c * (q90 - mu)` with `c = 1/3`: a lineup's tail is NOT the sum of nine players' tails (that
treats them as perfectly correlated and over-rewards volatility ~3x -- the model-analytics review); for
nine roughly independent players the lineup's q90 sits about `mu_L + 1.28 * sqrt(9) * sigma`, i.e. one
third of the summed individual excess. Correlation (the required stack, a bring-back) is added back as
explicit pair bonuses in the solve, not here.

`mu` and `q90` come from the calibration cells (`calibration.py`); a scenario thesis can scale them via
`expected_multipliers` (probability-weighted branch multipliers). Magnitudes are draft, not backtested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from nfl_dfs.build.thesis.contracts import GameThesis
from nfl_dfs.build.value.calibration import CalibrationTable

C_DEFAULT = 1.0 / 3.0


@dataclass(frozen=True)
class TailValue:
    mu: float
    q90: float
    tv: float


def expected_multipliers(theses: Iterable[GameThesis]) -> dict[str, tuple[float, float]]:
    """`{canonical_id: (mean_mult, q90_mult)}`: the probability-weighted multiplier across a game's
    branches. A branch that does not name the player contributes 1.0 (no change); the residual branch
    names nobody, so it correctly pulls every named player back toward 1.0."""
    out: dict[str, tuple[float, float]] = {}
    for thesis in theses:
        named: dict[str, list[tuple[float, float, float]]] = {}
        for b in thesis.branches:
            for o in b.player_outcomes:
                named.setdefault(o.canonical_id, []).append((b.prob, o.mean_mult, o.q90_mult))
        for cid, items in named.items():
            covered = sum(p for p, _, _ in items)
            mean_m = sum(p * m for p, m, _ in items) + (1.0 - covered) * 1.0
            q90_m = sum(p * q for p, _, q in items) + (1.0 - covered) * 1.0
            out[cid] = (mean_m, q90_m)
    return out


def tail_values(
    players: Iterable[tuple[str, str, float]],
    table: CalibrationTable,
    *,
    c: float = C_DEFAULT,
    multipliers: dict[str, tuple[float, float]] | None = None,
) -> dict[str, TailValue]:
    """`players`: `(canonical_id, position, projection)`. A player whose position/projection falls in
    no calibrated cell is OMITTED (never given an invented value) -- the caller must treat a missing
    value as 'cannot value this player'."""
    out: dict[str, TailValue] = {}
    for cid, position, projection in players:
        cell = table.lookup(position, projection)
        if cell is None:
            continue
        mean_m, q90_m = (multipliers or {}).get(cid, (1.0, 1.0))
        mu = projection * cell.mean_ratio * mean_m
        q90 = max(mu, projection * cell.q90_ratio * q90_m)
        out[cid] = TailValue(mu, q90, mu + c * (q90 - mu))
    return out
