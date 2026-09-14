"""Coverage-matchup adjustment layer of `MatchupContext` (PRD Section 6).

Computes a per-receiver coverage multiplier from a team-wide PFF coverage
grade differential, sharpened — when the data supports it — by identifying
the specific opposing defender who covers the receiver's dominant alignment
(slot vs. perimeter), per ADR-0001 and ADR-0006.

Known ingestion gap: `compute_coverage_multiplier`'s multiplier magnitude is
always derived from the team-wide `grade_differential`, even when confidence
is `CONFIDENT`. Alignment-snap/share ingestion is wired via
`nfl_dfs.ingestion.pff` (ADR-0022), so the confident path is reachable, but a
matched defender's *own* coverage grade — as opposed to the team's — is not
yet ingested, so identifying the right defender doesn't yet sharpen the
multiplier itself, only the confidence label attached to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Sequence

# ADR-0006: a defender's snap count in the target alignment must clear this
# floor before being treated as a specialist there.
ALIGNMENT_SNAP_FLOOR = 15

# ADR-0006: the leading candidate's snap count in the target alignment must
# beat the runner-up by more than this relative margin.
IDENTIFICATION_MARGIN_THRESHOLD = 0.5

# PRD Section 6: proposed multiplier cap range, pending Model Analytics
# Expert sign-off before treated as final.
MULTIPLIER_FLOOR = 0.85
MULTIPLIER_CEILING = 1.15

Alignment = Literal["slot", "perimeter"]


@dataclass(frozen=True)
class DefenderAlignmentSnaps:
    """A defender's coverage snaps split by alignment for one team-week."""

    native_id: str
    team: str
    slot_snaps: int
    perimeter_snaps: int

    def snaps(self, alignment: Alignment) -> int:
        return self.slot_snaps if alignment == "slot" else self.perimeter_snaps

    @property
    def total_snaps(self) -> int:
        return self.slot_snaps + self.perimeter_snaps


@dataclass(frozen=True)
class ReceiverAlignmentShare:
    """A receiver's snap share split by alignment for one team-week."""

    player_id: str
    slot_share: float
    perimeter_share: float

    @property
    def dominant_alignment(self) -> Alignment:
        return "slot" if self.slot_share >= self.perimeter_share else "perimeter"


@dataclass(frozen=True)
class AlignmentDefenderMatch:
    """The confidently-identified defender for a receiver's dominant alignment."""

    defender: DefenderAlignmentSnaps
    alignment: Alignment


class CoverageConfidence(str, Enum):
    """How the coverage multiplier for a receiver was derived."""

    CONFIDENT = "confident"
    TEAM_WIDE_FALLBACK = "team_wide_fallback"
    NO_DATA = "no_data"


@dataclass(frozen=True)
class CoverageMultiplierResult:
    multiplier: float
    confidence: CoverageConfidence
    matched_defender_id: str | None = None


def identify_alignment_defender(
    receiver: ReceiverAlignmentShare,
    defenders: Sequence[DefenderAlignmentSnaps],
) -> AlignmentDefenderMatch | None:
    """Identify the opposing defender responsible for `receiver`'s dominant alignment.

    `defenders` should already be filtered to the receiver's opponent's
    roster — this function has no team field to filter on (`ReceiverAlignmentShare`
    doesn't carry one) and does not attempt to infer opponent from the input.

    Applies the three ADR-0006 gates (snap floor, margin threshold, shadow-coverage
    guardrail) in order; returns `None` if any gate fails.
    """
    target = receiver.dominant_alignment
    opposite: Alignment = "perimeter" if target == "slot" else "slot"

    eligible = [d for d in defenders if d.total_snaps >= ALIGNMENT_SNAP_FLOOR]
    if not eligible:
        return None

    ranked_target = sorted(eligible, key=lambda d: d.snaps(target), reverse=True)
    top = ranked_target[0]
    top_snaps = top.snaps(target)

    if top_snaps < ALIGNMENT_SNAP_FLOOR:
        return None

    if len(ranked_target) > 1:
        second_snaps = ranked_target[1].snaps(target)
        margin = (top_snaps - second_snaps) / top_snaps if top_snaps else 0.0
        if margin <= IDENTIFICATION_MARGIN_THRESHOLD:
            return None

    ranked_opposite = sorted(eligible, key=lambda d: d.snaps(opposite), reverse=True)
    if ranked_opposite and ranked_opposite[0].native_id == top.native_id:
        return None

    return AlignmentDefenderMatch(defender=top, alignment=target)


MULTIPLIER_SPAN_PER_Z = 0.10


def _capped_multiplier(grade_differential: float) -> float:
    """Provisional z-score-to-multiplier mapping (PRD Section 6, draft range).

    Matches matchup.context's inline mapping for every other position, so a
    WR/TE's multiplier magnitude doesn't shift just because alignment data
    happened to be available for that matchup (see ADR-0022) — only the
    confidence label changes. Pending Model Analytics Expert sign-off on both
    the mapping and the cap range before this is treated as final.
    """
    return max(MULTIPLIER_FLOOR, min(MULTIPLIER_CEILING, 1.0 + MULTIPLIER_SPAN_PER_Z * grade_differential))


def compute_coverage_multiplier(
    grade_differential: float | None,
    defender_alignment_snaps: Sequence[DefenderAlignmentSnaps] | None = None,
    receiver_alignment_share: ReceiverAlignmentShare | None = None,
) -> CoverageMultiplierResult:
    """Compute a receiver's coverage multiplier and how confidently it was derived.

    `grade_differential` is the (already z-scored) PFF coverage-grade gap this
    multiplier scales from — team-wide today, regardless of confidence (see
    module docstring's "Known ingestion gap"). `None` means no grade data was
    available at all, which always yields `NO_DATA` regardless of the
    alignment inputs.
    """
    if grade_differential is None:
        return CoverageMultiplierResult(multiplier=1.0, confidence=CoverageConfidence.NO_DATA)

    match = None
    if defender_alignment_snaps and receiver_alignment_share is not None:
        match = identify_alignment_defender(receiver_alignment_share, defender_alignment_snaps)

    confidence = CoverageConfidence.CONFIDENT if match else CoverageConfidence.TEAM_WIDE_FALLBACK
    multiplier = _capped_multiplier(grade_differential)
    matched_defender_id = match.defender.native_id if match else None
    return CoverageMultiplierResult(multiplier, confidence, matched_defender_id)
