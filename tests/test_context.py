from nfl_dfs.matchup.context import PassCatcherInput, build_matchup_context_pool
from nfl_dfs.matchup.coverage import (
    CoverageConfidence,
    DefenderAlignmentSnaps,
    ReceiverAlignmentShare,
)


def test_confident_path_reachable_through_pool_wiring():
    """The full point of ADR-0022: build_matchup_context_pool must actually
    thread real alignment data through to compute_coverage_multiplier instead
    of hardcoding None, so a well-supported receiver reaches CONFIDENT."""
    pass_catchers = [
        PassCatcherInput(
            player_id="wr1",
            team="PHI",
            opponent="DAL",
            team_coverage_grade_differential=2.0,
        )
    ]
    defender_alignment_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="DAL", slot_snaps=40, perimeter_snaps=5),
        DefenderAlignmentSnaps(native_id="cb2", team="DAL", slot_snaps=5, perimeter_snaps=35),
        # Different team entirely — must not be considered for a DAL matchup.
        DefenderAlignmentSnaps(native_id="cb3", team="NYG", slot_snaps=50, perimeter_snaps=50),
    ]
    receiver_alignment_share = [
        ReceiverAlignmentShare(player_id="wr1", slot_share=0.85, perimeter_share=0.15),
    ]

    [context] = build_matchup_context_pool(
        pass_catchers, defender_alignment_snaps, receiver_alignment_share
    )

    assert context.player_id == "wr1"
    assert context.coverage_confidence == CoverageConfidence.CONFIDENT
    assert context.matched_defender_id == "cb1"


def test_falls_back_to_team_wide_without_alignment_inputs():
    pass_catchers = [
        PassCatcherInput(
            player_id="wr1", team="PHI", opponent="DAL", team_coverage_grade_differential=1.5
        )
    ]

    [context] = build_matchup_context_pool(pass_catchers)

    assert context.coverage_confidence == CoverageConfidence.TEAM_WIDE_FALLBACK
    assert context.matched_defender_id is None


def test_no_data_when_grade_differential_missing():
    pass_catchers = [
        PassCatcherInput(
            player_id="wr1", team="PHI", opponent="DAL", team_coverage_grade_differential=None
        )
    ]

    [context] = build_matchup_context_pool(pass_catchers)

    assert context.coverage_confidence == CoverageConfidence.NO_DATA


def test_receiver_with_no_matching_share_falls_back():
    pass_catchers = [
        PassCatcherInput(
            player_id="wr1", team="PHI", opponent="DAL", team_coverage_grade_differential=2.0
        )
    ]
    defender_alignment_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="DAL", slot_snaps=40, perimeter_snaps=5),
    ]
    # Share for a different player entirely.
    receiver_alignment_share = [
        ReceiverAlignmentShare(player_id="wr2", slot_share=0.85, perimeter_share=0.15),
    ]

    [context] = build_matchup_context_pool(
        pass_catchers, defender_alignment_snaps, receiver_alignment_share
    )

    assert context.coverage_confidence == CoverageConfidence.TEAM_WIDE_FALLBACK


def test_multiple_pass_catchers_looked_up_independently():
    pass_catchers = [
        PassCatcherInput(
            player_id="wr1", team="PHI", opponent="DAL", team_coverage_grade_differential=2.0
        ),
        PassCatcherInput(
            player_id="wr2", team="NYG", opponent="WAS", team_coverage_grade_differential=2.0
        ),
    ]
    defender_alignment_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="DAL", slot_snaps=40, perimeter_snaps=5),
        DefenderAlignmentSnaps(native_id="cb2", team="DAL", slot_snaps=5, perimeter_snaps=35),
    ]
    receiver_alignment_share = [
        ReceiverAlignmentShare(player_id="wr1", slot_share=0.85, perimeter_share=0.15),
        ReceiverAlignmentShare(player_id="wr2", slot_share=0.85, perimeter_share=0.15),
    ]

    results = build_matchup_context_pool(
        pass_catchers, defender_alignment_snaps, receiver_alignment_share
    )

    by_player = {r.player_id: r for r in results}
    assert by_player["wr1"].coverage_confidence == CoverageConfidence.CONFIDENT
    # wr2's opponent (WAS) has no defender data at all.
    assert by_player["wr2"].coverage_confidence == CoverageConfidence.TEAM_WIDE_FALLBACK
