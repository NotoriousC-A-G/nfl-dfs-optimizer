import json
from dataclasses import asdict

import pytest

from tests._build_fixtures import _packet, _response, league_fn
from nfl_dfs.build.runner import (
    STATUS_AWAITING, STATUS_CACHED, STATUS_OK, STATUS_REJECTED, STATUS_RETRY, StageFailure, collect_results, prepare_requests,
    require_all_ok,
)
from nfl_dfs.build.thesis.stage import analyst_specs

PACKET = _packet()


def _specs(freshness="f1", model="m1"):
    return analyst_specs({PACKET.game_id: PACKET}, league_fn, model=model, freshness=freshness)


def _kw(tmp_path):
    return dict(season=2026, week=4, root=tmp_path)


def _write_response(directory, payload):
    (directory / "response.json").write_text(payload if isinstance(payload, str) else json.dumps(payload))


def test_prepare_writes_a_self_contained_request_and_reports_it_pending(tmp_path):
    (d,) = prepare_requests(_specs(), **_kw(tmp_path))
    assert (d / "prompt.md").exists() and (d / "context.json").exists() and (d / "meta.json").exists()
    assert "EVIDENCE PACKET" in (d / "prompt.md").read_text()
    assert json.loads((d / "context.json").read_text())["packet"]["game_id"] == "LAR@PHI"
    assert json.loads((d / "meta.json").read_text())["packet_sha"] == PACKET.packet_sha


def test_without_an_answer_the_status_is_awaiting_never_a_silent_pass(tmp_path):
    prepare_requests(_specs(), **_kw(tmp_path))
    (r,) = collect_results(_specs(), **_kw(tmp_path))
    assert r.status == STATUS_AWAITING and r.result is None
    with pytest.raises(StageFailure, match="awaiting"):
        require_all_ok("analyst", [r])


def test_a_valid_answer_is_accepted_cached_and_not_re_requested(tmp_path):
    (d,) = prepare_requests(_specs(), **_kw(tmp_path))
    _write_response(d, _response(PACKET))
    (r,) = collect_results(_specs(), **_kw(tmp_path))
    assert r.status == STATUS_OK and r.result is not None and r.result.game_id == "LAR@PHI"
    require_all_ok("analyst", [r])
    assert prepare_requests(_specs(), **_kw(tmp_path)) == []  # cached -> nothing pending
    (again,) = collect_results(_specs(), **_kw(tmp_path))
    assert again.status == STATUS_CACHED and again.result == r.result


def test_a_bad_first_answer_gets_one_retry_with_the_exact_errors_then_is_rejected(tmp_path):
    (d,) = prepare_requests(_specs(), **_kw(tmp_path))
    bad = _response(PACKET, extra={"claims": [{"text": "x", "cite_keys": ["units.PHI.made_up"]}]})
    _write_response(d, bad)
    (r1,) = collect_results(_specs(), **_kw(tmp_path))
    assert r1.status == STATUS_RETRY and r1.errors[0].code == "cite_unresolved"
    retry = (d / "retry_prompt.md").read_text()
    assert "PREVIOUS ANSWER WAS REJECTED" in retry and "units.PHI.made_up" in retry
    assert not (d / "response.json").exists() and (d / "response.attempt1.json").exists()  # a fresh answer is required
    _write_response(d, bad)  # the retry fails too
    (r2,) = collect_results(_specs(), **_kw(tmp_path))
    assert r2.status == STATUS_REJECTED
    assert len(json.loads((d / "attempts.json").read_text())) == 2
    with pytest.raises(StageFailure, match="rejected"):
        require_all_ok("analyst", [r2])


def test_a_good_retry_after_a_bad_first_answer_is_accepted(tmp_path):
    (d,) = prepare_requests(_specs(), **_kw(tmp_path))
    _write_response(d, "this is not json")
    assert collect_results(_specs(), **_kw(tmp_path))[0].status == STATUS_RETRY
    _write_response(d, _response(PACKET))
    assert collect_results(_specs(), **_kw(tmp_path))[0].status == STATUS_OK


def test_changed_freshness_or_model_or_evidence_is_a_different_key_so_old_answers_never_leak(tmp_path):
    (d1,) = prepare_requests(_specs("f1"), **_kw(tmp_path))
    _write_response(d1, _response(PACKET))
    assert collect_results(_specs("f1"), **_kw(tmp_path))[0].status == STATUS_OK
    assert _specs("f1")[0].key != _specs("f2")[0].key != _specs("f1", model="m2")[0].key
    (r,) = collect_results(_specs("f2"), **_kw(tmp_path))  # the Q-override file changed -> fresh request needed
    assert r.status == STATUS_AWAITING


def test_a_cache_entry_that_no_longer_validates_is_discarded_not_trusted(tmp_path):
    (d,) = prepare_requests(_specs(), **_kw(tmp_path))
    _write_response(d, _response(PACKET))
    assert collect_results(_specs(), **_kw(tmp_path))[0].status == STATUS_OK
    (cache,) = list((tmp_path / "2026" / "4" / "analyst").glob("*.json"))
    corrupt = json.dumps({"response_text": "corrupted", "meta": {}, "key": "x"})  # e.g. validator tightened / file damaged
    # the source answer is still on disk and still valid -> the entry is healed from it, never read as-is
    cache.write_text(corrupt)
    (healed,) = collect_results(_specs(), **_kw(tmp_path))
    assert healed.status == STATUS_OK and "corrupted" not in cache.read_text()
    # with no valid source answer either, the entry is discarded and a new answer is required
    cache.write_text(corrupt)
    (d / "response.json").unlink()
    (r,) = collect_results(_specs(), **_kw(tmp_path))
    assert r.status == STATUS_AWAITING and not cache.exists()


def test_each_game_is_independent_one_failure_does_not_hide_another(tmp_path):
    import dataclasses
    other = dataclasses.replace(PACKET, game_id="KC@LV", packet_sha="other-sha")
    specs = analyst_specs({PACKET.game_id: PACKET, other.game_id: other}, league_fn, model="m1", freshness="f1")
    dirs = prepare_requests(specs, **_kw(tmp_path))
    ok_dir = next(d for d in dirs if "LAR" in d.name)
    _write_response(ok_dir, _response(PACKET))
    results = {r.item_id: r for r in collect_results(specs, **_kw(tmp_path))}
    assert results["LAR@PHI"].status == STATUS_OK and results["KC@LV"].status == STATUS_AWAITING
    with pytest.raises(StageFailure) as exc:
        require_all_ok("analyst", list(results.values()))
    assert "KC@LV" in str(exc.value) and "LAR@PHI" not in str(exc.value)
