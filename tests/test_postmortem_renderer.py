import re

from nfl_dfs.storage.contest_results_store import ContestResult
from nfl_dfs.tracking.postmortem.models import (
    ChalkComparison,
    CeilingPatterns,
    LineupOutcome,
    PlayerContext,
    PlayerExposure,
    PlayerOutcome,
    PositionalDelta,
    PostMortemReport,
    ProcessGrade,
    StackThesisReview,
)
from nfl_dfs.tracking.postmortem_renderer import render_postmortem_html
from nfl_dfs.tracking.season_record import SeasonRecord


def _player(cid="p1", actual=10.0, context: PlayerContext | None = None) -> PlayerOutcome:
    return PlayerOutcome(
        cid, cid, "AAA", "WR", 5000, 8.0, actual, (actual - 8.0) if actual is not None else None, context=context
    )


def _lineup(agent_id, label, actual_total=100.0) -> LineupOutcome:
    return LineupOutcome(agent_id, label, (_player(),), 90.0, actual_total, None if actual_total is None else actual_total - 90.0)


def _report(**overrides) -> PostMortemReport:
    defaults = dict(
        season=2026,
        week=2,
        lineup_outcomes=(_lineup("chalk_anchor", "Chalk Anchor", 110.0), _lineup("operator", "L1", 120.0)),
        top_performers=(),
        missed_players=(),
        chalk_comparison=None,
        ceiling_patterns=None,
        process_grade=None,
    )
    defaults.update(overrides)
    return PostMortemReport(**defaults)


def test_renders_every_lineup():
    out = render_postmortem_html(_report())
    assert "Chalk Anchor" in out
    assert "L1" in out
    assert 'class="operator"' in out


def test_sorts_lineups_by_actual_descending():
    out = render_postmortem_html(_report())
    assert out.index("L1") < out.index("Chalk Anchor")


def test_unscored_lineup_renders_dashes_and_note():
    report = _report(lineup_outcomes=(_lineup("chalk_anchor", "Chalk Anchor", None),))
    out = render_postmortem_html(report)
    assert "not fully scored" in out


def test_process_grade_na_state():
    report = _report(process_grade=ProcessGrade(letter="N/A", score=None, signals_hit=0, signals_evaluated=1, summary="too few signals"))
    out = render_postmortem_html(report)
    assert "too few signals" in out


def test_process_grade_letter_and_pills():
    grade = ProcessGrade(
        letter="B", score=75.0, signals_hit=2, signals_evaluated=3,
        signal_names_hit=["Ownership"], signal_names_missed=["Game Environment"], summary="2/3 hit",
    )
    out = render_postmortem_html(_report(process_grade=grade))
    assert "pill hit" in out and "Ownership" in out
    assert "pill miss" in out and "Game Environment" in out


def test_chalk_comparison_infeasible():
    chalk = ChalkComparison(infeasible=True, reason="Ownership data too sparse")
    out = render_postmortem_html(_report(chalk_comparison=chalk))
    assert "Ownership data too sparse" in out


def test_chalk_comparison_feasible_shows_beat_or_trailed():
    chalk_lineup = LineupOutcome("chalk_proxy", "Chalk Proxy", (_player(),), 90.0, 100.0, 10.0)
    chalk = ChalkComparison(chalk_lineup=chalk_lineup, chalk_actual=100.0, our_actual=110.0, delta=10.0, beat_chalk=True)
    out = render_postmortem_html(_report(chalk_comparison=chalk))
    assert "beat chalk" in out


def test_ceiling_patterns_render_highlights():
    patterns = CeilingPatterns(missed_count=2, salary_bucket_summary="2/2 missed in <$3.5K", position_theme="2/2 missed were WR", highlights=["Some Guy @ $3,000 -- scored 25.0 pts (AAA WR)"])
    out = render_postmortem_html(_report(ceiling_patterns=patterns))
    assert "Some Guy" in out


def test_escapes_html_in_lineup_labels():
    report = _report(lineup_outcomes=(_lineup("chalk_anchor", "<script>alert(1)</script>", 100.0),))
    out = render_postmortem_html(report)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_contest_results_section_omitted_when_not_passed():
    out = render_postmortem_html(_report())
    assert "Real Contest Results" not in out


def test_contest_results_section_renders_cash_and_miss_badges():
    contests = (
        ContestResult(2026, 2, "L1", "$125K First Down", 100, 30, 125000, 5, 156.2, 5.0),
        ContestResult(2026, 2, "L2", "$125K First Down", 100, 30, 125000, 90, 94.06, 0.0),
    )
    out = render_postmortem_html(_report(), contest_results=contests)
    assert "Real Contest Results (2 entries, 1 cashed, $5.00)" in out
    assert "CASHED" in out and "missed" in out


def test_season_records_section_shows_real_and_estimated_basis():
    records = (
        SeasonRecord("L1", True, 2, 5.0, (2, 156.2), (1, 90.0), 1, 3, 1, "real"),
        SeasonRecord("chalk_anchor", False, 2, -10.0, (1, 130.0), (2, 100.0), 0, 2, 0, "estimated"),
    )
    out = render_postmortem_html(_report(), season_records=records)
    assert "badge real" in out
    assert "badge est" in out
    assert "Chalk Anchor" in out  # real display name, not the raw agent_id slug


def test_exposure_section_only_shows_players_with_3_plus_lineups():
    report = _report(
        player_exposure=(
            PlayerExposure("p1", "Chalky Guy", "MIN", "WR", 10.0, 15.0, 5.0, ("A", "B", "C")),
            PlayerExposure("p2", "One Off", "MIN", "WR", 10.0, 5.0, -5.0, ("A",)),
        )
    )
    out = render_postmortem_html(report)
    assert "Chalky Guy" in out
    assert "One Off" not in out


def test_positional_bias_section_renders_when_present():
    report = _report(positional_deltas=(PositionalDelta("RB", 4, 18.0, 15.0, -3.0),))
    out = render_postmortem_html(report)
    assert "Positional Bias" in out
    assert "-3.0" in out


def test_stack_thesis_section_renders_hit_and_miss():
    report = _report(
        stack_thesis_reviews=(
            StackThesisReview("Chalk Anchor", "chalk_anchor", ("QB One", "WR One"), "MIN", 35.0, 45.0, 10.0, True),
            StackThesisReview("Arbitrageur", "arbitrageur", ("QB Two",), "KC", 20.0, 10.0, -10.0, False),
        )
    )
    out = render_postmortem_html(report)
    assert "QB One + WR One" in out
    assert "badge cash" in out and "HIT" in out
    assert "badge miss" in out and "MISS" in out


def test_lineup_table_includes_an_expandable_roster_per_lineup():
    report = _report()
    out = render_postmortem_html(report)
    assert "<details>" in out
    assert "Roster (1 players)" in out
    assert "roster-table" in out


def test_roster_row_shows_real_box_score_tooltip():
    ctx = PlayerContext(box_score_line="18/27, 245 pass yds, 2 TD, 1 INT")
    report = _report(lineup_outcomes=(_lineup_with_players("L1", (_player(context=ctx),)),))
    out = render_postmortem_html(report)
    assert 'title="18/27, 245 pass yds, 2 TD, 1 INT"' in out


def test_roster_row_shows_no_tooltip_when_box_score_is_absent():
    report = _report(lineup_outcomes=(_lineup_with_players("L1", (_player(context=PlayerContext()),)),))
    out = render_postmortem_html(report)
    assert "title=" not in out.split("Roster (1 players)")[1].split("</details>")[0]


def test_signal_tags_render_only_for_populated_fields():
    ctx = PlayerContext(is_chalk=True, is_leverage=False, is_primary_stack_candidate=True, injury_status="Q")
    report = _report(lineup_outcomes=(_lineup_with_players("L1", (_player(context=ctx),)),))
    out = render_postmortem_html(report)
    assert "tag chalk\">CHALK" in out
    assert "tag stack\">STACK" in out
    assert "tag injury\">Q" in out
    assert "LEVERAGE" not in out  # is_leverage False -> no tag


def test_player_with_no_context_renders_plain_name_with_no_tags():
    report = _report(lineup_outcomes=(_lineup_with_players("L1", (_player(context=None),)),))
    out = render_postmortem_html(report)
    assert 'class="tag' not in out


def test_top_performers_section_renders_players():
    report = _report(top_performers=(_player(cid="best", actual=41.4),))
    out = render_postmortem_html(report)
    assert "Top Performers" in out
    assert "best" in out


def test_top_performers_empty_state():
    out = render_postmortem_html(_report(top_performers=()))
    assert "No real settled scores yet." in out


def test_missed_players_section_renders_players_and_empty_state():
    with_players = render_postmortem_html(_report(missed_players=(_player(cid="missed_guy", actual=22.0),)))
    assert "Missed Players" in with_players
    assert "missed_guy" in with_players

    empty = render_postmortem_html(_report(missed_players=()))
    assert "No high scorers missed this week." in empty


def _lineup_with_players(label, players, agent_id="operator", actual_total=100.0):
    return LineupOutcome(agent_id, label, players, 90.0, actual_total, actual_total - 90.0)


def _positioned_player(cid, position) -> PlayerOutcome:
    return PlayerOutcome(cid, cid, "AAA", position, 5000, 8.0, 10.0, 2.0)


def test_roster_table_reorders_to_qb_rb_rb_wr_wr_wr_te_flex_dst_regardless_of_input_order():
    # Deliberately jumbled input order, same shape the real snapshot's `lineup.players` list
    # produces (solver emission order, not slot order).
    jumbled = (
        _positioned_player("rb2", "RB"),
        _positioned_player("wr1", "WR"),
        _positioned_player("dst", "DST"),
        _positioned_player("qb", "QB"),
        _positioned_player("te", "TE"),
        _positioned_player("wr2", "WR"),
        _positioned_player("flex", "FLEX"),
        _positioned_player("rb1", "RB"),
        _positioned_player("wr3", "WR"),
    )
    report = _report(lineup_outcomes=(_lineup_with_players("L1", jumbled),))
    out = render_postmortem_html(report)

    roster_html = out.split("Roster (9 players)")[1].split("</details>")[0]
    pos_sequence = re.findall(r'<td class="pos">(\w+)</td>', roster_html)
    assert pos_sequence == ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]


def test_roster_table_labels_a_did_not_play_zero_so_it_is_not_read_as_a_bust():
    inactive = PlayerOutcome("rb1", "Inactive Back", "NYJ", "RB", 6000, 15.0, 0.0, -15.0, did_not_play=True)
    played = PlayerOutcome("wr1", "Active Guy", "NYJ", "WR", 5000, 10.0, 0.0, -10.0)  # a genuine 0, played
    report = _report(lineup_outcomes=(_lineup_with_players("L1", (inactive, played)),))
    roster_html = render_postmortem_html(report).split("Roster (2 players)")[1].split("</details>")[0]

    inactive_cell = roster_html.split("Inactive Back")[1].split("</td>")[0]
    assert 'class="tag dnp"' in inactive_cell
    active_cell = roster_html.split("Active Guy")[1].split("</td>")[0]
    assert "DNP" not in active_cell
