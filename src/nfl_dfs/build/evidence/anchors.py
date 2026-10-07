"""Code-computed anchors for pivotal questions (model-analytics review): the analyst may move a
question's probability only a bounded step from here, with a stated reason.

- **Distribution questions** (a rate/volume metric vs a threshold): the anchor is the league base
  rate -- the share of single team-games that landed on the YES side. A reference class, not a view.
- **Lead-state questions** (`lead_at_q3_start`, `lead_at_q4`): a normal margin model from the
  closing spread -- the margin entering a quarter has mean spread x (fraction played) and standard
  deviation 13.5 x sqrt(fraction played). Disclosed draft constants, not backtested.

Anchors are clipped to [0.05, 0.95] so no question starts (or is adjusted to) certainty.
"""

from __future__ import annotations

import math

import numpy as np

from nfl_dfs.build.thesis.contracts import PivotalQuestion

MARGIN_SD = 13.5  # full-game NFL margin standard deviation around the spread (draft; verify on history)
ANCHOR_CLIP = (0.05, 0.95)
_FRACTION_PLAYED = {"lead_at_q3_start": 0.5, "lead_at_q4": 0.75}


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def favorite_win_probability(abs_spread: float) -> float:
    """P(the favorite wins) under margin ~ N(spread, 13.5)."""
    return _phi(abs_spread / MARGIN_SD)


def _clip(p: float) -> float:
    return min(max(p, ANCHOR_CLIP[0]), ANCHOR_CLIP[1])


def base_rate(values: np.ndarray, threshold: float, direction: str) -> float | None:
    """Share of `values` on the YES side of the threshold; `None` with no data."""
    if len(values) == 0:
        return None
    share = float(np.mean(values >= threshold)) if direction == "gte" else float(np.mean(values <= threshold))
    return _clip(share)


def percentile_of_threshold(values: np.ndarray, threshold: float) -> float | None:
    """Fraction of league team-games at or below the threshold (its league percentile)."""
    return float(np.mean(values <= threshold)) if len(values) else None


def lead_anchor(metric: str, team_spread: float) -> float:
    """P(this team leads entering the quarter). `team_spread` > 0 means the team is the favorite by
    that many points."""
    frac = _FRACTION_PLAYED[metric]
    return _clip(_phi((team_spread * frac) / (MARGIN_SD * math.sqrt(frac))))


def question_anchor(
    q: PivotalQuestion, *, league_value_fn, team_spread: dict[str, float], question_team: str | None
) -> float | None:
    """The code-computed anchor for `q`, or `None` if no anchor can be computed (the thesis validator
    then rejects the question: an unanchorable question cannot be graded against a baseline).

    `league_value_fn(metric) -> np.ndarray` of team-game values; `team_spread[team]` > 0 = favorite;
    `question_team` is the team the question's source field refers to."""
    if q.metric in _FRACTION_PLAYED:
        if question_team is None or question_team not in team_spread:
            return None
        p = lead_anchor(q.metric, team_spread[question_team])
        return p if q.direction == "gte" else _clip(1.0 - p)
    return base_rate(league_value_fn(q.metric), q.threshold, q.direction)


def make_percentile_fn(league_value_fn):
    """Adapter for `validate_game_thesis(percentile_of=...)`."""

    def percentile_of(metric: str, threshold: float, direction: str) -> float | None:
        return percentile_of_threshold(league_value_fn(metric), threshold)

    return percentile_of
