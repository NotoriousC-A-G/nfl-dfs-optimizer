"""Deterministic validation of a model-written `GameThesis`.

Nothing downstream may trust a thesis that has not passed this. Every rule here comes from a reviewer
or from Chris (football and model-analytics challenge of Revision 1, QA review; plan D1-D3 and
Revision 2) and each failure is a `Violation` with a stable `code` and a message precise enough to
send back to the model for one retry (the retry/reject policy lives in the runner, not here).

`severity="error"` rejects the thesis. `severity="warning"` is surfaced but does not reject (e.g. the
line-mix check between its pass band and its hard-fail band).
"""

from __future__ import annotations

import math
from typing import Callable

from nfl_dfs.build.common import Violation
from nfl_dfs.build.thesis.contracts import (
    CLAIM_KINDS, CONVICTIONS, DIRECTIONS, MULTIPLIER_BINS, PHASES, PROXY_METRICS, SCHEMA_VERSION, SIGNS, STRENGTHS, GameThesis,
)
from nfl_dfs.build.thesis.joint import expected_branch_probs, logit

# --- disclosed draft magnitudes (not backtested) ---------------------------------------------------
MAX_INDEPENDENT_QUESTIONS = 2
MIN_BATTLES, MAX_BATTLES = 2, 4
MAX_TOTAL_QUESTIONS = 3  # independent + at most one child
MIN_SAMPLE_N_FOR_RATE = 25
MIN_RESIDUAL_PROB = 0.10
BRANCH_PROB_BOUNDS = (0.02, 0.90)
PROB_SUM_TOLERANCE = 0.01
JOINT_TOLERANCE = 0.02
MARGINAL_BUCKET = 0.05
MAX_LOGODDS_SHIFT = 0.25
ALLOWED_LAMBDAS = (-0.5, 0.5)
MEAN_MULT_RANGE = (0.7, 1.4)
Q90_MULT_RANGE = (0.7, 1.6)
MAX_PLAYERS_PER_BRANCH = 20  # <= 10 per team in a game
LINE_PASS_BAND = 1.0
LINE_HARD_FAIL = 2.0
MAX_DECLARED_DISAGREEMENT = 2.0
BRANCH_SHIFT_WARN = 8.0
COLLAPSE_PERCENTILE_FLOOR = 0.60  # threshold of a "pressure/collapse" question must sit at/above this league percentile
_COLLAPSE_METRICS = frozenset({"sack_rate", "qb_hit_rate"})


# Callable(metric, threshold, direction) -> league percentile of the threshold in [0, 1], or None if unknown.
PercentileFn = Callable[[str, float, str], "float | None"]


def _near_bucket(p: float) -> bool:
    return abs(p / MARGINAL_BUCKET - round(p / MARGINAL_BUCKET)) < 1e-6


def _allowed_adjusted(anchor: float) -> set[float]:
    """5-point buckets within +/-0.25 log-odds of the anchor; if none exists (rounding can push a
    one-bucket move past the cap at low/high p), the bucket nearest the anchor is allowed."""
    buckets = [round(i * MARGINAL_BUCKET, 10) for i in range(1, int(1 / MARGINAL_BUCKET))]
    within = {b for b in buckets if abs(logit(b) - logit(anchor)) <= MAX_LOGODDS_SHIFT + 1e-9}
    nearest = min(buckets, key=lambda b: abs(b - anchor))
    return within | {nearest}


def validate_game_thesis(
    thesis: GameThesis,
    *,
    packet_keys: set[str],
    slate_player_ids: set[str],
    percentile_of: PercentileFn | None = None,
    require_battles: bool = False,
) -> list[Violation]:
    v: list[Violation] = []

    def err(code: str, message: str, path: str = "") -> None:
        v.append(Violation(code, message, path))

    def warn(code: str, message: str, path: str = "") -> None:
        v.append(Violation(code, message, path, "warning"))

    if thesis.schema_version != SCHEMA_VERSION:
        err("schema_version", f"schema_version must be {SCHEMA_VERSION}, got {thesis.schema_version}")
    if not thesis.headline.strip():
        err("headline", "headline is required")

    # ---- questions ---------------------------------------------------------------------------
    qids = [q.question_id for q in thesis.questions]
    if len(set(qids)) != len(qids):
        err("question_ids", "question ids must be unique", "questions")
    by_id = {q.question_id: q for q in thesis.questions}
    independent = [q for q in thesis.questions if q.parent_id is None]
    children = [q for q in thesis.questions if q.parent_id is not None]
    if not independent:
        err("no_questions", "at least one independent pivotal question is required", "questions")
    if len(independent) > MAX_INDEPENDENT_QUESTIONS:
        err("too_many_questions", f"at most {MAX_INDEPENDENT_QUESTIONS} independent questions per game, got {len(independent)}", "questions")
    if len(thesis.questions) > MAX_TOTAL_QUESTIONS:
        err("too_many_questions", f"at most {MAX_TOTAL_QUESTIONS} questions in total (a 3rd must be a child of the first), got {len(thesis.questions)}", "questions")
    for q in children:
        if q.parent_id not in by_id or by_id[q.parent_id].parent_id is not None or q.parent_answer is None:
            err("child_question", f"{q.question_id}: a child needs an existing independent parent_id and a parent_answer", f"questions.{q.question_id}")
    phases = [q.phase for q in independent]
    if len(independent) == 2 and phases[0] == phases[1]:
        err("same_phase", f"the two independent questions are both about '{phases[0]}' -- they must concern different units or phases", "questions")

    for q in thesis.questions:
        path = f"questions.{q.question_id}"
        if q.phase not in PHASES:
            err("phase", f"{q.question_id}: phase '{q.phase}' not in {sorted(PHASES)}", path)
        if q.metric not in PROXY_METRICS:
            err("metric", f"{q.question_id}: metric '{q.metric}' is not a measurable pbp proxy (allowed: {sorted(PROXY_METRICS)})", path)
        if q.source_field not in packet_keys:
            err("source_field", f"{q.source_field!r} is not a field in the evidence packet", path)
        if q.direction not in DIRECTIONS:
            err("direction", f"{q.question_id}: direction must be one of {DIRECTIONS}", path)
        if not math.isfinite(q.threshold):
            err("threshold", f"{q.question_id}: threshold must be a finite number", path)
        if "rate" in q.metric and (q.sample_n is None or q.sample_n < MIN_SAMPLE_N_FOR_RATE):
            err("sample_n", f"{q.question_id}: a rate proxy needs a stated sample of at least {MIN_SAMPLE_N_FOR_RATE} plays, got {q.sample_n}", path)
        if not q.adds_beyond_line.strip():
            err("adds_beyond_line", f"{q.question_id}: say what this question adds beyond the line", path)
        if percentile_of is not None and q.metric in _COLLAPSE_METRICS and q.direction == "gte":
            pct = percentile_of(q.metric, q.threshold, q.direction)
            if pct is not None and pct < COLLAPSE_PERCENTILE_FLOOR:
                err("easy_threshold", f"{q.question_id}: a collapse threshold must sit at or above the {COLLAPSE_PERCENTILE_FLOOR:.0%} league percentile, this is {pct:.0%} (it confirms the story too easily)", path)

    # ---- marginals / dependencies -------------------------------------------------------------
    marg = {m.question_id: m for m in thesis.marginals}
    if set(marg) != set(qids):
        err("marginals", f"need exactly one marginal per question; have {sorted(marg)} for questions {sorted(qids)}", "marginals")
    for m in thesis.marginals:
        path = f"marginals.{m.question_id}"
        if not (0.0 < m.anchor < 1.0 and 0.0 < m.adjusted < 1.0):
            err("marginal_range", f"{m.question_id}: anchor and adjusted must be strictly between 0 and 1", path)
            continue
        if not _near_bucket(m.adjusted):
            err("marginal_bucket", f"{m.question_id}: adjusted probability {m.adjusted} must be a {MARGINAL_BUCKET:.0%} bucket", path)
        elif abs(m.adjusted - m.anchor) > 1e-9 and m.adjusted not in _allowed_adjusted(m.anchor):
            err("marginal_shift", f"{m.question_id}: adjusted {m.adjusted} is more than {MAX_LOGODDS_SHIFT} log-odds from the anchor {m.anchor:.3f}", path)
        nearest = min((round(i * MARGINAL_BUCKET, 10) for i in range(1, int(1 / MARGINAL_BUCKET))), key=lambda b: abs(b - m.anchor))
        if abs(m.adjusted - nearest) > 1e-9 and not m.reason.strip():
            err("marginal_reason", f"{m.question_id}: moving off the bucket nearest the anchor ({nearest:.2f}) needs a cited reason", path)

    for d in thesis.dependencies:
        path = f"dependencies.{d.from_id}->{d.to_id}"
        if d.from_id not in by_id or d.to_id not in by_id or d.from_id == d.to_id:
            err("dependency_ids", "dependency must link two different existing questions", path)
        if d.lambda_ not in ALLOWED_LAMBDAS:
            err("dependency_lambda", f"lambda must be one of {ALLOWED_LAMBDAS} (a stated direction of effect, not a fitted size), got {d.lambda_}", path)
        if not d.channel.strip():
            err("dependency_channel", "a dependency must name its causal channel; otherwise it is decoration", path)
    if len(thesis.dependencies) > 1:
        err("dependency_count", "at most one dependency (two independent questions)", "dependencies")

    # ---- branches -----------------------------------------------------------------------------
    bids = [b.branch_id for b in thesis.branches]
    if len(set(bids)) != len(bids):
        err("branch_ids", "branch ids must be unique", "branches")
    residual = [b for b in thesis.branches if b.is_residual]
    if len(residual) != 1:
        err("residual_branch", f"exactly one residual branch ('neither resolves as framed') is required, got {len(residual)}", "branches")
    elif residual[0].prob < MIN_RESIDUAL_PROB - 1e-9:
        err("residual_prob", f"the residual branch needs probability >= {MIN_RESIDUAL_PROB}, got {residual[0].prob}", "branches")
    total_p = sum(b.prob for b in thesis.branches)
    if abs(total_p - 1.0) > PROB_SUM_TOLERANCE:
        err("prob_sum", f"branch probabilities sum to {total_p:.3f}, must be 1.0 (+/-{PROB_SUM_TOLERANCE})", "branches")
    lo, hi = BRANCH_PROB_BOUNDS
    for b in thesis.branches:
        path = f"branches.{b.branch_id}"
        if not (lo - 1e-9 <= b.prob <= hi + 1e-9):
            err("branch_prob_bounds", f"{b.branch_id}: probability {b.prob} outside [{lo}, {hi}]", path)
        if b.is_residual and b.answers:
            err("residual_answers", f"{b.branch_id}: the residual branch has no answers", path)
        if not b.is_residual:
            _validate_branch_answers(b, by_id, independent, err)
        if len(b.player_outcomes) > MAX_PLAYERS_PER_BRANCH:
            err("too_many_players", f"{b.branch_id}: at most {MAX_PLAYERS_PER_BRANCH} named players per branch", path)
        for o in b.player_outcomes:
            if o.canonical_id not in slate_player_ids:
                err("unknown_player", f"{b.branch_id}: player id {o.canonical_id!r} is not on the slate", path)
            if o.mean_mult not in MULTIPLIER_BINS or o.q90_mult not in MULTIPLIER_BINS:
                err("multiplier_bin", f"{b.branch_id}/{o.canonical_id}: multipliers must be one of {MULTIPLIER_BINS}, got {o.mean_mult}/{o.q90_mult}", path)
            elif not (MEAN_MULT_RANGE[0] <= o.mean_mult <= MEAN_MULT_RANGE[1] and Q90_MULT_RANGE[0] <= o.q90_mult <= Q90_MULT_RANGE[1]):
                err("multiplier_range", f"{b.branch_id}/{o.canonical_id}: multipliers outside the draft clamps", path)
        if abs(b.margin_shift) > BRANCH_SHIFT_WARN or abs(b.total_shift) > BRANCH_SHIFT_WARN:
            warn("large_branch_shift", f"{b.branch_id}: a branch shifts the line by more than {BRANCH_SHIFT_WARN} points -- implausible for one pivotal factor", path)

    _check_joint(thesis, independent, children, marg, residual, err)

    if thesis.counter_branch_id not in bids:
        err("counter_branch", f"counter_branch_id {thesis.counter_branch_id!r} is not one of the branches", "counter_branch_id")
    if not thesis.would_change_mind or any(not s.strip() for s in thesis.would_change_mind):
        err("would_change_mind", "name at least one pre-kickoff observable that would change this thesis (inactives, weather, a line move)", "would_change_mind")

    # ---- line mix -----------------------------------------------------------------------------
    for label, declared in (("margin", thesis.declared_margin_disagreement), ("total", thesis.declared_total_disagreement)):
        if abs(declared) > MAX_DECLARED_DISAGREEMENT + 1e-9:
            err("declared_disagreement", f"declared {label} disagreement with the line must be within +/-{MAX_DECLARED_DISAGREEMENT} points, got {declared}", label)
    for violation in check_line_mix(thesis):
        v.append(violation)

    # ---- claims / pair signs ------------------------------------------------------------------
    if not thesis.claims:
        err("claims", "a thesis needs at least one claim citing the evidence packet", "claims")
    for i, c in enumerate(thesis.claims):
        path = f"claims[{i}]"
        if not c.text.strip():
            err("claim_text", "claim text is required", path)
        if not c.cite_keys:
            err("claim_uncited", f"claim {c.text[:50]!r} cites nothing -- UNVERIFIED", path)
        for key in c.cite_keys:
            if key not in packet_keys:
                err("cite_unresolved", f"cite key {key!r} is not in the evidence packet -- UNVERIFIED", path)
        if c.kind not in CLAIM_KINDS:
            err("claim_kind", f"kind must be one of {CLAIM_KINDS}", path)
        if c.kind == "availability" and not (c.status and c.as_of):
            err("availability_basis", "an availability claim must state the status and its timestamp", path)
    branch_ids = {b.branch_id for b in thesis.branches if not b.is_residual}
    if (require_battles or thesis.battles) and not (MIN_BATTLES <= len(thesis.battles) <= MAX_BATTLES):
        err("battles_count", f"name {MIN_BATTLES}-{MAX_BATTLES} battles (the matchups that decide this game), got {len(thesis.battles)}", "battles")
    for i, bt in enumerate(thesis.battles):
        path = f"battles[{i}]"
        for field_name in ("title", "call", "consequence"):
            if not getattr(bt, field_name).strip():
                err("battle_field", f"a battle needs a {field_name}", path)
        if bt.conviction not in CONVICTIONS:
            err("battle_conviction", f"conviction must be one of {CONVICTIONS}", path)
        if not bt.evidence_keys:
            err("battle_uncited", f"battle {bt.title[:50]!r} cites nothing -- UNVERIFIED", path)
        for key in bt.evidence_keys:
            if key not in packet_keys:
                err("cite_unresolved", f"battle cite key {key!r} is not in the evidence packet -- UNVERIFIED", path)
        if bt.leans_branch is not None and bt.leans_branch not in branch_ids:
            err("battle_branch", f"leans_branch {bt.leans_branch!r} is not a (non-residual) branch of this thesis: {sorted(branch_ids)}", path)
    for i, s in enumerate(thesis.pair_signs):
        path = f"pair_signs[{i}]"
        if s.sign not in SIGNS or s.strength not in STRENGTHS:
            err("pair_sign", f"sign must be one of {SIGNS} and strength one of {STRENGTHS}", path)
        if s.player_a not in slate_player_ids or s.player_b not in slate_player_ids:
            err("unknown_player", "pair sign references a player id that is not on the slate", path)
        if not s.reason.strip():
            err("pair_sign_reason", "a stated correlation sign needs a reason", path)
    return v


def _validate_branch_answers(b, by_id, independent, err) -> None:
    answered = dict(b.answers)
    path = f"branches.{b.branch_id}"
    for q in independent:
        if q.question_id not in answered:
            err("branch_answers", f"{b.branch_id}: missing an answer for independent question {q.question_id}", path)
    for q in by_id.values():
        if q.parent_id is None:
            continue
        live = q.parent_id in answered and answered[q.parent_id] == q.parent_answer
        if live and q.question_id not in answered:
            err("branch_answers", f"{b.branch_id}: child question {q.question_id} is live here and needs an answer", path)
        if not live and q.question_id in answered:
            err("branch_answers", f"{b.branch_id}: child question {q.question_id} is not live here and must not be answered", path)
    for qid in answered:
        if qid not in by_id:
            err("branch_answers", f"{b.branch_id}: answers an unknown question {qid}", path)


def _check_joint(thesis, independent, children, marg, residual, err) -> None:
    """Recompute branch probabilities from marginals + dependency and require agreement (so the
    probabilities are derived in code, not free-typed). Skipped when a child question exists (only the
    sum check applies there) or the marginals are unusable."""
    if children or not (1 <= len(independent) <= MAX_INDEPENDENT_QUESTIONS) or len(residual) != 1:
        return
    ids = tuple(sorted(q.question_id for q in independent))
    if any(i not in marg or not (0.0 < marg[i].adjusted < 1.0) for i in ids):
        return
    lam = 0.0
    for d in thesis.dependencies:
        if {d.from_id, d.to_id} == set(ids) and d.lambda_ in ALLOWED_LAMBDAS:
            lam = d.lambda_
    expected = expected_branch_probs({i: marg[i].adjusted for i in ids}, ids, lam, residual[0].prob)
    for b in thesis.branches:
        if b.is_residual:
            continue
        key = tuple(sorted(b.answers))
        if key not in expected:
            continue  # already reported as a branch_answers problem
        if abs(b.prob - expected[key]) > JOINT_TOLERANCE:
            err("joint_mismatch", f"{b.branch_id}: probability {b.prob:.3f} does not match the {expected[key]:.3f} implied by the marginals and dependency", f"branches.{b.branch_id}")
    covered = {tuple(sorted(b.answers)) for b in thesis.branches if not b.is_residual}
    for key in expected:
        if key not in covered:
            err("missing_branch", f"no branch for answers {dict(key)} -- branches must cover every combination", "branches")


def check_line_mix(thesis: GameThesis) -> list[Violation]:
    """The probability-weighted branch shifts must reproduce the betting line (margin and total)
    after the analyst's *declared* disagreement: within +/-1.0 point passes, within +/-2.0 warns, more
    fails loudly (no silent renormalizing). A mix that cannot reproduce the market is either wrong or
    an undeclared edge, and the analyst must say which."""
    out: list[Violation] = []
    for label, shift, declared in (
        ("margin", lambda b: b.margin_shift, thesis.declared_margin_disagreement),
        ("total", lambda b: b.total_shift, thesis.declared_total_disagreement),
    ):
        mix = sum(b.prob * shift(b) for b in thesis.branches)
        gap = abs(mix - declared)
        if gap > LINE_HARD_FAIL:
            out.append(Violation("line_mix", f"the branch mix moves the {label} by {mix:+.1f} vs the line but only {declared:+.1f} was declared: off by {gap:.1f} points (> {LINE_HARD_FAIL})", label))
        elif gap > LINE_PASS_BAND:
            out.append(Violation("line_mix", f"the branch mix moves the {label} by {mix:+.1f} vs the line ({declared:+.1f} declared): {gap:.1f} points off", label, "warning"))
    return out
