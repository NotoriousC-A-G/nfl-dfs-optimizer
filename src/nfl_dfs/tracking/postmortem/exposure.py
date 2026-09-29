"""Portfolio-level postmortem analysis across ALL of one week's lineups (the 6 `NflAgentConstructor`
agents + the operator's own L1/L2/L3): player exposure, positional bias, and whether each agent's
own disclosed stack thesis actually hit. Real ports of week 2's `reconstruct_week2_postmortem.py`
one-off functions (`compute_player_exposure`/`compute_positional_delta`/`compute_stack_thesis_
review`) -- that script's own docstring flagged itself as "NOT a reusable pipeline piece," tuned to
one week's parsed HTML; this module is the real, tested, reusable replacement, operating on
`LineupOutcome`/`PlayerOutcome` objects straight from `postmortem/replay.py` instead of regex
output, and reading each agent's stack thesis from the real snapshot's `core_stack` field instead
of parsing rationale text.
"""

from __future__ import annotations

from nfl_dfs.tracking.postmortem.models import LineupOutcome, PlayerExposure, PositionalDelta, StackThesisReview

# Disclosed draft, not backtested (same posture as this project's other UI-facing thresholds): a
# player rostered in fewer than this many lineups isn't a meaningful "what went wrong/right"
# finding on its own -- matches week 2's own `>= 3` cutoff.
_MIN_EXPOSURE_FOR_FINDING = 3


def compute_player_exposure(lineup_outcomes: tuple[LineupOutcome, ...]) -> tuple[PlayerExposure, ...]:
    """Every distinct player (by canonical_id) across every lineup this week, with the real lineup
    labels that rostered them. Sorted by exposure count descending, then by actual points
    descending -- the biggest real decisions (most-shared picks) surface first."""
    by_id: dict[str, dict] = {}
    for lo in lineup_outcomes:
        for p in lo.players:
            entry = by_id.setdefault(
                p.canonical_id,
                {
                    "canonical_id": p.canonical_id,
                    "display_name": p.display_name,
                    "team": p.team,
                    "position": p.position,
                    "projected": p.projected,
                    "actual": p.actual,
                    "delta": p.delta,
                    "labels": [],
                },
            )
            entry["labels"].append(lo.label)

    exposure = [
        PlayerExposure(
            canonical_id=e["canonical_id"],
            display_name=e["display_name"],
            team=e["team"],
            position=e["position"],
            projected=e["projected"],
            actual=e["actual"],
            delta=e["delta"],
            lineup_labels=tuple(e["labels"]),
        )
        for e in by_id.values()
    ]
    exposure.sort(key=lambda p: (-p.count, -(p.actual if p.actual is not None else -1e9)))
    return tuple(exposure)


def compute_positional_deltas(exposure: tuple[PlayerExposure, ...]) -> tuple[PositionalDelta, ...]:
    """Avg projected/actual/delta by position, one vote per distinct player regardless of how many
    lineups rostered them (deliberately deduped -- see `PositionalDelta`'s own docstring for why
    this is a different question than exposure's portfolio-damage view)."""
    by_pos: dict[str, list[PlayerExposure]] = {}
    for p in exposure:
        if p.projected is None or p.actual is None:
            continue
        by_pos.setdefault(p.position, []).append(p)

    result = []
    for pos, players in by_pos.items():
        deltas = [p.actual - p.projected for p in players]
        result.append(
            PositionalDelta(
                position=pos,
                n=len(players),
                avg_projected=sum(p.projected for p in players) / len(players),
                avg_actual=sum(p.actual for p in players) / len(players),
                avg_delta=sum(deltas) / len(deltas),
            )
        )
    result.sort(key=lambda d: d.avg_delta)
    return tuple(result)


def compute_stack_thesis_reviews(lineup_outcomes: tuple[LineupOutcome, ...]) -> tuple[StackThesisReview, ...]:
    """For each lineup with a non-empty `core_stack` (the 6 agents; operator rows carry none, see
    `LineupOutcome.core_stack`'s docstring), did those specific real named players actually
    deliver. `actual`/`hit` are `None` when any stack player didn't resolve to a real settled
    score, same "don't silently treat a miss as 0" posture as everywhere else in this package."""
    reviews = []
    for lo in lineup_outcomes:
        if not lo.core_stack:
            continue
        stack_players = [p for p in lo.players if p.canonical_id in lo.core_stack]
        if not stack_players:
            continue

        projected = sum(p.projected for p in stack_players)
        any_unresolved = any(p.actual is None for p in stack_players)
        actual = None if any_unresolved else sum(p.actual for p in stack_players)
        delta = None if actual is None else actual - projected

        reviews.append(
            StackThesisReview(
                label=lo.label,
                agent_id=lo.agent_id,
                stack_player_names=tuple(p.display_name for p in stack_players),
                stack_team=lo.core_stack_team,
                projected=round(projected, 2),
                actual=round(actual, 2) if actual is not None else None,
                delta=round(delta, 2) if delta is not None else None,
                hit=None if actual is None else actual > projected,
            )
        )
    return tuple(reviews)
