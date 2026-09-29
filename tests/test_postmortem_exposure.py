from nfl_dfs.tracking.postmortem.exposure import (
    compute_player_exposure,
    compute_positional_deltas,
    compute_stack_thesis_reviews,
)
from nfl_dfs.tracking.postmortem.models import LineupOutcome, PlayerOutcome


def _player(cid, name="Player", team="AAA", position="WR", projected=10.0, actual=8.0) -> PlayerOutcome:
    delta = None if actual is None else actual - projected
    return PlayerOutcome(cid, name, team, position, 5000, projected, actual, delta)


def _lineup(agent_id, label, players, core_stack=(), core_stack_team=None) -> LineupOutcome:
    total_actual = None if any(p.actual is None for p in players) else sum(p.actual for p in players)
    total_proj = sum(p.projected for p in players)
    delta = None if total_actual is None else total_actual - total_proj
    return LineupOutcome(
        agent_id, label, tuple(players), total_proj, total_actual, delta,
        core_stack=core_stack, core_stack_team=core_stack_team,
    )


def test_compute_player_exposure_counts_every_real_appearance():
    walker = _player("walker", "Kenneth Walker III", "KC", "RB", projected=20.0, actual=25.0)
    lineups = (
        _lineup("chalk_anchor", "Chalk Anchor", [walker]),
        _lineup("operator", "L1", [_player("walker", "Kenneth Walker III", "KC", "RB", 20.0, 25.0)]),
        _lineup("operator", "L2", [_player("walker", "Kenneth Walker III", "KC", "RB", 20.0, 25.0)]),
    )
    exposure = compute_player_exposure(lineups)
    assert len(exposure) == 1
    row = exposure[0]
    assert row.count == 3
    assert row.lineup_labels == ("Chalk Anchor", "L1", "L2")
    assert row.actual == 25.0
    assert row.delta == 5.0


def test_compute_player_exposure_sorts_by_count_then_actual_descending():
    a = _lineup("chalk_anchor", "Chalk Anchor", [_player("p1", actual=30.0), _player("p2", actual=5.0)])
    b = _lineup("operator", "L1", [_player("p1", actual=30.0)])
    exposure = compute_player_exposure((a, b))
    assert exposure[0].canonical_id == "p1"  # 2 lineups beats 1
    assert exposure[0].count == 2


def test_compute_positional_deltas_dedupes_by_distinct_player():
    walker = _player("walker", position="RB", projected=20.0, actual=25.0)  # +5
    same_walker_again = _player("walker", position="RB", projected=20.0, actual=25.0)
    other_rb = _player("other", position="RB", projected=10.0, actual=8.0)  # -2
    exposure = compute_player_exposure(
        (_lineup("chalk_anchor", "Chalk Anchor", [walker]), _lineup("operator", "L1", [same_walker_again, other_rb]))
    )
    positional = compute_positional_deltas(exposure)
    rb = next(d for d in positional if d.position == "RB")
    assert rb.n == 2  # walker counted once despite 2 lineups
    assert rb.avg_delta == 1.5  # (5 + -2) / 2


def test_compute_positional_deltas_skips_unresolved_players():
    resolved = _player("p1", position="WR", projected=10.0, actual=8.0)
    unresolved = _player("p2", position="WR", projected=10.0, actual=None)
    exposure = compute_player_exposure((_lineup("chalk_anchor", "Chalk Anchor", [resolved, unresolved]),))
    positional = compute_positional_deltas(exposure)
    assert positional[0].n == 1


def test_compute_stack_thesis_reviews_only_covers_lineups_with_a_core_stack():
    qb = _player("qb1", "QB One", projected=20.0, actual=25.0)
    wr = _player("wr1", "WR One", projected=15.0, actual=10.0)
    other = _player("wr2", "WR Two", projected=8.0, actual=8.0)
    agent_lineup = _lineup("chalk_anchor", "Chalk Anchor", [qb, wr, other], core_stack=("qb1", "wr1"), core_stack_team="MIN")
    operator_lineup = _lineup("operator", "L1", [qb, wr, other])  # no core_stack -- not reviewed

    reviews = compute_stack_thesis_reviews((agent_lineup, operator_lineup))
    assert len(reviews) == 1
    r = reviews[0]
    assert r.label == "Chalk Anchor"
    assert r.stack_player_names == ("QB One", "WR One")
    assert r.stack_team == "MIN"
    assert r.projected == 35.0
    assert r.actual == 35.0
    assert r.hit is False  # actual == projected, not >


def test_compute_stack_thesis_reviews_hit_true_when_stack_beats_projection():
    qb = _player("qb1", projected=20.0, actual=30.0)
    reviews = compute_stack_thesis_reviews((_lineup("chalk_anchor", "Chalk Anchor", [qb], core_stack=("qb1",)),))
    assert reviews[0].hit is True
    assert reviews[0].delta == 10.0


def test_compute_stack_thesis_reviews_none_when_a_stack_player_is_unresolved():
    qb = _player("qb1", projected=20.0, actual=None)
    wr = _player("wr1", projected=15.0, actual=10.0)
    reviews = compute_stack_thesis_reviews(
        (_lineup("chalk_anchor", "Chalk Anchor", [qb, wr], core_stack=("qb1", "wr1")),)
    )
    assert reviews[0].actual is None
    assert reviews[0].hit is None
