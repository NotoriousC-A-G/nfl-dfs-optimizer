"""Tests for build_matchup_context_pool's alignment-aware WR/TE branch (ADR-0022).

tests/test_matchup_context.py covers the pre-existing RB/QB/WR/DST z-score
behavior; these tests focus specifically on what changes when
defender_alignment_snaps/receiver_alignment_share are supplied.
"""

from nfl_dfs.matchup.context import MatchupFacetInputs, build_matchup_context_pool
from nfl_dfs.matchup.coverage import CoverageConfidence, DefenderAlignmentSnaps, ReceiverAlignmentShare

_FACETS = MatchupFacetInputs(
    offense_run_blocking={"BUF": 78.0, "MIA": 65.0},
    defense_run={"BUF": 70.0, "MIA": 60.0},
    offense_pass_blocking={"BUF": 74.0, "MIA": 68.0},
    defense_pass_rush={"BUF": 72.0, "MIA": 66.0},
    defense_coverage_scheme={"BUF": 69.0, "MIA": 71.0},
    receiving_scheme={"BUF": 73.0, "MIA": 70.0},
)


def test_wr_reaches_confident_when_alignment_data_supports_it():
    players = [("wr1", "WR", "MIA", "BUF")]
    defender_alignment_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="BUF", slot_snaps=40, perimeter_snaps=5),
        DefenderAlignmentSnaps(native_id="cb2", team="BUF", slot_snaps=5, perimeter_snaps=35),
    ]
    receiver_alignment_share = [
        ReceiverAlignmentShare(player_id="wr1", slot_share=0.85, perimeter_share=0.15),
    ]

    pool = build_matchup_context_pool(
        players, _FACETS, defender_alignment_snaps, receiver_alignment_share
    )

    assert pool["wr1"].coverage_confidence == CoverageConfidence.CONFIDENT
    assert pool["wr1"].matched_defender_id == "cb1"


def test_wr_multiplier_magnitude_unchanged_by_alignment_confidence():
    """The alignment gate should only ever change the confidence label, never
    the multiplier magnitude, for the same grade inputs (ADR-0022)."""
    players = [("wr1", "WR", "MIA", "BUF")]
    defender_alignment_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="BUF", slot_snaps=40, perimeter_snaps=5),
        DefenderAlignmentSnaps(native_id="cb2", team="BUF", slot_snaps=5, perimeter_snaps=35),
    ]
    receiver_alignment_share = [
        ReceiverAlignmentShare(player_id="wr1", slot_share=0.85, perimeter_share=0.15),
    ]

    without_alignment = build_matchup_context_pool(players, _FACETS)
    with_alignment = build_matchup_context_pool(
        players, _FACETS, defender_alignment_snaps, receiver_alignment_share
    )

    assert without_alignment["wr1"].coverage_confidence == CoverageConfidence.TEAM_WIDE_FALLBACK
    assert with_alignment["wr1"].coverage_confidence == CoverageConfidence.CONFIDENT
    assert without_alignment["wr1"].multiplier == with_alignment["wr1"].multiplier


def test_wr_falls_back_when_alignment_identification_fails():
    players = [("wr1", "WR", "MIA", "BUF")]
    # Below the ADR-0006 snap floor -- no confident defender identification.
    defender_alignment_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="BUF", slot_snaps=5, perimeter_snaps=5),
    ]
    receiver_alignment_share = [
        ReceiverAlignmentShare(player_id="wr1", slot_share=0.85, perimeter_share=0.15),
    ]

    pool = build_matchup_context_pool(
        players, _FACETS, defender_alignment_snaps, receiver_alignment_share
    )

    assert pool["wr1"].coverage_confidence == CoverageConfidence.TEAM_WIDE_FALLBACK
    assert pool["wr1"].matched_defender_id is None


def test_rb_is_unaffected_by_alignment_inputs():
    players = [("rb1", "RB", "BUF", "MIA")]
    defender_alignment_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="MIA", slot_snaps=40, perimeter_snaps=5),
    ]

    pool = build_matchup_context_pool(players, _FACETS, defender_alignment_snaps)

    assert pool["rb1"].coverage_confidence is None
    assert pool["rb1"].matched_defender_id is None
    assert pool["rb1"].multiplier > 1.0


def test_wr_with_no_matching_alignment_share_falls_back():
    players = [("wr1", "WR", "MIA", "BUF")]
    defender_alignment_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="BUF", slot_snaps=40, perimeter_snaps=5),
    ]
    # Share for a different player entirely.
    receiver_alignment_share = [
        ReceiverAlignmentShare(player_id="wr2", slot_share=0.85, perimeter_share=0.15),
    ]

    pool = build_matchup_context_pool(
        players, _FACETS, defender_alignment_snaps, receiver_alignment_share
    )

    assert pool["wr1"].coverage_confidence == CoverageConfidence.TEAM_WIDE_FALLBACK


def test_wr_missing_grade_data_stays_neutral_with_no_confidence_label():
    players = [("wr1", "WR", "MIA", "NYJ")]  # NYJ absent from every facet map
    defender_alignment_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="NYJ", slot_snaps=40, perimeter_snaps=5),
    ]
    receiver_alignment_share = [
        ReceiverAlignmentShare(player_id="wr1", slot_share=0.85, perimeter_share=0.15),
    ]

    pool = build_matchup_context_pool(
        players, _FACETS, defender_alignment_snaps, receiver_alignment_share
    )

    assert pool["wr1"].multiplier == 1.0
    assert pool["wr1"].coverage_confidence is None
