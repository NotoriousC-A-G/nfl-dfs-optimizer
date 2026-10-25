"""Dashboard tabs fed by the redesigned pipeline (ADR-0046): "Build lineups" and "Evidence packets".

Each tab sits between marker comments in the dashboard HTML so a later script can refresh just that tab (`splice_panel`).

The legacy tabs show the six ILP-only agent lineups the dashboard script solves itself. The lineups that are actually candidates for entry
come later, from `scripts/build_pool_lineups.py` (analysts -> expert -> pool-constrained solve), and live in a build record under
`data/snapshots/redesign/`. This module renders that record as the first dashboard tab and splices it into an already-written dashboard,
which is how `build_pool_lineups.py` keeps the page current (the dashboard script runs BEFORE the build, so it can only leave an empty
state). Pure rendering -- no network.
"""

from __future__ import annotations

import html
import re

EMPTY_STATE = '<div class="empty-state">No build yet for this snapshot. Run the analyst and expert stages, then <code>build_pool_lineups.py</code>; it fills this tab.</div>'
PACKETS_EMPTY_STATE = '<div class="empty-state">No evidence packets yet. Run <code>render_evidence_packets.py</code>; it fills this tab.</div>'

# DraftKings roster order; FLEX is the one RB/WR/TE beyond the position minimums.
SLOT_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "DST": 5}
SLOT_COUNTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}

_CSS = (
    "<style>.bp-meta{color:var(--fg2);font-size:0.82rem}.bp-agent{border-top:2px solid var(--border);margin-top:22px;padding-top:4px}"
    ".bp-bad{color:var(--red)}.bp-why{color:var(--fg2);font-size:0.74rem}.bp-num{text-align:right}tr.bp-stack td{background:var(--accent-light)}"
    ".bp-table{min-width:0}.packets-frame{width:100%;height:85vh;border:1px solid var(--border);border-radius:var(--radius)}</style>"
)


def player_table_from_snapshot(snapshot: dict) -> dict[str, dict]:
    table = {}
    for rec in snapshot["player_pool"]:
        ident = rec["identity"]
        table[ident["canonical_id"]] = {
            "name": ident["display_name"], "team": rec["team"], "position": rec["position"], "salary": rec["salary"],
            "proj": rec.get("projection"), "own": (rec.get("ownership") or {}).get("projected_ownership"),
            "opp": rec.get("opponent_team_this_week"),
        }
    return table


def roster_slots(players: list[dict]) -> list[tuple[str, dict]]:
    """Label each player with its roster slot, in DK order (QB RB RB WR WR WR TE FLEX DST)."""
    seen: dict[str, int] = {}
    labelled = []
    for p in sorted(players, key=lambda p: (SLOT_ORDER[p["position"]], -p["salary"])):
        seen[p["position"]] = seen.get(p["position"], 0) + 1
        labelled.append(("FLEX" if seen[p["position"]] > SLOT_COUNTS[p["position"]] else p["position"], p))
    return sorted(labelled, key=lambda x: SLOT_ORDER[x[0]])


def _e(x) -> str:
    return html.escape(str(x))


def _num(x, fmt: str) -> str:
    return "" if x is None else fmt.format(x)


def _lineup_html(lu: dict, tiers: dict[str, tuple[str, str]], table: dict[str, dict]) -> str:
    players = []
    for pid in lu["player_ids"]:
        team = pid.replace("DST_", "")
        players.append({"id": pid, **(table.get(pid) or {"name": f"{team} DST", "team": team, "position": "DST", "salary": 0, "proj": None, "own": None, "opp": ""})})
    rows, own_total = [], 0.0
    for slot, p in roster_slots(players):
        tier, why = tiers.get(p["id"], ("", ""))
        own_total += p["own"] or 0
        cls = ' class="bp-stack"' if p["id"] in lu["stack"] else ""
        game = _e(p["team"]) + (f" vs {_e(p['opp'])}" if p["opp"] else "")
        rows.append(f"<tr{cls}><td>{slot}</td><td>{_e(p['name'])}</td><td>{game}</td><td class='bp-num'>${p['salary']:,}</td>"
                    f"<td class='bp-num'>{_num(p['proj'], '{:.1f}')}</td><td class='bp-num'>{_num(p['own'], '{:.1f}%')}</td>"
                    f"<td>{_e(tier)}</td><td class='bp-why'>{_e(why)}</td></tr>")
    t = lu["tiers"]
    views = f" &middot; views {_e(', '.join(lu['views']))}" if lu["views"] else ""
    tier_counts = f" &middot; core {t.get('core', 0)} / eligible {t.get('eligible', 0)} / reach {t.get('reach', 0)}" if t else ""
    head = (f"<h3>${lu['salary']:,} &middot; proj {lu['projected']:.1f}{views}{tier_counts} &middot; summed ownership {own_total:.0f}%</h3>"
            f"<p class='bp-meta'>{_e(lu['note'])}</p>")
    body = ('<table class="bp-table"><thead><tr><th>Slot</th><th>Player</th><th>Game</th><th>Salary</th><th>Proj (vendor)</th><th>Own</th>'
            "<th>Tier</th><th>Why</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    bad = f"<p class='bp-bad'>Unresolved status: {_e(lu['unresolved_players'])}</p>" if lu["unresolved_players"] else ""
    return head + body + bad


def chalk_anchor_lineup(snapshot: dict) -> dict | None:
    """The Chalk Anchor (the plain best-projection baseline, not pooled -- see `build/agents.py`) shaped like a build lineup, or None."""
    for entry in snapshot.get("agent_lineups", []):
        if entry["agent"]["agent_id"] == "chalk_anchor":
            lu = entry["lineup"]
            return {"views": [], "stack": [], "player_ids": [p["canonical_id"] for p in lu["slots"].values()],
                    "note": "Plain best projection under the salary cap. Not an expert design: the field-average lineup to compare the five designs against.",
                    "salary": lu["total_salary"], "projected": lu["total_projected_points"], "tiers": {}, "unresolved_players": []}
    return None


def render_build_panel(build: dict, table: dict[str, dict], chalk_anchor: dict | None = None) -> str:
    parts = [_CSS, f"<p class='bp-meta'>Build {_e(build['created_at'][:16])}Z from snapshot {_e(build['snapshot_filename'])}. "
                   f"Unavailable agents: {_e(', '.join(build['expert']['unavailable']) or 'none')}. Entry candidates; the Lineups tab shows the ILP-only baselines.</p>",
             f"<p>{_e(build['expert'].get('portfolio_notes', ''))}</p>"]
    for agent_id, a in build["agents"].items():
        parts.append(f"<div class='bp-agent'><h2>{_e(agent_id)}</h2>")
        if a["status"] != "built":
            parts.append(f"<p class='bp-bad'>{_e(a['status'])}: {_e(a.get('first_failure'))}</p></div>")
            continue
        bt = a["build_thesis"]
        avoids = f" &middot; avoids {_e(', '.join(bt['avoids']))}" if bt["avoids"] else ""
        spend = ", ".join(f"{k} {v}" for k, v in a["spend_plan"].items())
        parts.append(f"<p>{_e(bt['reason'])}</p><p class='bp-meta'>Floor lean {a['floor_lean']:+.1f} &middot; spend {_e(spend)} &middot; backs {_e(', '.join(bt['backs']))}{avoids}</p>")
        for i, lu in enumerate(a["lineups"]):
            tiers = {pid: (tier, why) for pid, tier, why in a["pools_used"][i]} if i < len(a["pools_used"]) else {}
            parts.append(_lineup_html(lu, tiers, table))
        parts.extend(f"<p class='bp-bad'>warning: {_e(w)}</p>" for w in a["warnings"])
        parts.append("</div>")
    if chalk_anchor is not None:
        parts.append("<div class='bp-agent'><h2>chalk_anchor (reference baseline)</h2>"
                     "<p class='bp-meta'>Not one of the five designs: the plain best-projection lineup, shown so the designs can be judged against the chalk.</p>"
                     + _lineup_html(chalk_anchor, {}, table) + "</div>")
    return "".join(parts)


def _markers(name: str) -> tuple[str, str]:
    return f"<!--{name}-panel:start-->", f"<!--{name}-panel:end-->"


def splice_panel(dashboard_html: str, name: str, panel_html: str) -> str:
    """Replace the marked `name` panel ("build" or "packets") inside an already-rendered dashboard."""
    start, end = _markers(name)
    pattern = re.compile(re.escape(start) + ".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(dashboard_html):
        raise ValueError(f"dashboard has no {name}-panel markers -- regenerate it with live_integration_check_dashboard.py")
    return pattern.sub(lambda _m: start + panel_html + end, dashboard_html, count=1)


def render_packets_panel(page_html: str) -> str:
    """The standalone evidence-packets page, isolated in an iframe (its CSS targets bare body/table/th and reuses the dashboard's variable names)."""
    return f'<iframe class="packets-frame" title="Evidence packets" srcdoc="{html.escape(page_html, quote=True)}"></iframe>'


def _refresh(path: str, name: str, panel_html: str) -> None:
    with open(path, encoding="utf-8") as f:
        current = f.read()
    updated = splice_panel(current, name, panel_html)
    with open(path, "w", encoding="utf-8") as f:
        f.write(updated)


def refresh_build_panel(path: str, build: dict, snapshot: dict) -> None:
    _refresh(path, "build", render_build_panel(build, player_table_from_snapshot(snapshot), chalk_anchor_lineup(snapshot)))


def refresh_packets_panel(path: str, page_html: str) -> None:
    _refresh(path, "packets", render_packets_panel(page_html))
