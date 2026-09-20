"""GPP-viability grade for one generated lineup -- adapted from the sister MLB project's real,
shipped `compute_leverage_rating` (`mlb_dfs/config.py`), at Chris's explicit request (2026-09-20):
"the MLB lineups give each a GPP grade."

**An adaptation, not a port** (same discipline `NflAgentConstructor` already established): MLB's
formula is reused where the underlying real signal genuinely transfers (both projects have real
per-player projected ownership; NFL's `ceiling_multiplier`, Component A/ADR-0028, is a real,
if narrower-scoped, ceiling signal analogous to MLB's own `ceiling` field), and every numeric
threshold is carried over as a DISCLOSED DRAFT starting point (ADR-0019/0020's own convention),
never claimed as NFL-backtested -- MLB's own thresholds were themselves hand-tuned, not
simulation/backtest-derived either (see that function's own docstring: "Dr. Webb" attribution, no
ADR cited).

**Blend, directly reused from MLB:** `0.6 * differentiation_score + 0.4 * ceiling_score`, both on
a 0-4 scale, mapped to a letter grade A (>=3.5) / B (>=2.5) / C (>=1.5) / D (else). Two real
components:

1. **Differentiation** -- `expected_shared = sum(projected_ownership / 100 for every rostered
   player)`, the expected number of players this lineup shares with a random field entry. Lower is
   more differentiated. Banded by real slate size (`game_count`) the same way MLB bands by its own
   slate size, though the NFL-specific band edges below are a disclosed proportional
   scale-down from MLB's own 10-player-roster thresholds to NFL's 9-player DK Classic roster
   (9/10 of MLB's values, rounded) -- NOT independently re-derived from real NFL field data. A
   concentration bonus (one player at real projected ownership > 30%, while the rest of the
   lineup is otherwise differentiated) softens the score the same way MLB's does, on the same
   reasoning: one obvious chalk play doesn't make an otherwise-differentiated build undifferentiated.
2. **Ceiling quality** -- this lineup's real ceiling-weighted total (`blended_projection *
   ceiling_multiplier`, `ceiling_multiplier` defaulting to 1.0 for a player Component A doesn't
   cover -- QB/TE/DST, or an RB/WR with no real signal yet this week) divided by the real BEST
   such total buildable from the same pool (`compute_max_ceiling_weighted_total` below, one extra
   real ILP solve reusing the `objective_delta_by_id` hook, PR B1 -- not a new solver mechanism).
   **Honest degradation, not fabrication:** when NO rostered player has a real `ceiling_multiplier`
   at all (confirmed live 2026-09-20: true for essentially every player this early in the season,
   Component A's trailing-week gate hasn't cleared for anyone yet), this ratio is `None` and the
   ceiling component falls back to MLB's own "unknown -- assume the neutral middle" posture
   (`ceiling_pct = 0.5`), but -- an improvement on the MLB precedent, matching this project's own
   "every `None` has a reason" discipline that MLB's version doesn't carry -- `ceiling_reason` is
   populated explaining exactly why, never silently defaulted with no trace.

**Informational only, exactly like MLB's own `leverage_rating` -- NEVER gates which lineups get
generated or returned.** A grade tells a caller how much real GPP viability a build carries; it is
not itself a filter. Contest-type-specific grade bumping (MLB's `contest_grades` table) is
deliberately NOT ported this round -- this project has no contest-type selection wired in yet, and
adding one un-requested is out of scope; a single blended grade is what was asked for.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.optimizer.lineup import Lineup, generate_lineups
from nfl_dfs.projection.blend import PlayerProjection

# Proportional scale-down from MLB's own 10-player-roster bands (1.0/1.5/2.0 for game_count > 6,
# tightened for smaller slates) to NFL's 9-player DK Classic roster -- 9/10 of MLB's real values,
# rounded to one decimal. A disclosed draft, not independently backtested against NFL field data.
_SMALL_SLATE_MAX_GAMES = 4  # matches MLB's own "<=4" band edge -- NFL showdown/2-3-game slates.
_MEDIUM_SLATE_MAX_GAMES = 6
_DIFFERENTIATION_THRESHOLDS_BY_SLATE_SIZE: dict[str, dict[str, float]] = {
    "small": {"A": 1.2, "B": 1.6, "C": 2.1},
    "medium": {"A": 1.1, "B": 1.4, "C": 1.9},
    "large": {"A": 0.9, "B": 1.4, "C": 1.8},
}
_CONCENTRATION_OWNERSHIP_FLOOR = 30.0  # percent -- directly reused from MLB, same real semantics.
_CONCENTRATION_BONUS = 0.2
_CONCENTRATION_OTHER_SHARED_CEILING = 1.0

_CEILING_ELITE = 0.90
_CEILING_GOOD = 0.80
_CEILING_ADEQUATE = 0.70
_CEILING_UNKNOWN_RATIO = 0.5  # MLB's own "unknown -- assume the middle" posture.

_BLEND_DIFFERENTIATION_WEIGHT = 0.6
_BLEND_CEILING_WEIGHT = 0.4
_GRADE_A_FLOOR = 3.5
_GRADE_B_FLOOR = 2.5
_GRADE_C_FLOOR = 1.5

_NO_CEILING_SIGNAL_ANYWHERE_REASON = (
    "no rostered player has a real Component A ceiling signal this week -- either too early in "
    "the season for the trailing-week gate to clear (ceiling/signals.py, ADR-0028), or every "
    "rostered player is at a position Component A doesn't cover (QB/TE/DST)"
)


@dataclass(frozen=True)
class GppGrade:
    """One lineup's real, computed GPP-viability grade. See module docstring for the full formula
    and what each component means; every field here is the real intermediate value that produced
    `grade`, not just the final letter -- so a caller (or a future backtest) can see exactly what
    drove it, the same "show your work" discipline this project applies everywhere else.
    """

    grade: str  # "A" / "B" / "C" / "D"
    differentiation_score: float  # 0-4
    ceiling_score: float  # 0-4
    expected_shared: float  # real sum(projected_ownership / 100) across the roster
    ceiling_ratio: float | None  # this lineup's ceiling-weighted total / the slate's real best
    ceiling_reason: str | None  # populated exactly when ceiling_ratio is None
    detail: str  # one-line human-readable summary, same shape as MLB's own `leverage_detail`


def _differentiation_score(expected_shared: float, max_projected_ownership: float, *, game_count: int) -> tuple[float, float]:
    """Returns `(score, adjusted_shared)` -- `adjusted_shared` exposed so `detail` can report the
    real, concentration-adjusted number, not just the raw one.
    """
    if game_count <= _SMALL_SLATE_MAX_GAMES:
        thresholds = _DIFFERENTIATION_THRESHOLDS_BY_SLATE_SIZE["small"]
    elif game_count <= _MEDIUM_SLATE_MAX_GAMES:
        thresholds = _DIFFERENTIATION_THRESHOLDS_BY_SLATE_SIZE["medium"]
    else:
        thresholds = _DIFFERENTIATION_THRESHOLDS_BY_SLATE_SIZE["large"]

    concentration_bonus = 0.0
    if max_projected_ownership > _CONCENTRATION_OWNERSHIP_FLOOR:
        other_shared = expected_shared - (max_projected_ownership / 100.0)
        if other_shared < _CONCENTRATION_OTHER_SHARED_CEILING:
            concentration_bonus = _CONCENTRATION_BONUS
    adjusted_shared = expected_shared - concentration_bonus

    if adjusted_shared <= thresholds["A"]:
        return 4.0, adjusted_shared
    if adjusted_shared <= thresholds["B"]:
        return 3.0, adjusted_shared
    if adjusted_shared <= thresholds["C"]:
        return 2.0, adjusted_shared
    return 1.0, adjusted_shared


def _ceiling_weighted_total(
    lineup: Lineup, ceiling_multiplier_by_canonical_id: dict[str, float] | None
) -> tuple[float, bool]:
    """This lineup's real `sum(blended_projection * ceiling_multiplier)` -- `ceiling_multiplier`
    defaults to 1.0 for any player with no real signal (Component A doesn't cover their position,
    or their trailing data hasn't cleared the gate yet). Returns `(total, has_any_real_signal)` --
    the second value is `False` (never fabricated as `True`) exactly when every rostered player
    defaulted to 1.0, i.e. this lineup carries no real ceiling information at all.
    """
    by_id = ceiling_multiplier_by_canonical_id or {}
    total = 0.0
    has_any_real_signal = False
    for p in lineup.players:
        multiplier = by_id.get(p.canonical_id)
        if multiplier is not None:
            has_any_real_signal = True
        total += p.blended_projection * (multiplier if multiplier is not None else 1.0)
    return total, has_any_real_signal


def compute_max_ceiling_weighted_total(
    pool: list[PlayerProjection], ceiling_multiplier_by_canonical_id: dict[str, float] | None
) -> float | None:
    """The real best `sum(blended_projection * ceiling_multiplier)` buildable from `pool` under
    the same roster/salary rules -- one extra real ILP solve, reusing `objective_delta_by_id` (PR
    B1) rather than a new solver mechanism: `objective = blended_projection + delta` where
    `delta = blended_projection * (ceiling_multiplier - 1.0)` is exactly `blended_projection *
    ceiling_multiplier`. Returns `None` (never a fabricated 0.0) when no player in `pool` has a
    real ceiling signal at all -- there is no real "ceiling-optimal" lineup to solve for yet.
    """
    by_id = ceiling_multiplier_by_canonical_id or {}
    if not any(cid in by_id for p in pool if (cid := p.canonical_id)):
        return None
    delta = {
        p.canonical_id: p.blended_projection * (by_id[p.canonical_id] - 1.0)
        for p in pool
        if p.canonical_id in by_id and p.blended_projection is not None
    }
    best = generate_lineups(pool, n=1, objective_delta_by_id=delta)[0]
    total, _ = _ceiling_weighted_total(best, ceiling_multiplier_by_canonical_id)
    return total


def compute_gpp_grade(
    lineup: Lineup,
    *,
    projected_ownership_by_canonical_id: dict[str, float],
    ceiling_multiplier_by_canonical_id: dict[str, float] | None,
    max_ceiling_weighted_total: float | None,
    game_count: int,
) -> GppGrade:
    """The real, computed grade for one already-generated `lineup`. `max_ceiling_weighted_total`
    is a caller-supplied real number (from `compute_max_ceiling_weighted_total`, computed ONCE per
    pool and reused across every lineup graded against it -- never recomputed per-lineup) so this
    function itself stays a pure, cheap, easily-testable calculation.
    """
    ownership_values = [projected_ownership_by_canonical_id.get(p.canonical_id, 0.0) for p in lineup.players]
    expected_shared = sum(v / 100.0 for v in ownership_values)
    max_own = max(ownership_values) if ownership_values else 0.0
    diff_score, adjusted_shared = _differentiation_score(expected_shared, max_own, game_count=game_count)

    lineup_ceiling_total, has_real_ceiling_signal = _ceiling_weighted_total(lineup, ceiling_multiplier_by_canonical_id)
    if not has_real_ceiling_signal or not max_ceiling_weighted_total:
        ceiling_ratio = None
        ceiling_reason = _NO_CEILING_SIGNAL_ANYWHERE_REASON
        ceiling_pct = _CEILING_UNKNOWN_RATIO
        ceil_label = "unknown"
    else:
        ceiling_ratio = lineup_ceiling_total / max_ceiling_weighted_total
        ceiling_reason = None
        ceiling_pct = ceiling_ratio
        if ceiling_pct >= _CEILING_ELITE:
            ceil_label = "elite"
        elif ceiling_pct >= _CEILING_GOOD:
            ceil_label = "good"
        elif ceiling_pct >= _CEILING_ADEQUATE:
            ceil_label = "adequate"
        else:
            ceil_label = "weak"

    if ceiling_pct >= _CEILING_ELITE:
        ceil_score = 4.0
    elif ceiling_pct >= _CEILING_GOOD:
        ceil_score = 3.0
    elif ceiling_pct >= _CEILING_ADEQUATE:
        ceil_score = 2.0
    else:
        ceil_score = 1.0

    blend = _BLEND_DIFFERENTIATION_WEIGHT * diff_score + _BLEND_CEILING_WEIGHT * ceil_score
    if blend >= _GRADE_A_FLOOR:
        grade = "A"
    elif blend >= _GRADE_B_FLOOR:
        grade = "B"
    elif blend >= _GRADE_C_FLOOR:
        grade = "C"
    else:
        grade = "D"

    grade_labels = {"A": "Strong", "B": "Good", "C": "Moderate", "D": "Low"}
    ceiling_detail = f"{ceil_label} ceiling" if ceiling_ratio is None else f"{ceil_label} ceiling ({ceiling_pct:.0%})"
    detail = f"{grade_labels[grade]} GPP viability -- {adjusted_shared:.1f} players shared w/ field, {ceiling_detail}"

    return GppGrade(
        grade=grade,
        differentiation_score=diff_score,
        ceiling_score=ceil_score,
        expected_shared=expected_shared,
        ceiling_ratio=ceiling_ratio,
        ceiling_reason=ceiling_reason,
        detail=detail,
    )
