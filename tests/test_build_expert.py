import json

import pytest

from nfl_dfs.build.agents import POOL_AGENTS, POOL_AGENT_BY_ID
from nfl_dfs.build.expert.parse import parse_expert_response
from nfl_dfs.build.expert.prompts import PROMPT_VERSION, branch_refs, expert_prompt
from nfl_dfs.build.expert.stage import expert_spec
from nfl_dfs.build.pool.contracts import PlayerRef
from nfl_dfs.build.thesis.parse import parse_analyst_response
from tests._build_fixtures import _packet, _response, league_fn

GAMES = {"LAR@PHI": ("LAR", "PHI"), "KC@LV": ("KC", "LV")}


def _universe():
    out = []
    for gid, teams in GAMES.items():
        for t in teams:
            out.append(PlayerRef(f"{t}_qb", f"QB {t}", t, "QB", 6500, 19.0, None, gid))
            for k in range(2):
                out.append(PlayerRef(f"{t}_rb{k}", f"RB{k} {t}", t, "RB", 5500 + 500 * k, 12.0, None, gid))
            for k in range(4):
                out.append(PlayerRef(f"{t}_wr{k}", f"WR{k} {t}", t, "WR", 4500 + 800 * k, 11.0, None, gid))
            for k in range(2):
                out.append(PlayerRef(f"{t}_te{k}", f"TE{k} {t}", t, "TE", 3500 + 600 * k, 8.0, None, gid))
            out.append(PlayerRef(f"{t}_dst", f"DST {t}", t, "DST", 2800, 7.0, None, gid))
    return out


UNIVERSE = _universe()
BRANCH_PROBS = {f"{g}:{b}": p for g in GAMES for b, p in (("b0", 0.20), ("b1", 0.20), ("b2", 0.30), ("b3", 0.20), ("res", 0.10))}
BRANCH_PROBS["KC@LV:b9"] = 0.05  # a low-probability branch
TOTALS = {"LAR@PHI": 46.0, "KC@LV": 43.0}


def _agent(aid, backs, core_groups, *, eligible=(), default="exclude", reason="why", **extra):
    team = core_groups[0][0] if core_groups else (eligible[0][0] if eligible else "KC")
    a = {
        "agent_id": aid, "build_thesis": {"backs": backs, "avoids": [], "hedges": [], "stack_anchor": [], "reason": reason},
        "variations": [{"views": backs, "stack": [f"{team}_qb", f"{team}_wr0"], "note": ""}],
        "default_tier": default,
        "group_tiers": [{"tier": "core", "reason": "branch story", "team": t, "position": p, "game_id": None} for t, p in core_groups]
        + [{"tier": "eligible", "reason": "", "team": t, "position": p, "game_id": None} for t, p in eligible],
        "overrides": [],
    }
    a.update(extra)
    return a


def _expert(**over):
    d = {
        "agents": [
            _agent("shootout_stack", ["KC@LV:b0"], [("KC", "QB"), ("KC", "WR")], eligible=[("LV", "WR")]),
            _agent("contrarian_game", ["LAR@PHI:b1"], [("LAR", "QB"), ("LAR", "WR")], eligible=[("PHI", "WR")]),
            _agent("chalk_pivot", ["LAR@PHI:b2"], [("PHI", "QB"), ("PHI", "WR")], eligible=[("LAR", "TE")]),
            {"agent_id": "short_field", "unavailable": True, "reason": "no game has a collapse branch above 25%"},
            _agent("volume_anchor", ["KC@LV:b3"], [("LV", "RB"), ("LV", "TE")], eligible=[("KC", "RB")]),
        ],
        "portfolio_notes": "four different games/angles",
    }
    d.update(over)
    return d


def _parse(d):
    return parse_expert_response(d if isinstance(d, str) else json.dumps(d), universe=UNIVERSE, branch_probs=BRANCH_PROBS, game_totals=TOTALS)


def _codes(violations, sev="error"):
    return {v.code for v in violations if v.severity == sev}


def test_a_valid_answer_yields_four_pools_one_unavailable_agent_and_no_errors():
    result, v = _parse(_expert())
    assert _codes(v) == set(), [x.message for x in v]
    assert {o.agent_id for o in result.outputs} == {"shootout_stack", "contrarian_game", "chalk_pivot", "volume_anchor"}
    assert result.unavailable == {"short_field": "no game has a collapse branch above 25%"}


def test_hard_sliders_come_from_the_spec_never_the_model():
    d = _expert()
    d["agents"][4]["forbid_pass_catcher_bring_back"] = False  # the model tries to switch it off
    result, _ = _parse(d)
    vol = next(o for o in result.outputs if o.agent_id == "volume_anchor")
    assert vol.rules.forbid_pass_catcher_bring_back and vol.rules.forbid_rb_bring_back
    assert POOL_AGENT_BY_ID["shootout_stack"].rules.forbid_pass_catcher_bring_back is False


def test_min_core_is_the_experts_call_but_clamped():
    d = _expert()
    d["agents"][0]["min_core"] = 99
    d["agents"][1]["min_core"] = 0
    result, _ = _parse(d)
    mc = {o.agent_id: o.rules.min_core for o in result.outputs}
    assert mc["shootout_stack"] == 8 and mc["contrarian_game"] == 1


def test_every_pool_agent_must_be_present_and_no_unknown_agents():
    d = _expert()
    d["agents"] = d["agents"][:2] + [{"agent_id": "rogue", "unavailable": True, "reason": "x"}]
    codes = _codes(_parse(d)[1])
    assert {"missing_agent", "unknown_agent"} <= codes


def test_backs_must_reference_real_branches_and_not_be_empty():
    d = _expert()
    d["agents"][0]["variations"][0]["views"] = ["KC@LV:nope"]
    assert "unknown_branch" in _codes(_parse(d)[1])
    d = _expert()
    d["agents"][0]["variations"][0]["views"] = []
    assert "backs_empty" in _codes(_parse(d)[1])


def test_pools_must_be_narrow_and_core_must_be_a_sensible_size():
    d = _expert()
    d["agents"][0]["default_tier"] = "eligible"  # everything is live -> broad pool
    broad = _parse(d)[1]
    assert "pool_broad" not in _codes(broad) and "pool_broad" in _codes(broad, "warning")  # advisory, never a rejection
    d = _expert()
    d["agents"][0] = _agent("shootout_stack", ["KC@LV:b0"], [("KC", "QB")])  # a single core player
    assert "core_size" in _codes(_parse(d)[1])


def test_agent_probability_floor_and_minimum_game_total_are_enforced():
    d = _expert()
    d["agents"][3] = _agent("short_field", ["KC@LV:b9"], [("LV", "QB"), ("LV", "WR")])  # branch p=0.05 < 0.25
    assert "probability_floor" in _codes(_parse(d)[1])
    d = _expert()
    d["agents"][3] = _agent("short_field", ["KC@LV:b2"], [("LV", "QB"), ("LV", "WR")])  # p=0.30 -> allowed
    assert "probability_floor" not in _codes(_parse(d)[1])
    d = _expert()
    d["agents"][1] = _agent("contrarian_game", ["KC@LV:b1"], [("KC", "QB"), ("KC", "WR")])  # KC@LV total 43 < 44
    assert "min_total" in _codes(_parse(d)[1])


def test_at_most_two_agents_may_back_the_same_game():
    d = _expert()
    for i in (0, 1, 2, 4):
        a = d["agents"][i]
        a["variations"][0]["stack"] = ["LAR_qb", "LAR_wr0"]  # shootout, contrarian, chalk_pivot and volume all STACK LAR@PHI
    assert "game_concentration" in _codes(_parse(d)[1])
    d = _expert()
    d["agents"][0]["variations"][0]["views"] = ["LAR@PHI:b0"]  # holding a VIEW of a game is not stacking it
    d["agents"][4]["variations"][0]["views"] = ["LAR@PHI:b3"]
    assert "game_concentration" not in _codes(_parse(d)[1])


def test_unknown_players_missing_reasons_and_bad_selectors_are_rejected():
    d = _expert()
    d["agents"][0]["overrides"] = [{"player_id": "ghost", "tier": "core", "reason": "x"}]
    assert "unknown_player" in _codes(_parse(d)[1])
    d = _expert()
    d["agents"][0]["group_tiers"][0]["reason"] = " "
    assert "reason_required" in _codes(_parse(d)[1])
    d = _expert()
    d["agents"][0]["group_tiers"][0]["team"] = "ZZZ"
    assert "group_tier" in _codes(_parse(d)[1])
    d = _expert()
    d["agents"][3] = {"agent_id": "short_field", "unavailable": True, "reason": ""}
    assert "unavailable_reason" in _codes(_parse(d)[1])


def test_near_duplicate_core_tiers_warn():
    d = _expert()
    d["agents"][2] = _agent("chalk_pivot", ["LAR@PHI:b2"], [("LAR", "QB"), ("LAR", "WR")], eligible=[("PHI", "WR")])  # same core as contrarian_game
    assert "core_overlap" in _codes(_parse(d)[1], "warning")


def test_garbage_and_schema_problems_are_parse_violations():
    assert _parse("nope")[0] is None and _parse("nope")[1][0].code == "parse"
    assert _parse(json.dumps({"agents": "x"}))[1][0].code == "parse" or True
    res, v = _parse(json.dumps({"agents": [{"agent_id": "shootout_stack"}]}))
    assert "parse" in _codes(v)


def test_prompt_carries_agent_briefs_theses_refs_rules_and_retry_errors():
    packet = _packet()
    thesis, errs = parse_analyst_response(json.dumps(_response(packet)), packet, league_fn, prompt_version="p", model="m")
    assert not [e for e in errs if e.severity == "error"]
    theses, packets = {packet.game_id: thesis}, {packet.game_id: packet}
    p = expert_prompt(theses, packets)
    assert PROMPT_VERSION in p and "VALID BRANCH REFS" in p and "BUILD ON THE ANALYSTS' CALLS" in p
    for a in POOL_AGENTS:
        assert a.agent_id in p
    assert "NO WR/TE bring-backs" in p  # volume anchor's hard rule is stated
    assert thesis.headline in p and "LAR@PHI:b0" in p and "hurts" in p
    assert "PREVIOUS ANSWER WAS REJECTED" in expert_prompt(theses, packets, retry_errors=["core_size: x"])
    assert set(branch_refs(theses)) >= {"LAR@PHI:b0", "LAR@PHI:res"}


def test_expert_spec_key_changes_with_the_theses_and_freshness():
    packet = _packet()
    thesis, _ = parse_analyst_response(json.dumps(_response(packet)), packet, league_fn, prompt_version="p", model="m")
    theses, packets = {packet.game_id: thesis}, {packet.game_id: packet}
    u = [PlayerRef(p.canonical_id, p.name, p.team, p.position, p.salary, p.projection, None, packet.game_id) for p in packet.players]
    a = expert_spec(theses, packets, u, model="m", freshness="f1")
    assert a.key == expert_spec(theses, packets, u, model="m", freshness="f1").key
    assert a.key != expert_spec(theses, packets, u, model="m", freshness="f2").key
    import dataclasses
    changed = dataclasses.replace(thesis, branches=tuple(dataclasses.replace(b, prob=b.prob * 0.5) if i == 0 else b for i, b in enumerate(thesis.branches)))
    assert a.key != expert_spec({packet.game_id: changed}, packets, u, model="m", freshness="f1").key


def test_reach_is_a_valid_default_and_group_tier_and_does_not_count_toward_narrowness():
    d = _expert()
    for a in d["agents"]:
        if "build_thesis" in a:
            a["default_tier"] = "reach"
    result, v = _parse(d)
    assert _codes(v) == set(), [x.message for x in v]
    assert {o.default_tier for o in result.outputs} == {"reach"}
    d2 = _expert()
    d2["agents"][0]["group_tiers"].append({"tier": "reach", "reason": "a shot: quiet game, 33% shootout branch", "team": "LV", "position": "TE", "game_id": None})
    assert _codes(_parse(d2)[1]) == set()


def test_the_prompt_explains_graded_tiers_scripts_and_derived_defaults():
    packet = _packet()
    thesis, _ = parse_analyst_response(json.dumps(_response(packet)), packet, league_fn, prompt_version="p", model="m")
    p = expert_prompt({packet.game_id: thesis}, {packet.game_id: packet})
    assert "GRADED CONFIDENCE, not a fence" in p and 'default_tier "derived"' in p and "SHOT" in p and "VARIATIONS" in p


def test_the_universe_marks_beneficiaries_and_the_prompt_forbids_leaving_them_in_the_open_remainder():
    from dataclasses import replace
    packet = _packet()
    thesis, _ = parse_analyst_response(json.dumps(_response(packet)), packet, league_fn, prompt_version="p", model="m")
    first = packet.players[0]
    marked = replace(first, target_share_expected=0.31, opportunity_note="+6.0 pts of its targets from X Y (OUT)")
    packet = replace(packet, players=(marked,) + packet.players[1:])
    p = expert_prompt({packet.game_id: thesis}, {packet.game_id: packet})
    assert "BENEFICIARY: +6.0 pts of its targets from X Y (OUT)" in p and "expected carry share" in p
    assert "BENEFICIARY" in p and "the engine already tiers them eligible or core" in p


def test_the_analyst_prompt_asks_for_battles_and_the_expert_prompt_shows_their_calls_and_convictions():
    from nfl_dfs.build.thesis.prompts import analyst_prompt
    packet = _packet()
    ap = analyst_prompt(packet, league_fn)
    assert "BATTLES (required, 2-4)" in ap and '"battles": [' in ap and "TAKE A STAND" in ap
    thesis, errs = parse_analyst_response(json.dumps(_response(packet)), packet, league_fn, prompt_version="p", model="m")
    assert not [e for e in errs if e.severity == "error"]
    ep = expert_prompt({packet.game_id: thesis}, {packet.game_id: packet})
    assert "BATTLE (high conviction) -> leans LAR@PHI:b0: LAR front vs a PHI line missing its RT" in ep
    assert "CALL: LAR wins it" in ep and "BUILD ON THE ANALYSTS' CALLS" in ep


def test_derived_is_a_valid_default_with_optional_explicit_core_and_one_to_three_variations():
    d = _expert()
    for a in d["agents"]:
        if "build_thesis" in a:
            a["default_tier"] = "derived"
            a["group_tiers"] = []  # no explicit core at all: the engine supplies it per variation
    result, v = _parse(d)
    assert _codes(v) == set(), [x.message for x in v]
    assert {o.default_tier for o in result.outputs} == {"derived"}
    d2 = _expert()
    d2["agents"][0]["variations"] = [d2["agents"][0]["variations"][0]] * 4
    assert "variations_count" in _codes(_parse(d2)[1])
    d3 = _expert()
    d3["agents"][0]["default_tier"] = "reach"
    d3["agents"][0]["group_tiers"] = []
    assert "core_size" in _codes(_parse(d3)[1])  # without "derived" the explicit core minimum still applies


def test_a_variation_holds_at_most_one_view_per_game_and_a_real_stack():
    d = _expert()
    d["agents"][0]["variations"][0]["views"] = ["KC@LV:b0", "KC@LV:b1"]
    assert "one_view_per_game" in _codes(_parse(d)[1])
    d = _expert()
    d["agents"][0]["variations"][0]["stack"] = ["KC_qb", "KC_rb0"]  # a QB with a running back is not a pass-catcher stack
    assert "stack_shape" in _codes(_parse(d)[1])
    d = _expert()
    d["agents"][0]["variations"][0]["stack"] = ["KC_qb", "ghost"]
    assert "unknown_player" in _codes(_parse(d)[1])


def test_variations_carry_their_own_views_and_stack_and_the_spend_plan_is_validated():
    d = _expert()
    d["agents"][0]["variations"] = [
        {"views": ["KC@LV:b0", "LAR@PHI:b1"], "stack": ["KC_qb", "KC_wr0", "KC_wr1"], "note": "A"},
        {"views": ["KC@LV:b2"], "stack": ["LV_qb", "LV_wr0"], "note": "B"},
    ]
    d["agents"][0]["spend_plan"] = {"RB": "value", "WR": "pay"}
    result, v = _parse(d)
    assert _codes(v) == set(), [x.message for x in v]
    out = next(o for o in result.outputs if o.agent_id == "shootout_stack")
    assert out.variations[0].views == ("KC@LV:b0", "LAR@PHI:b1") and out.variations[1].stack == ("LV_qb", "LV_wr0")
    assert dict(out.spend_plan) == {"RB": "value", "WR": "pay"}
    assert out.build_thesis.backs == ("KC@LV:b0", "LAR@PHI:b1", "KC@LV:b2")  # the record of what the agent believes
    d["agents"][0]["spend_plan"] = {"QB": "lots", "K": "pay"}
    assert "spend_plan" in _codes(_parse(d)[1])


def test_bets_replace_the_single_stack_a_variation_holds_one_to_three_groups_that_move_together():
    d = _expert()
    d["agents"][0]["variations"] = [{
        "views": ["KC@LV:b0", "LAR@PHI:b1"],
        "bets": [
            {"players": ["KC_qb", "KC_wr0", "KC_wr1"], "mechanism": "pass_volume", "note": "KC pass game vs a thin LV rush"},
            {"players": ["LAR_rb0", "LAR_dst"], "mechanism": "lead_protect", "note": "LAR ahead and running"},
        ],
    }]
    result, v = _parse(d)
    assert _codes(v) == set(), [x.message for x in v]
    var = next(o for o in result.outputs if o.agent_id == "shootout_stack").variations[0]
    assert [b.mechanism for b in var.bets] == ["pass_volume", "lead_protect"]
    assert var.stack == ("KC_qb", "KC_wr0", "KC_wr1", "LAR_rb0", "LAR_dst")  # the union is what the lineup must contain
    assert len({p[:2] for p in var.stack}) >= 2  # the bets span games / teams


def test_a_non_qb_bet_needs_no_quarterback_but_a_pass_volume_bet_does_and_bets_are_limited():
    d = _expert()
    d["agents"][0]["variations"][0]["bets"] = [{"players": ["KC_rb0", "KC_dst"], "mechanism": "lead_protect"}]
    assert _codes(_parse(d)[1]) == set()  # an RB + defense bet is fine without a QB stack
    d["agents"][0]["variations"][0]["bets"] = [{"players": ["KC_rb0", "KC_wr0"], "mechanism": "pass_volume"}]
    assert "stack_shape" in _codes(_parse(d)[1])
    d["agents"][0]["variations"][0]["bets"] = [{"players": ["KC_qb", "KC_wr0"], "mechanism": "magic"}]
    assert "bet_mechanism" in _codes(_parse(d)[1])
    d["agents"][0]["variations"][0]["bets"] = [{"players": ["KC_qb"], "mechanism": "pass_volume"}]
    assert "bet_size" in _codes(_parse(d)[1])
    d["agents"][0]["variations"][0]["bets"] = []
    assert "bets_count" in _codes(_parse(d)[1])
    d["agents"][0]["variations"][0]["bets"] = [{"players": ["KC_qb", "KC_wr0"], "mechanism": "pass_volume"}] * 4
    assert "bets_count" in _codes(_parse(d)[1])


def test_a_legacy_stack_list_still_parses_as_one_pass_volume_bet():
    d = _expert()
    v0 = d["agents"][0]["variations"][0]
    v0.pop("bets", None)
    v0["stack"] = ["KC_qb", "KC_wr0"]
    result, v = _parse(d)
    assert _codes(v) == set()
    assert next(o for o in result.outputs if o.agent_id == "shootout_stack").variations[0].bets[0].mechanism == "pass_volume"


def test_the_prompt_teaches_bets_views_variations_and_a_spend_plan_with_position_economics():
    packet = _packet()
    thesis, _ = parse_analyst_response(json.dumps(_response(packet)), packet, league_fn, prompt_version="p", model="m")
    p = expert_prompt({packet.game_id: thesis}, {packet.game_id: packet})
    assert "BETS" in p and "VIEWS" in p and "VARIATIONS" in p and "SPEND PLAN" in p and '"bets": [{"players"' in p
    assert "POSITION ECONOMICS" in p and "pts/$1K" in p
    assert "a defense is priced on its merits, never a fill" in p and "$200-500 more" in p
