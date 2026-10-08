"""Drive the LLM stages (game analysts, then the expert) through the file contract.

    PYTHONPATH=. .venv/bin/python scripts/run_llm_stages.py prepare-analysts   # writes one prompt.md per game
    #   ... a backend (a Claude Code session subagent on Friday) writes response.json next to each prompt ...
    PYTHONPATH=. .venv/bin/python scripts/run_llm_stages.py collect-analysts   # validates; ok / retry / rejected / awaiting
    PYTHONPATH=. .venv/bin/python scripts/run_llm_stages.py prepare-expert     # needs ALL analyst theses valid
    PYTHONPATH=. .venv/bin/python scripts/run_llm_stages.py collect-expert

Everything is read from the week's latest saved slate snapshot (SEASON/WEEK come from
`live_integration_check_dashboard.py`) plus play-by-play for the league reference distributions. A stage that
is not fully complete exits non-zero and says exactly what is missing -- nothing is skipped or substituted.
`--games A@B,C@D` limits the analyst stage to those games (the expert then sees only those theses).
NOT part of `pytest` (loads play-by-play over the network).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import warnings
from pathlib import Path

import nfl_data_py as nfl

from nfl_dfs.build.availability import availability_from_snapshot
from nfl_dfs.build.evidence.builder import build_evidence_packets
from nfl_dfs.build.evidence.opportunity import trailing_shares
from nfl_dfs.build.evidence.metrics import league_values, team_game_metrics
from nfl_dfs.build.expert.stage import expert_spec
from nfl_dfs.build.pool.contracts import PlayerRef
from nfl_dfs.build.runner import StageFailure, collect_results, prepare_requests, require_all_ok
from nfl_dfs.build.thesis.stage import analyst_specs
from nfl_dfs.storage.injury_clearance_store import OVERRIDES_PATH
from nfl_dfs.storage.official_injury_snapshot_store import latest_snapshot
from nfl_dfs.storage.slate_snapshot_store import load_latest_slate_snapshot
from scripts.live_integration_check_dashboard import SEASON, WEEK

MODEL_ID = "claude-session-subagent"
# Analyst answers are keyed on the packet's MATERIAL fingerprint (line, units, availability, vacated roles, weather), which already
# carries the Q/override decisions, so nothing outside the packet should invalidate them. In particular NOT the injury-capture time:
# a Sunday-noon re-pull must re-ask only the games whose facts changed.
ANALYST_FRESHNESS = "packet-v1"


def _freshness() -> str:
    """Anything outside the packet that should invalidate a cached answer: Chris's Q-override file and the
    time of the latest official injury capture."""
    ov = OVERRIDES_PATH.read_text() if OVERRIDES_PATH.exists() else ""
    snap = latest_snapshot(SEASON, WEEK)
    return hashlib.sha256((ov + "|" + (snap.fetched_at if snap else "no-injury-capture")).encode()).hexdigest()[:16]


def _load(games: set[str] | None):
    snapshot = load_latest_slate_snapshot(SEASON, WEEK)
    if snapshot is None:
        raise SystemExit(f"No slate snapshot for season={SEASON} week={WEEK}.")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pbp = nfl.import_pbp_data([SEASON - 1, SEASON], include_participation=False)  # last season stabilises the league distributions
    tg = team_game_metrics(pbp)
    tg_current = tg[tg["season"] == SEASON]
    # The data's own capture time, NOT the wall clock: a clock-derived stamp changes the packet hash every minute and
    # silently invalidates cached analyst answers (found in the 2026-10-07 rehearsal).
    as_of = str(snapshot.get("timestamp", "unknown"))[:16] + "Z"
    availability, _warn = availability_from_snapshot(snapshot)
    if _warn:
        print(f"WARNING: {_warn}")
    packets = build_evidence_packets(snapshot["player_pool"], snapshot["stack_profiles"], tg_current, season=SEASON, week=WEEK, as_of=as_of, availability=availability, opportunity=trailing_shares(pbp, season=SEASON, through_week=WEEK - 1))
    if games:
        unknown = games - set(packets)
        if unknown:
            raise SystemExit(f"Unknown game(s) {sorted(unknown)}; slate has {sorted(packets)}")
        packets = {g: p for g, p in packets.items() if g in games}
    return packets, (lambda metric: league_values(tg, metric)), snapshot


def _report(stage: str, results) -> bool:
    ok = True
    for r in results:
        line = f"  {r.item_id:12} {r.status}"
        if r.errors:
            line += f"  -- {r.errors[0].code}: {r.errors[0].message[:140]}"
        print(line)
        ok &= r.status in ("ok", "cached")
    print(f"{stage}: {'COMPLETE' if ok else 'NOT COMPLETE'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["prepare-analysts", "collect-analysts", "prepare-expert", "collect-expert"])
    ap.add_argument("--games", help="comma-separated game ids, e.g. LAR@PHI,KC@LV")
    args = ap.parse_args()
    games = set(args.games.split(",")) if args.games else None
    packets, league_fn, snapshot = _load(games)
    kw = dict(season=SEASON, week=WEEK)
    fresh = _freshness()
    a_specs = analyst_specs(packets, league_fn, model=MODEL_ID, freshness=ANALYST_FRESHNESS)

    if args.command == "prepare-analysts":
        pending = prepare_requests(a_specs, **kw)
        retry = [r.directory for r in collect_results(a_specs, **kw) if r.status == "retry"]
        print(f"{len(a_specs)} game(s); {len(pending)} awaiting an answer (prompt.md written):")
        for d in pending:
            print(f"  {d}" + ("   <-- use retry_prompt.md" if d in retry else ""))
        return 0
    if args.command == "collect-analysts":
        return 0 if _report("analysts", collect_results(a_specs, **kw)) else 1

    results = collect_results(a_specs, **kw)
    try:
        require_all_ok("analyst", results)
    except StageFailure as exc:
        print(f"Cannot run the expert: {exc}")
        return 1
    theses = {r.item_id: r.result for r in results}
    universe = [
        PlayerRef(p.canonical_id, p.name, p.team, p.position, p.salary, p.projection, p.status_after_q_pass or p.injury_status, gid)
        for gid, pk in packets.items() for p in pk.players
    ]
    e_spec = expert_spec(theses, packets, universe, model=MODEL_ID, freshness=fresh)
    if args.command == "prepare-expert":
        for d in prepare_requests([e_spec], **kw):
            print(f"expert request: {d}")
        return 0
    res = collect_results([e_spec], **kw)
    ok = _report("expert", res)
    if ok:
        print("  unavailable agents:", res[0].result.unavailable or "none")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
