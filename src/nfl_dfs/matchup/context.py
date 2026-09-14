"""Builds `MatchupContext` results for a pool of pass-catchers (PRD Section 6).

Wires the coverage-alignment gating in `matchup.coverage` to real per-team-week
inputs, per ADR-0022.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from nfl_dfs.matchup.coverage import (
    CoverageConfidence,
    DefenderAlignmentSnaps,
    ReceiverAlignmentShare,
    compute_coverage_multiplier,
)


@dataclass(frozen=True)
class PassCatcherInput:
    """A pass-catcher's identity plus the team-wide grade differential for their matchup."""

    player_id: str
    team: str
    opponent: str
    team_coverage_grade_differential: float | None


@dataclass(frozen=True)
class PlayerMatchupContext:
    player_id: str
    coverage_multiplier: float
    coverage_confidence: CoverageConfidence
    matched_defender_id: str | None


def build_matchup_context_pool(
    pass_catchers: Sequence[PassCatcherInput],
    defender_alignment_snaps: Sequence[DefenderAlignmentSnaps] | None = None,
    receiver_alignment_share: Sequence[ReceiverAlignmentShare] | None = None,
) -> list[PlayerMatchupContext]:
    """Compute a `PlayerMatchupContext` per pass-catcher.

    `defender_alignment_snaps` and `receiver_alignment_share` are optional pool-level
    inputs (typically from `nfl_dfs.ingestion.pff`) — a caller without them wired up
    still gets `team_wide_fallback`/`no_data` results rather than an error, since
    `compute_coverage_multiplier` treats missing alignment data the same way.
    """
    defenders_by_team: dict[str, list[DefenderAlignmentSnaps]] = defaultdict(list)
    for defender in defender_alignment_snaps or ():
        defenders_by_team[defender.team].append(defender)

    share_by_player = {share.player_id: share for share in receiver_alignment_share or ()}

    results = []
    for pass_catcher in pass_catchers:
        receiver_share = share_by_player.get(pass_catcher.player_id)
        opponent_defenders = defenders_by_team.get(pass_catcher.opponent) or None

        result = compute_coverage_multiplier(
            grade_differential=pass_catcher.team_coverage_grade_differential,
            defender_alignment_snaps=opponent_defenders,
            receiver_alignment_share=receiver_share,
        )
        results.append(
            PlayerMatchupContext(
                player_id=pass_catcher.player_id,
                coverage_multiplier=result.multiplier,
                coverage_confidence=result.confidence,
                matched_defender_id=result.matched_defender_id,
            )
        )
    return results
