"""Shared fixtures for the build-pipeline tests: a hand-built evidence packet, a league distribution, and a
well-formed analyst response."""

import numpy as np

from nfl_dfs.build.evidence.anchors import question_anchor
from nfl_dfs.build.evidence.contracts import (
    AvailabilityItem, EvidencePacket, Lines, MetricValue, PlayerEvidence, TeamEvidence, with_sha,
)

LEAGUE = {
    "sack_rate": np.linspace(0.0, 0.20, 100), "rush_attempts_leading": np.arange(0, 25, dtype=float),
    "qb_hit_rate": np.linspace(0.05, 0.25, 100), "pace_seconds_per_play": np.linspace(24, 34, 100),
}
league_fn = lambda m: LEAGUE.get(m, np.array([]))


def _player(cid, name, team, pos):
    return PlayerEvidence(cid, name, team, pos, 6000, 15.0, 8.0, 1.0, False, False, None, None, None, None, None, None, None)


def _packet() -> EvidencePacket:
    te = lambda team, opp: TeamEvidence(
        team, opp, 55.0, 50.0, "favorite", 0.9, 23.0,
        metrics={"sack_rate": MetricValue(0.09, 40, 0.8), "rush_attempts_leading": MetricValue(9.0, 3, 0.5), "lead_at_q4": MetricValue(0.5, 3, 0.5)},
    )
    return with_sha(EvidencePacket(
        1, "LAR@PHI", 2026, 4, "PHI", "LAR", "early",
        Lines(3.5, 44.5, 20.5, 24.0, "LAR", 3.5, 0.6), {"PHI": te("PHI", "LAR"), "LAR": te("LAR", "PHI")},
        (_player("hurts", "Jalen Hurts", "PHI", "QB"), _player("smith", "DeVonta Smith", "PHI", "WR"),
         _player("rams_rb", "Rams RB", "LAR", "RB"), _player("kupp", "Cooper Kupp", "LAR", "WR")),
        (AvailabilityItem("PHI RT", "PHI", "out", "official: Out", "official", "2026-10-09T18:00Z"),), None, ("grades availability-unaware",),
    ))


def _q(qid, phase, metric, field, thr, direction="gte", n=40):
    return {"id": qid, "text": f"{qid}?", "phase": phase, "metric": metric, "source_field": field, "threshold": thr,
            "direction": direction, "sample_n": n, "adds_beyond_line": "not in the line"}


def _response(packet, *, extra=None, drop=None):
    q1 = _q("q1", "pass_protection", "sack_rate", "units.PHI.sack_rate", 0.15)
    q2 = _q("q2", "run_game", "rush_attempts_leading", "units.LAR.rush_attempts_leading", 12.0, n=None)
    def nearest(q):
        from nfl_dfs.build.thesis.contracts import PivotalQuestion as PQ
        pq = PQ(q["id"], "t", q["phase"], q["metric"], q["source_field"], q["threshold"], q["direction"], q["sample_n"], "a")
        team = q["source_field"].split(".")[1]
        a = question_anchor(pq, league_value_fn=league_fn, team_spread={"LAR": 3.5, "PHI": -3.5}, question_team=team)
        return round(round(a / 0.05) * 0.05, 2)
    outs = [{"player_id": "hurts", "mean_mult": 0.9, "q90_mult": 0.9, "reason": "pressure cuts his dropbacks"}]
    d = {
        "headline": "PHI loses its RT -> LAR pressure -> PHI pass game stalls",
        "questions": [q1, q2],
        "marginals": [{"question_id": "q1", "adjusted": nearest(q1), "reason": ""}, {"question_id": "q2", "adjusted": nearest(q2), "reason": ""}],
        "dependency": {"from": "q1", "to": "q2", "channel": "pressure ends drives so LAR leads more", "lambda": 0.5},
        "residual_prob": 0.10,
        "branches": [
            {"id": f"b{i}", "answers": {"q1": a, "q2": b}, "description": f"branch {i}", "chain": ["link one", "link two"], "player_outcomes": outs}
            for i, (a, b) in enumerate([(True, True), (True, False), (False, True), (False, False)])
        ] + [{"id": "res", "residual": True, "description": "neither resolves as framed", "chain": []}],
        "counter_branch_id": "res",
        "claims": [{"text": "PHI sack rate is elevated", "cite_keys": ["units.PHI.sack_rate"], "kind": "general", "status": None, "as_of": None}],
        "would_change_mind": ["PHI RT inactive status Sunday morning"],
        "pair_signs": [{"player_a": "hurts", "player_b": "smith", "sign": "positive", "strength": "strong", "reason": "same offense"}],
    }
    d.update(extra or {})
    for k in drop or ():
        d.pop(k)
    return d


