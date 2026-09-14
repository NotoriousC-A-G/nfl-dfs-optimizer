"""`MatchupContext`'s Coverage row (PRD Section 6) -- the most complex row: a scheme (man/zone)
differential, weighted by which alignment (slot/perimeter) the receiver actually plays, gated by
ADR-0006's confidence checks before trusting an individually-identified defender's grade over a
team-wide fallback.

## Known ingestion gap -- the alignment piece's input data does not exist in this pipeline yet

ADR-0001's alignment approximation needs `GET /v1/facet/signature/defense/slot_coverage` (per-
defender slot/perimeter coverage-snap volume) and the offense-side equivalent (a receiver's own
slot/perimeter snap share). **Neither is ingested anywhere in this pipeline** -- confirmed absent
from `pff.py`'s `FACETS`/`GRADE_FACETS` by ADR-0022's own live audit ("referenced in the PRD's
`MatchupContext` table but not present in `pff.py`'s fetch code at all"). Per this round's own
constraints (pure consumption of already-ingested modules, `pff.py` not touched), this module
defines the typed input shapes (`DefenderAlignmentSnaps`, `ReceiverAlignmentShare`,
`ShadowCoverageSignal`) that a *future* ingestion pass would populate, and implements ADR-0001/
ADR-0006's full identification-and-gating logic faithfully against them -- but with no live
producer for those inputs today, every caller of this module operates with
`defender_alignment_snaps=None`/`receiver_alignment_share=None`, which `compute_coverage_multiplier`
treats as an automatic, correctly-labeled fall-through to ADR-0006's own fallback (the team-wide
snap-weighted grade), not a crash or a guessed alignment split. **Flagged explicitly, not silently
absorbed:** this means the alignment piece of the coverage row cannot fire for real in this
pipeline until `signature/defense/slot_coverage` (and a receiver-side alignment-share source) are
actually added to ingestion -- a concrete, scoped follow-up for the Data Integration Engineer, the
same gap ADR-0022 already flagged for the eventual player-detail "matchups" view.

## Scheme differential -- a judgment call, flagged explicitly (same discipline as `grading.py`)

The PRD's Primary Data Inputs column names the defender's man/zone grades "matched to the
receiver's own man-rate/zone-rate split faced" as the scheme input, but doesn't fully specify what
the differential's *other side* is. This module diffs the defender-side grade (identified-defender
blend, or the ADR-0006 fallback) against the receiver's own PFF `receiving/scheme` performance
grade (`man_grades_pass_route`/`zone_grades_pass_route`) -- a genuine unit-vs-unit comparison in
the same spirit as run game/pass protection's grade-vs-grade differential, and the exact "does
Player X perform better against man or zone" signal ADR-0022 added `receiving/scheme` ingestion
to supply. Both sides are weighted by the receiver's own man-rate/zone-rate split before
differencing, per the PRD's explicit instruction. This is a real formula-behavior decision the PRD
text under-specifies, not a certainty -- flagged here for Model Analytics Expert/Fantasy Football
Expert review the same way every other Section 6 judgment call in this codebase is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from nfl_dfs.matchup.grading import (
    MULTIPLIER_CAP,
    TeamAggregate,
    population_zscore,
    snap_weighted_grade,
    zscore_diff_to_multiplier,
)

# defense/coverage_scheme (already ingested, ADR-0014) field names -- PRD Section 6 names these
# explicitly.
MAN_COVERAGE_GRADE_FIELD = "man_grades_coverage_defense"
ZONE_COVERAGE_GRADE_FIELD = "zone_grades_coverage_defense"
# Confirmed live (ADR-0022) to already flow through pff.py's generic `grades` capture -- used both
# for the fallback's snap-weighting and, in a future ingestion pass, as a cross-check on
# DefenderAlignmentSnaps.
MAN_COVERAGE_SNAP_FIELD = "man_snap_counts_coverage"
ZONE_COVERAGE_SNAP_FIELD = "zone_snap_counts_coverage"

# receiving/scheme (already ingested, ADR-0022) field names -- the receiver's own performance
# grade against each scheme type, and the rate (share of targets/routes) at which they face each
# -- confirmed live field names (ADR-0022): "a full man_*/zone_* parallel field set ... man_targets/
# zone_targets, man_targets_percent/zone_targets_percent, ... man_grades_pass_route/
# zone_grades_pass_route".
RECEIVER_MAN_GRADE_FIELD = "man_grades_pass_route"
RECEIVER_ZONE_GRADE_FIELD = "zone_grades_pass_route"
RECEIVER_MAN_RATE_FIELD = "man_targets_percent"
RECEIVER_ZONE_RATE_FIELD = "zone_targets_percent"


def receiver_man_zone_rate(receiver_id: str, receiving_facet_rows: dict) -> tuple[float, float] | None:
    """This receiver's own man-rate/zone-rate split faced, straight off the already-ingested
    `receiving/scheme` facet (PRD Section 6's own instruction: coverage multipliers are "matched
    to the receiver's own man-rate/zone-rate split faced") -- no separate data source needed.
    Normalizes so the two rates sum to `1.0` regardless of whether PFF's `_percent` fields are
    expressed as a 0-1 fraction or a 0-100 percentage (not confirmed live either way). `None` when
    this receiver has no `receiving/scheme` row, or that row is missing either rate field.
    """
    row = receiving_facet_rows.get(receiver_id)
    if row is None:
        return None
    man = row.grades.get(RECEIVER_MAN_RATE_FIELD)
    zone = row.grades.get(RECEIVER_ZONE_RATE_FIELD)
    if man is None or zone is None:
        return None
    total = man + zone
    if total <= 0:
        return None
    return man / total, zone / total

# ADR-0006's two statistical gates.
ALIGNMENT_MARGIN_THRESHOLD = 0.50  # absolute majority, strictly greater than
ALIGNMENT_SNAP_FLOOR = 15
# ADR-0006's shadow-coverage guardrail.
SHADOW_MATCH_RATE_THRESHOLD = 0.70

Alignment = Literal["slot", "perimeter"]
CoverageConfidence = Literal["confident", "team_wide_fallback", "no_data"]


# --------------------------------------------------------------------------------------------
# Typed input shapes for ADR-0001's alignment approximation -- see module docstring's "Known
# ingestion gap" section. No live producer exists for these in this pipeline today.
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DefenderAlignmentSnaps:
    """One defender's slot/perimeter coverage-snap volume over ADR-0006's trailing window (3 weeks
    for the two statistical gates). Shape mirrors `GET /v1/facet/signature/defense/slot_coverage`
    (ADR-0001) -- not ingested anywhere yet, see module docstring.
    """

    native_id: str
    team: str
    slot_snaps: int
    perimeter_snaps: int


@dataclass(frozen=True)
class ReceiverAlignmentShare:
    """A receiver's own trailing slot-vs-perimeter snap share -- the offense-side half of the same
    un-ingested `slot_coverage`-shaped volume data. `slot_share + perimeter_share` is expected (not
    enforced here) to equal `1.0`.
    """

    player_id: str
    slot_share: float
    perimeter_share: float


@dataclass(frozen=True)
class ShadowCoverageSignal:
    """ADR-0006's shadow-coverage guardrail input: a defender's per-play alignment match-rate
    against the opposing WR1's alignment, over the longer (e.g. 6-week) lookback ADR-0006
    specifies for this specific behavioral check. Also not produced by any ingestion module today.

    `None` (the default, since no caller can supply this without the upstream data) is treated as
    "cannot evaluate the guardrail" -- `identify_alignment_defender` fails *open* on a missing
    signal (the two statistical gates can still pass on their own), not closed. This is a
    deliberate, singular departure from this project's usual "no data -> no adjustment" posture,
    stated explicitly rather than silently defaulted: failing closed here (forcing the team-wide
    fallback every single week purely because a second, harder-to-build signal doesn't exist yet)
    would make ADR-0001's whole alignment approximation permanently inert until this guardrail's
    own data exists too, which is a strictly worse outcome than the honest, flagged limitation
    "this guardrail cannot fire yet."
    """

    match_rate: float


@dataclass(frozen=True)
class AlignmentIdentification:
    """One team's plurality slot-or-perimeter coverage defender for one alignment, and whether
    ADR-0006's gates trust that identification."""

    alignment: Alignment
    defender_native_id: str | None
    share: float | None
    snaps: int | None
    gate_passed: bool
    reason: str


def identify_alignment_defender(
    team: str,
    alignment: Alignment,
    defender_snaps: Sequence[DefenderAlignmentSnaps],
    shadow_signal: ShadowCoverageSignal | None = None,
) -> AlignmentIdentification:
    """ADR-0001's plurality-by-volume identification, gated by ADR-0006's margin threshold
    (>50% absolute majority) and snap-count floor (>=15 snaps in that alignment), further
    downgraded by ADR-0006's shadow-coverage guardrail when a match-rate signal is supplied and
    fires. Returns `gate_passed=False` (never a guessed identification) when any check fails --
    callers must fall back to the team-wide grade (see `_team_wide_fallback_grade`) in that case.
    """
    snap_attr = "slot_snaps" if alignment == "slot" else "perimeter_snaps"
    team_rows = [d for d in defender_snaps if d.team == team]
    total_snaps = sum(getattr(d, snap_attr) for d in team_rows)

    if not team_rows or total_snaps <= 0:
        return AlignmentIdentification(
            alignment=alignment,
            defender_native_id=None,
            share=None,
            snaps=None,
            gate_passed=False,
            reason=f"no {alignment} coverage-snap data for {team} -- team-wide fallback required.",
        )

    plurality = max(team_rows, key=lambda d: getattr(d, snap_attr))
    snaps = getattr(plurality, snap_attr)
    share = snaps / total_snaps

    if share <= ALIGNMENT_MARGIN_THRESHOLD:
        return AlignmentIdentification(
            alignment=alignment,
            defender_native_id=plurality.native_id,
            share=share,
            snaps=snaps,
            gate_passed=False,
            reason=(
                f"margin gate failed (ADR-0006): plurality {alignment} defender holds {share:.0%} "
                f"of {team}'s {alignment} coverage snaps, not > {ALIGNMENT_MARGIN_THRESHOLD:.0%}."
            ),
        )
    if snaps < ALIGNMENT_SNAP_FLOOR:
        return AlignmentIdentification(
            alignment=alignment,
            defender_native_id=plurality.native_id,
            share=share,
            snaps=snaps,
            gate_passed=False,
            reason=(
                f"snap-count floor failed (ADR-0006): plurality {alignment} defender has {snaps} "
                f"snaps, below the {ALIGNMENT_SNAP_FLOOR}-snap floor."
            ),
        )
    if shadow_signal is not None and shadow_signal.match_rate > SHADOW_MATCH_RATE_THRESHOLD:
        return AlignmentIdentification(
            alignment=alignment,
            defender_native_id=plurality.native_id,
            share=share,
            snaps=snaps,
            gate_passed=False,
            reason=(
                f"shadow-coverage guardrail fired (ADR-0006): {shadow_signal.match_rate:.0%} "
                f"alignment match-rate against opposing WR1 exceeds {SHADOW_MATCH_RATE_THRESHOLD:.0%}."
            ),
        )

    return AlignmentIdentification(
        alignment=alignment,
        defender_native_id=plurality.native_id,
        share=share,
        snaps=snaps,
        gate_passed=True,
        reason="gates passed (ADR-0006): identification trusted.",
    )


def _defender_grades(native_id: str, coverage_facet_rows: dict) -> tuple[float | None, float | None]:
    row = coverage_facet_rows.get(native_id)
    if row is None:
        return None, None
    return row.grades.get(MAN_COVERAGE_GRADE_FIELD), row.grades.get(ZONE_COVERAGE_GRADE_FIELD)


def _team_wide_fallback_grade(team: str, coverage_facet_rows: dict) -> tuple[float | None, float | None, str]:
    """ADR-0006/ADR-0010's fallback: the team-wide overall coverage grade, snap-weighted across
    defenders (not a simple average -- see `grading.snap_weighted_grade`), undifferentiated by
    alignment.
    """
    team_rows = [r for r in coverage_facet_rows.values() if r.team == team]
    man_grade, man_method = snap_weighted_grade(team_rows, MAN_COVERAGE_GRADE_FIELD, MAN_COVERAGE_SNAP_FIELD)
    zone_grade, zone_method = snap_weighted_grade(team_rows, ZONE_COVERAGE_GRADE_FIELD, ZONE_COVERAGE_SNAP_FIELD)
    return man_grade, zone_grade, f"man={man_method}, zone={zone_method}"


@dataclass(frozen=True)
class CoverageMultiplier:
    """One receiver's coverage-row result. `confidence` distinguishes three states (mirroring
    `StackProfile.bring_back_status`'s discipline of never collapsing distinguishable `None`/
    ambiguous cases into one bare value):

    - `"confident"` -- both alignment gates passed (or the receiver has no meaningful alignment
      split to blend, degrading gracefully to the identified defender directly) and the
      defender-specific grade was used.
    - `"team_wide_fallback"` -- either gate failed (or no alignment data was available at all,
      the current real-world case per this module's "Known ingestion gap" note) -- the team-wide
      snap-weighted grade was used instead.
    - `"no_data"` -- neither the defender-specific nor the team-wide grade was computable at all
      (e.g. no `defense/coverage_scheme` rows for this team this week) -- `multiplier=None`.
    """

    receiver_id: str
    receiver_team: str
    defense_team: str
    multiplier: float | None
    confidence: CoverageConfidence
    defender_man_grade: float | None
    defender_zone_grade: float | None
    receiver_man_grade: float | None
    receiver_zone_grade: float | None
    z_diff: float | None
    reason: str | None = None


def compute_coverage_multiplier(
    receiver_id: str,
    receiver_team: str,
    defense_team: str,
    receiver_man_rate: float,
    receiver_zone_rate: float,
    coverage_facet_rows: dict,
    receiving_facet_rows: dict,
    league_defender_man_population: Sequence[float],
    league_defender_zone_population: Sequence[float],
    league_receiver_man_population: Sequence[float],
    league_receiver_zone_population: Sequence[float],
    receiver_alignment_share: ReceiverAlignmentShare | None = None,
    defender_alignment_snaps: Sequence[DefenderAlignmentSnaps] | None = None,
    shadow_signal_slot: ShadowCoverageSignal | None = None,
    shadow_signal_perimeter: ShadowCoverageSignal | None = None,
    cap: float = MULTIPLIER_CAP,
) -> CoverageMultiplier:
    """Full coverage-row computation for one receiver:

    1. **Alignment** (ADR-0001/ADR-0006): if `receiver_alignment_share` and
       `defender_alignment_snaps` are both supplied, attempt to identify the plurality slot and
       perimeter defenders and gate each per ADR-0006. If both gates pass, blend their individual
       man/zone grades by the receiver's own slot/perimeter snap share (ADR-0001's own weighting).
       Otherwise (either gate fails, or no alignment data was supplied at all -- the real-world
       case today, see module docstring) fall back to the team-wide snap-weighted grade.
    2. **Scheme** (this module's own judgment call, see module docstring): z-score the resulting
       defender man/zone grades against the league population of the same, and the receiver's own
       `receiving/scheme` man/zone performance grades against *their* league population, each
       weighted by the receiver's own man-rate/zone-rate split, then diff (receiver z minus
       defender z) and map to a capped multiplier.

    `coverage_facet_rows`/`receiving_facet_rows` are `PffFacetGrades.by_player_id` dicts (already-
    fetched, ADR-0014-populated pulls) -- not fetched here.
    """
    confidence: CoverageConfidence
    alignment_notes: list[str] = []

    if receiver_alignment_share is not None and defender_alignment_snaps:
        slot_id = identify_alignment_defender(defense_team, "slot", defender_alignment_snaps, shadow_signal_slot)
        perimeter_id = identify_alignment_defender(
            defense_team, "perimeter", defender_alignment_snaps, shadow_signal_perimeter
        )
        alignment_notes.extend([slot_id.reason, perimeter_id.reason])

        if slot_id.gate_passed and perimeter_id.gate_passed:
            slot_man, slot_zone = _defender_grades(slot_id.defender_native_id, coverage_facet_rows)
            perim_man, perim_zone = _defender_grades(perimeter_id.defender_native_id, coverage_facet_rows)
            if None not in (slot_man, slot_zone, perim_man, perim_zone):
                defender_man_grade = (
                    receiver_alignment_share.slot_share * slot_man
                    + receiver_alignment_share.perimeter_share * perim_man
                )
                defender_zone_grade = (
                    receiver_alignment_share.slot_share * slot_zone
                    + receiver_alignment_share.perimeter_share * perim_zone
                )
                confidence = "confident"
            else:
                defender_man_grade, defender_zone_grade, fallback_note = _team_wide_fallback_grade(
                    defense_team, coverage_facet_rows
                )
                alignment_notes.append(
                    "gates passed but identified defender(s) missing a coverage_scheme grade row "
                    f"-- fell back to team-wide grade ({fallback_note})."
                )
                confidence = "team_wide_fallback"
        else:
            defender_man_grade, defender_zone_grade, fallback_note = _team_wide_fallback_grade(
                defense_team, coverage_facet_rows
            )
            alignment_notes.append(f"ADR-0006 gate(s) failed -- team-wide fallback used ({fallback_note}).")
            confidence = "team_wide_fallback"
    else:
        defender_man_grade, defender_zone_grade, fallback_note = _team_wide_fallback_grade(
            defense_team, coverage_facet_rows
        )
        alignment_notes.append(
            "no alignment (slot_coverage) data supplied -- team-wide fallback used "
            f"({fallback_note}). See coverage.py's module docstring: this facet is not yet "
            "ingested anywhere in this pipeline."
        )
        confidence = "team_wide_fallback"

    if defender_man_grade is None or defender_zone_grade is None:
        return CoverageMultiplier(
            receiver_id=receiver_id,
            receiver_team=receiver_team,
            defense_team=defense_team,
            multiplier=None,
            confidence="no_data",
            defender_man_grade=defender_man_grade,
            defender_zone_grade=defender_zone_grade,
            receiver_man_grade=None,
            receiver_zone_grade=None,
            z_diff=None,
            reason="; ".join(alignment_notes + ["no defense/coverage_scheme grade available for this defense."]),
        )

    receiver_row = receiving_facet_rows.get(receiver_id)
    receiver_man_grade = receiver_row.grades.get(RECEIVER_MAN_GRADE_FIELD) if receiver_row else None
    receiver_zone_grade = receiver_row.grades.get(RECEIVER_ZONE_GRADE_FIELD) if receiver_row else None

    defender_man_z = population_zscore(defender_man_grade, league_defender_man_population)
    defender_zone_z = population_zscore(defender_zone_grade, league_defender_zone_population)
    receiver_man_z = population_zscore(receiver_man_grade, league_receiver_man_population)
    receiver_zone_z = population_zscore(receiver_zone_grade, league_receiver_zone_population)

    if None in (defender_man_z, defender_zone_z, receiver_man_z, receiver_zone_z):
        return CoverageMultiplier(
            receiver_id=receiver_id,
            receiver_team=receiver_team,
            defense_team=defense_team,
            multiplier=None,
            confidence=confidence,
            defender_man_grade=defender_man_grade,
            defender_zone_grade=defender_zone_grade,
            receiver_man_grade=receiver_man_grade,
            receiver_zone_grade=receiver_zone_grade,
            z_diff=None,
            reason="; ".join(
                alignment_notes
                + [
                    "coverage z-score undefined: missing receiving/scheme grade for this player "
                    "or an insufficient league population this week."
                ]
            ),
        )

    z_defense_faced = receiver_man_rate * defender_man_z + receiver_zone_rate * defender_zone_z
    z_receiver = receiver_man_rate * receiver_man_z + receiver_zone_rate * receiver_zone_z
    z_diff = z_receiver - z_defense_faced
    multiplier = zscore_diff_to_multiplier(z_diff, cap=cap)

    return CoverageMultiplier(
        receiver_id=receiver_id,
        receiver_team=receiver_team,
        defense_team=defense_team,
        multiplier=multiplier,
        confidence=confidence,
        defender_man_grade=defender_man_grade,
        defender_zone_grade=defender_zone_grade,
        receiver_man_grade=receiver_man_grade,
        receiver_zone_grade=receiver_zone_grade,
        z_diff=z_diff,
        reason="; ".join(alignment_notes) if alignment_notes else None,
    )


def team_coverage_grade_aggregate(team: str, coverage_facet_rows: dict) -> TeamAggregate | None:
    """Convenience wrapper used by `composition/player_detail.py`'s `opponent_unit_grade` (a WR/TE's
    "opposing unit" is the defense's aggregate coverage grade, not an individual defender) --
    snap-weighted man-grade rollup, same helper the fallback path above uses.
    """
    team_rows = [r for r in coverage_facet_rows.values() if r.team == team]
    value, method = snap_weighted_grade(team_rows, MAN_COVERAGE_GRADE_FIELD, MAN_COVERAGE_SNAP_FIELD)
    if value is None:
        return None
    return TeamAggregate(team=team, value=value, method=method, n_players=len(team_rows))
