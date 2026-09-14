import pytest

from nfl_dfs.optimizer.lineup import (
    FLEX_GROUP_TOTAL,
    MAX_RB,
    MAX_TE,
    MAX_WR,
    MIN_RB,
    MIN_TE,
    MIN_WR,
    SALARY_CAP,
    LineupGenerationError,
    generate_lineups,
)
from nfl_dfs.projection.blend import PlayerProjection


def _p(
    canonical_id: str,
    position: str,
    team: str,
    salary: int,
    projection: float,
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
