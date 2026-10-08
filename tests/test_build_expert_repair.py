import json
from dataclasses import replace

import pytest

from nfl_dfs.build.agents import POOL_AGENT_BY_ID
from nfl_dfs.build.expert.repair import (
    REPAIRABLE_STAGES, build_with_repair, describe_failure, output_to_json, repair_prompt, repair_spec,
)
from nfl_dfs.build.pool.contracts import BuildThesis, ExpertAgentOutput, GroupTier, PlayerRef, PoolEntry, PoolRules, Variation, Bet
from nfl_dfs.build.thesis.parse import parse_analyst_response
from nfl_dfs.optimizer.pool_solve import PoolBuildFailure
from tests._build_fixtures import _packet, _response, league_fn

AGENT = "shootout_stack"


def _world():
    packet = _packet()
    thesis, errs = parse_analyst_response(json.dumps(_response(packet)), packet, league_fn, prompt_version="p", model="m")
    assert not [e for e in errs if e.severity == "error"]
    universe = []  # a full-size slate for the pool-shape checks (the fixture packet itself only has a few players)
    for t in (packet.home, packet.away):
        universe.append(PlayerRef(f"{t}_qb", f"QB {t}", t, "QB", 6500, 19.0, None, packet.game_id))
        universe += [PlayerRef(f"{t}_rb{k}", f"RB{k} {t}", t, "RB", 5500 + 500 * k, 12.0, None, packet.game_id) for k in range(2)]
        universe += [PlayerRef(f"{t}_wr{k}", f"WR{k} {t}", t, "WR", 4500 + 800 * k, 11.0, None, packet.game_id) for k in range(4)]
        universe += [PlayerRef(f"{t}_te{k}", f"TE{k} {t}", t, "TE", 3500 + 600 * k, 8.0, None, packet.game_id) for k in range(2)]
        universe.append(PlayerRef(f"{t}_dst", f"DST {t}", t, "DST", 2800, 7.0, None, packet.game_id))
    return {packet.game_id: thesis}, {packet.game_id: packet}, universe


def _prior(universe) -> ExpertAgentOutput:
    first = universe[0]
    return ExpertAgentOutput(
        AGENT, BuildThesis((f"{universe[0].game_id}:b0",), (), (), (), "stack the shootout"), "eligible",
        (GroupTier("core", "branch story", first.team, "QB", None),), (PoolEntry(universe[1].canonical_id, "exclude", "field owns him"),), PoolRules(min_core=4),
        (Variation((f"{universe[0].game_id}:b0",), (f"{first.team}_qb", f"{first.team}_wr0"), "",
                   (Bet((f"{first.team}_qb", f"{first.team}_wr0"), "pass_volume", "the stack"),)),),
    )


def _failure(stage="underspend"):
    return PoolBuildFailure(AGENT, stage, "lineup 1 spends only $41,400 of $50,000 ($8,600 unspent)",
                            {"attempts": [], "lineup": ["A", "B"], "supply": {"QB": [("Q", 6000)], "RB": [], "WR": [], "TE": [], "DST": []}})


def _good_answer(universe, prior) -> str:
    qbs = [u for u in universe if u.position == "QB"]
    team = universe[0].team
    core = [u for u in universe if u.team == team and u.position in ("QB", "WR", "TE")][:5]
    a = output_to_json(prior)
    a["default_tier"] = "exclude"
    a["group_tiers"] = []
    a["overrides"] = [{"player_id": u.canonical_id, "tier": "core", "reason": "higher-priced volume in the backed game"} for u in core]
    a["min_core"] = 4
    return json.dumps({"agents": [a], "portfolio_notes": "repaired"})


def _run(tmp_path, build, prior=None):
    theses, packets, universe = _world()
    prior = prior or _prior(universe)
    return build_with_repair(prior, build, theses, packets, universe, model="m", freshness="f", season=2026, week=5, root=tmp_path), (theses, packets, universe, prior)


def test_a_first_time_success_never_touches_the_expert(tmp_path):
    oc, _ = _run(tmp_path, lambda o: "built-lineups")
    assert oc.status == "built" and oc.result == "built-lineups" and not oc.repaired
    assert not any(tmp_path.rglob("prompt.md"))


def test_an_underspend_failure_writes_one_repair_request_and_waits_for_the_expert(tmp_path):
    def build(o):
        raise _failure()
    oc, _ = _run(tmp_path, build)
    assert oc.status == "awaiting_repair" and "underspend" in oc.detail
    prompt = (oc.directory / "prompt.md").read_text()
    assert "$41,400" in prompt and "$8,600" in prompt and AGENT in prompt
    assert "YOUR PREVIOUS POOL" in prompt and "stack the shootout" in prompt  # the expert sees what it wrote
    assert "PLAYERS (the only ids you may name)" in prompt


def test_the_experts_repair_is_rebuilt_and_flagged_as_repaired(tmp_path):
    calls = []
    theses, packets, universe = _world()
    prior = _prior(universe)

    def build(o):
        calls.append(o)
        if o is prior:
            raise _failure()
        return "ok-lineups"
    oc, _ = _run(tmp_path, build, prior)
    (oc.directory / "response.json").write_text(_good_answer(universe, prior))
    oc2 = build_with_repair(prior, build, theses, packets, universe, model="m", freshness="f", season=2026, week=5, root=tmp_path)
    assert oc2.status == "built" and oc2.repaired and oc2.result == "ok-lineups"
    assert calls[-1] is not prior and calls[-1].default_tier == "exclude"  # the repaired pool was the one rebuilt
    assert oc2.first_failure.stage == "underspend"


def test_if_the_repair_fails_again_the_agent_is_unavailable_with_both_diagnoses_and_nothing_is_substituted(tmp_path):
    def build(o):
        raise _failure()
    oc, (theses, packets, universe, prior) = _run(tmp_path, build)
    (oc.directory / "response.json").write_text(_good_answer(universe, prior))
    oc2 = build_with_repair(prior, build, theses, packets, universe, model="m", freshness="f", season=2026, week=5, root=tmp_path)
    assert oc2.status == "unavailable" and oc2.result is None
    assert "failed again" in oc2.detail and "underspend" in oc2.detail


def test_an_expert_that_declares_the_agent_unavailable_is_respected(tmp_path):
    def build(o):
        raise _failure()
    oc, (theses, packets, universe, prior) = _run(tmp_path, build)
    (oc.directory / "response.json").write_text(json.dumps({"agents": [{"agent_id": AGENT, "unavailable": True, "reason": "no priced volume in the backed game"}]}))
    oc2 = build_with_repair(prior, build, theses, packets, universe, model="m", freshness="f", season=2026, week=5, root=tmp_path)
    assert oc2.status == "unavailable" and oc2.repaired and "no priced volume" in oc2.detail


def test_a_bad_repair_answer_gets_one_retry_then_the_agent_is_unavailable(tmp_path):
    def build(o):
        raise _failure()
    oc, (theses, packets, universe, prior) = _run(tmp_path, build)
    (oc.directory / "response.json").write_text("not json")
    oc2 = build_with_repair(prior, build, theses, packets, universe, model="m", freshness="f", season=2026, week=5, root=tmp_path)
    assert oc2.status == "awaiting_repair" and "retry_prompt.md" in oc2.detail
    (oc2.directory / "response.json").write_text("still not json")
    oc3 = build_with_repair(prior, build, theses, packets, universe, model="m", freshness="f", season=2026, week=5, root=tmp_path)
    assert oc3.status == "unavailable" and "rejected twice" in oc3.detail


def test_code_and_data_failures_are_not_the_experts_to_fix(tmp_path):
    for stage in ("values", "post_solve_verification"):
        assert stage not in REPAIRABLE_STAGES
        def build(o, stage=stage):
            raise PoolBuildFailure(AGENT, stage, "boom", {})
        oc, _ = _run(tmp_path, build)
        assert oc.status == "unavailable" and "not repairable" in oc.detail
    assert not any(tmp_path.rglob("prompt.md"))


def test_a_different_failure_is_a_different_request(tmp_path):
    theses, packets, universe = _world()
    prior = _prior(universe)
    a = repair_spec(prior, _failure(), theses, packets, universe, model="m", freshness="f")
    b = repair_spec(prior, PoolBuildFailure(AGENT, "underspend", "lineup 1 spends only $44,000", {}), theses, packets, universe, model="m", freshness="f")
    assert a.key != b.key and a.key == repair_spec(prior, _failure(), theses, packets, universe, model="m", freshness="f").key


def test_the_prompt_restates_the_agents_hard_rules_and_the_raw_output_point():
    theses, packets, universe = _world()
    vol = replace(_prior(universe), agent_id="volume_anchor")
    p = repair_prompt(POOL_AGENT_BY_ID["volume_anchor"], vol, _failure(), theses, packets)
    assert "NO WR/TE bring-backs" in p and "value per dollar" in p
    assert "stage: underspend" in describe_failure(_failure())
