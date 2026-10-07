import json

import numpy as np
import pytest

from nfl_dfs.build.evidence.anchors import question_anchor
from nfl_dfs.build.evidence.contracts import (
    AvailabilityItem, EvidencePacket, Lines, MetricValue, PlayerEvidence, TeamEvidence, packet_keys, with_sha,
)
from nfl_dfs.build.thesis.contracts import PivotalQuestion
from nfl_dfs.build.thesis.joint import expected_branch_probs
from nfl_dfs.build.thesis.parse import parse_analyst_response
from nfl_dfs.build.thesis.prompts import PROMPT_VERSION, analyst_prompt, anchor_menu, render_packet
from nfl_dfs.build.thesis.serialize import thesis_from_json, thesis_to_json

from tests._build_fixtures import LEAGUE, _packet, _q, _response, league_fn  # noqa: E402


def _parse(raw, packet=None):
    packet = packet or _packet()
    return parse_analyst_response(raw, packet, league_fn, prompt_version=PROMPT_VERSION, model="test-model")


def _errors(violations):
    return [v for v in violations if v.severity == "error"]


def test_a_well_formed_answer_parses_validates_and_derives_probabilities_in_code():
    packet = _packet()
    thesis, v = _parse(_response(packet), packet)
    assert _errors(v) == [], [x.message for x in v]
    assert thesis.packet_sha == packet.packet_sha and thesis.model == "test-model" and thesis.prompt_version == PROMPT_VERSION
    assert sum(b.prob for b in thesis.branches) == pytest.approx(1.0, abs=1e-4)
    marg = {m.question_id: m.adjusted for m in thesis.marginals}
    exp = expected_branch_probs(marg, ("q1", "q2"), 0.5, 0.10)
    got = {tuple(sorted(b.answers)): b.prob for b in thesis.branches if not b.is_residual}
    assert all(got[k] == pytest.approx(exp[k], abs=1e-5) for k in exp)
    assert all(m.anchor != 0.5 or m.question_id for m in thesis.marginals)  # anchors were computed by code


def test_a_probability_the_model_volunteers_is_ignored_when_code_can_derive_it():
    packet = _packet()
    resp = _response(packet)
    resp["branches"][0]["prob"] = 0.9
    thesis, v = _parse(resp, packet)
    assert _errors(v) == []
    assert thesis.branches[0].prob < 0.5


def test_invalid_json_and_schema_problems_are_parse_violations_not_crashes():
    t, v = _parse("not json at all")
    assert t is None and v[0].code == "parse"
    t, v = _parse(json.dumps({"headline": "x"}))  # missing everything else
    assert t is None and v[0].code == "parse" and "KeyError" in v[0].message
    t, v = _parse("[1, 2]")
    assert t is None and v[0].code == "parse"


def test_markdown_fences_around_the_json_are_tolerated():
    packet = _packet()
    thesis, v = _parse("```json\n" + json.dumps(_response(packet)) + "\n```", packet)
    assert thesis is not None and _errors(v) == []


def test_an_unanchorable_metric_is_reported_with_the_reason():
    packet = _packet()
    resp = _response(packet)
    resp["questions"][1] = _q("q2", "run_game", "rb_target_count", "units.LAR.rush_attempts_leading", 3.0, n=None)  # no league data for this metric
    _, v = _parse(resp, packet)
    assert "unanchorable" in {x.code for x in v}


def test_an_adjustment_beyond_the_cap_from_the_code_computed_anchor_is_rejected():
    packet = _packet()
    resp = _response(packet)
    resp["marginals"][0] = {"question_id": "q1", "adjusted": 0.80, "reason": "I feel strongly"}
    _, v = _parse(resp, packet)
    assert "marginal_shift" in {x.code for x in _errors(v)}


def test_a_claim_citing_a_key_outside_the_packet_is_unverified():
    packet = _packet()
    resp = _response(packet, extra={"claims": [{"text": "x", "cite_keys": ["units.PHI.made_up"]}]})
    _, v = _parse(resp, packet)
    assert "cite_unresolved" in {x.code for x in _errors(v)}


def test_thesis_round_trips_through_json():
    packet = _packet()
    thesis, _ = _parse(_response(packet), packet)
    assert thesis_from_json(thesis_to_json(thesis)) == thesis


def test_prompt_contains_rules_menu_packet_players_keys_and_retry_errors():
    packet = _packet()
    p = analyst_prompt(packet, league_fn)
    assert PROMPT_VERSION in p and "RULES" in p and "ANCHOR MENU" in p and "CITEABLE KEYS" in p
    for cid in ("hurts", "smith", "rams_rb", "kupp"):
        assert cid in p
    assert "units.PHI.sack_rate" in p and "grades availability-unaware" in p and "DO NOT write branch probabilities" in p
    retry = analyst_prompt(packet, league_fn, retry_errors=["cite key 'x' is not in the evidence packet"])
    assert "PREVIOUS ANSWER WAS REJECTED" in retry and "cite key 'x'" in retry
    assert "units.PHI.sack_rate" in p and set(packet_keys(packet)) and all(k in p for k in list(packet_keys(packet))[:5])


def test_anchor_menu_lists_thresholds_with_base_rates_and_lead_anchors_per_team():
    menu = anchor_menu(_packet(), league_fn)
    assert "| sack_rate |" in menu and "lead_at_q4" in menu and "units.PHI.lead_at_q4" in menu and "units.LAR.lead_at_q4" in menu
    assert "rb_target_count" not in menu  # metrics without league data are not offered
    assert "Jalen Hurts" in render_packet(_packet())


def test_collapse_metric_thresholds_in_the_menu_clear_the_validator_floor_after_rounding():
    from nfl_dfs.build.evidence.anchors import percentile_of_threshold
    menu = anchor_menu(_packet(), league_fn)
    rows = [r.split("|") for r in menu.splitlines() if r.startswith("| sack_rate |") or r.startswith("| qb_hit_rate |")]
    assert rows
    for r in rows:
        metric, thr = r[1].strip(), float(r[2])
        assert percentile_of_threshold(league_fn(metric), thr) >= 0.60  # every offered collapse threshold passes the rule it is checked against


def test_prompt_defines_metrics_and_exempts_per_game_metrics_from_the_sample_rule():
    p = analyst_prompt(_packet(), league_fn)
    assert "METRIC GLOSSARY" in p and "OWN offense takes" in p and "ONLY to metrics whose name contains" in p
    assert "write \"unknown\"" in p


def test_players_render_as_labelled_lines_with_explicit_na_never_empty_cells():
    lines = [l for l in render_packet(_packet()).splitlines() if l.startswith("- ") and "| salary" in l]
    assert len(lines) == 4
    for l in lines:
        for label in ("salary", "proj", "own%", "chalk", "leverage", "ceiling mult", "carry share", "target share", "status", "note"):
            assert f"{label} " in l
        assert "None" not in l and "|  |" not in l  # missing values say n/a
    assert any("ceiling mult n/a" in l and "carry share n/a" in l for l in lines)


def test_expert_player_universe_is_labelled_lines_too():
    from nfl_dfs.build.expert.prompts import render_universe
    lines = render_universe({"LAR@PHI": _packet()}).splitlines()
    assert len(lines) == 4 and all("game LAR@PHI" in l and "status " in l and "None" not in l for l in lines)
