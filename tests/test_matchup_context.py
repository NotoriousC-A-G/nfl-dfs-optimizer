import math

import pytest

from nfl_dfs.ingestion.pff import PffFacetGrades, PffGradeRow
from nfl_dfs.matchup.combination import capped_log_combine
from nfl_dfs.matchup.context import (
    MatchupFacetInputs,
    PlayerMatchupInput,
    build_matchup_context_for_receiver,
    build_matchup_context_pool,
    resolve_own_opponent_unit_grades,
)
from nfl_dfs.matchup.coverage import (
    DefenderAlignmentSnaps,
    ReceiverAlignmentShare,
    ShadowCoverageSignal,
    compute_coverage_multiplier,
    identify_alignment_defender,
    receiver_man_zone_rate,
)
from nfl_dfs.matchup.grading import (
    population_zscore,
    snap_weighted_grade,
    team_aggregate_grades,
    zscore_diff_to_multiplier,
)
from nfl_dfs.matchup.pass_protection import compute_pass_protection_multiplier
from nfl_dfs.matchup.run_game import compute_run_game_multiplier


def _grade_row(native_id, team, position="T", **grades) -> PffGradeRow:
    return PffGradeRow(
        native_id=native_id, name=native_id, team=team, position=position, jersey_number=None,
        player_game_count=10, grades=grades,
    )


def _facet(rows: list[PffGradeRow], population="cumulative_through_last_completed_week") -> PffFacetGrades:
    return PffFacetGrades(
        population=population, weeks_requested="1,2,3", seasons_requested="2026",
        by_player_id={r.native_id: r for r in rows},
    )


# --------------------------------------------------------------------------------------------
# combination.py
# --------------------------------------------------------------------------------------------


def test_capped_log_combine_neutral_multipliers_stay_neutral():
    result = capped_log_combine([1.0, 1.0], cap=0.20)
    assert result.combined_multiplier == pytest.approx(1.0)
    assert not result.capped


def test_capped_log_combine_caps_extreme_combination():
    # 1.15 * 1.15 naive = 1.3225 (+32.25%) -- well past a +-20% cap.
    result = capped_log_combine([1.15, 1.15], cap=0.20)
    assert result.combined_multiplier == pytest.approx(1.20)
    assert result.capped is True
    naive = 1.15 * 1.15
    assert result.combined_multiplier < naive


def test_capped_log_combine_requires_nonempty_and_valid_cap():
    with pytest.raises(ValueError):
        capped_log_combine([], cap=0.20)
    with pytest.raises(ValueError):
        capped_log_combine([1.0], cap=1.5)


# --------------------------------------------------------------------------------------------
# grading.py
# --------------------------------------------------------------------------------------------


def test_zscore_diff_to_multiplier_neutral_at_zero():
    assert zscore_diff_to_multiplier(0.0) == pytest.approx(1.0)


def test_zscore_diff_to_multiplier_positive_diff_increases_and_caps():
    m = zscore_diff_to_multiplier(5.0, cap=0.15)  # far positive -> approaches the cap
    assert 1.0 < m <= 1.15
    assert m == pytest.approx(1.15, abs=1e-3)


def test_zscore_diff_to_multiplier_none_in_none_out():
    assert zscore_diff_to_multiplier(None) is None


def test_snap_weighted_grade_prefers_snap_weighting_over_unweighted_mean():
    rows = [
        _grade_row("a", "KC", grade=90.0, snaps=100),
        _grade_row("b", "KC", grade=50.0, snaps=5),
    ]
    weighted, method = snap_weighted_grade(rows, "grade", "snaps")
    assert method == "snap_weighted"
    # Heavily weighted toward the 100-snap starter's grade, not a simple (90+50)/2=70 average.
    assert weighted == pytest.approx((90 * 100 + 50 * 5) / 105)
    assert weighted > 85


def test_snap_weighted_grade_falls_back_to_unweighted_mean_without_snap_field():
    rows = [_grade_row("a", "KC", grade=90.0), _grade_row("b", "KC", grade=50.0)]
    value, method = snap_weighted_grade(rows, "grade", None)
    assert method == "unweighted_mean"
    assert value == pytest.approx(70.0)


def test_team_aggregate_grades_rolls_up_by_team():
    rows = [
        _grade_row("a", "KC", grade=90.0, snaps=50),
        _grade_row("b", "KC", grade=80.0, snaps=50),
        _grade_row("c", "BUF", grade=60.0, snaps=50),
    ]
    facet = _facet(rows)
    aggregates = team_aggregate_grades(facet.by_player_id, "grade", "snaps")
    assert aggregates["KC"].value == pytest.approx(85.0)
    assert aggregates["BUF"].value == pytest.approx(60.0)


def test_population_zscore_undefined_for_small_population():
    assert population_zscore(90.0, [90.0]) is None


# --------------------------------------------------------------------------------------------
# run_game.py -- clear run-game mismatch produces the expected direction and magnitude
# --------------------------------------------------------------------------------------------


def test_run_game_strong_offense_vs_weak_defense_is_positive_and_capped():
    # KC has a clearly elite run-block grade vs. a league of average lines; opponent JAX has a
    # clearly weak run defense vs. a league of average defenses -- expect a strong positive
    # (near-cap) multiplier for KC's RB.
    offense_rows = [
        _grade_row("kc1", "KC", grades_run_block=95.0),
        _grade_row("buf1", "BUF", grades_run_block=70.0),
        _grade_row("mia1", "MIA", grades_run_block=68.0),
        _grade_row("nyj1", "NYJ", grades_run_block=72.0),
    ]
    defense_rows = [
        _grade_row("jax1", "JAX", grades_run_defense=40.0),
        _grade_row("buf2", "BUF", grades_run_defense=75.0),
        _grade_row("mia2", "MIA", grades_run_defense=73.0),
        _grade_row("nyj2", "NYJ", grades_run_defense=77.0),
    ]
    offense_agg = team_aggregate_grades(_facet(offense_rows).by_player_id, "grades_run_block", None)
    defense_agg = team_aggregate_grades(_facet(defense_rows).by_player_id, "grades_run_defense", None)

    result = compute_run_game_multiplier("KC", "JAX", offense_agg, defense_agg)
    assert result.multiplier is not None
    assert result.multiplier > 1.10  # strong positive matchup
    assert result.multiplier <= 1.15  # respects the PRD's stated cap
    assert result.offense_z > 0
    assert result.defense_z < 0


def test_run_game_missing_team_returns_none_with_reason():
    offense_agg = team_aggregate_grades(_facet([_grade_row("kc1", "KC", grades_run_block=95.0)]).by_player_id, "grades_run_block", None)
    defense_agg = {}
    result = compute_run_game_multiplier("KC", "JAX", offense_agg, defense_agg)
    assert result.multiplier is None
    assert "JAX" in result.reason


# --------------------------------------------------------------------------------------------
# pass_protection.py
# --------------------------------------------------------------------------------------------


def test_pass_protection_weak_offense_vs_strong_pass_rush_is_negative():
    offense_rows = [
        _grade_row("jax1", "JAX", grades_pass_block=55.0),
        _grade_row("kc1", "KC", grades_pass_block=85.0),
        _grade_row("buf1", "BUF", grades_pass_block=80.0),
    ]
    defense_rows = [
        _grade_row("den1", "DEN", pass_rush_win_rate=0.55),
        _grade_row("kc2", "KC", pass_rush_win_rate=0.35),
        _grade_row("buf2", "BUF", pass_rush_win_rate=0.30),
    ]
    offense_agg = team_aggregate_grades(_facet(offense_rows).by_player_id, "grades_pass_block", None)
    defense_agg = team_aggregate_grades(_facet(defense_rows).by_player_id, "pass_rush_win_rate", None)

    result = compute_pass_protection_multiplier("JAX", "DEN", offense_agg, defense_agg)
    assert result.multiplier is not None
    assert result.multiplier < 0.90  # weak pass-block vs. elite pass rush -> real negative
    assert result.multiplier >= 0.85


# --------------------------------------------------------------------------------------------
# coverage.py -- alignment gates
# --------------------------------------------------------------------------------------------


def test_alignment_gate_passes_with_clear_majority_and_snap_floor():
    snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="NE", slot_snaps=40, perimeter_snaps=5),
        DefenderAlignmentSnaps(native_id="cb2", team="NE", slot_snaps=10, perimeter_snaps=45),
    ]
    slot = identify_alignment_defender("NE", "slot", snaps)
    assert slot.gate_passed is True
    assert slot.defender_native_id == "cb1"
    assert slot.share > 0.5


def test_alignment_gate_fails_on_thin_margin():
    snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="NE", slot_snaps=25, perimeter_snaps=0),
        DefenderAlignmentSnaps(native_id="cb2", team="NE", slot_snaps=25, perimeter_snaps=0),
    ]
    slot = identify_alignment_defender("NE", "slot", snaps)
    assert slot.gate_passed is False
    assert "margin gate failed" in slot.reason


def test_alignment_gate_fails_on_snap_floor():
    snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="NE", slot_snaps=10, perimeter_snaps=0),
        DefenderAlignmentSnaps(native_id="cb2", team="NE", slot_snaps=2, perimeter_snaps=0),
    ]
    slot = identify_alignment_defender("NE", "slot", snaps)
    assert slot.gate_passed is False
    assert "snap-count floor failed" in slot.reason


def test_shadow_guardrail_forces_fallback_even_when_statistical_gates_pass():
    snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="NE", slot_snaps=0, perimeter_snaps=40),
        DefenderAlignmentSnaps(native_id="cb2", team="NE", slot_snaps=0, perimeter_snaps=5),
    ]
    shadow = ShadowCoverageSignal(match_rate=0.85)
    perimeter = identify_alignment_defender("NE", "perimeter", snaps, shadow_signal=shadow)
    assert perimeter.gate_passed is False
    assert "shadow-coverage guardrail" in perimeter.reason


def test_alignment_gate_no_data_falls_back():
    result = identify_alignment_defender("NE", "slot", [])
    assert result.gate_passed is False
    assert "no slot coverage-snap data" in result.reason


# --------------------------------------------------------------------------------------------
# coverage.py -- full multiplier computation, confident vs. team-wide fallback
# --------------------------------------------------------------------------------------------


def _coverage_scenario():
    coverage_rows = [
        # Defense NE: weak slot corner (cb1, elite grade), weak perimeter corner (cb2, poor grade).
        _grade_row("cb1", "NE", man_grades_coverage_defense=90.0, zone_grades_coverage_defense=88.0,
                   man_snap_counts_coverage=40, zone_snap_counts_coverage=40),
        _grade_row("cb2", "NE", man_grades_coverage_defense=55.0, zone_grades_coverage_defense=58.0,
                   man_snap_counts_coverage=40, zone_snap_counts_coverage=40),
        # A league-mate defense for population purposes.
        _grade_row("cb3", "MIA", man_grades_coverage_defense=70.0, zone_grades_coverage_defense=72.0,
                   man_snap_counts_coverage=40, zone_snap_counts_coverage=40),
    ]
    receiving_rows = [
        _grade_row("wr1", "KC", position="WR", man_grades_pass_route=85.0, zone_grades_pass_route=80.0,
                   man_targets_percent=0.6, zone_targets_percent=0.4),
        _grade_row("wr2", "MIA", position="WR", man_grades_pass_route=70.0, zone_grades_pass_route=68.0,
                   man_targets_percent=0.5, zone_targets_percent=0.5),
    ]
    coverage_facet = _facet(coverage_rows)
    receiving_facet = _facet(receiving_rows)
    league_man_pop = [r.grades["man_grades_coverage_defense"] for r in coverage_rows]
    league_zone_pop = [r.grades["zone_grades_coverage_defense"] for r in coverage_rows]
    league_receiver_man_pop = [r.grades["man_grades_pass_route"] for r in receiving_rows]
    league_receiver_zone_pop = [r.grades["zone_grades_pass_route"] for r in receiving_rows]
    return coverage_facet, receiving_facet, league_man_pop, league_zone_pop, league_receiver_man_pop, league_receiver_zone_pop


def test_coverage_confident_alignment_identification_uses_identified_defender():
    coverage_facet, receiving_facet, man_pop, zone_pop, r_man_pop, r_zone_pop = _coverage_scenario()
    man_rate, zone_rate = receiver_man_zone_rate("wr1", receiving_facet.by_player_id)

    defender_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="NE", slot_snaps=40, perimeter_snaps=5),
        DefenderAlignmentSnaps(native_id="cb2", team="NE", slot_snaps=5, perimeter_snaps=40),
    ]
    receiver_share = ReceiverAlignmentShare(player_id="wr1", slot_share=1.0, perimeter_share=0.0)

    result = compute_coverage_multiplier(
        receiver_id="wr1", receiver_team="KC", defense_team="NE",
        receiver_man_rate=man_rate, receiver_zone_rate=zone_rate,
        coverage_facet_rows=coverage_facet.by_player_id, receiving_facet_rows=receiving_facet.by_player_id,
        league_defender_man_population=man_pop, league_defender_zone_population=zone_pop,
        league_receiver_man_population=r_man_pop, league_receiver_zone_population=r_zone_pop,
        receiver_alignment_share=receiver_share, defender_alignment_snaps=defender_snaps,
    )
    assert result.confidence == "confident"
    # wr1 lines up 100% slot, facing cb1 (the weak/elite-graded slot corner) -- his effective
    # defender grade should equal cb1's raw grades exactly (a pure 100% weight on one defender).
    assert result.defender_man_grade == pytest.approx(90.0)
    assert result.defender_zone_grade == pytest.approx(88.0)
    assert result.multiplier is not None


def test_coverage_falls_back_to_team_wide_grade_when_alignment_data_missing():
    coverage_facet, receiving_facet, man_pop, zone_pop, r_man_pop, r_zone_pop = _coverage_scenario()
    man_rate, zone_rate = receiver_man_zone_rate("wr1", receiving_facet.by_player_id)

    result = compute_coverage_multiplier(
        receiver_id="wr1", receiver_team="KC", defense_team="NE",
        receiver_man_rate=man_rate, receiver_zone_rate=zone_rate,
        coverage_facet_rows=coverage_facet.by_player_id, receiving_facet_rows=receiving_facet.by_player_id,
        league_defender_man_population=man_pop, league_defender_zone_population=zone_pop,
        league_receiver_man_population=r_man_pop, league_receiver_zone_population=r_zone_pop,
        # No alignment data supplied at all -- the real-world case today.
    )
    assert result.confidence == "team_wide_fallback"
    # Team-wide snap-weighted grade across cb1 (90/88) and cb2 (55/58), equal snaps -> simple mean.
    assert result.defender_man_grade == pytest.approx((90.0 + 55.0) / 2)
    assert result.defender_zone_grade == pytest.approx((88.0 + 58.0) / 2)


def test_coverage_falls_back_when_gate_fails_even_with_alignment_data_supplied():
    coverage_facet, receiving_facet, man_pop, zone_pop, r_man_pop, r_zone_pop = _coverage_scenario()
    man_rate, zone_rate = receiver_man_zone_rate("wr1", receiving_facet.by_player_id)

    # Thin margin (exactly 50/50) on both alignments -- neither gate should pass (the gate
    # requires a strict majority, > 50%).
    defender_snaps = [
        DefenderAlignmentSnaps(native_id="cb1", team="NE", slot_snaps=25, perimeter_snaps=25),
        DefenderAlignmentSnaps(native_id="cb2", team="NE", slot_snaps=25, perimeter_snaps=25),
    ]
    receiver_share = ReceiverAlignmentShare(player_id="wr1", slot_share=1.0, perimeter_share=0.0)

    result = compute_coverage_multiplier(
        receiver_id="wr1", receiver_team="KC", defense_team="NE",
        receiver_man_rate=man_rate, receiver_zone_rate=zone_rate,
        coverage_facet_rows=coverage_facet.by_player_id, receiving_facet_rows=receiving_facet.by_player_id,
        league_defender_man_population=man_pop, league_defender_zone_population=zone_pop,
        league_receiver_man_population=r_man_pop, league_receiver_zone_population=r_zone_pop,
        receiver_alignment_share=receiver_share, defender_alignment_snaps=defender_snaps,
    )
    assert result.confidence == "team_wide_fallback"
    assert "ADR-0006 gate(s) failed" in result.reason


# --------------------------------------------------------------------------------------------
# context.py -- ADR-0005 combination of pass-protection flow-through + coverage
# --------------------------------------------------------------------------------------------


def test_receiver_combines_pass_protection_and_coverage_via_capped_log_space():
    from nfl_dfs.matchup.coverage import CoverageMultiplier
    from nfl_dfs.matchup.pass_protection import PassProtectionMultiplier

    coverage = CoverageMultiplier(
        receiver_id="wr1", receiver_team="KC", defense_team="NE", multiplier=1.15,
        confidence="team_wide_fallback", defender_man_grade=50.0, defender_zone_grade=50.0,
        receiver_man_grade=90.0, receiver_zone_grade=90.0, z_diff=2.0,
    )
    pass_protection = PassProtectionMultiplier(
        offense_team="KC", defense_team="NE", multiplier=1.15, offense_grade=90.0, defense_grade=30.0,
        offense_z=2.0, defense_z=-2.0,
    )
    result = build_matchup_context_for_receiver("wr1", "KC", "NE", coverage, pass_protection)
    naive_product = 1.15 * 1.15
    assert result.combined_multiplier < naive_product  # capped, not naive multiplication
    assert result.combined_multiplier <= 1.20 + 1e-9
    assert result.coverage_confidence == "team_wide_fallback"


def test_receiver_uses_single_multiplier_directly_when_only_one_row_computable():
    from nfl_dfs.matchup.coverage import CoverageMultiplier

    coverage = CoverageMultiplier(
        receiver_id="wr1", receiver_team="KC", defense_team="NE", multiplier=1.08,
        confidence="confident", defender_man_grade=50.0, defender_zone_grade=50.0,
        receiver_man_grade=70.0, receiver_zone_grade=70.0, z_diff=0.5,
    )
    result = build_matchup_context_for_receiver("wr1", "KC", "NE", coverage, pass_protection_multiplier=None)
    assert result.combined_multiplier == pytest.approx(1.08)


# --------------------------------------------------------------------------------------------
# context.py -- full pool orchestration (position dispatch, bye weeks, missing data)
# --------------------------------------------------------------------------------------------


def _pool_facets() -> MatchupFacetInputs:
    run_blocking = _facet([
        _grade_row("kc_ol", "KC", grades_run_block=90.0),
        _grade_row("jax_ol", "JAX", grades_run_block=65.0),
    ])
    run_defense = _facet([
        _grade_row("kc_dl", "KC", grades_run_defense=70.0),
        _grade_row("jax_dl", "JAX", grades_run_defense=40.0),
    ])
    pass_blocking = _facet([
        _grade_row("kc_ol2", "KC", grades_pass_block=85.0),
        _grade_row("jax_ol2", "JAX", grades_pass_block=60.0),
    ])
    pass_rush = _facet([
        _grade_row("kc_dl2", "KC", pass_rush_win_rate=0.55),
        _grade_row("jax_dl2", "JAX", pass_rush_win_rate=0.40),
    ])
    coverage_scheme = _facet([
        _grade_row("kc_cb", "KC", man_grades_coverage_defense=80.0, zone_grades_coverage_defense=78.0,
                   man_snap_counts_coverage=30, zone_snap_counts_coverage=30),
        _grade_row("jax_cb", "JAX", man_grades_coverage_defense=55.0, zone_grades_coverage_defense=58.0,
                   man_snap_counts_coverage=30, zone_snap_counts_coverage=30),
    ])
    receiving_scheme = _facet([
        _grade_row("wr_kc", "KC", position="WR", man_grades_pass_route=75.0, zone_grades_pass_route=72.0,
                   man_targets_percent=0.55, zone_targets_percent=0.45),
    ])
    return MatchupFacetInputs(
        run_blocking=run_blocking, run_defense=run_defense, pass_blocking=pass_blocking,
        pass_rush=pass_rush, coverage_scheme=coverage_scheme, receiving_scheme=receiving_scheme,
    )


def test_pool_dispatches_by_position_and_computes_sensible_directions():
    facets = _pool_facets()
    players = [
        PlayerMatchupInput(canonical_player_id="rb_kc", team="KC", position="RB"),
        PlayerMatchupInput(canonical_player_id="qb_kc", team="KC", position="QB"),
        PlayerMatchupInput(canonical_player_id="wr_kc", team="KC", position="WR", pff_native_id="wr_kc"),
        PlayerMatchupInput(canonical_player_id="dst_kc", team="KC", position="DST"),
    ]
    opponents = {"KC": "JAX", "JAX": "KC"}
    results = build_matchup_context_pool(players, opponents, facets)

    # KC's O-line grades far better than JAX's D everywhere -- expect every offensive KC player's
    # combined multiplier to be >= 1.0 (favorable matchup direction).
    assert results["rb_kc"].combined_multiplier > 1.0
    assert results["qb_kc"].combined_multiplier > 1.0
    assert results["wr_kc"].combined_multiplier >= 1.0
    # DST has no MatchupContext row -- neutral, explained.
    assert results["dst_kc"].combined_multiplier == pytest.approx(1.0)
    assert results["dst_kc"].notes


def test_pool_skips_player_with_no_opponent_this_week():
    facets = _pool_facets()
    players = [PlayerMatchupInput(canonical_player_id="bye_rb", team="NE", position="RB")]
    results = build_matchup_context_pool(players, opponents={}, facets=facets)
    assert results["bye_rb"].combined_multiplier == pytest.approx(1.0)
    assert "no opponent found" in results["bye_rb"].notes[0]


# --------------------------------------------------------------------------------------------
# composition/player_detail.py wiring -- own_unit_grade/opponent_unit_grade
# --------------------------------------------------------------------------------------------


def test_resolve_own_opponent_unit_grades_for_rb():
    facets = _pool_facets()
    own, opp, reason = resolve_own_opponent_unit_grades("RB", "KC", "JAX", facets)
    assert own is not None and own.grades["grades_run_block"] == pytest.approx(90.0)
    assert opp is not None and opp.grades["grades_run_defense"] == pytest.approx(40.0)
    assert reason is None


def test_resolve_own_opponent_unit_grades_wr_has_no_own_unit():
    facets = _pool_facets()
    own, opp, reason = resolve_own_opponent_unit_grades("WR", "KC", "JAX", facets)
    assert own is None
    assert opp is not None
    assert "not applicable for WR/TE" in reason


def test_resolve_own_opponent_unit_grades_no_opponent():
    facets = _pool_facets()
    own, opp, reason = resolve_own_opponent_unit_grades("RB", "KC", None, facets)
    assert own is None and opp is None
    assert "no opponent" in reason
