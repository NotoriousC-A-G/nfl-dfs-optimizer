from nfl_dfs.tracking.postmortem.models import LineupOutcome, PostMortemReport
from nfl_dfs.tracking.postmortem.optimal import compute_optimal_lineup
from nfl_dfs.tracking.postmortem_renderer import _render_optimal


def _row(cid, pos, salary, team="AAA"):
    return {"identity": {"canonical_id": cid, "display_name": cid}, "team": team, "position": pos, "salary": salary, "projection": 5.0}


def _pool():
    pool = [_row("qb1", "QB", 5000), _row("qb2", "QB", 9000), _row("dst1", "DST", 2500), _row("dst2", "DST", 3000)]
    pool += [_row(f"rb{i}", "RB", 5000) for i in range(1, 5)]
    pool += [_row(f"wr{i}", "WR", 5000) for i in range(1, 6)]
    pool += [_row(f"te{i}", "TE", 4000) for i in range(1, 3)]
    return pool


ACTUAL = {
    "qb1": 20.0, "qb2": 30.0, "dst1": 10.0, "dst2": 10.0,
    "rb1": 25.0, "rb2": 20.0, "rb3": 15.0, "rb4": 5.0,
    "wr1": 22.0, "wr2": 18.0, "wr3": 16.0, "wr4": 12.0, "wr5": 1.0,
    "te1": 14.0, "te2": 3.0,
}


def test_optimal_respects_cap_and_roster_shape():
    opt = compute_optimal_lineup(_pool(), ACTUAL)
    players = opt.lineup.players
    assert len(players) == 9
    assert sum(p.salary for p in players) <= 50000
    positions = [p.position for p in players]
    assert positions.count("QB") == 1 and positions.count("DST") == 1
    assert 2 <= positions.count("RB") <= 3 and 3 <= positions.count("WR") <= 4 and 1 <= positions.count("TE") <= 2
    assert opt.optimal_total == round(sum(p.actual for p in players), 2)


def test_optimal_is_the_true_maximum_on_a_cap_binding_pool():
    # qb2 (30) costs 4000 more than qb1 (20); the cap forces a trade-off, and brute force confirms the optimum.
    import itertools

    pool, best = _pool(), 0.0
    by_pos = {}
    for r in pool:
        by_pos.setdefault(r["position"], []).append(r)
    for qb, dst in itertools.product(by_pos["QB"], by_pos["DST"]):
        flex_pool = by_pos["RB"] + by_pos["WR"] + by_pos["TE"]
        for combo in itertools.combinations(flex_pool, 7):
            n = lambda pos: sum(1 for c in combo if c["position"] == pos)
            if not (2 <= n("RB") <= 3 and 3 <= n("WR") <= 4 and 1 <= n("TE") <= 2):
                continue
            if qb["salary"] + dst["salary"] + sum(c["salary"] for c in combo) > 50000:
                continue
            best = max(best, ACTUAL[qb["identity"]["canonical_id"]] + ACTUAL[dst["identity"]["canonical_id"]] + sum(ACTUAL[c["identity"]["canonical_id"]] for c in combo))
    assert compute_optimal_lineup(pool, ACTUAL).optimal_total == round(best, 2)


def test_ties_break_toward_lower_salary_and_unscored_players_are_excluded():
    opt = compute_optimal_lineup(_pool(), {k: v for k, v in ACTUAL.items() if k != "qb2"})
    ids = {p.canonical_id for p in opt.lineup.players}
    assert "dst1" in ids and "dst2" not in ids  # equal 10.0 pts, dst1 is cheaper
    assert "qb2" not in ids
    assert opt.unresolved_count == 1


def test_fractions_of_optimal_for_our_lineups():
    ours = (
        LineupOutcome("operator", "L1", (), 0.0, 100.0, 0.0),
        LineupOutcome("operator", "L2", (), 0.0, None, None),  # unscored -> skipped
    )
    opt = compute_optimal_lineup(_pool(), ACTUAL, ours)
    assert [(label, actual) for label, actual, _ in opt.fractions] == [("L1", 100.0)]
    assert opt.fractions[0][2] == round(100.0 / opt.optimal_total, 4)


def test_renderer_shows_total_and_handles_missing():
    report = PostMortemReport(season=2026, week=5, lineup_outcomes=(), top_performers=(), missed_players=())
    assert _render_optimal(report) == ""
    report = PostMortemReport(season=2026, week=5, lineup_outcomes=(), top_performers=(), missed_players=(), optimal=compute_optimal_lineup(_pool(), ACTUAL))
    html = _render_optimal(report)
    assert "Optimal Lineup" in html and "perfect hindsight" in html
