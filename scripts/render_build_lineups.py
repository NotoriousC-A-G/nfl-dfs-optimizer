"""Render the redesigned build's lineups (the 5 expert-designed agents) as one HTML page.

    PYTHONPATH=. .venv/bin/python scripts/render_build_lineups.py

Reads the week's latest build record (`data/snapshots/redesign/`, written by `build_pool_lineups.py`) and the slate snapshot it was
built from, and writes `dashboard_output/build_lineups.html`: per agent, its thesis, spend plan, bets and views, then each lineup's
roster with salary, projection, projected ownership, tier and the expert's reason for that tier. The legacy weekly dashboard shows the
six ILP-only agent lineups instead; this is the page for the lineups that are actually candidates for entry.
Pure rendering -- no network, safe to re-run. NOT part of `pytest`.
"""

from __future__ import annotations

import html
from pathlib import Path

from nfl_dfs.storage.build_artifact_store import load_latest_build_artifact
from nfl_dfs.storage.slate_snapshot_store import load_latest_slate_snapshot
from scripts.live_integration_check_dashboard import SEASON, WEEK

OUT = Path("dashboard_output/build_lineups.html")
SLOTS = (("QB", 1), ("RB", 2), ("WR", 3), ("TE", 1))
POS_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "DST": 5}


def _roster(players: list[dict]) -> list[tuple[str, dict]]:
    """Label each player with its roster slot; the one RB/WR/TE beyond the position minimums is the FLEX."""
    seen: dict[str, int] = {}
    labelled = []
    for p in sorted(players, key=lambda p: (POS_ORDER[p["position"]], -p["salary"])):
        pos = p["position"]
        seen[pos] = seen.get(pos, 0) + 1
        limit = dict(SLOTS).get(pos, 1)
        labelled.append(("FLEX" if seen[pos] > limit else pos, p))
    return sorted(labelled, key=lambda x: (x[0] == "FLEX", POS_ORDER[x[1]["position"]]))


def _player_table(snapshot: dict) -> dict[str, dict]:
    table = {}
    for rec in snapshot["player_pool"]:
        ident = rec["identity"]
        own = (rec.get("ownership") or {}).get("projected_ownership")
        table[ident["canonical_id"]] = {
            "name": ident["display_name"], "team": rec["team"], "position": rec["position"], "salary": rec["salary"],
            "proj": rec.get("projection"), "own": own, "opp": rec.get("opponent_team_this_week"),
        }
    return table


def _num(x, fmt: str) -> str:
    return "" if x is None else fmt.format(x)


def _e(x) -> str:
    return html.escape(str(x))


def render(build: dict, snapshot: dict) -> str:
    table = _player_table(snapshot)
    parts = [f"<h1>Week {build['week']} build lineups</h1>",
             f"<p class='meta'>Build {_e(build['created_at'][:16])}Z from snapshot {_e(build['snapshot_filename'])}. "
             f"Unavailable agents: {_e(', '.join(build['expert']['unavailable']) or 'none')}.</p>",
             f"<p>{_e(build['expert'].get('portfolio_notes', ''))}</p>"]
    for agent_id, a in build["agents"].items():
        parts.append(f"<section><h2>{_e(agent_id)}</h2>")
        if a["status"] != "built":
            parts.append(f"<p class='bad'>{_e(a['status'])}: {_e(a.get('first_failure'))}</p></section>")
            continue
        bt = a["build_thesis"]
        parts.append(f"<p>{_e(bt['reason'])}</p><p class='meta'>Floor lean {a['floor_lean']:+.1f} | spend "
                     f"{_e(', '.join(f'{k} {v}' for k, v in a['spend_plan'].items()))} | backs {_e(', '.join(bt['backs']))}"
                     f"{' | avoids ' + _e(', '.join(bt['avoids'])) if bt['avoids'] else ''}</p>")
        for i, lu in enumerate(a["lineups"]):
            tiers = {pid: (tier, why) for pid, tier, why in a["pools_used"][i]} if i < len(a["pools_used"]) else {}
            rows, own_total = [], 0.0
            players = []
            for pid in lu["player_ids"]:
                p = table.get(pid) or {"name": pid.replace("DST_", "") + " DST", "team": pid.replace("DST_", ""), "position": "DST", "salary": 0, "proj": None, "own": None, "opp": ""}
                players.append({**p, "id": pid})
            for slot, p in _roster(players):
                tier, why = tiers.get(p["id"], ("", ""))
                own_total += p["own"] or 0
                stacked = " class='stack'" if p["id"] in lu["stack"] else ""
                rows.append(f"<tr{stacked}><td>{slot}</td><td>{_e(p['name'])}</td><td>{_e(p['team'])}{' vs ' + _e(p['opp']) if p['opp'] else ''}</td>"
                            f"<td class='n'>${p['salary']:,}</td><td class='n'>{_num(p['proj'], '{:.1f}')}</td>"
                            f"<td class='n'>{_num(p['own'], '{:.1f}%')}</td><td>{_e(tier)}</td><td class='why'>{_e(why)}</td></tr>")
            parts.append(f"<h3>${lu['salary']:,} | proj {lu['projected']:.1f} | views {_e(', '.join(lu['views']))} | "
                         f"core {lu['tiers'].get('core', 0)} / eligible {lu['tiers'].get('eligible', 0)} / reach {lu['tiers'].get('reach', 0)} | "
                         f"sum own {own_total:.0f}%</h3><p class='meta'>{_e(lu['note'])}</p>"
                         "<table><tr><th>Slot</th><th>Player</th><th>Game</th><th>Salary</th><th>Proj</th><th>Own</th><th>Tier</th><th>Why</th></tr>"
                         + "".join(rows) + "</table>")
            if lu["unresolved_players"]:
                parts.append(f"<p class='bad'>Unresolved status: {_e(lu['unresolved_players'])}</p>")
        for w in a["warnings"]:
            parts.append(f"<p class='bad'>warning: {_e(w)}</p>")
        parts.append("</section>")
    style = ("body{font:14px system-ui;max-width:1100px;margin:24px auto;padding:0 16px;color:#222;background:#fff}"
             "table{border-collapse:collapse;width:100%;margin:6px 0 18px}td,th{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left}"
             ".n{text-align:right;font-variant-numeric:tabular-nums}.meta{color:#666}.bad{color:#b00}.why{color:#666;font-size:12px}"
             "tr.stack td{background:#eef6ff}section{border-top:2px solid #333;margin-top:28px}"
             "@media(prefers-color-scheme:dark){body{background:#161616;color:#ddd}td,th{border-color:#333}tr.stack td{background:#1d2b3a}.meta,.why{color:#999}}")
    return f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Build lineups</title><style>{style}</style>" + "".join(parts)


def main() -> None:
    build = load_latest_build_artifact(SEASON, WEEK)
    if build is None:
        raise SystemExit(f"No build record for season={SEASON} week={WEEK} -- run build_pool_lineups.py first")
    snapshot = load_latest_slate_snapshot(SEASON, WEEK)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(build, snapshot))
    print(f"Wrote {OUT} ({len(build['agents'])} agent(s), build {build['created_at'][:16]}Z)")


if __name__ == "__main__":
    main()
