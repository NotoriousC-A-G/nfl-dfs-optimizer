"""Capped log-space multiplier combination (ADR-0005, generalizing the same pattern ADR-0009/
ADR-0012/ADR-0015 all independently specify for their own multi-leg combinations).

**No prior reusable implementation existed.** `game_environment/score.py`'s
`compute_weather_subscore` inlines the identical sum-log/clip/exp math for its own three-way
weather combination -- there was nothing importable at the time it was written. This module is
the first standalone version of that pattern; `MatchupContext`'s pass-protection/coverage
combination (ADR-0005) is its first consumer. Future combination needs in this pipeline should
import `capped_log_combine` from here rather than re-inlining the steps a third time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class LogCombineResult:
    """Transparency wrapper, same spirit as `game_environment/score.py`'s `ComponentScore`: the
    final combined multiplier plus enough of the intermediate math to audit it (per-leg log terms,
    whether the cap actually bound) rather than only exposing the final number.
    """

    combined_multiplier: float
    log_terms: list[float]
    combined_log: float  # sum of log_terms, BEFORE clipping
    capped: bool  # True iff the cap actually changed the result
    cap: float  # the fractional cap used (e.g. 0.20 for +-20%)


def capped_log_combine(multipliers: Sequence[float], cap: float) -> LogCombineResult:
    """`combined = exp(clip(sum(ln(m_i)), ln(1-cap), ln(1+cap)))` -- ADR-0005's exact formula,
    generalized to any number of legs >= 1 (ADR-0009's three-factor DST combination and ADR-0015's
    three-way weather combination are the same pattern at a different leg count).

    Raises `ValueError` for an empty `multipliers` sequence (nothing to combine -- a caller bug,
    not a legitimate "neutral" case) or a `cap` outside `(0, 1)` (a fractional deviation like 0.20
    for +-20%, not a percentage or a multiplier bound like 1.20).
    """
    if not (0 < cap < 1):
        raise ValueError(f"cap must be a fractional deviation in (0, 1), e.g. 0.20 for +-20%%; got {cap!r}")
    multipliers = list(multipliers)
    if not multipliers:
        raise ValueError("capped_log_combine requires at least one multiplier")

    log_terms = [math.log(m) for m in multipliers]
    combined_log = sum(log_terms)
    lo, hi = math.log(1 - cap), math.log(1 + cap)
    clipped_log = min(max(combined_log, lo), hi)

    return LogCombineResult(
        combined_multiplier=math.exp(clipped_log),
        log_terms=log_terms,
        combined_log=combined_log,
        capped=clipped_log != combined_log,
        cap=cap,
    )
