from nfl_dfs.matchup.coverage import (
    CoverageConfidence,
    DefenderAlignmentSnaps,
    ReceiverAlignmentShare,
    compute_coverage_multiplier,
    identify_alignment_defender,
)


def _defender(native_id, team="OPP", slot=0, perimeter=0):
    return DefenderAlignmentSnaps(
        native_id=native_id, team=team, slot_snaps=slot, perimeter_snaps=perimeter
    )


def _receiver(player_id="wr1", slot_share=0.8, perimeter_share=0.2):
    return ReceiverAlignmentShare(
        player_id=player_id, slot_share=slot_share, perimeter_share=perimeter_share
    )


class TestIdentifyAlignmentDefender:
    def test_confident_match_when_one_clear_slot_specialist(self):
        receiver = _receiver(slot_share=0.9, perimeter_share=0.1)
        defenders = [
            _defender("cb1", slot=40, perimeter=5),
            _defender("cb2", slot=10, perimeter=30),
            _defender("cb3", slot=5, perimeter=25),
        ]

        match = identify_alignment_defender(receiver, defenders)

        assert match is not None
        assert match.alignment == "slot"
        assert match.defender.native_id == "cb1"

    def test_no_match_below_snap_floor(self):
        # Leader has only 10 slot snaps (< 15 floor), even with a big margin.
        receiver = _receiver(slot_share=0.9, perimeter_share=0.1)
        defenders = [
            _defender("cb1", slot=10, perimeter=5),
            _defender("cb2", slot=1, perimeter=20),
        ]

        assert identify_alignment_defender(receiver, defenders) is None

    def test_no_match_when_margin_too_thin(self):
        # cb1 and cb2 split slot coverage almost evenly — no clear specialist.
        receiver = _receiver(slot_share=0.9, perimeter_share=0.1)
        defenders = [
            _defender("cb1", slot=20, perimeter=5),
            _defender("cb2", slot=18, perimeter=5),
        ]

        assert identify_alignment_defender(receiver, defenders) is None

    def test_no_match_when_leader_also_tops_opposite_alignment(self):
        # cb1 leads both slot and perimeter counts — looks like shadow coverage,
        # not a zone/alignment specialist.
        receiver = _receiver(slot_share=0.9, perimeter_share=0.1)
        defenders = [
            _defender("cb1", slot=40, perimeter=35),
            _defender("cb2", slot=5, perimeter=10),
        ]

        assert identify_alignment_defender(receiver, defenders) is None

    def test_targets_perimeter_alignment_when_receiver_is_perimeter_dominant(self):
        receiver = _receiver(slot_share=0.2, perimeter_share=0.8)
        defenders = [
            _defender("cb1", slot=30, perimeter=5),
            _defender("cb2", slot=5, perimeter=40),
        ]

        match = identify_alignment_defender(receiver, defenders)

        assert match is not None
        assert match.alignment == "perimeter"
        assert match.defender.native_id == "cb2"

    def test_no_eligible_defenders_returns_none(self):
        receiver = _receiver()
        defenders = [_defender("cb1", slot=5, perimeter=5)]

        assert identify_alignment_defender(receiver, defenders) is None


class TestComputeCoverageMultiplier:
    def test_no_data_when_grade_differential_missing(self):
        result = compute_coverage_multiplier(grade_differential=None)

        assert result.confidence == CoverageConfidence.NO_DATA
        assert result.multiplier == 1.0
        assert result.matched_defender_id is None

    def test_team_wide_fallback_when_alignment_inputs_absent(self):
        result = compute_coverage_multiplier(grade_differential=2.0)

        assert result.confidence == CoverageConfidence.TEAM_WIDE_FALLBACK
        assert result.matched_defender_id is None

    def test_team_wide_fallback_when_identification_fails(self):
        receiver = _receiver(slot_share=0.9, perimeter_share=0.1)
        defenders = [_defender("cb1", slot=5, perimeter=5)]  # below snap floor

        result = compute_coverage_multiplier(
            grade_differential=2.0,
            defender_alignment_snaps=defenders,
            receiver_alignment_share=receiver,
        )

        assert result.confidence == CoverageConfidence.TEAM_WIDE_FALLBACK
        assert result.matched_defender_id is None

    def test_confident_when_identification_succeeds(self):
        receiver = _receiver(slot_share=0.9, perimeter_share=0.1)
        defenders = [
            _defender("cb1", slot=40, perimeter=5),
            _defender("cb2", slot=5, perimeter=30),
        ]

        result = compute_coverage_multiplier(
            grade_differential=2.0,
            defender_alignment_snaps=defenders,
            receiver_alignment_share=receiver,
        )

        assert result.confidence == CoverageConfidence.CONFIDENT
        assert result.matched_defender_id == "cb1"

    def test_multiplier_caps_at_ceiling_and_floor(self):
        high = compute_coverage_multiplier(grade_differential=100.0)
        low = compute_coverage_multiplier(grade_differential=-100.0)

        assert high.multiplier == 1.15
        assert low.multiplier == 0.85
