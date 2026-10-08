"""Static HTML view of the evidence packets: one collapsible card per game, so Chris (and anyone
reviewing a thesis) can see exactly what an analyst was given -- lines, team proxy metrics with league
percentiles, the players listed, availability, and the explicit data gaps. Mirrors the postmortem
page's conventions (dark cards, `<details>`, no JS). Every value is HTML-escaped; a missing value
renders as a dash, never a made-up number.
"""

from __future__ import annotations

import html
from typing import Any

from nfl_dfs.build.evidence.contracts import EvidencePacket, MetricValue, packet_keys

_RATE_METRICS = {
    "sack_rate", "qb_hit_rate", "explosive_pass_rate", "def_sack_rate", "def_qb_hit_rate", "lead_at_q3_start", "lead_at_q4",
}
_LABELS = {
    "sack_rate": "Sack rate (allowed)", "qb_hit_rate": "QB-hit rate (allowed)", "explosive_pass_rate": "Explosive pass rate (20+ yds)",
    "pass_epa": "Pass EPA / dropback", "rush_epa": "Rush EPA / rush", "pass_rate_over_expected": "Pass rate over expected (pts)",
    "pass_rate_over_expected_leading": "Pass rate over expected, leading (pts)", "pace_seconds_per_play": "Pace (sec / play)",
    "total_plays": "Plays / game", "rush_attempts_leading": "Rush attempts / game while leading 4+, 2nd half",
    "combined_pass_attempts": "Pass attempts / game", "lead_at_q3_start": "Led entering Q3 (share of games)",
    "lead_at_q4": "Led entering Q4 (share of games)", "def_sack_rate": "Pass rush: sack rate generated",
    "def_qb_hit_rate": "Pass rush: QB-hit rate generated",
}
_STYLE = """
<style>
  :root { --bg:#0a0d13; --card:#161c29; --line:#232a3d; --text:#eef1f7; --muted:#aab4c8; --accent:#7c9bff; --warn:#ffc94d; --good:#3ddc9b; --bad:#fb7979; }
  @media (prefers-color-scheme: light) { :root { --bg:#f6f7fb; --card:#ffffff; --line:#dfe3ee; --text:#161a24; --muted:#5b667d; --accent:#3556d6; --warn:#a56a00; --good:#0a7d52; --bad:#c2332f; } }
  body { font-family: -apple-system,'Segoe UI',sans-serif; background:var(--bg); color:var(--text); margin:0; padding:20px 16px 60px; }
  main { max-width: 980px; margin: 0 auto; }
  h1 { font-size:1.3rem; color:var(--accent); margin:0 0 4px; }
  .meta, .muted { color:var(--muted); font-size:.8rem; }
  nav { margin:14px 0 18px; display:flex; flex-wrap:wrap; gap:8px; }
  nav a { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 10px; font-size:.78rem; color:var(--text); text-decoration:none; }
  details.game { background:var(--card); border:1px solid var(--line); border-radius:10px; margin-bottom:12px; }
  details.game > summary { cursor:pointer; padding:12px 16px; font-weight:600; list-style:none; }
  .body { padding:0 16px 14px; }
  h3 { font-size:.72rem; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; margin:16px 0 6px; }
  table { border-collapse:collapse; width:100%; }
  th, td { padding:5px 8px; text-align:left; border-bottom:1px solid var(--line); font-size:.8rem; }
  th { color:var(--muted); font-size:.66rem; text-transform:uppercase; letter-spacing:.04em; }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .gap { color:var(--warn); font-size:.8rem; margin:2px 0; }
  .tag { display:inline-block; padding:1px 6px; border-radius:4px; font-size:.62rem; font-weight:700; margin-left:4px; }
  .tag.chalk { background:#3a2c12; color:#ffc94d; } .tag.lev { background:#11332a; color:#3ddc9b; } .tag.st { background:#3a1c1c; color:#fb7979; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; } @media (max-width:760px) { .grid2 { grid-template-columns:1fr; } }
  code { font-size:.72rem; color:var(--muted); word-break:break-all; }
</style>
"""


def _e(v: Any) -> str:
    return html.escape(str(v))


def _fmt(v: float | None, digits: int = 1, pct: bool = False) -> str:
    if v is None:
        return "&mdash;"
    return f"{v * 100:.{digits}f}%" if pct else f"{v:.{digits + 1 if abs(v) < 1 else digits}f}"


def _metric_row(name: str, mv: MetricValue) -> str:
    pct = name in _RATE_METRICS
    value = _fmt(mv.value, 1, pct=pct)
    lp = "&mdash;" if mv.league_percentile is None else f"{mv.league_percentile * 100:.0f}"
    n = "&mdash;" if mv.n is None else str(mv.n)
    return f"<tr><td>{_e(_LABELS.get(name, name))}</td><td class='num'>{value}</td><td class='num'>{n}</td><td class='num'>{lp}</td></tr>"


def _team_block(p: EvidencePacket, team: str) -> str:
    te = p.teams[team]
    head = (
        f"<b>{_e(team)}</b> vs {_e(te.opponent)} &middot; env score {_fmt(te.game_environment_score)} &middot; "
        f"stack viability {_fmt(te.single_team_viability)} &middot; script: {_e(te.game_script_stance or '—')} "
        f"({_fmt(te.game_script_intensity)}) &middot; implied {_fmt(te.implied_total)}"
    )
    rows = "".join(_metric_row(m, mv) for m, mv in te.metrics.items()) + "".join(_metric_row(m, mv) for m, mv in te.def_metrics.items())
    table = (
        "<table><tr><th>Metric (season to date)</th><th class='num'>Value</th><th class='num'>n</th><th class='num'>League pctile</th></tr>"
        f"{rows}</table>"
    ) if rows else "<div class='gap'>no proxy metrics for this team (see data gaps)</div>"
    return f"<div><div style='margin-bottom:6px'>{head}</div>{table}</div>"


def _player_rows(p: EvidencePacket) -> str:
    out = []
    for pl in p.players:
        tags = ""
        if pl.is_chalk:
            tags += "<span class='tag chalk'>CHALK</span>"
        if pl.is_leverage:
            tags += "<span class='tag lev'>LEVERAGE</span>"
        status = pl.status_after_q_pass or pl.injury_status
        if status:
            tags += f"<span class='tag st'>{_e(status)}</span>"
        out.append(
            f"<tr><td>{_e(pl.name)}{tags}</td><td>{_e(pl.team)}</td><td>{_e(pl.position)}</td>"
            f"<td class='num'>{'&mdash;' if pl.salary is None else f'${pl.salary:,}'}</td><td class='num'>{_fmt(pl.projection)}</td>"
            f"<td class='num'>{_fmt(pl.ownership_pct)}</td><td class='num'>{_fmt(pl.ownership_vs_baseline)}</td>"
            f"<td class='num'>{_fmt(pl.carry_share_trailing, 0, pct=True)}</td><td class='num'>{_fmt(pl.target_share_trailing, 0, pct=True)}</td>"
            f"<td class='num'>{_fmt(pl.ceiling_multiplier, 2)}</td><td>{_e(' '.join(x for x in (pl.circumstance_note, pl.opportunity_note) if x))}</td></tr>"
        )
    return "".join(out)


def _game_card(p: EvidencePacket) -> str:
    ln = p.lines
    total = "—" if ln.total is None else f"{ln.total:.1f}"
    summary = (
        f"{_e(p.away)} @ {_e(p.home)} &nbsp;<span class='muted'>{_e(ln.favorite)} by {ln.abs_spread:.1f} &middot; "
        f"total {total} &middot; fav wins {ln.favorite_win_probability * 100:.0f}% &middot; {_e(p.slate_window or '—')}</span>"
    )
    gaps = "".join(f"<div class='gap'>&#9888; {_e(g)}</div>" for g in p.data_gaps) or "<div class='muted'>none</div>"
    avail = "".join(
        f"<tr><td>{_e(a.name)}</td><td>{_e(a.team)}</td><td>{_e(a.decision)}</td><td>{_e(a.basis)}</td><td>{_e(a.source)}</td></tr>"
        for a in p.availability
    ) or "<tr><td colspan='5' class='muted'>none listed</td></tr>"
    vac = "".join(
        f"<tr><td>{_e(v.name)}</td><td>{_e(v.team)}</td><td>{_e(v.status)}</td><td class='num'>{v.carry_share:.1%}</td><td class='num'>{v.target_share:.1%}</td></tr>"
        for v in p.vacated
    ) or "<tr><td colspan='5' class='muted'>nobody with a meaningful role is out</td></tr>"
    weather = ", ".join(f"{_e(k)}: {_e(v)}" for k, v in (p.weather or {}).items()) or "no reading"
    keys = sorted(packet_keys(p))
    return (
        f"<details class='game' id='{_e(p.game_id)}'><summary>{summary}</summary><div class='body'>"
        f"<h3>Data gaps (what the analyst does NOT have)</h3>{gaps}"
        f"<h3>Teams</h3><div class='grid2'>{_team_block(p, p.away)}{_team_block(p, p.home)}</div>"
        f"<h3>Players listed (top by projection, plus DST)</h3>"
        "<table><tr><th>Player</th><th>Team</th><th>Pos</th><th class='num'>Salary</th><th class='num'>Proj</th><th class='num'>Own %</th>"
        "<th class='num'>Own vs baseline</th><th class='num'>RZ carry sh.</th><th class='num'>RZ target sh.</th><th class='num'>Ceil mult</th><th>Note</th></tr>"
        f"{_player_rows(p)}</table>"
        f"<h3>Roles vacated (share of the team's last-4-game carries / targets)</h3><table><tr><th>Player</th><th>Team</th><th>Status</th><th class='num'>Carries</th><th class='num'>Targets</th></tr>{vac}</table>"
        f"<h3>Availability</h3><table><tr><th>Player</th><th>Team</th><th>Decision</th><th>Basis</th><th>Source</th></tr>{avail}</table>"
        f"<h3>Weather</h3><div class='muted'>{weather}</div>"
        f"<h3>Citeable keys ({len(keys)})</h3><details><summary class='muted'>show</summary><code>{_e(' · '.join(keys))}</code></details>"
        f"<h3>Packet hash</h3><code>{_e(p.packet_sha)}</code>"
        "</div></details>"
    )


def render_evidence_page(packets: dict[str, EvidencePacket], *, title: str, generated_at: str = "") -> str:
    ordered = [packets[k] for k in sorted(packets)]
    nav = "".join(f"<a href='#{_e(p.game_id)}'>{_e(p.away)} @ {_e(p.home)}</a>" for p in ordered)
    cards = "".join(_game_card(p) for p in ordered) or "<p class='muted'>No games.</p>"
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(title)}</title>{_STYLE}</head><body><main>"
        f"<h1>{_e(title)}</h1><div class='meta'>{len(ordered)} game(s) &middot; {_e(generated_at)} &middot; "
        "evidence only &mdash; no opinions; analysts may cite only the keys listed under each game</div>"
        f"<nav>{nav}</nav>{cards}</main></body></html>"
    )
