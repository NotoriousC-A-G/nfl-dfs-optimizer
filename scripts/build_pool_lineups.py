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
import warnings
from pathlib import Path

import nfl_data_py as nfl

from nfl_dfs.build.agents import POOL_AGENT_BY_ID
from nfl_dfs.build.evidence.builder import build_evidence_packets
from nfl_dfs.build.evidence.metrics import league_values, team_game_metrics
from nfl_dfs.build.expert.repair import build_with_repair
from nfl_dfs.build.expert.stage import expert_spec
from nfl_dfs.build.pool.contracts import PlayerRef
from nfl_dfs.build.pool.expand import expand_pool
from nfl_dfs.build.runner import collect_results, require_all_ok
from nfl_dfs.build.thesis.contracts import PairSign
from nfl_dfs.build.thesis.stage import analyst_specs
from nfl_dfs.build.value.calibration import CalibrationTable
from nfl_dfs.build.value.tail_value import expected_multipliers, tail_values
from nfl_dfs.optimizer.pool_solve import build_agent_lineups
from nfl_dfs.projection.blend import PlayerProjection
from nfl_dfs.storage.slate_snapshot_store import load_latest_slate_snapshot
from scripts.live_integration_check_dashboard import SEASON, WEEK
from scripts.run_llm_stages import MODEL_ID, _freshness

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
    packets = build_evidence_packets(snapshot["player_pool"], snapshot["stack_profiles"], tg[tg["season"] == SEASON], season=SEASON, week=WEEK, as_of=as_of)
    fresh, kw = _freshness(), dict(season=SEASON, week=WEEK)

    a_res = collect_results(analyst_specs(packets, league_fn, model=MODEL_ID, freshness=fresh), **kw)
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
    mult = expected_multipliers(theses.values())
    inputs = [(p.canonical_id, p.position, p.blended_projection) for p in projs]
    opp, game_of = {}, {}
    for gid, pk in packets.items():
        opp[pk.home], opp[pk.away] = pk.away, pk.home
        game_of[pk.home] = game_of[pk.away] = gid
    signs: list[PairSign] = [s for t in theses.values() for s in t.pair_signs]

    print(f"{len(theses)} theses; expert unavailable: {expert.unavailable or 'none'}; portfolio: {expert.portfolio_notes[:200]}")
    avoid: list[frozenset[str]] = []
    exit_code = 0
    for out in expert.outputs:
        lean = POOL_AGENT_BY_ID[out.agent_id].floor_lean  # each agent maximizes its own tilt of the tail value
        values = {i: v.tv for i, v in tail_values(inputs, table, multipliers=mult, floor_lean=lean).items()}

        def build(o, _avoid=list(avoid), values=values):
            return build_agent_lineups(expand_pool(o, universe), projs, values, n=args.n, opponent_of=opp, game_id_by_team=game_of, pair_signs=signs,
                                       avoid_lineups=_avoid, min_player_difference=3)
        oc = build_with_repair(out, build, theses, packets, universe, model=MODEL_ID, freshness=fresh, season=SEASON, week=WEEK)
        if oc.status != "built":
            exit_code = 2 if oc.status == "awaiting_repair" else 1
            label = "AWAITING EXPERT REPAIR" if oc.status == "awaiting_repair" else "CANNOT BUILD"
            print(f"\n[{out.agent_id}] {label} -- {oc.detail}")
            if oc.first_failure is not None:
                print(f"  diagnosis: {oc.first_failure}")
            continue
        res = oc.result
        o = res.pool
        print(f"\n[{out.agent_id}] (floor lean {lean:+.1f}){' REPAIRED by the expert after: ' + str(oc.first_failure) if oc.repaired else ''} | widened: {list(o.widened_steps) or 'no'} | "
              f"backs {list(o.build_thesis.backs)} | {o.build_thesis.reason[:140]}")
        for i, lu in enumerate(res.lineups, 1):
            avoid.append(frozenset(p.canonical_id for p in lu.players))
            print(f"  L{i} ${lu.total_salary:,} proj {lu.total_projected_points:.1f} | " + ", ".join(f"{p.position} {p.display_name}" for p in lu.players))
        for w in res.warnings:
            print(f"  warning: {w.message}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
