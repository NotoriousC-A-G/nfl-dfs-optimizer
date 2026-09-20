from nfl_dfs.storage.agent_results_store import AgentResultRow
from nfl_dfs.tracking.agent_results_renderer import render_agent_performance_html


def _row(agent_id, strategy_name, **overrides) -> AgentResultRow:
    defaults = dict(
        season=2026,
        week=2,
        agent_id=agent_id,
        strategy_name=strategy_name,
        proj_total=140.0,
        salary=49900,
        players=("A (QB-GB)",),
    )
    defaults.update(overrides)
    return AgentResultRow(**defaults)


def test_render_includes_every_row_and_operator_class():
    rows = [
        _row("chalk_anchor", "Chalk Anchor", total_dk_score=110.0, lineup_rank=2),
        _row("operator", "L1", total_dk_score=130.0, lineup_rank=1),
    ]
    out = render_agent_performance_html(rows, season=2026, week=2)
    assert "Chalk Anchor" in out
    assert 'class="operator"' in out
    assert "L1" in out


def test_render_sorts_by_lineup_rank():
    rows = [
        _row("chalk_anchor", "Chalk Anchor", total_dk_score=110.0, lineup_rank=2),
        _row("operator", "L1", total_dk_score=130.0, lineup_rank=1),
    ]
    out = render_agent_performance_html(rows, season=2026, week=2)
    assert out.index("L1") < out.index("Chalk Anchor")


def test_render_shows_dashes_for_unscored_rows_not_zero():
    rows = [_row("chalk_anchor", "Chalk Anchor", total_dk_score=None, lineup_rank=None, boom=None)]
    out = render_agent_performance_html(rows, season=2026, week=2)
    row_html = out[out.index("<tbody>") : out.index("</tbody>")]
    # DK Score, Rank, and Boom are all unresolved -- each renders as "--", never a fabricated 0.
    assert row_html.count("<td class=\"num\">--</td>") == 3


def test_render_notes_unscored_count():
    rows = [
        _row("chalk_anchor", "Chalk Anchor", total_dk_score=110.0, lineup_rank=1),
        _row("operator", "L1", total_dk_score=None, lineup_rank=None),
    ]
    out = render_agent_performance_html(rows, season=2026, week=2)
    assert "1 of 2 lineup(s) not yet scored" in out


def test_render_no_unscored_note_when_everything_is_scored():
    rows = [_row("chalk_anchor", "Chalk Anchor", total_dk_score=110.0, lineup_rank=1)]
    out = render_agent_performance_html(rows, season=2026, week=2)
    assert "not yet scored" not in out


def test_render_escapes_html_in_names():
    rows = [_row("chalk_anchor", "<script>alert(1)</script>")]
    out = render_agent_performance_html(rows, season=2026, week=2)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
