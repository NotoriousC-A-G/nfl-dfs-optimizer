import json
from dataclasses import replace

import numpy as np
import pytest

from nfl_dfs.build.evidence.contracts import with_sha
from nfl_dfs.build.pool.contracts import BuildThesis, ExpertAgentOutput, GroupTier, PlayerRef, PoolEntry, PoolRules
from nfl_dfs.build.pool.derive import LIFT, derive_tiers, is_beneficiary
from nfl_dfs.build.pool.expand import expand_pool
from nfl_dfs.build.thesis.contracts import Branch, GameThesis, PlayerBranchOutcome
from nfl_dfs.build.value.calibration import CalibrationTable, CellStat
from nfl_dfs.optimizer.pool_solve import PoolBuildFailure
from nfl_dfs.optimizer.script_solve import build_agent_by_script
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


def test_a_script_lifts_hurts_and_leaves_neutral_in_its_own_game_and_other_games_are_fills():
    t = derive_tiers(_universe(), THESES, {}, "g0:b0")
    assert t["qb_T1"][0] == "core" and "lifts him" in t["qb_T1"][1]
    assert t["rb0_T1"][0] == "eligible"  # not named by the branch -> neutral
    assert t["qb_T3"][0] == "eligible" or t["qb_T3"][0] == "reach"  # other game: lifted in its own mix only if the mix lifts him
    assert all(t[p.canonical_id][0] in ("reach", "eligible") for p in _universe() if p.game_id == "g1")
    t2 = derive_tiers(_universe(), THESES, {}, "g0:b1")
    assert t2["rb1_T2"][0] == "reach" and "hurts him" in t2["rb1_T2"][1] and t2["qb_T1"][0] == "eligible"  # the same player flips with the script


def test_tiers_flip_with_the_script_core_in_one_reach_in_the_other():
    th = {"g0": _thesis("g0", ["rb1_T1"], ["rb1_T1"])}
    # the same RB is lifted in b0 and hurt in b1 -- build the second explicitly
    b0 = Branch("b0", (("q1", True),), False, 0.5, "d", player_outcomes=(PlayerBranchOutcome("rb1_T1", 1.3, 1.3),))
    b1 = Branch("b1", (("q1", False),), False, 0.4, "d", player_outcomes=(PlayerBranchOutcome("rb1_T1", 0.8, 0.8),))
    th = {"g0": replace(th["g0"], branches=(b0, b1, Branch("res", (), True, 0.10, "d")))}
    a, b = derive_tiers(_universe(), th, {}, "g0:b0")["rb1_T1"][0], derive_tiers(_universe(), th, {}, "g0:b1")["rb1_T1"][0]
    assert (a, b) == ("core", "reach")


def test_unavailable_players_are_excluded_with_the_reason_and_unknown_games_raise():
    u = [replace(p, status="OUT") if p.canonical_id == "qb_T1" else p for p in _universe()]
    t = derive_tiers(u, THESES, {}, "g0:b0")
    assert t["qb_T1"] == ("exclude", "unavailable (OUT)")
    with pytest.raises(ValueError, match="no thesis"):
        derive_tiers(_universe(), THESES, {}, "zz@yy:b0")


def test_a_beneficiary_is_core_in_a_script_that_does_not_hurt_him_and_eligible_elsewhere():
    from nfl_dfs.build.evidence.contracts import PlayerEvidence
    pe = lambda cid, gain: PlayerEvidence(cid, cid, "T1", "WR", 5000, 10.0, None, None, False, False, None, None, None, None, None, None, None, None,
                                          target_share_l4=0.10, target_share_expected=0.10 + gain)
    assert is_beneficiary(pe("x", 0.04)) and not is_beneficiary(pe("x", 0.01)) and not is_beneficiary(None)
    pk = {"g0": _Pk([pe("wr0_T1", 0.05), pe("wr0_T3", 0.05)])}
    t = derive_tiers(_universe(), THESES, pk, "g0:b0")
    assert t["wr0_T1"][0] == "core" and "vacated role" in t["wr0_T1"][1]  # in the scripted game, not hurt
    assert t["wr0_T3"][0] == "eligible"  # elsewhere: eligible, not core


def test_expand_uses_the_derived_map_for_unnamed_players_and_the_experts_adjustments_win():
    out = ExpertAgentOutput("a", BuildThesis(("g0:b0",), (), (), (), "r"), "derived",
                            (GroupTier("exclude", "refuse the chalk", "T2", "QB", None),), (PoolEntry("wr0_T1", "core", "battle call"),), PoolRules(min_core=2))
    d = derive_tiers(_universe(), THESES, {}, "g0:b0")
    pool = expand_pool(out, _universe(), d)
    tier = {e.canonical_id: e.tier for e in pool.entries}
    assert tier["qb_T1"] == "core"  # derived
    assert tier["qb_T2"] == "exclude"  # the expert's group adjustment
    assert tier["wr0_T1"] == "core" and next(e for e in pool.entries if e.canonical_id == "wr0_T1").reason == "battle call"
    assert {e.tier for e in expand_pool(out, _universe(), None).entries if e.canonical_id == "qb_T3"} == {"reach"}  # no script -> fallback is reach


def _projections():
    return [PlayerProjection(p.canonical_id, p.name, p.position, p.team, p.salary, p.projection, 2, {"x": p.projection}, None) for p in _universe()]


TABLE = CalibrationTable({pos: (CellStat(-np.inf, np.inf, 1000, 1.0, 1.8, 0.5),) for pos in ("QB", "RB", "WR", "TE", "DST")})


def _build(backs, n=2, **kw):
    out = ExpertAgentOutput("a1", BuildThesis(tuple(backs), (), (), (), "r"), "derived", (), (), PoolRules(min_core=2))
    return build_agent_by_script(
        out, universe=_universe(), theses=THESES, packets={}, projections=_projections(), table=TABLE, floor_lean=0.0, n=n,
        opponent_of=OPP, game_id_by_team=GAME, pair_signs=[], avoid_lineups=[], **kw,
    )


def test_one_lineup_per_script_in_order_each_built_under_its_own_scripts_tiers():
    sb = _build(["g0:b0", "g1:b0"])
    assert sb.scripts == ("g0:b0", "g1:b0") and len(sb.result.lineups) == 2
    # the lineup for g0:b0 is built from the g0 script's core, the other from g1's
    core0 = {e.canonical_id for e in sb.pools["g0:b0"].entries if e.tier == "core"}
    core1 = {e.canonical_id for e in sb.pools["g1:b0"].entries if e.tier == "core"}
    assert core0 and core1 and core0 != core1
    ids0 = {p.canonical_id for p in sb.result.lineups[0].players}
    ids1 = {p.canonical_id for p in sb.result.lineups[1].players}
    assert len(ids0 & core0) >= 2 and len(ids1 & core1) >= 2
    assert len(ids0 & ids1) <= 6  # kept apart by the 3-player minimum difference


def test_fewer_scripts_than_lineups_reuse_the_scripts_in_order():
    sb = _build(["g0:b0"], n=2)
    assert sb.scripts == ("g0:b0", "g0:b0")
    a, b = ({p.canonical_id for p in lu.players} for lu in sb.result.lineups)
    assert a != b


def test_no_script_or_an_unknown_game_fails_loudly_for_the_expert_loop():
    out = ExpertAgentOutput("a1", BuildThesis((), (), (), (), "r"), "derived", (), (), PoolRules(min_core=2))
    kw = dict(universe=_universe(), theses=THESES, packets={}, projections=_projections(), table=TABLE, floor_lean=0.0, n=2, opponent_of=OPP,
              game_id_by_team=GAME, pair_signs=[], avoid_lineups=[])
    with pytest.raises(PoolBuildFailure, match="no script"):
        build_agent_by_script(out, **kw)
    with pytest.raises(PoolBuildFailure, match="no thesis"):
        _build(["zz@yy:b0"])


def test_a_scripts_core_comes_from_the_scripted_game_the_experts_core_elsewhere_is_demoted_to_eligible():
    from nfl_dfs.optimizer.script_solve import script_pool
    out = ExpertAgentOutput("a1", BuildThesis(("g0:b0",), (), (), (), "r"), "derived", (), (PoolEntry("qb_T3", "core", "volume play in g1"),), PoolRules(min_core=2))
    pool = expand_pool(out, _universe(), derive_tiers(_universe(), THESES, {}, "g0:b0"))
    assert {e.canonical_id: e.tier for e in pool.entries}["qb_T3"] == "core"
    scripted = script_pool(pool, _universe(), "g0:b0")
    tiers = {e.canonical_id: e for e in scripted.entries}
    assert tiers["qb_T3"].tier == "eligible" and "demoted" in tiers["qb_T3"].reason
    assert tiers["qb_T1"].tier == "core"  # the scripted game's own core is untouched
    assert all(e.tier != "core" or next(p for p in _universe() if p.canonical_id == e.canonical_id).game_id == "g0" for e in scripted.entries)


def test_every_lineup_holds_at_least_min_core_players_from_its_scripted_game():
    sb = _build(["g0:b0", "g1:b0"])
    for lu, ref in zip(sb.result.lineups, sb.scripts):
        game = ref.split(":")[0]
        in_game = [p for p in lu.players if GAME.get(p.team) == game]
        assert len(in_game) >= 2  # min_core = 2, and core can only come from the scripted game


def test_a_defense_follows_what_the_script_does_to_the_opposing_quarterback():
    th = {"g0": _thesis("g0", ["qb_T2"], ["qb_T1"])}  # b0 lifts T2's QB, b1 hurts T1's QB
    up = derive_tiers(_universe(), th, {}, "g0:b0")  # T2's QB lifted -> T1's defense (facing him) is hurt; T2's defense (facing T1's QB) neutral
    assert up["dst_T1"][0] == "reach" and "lifts the opposing quarterback" in up["dst_T1"][1] and up["dst_T2"][0] == "eligible"
    down = derive_tiers(_universe(), th, {}, "g0:b1")  # T1's QB hurt (x0.8 <= 1/1.15) -> T2's defense is lifted
    assert down["dst_T2"][0] == "core" and "hurts the opposing quarterback" in down["dst_T2"][1]
    # a DST in another game stays a fill unless its own game's mix lifts it
    assert up["dst_T3"][0] == "reach"
