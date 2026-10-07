"""Renders one week's `PostMortemReport` as a standalone HTML page -- lineup outcomes (agents +
operator, sorted best-actual-first), the chalk-proxy comparison, the process grade, the
ceiling-pattern card, player exposure, positional bias, and each agent's stack-thesis-hit review.
Scoped to what `tracking/postmortem/replay.py` + `tracking/postmortem/exposure.py` can actually
compute today; not a port of MLB's full 3,559-line renderer (calibration surfaces, agent-evolution
trend strips, cash-line history -- all built on months of settled MLB slates this project doesn't
have yet).

`contest_results`/`season_records` are optional render-time extras, not `PostMortemReport` fields:
unlike everything else this module renders, they aren't derived from the week's slate snapshot --
they're Chris's own real DK play history (`storage/contest_results_store.py`) and season-to-date
record (`tracking/season_record.py`), read separately by the calling script. Passing neither still
renders a complete, valid page (this is the direct real-path replacement for week 2's one-off
`reconstruct_week2_postmortem.py`, which needed both because no real snapshot existed that week;
from week 3 on, a snapshot always exists, and these two are the only pieces a real snapshot can't
supply on its own).
"""

from __future__ import annotations

import html

from nfl_dfs.storage.contest_results_store import ContestResult
from nfl_dfs.tracking.postmortem.models import LineupOutcome, PlayerContext, PlayerOutcome, PostMortemReport
from nfl_dfs.tracking.season_record import SeasonRecord

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
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.65rem; font-weight: 700; }
  .badge.cash { background: #11332a; color: #3ddc9b; }
  .badge.miss { background: #3a1c1c; color: #fb7979; }
  .badge.real { background: #11332a; color: #3ddc9b; }
  .badge.est { background: #3a2c12; color: #ffc94d; }
  .green { color: #3ddc9b; font-weight: 600; }
  .red { color: #fb7979; font-weight: 600; }
  details > summary { cursor: pointer; color: #7c9bff; font-size: 0.78rem; padding: 6px 0; list-style: none; }
  details > summary::-webkit-details-marker { display: none; }
  details > summary::before { content: "\25B8 "; }
  details[open] > summary::before { content: "\25BE "; }
  table.roster-table { margin-top: 4px; font-size: 0.78rem; }
  table.roster-table th, table.roster-table td { padding: 4px 8px; }
  .tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 0.62rem; font-weight: 600; margin: 0 2px 2px 0; }
  .tag.chalk { background: #3a2c12; color: #ffc94d; }
  .tag.leverage { background: #11332a; color: #3ddc9b; }
  .tag.stack { background: #202a4d; color: #7c9bff; }
  .tag.injury { background: #3a1c1c; color: #fb7979; }
  .tag.dnp { background: #3a1c1c; color: #fb7979; cursor: help; }
  .tag.note { background: #232a3d; color: #aab4c8; cursor: help; }
  tr.detail-row td { padding-top: 0; }
</style>
""".strip()


def _fmt(value, decimals=1) -> str:
    return "--" if value is None else f"{value:,.{decimals}f}"


def _salary(value: int | None) -> str:
    return "--" if value is None else f"${value:,}"


def _delta_class(delta: float | None) -> str:
    if delta is None:
        return ""
    return "green" if delta > 0 else ("red" if delta < 0 else "")


def _render_signal_tags(context: PlayerContext | None) -> str:
    """A compact tag strip of real, already-computed per-player signal context -- see
    `PlayerContext`'s own docstring for what each field is and where it comes from. Every tag is
    conditional on the field actually being populated; a player with no real signal anywhere shows
    no tags at all, never a placeholder."""
    if context is None:
        return ""
    tags: list[str] = []
    if context.is_chalk:
        tags.append('<span class="tag chalk">CHALK</span>')
    if context.is_leverage:
        tags.append('<span class="tag leverage">LEVERAGE</span>')
    if context.is_primary_stack_candidate:
        tags.append('<span class="tag stack">STACK</span>')
    if context.injury_status:
        tags.append(f'<span class="tag injury">{html.escape(context.injury_status)}</span>')
    if context.ceiling_multiplier is not None:
        tags.append(f'<span class="tag note" title="Ceiling multiplier">CEIL {context.ceiling_multiplier:.2f}x</span>')
    if context.game_environment_score is not None:
        tags.append(
            f'<span class="tag note" title="Game Environment composite score">GES {context.game_environment_score:.0f}</span>'
        )
    if context.implied_total is not None:
        tags.append(f'<span class="tag note" title="Implied team total">IT {context.implied_total:.1f}</span>')
    if context.red_zone_role_security_discount is not None:
        tags.append(
            f'<span class="tag note" title="Red-zone role-security discount">'
            f"RZ {context.red_zone_role_security_discount:+.2f}</span>"
        )
    if context.circumstance_note:
        tags.append(f'<span class="tag note" title="{html.escape(context.circumstance_note)}">NOTE</span>')
    return "".join(tags)


def _player_name_cell(p: PlayerOutcome) -> str:
    """The `<td>` for a player's name: a real box-score-line tooltip (when one exists) plus the
    signal-tags strip, both purely additive -- a player with neither renders exactly like today's
    plain name cell."""
    title_attr = ""
    if p.context and p.context.box_score_line:
        title_attr = f' title="{html.escape(p.context.box_score_line)}"'
    tags = _render_signal_tags(p.context)
    if p.did_not_play:
        # A real 0.0 because he was inactive (see `PlayerOutcome.did_not_play`) -- label it so it
        # isn't read as a played-and-busted zero.
        tags = '<span class="tag dnp" title="Did not play -- scored 0 (inactive)">DNP</span>' + tags
    tags_html = f" {tags}" if tags else ""
    return f"<td{title_attr}>{html.escape(p.display_name)}{tags_html}</td>"


_ROSTER_POSITION_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "DST": 5}


def _in_roster_order(players: tuple[PlayerOutcome, ...]) -> list[PlayerOutcome]:
    """QB-RB-RB-WR-WR-WR-TE-FLEX-DST, matching DK's own roster-slot order -- `lineup.players` in
    the slate snapshot preserves whatever order the solver happened to emit (confirmed live: not
    slot order), so this is a real, needed sort, not just a stable no-op. A stable sort keeps
    same-position players (RB1 vs RB2, WR1 vs WR2 vs WR3) in their original relative order, since
    `PlayerOutcome.position` doesn't carry the slot number, only the position label."""
    return sorted(players, key=lambda p: _ROSTER_POSITION_ORDER.get(p.position, 99))


def _render_roster_table(players: tuple[PlayerOutcome, ...]) -> str:
    rows = "".join(
        f"<tr><td class=\"pos\">{p.position}</td>{_player_name_cell(p)}<td>{p.team}</td>"
        f"<td class=\"num\">{_salary(p.salary)}</td>"
        f"<td class=\"num\">{_fmt(p.projected)}</td><td class=\"num\">{_fmt(p.actual)}</td>"
        f"<td class=\"num {_delta_class(p.delta)}\">{'--' if p.delta is None else f'{p.delta:+.1f}'}</td></tr>"
        for p in _in_roster_order(players)
    )
    return (
        '<table class="roster-table"><thead><tr><th>Pos</th><th>Player</th><th>Team</th><th>Salary</th>'
        "<th>Proj</th><th>Actual</th><th>Delta</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


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
        rows.append(
            '<tr class="detail-row"><td colspan="4"><details>'
            f"<summary>Roster ({len(lo.players)} players)</summary>"
            f"{_render_roster_table(lo.players)}"
            "</details></td></tr>"
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


def _render_player_table(title: str, players: tuple[PlayerOutcome, ...], empty_message: str) -> str:
    if not players:
        return f"<h2>{html.escape(title)}</h2><div class=\"card\"><p>{html.escape(empty_message)}</p></div>"
    rows = "".join(
        f"<tr><td class=\"pos\">{p.position}</td>{_player_name_cell(p)}<td>{p.team}</td>"
        f"<td class=\"num\">{_salary(p.salary)}</td>"
        f"<td class=\"num\">{_fmt(p.projected)}</td><td class=\"num\">{_fmt(p.actual)}</td></tr>"
        for p in players
    )
    return (
        f"<h2>{html.escape(title)}</h2>"
        '<div class="card"><table><thead><tr><th>Pos</th><th>Player</th><th>Team</th><th>Salary</th>'
        "<th>Proj</th><th>Actual</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
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


def _render_contest_results(contest_results: tuple[ContestResult, ...]) -> str:
    if not contest_results:
        return ""
    total = len(contest_results)
    cashed = sum(1 for c in contest_results if c.cashed)
    rows = "".join(
        f"<tr><td>{html.escape(c.lineup_label)}</td><td>{html.escape(c.contest_name)}</td>"
        f"<td class=\"num\">{c.entries:,}</td><td class=\"num\">{c.rank:,}</td>"
        f"<td class=\"num\">{c.rank / c.entries * 100:.1f}%</td>"
        f"<td class=\"num\">{_fmt(c.fpts)}</td><td class=\"num\">${c.winnings:,.2f}</td>"
        f"<td><span class=\"badge {'cash' if c.cashed else 'miss'}\">{'CASHED' if c.cashed else 'missed'}</span></td></tr>"
        for c in sorted(contest_results, key=lambda c: c.rank / c.entries)
    )
    total_winnings = sum(c.winnings for c in contest_results)
    return (
        f"<h2>Real Contest Results ({total} entries, {cashed} cashed, ${total_winnings:,.2f})</h2>"
        '<div class="card"><table><thead><tr><th>Lineup</th><th>Contest</th><th>Entries</th>'
        "<th>Rank</th><th>Percentile</th><th>FPTS</th><th>Winnings</th><th>Result</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _render_season_records(season_records: tuple[SeasonRecord, ...]) -> str:
    if not season_records:
        return ""
    from nfl_dfs.agents.registry import NFL_AGENTS

    agent_display_names = {a.agent_id: a.display_name for a in NFL_AGENTS}
    rows = []
    for r in season_records:
        label = r.agent_id if r.is_operator_lineup else agent_display_names.get(r.agent_id, r.agent_id)
        basis_cls = "real" if r.basis == "real" else "est"
        basis_title = "Real DK contest entries" if r.basis == "real" else "Interpolated against the real contests you entered"
        cash_record = f"{r.contest_cashes}/{r.contest_entries}" if r.contest_entries else "--"
        delta_cls = "green" if (r.avg_delta or 0) > 0 else "red"
        best = f"{_fmt(r.best_week[1])} (wk{r.best_week[0]})" if r.best_week else "--"
        rows.append(
            f"<tr><td>{html.escape(label)} <span class=\"badge {basis_cls}\" title=\"{basis_title}\">{r.basis}</span></td>"
            f"<td class=\"num\">{r.wins}-{r.weeks_tracked - r.wins}</td>"
            f"<td class=\"num\">{cash_record}</td>"
            f"<td class=\"num {delta_cls}\">{'' if r.avg_delta is None else f'{r.avg_delta:+.1f}'}</td>"
            f"<td class=\"num\">{best}</td></tr>"
        )
    return (
        "<h2>Season Record (L1/L2/L3 individually + the 6 agents)</h2>"
        '<div class="card"><table><thead><tr><th>Lineup / Agent</th><th>W-L (would-cash)</th>'
        "<th>Cashed</th><th>Avg Delta</th><th>Best Week</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        '<p class="note">"real" = your own DK contest entries. "est" = an agent never actually '
        "entered -- cash outcome is interpolated against the same real contests you played that "
        "week.</p></div>"
    )


def _render_exposure(report: PostMortemReport) -> str:
    meaningful = [p for p in report.player_exposure if p.count >= 3]
    if not meaningful:
        return '<h2>Player Exposure (3+ lineups)</h2><div class="card"><p>No player was rostered by 3 or more of this week\'s lineups.</p></div>'
    rows = "".join(
        f"<tr><td class=\"pos\">{p.position}</td><td>{html.escape(p.display_name)}</td><td>{p.team}</td>"
        f"<td class=\"num\">{p.count}/{len(report.lineup_outcomes)}</td>"
        f"<td class=\"num\">{_fmt(p.projected)}</td><td class=\"num\">{_fmt(p.actual)}</td>"
        f"<td class=\"num {'green' if (p.delta or 0) > 0 else 'red'}\">{'--' if p.delta is None else f'{p.delta:+.1f}'}</td></tr>"
        for p in meaningful
    )
    return (
        "<h2>Player Exposure (3+ lineups -- real portfolio decision impact)</h2>"
        '<div class="card"><table><thead><tr><th>Pos</th><th>Player</th><th>Team</th><th>Exposure</th>'
        "<th>Proj</th><th>Actual</th><th>Delta</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _render_positional(report: PostMortemReport) -> str:
    if not report.positional_deltas:
        return ""
    rows = "".join(
        f"<tr><td class=\"pos\">{d.position}</td><td class=\"num\">{d.n}</td>"
        f"<td class=\"num\">{_fmt(d.avg_projected)}</td><td class=\"num\">{_fmt(d.avg_actual)}</td>"
        f"<td class=\"num {'green' if d.avg_delta > 0 else 'red'}\">{d.avg_delta:+.1f}</td></tr>"
        for d in report.positional_deltas
    )
    return (
        "<h2>Positional Bias (avg delta, one vote per distinct player)</h2>"
        '<div class="card"><table><thead><tr><th>Pos</th><th># Players</th><th>Avg Proj</th>'
        "<th>Avg Actual</th><th>Avg Delta</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _stack_result_badge(hit: bool | None) -> str:
    if hit is None:
        return ""
    css = "cash" if hit else "miss"
    text = "HIT" if hit else "MISS"
    return f'<span class="badge {css}">{text}</span>'


def _render_stack_thesis(report: PostMortemReport) -> str:
    if not report.stack_thesis_reviews:
        return ""
    rows = []
    for r in report.stack_thesis_reviews:
        delta_cls = "" if r.hit is None else ("green" if r.hit else "red")
        delta_text = "--" if r.delta is None else f"{r.delta:+.1f}"
        stack_names = " + ".join(html.escape(n) for n in r.stack_player_names)
        rows.append(
            f"<tr><td>{html.escape(r.label)}</td><td>{stack_names}</td>"
            f"<td class=\"num\">{_fmt(r.projected)}</td><td class=\"num\">{_fmt(r.actual)}</td>"
            f"<td class=\"num {delta_cls}\">{delta_text}</td>"
            f"<td>{_stack_result_badge(r.hit)}</td></tr>"
        )
    rows = "".join(rows)
    return (
        "<h2>Did Each Agent's Named Stack Thesis Hit?</h2>"
        '<div class="card"><table><thead><tr><th>Agent</th><th>Core Stack</th><th>Proj</th>'
        "<th>Actual</th><th>Delta</th><th>Result</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def render_postmortem_html(
    report: PostMortemReport,
    *,
    contest_results: tuple[ContestResult, ...] = (),
    season_records: tuple[SeasonRecord, ...] = (),
) -> str:
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8">{_STYLE}</head>
<body>
<h1>Postmortem -- Season {report.season}, Week {report.week}</h1>
<p class="meta">{len(report.lineup_outcomes)} lineup(s) tracked.</p>
{_render_lineup_table(report.lineup_outcomes)}
{_render_season_records(season_records)}
{_render_contest_results(contest_results)}
{_render_process_grade(report)}
{_render_chalk_comparison(report)}
{_render_player_table("Top Performers", report.top_performers, "No real settled scores yet.")}
{_render_player_table("Missed Players (real scorers, not on any of our lineups)", report.missed_players, "No high scorers missed this week.")}
{_render_exposure(report)}
{_render_positional(report)}
{_render_stack_thesis(report)}
{_render_ceiling_patterns(report)}
</body>
</html>
"""
