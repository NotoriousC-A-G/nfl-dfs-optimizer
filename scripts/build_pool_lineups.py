"""Run the pool-built agents end to end for a week: validated game theses -> expert pools -> tail values ->
pool-constrained lineups, one agent at a time, each failing LOUDLY on its own (nothing is substituted).

    PYTHONPATH=. .venv/bin/python scripts/build_pool_lineups.py [--n 2]

Requires the analyst and expert stages to be COMPLETE (`scripts/run_llm_stages.py collect-analysts` /
`collect-expert`) and the fitted tail-value table (`scripts/tail_value_calibration_report.py`).
Cross-agent rules are applied here: every lineup differs from every earlier agent's lineups by at least 3
players, and agents are built in a fixed order. An agent whose pool fails (e.g. an under-spent lineup) goes back to the expert ONCE (`build/expert/repair.py`, exit 2 while its
repair request awaits an answer); one that still cannot be built is reported with its diagnostic;
an agent the expert marked unavailable is reported with the expert's reason.
NOT part of `pytest` (loads play-by-play and a saved snapshot).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
import warnings
from pathlib import Path

import nfl_data_py as nfl

from nfl_dfs.build.agents import POOL_AGENT_BY_ID
from nfl_dfs.build.availability import availability_from_snapshot
from nfl_dfs.build.evidence.builder import build_evidence_packets
from nfl_dfs.build.evidence.opportunity import trailing_shares
from nfl_dfs.build.evidence.metrics import league_values, team_game_metrics
from nfl_dfs.build.expert.repair import build_with_repair, output_to_json
from nfl_dfs.build.expert.stage import expert_spec
from nfl_dfs.build.pool.contracts import PlayerRef
from nfl_dfs.build.runner import collect_results, require_all_ok
from nfl_dfs.build.thesis.contracts import PairSign
from nfl_dfs.build.thesis.stage import analyst_specs
from nfl_dfs.build.value.calibration import CalibrationTable
from nfl_dfs.optimizer.script_solve import build_agent_by_script
from nfl_dfs.projection.blend import PlayerProjection
from nfl_dfs.storage.build_artifact_store import save_build_artifact
from nfl_dfs.storage.slate_snapshot_store import load_latest_slate_snapshot
from scripts.live_integration_check_dashboard import SEASON, WEEK
from scripts.run_llm_stages import ANALYST_FRESHNESS, MODEL_ID, _freshness

TABLE = Path("data/cache/tail_value_cells.json")
RG_STATUS = {"O": "OUT", "D": "D", "Q": "Q"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=2, help="lineups per agent")
    args = ap.parse_args()
    if not TABLE.exists():
        raise SystemExit(f"{TABLE} missing -- run scripts/tail_value_calibration_report.py first")

    snapshot = load_latest_slate_snapshot(SEASON, WEEK)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pbp = nfl.import_pbp_data([SEASON - 1, SEASON], include_participation=False)
    tg = team_game_metrics(pbp)
    league_fn = lambda m: league_values(tg, m)
    as_of = str(snapshot.get("timestamp", "unknown"))[:16] + "Z"
    availability, _warn = availability_from_snapshot(snapshot)
    if _warn:
        print(f"WARNING: {_warn}")
    packets = build_evidence_packets(snapshot["player_pool"], snapshot["stack_profiles"], tg[tg["season"] == SEASON], season=SEASON, week=WEEK, as_of=as_of, availability=availability, opportunity=trailing_shares(pbp, season=SEASON, through_week=WEEK - 1))
    fresh, kw = _freshness(), dict(season=SEASON, week=WEEK)

    a_res = collect_results(analyst_specs(packets, league_fn, model=MODEL_ID, freshness=ANALYST_FRESHNESS), **kw)
    require_all_ok("analyst", a_res)  # raises with the full list if any game is incomplete
    theses = {r.item_id: r.result for r in a_res}
    universe = [PlayerRef(p.canonical_id, p.name, p.team, p.position, p.salary, p.projection, p.status_after_q_pass or p.injury_status, gid)
                for gid, pk in packets.items() for p in pk.players]
    e_res = collect_results([expert_spec(theses, packets, universe, model=MODEL_ID, freshness=fresh)], **kw)
    require_all_ok("expert", e_res)
    expert = e_res[0].result

    # Projections for exactly the players the expert/analysts saw (statuses use the DK vocabulary after the Q pass)
    status_map = {"cleared": "Q_CLEARED", "barred": "BARRED", "out": "OUT"}
    projs = [PlayerProjection(p.canonical_id, p.name, p.position, p.team, p.salary, p.projection, 2, {"x": p.projection},
                              status_map.get(p.status_after_q_pass) or p.injury_status)
             for pk in packets.values() for p in pk.players if p.salary is not None and p.projection is not None]
    table = CalibrationTable.from_json(TABLE.read_text())
    opp, game_of = {}, {}
    for gid, pk in packets.items():
        opp[pk.home], opp[pk.away] = pk.away, pk.home
        game_of[pk.home] = game_of[pk.away] = gid
    signs: list[PairSign] = [s for t in theses.values() for s in t.pair_signs]

    print(f"{len(theses)} theses; expert unavailable: {expert.unavailable or 'none'}; portfolio: {expert.portfolio_notes[:200]}")
    avoid: list[frozenset[str]] = []
    exit_code = 0
    scripted: dict = {}
    agents_record: dict[str, dict] = {a: {"status": "unavailable_by_expert", "detail": r} for a, r in expert.unavailable.items()}
    for out in expert.outputs:
        lean = POOL_AGENT_BY_ID[out.agent_id].floor_lean  # each agent maximizes its own tilt of the tail value

        def build(o, _avoid=list(avoid)):
            # One lineup per script variation; a repaired pool may back different scripts than the first one
            sb = build_agent_by_script(
                o, universe=universe, theses=theses, packets=packets, projections=projs, table=table, floor_lean=lean, n=args.n,
                opponent_of=opp, game_id_by_team=game_of, pair_signs=signs, avoid_lineups=_avoid,
            )
            scripted[o.agent_id] = sb
            return sb.result
        oc = build_with_repair(out, build, theses, packets, universe, model=MODEL_ID, freshness=fresh, season=SEASON, week=WEEK)
        if oc.status != "built":
            exit_code = 2 if oc.status == "awaiting_repair" else 1
            label = "AWAITING EXPERT REPAIR" if oc.status == "awaiting_repair" else "CANNOT BUILD"
            print(f"\n[{out.agent_id}] {label} -- {oc.detail}")
            if oc.first_failure is not None:
                print(f"  diagnosis: {oc.first_failure}")
            agents_record[out.agent_id] = {"status": oc.status, "floor_lean": lean, "detail": oc.detail, "first_failure": str(oc.first_failure) if oc.first_failure else None,
                                           "failure_diagnostics": oc.first_failure.diagnostics if oc.first_failure else None, "expert_pool": output_to_json(out)}
            continue
        sb = scripted[out.agent_id]
        res = sb.result
        print(f"\n[{out.agent_id}] (floor lean {lean:+.1f}){' REPAIRED by the expert after: ' + str(oc.first_failure) if oc.repaired else ''} | "
              f"scripts {list(dict.fromkeys(sb.scripts))} | {res.pool.build_thesis.reason[:150]}")
        lineup_records = []
        for i, (lu, ref) in enumerate(zip(res.lineups, sb.scripts), 1):
            tier_of = {e.canonical_id: e.tier for e in sb.pools[ref].entries}
            tc = {t: sum(1 for p in lu.players if tier_of.get(p.canonical_id) == t) for t in ("core", "eligible", "reach")}
            avoid.append(frozenset(p.canonical_id for p in lu.players))
            print(f"  L{i} [{ref}] ${lu.total_salary:,} proj {lu.total_projected_points:.1f} [core {tc['core']} / eligible {tc['eligible']} / reach {tc['reach']}] | "
                  + ", ".join(f"{p.position} {p.display_name}" + ("*" if tier_of.get(p.canonical_id) == "reach" else "") for p in lu.players))
            lineup_records.append({"script": ref, "player_ids": [p.canonical_id for p in lu.players], "salary": lu.total_salary, "projected": lu.total_projected_points, "tiers": tc})
        for w in res.warnings:
            print(f"  warning: {w.message}")
        agents_record[out.agent_id] = {
            "status": "built", "floor_lean": lean, "repaired": oc.repaired, "first_failure": str(oc.first_failure) if oc.first_failure else None,
            "scripts": list(dict.fromkeys(sb.scripts)), "build_thesis": asdict(res.pool.build_thesis), "expert_pool": output_to_json(out),
            "pools_used": {ref: [(e.canonical_id, e.tier, e.reason) for e in p.entries if e.tier != "exclude"] for ref, p in sb.pools.items()},
            "lineups": lineup_records, "warnings": [w.message for w in res.warnings],
        }
    path = save_build_artifact(SEASON, WEEK, {
        "snapshot_timestamp": snapshot.get("timestamp"), "snapshot_filename": snapshot.get("snapshot_filename"), "model": MODEL_ID,
        "availability_decisions": [asdict(d) for d in availability],
        "theses": {gid: asdict(t) for gid, t in theses.items()},
        "expert": {"unavailable": dict(expert.unavailable), "portfolio_notes": expert.portfolio_notes},
        "agents": agents_record, "exit_code": exit_code,
    })
    print(f"\nWrote the build record (theses, pools, lineups, failures) to {path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
