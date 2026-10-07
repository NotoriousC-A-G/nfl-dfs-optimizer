from dataclasses import replace

import pytest

import nfl_dfs.optimizer.pool_solve as ps
from nfl_dfs.build.pool.contracts import BuildThesis, Pool, PoolEntry, PoolRules
from nfl_dfs.build.thesis.contracts import PairSign
from nfl_dfs.optimizer.pool_solve import PoolBuildFailure, build_agent_lineups, stack_bonus_pairs
from nfl_dfs.projection.blend import PlayerProjection

TEAMS = ("T1", "T2", "T3", "T4")
OPP = {"T1": "T2", "T2": "T1", "T3": "T4", "T4": "T3"}
GAME = {"T1": "g0", "T2": "g0", "T3": "g1", "T4": "g1"}
THESIS = BuildThesis(("g0:b0",), (), (), (), "test")


def _p(cid, name, pos, team, sal, proj, status=None):
    return PlayerProjection(cid, name, pos, team, sal, proj, 2, {"rotogrinders": proj, "footballguys": proj}, status)


def _projections(**status):
    out = []
    for i, t in enumerate(TEAMS):
        out.append(_p(f"qb_{t}", f"QB {t}", "QB", t, 6000 + 400 * i, 20.0 - i, status.get(f"qb_{t}")))
        for k in range(2):
            out.append(_p(f"rb{k}_{t}", f"RB{k} {t}", "RB", t, 5000 + 1200 * k, 11.0 + 2.5 * k - i * 0.1, status.get(f"rb{k}_{t}")))
        for k in range(3):
            out.append(_p(f"wr{k}_{t}", f"WR{k} {t}", "WR", t, 4500 + 1000 * k, 10.0 + 2.0 * k - i * 0.1, status.get(f"wr{k}_{t}")))
        out.append(_p(f"te_{t}", f"TE {t}", "TE", t, 3500 + 300 * i, 9.0 - i * 0.2, status.get(f"te_{t}")))
        out.append(_p(f"dst_{t}", f"DST {t}", "DST", t, 2500 + 100 * i, 7.0 - i * 0.2, status.get(f"dst_{t}")))
    return out


def _values(projs, override=None):
    v = {p.canonical_id: p.blended_projection for p in projs}
    v.update(override or {})
    return v


def _pool(projs, *, core=("qb_T1", "wr2_T1", "wr2_T2", "rb1_T2"), exclude=(), rules=PoolRules(min_core=2), agent="a1"):
    entries = []
    for p in projs:
        tier = "core" if p.canonical_id in core else "exclude" if p.canonical_id in exclude else "eligible"
        entries.append(PoolEntry(p.canonical_id, tier, "r" if tier != "eligible" else ""))
    return Pool(1, agent, THESIS, tuple(entries), rules)


def _build(pool, projs, values=None, **kw):
    kw.setdefault("n", 2)
    return build_agent_lineups(pool, projs, values or _values(projs), opponent_of=OPP, game_id_by_team=GAME, **kw)


def test_builds_legal_lineups_inside_the_pool_with_distinct_stacks_and_core_minimum():
    projs = _projections()
    res = _build(_pool(projs), projs, n=3)
    assert len(res.lineups) == 3
    stacks = {l.core_stack for l in res.lineups}
    assert len(stacks) == 3
    core = {"qb_T1", "wr2_T1", "wr2_T2", "rb1_T2"}
    for l in res.lineups:
        assert len(l.players) == 9 and l.total_salary <= 50000
        assert len({p.canonical_id for p in l.players} & core) >= 2


def test_excluded_tier_players_never_appear_even_if_they_are_the_best_value():
    projs = _projections()
    pool = _pool(projs, core=("rb1_T3", "rb1_T4", "te_T3", "te_T4"), exclude=("qb_T1", "wr2_T1", "wr2_T2"))
    res = _build(pool, projs, n=3)
    used = {p.canonical_id for l in res.lineups for p in l.players}
    assert not used & {"qb_T1", "wr2_T1", "wr2_T2"}


def test_tail_values_drive_selection_not_the_projection():
    projs = _projections()
    plain = _build(_pool(projs, core=()), projs, n=1, values=_values(projs), pair_signs=()) if False else None
    base = _build(_pool(projs, core=(), rules=PoolRules(min_core=0)), projs, n=1)
    assert "wr0_T4" not in {p.canonical_id for p in base.lineups[0].players}  # low projection -> not chosen
    boosted = _build(_pool(projs, core=(), rules=PoolRules(min_core=0)), projs, n=1, values=_values(projs, {"wr0_T4": 60.0}))
    assert "wr0_T4" in {p.canonical_id for p in boosted.lineups[0].players}
    assert boosted.lineups[0].total_projected_points < 1e9 and sum(p.blended_projection for p in boosted.lineups[0].players) == pytest.approx(boosted.lineups[0].total_projected_points)  # reported points stay the REAL projection sum


def test_hard_bring_back_sliders_are_honoured():
    projs = [replace(p, blended_projection=40.0) if p.canonical_id == "wr2_T2" else replace(p, blended_projection=5.0) if p.canonical_id == "qb_T2" else p for p in _projections()]
    values = _values(projs)
    loose = _build(_pool(projs, core=(), rules=PoolRules(min_core=0)), projs, n=1, values=values)
    assert "wr2_T2" in {p.canonical_id for p in loose.lineups[0].players}
    strict = _build(_pool(projs, core=(), rules=PoolRules(min_core=0, forbid_pass_catcher_bring_back=True)), projs, n=1, values=values)
    qb = next(p for p in strict.lineups[0].players if p.position == "QB")
    assert not [p for p in strict.lineups[0].players if p.team == OPP[qb.team] and p.position in ("WR", "TE")]


def test_widening_is_limited_logged_and_still_succeeds():
    projs = _projections()
    pool = _pool(projs, core=("qb_T1", "wr2_T1"), rules=PoolRules(min_core=4))  # only 2 core, rules ask 4
    res = _build(pool, projs, n=1)
    assert any("min_core" in s for s in res.pool.widened_steps)
    assert res.pool.rules.min_core < 4 and len(res.lineups) == 1


def test_depth_failure_widens_at_the_failing_position_only():
    projs = _projections()
    pool = _pool(projs, core=("qb_T1", "wr2_T1", "wr2_T2", "rb1_T2"),
                 exclude=tuple(p.canonical_id for p in projs if p.position == "WR" and p.team in ("T3", "T4")))
    res = _build(pool, projs, n=1)
    assert any("WR" in s for s in res.pool.widened_steps)
    added = {e.canonical_id for e in res.pool.entries if e.reason.startswith("widened:")}
    assert added and all(i.startswith("wr") for i in added)


def test_unwidenable_pool_fails_loudly_with_a_diagnostic_and_substitutes_nothing():
    projs = _projections(**{f"qb_{t}": "OUT" for t in TEAMS})  # every QB is out: nothing can fix this
    with pytest.raises(PoolBuildFailure) as exc:
        _build(_pool(projs, core=("wr2_T1", "wr2_T2", "rb1_T2", "rb1_T1")), projs)
    assert exc.value.stage == "infeasible_after_widening" and exc.value.agent_id == "a1"
    attempts = exc.value.diagnostics["attempts"]
    assert attempts and any(code == "feasibility_QB" for a in attempts for code, _ in a["errors"])
    assert len(attempts) >= 2  # widening was tried before giving up


def test_an_unavailable_core_player_is_an_immediate_expert_error_not_widened():
    projs = _projections(**{"qb_T1": "OUT"})
    with pytest.raises(PoolBuildFailure) as exc:
        _build(_pool(projs), projs)  # qb_T1 is in the default core
    assert exc.value.stage == "pool_validation" and "qb_T1" in str(exc.value) or "QB T1" in str(exc.value)
    assert len(exc.value.diagnostics["attempts"]) == 1


def test_missing_tail_values_fail_loudly_naming_the_players():
    projs = _projections()
    values = _values(projs)
    del values["wr1_T3"]
    with pytest.raises(PoolBuildFailure) as exc:
        _build(_pool(projs), projs, values=values)
    assert exc.value.stage == "values" and "wr1_T3" in exc.value.diagnostics["missing_values"]


def test_strong_negative_pair_fails_mild_warns():
    projs = _projections()
    pool = _pool(projs, core=(), rules=PoolRules(min_core=0))
    base = _build(pool, projs, n=1)
    ids = [p.canonical_id for p in base.lineups[0].players]
    strong = [PairSign(ids[0], ids[1], "negative", "strong", "contradiction")]
    with pytest.raises(PoolBuildFailure) as exc:
        _build(pool, projs, n=1, pair_signs=strong)
    assert exc.value.stage == "pair_sign_lint"
    mild = [PairSign(ids[0], ids[1], "negative", "mild", "soft")]
    res = _build(pool, projs, n=1, pair_signs=mild)
    assert [w.code for w in res.warnings] == ["negative_pair"]


def test_independent_verification_catches_a_bad_solver_output(monkeypatch):
    projs = _projections()
    good = _build(_pool(projs), projs, n=1).lineups[0]
    banned = next(p for p in good.players if p.position == "WR")
    bad_players = tuple(replace(p, dk_injury_status="OUT") if p is banned else p for p in good.players)
    real = ps.generate_lineups
    # sabotage only the real solve (n=1); the validator's dry-run solve (n=3) must still work
    monkeypatch.setattr(ps, "generate_lineups", lambda *a, n=None, **k: [replace(good, players=bad_players)] if n == 1 else real(*a, n=n, **k))
    with pytest.raises(PoolBuildFailure) as exc:
        _build(_pool(projs), projs, n=1)
    assert exc.value.stage == "post_solve_verification" and "excluded-status" in str(exc.value)


def test_stack_bonus_pairs_reward_the_stack_and_bring_back_only_when_allowed():
    projs = _projections()
    values = _values(projs)
    pairs = stack_bonus_pairs(projs, values, OPP, allow_bring_back=True)
    stack = [p for p in pairs if p[2] == ps.STACK_BONUS]
    back = [p for p in pairs if p[2] == ps.BRING_BACK_BONUS]
    assert stack and back and all(a.startswith("qb_") for a, _, _ in pairs)
    assert not [1 for a, b, bonus in stack if a.split("_")[1] != b.split("_")[-1]]  # same-team catchers only
    assert stack_bonus_pairs(projs, values, OPP, allow_bring_back=False) == stack


def test_candidate_generation_without_distinct_stacks_yields_distinct_lineups():
    projs = _projections()
    res = _build(_pool(projs, core=(), rules=PoolRules(min_core=0)), projs, n=4, distinct_core_stacks=False)
    sets = {frozenset(p.canonical_id for p in l.players) for l in res.lineups}
    assert len(sets) == 4


def test_cross_agent_diversity_every_lineup_differs_from_other_agents_by_at_least_three_players():
    projs = _projections()
    pool_a = _pool(projs, core=(), rules=PoolRules(min_core=0), agent="a1")
    a = _build(pool_a, projs, n=1)
    avoid = [frozenset(p.canonical_id for p in a.lineups[0].players)]
    b = _build(_pool(projs, core=(), rules=PoolRules(min_core=0), agent="a2"), projs, n=2, avoid_lineups=avoid, min_player_difference=3)
    for lu in b.lineups:
        ids = {p.canonical_id for p in lu.players}
        assert len(ids & set(avoid[0])) <= 6  # at most 6 shared => differs by >= 3
    first, second = ({p.canonical_id for p in lu.players} for lu in b.lineups)
    assert len(first & second) <= 6  # and the agent's own lineups also differ by >= 3


def test_default_difference_rule_leaves_prior_behaviour_unchanged():
    projs = _projections()
    pool = _pool(projs, core=(), rules=PoolRules(min_core=0))
    a = _build(pool, projs, n=2)
    b = _build(pool, projs, n=2, avoid_lineups=None, min_player_difference=1)
    assert [[p.canonical_id for p in l.players] for l in a.lineups] == [[p.canonical_id for p in l.players] for l in b.lineups]
