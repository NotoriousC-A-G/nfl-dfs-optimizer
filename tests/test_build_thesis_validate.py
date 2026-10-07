from dataclasses import replace

import pytest

from nfl_dfs.build.thesis.contracts import (
    Branch, Claim, Dependency, GameThesis, PairSign, PivotalQuestion, PlayerBranchOutcome, QuestionMarginal,
)
from nfl_dfs.build.thesis.joint import expected_branch_probs, joint_cells, logit
from nfl_dfs.build.thesis.validate import validate_game_thesis

PACKET_KEYS = {"units.PHI.sack_rate", "units.LAR.rush_attempts_leading", "lines.spread", "availability.PHI.rt"}
SLATE = {"hurts", "rams_rb", "smith", "kupp"}


def _q1():
    return PivotalQuestion("q1", "Does PHI protect Hurts without its RT?", "pass_protection", "sack_rate",
                           "units.PHI.sack_rate", 0.10, "gte", 35, "The line does not price the RT absence by itself")


def _q2():
    return PivotalQuestion("q2", "Can LAR run while leading?", "run_game", "rush_attempts_leading",
                           "units.LAR.rush_attempts_leading", 12.0, "gte", None, "Run volume when ahead is not in the total")


def _branches(p1=0.25, p2=0.45, lam=0.5, residual=0.10, outcomes=None):
    exp = expected_branch_probs({"q1": p1, "q2": p2}, ("q1", "q2"), lam, residual)
    outs = outcomes if outcomes is not None else (PlayerBranchOutcome("hurts", 0.9, 0.9, "pressure cuts his dropbacks"),)
    bs = []
    for i, (key, prob) in enumerate(sorted(exp.items())):
        bs.append(Branch(f"b{i}", key, False, round(prob, 4), f"branch {i}", ("link one", "link two"), outs))
    bs.append(Branch("res", (), True, residual, "neither resolves as framed"))
    return tuple(bs)


def _thesis(**over) -> GameThesis:
    base = GameThesis(
        schema_version=1, game_id="LAR@PHI", packet_sha="abc", prompt_version="p1", model="m",
        headline="PHI loses its RT -> LAR pressure -> PHI pass game stalls",
        questions=(_q1(), _q2()),
        marginals=(QuestionMarginal("q1", 0.25, 0.25), QuestionMarginal("q2", 0.45, 0.45)),
        dependencies=(Dependency("q1", "q2", "pressure shortens drives so LAR leads more often", 0.5),),
        branches=_branches(),
        counter_branch_id="res",
        claims=(Claim("PHI RT is out", ("availability.PHI.rt",), "availability", "Out", "2026-10-09T18:00Z"),
                Claim("PHI allowed pressure on 12% of dropbacks", ("units.PHI.sack_rate",))),
        would_change_mind=("PHI RT inactive status Sunday morning",),
        pair_signs=(PairSign("hurts", "smith", "positive", "strong", "same offense"),),
    )
    return replace(base, **over)


def _codes(thesis, **kw):
    kwargs = dict(packet_keys=PACKET_KEYS, slate_player_ids=SLATE)
    kwargs.update(kw)
    return [x.code for x in validate_game_thesis(thesis, **kwargs) if x.severity == "error"]


def test_joint_cells_preserve_marginals_and_log_odds_ratio():
    import math
    for lam in (-0.5, 0.0, 0.5):
        c = joint_cells(0.25, 0.4, lam)
        assert c[(True, True)] + c[(True, False)] == pytest.approx(0.25)
        assert c[(True, True)] + c[(False, True)] == pytest.approx(0.4)
        assert math.log((c[(True, True)] * c[(False, False)]) / (c[(True, False)] * c[(False, True)])) == pytest.approx(lam, abs=1e-9)
        assert sum(c.values()) == pytest.approx(1.0)


def test_a_fully_valid_thesis_has_no_errors():
    assert _codes(_thesis()) == []


def test_question_count_caps_and_child_rule():
    q3 = replace(_q2(), question_id="q3", phase="pace", metric="pace_seconds_per_play", source_field="lines.spread")
    assert "too_many_questions" in _codes(_thesis(questions=(_q1(), _q2(), q3)))
    child = replace(_q2(), question_id="q3", phase="pace", metric="pace_seconds_per_play", source_field="lines.spread", parent_id="q1", parent_answer=True)
    codes = _codes(_thesis(questions=(_q1(), _q2(), child)))
    assert "too_many_questions" not in codes  # a 3rd question is allowed only as a child
    orphan = replace(child, parent_id="nope")
    assert "child_question" in _codes(_thesis(questions=(_q1(), _q2(), orphan)))


def test_two_independent_questions_must_concern_different_phases():
    same = replace(_q2(), phase="pass_protection")
    assert "same_phase" in _codes(_thesis(questions=(_q1(), same)))


def test_question_must_have_measurable_proxy_and_resolvable_source_field():
    assert "metric" in _codes(_thesis(questions=(replace(_q1(), metric="offensive_rhythm"), _q2())))
    assert "source_field" in _codes(_thesis(questions=(replace(_q1(), source_field="units.PHI.vibes"), _q2())))
    assert "direction" in _codes(_thesis(questions=(replace(_q1(), direction="above"), _q2())))
    assert "adds_beyond_line" in _codes(_thesis(questions=(replace(_q1(), adds_beyond_line=" "), _q2())))


def test_rate_proxies_need_an_adequate_stated_sample():
    assert "sample_n" in _codes(_thesis(questions=(replace(_q1(), sample_n=12), _q2())))
    assert "sample_n" in _codes(_thesis(questions=(replace(_q1(), sample_n=None), _q2())))


def test_collapse_threshold_must_not_be_easy_to_hit():
    assert "easy_threshold" in _codes(_thesis(), percentile_of=lambda m, t, d: 0.50)
    assert "easy_threshold" not in _codes(_thesis(), percentile_of=lambda m, t, d: 0.75)
    assert "easy_threshold" not in _codes(_thesis(), percentile_of=lambda m, t, d: None)


def test_marginals_are_five_point_buckets_within_the_logodds_cap_and_reasoned():
    bad_bucket = (QuestionMarginal("q1", 0.25, 0.27), QuestionMarginal("q2", 0.45, 0.45))
    assert "marginal_bucket" in _codes(_thesis(marginals=bad_bucket))
    too_far = (QuestionMarginal("q1", 0.25, 0.40, "x"), QuestionMarginal("q2", 0.45, 0.45))
    assert "marginal_shift" in _codes(_thesis(marginals=too_far))
    no_reason = (QuestionMarginal("q1", 0.40, 0.45, ""), QuestionMarginal("q2", 0.45, 0.45))
    assert "marginal_reason" in _codes(_thesis(marginals=no_reason))
    assert "marginals" in _codes(_thesis(marginals=(QuestionMarginal("q1", 0.25, 0.25),)))


def test_dependency_needs_a_channel_a_legal_lambda_and_real_questions():
    assert "dependency_channel" in _codes(_thesis(dependencies=(Dependency("q1", "q2", " ", 0.5),)))
    assert "dependency_lambda" in _codes(_thesis(dependencies=(Dependency("q1", "q2", "ch", 1.5),)))
    assert "dependency_ids" in _codes(_thesis(dependencies=(Dependency("q1", "qX", "ch", 0.5),)))


def test_branch_probabilities_must_be_derived_from_marginals_and_dependency():
    bs = list(_branches())
    bs[0] = replace(bs[0], prob=bs[0].prob + 0.06)
    bs[1] = replace(bs[1], prob=bs[1].prob - 0.06)
    assert "joint_mismatch" in _codes(_thesis(branches=tuple(bs)))


def test_branch_probabilities_must_sum_to_one_and_stay_in_bounds():
    bs = list(_branches())
    bs[0] = replace(bs[0], prob=0.60)
    codes = _codes(_thesis(branches=tuple(bs)))
    assert "prob_sum" in codes and "branch_prob_bounds" not in codes or "branch_prob_bounds" in codes
    tiny = list(_branches())
    tiny[0] = replace(tiny[0], prob=0.005)
    assert "branch_prob_bounds" in _codes(_thesis(branches=tuple(tiny)))


def test_a_residual_branch_with_enough_mass_is_required():
    no_res = tuple(b for b in _branches() if not b.is_residual)
    assert "residual_branch" in _codes(_thesis(branches=no_res))
    assert "residual_prob" in _codes(_thesis(branches=_branches(residual=0.05)))


def test_every_combination_of_answers_needs_a_branch():
    bs = tuple(b for i, b in enumerate(_branches()) if i != 0)
    assert "missing_branch" in _codes(_thesis(branches=bs))


def test_branch_must_answer_every_independent_question():
    bs = list(_branches())
    bs[0] = replace(bs[0], answers=(("q1", True),))
    assert "branch_answers" in _codes(_thesis(branches=tuple(bs)))


def test_player_outcomes_must_be_on_the_slate_in_the_bins_and_within_clamps():
    bad_id = _branches(outcomes=(PlayerBranchOutcome("ghost", 1.0, 1.0),))
    assert "unknown_player" in _codes(_thesis(branches=bad_id))
    bad_bin = _branches(outcomes=(PlayerBranchOutcome("hurts", 0.85, 1.0),))
    assert "multiplier_bin" in _codes(_thesis(branches=bad_bin))
    too_many = _branches(outcomes=tuple(PlayerBranchOutcome("hurts", 1.0, 1.0) for _ in range(21)))
    assert "too_many_players" in _codes(_thesis(branches=too_many))


def test_counter_branch_and_what_would_change_my_mind_are_required():
    assert "counter_branch" in _codes(_thesis(counter_branch_id="nope"))
    assert "would_change_mind" in _codes(_thesis(would_change_mind=()))
    assert "would_change_mind" in _codes(_thesis(would_change_mind=(" ",)))


def test_claims_must_cite_resolvable_packet_keys_and_availability_claims_need_a_basis():
    assert "claim_uncited" in _codes(_thesis(claims=(Claim("trust me", ()),)))
    assert "cite_unresolved" in _codes(_thesis(claims=(Claim("x", ("units.PHI.made_up",)),)))
    assert "availability_basis" in _codes(_thesis(claims=(Claim("RT out", ("availability.PHI.rt",), "availability"),)))
    assert "claims" in _codes(_thesis(claims=()))


def test_pair_signs_need_known_players_valid_values_and_a_reason():
    assert "unknown_player" in _codes(_thesis(pair_signs=(PairSign("hurts", "ghost", "negative", "mild", "r"),)))
    assert "pair_sign" in _codes(_thesis(pair_signs=(PairSign("hurts", "smith", "sideways", "mild", "r"),)))
    assert "pair_sign_reason" in _codes(_thesis(pair_signs=(PairSign("hurts", "smith", "negative", "mild", " "),)))


def test_line_mix_passes_warns_and_fails_loudly():
    def shifted(mix_margin):
        bs = list(_branches())
        bs[0] = replace(bs[0], margin_shift=mix_margin / bs[0].prob)  # puts the whole mix shift in one branch
        return tuple(bs)

    ok = validate_game_thesis(_thesis(branches=shifted(0.5)), packet_keys=PACKET_KEYS, slate_player_ids=SLATE)
    assert [x for x in ok if x.code == "line_mix"] == []
    warn = [x for x in validate_game_thesis(_thesis(branches=shifted(1.5)), packet_keys=PACKET_KEYS, slate_player_ids=SLATE) if x.code == "line_mix"]
    assert [x.severity for x in warn] == ["warning"]
    assert "line_mix" in _codes(_thesis(branches=shifted(3.0)))


def test_a_declared_disagreement_offsets_the_mix_but_is_itself_bounded():
    bs = list(_branches())
    bs[0] = replace(bs[0], margin_shift=3.0 / bs[0].prob)
    declared = _thesis(branches=tuple(bs), declared_margin_disagreement=2.0)
    assert "line_mix" not in _codes(declared)  # |3.0 - 2.0| = 1.0 -> within the pass band
    assert "declared_disagreement" in _codes(_thesis(declared_margin_disagreement=3.5))


from nfl_dfs.build.thesis.lint import lint_lineup_pair_signs  # noqa: E402


def test_lineup_lint_errors_on_strong_negatives_warns_on_mild_and_ignores_the_rest():
    signs = [
        PairSign("qb", "opp_dst", "negative", "strong", "the QB was sacked or picked if the DST scored"),
        PairSign("rb", "wr", "negative", "mild", "blowout volume throttles the pass game"),
        PairSign("qb", "wr", "positive", "strong", "stack"),
        PairSign("te", "dst", "neutral", "mild", "n/a"),
    ]
    out = lint_lineup_pair_signs({"qb", "opp_dst", "rb", "wr", "te", "dst"}, signs)
    assert [(v.code, v.severity) for v in out] == [("negative_pair", "error"), ("negative_pair", "warning")]
    assert lint_lineup_pair_signs({"qb", "wr"}, signs) == []  # only the pairs actually held count
