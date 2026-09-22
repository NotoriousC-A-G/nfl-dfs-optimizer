"""One-off week 2 postmortem reconstruction, built from `dashboard_output/weekly_dashboard.html`
(still on disk from the live run) instead of a real slate snapshot -- no snapshot exists for week
2 (the persistence-layer commit landed after that run). Chris: "we have the lineups - why do we
need to pull the information from the vendors?" -- correct: the dashboard's own rendered text
already carries per-player salary/projection (the "Player Detail" table) and each agent's real
construction rationale (stack thesis, dup-risk read); this script parses that instead of
re-hitting DK/RotoGrinders, which can't serve a locked historical slate anyway.

NOT a reusable pipeline piece -- the regexes here are tuned to this one HTML file's exact text
layout and will not survive a renderer change. From week 3 on, `tracking/postmortem/replay.py`
running against a real `slate_snapshot_store` snapshot is the real path; this script exists only
because week 2 predates that snapshot.

Produces `dashboard_output/postmortem_week2_full.html`: per lineup (6 agents + Chris's L1/L2/L3),
the real construction rationale (agents only) plus a full 9-player table (salary, real projected,
real settled actual, delta) -- not just the lineup-level totals `agent_performance.html` shows.
Also a "Missed Players" section: real high scorers from the reconciled pool that no lineup, agent
or operator, rostered.
"""

from __future__ import annotations

import re
import warnings

import nfl_data_py as nfl

from nfl_dfs.ingestion.dst_actual_scoring import aggregate_team_week_dst_points
from nfl_dfs.ingestion.offense_actual_scoring import fetch_weekly_player_stats, settled_offensive_points_by_player
from nfl_dfs.normalization.team_aliases import normalize_team
from nfl_dfs.storage.agent_results_store import read_agent_results
from nfl_dfs.storage.contest_results_store import read_contest_results
from nfl_dfs.tracking.name_matching import normalize_player_name, parse_player_token
from nfl_dfs.tracking.season_record import compute_season_records

SEASON = 2026
WEEK = 2
HTML_PATH = "dashboard_output/weekly_dashboard.html"
OUTPUT_PATH = "dashboard_output/postmortem_week2_full.html"

AGENTS = [
    "Chalk Anchor",
    "Game Script Architect",
    "Matchup Purist",
    "Arbitrageur",
    "Explosion/Shootout",
    "Volatility Engine",
]

# Real agent_id -> display_name, from agents/registry.py -- NOT re-derived via
# `agent_id.replace("_", " ").title()` (that turns "explosion_shootout" into "Explosion
# Shootout", losing the "/", which then fails to match the "Explosion/Shootout" rationale key
# and silently drops that lineup's rationale -- caught live, fixed here).
AGENT_DISPLAY_NAME = {
    "chalk_anchor": "Chalk Anchor",
    "game_script_architect": "Game Script Architect",
    "matchup_purist": "Matchup Purist",
    "arbitrageur": "Arbitrageur",
    "explosion_shootout": "Explosion/Shootout",
    "volatility_engine": "Volatility Engine",
}

_NAME = r"([A-Z][A-Za-z.\x27\-]*(?:\s[A-Z][A-Za-z.\x27\-]*){0,3})"
_REST = r"\s+(?:In [^<]{0,200}?)?\s*(QB|RB|WR|TE|DST)\s+([A-Z]{2,3})\s+([A-Z]{2,3})\s+\$([\d,]+)\s+([\d.]+|N/A)"
_PAT_INJURY = re.compile(r"Injury\s+[A-Za-z]+\s+" + _NAME + _REST)
_PAT_TS = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+\+00:00\s+" + _NAME + _REST)


def _load_text() -> str:
    html = open(HTML_PATH).read()
    text = re.sub("<[^>]+>", " ", html)
    text = text.replace("&#x27;", "'").replace("&mdash;", "--").replace("&quot;", '"').replace("&middot;", "·")
    return text


def parse_player_detail_table(text: str) -> dict[tuple[str, str], dict]:
    """Real per-player salary/projection, keyed by (normalized name, team). Two anchor patterns
    are needed: rows for a player rostered by >=1 agent carry a "synthesized by..." timestamp
    prefix; every other row is anchored by the previous row's own "Injury <status>" field
    instead (confirmed live -- neither anchor alone covers the whole table)."""
    by_key: dict[tuple[str, str], dict] = {}
    for pat in (_PAT_INJURY, _PAT_TS):
        for name, pos, team, opp, sal, proj in pat.findall(text):
            key = (normalize_player_name(name), team)
            by_key[key] = {
                "display_name": name.strip(),
                "position": pos,
                "team": team,
                "salary": int(sal.replace(",", "")),
                "projected": None if proj == "N/A" else float(proj),
            }
    return by_key


_LINEUP_CARD_HEADER = re.compile(r"{name}\s+\$[\d,]+\s+salary")


def parse_agent_rationales(text: str) -> dict[str, str]:
    """Bug fixed 2026-09-22: the original boundary search used a literal single space before
    "$" (`f"{next_agent} $"`), but the real text has TWO spaces there (confirmed live) -- `str.
    find` silently returned -1 (not found), and `text[start:-1]` then sliced almost the entire
    rest of the document into "Chalk Anchor"'s rationale (every other agent's card, the exposure
    report, the page footer). Fixed by matching the lineup-card-header pattern with a regex
    (`\s+` instead of a literal single space) instead of an exact substring."""
    rationales = {}
    for i, agent in enumerate(AGENTS):
        marker = f"Rationale  {agent}:"
        start = text.find(marker)
        if start < 0:
            continue
        start += len("Rationale  ")
        next_agent = AGENTS[i + 1] if i + 1 < len(AGENTS) else None
        if next_agent:
            m = _LINEUP_CARD_HEADER.pattern.format(name=re.escape(next_agent))
            end_match = re.search(m, text[start:])
            end = start + end_match.start() if end_match else len(text)
        else:
            # Last agent (Volatility Engine): its rationale ends where the next real section
            # starts -- confirmed live the exposure report's own header text is "lineup(s) in
            # this set." (preceded by the lineup count), not a fixed string worth hardcoding
            # further than this.
            m = re.search(r"\d+ lineup\(s\) in this set\.", text[start:])
            end = start + m.start() if m else len(text)
        rationales[agent] = re.sub(r"\s+", " ", text[start:end]).strip()
    return rationales


def parse_dup_risk(text: str) -> dict[str, str]:
    dup_risk = {}
    for i, agent in enumerate(AGENTS):
        marker = f"Rationale  {agent}:"
        idx = text.find(marker)
        if idx < 0:
            continue
        window = text[max(0, idx - 400) : idx]
        m = re.search(r"Dup Risk\s+([\d.]+% historical field-duplication rate[^)]*\)[^-]*-- 2024-2025 field data, ADR-0033)?", window)
        if m:
            dup_risk[agent] = m.group(0).replace("Dup Risk", "").strip()
    return dup_risk


_CORE_STACK_RE = re.compile(r":\s*(.+?)\s*\([A-Z]{2,3} core stack")


def compute_player_exposure(lineup_sections: list[dict]) -> list[dict]:
    """Every distinct (name, team) rostered across all lineups this week, with how many lineups
    carried them and their real projected/actual/delta -- the direct answer to "which real
    decision dragged us down or carried us, and how exposed were we to it." A player in 9/9
    lineups who busted did more damage than one bust in a single lineup; this is the one view
    that actually shows that."""
    by_key: dict[tuple[str, str], dict] = {}
    for s in lineup_sections:
        for p in s["players"]:
            key = (p["name"], p["team"])
            if key not in by_key:
                by_key[key] = {**p, "count": 0, "lineups": []}
            by_key[key]["count"] += 1
            by_key[key]["lineups"].append(s["label"])
    exposure = list(by_key.values())
    exposure.sort(key=lambda p: (-p["count"], p["actual"] if p["actual"] is not None else 0))
    return exposure


def compute_positional_delta(exposure: list[dict]) -> dict[str, dict]:
    """Average projected/actual/delta BY DISTINCT PLAYER (one vote each, regardless of how many
    lineups rostered them) -- a check on whether the projection system itself was biased by
    position this week, separate from `compute_player_exposure`'s "which real lineup decision hurt
    us" view (which deliberately does NOT dedupe, since 9x exposure to one bust is 9x the real
    damage)."""
    by_pos: dict[str, list[dict]] = {}
    for p in exposure:
        if p["projected"] is None or p["actual"] is None:
            continue
        by_pos.setdefault(p["position"], []).append(p)
    result = {}
    for pos, players in by_pos.items():
        deltas = [p["actual"] - p["projected"] for p in players]
        result[pos] = {
            "n": len(players),
            "avg_projected": sum(p["projected"] for p in players) / len(players),
            "avg_actual": sum(p["actual"] for p in players) / len(players),
            "avg_delta": sum(deltas) / len(deltas),
        }
    return result


def compute_stack_thesis_review(lineup_sections: list[dict]) -> list[dict]:
    """For each agent lineup's own named "core stack" (parsed straight from its real rationale
    text, e.g. "Trevor Lawrence + Parker Washington"), did those SPECIFIC named players actually
    deliver -- the direct, real answer to "did the thesis this agent bet on pay off," not just
    "did the whole lineup do well" (a lineup can win despite its stack, or lose despite a stack
    that hit, if the rest of the roster swings the other way)."""
    reviews = []
    for s in lineup_sections:
        if not s["rationale"]:
            continue
        m = _CORE_STACK_RE.search(s["rationale"])
        if not m:
            continue
        names = [n.strip() for n in m.group(1).split(" + ")]
        by_name = {p["name"]: p for p in s["players"]}
        stack_players = [by_name[n] for n in names if n in by_name]
        if not stack_players:
            continue
        proj = sum(p["projected"] for p in stack_players if p["projected"] is not None)
        actual = sum(p["actual"] for p in stack_players if p["actual"] is not None)
        reviews.append(
            {
                "label": s["label"],
                "stack_names": names,
                "projected": proj,
                "actual": actual,
                "delta": actual - proj,
                "hit": actual > proj,
            }
        )
    return reviews


def main() -> None:
    text = _load_text()
    player_detail = parse_player_detail_table(text)
    rationales = parse_agent_rationales(text)
    dup_risk = parse_dup_risk(text)
    contest_results = read_contest_results(season=SEASON, week=WEEK)
    print(
        f"Parsed {len(player_detail)} player-detail rows, {len(rationales)} rationales, "
        f"{len(dup_risk)} dup-risk reads, {len(contest_results)} real contest entries."
    )

    print("Fetching real settled data...")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        weekly = fetch_weekly_player_stats(SEASON)
        pbp = nfl.import_pbp_data([SEASON], include_participation=False)
    offensive_points = settled_offensive_points_by_player(weekly, SEASON, WEEK)
    dst_points = {
        (normalize_team("nflverse_schedule", r.team) or r.team): r.dk_points
        for r in aggregate_team_week_dst_points(pbp)
        if r.week == WEEK
    }

    rows = read_agent_results(season=SEASON, week=WEEK)
    rostered_keys: set[tuple[str, str]] = set()
    lineup_sections = []
    for row in rows:
        players = []
        for token in row.players:
            name, position, team = parse_player_token(token)
            norm_name = normalize_player_name(name)
            rostered_keys.add((norm_name, team))
            detail = player_detail.get((norm_name, team))
            salary = detail["salary"] if detail else None
            projected = detail["projected"] if detail else None
            actual = dst_points.get(team) if position == "DST" else offensive_points.get((norm_name, team))
            players.append(
                {
                    "name": name,
                    "position": position,
                    "team": team,
                    "salary": salary,
                    "projected": projected,
                    "actual": actual,
                }
            )
        label = AGENT_DISPLAY_NAME.get(row.agent_id, row.agent_id) if row.agent_id != "operator" else row.strategy_name
        lineup_sections.append(
            {
                "label": label,
                "agent_id": row.agent_id,
                "players": players,
                "total_dk_score": row.total_dk_score,
                "proj_total": row.proj_total,
                "rationale": rationales.get(label),
                "dup_risk": dup_risk.get(label),
            }
        )

    # Missed players: real pool entries with a resolvable real actual score, not on any lineup.
    missed = []
    for (norm_name, team), detail in player_detail.items():
        if (norm_name, team) in rostered_keys:
            continue
        actual = offensive_points.get((norm_name, team))
        if actual is not None and actual >= 12.0:
            missed.append({**detail, "actual": actual})
    missed.sort(key=lambda p: -p["actual"])

    exposure = compute_player_exposure(lineup_sections)
    positional = compute_positional_delta(exposure)
    stack_review = compute_stack_thesis_review(lineup_sections)
    season_records = compute_season_records(SEASON)
    print(
        f"Analysis: {len(exposure)} distinct players rostered, {len(positional)} positions, "
        f"{len(stack_review)} stack theses reviewed, {len(season_records)} season records."
    )

    html = render(lineup_sections, missed, contest_results, exposure, positional, stack_review, season_records)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH}")


def _fmt(v, decimals=1):
    return "--" if v is None else f"{v:,.{decimals}f}"


# Design system: colors/spacing/class names taken directly from the sister MLB project's own real
# rendered postmortem_2026-09-19.html (viewed live via the browser tool, not guessed at) -- same
# dark palette, stat-card/hero-card/lu-card/badge components. NFL content only; no MLB text/numbers.
_STYLE = """
<style>
  :root {
    --bg: #0a0d13; --bg2: #161c29; --bg3: #232a3d;
    --fg: #eef1f7; --fg2: #aab4c8; --fg3: #868fa8;
    --accent: #7c9bff; --accent-light: #202a4d;
    --green: #3ddc9b; --green-bg: #11332a;
    --red: #fb7979; --red-bg: #3a1c1c;
    --amber: #ffc94d; --amber-bg: #3a2c12;
    --border: #454f72; --radius: 10px; --radius-sm: 6px;
    --shadow: 0 1px 3px rgba(0,0,0,0.45);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, 'Inter', 'Segoe UI', sans-serif; background: var(--bg); color: var(--fg); padding: 20px; font-size: 13px; }
  .header { text-align: center; padding: 8px 0 20px; }
  .header h1 { font-size: 1.4rem; color: var(--accent); font-weight: 700; }
  .header .meta { color: var(--fg2); font-size: 0.8rem; margin-top: 4px; }
  .wrap { max-width: 1040px; margin: 0 auto; }
  .section-label { font-size: 0.75rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.04em; font-weight: 700; margin: 24px 0 10px; display: flex; align-items: center; gap: 8px; }
  .section-label::after { content: ''; flex: 1; height: 1px; background: var(--border); }
  .hero-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 8px; }
  .hero-card { background: var(--bg2); border: 1px solid var(--border); border-left: 6px solid var(--border); border-radius: var(--radius); padding: 14px 16px; box-shadow: var(--shadow); }
  .hero-card.good { border-left-color: var(--green); }
  .hero-card.bad { border-left-color: var(--red); }
  .hero-tag { font-size: 0.65rem; font-weight: 700; color: var(--fg3); letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 8px; }
  .hero-big { font-size: 1.7rem; font-weight: 800; margin: 2px 0 4px; }
  .hero-card.good .hero-big { color: var(--green); }
  .hero-card.bad .hero-big { color: var(--red); }
  .hero-sub { font-size: 0.75rem; color: var(--fg2); }
  .card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; box-shadow: var(--shadow); margin-bottom: 14px; }
  .card h4 { font-size: 0.75rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 10px; }
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
  .stat-card { background: var(--bg3); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px 12px; text-align: center; }
  .stat-val { font-size: 1.3rem; font-weight: 700; color: var(--accent); }
  .stat-val.green { color: var(--green); } .stat-val.red { color: var(--red); }
  .stat-label { font-size: 0.62rem; color: var(--fg3); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
  th { padding: 7px 8px; text-align: left; font-weight: 600; font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.03em; background: var(--bg3); color: var(--accent); border-bottom: 2px solid var(--border); }
  td { padding: 6px 8px; border-bottom: 1px solid var(--border); }
  tr:hover { background: var(--bg3); }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .pos { text-align: center; }
  .green { color: var(--green); font-weight: 600; } .red { color: var(--red); font-weight: 600; } .muted { color: var(--fg3); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.65rem; font-weight: 700; }
  .badge-cash { background: var(--green-bg); color: var(--green); }
  .badge-miss { background: var(--red-bg); color: var(--red); }
  .lu-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow); margin-bottom: 16px; }
  .lu-card.operator { border-color: var(--accent); }
  .lu-header { background: linear-gradient(135deg, var(--accent-light), var(--bg2)); padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .lu-header h3 { font-size: 1rem; color: var(--accent); margin-bottom: 8px; }
  .lu-stats { display: flex; gap: 20px; }
  .lu-stat { text-align: left; }
  .lu-stat .v { font-size: 1.1rem; font-weight: 700; }
  .lu-stat .l { font-size: 0.62rem; color: var(--fg3); text-transform: uppercase; letter-spacing: 0.04em; }
  .rationale { padding: 12px 16px; font-size: 0.8rem; color: var(--fg2); border-bottom: 1px solid var(--border); font-style: italic; }
  .rationale b { color: var(--fg); font-style: normal; }
  .dup-risk { padding: 8px 16px; font-size: 0.72rem; color: var(--amber); border-bottom: 1px solid var(--border); }
  .lu-body { padding: 4px 16px 12px; }
  .contest-summary { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
</style>
"""


def _lineup_hero(lineup_sections):
    scored = [s for s in lineup_sections if s["total_dk_score"] is not None]
    best = max(scored, key=lambda s: s["total_dk_score"])
    worst = min(scored, key=lambda s: s["total_dk_score"])
    return best, worst


def render(lineup_sections, missed, contest_results, exposure, positional, stack_review, season_records) -> str:
    best, worst = _lineup_hero(lineup_sections)
    total_entries = len(contest_results)
    cashed = sum(1 for c in contest_results if c.cashed)

    hero_html = f"""
    <div class="hero-cards">
      <div class="hero-card good"><div class="hero-tag">Best Lineup</div>
        <div class="hero-big">{best['label']}</div><div class="hero-sub">{_fmt(best['total_dk_score'])} pts actual (proj {_fmt(best['proj_total'])})</div></div>
      <div class="hero-card bad"><div class="hero-tag">Worst Lineup</div>
        <div class="hero-big">{worst['label']}</div><div class="hero-sub">{_fmt(worst['total_dk_score'])} pts actual (proj {_fmt(worst['proj_total'])})</div></div>
      <div class="hero-card {'bad' if cashed == 0 else 'good'}"><div class="hero-tag">Contest Record</div>
        <div class="hero-big">{cashed}/{total_entries}</div><div class="hero-sub">real DK entries cashed, week {WEEK}</div></div>
    </div>
    """

    sections_html = []
    for s in lineup_sections:
        rows_html = []
        for p in s["players"]:
            delta = None if p["actual"] is None or p["projected"] is None else p["actual"] - p["projected"]
            delta_cls = "green" if (delta or 0) > 0 else ("red" if (delta or 0) < 0 else "muted")
            rows_html.append(
                f"<tr><td class='pos'>{p['position']}</td><td>{p['name']}</td><td>{p['team']}</td>"
                f"<td class='num'>${_fmt(p['salary'], 0)}</td><td class='num'>{_fmt(p['projected'])}</td>"
                f"<td class='num'>{_fmt(p['actual'])}</td>"
                f"<td class='num {delta_cls}'>{'--' if delta is None else f'{delta:+.1f}'}</td></tr>"
            )
        rationale_html = f'<div class="rationale">{s["rationale"]}</div>' if s["rationale"] else ""
        dup_risk_html = f'<div class="dup-risk">&#9888; Dup Risk: {s["dup_risk"]}</div>' if s["dup_risk"] else ""

        my_contests = [c for c in contest_results if c.lineup_label == s["label"]]
        contest_badges = "".join(
            f'<span class="badge {"badge-cash" if c.cashed else "badge-miss"}" title="{c.contest_name}">'
            f'{"CASH" if c.cashed else "miss"} · {c.entries:,}-entry</span>'
            for c in my_contests
        )
        contest_html = f'<div class="lu-body"><div class="contest-summary">{contest_badges}</div></div>' if my_contests else ""

        delta_total = None if s["total_dk_score"] is None else s["total_dk_score"] - s["proj_total"]
        delta_total_cls = "green" if (delta_total or 0) > 0 else "red"
        css = "lu-card operator" if s["agent_id"] == "operator" else "lu-card"
        sections_html.append(
            f'<div class="{css}"><div class="lu-header"><h3>{s["label"]}</h3>'
            '<div class="lu-stats">'
            f'<div class="lu-stat"><div class="v">{_fmt(s["total_dk_score"])}</div><div class="l">Actual</div></div>'
            f'<div class="lu-stat"><div class="v">{_fmt(s["proj_total"])}</div><div class="l">Proj</div></div>'
            f'<div class="lu-stat"><div class="v {delta_total_cls}">{"--" if delta_total is None else f"{delta_total:+.1f}"}</div><div class="l">Delta</div></div>'
            "</div></div>"
            f"{rationale_html}{dup_risk_html}{contest_html}"
            '<div class="lu-body"><table><thead><tr><th>Pos</th><th>Player</th><th>Team</th><th>Salary</th><th>Proj</th><th>Actual</th><th>Delta</th></tr></thead>'
            f"<tbody>{''.join(rows_html)}</tbody></table></div></div>"
        )

    missed_rows = "".join(
        f"<tr><td class='pos'>{p['position']}</td><td>{p['display_name']}</td><td>{p['team']}</td>"
        f"<td class='num'>${_fmt(p['salary'], 0)}</td><td class='num'>{_fmt(p['projected'])}</td>"
        f"<td class='num green'>{_fmt(p['actual'])}</td></tr>"
        for p in missed[:20]
    )

    # Season records (Chris: "we should build the agent and operator records," then "show L1/L2/L3
    # individually," then "win... more like they would have cashed") -- one row per tracked entity
    # (L1/L2/L3 individually + the 6 agents), "win" = weeks with >=1 real-or-estimated cash.
    def _season_row(r):
        label = AGENT_DISPLAY_NAME.get(r.agent_id, r.agent_id) if not r.is_operator_lineup else r.agent_id
        basis_badge = (
            '<span class="badge badge-cash" title="Real DK contest entries">real</span>'
            if r.basis == "real"
            else '<span class="badge badge-miss" title="Interpolated against the real contests you entered">est.</span>'
        )
        cash_record = f"{r.contest_cashes}/{r.contest_entries}" if r.contest_entries else "--"
        return (
            f"<tr><td>{label} {basis_badge}</td>"
            f"<td class='num'>{r.wins}-{r.weeks_tracked - r.wins}</td>"
            f"<td class='num'>{cash_record}</td>"
            f"<td class='num {'green' if (r.avg_delta or 0) > 0 else 'red'}'>{'' if r.avg_delta is None else f'{r.avg_delta:+.1f}'}</td>"
            f"<td class='num'>{_fmt(r.best_week[1]) if r.best_week else '--'}"
            f"{f' (wk{r.best_week[0]})' if r.best_week else ''}</td></tr>"
        )

    season_rows = "".join(_season_row(r) for r in season_records)

    # Analysis: exposure (real decision impact, NOT deduped), positional bias (deduped, one vote
    # per distinct player), and each agent's own named stack thesis vs what it actually delivered.
    def _exposure_row(p):
        d = None if p["actual"] is None or p["projected"] is None else p["actual"] - p["projected"]
        cls = "green" if (d or 0) > 0 else "red"
        return (
            f"<tr><td class='pos'>{p['position']}</td><td>{p['name']}</td><td>{p['team']}</td>"
            f"<td class='num'>{p['count']}/9</td>"
            f"<td class='num'>{_fmt(p['projected'])}</td><td class='num'>{_fmt(p['actual'])}</td>"
            f"<td class='num {cls}'>{'--' if d is None else f'{d:+.1f}'}</td></tr>"
        )

    exposure_rows = "".join(
        _exposure_row(p) for p in exposure if p["count"] >= 3
        # meaningfully-exposed players only -- a 1/9 bust isn't a "what went wrong" finding
    )

    positional_rows = "".join(
        f"<tr><td class='pos'>{pos}</td><td class='num'>{d['n']}</td>"
        f"<td class='num'>{_fmt(d['avg_projected'])}</td><td class='num'>{_fmt(d['avg_actual'])}</td>"
        f"<td class='num {'green' if d['avg_delta'] > 0 else 'red'}'>{d['avg_delta']:+.1f}</td></tr>"
        for pos, d in sorted(positional.items(), key=lambda kv: kv[1]["avg_delta"])
    )

    stack_rows = "".join(
        f"<tr><td>{r['label']}</td><td>{' + '.join(r['stack_names'])}</td>"
        f"<td class='num'>{_fmt(r['projected'])}</td><td class='num'>{_fmt(r['actual'])}</td>"
        f"<td class='num {'green' if r['hit'] else 'red'}'>{r['delta']:+.1f}</td>"
        f"<td><span class='badge {'badge-cash' if r['hit'] else 'badge-miss'}'>{'HIT' if r['hit'] else 'MISS'}</span></td></tr>"
        for r in stack_review
    )

    by_lineup_contest_rows = "".join(
        f"<tr><td>{c.lineup_label}</td><td>{c.contest_name}</td><td class='num'>{c.entries:,}</td>"
        f"<td class='num'>{c.rank:,}</td><td class='num'>{c.rank / c.entries * 100:.1f}%</td>"
        f"<td class='num'>{_fmt(c.fpts)}</td>"
        f"<td><span class='badge {'badge-cash' if c.cashed else 'badge-miss'}'>{'CASHED' if c.cashed else 'missed'}</span></td></tr>"
        for c in sorted(contest_results, key=lambda c: c.rank / c.entries)
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8">{_STYLE}</head><body><div class="wrap">
<div class="header"><h1>Week {WEEK} Postmortem</h1>
<div class="meta">Season {SEASON} &middot; reconstructed from weekly_dashboard.html (no slate snapshot exists for week {WEEK})</div></div>
{hero_html}
<div class="section-label">Season Record (L1/L2/L3 individually + the 6 agents, every week logged so far)</div>
<div class="card"><table><thead><tr><th>Lineup / Agent</th><th>W-L (would-cash)</th><th>Cashed</th><th>Avg Delta</th><th>Best Week</th></tr></thead>
<tbody>{season_rows}</tbody></table>
<p style="font-size:0.72rem;color:var(--fg3);margin-top:8px;">"real" = your own DK contest entries. "est." = the agent was never actually entered -- cash outcome is interpolated against the SAME real contests you played that week (see contest_placement_estimate.py). Zero anchors some weeks (single-entry-only contests) means 0 entries shown, not a real 0% cash rate.</p>
</div>
<div class="section-label">Lineups</div>
{''.join(sections_html)}
<div class="section-label">Real Contest Results ({total_entries} entries, {cashed} cashed)</div>
<div class="card"><table><thead><tr><th>Lineup</th><th>Contest</th><th>Entries</th><th>Rank</th><th>Percentile</th><th>FPTS</th><th>Result</th></tr></thead>
<tbody>{by_lineup_contest_rows}</tbody></table></div>
<div class="section-label">What Went Right or Wrong -- Player Exposure (players in 3+ of our 9 lineups)</div>
<div class="card"><table><thead><tr><th>Pos</th><th>Player</th><th>Team</th><th>Exposure</th><th>Proj</th><th>Actual</th><th>Delta</th></tr></thead>
<tbody>{exposure_rows}</tbody></table></div>
<div class="section-label">What Went Right or Wrong -- Positional Bias (avg delta, one vote per distinct player)</div>
<div class="card"><table><thead><tr><th>Pos</th><th># Players</th><th>Avg Proj</th><th>Avg Actual</th><th>Avg Delta</th></tr></thead>
<tbody>{positional_rows}</tbody></table></div>
<div class="section-label">What Went Right or Wrong -- Did Each Agent's Named Stack Thesis Hit?</div>
<div class="card"><table><thead><tr><th>Agent</th><th>Core Stack</th><th>Proj</th><th>Actual</th><th>Delta</th><th>Result</th></tr></thead>
<tbody>{stack_rows}</tbody></table></div>
<div class="section-label">Missed Players (real scorers, not on any of our 9 lineups)</div>
<div class="card"><table><thead><tr><th>Pos</th><th>Player</th><th>Team</th><th>Salary</th><th>Proj</th><th>Actual</th></tr></thead>
<tbody>{missed_rows}</tbody></table></div>
</div></body></html>
"""


if __name__ == "__main__":
    main()
