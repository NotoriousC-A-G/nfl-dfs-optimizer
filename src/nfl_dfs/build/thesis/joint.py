"""Joint probabilities for a game's pivotal-question branches.

Two binary questions with marginals p1, p2 and a log odds ratio `lambda_` determine the four cell
probabilities (Plackett's solution) -- so the analyst supplies marginals and a dependency, and the
branch probabilities are *derived in code*, not typed by a model. A residual mass ("neither question
resolves as framed") is carved out first and the cells share the rest.
"""

from __future__ import annotations

import math


def logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def joint_cells(p1: float, p2: float, lambda_: float = 0.0) -> dict[tuple[bool, bool], float]:
    """`{(q1, q2): prob}` with exact marginals P(q1)=p1, P(q2)=p2 and log odds ratio `lambda_`."""
    if not (0.0 < p1 < 1.0 and 0.0 < p2 < 1.0):
        raise ValueError(f"marginals must be strictly between 0 and 1, got {p1}, {p2}")
    odds_ratio = math.exp(lambda_)
    if abs(odds_ratio - 1.0) < 1e-12:
        p11 = p1 * p2
    else:
        s = 1.0 + (p1 + p2) * (odds_ratio - 1.0)
        p11 = (s - math.sqrt(s * s - 4.0 * odds_ratio * (odds_ratio - 1.0) * p1 * p2)) / (2.0 * (odds_ratio - 1.0))
    p10 = p1 - p11
    p01 = p2 - p11
    p00 = 1.0 - p11 - p10 - p01
    return {(True, True): p11, (True, False): p10, (False, True): p01, (False, False): p00}


def expected_branch_probs(
    marginals: dict[str, float], independent_ids: tuple[str, ...], lambda_: float, residual: float
) -> dict[tuple[tuple[str, bool], ...], float]:
    """Branch probabilities (keyed by sorted `((question_id, answer), ...)`) for 1 or 2 independent
    questions, scaled by `1 - residual`. More than two independent questions is not supported here
    (the validator caps them at two)."""
    scale = 1.0 - residual
    if len(independent_ids) == 1:
        qid = independent_ids[0]
        p = marginals[qid]
        return {((qid, True),): scale * p, ((qid, False),): scale * (1.0 - p)}
    if len(independent_ids) == 2:
        a, b = sorted(independent_ids)
        cells = joint_cells(marginals[a], marginals[b], lambda_)
        return {((a, ya), (b, yb)): scale * prob for (ya, yb), prob in cells.items()}
    raise ValueError("expected_branch_probs supports 1 or 2 independent questions")
