"""Lineup lint against the analyst's stated correlation signs (football review, Revision 2 #6).

A game analyst states, per pair of players, whether their outcomes are positively or negatively
related and how strongly. This lint takes a lineup's player ids and every thesis's `PairSign`s and
reports each stated-negative pair the lineup holds. A *strong* negative is an error (the lineup should
not be built that way); a *mild* negative is a warning (a soft penalty, not a ban). Positive and neutral
pairs never warn -- correlation is never forced, only contradictions are avoided.
"""

from __future__ import annotations

from nfl_dfs.build.common import Violation
from nfl_dfs.build.thesis.contracts import PairSign


def lint_lineup_pair_signs(lineup_player_ids: set[str], pair_signs: list[PairSign]) -> list[Violation]:
    out: list[Violation] = []
    for s in pair_signs:
        if s.sign != "negative":
            continue
        if s.player_a in lineup_player_ids and s.player_b in lineup_player_ids:
            severity = "error" if s.strength == "strong" else "warning"
            out.append(Violation("negative_pair", f"{s.player_a} and {s.player_b} are stated negatively correlated ({s.strength}): {s.reason}", "lineup", severity))
    return out
