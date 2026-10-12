from nfl_dfs.tracking.postmortem.redesign_outcomes import score_redesign_lineups
from nfl_dfs.tracking.postmortem_renderer import _render_redesign_outcomes

POOL = [
    {"identity": {"canonical_id": f"p{i}", "display_name": f"Player {i}"}, "team": "AAA", "position": "WR"} for i in range(1, 4)
]


def _artifact(**agent):
    base = {"status": "built", "build_thesis": {"reason": "because"}, "lineups": [], "warnings": [], "first_failure": None}
    base.update(agent)
    return {"agents": {"a1": base}}


def test_scores_lineup_and_delta():
    art = _artifact(lineups=[{"player_ids": ["p1", "p2", "p3"], "projected": 30.0, "salary": 49000, "views": ["G:b1"]}])
    (o,) = score_redesign_lineups(art, POOL, {"p1": 10.0, "p2": 20.0, "p3": 5.5})
    assert o.actual_total == 35.5
    assert o.delta == 5.5
    assert o.views == ("G:b1",)
    assert o.thesis == "because"


def test_unresolved_player_leaves_total_none_not_zero():
    art = _artifact(lineups=[{"player_ids": ["p1", "p2"], "projected": 20.0, "salary": 1, "views": []}])
    (o,) = score_redesign_lineups(art, POOL, {"p1": 10.0})
    assert o.actual_total is None
    assert o.unresolved == ("Player 2",)


def test_unbuilt_agent_is_reported_with_its_failure():
    art = _artifact(status="failed", first_failure="infeasible: no DST")
    (o,) = score_redesign_lineups(art, POOL, {})
    assert o.actual_total is None and o.players == ()
    assert "infeasible" in o.note


def test_renderer_sorts_best_first_and_handles_empty():
    assert _render_redesign_outcomes(()) == ""
    art = {
        "agents": {
            "low": {"status": "built", "lineups": [{"player_ids": ["p1"], "projected": 5.0, "salary": 1, "views": []}]},
            "high": {"status": "built", "lineups": [{"player_ids": ["p2"], "projected": 5.0, "salary": 1, "views": []}]},
        }
    }
    html = _render_redesign_outcomes(tuple(score_redesign_lineups(art, POOL, {"p1": 1.0, "p2": 9.0})))
    assert html.index("high") < html.index("low")
