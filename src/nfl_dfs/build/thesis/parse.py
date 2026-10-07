"""Turn a game analyst's JSON answer into a validated `GameThesis`.

What the model writes vs what code computes -- by design the model does NO arithmetic:
- **Anchors** are computed here (`evidence.anchors.question_anchor`); the model supplies only its adjusted
  marginal and a reason.
- **Branch probabilities** are derived here from the marginals, the stated dependency and the residual mass
  (`thesis.joint`); the model supplies the branches' answers, story and per-player outcomes, never `prob`
  (except for a game with a child question, where the joint has no closed form and `prob` is required).
- `packet_sha`, `schema_version`, `prompt_version` and `model` come from the caller, never the model.

Structural problems (missing keys, wrong types) come back as `parse` violations; everything else is the
validator's job. Returns `(thesis | None, violations)`; `None` only when the JSON could not be read at all.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import numpy as np

from nfl_dfs.build.common import Violation
from nfl_dfs.build.evidence.anchors import make_percentile_fn, question_anchor
from nfl_dfs.build.evidence.contracts import EvidencePacket, packet_keys
from nfl_dfs.build.thesis.contracts import (
    SCHEMA_VERSION, Branch, Claim, Dependency, GameThesis, PairSign, PivotalQuestion, PlayerBranchOutcome, QuestionMarginal,
)
from nfl_dfs.build.thesis.joint import expected_branch_probs
from nfl_dfs.build.thesis.validate import validate_game_thesis

LeagueValueFn = Callable[[str], np.ndarray]


def _strip_fences(text: str) -> str:
    """Models sometimes wrap JSON in a markdown fence despite instructions; tolerate that, nothing else."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _team_of(source_field: str) -> str | None:
    parts = source_field.split(".")
    return parts[1] if len(parts) >= 3 and parts[0] in ("units", "teams") else None


def _team_spreads(packet: EvidencePacket) -> dict[str, float]:
    fav = packet.lines.favorite
    other = packet.away if fav == packet.home else packet.home
    return {fav: packet.lines.abs_spread, other: -packet.lines.abs_spread}


def parse_analyst_response(
    raw: str | dict[str, Any], packet: EvidencePacket, league_value_fn: LeagueValueFn, *, prompt_version: str, model: str,
) -> tuple[GameThesis | None, list[Violation]]:
    try:
        d = json.loads(_strip_fences(raw)) if isinstance(raw, str) else raw
        if not isinstance(d, dict):
            raise ValueError("top-level JSON must be an object")
    except (ValueError, TypeError) as exc:
        return None, [Violation("parse", f"response is not valid JSON: {exc}")]
    try:
        thesis, extra = _build(d, packet, league_value_fn, prompt_version, model)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return None, [Violation("parse", f"response does not match the schema: {type(exc).__name__}: {exc}")]
    violations = extra + validate_game_thesis(
        thesis, packet_keys=packet_keys(packet), slate_player_ids={p.canonical_id for p in packet.players},
        percentile_of=make_percentile_fn(league_value_fn),
    )
    return thesis, violations


def _build(d: dict, packet: EvidencePacket, league_value_fn: LeagueValueFn, prompt_version: str, model: str) -> tuple[GameThesis, list[Violation]]:
    extra: list[Violation] = []
    questions = tuple(
        PivotalQuestion(
            question_id=q["id"], text=q["text"], phase=q["phase"], metric=q["metric"], source_field=q["source_field"],
            threshold=float(q["threshold"]), direction=q["direction"], sample_n=q.get("sample_n"),
            adds_beyond_line=q.get("adds_beyond_line", ""), parent_id=q.get("parent_id"), parent_answer=q.get("parent_answer"),
        )
        for q in d["questions"]
    )
    spreads = _team_spreads(packet)
    adjusted = {m["question_id"]: m for m in d["marginals"]}
    marginals = []
    for q in questions:
        anchor = question_anchor(q, league_value_fn=league_value_fn, team_spread=spreads, question_team=_team_of(q.source_field))
        if anchor is None:
            extra.append(Violation("unanchorable", f"{q.question_id}: no anchor can be computed for metric {q.metric!r} (no league data, or a lead question with no team); pick a supported metric/threshold", f"questions.{q.question_id}"))
            anchor = 0.5
        m = adjusted.get(q.question_id)
        if m is None:
            continue  # validator reports the missing marginal
        marginals.append(QuestionMarginal(q.question_id, anchor, float(m["adjusted"]), m.get("reason", "")))
    deps = ()
    if d.get("dependency"):
        x = d["dependency"]
        deps = (Dependency(x["from"], x["to"], x.get("channel", ""), float(x["lambda"])),)

    independent = tuple(sorted(q.question_id for q in questions if q.parent_id is None))
    children = [q for q in questions if q.parent_id is not None]
    residual = float(d.get("residual_prob", 0.10))
    derivable = not children and 1 <= len(independent) <= 2 and all(i in {m.question_id for m in marginals} for i in independent)
    expected: dict = {}
    if derivable and all(0.0 < m.adjusted < 1.0 for m in marginals):
        lam = deps[0].lambda_ if deps and deps[0].lambda_ in (-0.5, 0.5) and {deps[0].from_id, deps[0].to_id} == set(independent) else 0.0
        expected = expected_branch_probs({m.question_id: m.adjusted for m in marginals}, independent, lam, residual)

    branches = []
    for b in d["branches"]:
        is_res = bool(b.get("residual", False))
        answers = tuple(sorted((qid, bool(v)) for qid, v in (b.get("answers") or {}).items()))
        if is_res:
            prob = residual
        elif expected:  # derivable in code: a probability the model volunteered is IGNORED, never trusted
            prob = round(expected.get(answers, 0.0), 6)
        elif "prob" in b:  # e.g. a game with a child question: no closed-form joint, the model must supply it
            prob = float(b["prob"])
        else:
            prob = 0.0
        branches.append(Branch(
            branch_id=b["id"], answers=() if is_res else answers, is_residual=is_res, prob=prob, description=b.get("description", ""),
            chain=tuple(b.get("chain", ())),
            player_outcomes=tuple(PlayerBranchOutcome(o["player_id"], float(o["mean_mult"]), float(o["q90_mult"]), o.get("reason", "")) for o in b.get("player_outcomes", ())),
            margin_shift=float(b.get("margin_shift", 0.0)), total_shift=float(b.get("total_shift", 0.0)),
        ))
    claims = tuple(Claim(c["text"], tuple(c.get("cite_keys", ())), c.get("kind", "general"), c.get("status"), c.get("as_of")) for c in d["claims"])
    signs = tuple(PairSign(p["player_a"], p["player_b"], p["sign"], p["strength"], p.get("reason", "")) for p in d.get("pair_signs", ()))
    thesis = GameThesis(
        schema_version=SCHEMA_VERSION, game_id=packet.game_id, packet_sha=packet.packet_sha, prompt_version=prompt_version, model=model,
        headline=d["headline"], questions=questions, marginals=tuple(marginals), dependencies=deps, branches=tuple(branches),
        counter_branch_id=d["counter_branch_id"], claims=claims, would_change_mind=tuple(d.get("would_change_mind", ())),
        pair_signs=signs, declared_margin_disagreement=float(d.get("declared_margin_disagreement", 0.0)),
        declared_total_disagreement=float(d.get("declared_total_disagreement", 0.0)),
    )
    return thesis, extra
