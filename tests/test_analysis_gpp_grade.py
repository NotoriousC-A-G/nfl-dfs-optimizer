from nfl_dfs.analysis.gpp_grade import compute_gpp_grade, compute_max_ceiling_weighted_total
from nfl_dfs.optimizer.lineup import Lineup
from nfl_dfs.projection.blend import PlayerProjection


def _p(canonical_id: str, position: str, team: str, salary: int, projection: float) -> PlayerProjection:
    return PlayerProjection(
        canonical_id=canonical_id,
        display_name=canonical_id,
        position=position,
        team=team,
        salary=salary,
        blended_projection=projection,
        source_count=2,
        source_values={"rotogrinders": projection, "footballguys": projection},
    )


def _synthetic_pool() -> list[PlayerProjection]:
    return [
        _p("qb_a", "QB", "AAA", 7500, 22.0),
        _p("wr_a1", "WR", "AAA", 7000, 18.0),
        _p("wr_a2", "WR", "AAA", 6000, 14.0),
        _p("te_a1", "TE", "AAA", 4500, 10.0),
        _p("rb_a1", "RB", "AAA", 6500, 15.0),
        _p("rb_a2", "RB", "AAA", 5000, 11.0),
        _p("dst_a", "DST", "AAA", 3000, 8.0),
        _p("qb_b", "QB", "BBB", 7200, 21.0),
        _p("wr_b1", "WR", "BBB", 6800, 17.0),
        _p("wr_b2", "WR", "BBB", 5800, 13.5),
        _p("te_b1", "TE", "BBB", 4200, 9.5),
        _p("rb_b1", "RB", "BBB", 6200, 14.5),
        _p("rb_b2", "RB", "BBB", 4800, 10.5),
        _p("dst_b", "DST", "BBB", 2800, 7.5),
        _p("wr_c1", "WR", "CCC", 3500, 8.0),
        _p("wr_c2", "WR", "CCC", 3200, 7.0),
        _p("te_c1", "TE", "CCC", 2800, 5.0),
        _p("rb_c1", "RB", "CCC", 3800, 8.5),
        _p("rb_d1", "RB", "DDD", 3600, 7.5),
    ]


def _lineup(players: tuple[PlayerProjection, ...]) -> Lineup:
    return Lineup(
        slots={}, players=players, total_salary=sum(p.salary for p in players),
        total_projected_points=sum(p.blended_projection for p in players),
        core_stack=frozenset({players[0].canonical_id}), core_stack_team=players[0].team,
    )


def _nine_player_lineup(pool: list[PlayerProjection]) -> Lineup:
    return _lineup(tuple(pool[:9]))


def test_compute_gpp_grade_low_ownership_lineup_grades_well_on_differentiation():
    pool = _synthetic_pool()
    lineup = _nine_player_lineup(pool)
    low_ownership = {p.canonical_id: 2.0 for p in lineup.players}  # every player barely owned
    grade = compute_gpp_grade(
        lineup,
        projected_ownership_by_canonical_id=low_ownership,
        ceiling_multiplier_by_canonical_id=None,
        max_ceiling_weighted_total=None,
        game_count=13,
    )
    assert grade.differentiation_score == 4.0
    assert grade.expected_shared == pool_own_sum(lineup, low_ownership)


def pool_own_sum(lineup, own):
    return sum(own.get(p.canonical_id, 0.0) / 100.0 for p in lineup.players)


def test_compute_gpp_grade_high_ownership_lineup_grades_poorly_on_differentiation():
    pool = _synthetic_pool()
    lineup = _nine_player_lineup(pool)
    high_ownership = {p.canonical_id: 45.0 for p in lineup.players}
    grade = compute_gpp_grade(
        lineup,
        projected_ownership_by_canonical_id=high_ownership,
        ceiling_multiplier_by_canonical_id=None,
        max_ceiling_weighted_total=None,
        game_count=13,
    )
    assert grade.differentiation_score == 1.0


def test_compute_gpp_grade_concentration_bonus_softens_one_chalk_play():
    pool = _synthetic_pool()
    lineup = _nine_player_lineup(pool)
    # One player far above the concentration floor, everyone else genuinely low-owned.
    ownership = {p.canonical_id: 3.0 for p in lineup.players}
    ownership[lineup.players[0].canonical_id] = 40.0
    grade_with_concentration = compute_gpp_grade(
        lineup, projected_ownership_by_canonical_id=ownership, ceiling_multiplier_by_canonical_id=None,
        max_ceiling_weighted_total=None, game_count=13,
    )
    # Real, not-concentrated version at the same raw expected_shared for comparison.
    flat_ownership = {p.canonical_id: (sum(ownership.values()) / len(ownership)) for p in lineup.players}
    grade_flat = compute_gpp_grade(
        lineup, projected_ownership_by_canonical_id=flat_ownership, ceiling_multiplier_by_canonical_id=None,
        max_ceiling_weighted_total=None, game_count=13,
    )
    assert grade_with_concentration.differentiation_score >= grade_flat.differentiation_score


def test_compute_gpp_grade_ceiling_ratio_none_when_no_real_signal_anywhere():
    pool = _synthetic_pool()
    lineup = _nine_player_lineup(pool)
    ownership = {p.canonical_id: 10.0 for p in lineup.players}
    grade = compute_gpp_grade(
        lineup, projected_ownership_by_canonical_id=ownership, ceiling_multiplier_by_canonical_id=None,
        max_ceiling_weighted_total=None, game_count=13,
    )
    assert grade.ceiling_ratio is None
    assert grade.ceiling_reason is not None
    assert "no rostered player has a real Component A" in grade.ceiling_reason


def test_compute_gpp_grade_ceiling_ratio_real_when_signal_and_max_total_present():
    pool = _synthetic_pool()
    lineup = _nine_player_lineup(pool)
    ownership = {p.canonical_id: 10.0 for p in lineup.players}
    ceiling_by_id = {lineup.players[0].canonical_id: 1.2}
    total, _ = (
        sum(p.blended_projection * (ceiling_by_id.get(p.canonical_id) or 1.0) for p in lineup.players),
        None,
    )
    grade = compute_gpp_grade(
        lineup, projected_ownership_by_canonical_id=ownership, ceiling_multiplier_by_canonical_id=ceiling_by_id,
        max_ceiling_weighted_total=total * 1.1,  # a real, slightly-higher "best of slate" reference
        game_count=13,
    )
    assert grade.ceiling_ratio is not None
    assert grade.ceiling_reason is None
    assert 0.0 < grade.ceiling_ratio < 1.0


def test_compute_gpp_grade_letter_grade_is_a_when_both_components_are_strong():
    pool = _synthetic_pool()
    lineup = _nine_player_lineup(pool)
    ownership = {p.canonical_id: 2.0 for p in lineup.players}
    ceiling_by_id = {p.canonical_id: 1.3 for p in lineup.players}
    grade = compute_gpp_grade(
        lineup, projected_ownership_by_canonical_id=ownership, ceiling_multiplier_by_canonical_id=ceiling_by_id,
        max_ceiling_weighted_total=lineup.total_projected_points * 1.3,  # this lineup IS ~the real best
        game_count=13,
    )
    assert grade.grade == "A"


def test_compute_max_ceiling_weighted_total_none_when_pool_has_no_real_ceiling_signal():
    pool = _synthetic_pool()
    assert compute_max_ceiling_weighted_total(pool, None) is None
    assert compute_max_ceiling_weighted_total(pool, {}) is None


def test_compute_max_ceiling_weighted_total_real_value_when_signal_present():
    pool = _synthetic_pool()
    ceiling_by_id = {"wr_a1": 1.5}  # a real, strong ceiling signal on one real player
    total = compute_max_ceiling_weighted_total(pool, ceiling_by_id)
    assert total is not None
    assert total > 0.0
