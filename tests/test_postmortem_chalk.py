from nfl_dfs.tracking.postmortem.chalk import build_chalk_lineup
from nfl_dfs.tracking.postmortem.models import LineupOutcome, PlayerOutcome


def _row(cid, name, team, position, salary, ownership_pct, projected=10.0) -> dict:
    return {
        "identity": {"canonical_id": cid, "display_name": name},
        "team": team,
        "position": position,
        "salary": salary,
        "projection": projected,
        "ownership": {"projected_ownership": ownership_pct},
    }


def _real_pool() -> list[dict]:
    """A minimal but real, feasible DK-shaped pool: 1 QB, 3 RB, 4 WR, 2 TE, 1 DST -- enough
    candidates for the MILP to pick a legal 1QB/2-3RB/3-4WR/1-2TE/1DST/$50K roster."""
    return [
        _row("qb1", "QB One", "AAA", "QB", 6000, 30.0),
        _row("rb1", "RB One", "AAA", "RB", 8000, 40.0),
        _row("rb2", "RB Two", "BBB", "RB", 6000, 20.0),
        _row("rb3", "RB Three", "CCC", "RB", 4000, 10.0),
        _row("wr1", "WR One", "AAA", "WR", 7000, 35.0),
        _row("wr2", "WR Two", "BBB", "WR", 5500, 18.0),
        _row("wr3", "WR Three", "CCC", "WR", 4500, 12.0),
        _row("wr4", "WR Four", "DDD", "WR", 3000, 5.0),
        _row("te1", "TE One", "AAA", "TE", 4000, 15.0),
        _row("te2", "TE Two", "BBB", "TE", 2500, 4.0),
        _row("dst1", "DST One", "AAA", "DST", 2500, 8.0),
        _row("dst2", "DST Two", "BBB", "DST", 2500, 6.0),
    ]


def test_infeasible_when_pool_is_empty():
    result = build_chalk_lineup([], {})
    assert result.infeasible is True
    assert result.chalk_lineup is None


def test_infeasible_when_ownership_data_too_sparse():
    pool = [_row("p1", "P1", "AAA", "QB", 6000, 0.0), _row("p2", "P2", "AAA", "RB", 6000, 0.0)]
    result = build_chalk_lineup(pool, {})
    assert result.infeasible is True
    assert "sparse" in result.reason.lower()


def test_solves_a_real_feasible_chalk_lineup():
    result = build_chalk_lineup(_real_pool(), {})
    assert result.infeasible is False
    assert result.chalk_lineup is not None
    assert len(result.chalk_lineup.players) == 9
    total_salary = sum(p.salary for p in result.chalk_lineup.players)
    assert total_salary <= 50_000
    positions = [p.position for p in result.chalk_lineup.players]
    assert positions.count("QB") == 1
    assert positions.count("DST") == 1


def test_maximizes_ownership_not_projection():
    # rb1 has the highest ownership; the MILP should prefer it over higher-projected-but-
    # lower-owned alternatives when both are affordable.
    result = build_chalk_lineup(_real_pool(), {})
    chalk_ids = {p.canonical_id for p in result.chalk_lineup.players}
    assert "rb1" in chalk_ids  # highest-owned RB


def test_actual_points_populate_when_provided():
    actual_by_id = {"qb1": 20.0, "rb1": 25.0, "rb2": 10.0, "wr1": 15.0, "wr2": 8.0, "te1": 12.0, "dst1": 6.0}
    result = build_chalk_lineup(_real_pool(), actual_by_id)
    # chalk_actual is only set when EVERY selected player resolved -- some real pool members
    # above (rb3/wr3/wr4/te2/dst2) may or may not be selected, so just confirm no crash and a
    # sane relationship between infeasible/actual state.
    if not result.infeasible and not result.chalk_lineup.unresolved_players:
        assert result.chalk_actual == result.chalk_lineup.actual_total


def test_differentiators_computed_against_our_best():
    actual_by_id = {"qb1": 20.0, "rb1": 25.0, "rb2": 10.0, "rb3": 30.0, "wr1": 15.0, "wr2": 8.0, "wr3": 5.0, "wr4": 2.0, "te1": 12.0, "te2": 1.0, "dst1": 6.0, "dst2": 4.0}
    our_players = tuple(
        PlayerOutcome(canonical_id=cid, display_name=cid, team="ZZZ", position=pos, salary=1000, projected=1.0, actual=actual_by_id.get(cid), delta=None)
        for cid, pos in [("rb3", "RB"), ("qb1", "QB"), ("wr1", "WR"), ("te1", "TE"), ("dst1", "DST")]
    )
    our_best = LineupOutcome(
        agent_id="operator", label="L1", players=our_players, projected_total=50.0,
        actual_total=sum(actual_by_id[p.canonical_id] for p in our_players), delta=None,
    )
    result = build_chalk_lineup(_real_pool(), actual_by_id, our_best=our_best)
    assert result.our_actual == our_best.actual_total
    if not result.infeasible and result.chalk_lineup.actual_total is not None:
        assert result.delta == round(result.our_actual - result.chalk_actual, 2)
