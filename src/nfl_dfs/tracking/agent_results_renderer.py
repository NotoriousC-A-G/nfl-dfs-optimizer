"""Renders one week's `agent_results.csv` rows as a standalone "Agent Performance" table --
matching the real column layout confirmed live against the sister MLB project's own rendered
output (`postmortem_2026-09-19.html`'s "Agent Performance" panel: Agent / Strategy / DK Score /
Proj / Salary / Rank / Boom, `agent_id="operator"` sitting in the same table as the generated
agents, sorted best-DK-score-first).

**Deliberately just this one table, not MLB's full postmortem.** MLB's real report also carries
lock-journal reasoning, a chalk-proxy comparison, process grades, and ceiling-miss pattern
analysis -- built up over many iterations on top of years of settled slates. None of that exists
for NFL yet (this is the first week anything here gets scored at all), so this renderer is scoped
to the one piece `agent_results_store.py` can actually back: the performance table itself. A
richer report is a real, separate future piece, not attempted here.

**Honest about "not yet scored."** A row with `total_dk_score is None` (the normal state before a
week settles, or a row `agent_results_collector.py` couldn't fully resolve) renders as "--" in
every settled-data column rather than a fabricated 0 -- 0 is a real, different claim ("this lineup
scored zero DK points") that this renderer must never make on missing data's behalf.
"""

from __future__ import annotations

import html

from nfl_dfs.storage.agent_results_store import AgentResultRow

_STYLE = """
<style>
  body { font-family: -apple-system, 'Segoe UI', sans-serif; background: #0a0d13; color: #eef1f7; padding: 24px; }
  h1 { font-size: 1.2rem; color: #7c9bff; margin-bottom: 4px; }
  .meta { color: #aab4c8; font-size: 0.8rem; margin-bottom: 16px; }
  table { border-collapse: collapse; width: 100%; max-width: 900px; }
  th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #232a3d; font-size: 0.85rem; }
  th { color: #aab4c8; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.04em; }
  tr.operator { background: #202a4d; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .unscored-note { color: #ffc94d; font-size: 0.8rem; margin-top: 12px; }
</style>
""".strip()


def _fmt(value: float | int | None, *, decimals: int = 1) -> str:
    if value is None:
        return "--"
    return f"{value:,.{decimals}f}" if isinstance(value, float) else f"{value:,}"


def render_agent_performance_html(rows: list[AgentResultRow], *, season: int, week: int) -> str:
    """Renders the Agent Performance table for one (season, week). Rows with a real
    `total_dk_score` sort first (best score on top, per `lineup_rank` when present, else by
    `total_dk_score` directly); unscored rows follow, in their original order."""
    scored = sorted(
        (r for r in rows if r.total_dk_score is not None),
        key=lambda r: (r.lineup_rank if r.lineup_rank is not None else 10**9, -r.total_dk_score),
    )
    unscored = [r for r in rows if r.total_dk_score is None]
    ordered = scored + unscored

    row_html = []
    for r in ordered:
        css_class = ' class="operator"' if r.agent_id == "operator" else ""
        row_html.append(
            f"<tr{css_class}>"
            f"<td>{html.escape(r.agent_id)}</td>"
            f"<td>{html.escape(r.strategy_name)}</td>"
            f"<td class=\"num\">{_fmt(r.total_dk_score)}</td>"
            f"<td class=\"num\">{_fmt(r.proj_total)}</td>"
            f"<td class=\"num\">${_fmt(r.salary, decimals=0)}</td>"
            f"<td class=\"num\">{'#' + _fmt(r.lineup_rank, decimals=0) if r.lineup_rank is not None else '--'}</td>"
            f"<td class=\"num\">{'--' if r.boom is None else ('Yes' if r.boom else 'No')}</td>"
            f"</tr>"
        )

    unscored_note = (
        f'<p class="unscored-note">{len(unscored)} of {len(rows)} lineup(s) not yet scored '
        f"(games not settled, or a name-match miss -- see the collector run's own output).</p>"
        if unscored
        else ""
    )

    return f"""<!doctype html>
<html>
<head><meta charset="utf-8">{_STYLE}</head>
<body>
<h1>Agent Performance -- Season {season}, Week {week}</h1>
<p class="meta">{len(rows)} lineup(s) logged -- {len(scored)} scored, {len(unscored)} pending.</p>
<table>
<thead><tr><th>Agent</th><th>Strategy</th><th>DK Score</th><th>Proj</th><th>Salary</th><th>Rank</th><th>Boom</th></tr></thead>
<tbody>
{"".join(row_html)}
</tbody>
</table>
{unscored_note}
</body>
</html>
"""
