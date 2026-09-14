"""Shared math for the two cross-sectional-per-week z-scored `GameEnvironmentScore` inputs
(`nflverse.py`'s pace/PROE and `odds_api.py`'s implied team total) -- factored out once both
modules needed the exact same ADR-0003 population/window spec, rather than restating it twice.

Both formulas are specified precisely in ADR-0003 (`docs/adr/0003-game-environment-score-scale-
and-baselines.md`) and ADR-0011 (`docs/adr/0011-shared-shrinkage-functional-form.md`). This module
only implements the two small, generic pieces of math those ADRs define -- it does not decide
z-score *population membership* or *what counts as a completed week*, which stay the caller's
job (they differ: implied total's population is "every team playing that week," pace/PROE's is
"all 32 teams, blended toward a prior baseline if the team has few/no completed weeks yet").
"""

from __future__ import annotations

import pandas as pd


def shrinkage_weight(n: float, k: float) -> float:
    """ADR-0011's shared empirical-Bayes form: `w(n) = n / (n + k)`. Asymptotic -- never reaches
    1.0 even for a large `n`, unlike the capped linear ramp ADR-0011 replaced project-wide.
    `n` and `k` must both be expressed in the same unit (e.g. weeks played, both against `k=6`)."""
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    if k <= 0:
        raise ValueError(f"k must be > 0, got {k}")
    return n / (n + k)


def blend_toward_prior(current_value: float | None, prior_baseline: float | None, weight: float) -> float:
    """`blended = w * current + (1 - w) * prior` (ADR-0003's fallback formula, generalized to the
    ADR-0011 weight). `current_value=None` (zero current-season sample) is treated as contributing
    nothing -- safe because `weight` is 0 whenever `n=0` under `shrinkage_weight`, but this also
    guards a caller that passes a nonzero weight with no current-season value by construction
    error, by requiring a real prior baseline in that case instead of silently using 0.0."""
    if prior_baseline is None:
        if weight < 1.0:
            raise ValueError(
                "prior_baseline is None but weight < 1.0 -- no prior-season baseline is available "
                "for this team/metric (see the 'single most recent season' fallback in ADR-0003); "
                "the caller must supply one or force weight=1.0 (fully current-season)."
            )
        current_value = current_value if current_value is not None else 0.0
        return weight * current_value + (1 - weight) * 0.0
    current_component = weight * (current_value if current_value is not None else 0.0)
    return current_component + (1 - weight) * prior_baseline


def cross_sectional_zscore(values: pd.Series) -> pd.Series:
    """Z-score a single week's cross-section of team values against each other -- population
    standard deviation (`ddof=0`), not sample, because the input *is* the full population being
    described (per ADR-0003: "all 32 teams," or however many are in scope that week), not a
    sample drawn from a larger population.

    Returns `NaN` for every row if the population has fewer than 2 distinct values (std == 0) or
    fewer than 2 rows -- an undefined z-score, not a silently-wrong 0.0. Callers should treat a
    `NaN` z-score as "insufficient population this week," not as "average."
    """
    if len(values) < 2:
        return pd.Series([float("nan")] * len(values), index=values.index)
    std = values.std(ddof=0)
    if std == 0 or pd.isna(std):
        return pd.Series([float("nan")] * len(values), index=values.index)
    return (values - values.mean()) / std


def cross_sectional_zscore_by_group(df: pd.DataFrame, value_col: str, group_col: str) -> pd.Series:
    """`cross_sectional_zscore`, applied independently within each `group_col` value (e.g. each
    week) -- the "cross-sectional per week, not pooled-cumulative" population shape ADR-0003
    specifies. Returns a Series aligned to `df`'s index."""
    return df.groupby(group_col)[value_col].transform(lambda s: cross_sectional_zscore(s))
