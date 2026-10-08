"""JSON round-trip for `GameThesis` (cache + slate snapshot persistence)."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from nfl_dfs.build.thesis.contracts import (
    Battle, Branch, Claim, Dependency, GameThesis, PairSign, PivotalQuestion, PlayerBranchOutcome, QuestionMarginal,
)


def thesis_to_json(thesis: GameThesis) -> str:
    return json.dumps(asdict(thesis), sort_keys=True)


def thesis_from_dict(d: dict[str, Any]) -> GameThesis:
    return GameThesis(
        schema_version=d["schema_version"], game_id=d["game_id"], packet_sha=d["packet_sha"],
        prompt_version=d["prompt_version"], model=d["model"], headline=d["headline"],
        questions=tuple(PivotalQuestion(**q) for q in d["questions"]),
        marginals=tuple(QuestionMarginal(**m) for m in d["marginals"]),
        dependencies=tuple(Dependency(**x) for x in d["dependencies"]),
        branches=tuple(
            Branch(
                branch_id=b["branch_id"], answers=tuple((q, bool(a)) for q, a in b["answers"]), is_residual=b["is_residual"],
                prob=b["prob"], description=b["description"], chain=tuple(b["chain"]),
                player_outcomes=tuple(PlayerBranchOutcome(**o) for o in b["player_outcomes"]),
                margin_shift=b["margin_shift"], total_shift=b["total_shift"],
            )
            for b in d["branches"]
        ),
        counter_branch_id=d["counter_branch_id"],
        claims=tuple(Claim(text=c["text"], cite_keys=tuple(c["cite_keys"]), kind=c["kind"], status=c["status"], as_of=c["as_of"]) for c in d["claims"]),
        would_change_mind=tuple(d["would_change_mind"]),
        pair_signs=tuple(PairSign(**p) for p in d["pair_signs"]),
        declared_margin_disagreement=d["declared_margin_disagreement"],
        declared_total_disagreement=d["declared_total_disagreement"],
        battles=tuple(Battle(**{**b, "evidence_keys": tuple(b["evidence_keys"])}) for b in d.get("battles", ())),
    )


def thesis_from_json(blob: str) -> GameThesis:
    return thesis_from_dict(json.loads(blob))
