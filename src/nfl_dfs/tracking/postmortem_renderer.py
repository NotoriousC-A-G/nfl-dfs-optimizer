"""Renders one week's `PostMortemReport` as a standalone HTML page -- lineup outcomes (agents +
operator, sorted best-actual-first), the chalk-proxy comparison, the process grade, and the
ceiling-pattern card. Scoped to what `tracking/postmortem/replay.py` can actually compute today;
not a port of MLB's full 3,559-line renderer (calibration surfaces, agent-evolution trend strips,
cash-line history -- all built on months of settled MLB slates this project doesn't have yet).
"""

from __future__ import annotations

import html

from nfl_dfs.tracking.postmortem.models import LineupOutcome, PostMortemReport

_STYLE = """
<style>
  body { font-family: -apple-system, 'Segoe UI', sans-serif; background: #0a0d13; color: #eef1f7; padding: 24px; }
  h1 { font-size: 1.3rem; color: #7c9bff; margin-bottom: 4px; }
  h2 { font-size: 0.95rem; color: #aab4c8; text-transform: uppercase; letter-spacing: 0.04em; margin: 24px 0 10px; }
  .meta { color: #aab4c8; font-size: 0.8rem; margin-bottom: 8px; }
  .card { background: #161c29; border: 1px solid #232a3d; border-radius: 10px; padding: 16px; margin-bottom: 14px; max-width: 900px; }
  table { border-collapse: collapse; width: 100%; max-width: 900px; }
  th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid #232a3d; font-size: 0.82rem; }
  th { color: #aab4c8; text-transform: uppercase; font-size: 0.68rem; letter-spacing: 0.04em; }
  tr.operator { background: #202a4d; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .grade { font-size: 2rem; font-weight: 700; color: #7c9bff; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.72rem; margin: 2px; }
  .pill.hit { background: #11332a; color: #3ddc9b; }
  .pill.miss { background: #3a1c1c; color: #fb7979; }
  .note { color: #ffc94d; font-size: 0.8rem; }
</style>
""".strip()


def _fmt(value, decimals=1) -> str:
    return "--" if value is None else f"{value:,.{decimals}f}"


def _render_lineup_table(lineup_outcomes: tuple[LineupOutcome, ...]) -> str:
    scored = sorted((lo for lo in lineup_outcomes if lo.actual_total is not None), key=lambda lo: -lo.actual_total)
    unscored = [lo for lo in lineup_outcomes if lo.actual_total is None]
    rows = []
    for lo in scored + unscored:
        css = ' class="operator"' if lo.agent_id == "operator" else ""
        rows.append(
            f"<tr{css}><td>{html.escape(lo.label)}</td>"
            f"<td class=\"num\">{_fmt(lo.actual_total)}</td>"
            f"<td class=\"num\">{_fmt(lo.projected_total)}</td>"
            f"<td class=\"num\">{_fmt(lo.delta, decimals=1) if lo.delta is not None else '--'}</td></tr>"
        )
    note = (
        f'<p class="note">{len(unscored)} lineup(s) not fully scored yet '
        f"(games not settled, or a name-match miss).</p>"
        if unscored
        else ""
    )
    return (
        "<h2>Lineup Outcomes</h2><div class=\"card\"><table>"
        "<thead><tr><th>Lineup</th><th>Actual</th><th>Proj</th><th>Delta</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>{note}</div>"
    )


def _render_process_grade(report: PostMortemReport) -> str:
    grade = report.process_grade
    if grade is None or grade.letter == "N/A":
        summary = grade.summary if grade else "Not computed."
        return f'<h2>Process Grade</h2><div class="card"><p class="note">N/A -- {html.escape(summary)}</p></div>'
    hits = "".join(f'<span class="pill hit">{html.escape(s)}</span>' for s in grade.signal_names_hit)
    misses = "".join(f'<span class="pill miss">{html.escape(s)}</span>' for s in grade.signal_names_missed)
    return (
        "<h2>Process Grade</h2><div class=\"card\">"
        f'<span class="grade">{html.escape(grade.letter)}</span> '
        f"<span>{grade.score:.0f}/100 -- {html.escape(grade.summary)}</span>"
        f"<div>{hits}{misses}</div></div>"
    )


def _render_chalk_comparison(report: PostMortemReport) -> str:
    chalk = report.chalk_comparison
    if chalk is None:
        return ""
    if chalk.infeasible:
        return f'<h2>Chalk Comparison</h2><div class="card"><p class="note">Not available -- {html.escape(chalk.reason)}</p></div>'
    body = f"Chalk actual: {_fmt(chalk.chalk_actual)} pts. Our best: {_fmt(chalk.our_actual)} pts."
    if chalk.delta is not None:
        verdict = "beat" if chalk.beat_chalk else "trailed"
        body += f" We {verdict} chalk by {abs(chalk.delta):.1f} pts."
    diffs = "".join(
        f"<li>{html.escape(ours)} vs {html.escape(theirs)}: {d:+.1f}</li>" for ours, theirs, d in chalk.differentiators
    )
    diffs_html = f"<ul>{diffs}</ul>" if diffs else ""
    return f'<h2>Chalk Comparison</h2><div class="card"><p>{body}</p>{diffs_html}</div>'


def _render_ceiling_patterns(report: PostMortemReport) -> str:
    patterns = report.ceiling_patterns
    if patterns is None or patterns.missed_count == 0:
        return '<h2>Ceiling Patterns</h2><div class="card"><p>No high scorers missed this week.</p></div>'
    highlights = "".join(f"<li>{html.escape(h)}</li>" for h in patterns.highlights)
    return (
        "<h2>Ceiling Patterns</h2><div class=\"card\">"
        f"<p>{patterns.missed_count} missed. {html.escape(patterns.salary_bucket_summary)}. "
        f"{html.escape(patterns.position_theme)}.</p><ul>{highlights}</ul></div>"
    )


def render_postmortem_html(report: PostMortemReport) -> str:
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8">{_STYLE}</head>
<body>
<h1>Postmortem -- Season {report.season}, Week {report.week}</h1>
<p class="meta">{len(report.lineup_outcomes)} lineup(s) tracked.</p>
{_render_lineup_table(report.lineup_outcomes)}
{_render_process_grade(report)}
{_render_chalk_comparison(report)}
{_render_ceiling_patterns(report)}
</body>
</html>
"""
