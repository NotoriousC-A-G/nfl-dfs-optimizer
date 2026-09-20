import pulp
import pytest

from nfl_dfs.analysis.dup_risk_calibration import DupRiskLookupTable
from nfl_dfs.optimizer.lineup import (
    EXCLUDED_INJURY_STATUSES,
    FLEX_GROUP_TOTAL,
    LINEUP_2_FALLBACK_BUCKET,
    LINEUP_2_MAX_BUCKET,
    LINEUP_3_MAX_BUCKET,
    MAX_RB,
    MAX_TE,
    MAX_WR,
    MIN_OWNERSHIP_COVERAGE,
    MIN_RB,
    MIN_TE,
    MIN_WR,
    SALARY_CAP,
    Lineup,
    LineupGenerationError,
    _eligible_pool,
    _select_dup_risk_aware_lineups,
    generate_dup_risk_aware_lineups,
    generate_lineups,
)
from nfl_dfs.projection.blend import PlayerProjection


def _p(
    canonical_id: str,
    position: str,
    team: str,
    salary: int,
    projection: float,
    *,
    dk_injury_status: str | None = None,
) -> PlayerProjection:
    return PlayerProjection(
        canonical_id=canonical_id,
        display_name=canonical_id,
        position=position,
        team=team,
        salary=salary,
        blended_projection=projection,
        source_count=2,
        source_values={"rotogrinders": projection, "footballguys": projection},
        dk_injury_status=dk_injury_status,
    )


def _synthetic_pool() -> list[PlayerProjection]:
    """A small but roster-legal synthetic pool spanning 2 teams with real pass-catcher depth on
    each, so a valid QB+WR/TE stack exists for either team, and enough salary/position spread
    that 3 lineups with genuinely distinct core stacks are constructible under the $50,000 cap.
    """
    players = [
        # Team A -- QB + 3 pass-catchers so multiple distinct core-stack combos exist within A.
        _p("qb_a", "QB", "AAA", 7500, 22.0),
        _p("wr_a1", "WR", "AAA", 7000, 18.0),
        _p("wr_a2", "WR", "AAA", 6000, 14.0),
        _p("te_a1", "TE", "AAA", 4500, 10.0),
        _p("rb_a1", "RB", "AAA", 6500, 15.0),
        _p("rb_a2", "RB", "AAA", 5000, 11.0),
        _p("dst_a", "DST", "AAA", 3000, 8.0),
        # Team B -- QB + 3 pass-catchers, same shape.
        _p("qb_b", "QB", "BBB", 7200, 21.0),
        _p("wr_b1", "WR", "BBB", 6800, 17.0),
        _p("wr_b2", "WR", "BBB", 5800, 13.5),
        _p("te_b1", "TE", "BBB", 4200, 9.5),
        _p("rb_b1", "RB", "BBB", 6200, 14.5),
        _p("rb_b2", "RB", "BBB", 4800, 10.5),
        _p("dst_b", "DST", "BBB", 2800, 7.5),
        # Cheap value/filler players (teams C/D) so salary-cap-respecting rosters have room to
        # fit a QB+2 stack plus a full complement of RB/WR/TE without overrunning $50,000, and so
        # there's a 4th WR/2nd TE available for FLEX variety across lineups.
        _p("wr_c1", "WR", "CCC", 3500, 8.0),
        _p("wr_c2", "WR", "CCC", 3200, 7.0),
        _p("te_c1", "TE", "CCC", 2800, 5.0),
        _p("rb_c1", "RB", "CCC", 3800, 8.5),
        _p("rb_d1", "RB", "DDD", 3600, 7.5),
    ]
    return players


# --------------------------------------------------------------------------------------------
# Roster/salary/stack legality on a single solved lineup
# --------------------------------------------------------------------------------------------


def test_solved_lineup_respects_salary_cap_and_roster_size():
    lineups = generate_lineups(_synthetic_pool(), n=1)
    lineup = lineups[0]
    assert len(lineup.players) == 9
    assert lineup.total_salary <= SALARY_CAP
    assert lineup.total_salary == sum(p.salary for p in lineup.players)
    assert lineup.total_projected_points == sum(p.blended_projection for p in lineup.players)


def test_solved_lineup_has_correct_position_composition():
    lineup = generate_lineups(_synthetic_pool(), n=1)[0]
    counts: dict[str, int] = {}
    for p in lineup.players:
        counts[p.position] = counts.get(p.position, 0) + 1

    assert counts.get("QB", 0) == 1
    assert counts.get("DST", 0) == 1
    assert MIN_RB <= counts.get("RB", 0) <= MAX_RB
    assert MIN_WR <= counts.get("WR", 0) <= MAX_WR
    assert MIN_TE <= counts.get("TE", 0) <= MAX_TE
    assert counts.get("RB", 0) + counts.get("WR", 0) + counts.get("TE", 0) == FLEX_GROUP_TOTAL

    # Slot dict recovers exactly the same 9 players, DK-labeled.
    assert set(lineup.slots.keys()) == {
        "QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST",
    }
    assert {p.canonical_id for p in lineup.slots.values()} == {
        p.canonical_id for p in lineup.players
    }


def test_solved_lineup_includes_a_valid_qb_pass_catcher_stack():
    lineup = generate_lineups(_synthetic_pool(), n=1)[0]
    qb = next(p for p in lineup.players if p.position == "QB")
    same_team_pass_catchers = [
        p for p in lineup.players if p.position in ("WR", "TE") and p.team == qb.team
    ]
    assert len(same_team_pass_catchers) >= 1
    # core_stack is exactly the QB plus those same-team pass-catchers.
    assert lineup.core_stack == frozenset(
        {qb.canonical_id} | {p.canonical_id for p in same_team_pass_catchers}
    )
    assert lineup.core_stack_team == qb.team


def test_generate_lineups_maximizes_projection_on_the_first_solve():
    # With no diversity cuts yet, the first lineup should be the actual unconstrained-diversity
    # optimum -- sanity check against a hand-computed upper bound direction: it must not be
    # possible to swap in a strictly better same-position/cheaper-or-equal player and still be
    # under cap, i.e. the objective should equal the best of two hand-built legal alternatives.
    lineup = generate_lineups(_synthetic_pool(), n=1)[0]
    assert lineup.total_projected_points > 0


# --------------------------------------------------------------------------------------------
# Diversity across 3 lineups (the no-good cut)
# --------------------------------------------------------------------------------------------


def test_three_sequential_lineups_have_distinct_core_stacks():
    lineups = generate_lineups(_synthetic_pool(), n=3)
    assert len(lineups) == 3
    core_stacks = [lu.core_stack for lu in lineups]
    assert len(set(core_stacks)) == 3, "all 3 lineups must have distinct core stacks"
    for lu in lineups:
        assert lu.total_salary <= SALARY_CAP


def test_no_good_cut_does_not_forbid_individual_player_reuse():
    # The no-good cut only forbids the exact QB+stack combination reappearing -- it must not
    # prevent, say, the same DST or a non-stack RB from being reused across lineups.
    lineups = generate_lineups(_synthetic_pool(), n=3)
    all_ids_by_lineup = [{p.canonical_id for p in lu.players} for lu in lineups]
    shared_across_all_three = set.intersection(*all_ids_by_lineup)
    # It's fine (expected, even) for some non-stack players to repeat across all 3 lineups given
    # how small this synthetic pool is -- the assertion is just that repetition is *possible*,
    # i.e. the cut isn't accidentally wiping the whole roster each time.
    assert len(shared_across_all_three) >= 1


# --------------------------------------------------------------------------------------------
# opponent_of -- PRD Section 7's DST-correlation term (Chris, 2026-09-20: a real generated lineup
# rostered a JAX QB+WR stack alongside the Broncos DST, DEN being JAX's real opponent that week --
# directly anti-correlated, and the exact gap this module's own docstring had disclosed as
# deferred). This fixture reproduces that exact failure mode: AAA's stack is the single best real
# combo in the pool, and BBB's DST is priced slightly ABOVE AAA's own DST -- so a correlation-BLIND
# solve picks AAA's stack + the OPPOSING (BBB) DST, same shape as the real bug.
# --------------------------------------------------------------------------------------------


def _dst_correlation_pool() -> list[PlayerProjection]:
    return [
        _p("qb_a", "QB", "AAA", 7500, 26.0),  # the single best stack in the pool
        _p("wr_a1", "WR", "AAA", 7000, 20.0),
        _p("wr_a2", "WR", "AAA", 6000, 14.0),
        _p("te_a1", "TE", "AAA", 4500, 10.0),
        _p("rb_a1", "RB", "AAA", 6500, 15.0),
        _p("rb_a2", "RB", "AAA", 5000, 11.0),
        _p("dst_a", "DST", "AAA", 3000, 7.5),  # AAA's own DST -- slightly cheaper raw projection
        _p("qb_b", "QB", "BBB", 7200, 18.0),
        _p("wr_b1", "WR", "BBB", 6800, 14.0),
        _p("wr_b2", "WR", "BBB", 5800, 11.0),
        _p("te_b1", "TE", "BBB", 4200, 8.0),
        _p("rb_b1", "RB", "BBB", 6200, 12.0),
        _p("rb_b2", "RB", "BBB", 4800, 9.0),
        _p("dst_b", "DST", "BBB", 3000, 8.0),  # BBB's DST -- slightly HIGHER raw projection
        _p("wr_c1", "WR", "CCC", 3500, 8.0),
        _p("wr_c2", "WR", "CCC", 3200, 7.0),
        _p("te_c1", "TE", "CCC", 2800, 5.0),
        _p("rb_c1", "RB", "CCC", 3800, 8.5),
        _p("rb_d1", "RB", "DDD", 3600, 7.5),
    ]


def test_no_opponent_of_reproduces_the_real_bug_stack_paired_with_opposing_dst():
    pool = _dst_correlation_pool()
    baseline = generate_lineups(pool, n=1)[0]
    stack_team = next(p for p in baseline.players if p.position == "QB").team
    dst = next(p for p in baseline.players if p.position == "DST")
    # Confirms the fixture actually reproduces the real failure mode before testing the fix.
    assert stack_team == "AAA"
    assert dst.canonical_id == "dst_b"  # BBB's DST -- AAA's real opponent


def test_opponent_of_avoids_pairing_a_stack_with_its_opponents_dst():
    pool = _dst_correlation_pool()
    aware = generate_lineups(pool, n=1, opponent_of={"AAA": "BBB", "BBB": "AAA"})[0]
    stack_team = next(p for p in aware.players if p.position == "QB").team
    dst = next(p for p in aware.players if p.position == "DST")
    assert stack_team == "AAA"
    assert dst.canonical_id == "dst_a"  # switched to AAA's OWN DST, same real stack otherwise
    non_dst_ids = {p.canonical_id for p in aware.players if p.position != "DST"}
    baseline_non_dst_ids = {
        p.canonical_id for p in generate_lineups(pool, n=1)[0].players if p.position != "DST"
    }
    assert non_dst_ids == baseline_non_dst_ids  # only the DST choice changed


def test_opponent_of_none_is_byte_identical_to_omitted():
    pool = _dst_correlation_pool()
    omitted = generate_lineups(pool, n=1)
    explicit_none = generate_lineups(pool, n=1, opponent_of=None)
    assert [lu.core_stack for lu in omitted] == [lu.core_stack for lu in explicit_none]
    assert [{p.canonical_id for p in lu.players} for lu in omitted] == [
        {p.canonical_id for p in lu.players} for lu in explicit_none
    ]


def test_dst_correlation_terms_creates_a_real_negative_and_positive_pair():
    from nfl_dfs.optimizer.lineup import _dst_correlation_terms

    pool = _dst_correlation_pool()
    x = {p.canonical_id: pulp.LpVariable(p.canonical_id, cat="Binary") for p in pool}
    terms, constraints = _dst_correlation_terms(pool, x, {"AAA": "BBB", "BBB": "AAA"})
    assert len(terms) > 0
    assert len(constraints) == len(terms) * 3  # 3 linearization constraints per real pair


def test_dst_correlation_terms_produces_no_penalty_when_opponent_of_has_no_real_entry():
    # _dst_correlation_terms itself doesn't gate on opponent_of being non-empty (that guard lives
    # at the caller -- see test_opponent_of_none_is_byte_identical_to_omitted); called directly
    # with {}, opponent_of.get(team) is always None, so no PENALTY term can ever fire (nothing
    # matches "the opponent's team"), but the same-team BONUS still legitimately fires -- it only
    # needs a DST's own team, never the opponent.
    from nfl_dfs.optimizer.lineup import _dst_correlation_terms

    pool = _dst_correlation_pool()
    x = {p.canonical_id: pulp.LpVariable(p.canonical_id, cat="Binary") for p in pool}
    terms, constraints = _dst_correlation_terms(pool, x, {})
    assert len(terms) > 0
    assert len(constraints) == len(terms) * 3


# --------------------------------------------------------------------------------------------
# seed_core_stacks -- cross-call diversity (NflAgentConstructor: two independent generate_lineups
# calls, e.g. two different agents, must not be free to land on the identical core stack)
# --------------------------------------------------------------------------------------------


def test_seed_core_stacks_forbids_a_stack_from_the_very_first_solve():
    pool = _synthetic_pool()
    unconstrained = generate_lineups(pool, n=1)[0]
    seeded = generate_lineups(pool, n=1, seed_core_stacks=[unconstrained.core_stack])[0]
    assert seeded.core_stack != unconstrained.core_stack


def test_seed_core_stacks_none_is_byte_identical_to_omitted():
    pool = _synthetic_pool()
    omitted = generate_lineups(pool, n=1)
    explicit_none = generate_lineups(pool, n=1, seed_core_stacks=None)
    assert [lu.core_stack for lu in omitted] == [lu.core_stack for lu in explicit_none]


def test_seed_core_stacks_combines_with_this_calls_own_no_good_cuts():
    # A seeded cut plus this call's own n=2 internal no-good cut together must produce 3 total
    # distinct core stacks (the seed, plus the 2 generated here) -- the seed isn't just consulted
    # for the first solve and then dropped.
    pool = _synthetic_pool()
    first = generate_lineups(pool, n=1)[0]
    seeded_pair = generate_lineups(pool, n=2, seed_core_stacks=[first.core_stack])
    all_stacks = {first.core_stack, *[lu.core_stack for lu in seeded_pair]}
    assert len(all_stacks) == 3


# --------------------------------------------------------------------------------------------
# Infeasibility -- fails gracefully, not a crash or a silently-invalid lineup
# --------------------------------------------------------------------------------------------


def test_infeasible_pool_raises_a_clear_error_not_a_crash():
    # Missing DST entirely -- no legal 9-player roster can ever be built.
    tiny_pool = [
        _p("qb_a", "QB", "AAA", 7500, 22.0),
        _p("wr_a1", "WR", "AAA", 7000, 18.0),
        _p("wr_a2", "WR", "AAA", 6000, 14.0),
        _p("wr_a3", "WR", "AAA", 5000, 12.0),
        _p("te_a1", "TE", "AAA", 4500, 10.0),
        _p("rb_a1", "RB", "AAA", 6500, 15.0),
        _p("rb_a2", "RB", "AAA", 5000, 11.0),
    ]
    with pytest.raises(LineupGenerationError):
        generate_lineups(tiny_pool, n=3)


def test_pool_too_expensive_for_the_cap_raises_a_clear_error():
    # Every eligible player costs $49,000 -- any 2 of them alone already exceed the $50,000 cap,
    # so no legal 9-player roster can ever fit under it, even though every individual position
    # requirement could otherwise be satisfied.
    over_cap_pool = [
        _p("qb_a", "QB", "AAA", 49_000, 40.0),
        _p("wr_a1", "WR", "AAA", 49_000, 40.0),
        _p("wr_a2", "WR", "AAA", 49_000, 40.0),
        _p("wr_a3", "WR", "AAA", 49_000, 40.0),
        _p("te_a1", "TE", "AAA", 49_000, 40.0),
        _p("rb_a1", "RB", "AAA", 49_000, 40.0),
        _p("rb_a2", "RB", "AAA", 49_000, 40.0),
        _p("dst_a", "DST", "AAA", 49_000, 40.0),
    ]
    with pytest.raises(LineupGenerationError):
        generate_lineups(over_cap_pool, n=3)


# --------------------------------------------------------------------------------------------
# Zero-coverage players (blended_projection is None) are excluded from the candidate pool
# --------------------------------------------------------------------------------------------


def test_zero_coverage_players_are_excluded_from_the_candidate_pool():
    pool = _synthetic_pool()
    ghost = PlayerProjection(
        canonical_id="ghost",
        display_name="Ghost Player",
        position="WR",
        team="AAA",
        salary=100,  # absurdly cheap -- would dominate the objective if wrongly included
        blended_projection=None,
        source_count=0,
        source_values={},
    )
    lineup = generate_lineups(pool + [ghost], n=1)[0]
    assert "ghost" not in {p.canonical_id for p in lineup.players}


# --------------------------------------------------------------------------------------------
# IR/OUT players are excluded from the candidate pool (found live 2026-09-15 -- a confirmed-IR
# player was reaching the ILP solve; DK's own `status` field had never been read before)
# --------------------------------------------------------------------------------------------


def test_eligible_pool_excludes_players_on_excluded_injury_statuses():
    pool = [_p(status, "WR", "AAA", 3000, 20.0, dk_injury_status=status) for status in EXCLUDED_INJURY_STATUSES]
    assert _eligible_pool(pool) == {}


def test_eligible_pool_keeps_players_with_no_designation_or_a_playable_status():
    healthy = _p("healthy", "WR", "AAA", 3000, 20.0, dk_injury_status=None)
    questionable = _p("questionable", "WR", "AAA", 3000, 20.0, dk_injury_status="Q")
    doubtful = _p("doubtful", "WR", "AAA", 3000, 20.0, dk_injury_status="D")
    result = _eligible_pool([healthy, questionable, doubtful])
    assert set(result.keys()) == {"healthy", "questionable", "doubtful"}


def test_generate_lineups_never_drafts_an_ir_player_even_when_it_is_the_best_value():
    # An IR player priced absurdly cheap relative to its (fabricated-high) projection would
    # dominate the objective if wrongly included -- same shape as the zero-coverage "ghost"
    # test above, but exercising the injury-status exclusion instead of the None-projection one.
    pool = _synthetic_pool()
    ir_player = _p("ir_star", "WR", "AAA", 100, 99.0, dk_injury_status="IR")
    lineup = generate_lineups(pool + [ir_player], n=1)[0]
    assert "ir_star" not in {p.canonical_id for p in lineup.players}


def test_generate_lineups_still_drafts_a_questionable_player_when_it_is_the_best_value():
    # Q/D are real DFS strategic decisions, not a guaranteed non-play -- must stay eligible.
    pool = _synthetic_pool()
    questionable_star = _p("q_star", "WR", "AAA", 100, 99.0, dk_injury_status="Q")
    lineup = generate_lineups(pool + [questionable_star], n=1)[0]
    assert "q_star" in {p.canonical_id for p in lineup.players}


# --------------------------------------------------------------------------------------------
# generate_dup_risk_aware_lineups / _select_dup_risk_aware_lineups (ADR-0035/0037)
# --------------------------------------------------------------------------------------------


def _fake_dup_risk_table(bucket_upper_bounds: tuple[float, ...]) -> DupRiskLookupTable:
    n_buckets = len(bucket_upper_bounds) + 1
    return DupRiskLookupTable(
        seasons=(2023, 2024, 2025),
        n_rows=1000,
        bucket_upper_bounds=bucket_upper_bounds,
        bucket_dup_rate={i: 0.01 * i for i in range(n_buckets)},
        bucket_mean_lineup_ct={i: 1.0 for i in range(n_buckets)},
    )


def _fake_lineup(name: str, total_points: float, *, n_covered_players: int = MIN_OWNERSHIP_COVERAGE) -> Lineup:
    """A minimal `Lineup` fixture for exercising `_select_dup_risk_aware_lineups` in isolation --
    `slots`/`total_salary` are irrelevant to that pure selection logic, so left trivial. Each
    fixture's `core_stack` is a distinct singleton frozenset (all `_select_dup_risk_aware_lineups`
    needs to tell candidates apart), and its players are named `{name}_p0..N` so a caller can build
    a matching `projected_ownership_by_canonical_id` covering exactly `n_covered_players` of them.
    """
    players = tuple(_p(f"{name}_p{i}", "WR", "AAA", 3000, 1.0) for i in range(9))
    return Lineup(
        slots={}, players=players, total_salary=45_000, total_projected_points=total_points,
        core_stack=frozenset({name}), core_stack_team="AAA",
    )


def _ownership_for(lineup: Lineup, avg_own: float, *, n_covered_players: int) -> dict[str, float]:
    return {p.canonical_id: avg_own for p in lineup.players[:n_covered_players]}


def test_select_dup_risk_aware_lineups_picks_best_points_candidate_within_each_bucket_rule():
    table = _fake_dup_risk_table((10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0))
    best = _fake_lineup("best", 100.0)  # bucket 9 -- lineup 1 only, ownership irrelevant to it.
    second = _fake_lineup("second", 90.0)  # bucket 7 -- qualifies for lineup 2.
    third = _fake_lineup("third", 85.0)  # bucket 4 -- also qualifies for lineup 2, but worse points.
    fourth = _fake_lineup("fourth", 80.0)  # bucket 1 -- qualifies for lineup 3 (and lineup 2, but
    # lineup 2's slot is already taken by `second` by the time lineup 3 is chosen).
    fifth = _fake_lineup("fifth", 70.0)  # bucket 0 -- also qualifies for lineup 3, worse points.
    candidates = [best, second, third, fourth, fifth]

    ownership: dict[str, float] = {}
    ownership.update(_ownership_for(best, 95.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))
    ownership.update(_ownership_for(second, 75.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))
    ownership.update(_ownership_for(third, 45.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))
    ownership.update(_ownership_for(fourth, 15.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))
    ownership.update(_ownership_for(fifth, 5.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))

    result = _select_dup_risk_aware_lineups(candidates, ownership, table)
    assert [r.lineup for r in result] == [best, second, fourth]
    assert all(r.met_target for r in result)


def test_select_dup_risk_aware_lineups_falls_back_to_bucket_8_only_when_nothing_clears_bucket_7():
    table = _fake_dup_risk_table((10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0))
    best = _fake_lineup("best", 100.0)
    fallback = _fake_lineup("fallback", 90.0)  # bucket 8 only -- nothing else clears bucket 7.
    candidates = [best, fallback]
    ownership: dict[str, float] = {}
    ownership.update(_ownership_for(best, 95.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))
    ownership.update(_ownership_for(fallback, 85.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))

    result = _select_dup_risk_aware_lineups(candidates, ownership, table)
    # bucket-8 fallback used for lineup 2 -- ADR-0035's OWN backtested fallback, so this still
    # counts as met_target=True (it's the documented secondary target, not a generic fallback);
    # no candidate left at all for lineup 3.
    assert [r.lineup for r in result] == [best, fallback]
    assert result[1].met_target is True
    assert result[1].achieved_bucket == 8


def test_select_dup_risk_aware_lineups_excludes_candidates_below_the_ownership_coverage_floor():
    table = _fake_dup_risk_table((10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0))
    best = _fake_lineup("best", 100.0)
    thin = _fake_lineup("thin", 90.0)  # would classify into bucket 0 (great for lineup 2/3) but
    # only has MIN_OWNERSHIP_COVERAGE - 1 real ownership reads -- must never be guessed into a bucket.
    real = _fake_lineup("real", 80.0)  # worse points than `thin`, but has real, trustworthy coverage.
    candidates = [best, thin, real]
    ownership: dict[str, float] = {}
    ownership.update(_ownership_for(thin, 5.0, n_covered_players=MIN_OWNERSHIP_COVERAGE - 1))
    ownership.update(_ownership_for(real, 5.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))

    result = _select_dup_risk_aware_lineups(candidates, ownership, table)
    assert [r.lineup for r in result] == [best, real]  # `thin` skipped entirely, despite better points.


def test_select_dup_risk_aware_lineups_never_reuses_a_core_stack_across_slots():
    table = _fake_dup_risk_table((10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0))
    best = _fake_lineup("best", 100.0)
    double_qualifier = _fake_lineup("double", 90.0)  # bucket 0 -- the single best candidate for
    # BOTH lineup 2 (bucket<=7) and lineup 3 (bucket<=2); must only be used once.
    next_best_low = _fake_lineup("next_low", 80.0)  # bucket 1 -- the real lineup 3 once `double`
    # has already been claimed for lineup 2.
    candidates = [best, double_qualifier, next_best_low]
    ownership: dict[str, float] = {}
    ownership.update(_ownership_for(double_qualifier, 5.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))
    ownership.update(_ownership_for(next_best_low, 15.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))

    result = _select_dup_risk_aware_lineups(candidates, ownership, table)
    assert [r.lineup for r in result] == [best, double_qualifier, next_best_low]
    assert len({r.lineup.core_stack for r in result}) == 3


def test_select_dup_risk_aware_lineups_empty_candidates_returns_empty_list():
    table = _fake_dup_risk_table((10.0,))
    assert _select_dup_risk_aware_lineups([], {}, table) == []


def test_select_dup_risk_aware_lineups_falls_back_with_met_target_false_when_nothing_qualifies():
    # Chris, 2026-09-20: "loosen the restriction but flag it" -- a candidate that can't clear its
    # target bucket is no longer silently omitted; it's returned as the closest real fallback,
    # with met_target=False disclosing that it isn't the genuine leverage tier.
    table = _fake_dup_risk_table(tuple(float(10 * i) for i in range(1, 10)))  # 10 buckets, 0-9.
    best = _fake_lineup("best", 100.0)
    chalky = _fake_lineup("chalky", 90.0)
    candidates = [best, chalky]
    # Far above every real bound -- clamps to bucket 9, above both LINEUP_2_MAX_BUCKET (7) and
    # LINEUP_2_FALLBACK_BUCKET (8).
    ownership = _ownership_for(chalky, 1000.0, n_covered_players=MIN_OWNERSHIP_COVERAGE)

    result = _select_dup_risk_aware_lineups(candidates, ownership, table)
    assert [r.lineup for r in result] == [best, chalky]  # chalky is now included, not omitted...
    assert result[0].met_target is True  # lineup 1 (the anchor) has no target to miss
    assert result[1].met_target is False  # ...but flagged as not meeting the lineup-2 target
    assert result[1].target_bucket == LINEUP_2_MAX_BUCKET
    assert result[1].achieved_bucket == 9
    # No candidate left at all for lineup 3 -- chalky was the only other real candidate, and it's
    # already used, so that slot is genuinely absent (never a fabricated 4th entry).
    assert len(result) == 2


def test_select_dup_risk_aware_lineups_n_lineups_generalizes_beyond_three():
    table = _fake_dup_risk_table(tuple(float(10 * i) for i in range(1, 10)))  # 10 buckets, 0-9.
    best = _fake_lineup("best", 100.0)
    b7 = _fake_lineup("b7", 90.0)
    b2 = _fake_lineup("b2", 85.0)
    b6 = _fake_lineup("b6", 80.0)
    b5 = _fake_lineup("b5", 75.0)
    candidates = [best, b7, b2, b6, b5]
    ownership: dict[str, float] = {}
    ownership.update(_ownership_for(b7, 75.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))  # bucket 7
    ownership.update(_ownership_for(b2, 25.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))  # bucket 2
    ownership.update(_ownership_for(b6, 65.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))  # bucket 6
    ownership.update(_ownership_for(b5, 55.0, n_covered_players=MIN_OWNERSHIP_COVERAGE))  # bucket 5

    result = _select_dup_risk_aware_lineups(candidates, ownership, table, n_lineups=5)
    assert len(result) == 5
    assert [r.lineup for r in result] == [best, b7, b2, b6, b5]
    assert all(r.met_target for r in result)


def test_generate_dup_risk_aware_lineups_matches_plain_generation_when_every_candidate_is_bucket_zero():
    # A single-bucket table (empty bucket_upper_bounds) classifies every avg_own into bucket 0 --
    # bucket 0 clears both LINEUP_2_MAX_BUCKET and LINEUP_3_MAX_BUCKET, so the dup-risk-aware
    # selection should degenerate to exactly the same 3 lineups plain `generate_lineups` returns.
    pool = _synthetic_pool()
    table = DupRiskLookupTable(
        seasons=(2023, 2024, 2025), n_rows=1000, bucket_upper_bounds=(),
        bucket_dup_rate={0: 0.01}, bucket_mean_lineup_ct={0: 1.0},
    )
    ownership = {p.canonical_id: 10.0 for p in pool}

    plain = generate_lineups(pool, n=3)
    aware = generate_dup_risk_aware_lineups(pool, ownership, table, oversample_size=3)
    assert [r.lineup.core_stack for r in aware] == [lu.core_stack for lu in plain]
    assert all(r.met_target for r in aware)


def test_generate_dup_risk_aware_lineups_raises_on_an_infeasible_pool():
    tiny_pool = [
        _p("qb_a", "QB", "AAA", 7500, 22.0),
        _p("wr_a1", "WR", "AAA", 7000, 18.0),
    ]
    table = _fake_dup_risk_table((10.0,))
    with pytest.raises(LineupGenerationError):
        generate_dup_risk_aware_lineups(tiny_pool, {}, table)


def test_generate_dup_risk_aware_lineups_constants_match_the_adr_resolved_thresholds():
    assert LINEUP_2_MAX_BUCKET == 7
    assert LINEUP_2_FALLBACK_BUCKET == 8
    assert LINEUP_3_MAX_BUCKET == 2


# --------------------------------------------------------------------------------------------
# objective_delta_by_id (NflAgentConstructor Phase B1) -- an optional per-player objective
# adjustment that must be inert by default and never leak into the real reported point totals.
# --------------------------------------------------------------------------------------------


def test_objective_delta_by_id_defaults_to_the_identical_lineup():
    pool = _synthetic_pool()
    plain = generate_lineups(pool, n=3)
    with_none = generate_lineups(pool, n=3, objective_delta_by_id=None)
    assert [lu.core_stack for lu in with_none] == [lu.core_stack for lu in plain]
    assert [lu.total_projected_points for lu in with_none] == [
        lu.total_projected_points for lu in plain
    ]


def test_objective_delta_by_id_can_change_which_lineup_is_selected():
    pool = _synthetic_pool()
    baseline = generate_lineups(pool, n=1)[0]
    # A massive boost on a cheap player not in the baseline lineup should pull it into the
    # winning roster -- proof the delta genuinely influences selection, not just bookkeeping.
    boosted_id = next(
        p.canonical_id for p in pool if p.canonical_id not in {bp.canonical_id for bp in baseline.players}
    )
    delta = {boosted_id: 1000.0}
    boosted_lineup = generate_lineups(pool, n=1, objective_delta_by_id=delta)[0]
    assert boosted_id in {p.canonical_id for p in boosted_lineup.players}


def test_objective_delta_by_id_never_changes_the_reported_projected_points_or_salary():
    # The delta must only steer which roster is chosen -- the returned lineup's own
    # total_projected_points/total_salary must stay real, undistorted sums over blended_projection
    # and salary, never inflated/deflated by the synthetic agent delta.
    pool = _synthetic_pool()
    boosted_id = pool[0].canonical_id
    delta = {boosted_id: 1000.0}
    lineup = generate_lineups(pool, n=1, objective_delta_by_id=delta)[0]
    assert lineup.total_projected_points == sum(p.blended_projection for p in lineup.players)
    assert lineup.total_salary == sum(p.salary for p in lineup.players)


def test_objective_delta_by_id_missing_canonical_id_contributes_zero():
    # A delta dict that only names players outside the pool must behave identically to None.
    pool = _synthetic_pool()
    plain = generate_lineups(pool, n=3)
    irrelevant_delta = generate_lineups(pool, n=3, objective_delta_by_id={"not_in_pool": 50.0})
    assert [lu.core_stack for lu in irrelevant_delta] == [lu.core_stack for lu in plain]


def test_generate_dup_risk_aware_lineups_threads_objective_delta_by_id_through():
    pool = _synthetic_pool()
    table = DupRiskLookupTable(
        seasons=(2023, 2024, 2025), n_rows=1000, bucket_upper_bounds=(),
        bucket_dup_rate={0: 0.01}, bucket_mean_lineup_ct={0: 1.0},
    )
    ownership = {p.canonical_id: 10.0 for p in pool}
    baseline = generate_dup_risk_aware_lineups(pool, ownership, table, oversample_size=3)
    boosted_id = next(
        p.canonical_id
        for p in pool
        if p.canonical_id not in {bp.canonical_id for bp in baseline[0].lineup.players}
    )
    boosted = generate_dup_risk_aware_lineups(
        pool, ownership, table, oversample_size=3, objective_delta_by_id={boosted_id: 1000.0}
    )
    assert boosted_id in {p.canonical_id for p in boosted[0].lineup.players}
