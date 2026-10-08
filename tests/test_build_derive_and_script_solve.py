import json
from dataclasses import replace

import numpy as np
import pytest

from nfl_dfs.build.evidence.contracts import with_sha
from nfl_dfs.build.pool.contracts import BuildThesis, ExpertAgentOutput, GroupTier, PlayerRef, PoolEntry, PoolRules, Variation
from nfl_dfs.build.pool.derive import LIFT, derive_tiers, is_beneficiary
from nfl_dfs.build.pool.expand import expand_pool
from nfl_dfs.build.thesis.contracts import Branch, GameThesis, PlayerBranchOutcome
from nfl_dfs.build.value.calibration import CalibrationTable, CellStat
from nfl_dfs.optimizer.pool_solve import PoolBuildFailure
from nfl_dfs.optimizer.script_solve import SPEND_TILT, build_agent_by_variation, spend_tilt
from nfl_dfs.projection.blend import PlayerProjection

OPP = {"T1": "T2", "T2": "T1", "T3": "T4", "T4": "T3"}
GAME = {"T1": "g0", "T2": "g0", "T3": "g1", "T4": "g1"}


def _universe():
    out = []
    for i, t in enumerate(("T1", "T2", "T3", "T4")):
        g = GAME[t]
        out.append(PlayerRef(f"qb_{t}", f"QB {t}", t, "QB", 6000 + 400 * i, 20.0 - i, None, g))
        out += [PlayerRef(f"rb{k}_{t}", f"RB{k} {t}", t, "RB", 5600 + 1200 * k, 11.0 + 2.5 * k - 0.1 * i, None, g) for k in range(2)]
        out += [PlayerRef(f"wr{k}_{t}", f"WR{k} {t}", t, "WR", 5000 + 1000 * k, 10.0 + 2.0 * k - 0.1 * i, None, g) for k in range(3)]
        out.append(PlayerRef(f"te_{t}", f"TE {t}", t, "TE", 3500 + 300 * i, 9.0 - 0.2 * i, None, g))
        out.append(PlayerRef(f"dst_{t}", f"{t} D", t, "DST", 2500 + 100 * i, 7.0 - 0.2 * i, None, g))
    return out


def _thesis(gid, lifted, hurt):
    """b0: `lifted` players x1.3; b1: `hurt` players x0.8; plus a residual. Probabilities are fixed by hand."""
    b0 = Branch("b0", (("q1", True),), False, 0.5, "d", player_outcomes=tuple(PlayerBranchOutcome(p, 1.3, 1.3) for p in lifted))
    b1 = Branch("b1", (("q1", False),), False, 0.4, "d", player_outcomes=tuple(PlayerBranchOutcome(p, 0.8, 0.8) for p in hurt) + tuple(PlayerBranchOutcome(p, 1.3, 1.3) for p in hurt[:0]))
    res = Branch("res", (), True, 0.10, "d")
    return GameThesis(1, gid, "sha", "p", "m", "h", (), (), (), (b0, b1, res), "res", (), ())


THESES = {
    "g0": _thesis("g0", ["qb_T1", "wr2_T1", "wr1_T1", "te_T1", "rb1_T1"], ["rb1_T2", "wr2_T2"]),
    "g1": _thesis("g1", ["qb_T3", "wr2_T3", "rb1_T3", "te_T3"], ["wr2_T4"]),
}


class _Pk:  # the slice of an EvidencePacket derive_tiers reads
    def __init__(self, players):
        self.players = players


def test_stack_is_core_lifted_teammates_on_a_stack_team_are_core_lifted_pieces_elsewhere_eligible_hurt_players_reach():
    t = derive_tiers(_universe(), THESES, {}, ["g0:b0", "g1:b0"], ["qb_T1", "wr2_T1"])
    assert t["qb_T1"] == ("core", "on the agent's core stack") and t["wr2_T1"][0] == "core"
    assert t["wr1_T1"][0] == "core" and "stack team" in t["wr1_T1"][1]  # g0:b0 lifts him and he plays for a stack team
    assert t["qb_T3"][0] == "eligible" and "piece of a game" in t["qb_T3"][1]  # lifted in g1's view, not on the stack -> grab a piece
    t2 = derive_tiers(_universe(), THESES, {}, ["g0:b1", "g1:b0"], ["qb_T1", "wr2_T1"])
    assert t2["rb1_T2"][0] == "reach" and "avoid" in t2["rb1_T2"][1]  # g0:b1 hurts him


def test_a_game_has_its_own_view_games_do_not_influence_each_other():
    a = derive_tiers(_universe(), THESES, {}, ["g0:b0"], ["qb_T1", "wr2_T1"])
    b = derive_tiers(_universe(), THESES, {}, ["g0:b0", "g1:b1"], ["qb_T1", "wr2_T1"])
    assert all(a[p.canonical_id] == b[p.canonical_id] for p in _universe() if p.game_id == "g0")  # g1's view changes nothing in g0
    assert a["wr2_T4"][0] == "reach" and b["wr2_T4"][0] == "reach"  # g1:b1 hurts T4's WR2; with no g1 view he is a fill


def test_the_same_player_flips_tier_with_the_view_core_when_lifted_reach_when_hurt():
    b0 = Branch("b0", (("q1", True),), False, 0.5, "d", player_outcomes=(PlayerBranchOutcome("rb1_T1", 1.3, 1.3),))
    b1 = Branch("b1", (("q1", False),), False, 0.4, "d", player_outcomes=(PlayerBranchOutcome("rb1_T1", 0.8, 0.8),))
    th = {"g0": replace(THESES["g0"], branches=(b0, b1, Branch("res", (), True, 0.10, "d")))}
    lifted = derive_tiers(_universe(), th, {}, ["g0:b0"], ["qb_T1", "wr2_T1"])["rb1_T1"][0]
    hurt = derive_tiers(_universe(), th, {}, ["g0:b1"], ["qb_T1", "wr2_T1"])["rb1_T1"][0]
    assert (lifted, hurt) == ("core", "reach")


def test_unavailable_players_are_excluded_unknown_games_raise_and_a_game_with_no_view_is_a_fill_unless_lifted_in_its_mix():
    u = [replace(p, status="OUT") if p.canonical_id == "qb_T3" else p for p in _universe()]
    t = derive_tiers(u, THESES, {}, ["g0:b0"], ["qb_T1", "wr2_T1"])
    assert t["qb_T3"] == ("exclude", "unavailable (OUT)")
    assert t["wr0_T3"][0] == "reach" and "no view" in t["wr0_T3"][1]
    with pytest.raises(ValueError, match="no thesis"):
        derive_tiers(_universe(), THESES, {}, ["zz@yy:b0"], [])


def test_a_defense_is_never_a_haircut_fill_and_its_value_moves_opposite_to_the_quarterback_it_faces():
    from nfl_dfs.build.pool.derive import DST_MULT_RANGE, dst_multipliers, opposing_qb_by_dst
    from nfl_dfs.build.value.tail_value import conditional_multipliers
    th = {"g0": _thesis("g0", ["qb_T2"], ["qb_T1"])}
    for views in (["g0:b0"], ["g0:b1"], []):
        t = derive_tiers(_universe(), th, {}, views, ["qb_T1", "wr2_T1"])
        assert all(t[f"dst_{x}"][0] == "eligible" for x in ("T1", "T2", "T3", "T4"))  # eligible even with no view of the game: priced on merit
    assert opposing_qb_by_dst(_universe())["dst_T1"] == "qb_T2" and opposing_qb_by_dst(_universe())["dst_T3"] == "qb_T4"
    lifted_qb = conditional_multipliers(th.values(), ["g0:b0"])  # b0 lifts qb_T2 (x1.3)
    hurt_qb = conditional_multipliers(th.values(), ["g0:b1"])  # b1 hurts qb_T1 (x0.8)
    assert dst_multipliers(_universe(), lifted_qb)["dst_T1"][0] == pytest.approx(DST_MULT_RANGE[0])  # 1 - 0.5*0.3 = 0.85, floored at 0.9
    assert dst_multipliers(_universe(), hurt_qb)["dst_T2"][0] == pytest.approx(1.10)  # 1 + 0.5*0.2
    assert "dst_T3" not in dst_multipliers(_universe(), hurt_qb)  # a game the view does not touch: no change
    named = {"dst_T2": (1.3, 1.3), **hurt_qb}
    assert "dst_T2" not in dst_multipliers(_universe(), named)  # an analyst's own price for the defense wins


def test_a_beneficiary_is_eligible_even_in_a_game_without_a_view_and_core_on_a_stack_team():
    from nfl_dfs.build.evidence.contracts import PlayerEvidence
    pe = lambda cid, gain: PlayerEvidence(cid, cid, "T1", "WR", 5000, 10.0, None, None, False, False, None, None, None, None, None, None, None, None,
                                          target_share_l4=0.10, target_share_expected=0.10 + gain)
    assert is_beneficiary(pe("x", 0.04)) and not is_beneficiary(pe("x", 0.01)) and not is_beneficiary(None)
    pk = {"g0": _Pk([pe("wr0_T1", 0.05), pe("wr0_T3", 0.05)])}
    t = derive_tiers(_universe(), THESES, pk, ["g0:b0"], ["qb_T1", "wr2_T1"])
    assert t["wr0_T1"][0] == "core"  # stack team, view does not hurt him
    assert t["wr0_T3"][0] == "eligible" and "vacated role" in t["wr0_T3"][1]  # g1 has no view, still eligible


def test_expand_uses_the_derived_map_for_unnamed_players_and_the_experts_adjustments_win():
    out = ExpertAgentOutput("a", BuildThesis(("g0:b0",), (), (), (), "r"), "derived",
                            (GroupTier("exclude", "refuse the chalk", "T2", "QB", None),), (PoolEntry("wr0_T1", "core", "battle call"),), PoolRules(min_core=2))
    d = derive_tiers(_universe(), THESES, {}, ["g0:b0"], ["qb_T1", "wr2_T1"])
    tier = {e.canonical_id: e.tier for e in expand_pool(out, _universe(), d).entries}
    assert tier["qb_T1"] == "core" and tier["qb_T2"] == "exclude" and tier["wr0_T1"] == "core"
    assert {e.tier for e in expand_pool(out, _universe(), None).entries if e.canonical_id == "qb_T3"} == {"reach"}


def test_the_spend_plan_tilts_value_by_salary_pay_up_value_down_neutral_untouched():
    projs = _projections()
    base = {p.canonical_id: 10.0 for p in projs}
    out = spend_tilt(base, projs, (("WR", "pay"), ("RB", "value")))
    wr = next(p for p in projs if p.canonical_id == "wr2_T1")
    rb = next(p for p in projs if p.canonical_id == "rb1_T1")
    qb = next(p for p in projs if p.canonical_id == "qb_T1")
    assert out["wr2_T1"] == pytest.approx(10.0 + SPEND_TILT * wr.salary / 1000)
    assert out["rb1_T1"] == pytest.approx(10.0 - SPEND_TILT * rb.salary / 1000)
    assert out["qb_T1"] == 10.0 and spend_tilt(base, projs, ()) == base


def _projections():
    return [PlayerProjection(p.canonical_id, p.name, p.position, p.team, p.salary, p.projection, 2, {"x": p.projection}, None) for p in _universe()]


TABLE = CalibrationTable({pos: (CellStat(-np.inf, np.inf, 1000, 1.0, 1.8, 0.5),) for pos in ("QB", "RB", "WR", "TE", "DST")})


def _build(variations, n=2, spend=(), **kw):
    out = ExpertAgentOutput("a1", BuildThesis((), (), (), (), "r"), "derived", (), (), PoolRules(min_core=2), tuple(variations), tuple(spend))
    return build_agent_by_variation(
        out, universe=_universe(), theses=THESES, packets={}, projections=_projections(), table=TABLE, floor_lean=0.0, n=n,
        opponent_of=OPP, game_id_by_team=GAME, pair_signs=[], avoid_lineups=[], **kw,
    )


def test_one_lineup_per_variation_each_carrying_its_own_stack():
    a = Variation(("g0:b0", "g1:b0"), ("qb_T1", "wr2_T1"))
    b = Variation(("g0:b0", "g1:b0"), ("qb_T3", "wr2_T3"))
    vb = _build([a, b])
    assert len(vb.result.lineups) == 2 and vb.variations == (a, b)
    ids = [{p.canonical_id for p in lu.players} for lu in vb.result.lineups]
    assert {"qb_T1", "wr2_T1"} <= ids[0] and {"qb_T3", "wr2_T3"} <= ids[1]  # the core stack is in the lineup it belongs to
    assert len(ids[0] & ids[1]) <= 6


def test_a_lineup_spans_games_it_is_not_one_game():
    vb = _build([Variation(("g0:b0", "g1:b0"), ("qb_T1", "wr2_T1"))], n=1)
    teams = {p.team for p in vb.result.lineups[0].players}
    assert any(GAME[t] == "g0" for t in teams) and any(GAME[t] == "g1" for t in teams)


def test_fewer_variations_than_lineups_repeat_in_order_and_the_lineups_still_differ():
    vb = _build([Variation(("g0:b0",), ("qb_T1", "wr2_T1"))], n=2)
    a, b = ({p.canonical_id for p in lu.players} for lu in vb.result.lineups)
    assert vb.variations[0] == vb.variations[1] and a != b


def test_a_spend_plan_shifts_where_the_salary_goes():
    var = [Variation(("g0:b0",), ("qb_T1", "wr2_T1"))]
    base = _build(var, n=1).result.lineups[0]
    value_rb = _build(var, n=1, spend=(("RB", "value"), ("WR", "pay"))).result.lineups[0]
    rb_salary = lambda lu: sum(p.salary for p in lu.players if p.position == "RB")
    assert rb_salary(value_rb) <= rb_salary(base)


def test_no_variation_or_an_unknown_game_fails_loudly_for_the_expert_loop():
    out = ExpertAgentOutput("a1", BuildThesis((), (), (), (), "r"), "derived", (), (), PoolRules(min_core=2))
    kw = dict(universe=_universe(), theses=THESES, packets={}, projections=_projections(), table=TABLE, floor_lean=0.0, n=2, opponent_of=OPP,
              game_id_by_team=GAME, pair_signs=[], avoid_lineups=[])
    with pytest.raises(PoolBuildFailure, match="no variation"):
        build_agent_by_variation(out, **kw)
    with pytest.raises(PoolBuildFailure, match="no thesis"):
        _build([Variation(("zz@yy:b0",), ("qb_T1", "wr2_T1"))])


def test_a_declared_stack_player_who_cannot_play_fails_the_build_loudly():
    u = [replace(p, status="OUT") if p.canonical_id == "wr2_T1" else p for p in _universe()]
    projs = [replace(p, dk_injury_status="OUT") if p.canonical_id == "wr2_T1" else p for p in _projections()]
    out = ExpertAgentOutput("a1", BuildThesis((), (), (), (), "r"), "derived", (), (), PoolRules(min_core=1), (Variation(("g0:b0",), ("qb_T1", "wr2_T1")),))
    with pytest.raises(PoolBuildFailure, match="declared stack player"):
        build_agent_by_variation(out, universe=u, theses=THESES, packets={}, projections=projs, table=TABLE, floor_lean=0.0, n=1,
                                 opponent_of=OPP, game_id_by_team=GAME, pair_signs=[], avoid_lineups=[])


def test_a_variation_of_non_qb_bets_builds_a_lineup_without_a_qb_stack_requirement_and_holds_every_bet_player():
    from nfl_dfs.build.pool.contracts import Bet
    bets = (Bet(("rb1_T1", "dst_T1"), "lead_protect", "T1 ahead and running"), Bet(("wr2_T3", "te_T3"), "pass_volume", "")) 
    var = Variation(("g0:b0", "g1:b0"), ("rb1_T1", "dst_T1", "wr2_T3", "te_T3"), "", bets)
    vb = _build([var], n=1)
    ids = {p.canonical_id for p in vb.result.lineups[0].players}
    assert {"rb1_T1", "dst_T1", "wr2_T3", "te_T3"} <= ids  # every bet player is in
    teams = {p.team for p in vb.result.lineups[0].players}
    assert any(GAME[t] == "g0" for t in teams) and any(GAME[t] == "g1" for t in teams)  # bets in different games


def test_bets_that_cannot_share_one_roster_are_named_exactly():
    from nfl_dfs.build.pool.bets import roster_shape_problems
    u = {p.canonical_id: p for p in _universe()}
    two_qbs = [u["qb_T1"], u["wr2_T1"], u["qb_T3"], u["wr2_T3"]]
    assert any("2 QB declared" in x and "at most 1" in x for x in roster_shape_problems(two_qbs))
    two_dst = [u["dst_T1"], u["dst_T3"]]
    assert any("2 DST" in x for x in roster_shape_problems(two_dst))
    too_many = [u[f"{pos}{k}_{t}"] for pos in ("wr",) for k in range(3) for t in ("T1", "T2")]  # 6 WR
    assert any("6 WR" in x for x in roster_shape_problems(too_many))
    broke = [replace(u["qb_T1"], salary=20_000), replace(u["rb1_T1"], salary=20_000), replace(u["wr2_T1"], salary=9_000)]
    assert any("over the $50,000 cap" in x for x in roster_shape_problems(broke))
    assert roster_shape_problems([u["qb_T1"], u["wr2_T1"], u["rb1_T3"], u["dst_T3"]]) == []  # a QB stack plus an RB + defense bet fits


def test_the_build_fails_with_the_exact_shape_problem_not_a_vague_infeasibility():
    from nfl_dfs.build.pool.contracts import Bet
    bets = (Bet(("qb_T1", "wr2_T1"), "pass_volume"), Bet(("qb_T3", "wr2_T3"), "pass_volume"))
    var = Variation(("g0:b0", "g1:b0"), ("qb_T1", "wr2_T1", "qb_T3", "wr2_T3"), "", bets)
    with pytest.raises(PoolBuildFailure, match=r"2 QB declared but a lineup holds at most 1"):
        _build([var], n=1)
