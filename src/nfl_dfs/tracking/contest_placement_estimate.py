"""Estimates whether a lineup that was NEVER entered into a real DK contest (the 6
`NflAgentConstructor` agents -- generated but never submitted) would have cashed in the SAME real
contests Chris actually entered that week. Chris, 2026-09-22: "We could do this as if each of the
agents had been entered in the contests I had entered."

**The real anchor points this leans on.** A single played lineup (Chris's own L1/L2/L3) is
routinely entered into several real contests, and a lineup's real settled score is the SAME
number in every one of them (it's the same 9 players; only the contest's own field/payout
structure differs). So for any contest Chris entered with 2+ of his own lineups, those lineups'
(real_score, real_rank) pairs trace out a REAL point on that specific contest's actual
score-to-rank curve -- not a generic assumption borrowed from another contest. An agent's own real
settled score can be interpolated (or, past the observed range, extrapolated) against that curve.

**Genuinely estimated, not exact -- and disclosed as exactly that everywhere it's used.** Piecewise
-linear interpolation/extrapolation from 2-3 real points is a real, defensible estimate, not a
guess pulled from nowhere, but it is NOT the same certainty as `contest_results_store`'s own
`cashed` field (a real settled fact for an entry that actually existed). Every `EstimatedPlacement`
below carries its own `method` ("interpolated" or "extrapolated") so nothing downstream conflates
the two. Contests where Chris only had ONE real entry that week (6 of week 2's 13) have no second
point to fit a slope from -- deliberately left un-estimated (`estimate_agent_placements` simply
returns nothing for those) rather than borrowing another contest's slope, which would mix two
different real fields' actual behavior into one number presented as if it were still local.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from nfl_dfs.storage.contest_results_store import ContestResult, read_contest_results

_MIN_REAL_ANCHORS = 2  # need at least 2 real (score, rank) points to fit any slope at all


@dataclass(frozen=True)
class EstimatedPlacement:
    agent_id: str
    week: int
    contest_name: str
    entries: int
    positions_paid: int
    real_score: float
    estimated_rank: int
    estimated_cashed: bool
    method: str  # "interpolated" | "extrapolated"


def _fit_rank(anchors: list[tuple[float, int]], score: float) -> tuple[int, str]:
    """`anchors` is a list of real (score, rank) pairs for one contest, already known to be
    monotonic (higher score -> lower/better rank, per every real week 2 contest checked live).
    Piecewise-linear between the two anchors bracketing `score`; linear extrapolation using the
    nearest edge segment's own slope when `score` falls outside the observed range."""
    anchors = sorted(anchors)  # ascending by score
    scores = [s for s, _ in anchors]

    if score <= scores[0]:
        (s0, r0), (s1, r1) = anchors[0], anchors[1]
        method = "interpolated" if score == scores[0] else "extrapolated"
    elif score >= scores[-1]:
        (s0, r0), (s1, r1) = anchors[-2], anchors[-1]
        method = "interpolated" if score == scores[-1] else "extrapolated"
    else:
        i = bisect_left(scores, score)
        (s0, r0), (s1, r1) = anchors[i - 1], anchors[i]
        method = "interpolated"

    if s1 == s0:  # degenerate (shouldn't happen with real distinct scores, guarded anyway)
        rank = round((r0 + r1) / 2)
    else:
        rank = round(r0 + (r1 - r0) * (score - s0) / (s1 - s0))
    return max(1, rank), method


def estimate_agent_placements(
    season: int, week: int, agent_scores: dict[str, float], *, contest_path=None
) -> list[EstimatedPlacement]:
    """`agent_scores` is `{agent_id: real_total_dk_score}` for whichever agents should be
    estimated (the 6 `NflAgentConstructor` agents -- never the operator, which has real contest
    data already). Returns one `EstimatedPlacement` per (agent, contest) for every contest with
    >=2 of Chris's own real entries that week; contests with only one real entry are skipped
    entirely (see module docstring)."""
    rows = read_contest_results(season=season, week=week, path=contest_path)
    by_contest: dict[str, list[ContestResult]] = {}
    for r in rows:
        by_contest.setdefault(r.contest_name, []).append(r)

    placements = []
    for contest_name, entries in by_contest.items():
        anchors = [(e.fpts, e.rank) for e in entries]
        if len(set(a[0] for a in anchors)) < _MIN_REAL_ANCHORS:
            continue
        positions_paid = entries[0].positions_paid
        total_entries = entries[0].entries
        for agent_id, score in agent_scores.items():
            rank, method = _fit_rank(anchors, score)
            placements.append(
                EstimatedPlacement(
                    agent_id=agent_id,
                    week=week,
                    contest_name=contest_name,
                    entries=total_entries,
                    positions_paid=positions_paid,
                    real_score=score,
                    estimated_rank=rank,
                    estimated_cashed=rank <= positions_paid,
                    method=method,
                )
            )
    return placements
