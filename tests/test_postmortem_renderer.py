from nfl_dfs.storage.contest_results_store import ContestResult
from nfl_dfs.tracking.postmortem.models import (
    ChalkComparison,
    CeilingPatterns,
    LineupOutcome,
    PlayerExposure,
    PlayerOutcome,
    PositionalDelta,
    PostMortemReport,
    ProcessGrade,
    StackThesisReview,
)
from nfl_dfs.tracking.postmortem_renderer import render_postmortem_html
from nfl_dfs.tracking.season_record import SeasonRecord


def _player(cid="p1", actual=10.0) -> PlayerOutcome:
    return PlayerOutcome(cid, cid, "AAA", "WR", 5000, 8.0, actual, (actual - 8.0) if actual is not None else None)


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
