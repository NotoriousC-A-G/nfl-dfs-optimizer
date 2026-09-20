from nfl_dfs.tracking.postmortem.models import PlayerOutcome
from nfl_dfs.tracking.postmortem.retrospective import (
    compute_process_grade,
    compute_signal_verdicts,
    extract_ceiling_patterns,
)


def _row(cid, name="P", team="AAA", position="WR", **overrides) -> dict:
    base = {"identity": {"canonical_id": cid, "display_name": name}, "team": team, "position": position}
    base.update(overrides)
    return base


def test_compute_signal_verdicts_empty_pool():
    assert compute_signal_verdicts([], {}) == {}


def test_compute_signal_verdicts_skips_players_without_actual():
    pool = [_row("p1", ownership={"is_chalk": True})]
    verdicts = compute_signal_verdicts(pool, {})  # no actual points at all
    assert verdicts == {}


def test_ownership_leverage_signal_computed_when_both_groups_present():
    pool = [
        _row("chalk1", ownership={"is_chalk": True, "is_leverage": False}),
        _row("chalk2", ownership={"is_chalk": True, "is_leverage": False}),
        _row("lev1", ownership={"is_chalk": False, "is_leverage": True}),
        _row("lev2", ownership={"is_chalk": False, "is_leverage": True}),
    ]
    actual = {"chalk1": 10.0, "chalk2": 12.0, "lev1": 20.0, "lev2": 22.0}
    verdicts = compute_signal_verdicts(pool, actual)
    assert "ownership_leverage" in verdicts
    v = verdicts["ownership_leverage"]
    assert v["worked"] is True  # leverage (avg 21) outscored chalk (avg 11)
    assert v["delta"] == 10.0


def test_signal_absent_when_one_group_empty():
    pool = [_row("chalk1", ownership={"is_chalk": True, "is_leverage": False})]
    verdicts = compute_signal_verdicts(pool, {"chalk1": 10.0})
    assert "ownership_leverage" not in verdicts


def test_game_environment_signal():
    pool = [
        _row("hi1", game_environment={"composite_score": 75.0}),
        _row("hi2", game_environment={"composite_score": 70.0}),
        _row("lo1", game_environment={"composite_score": 20.0}),
        _row("lo2", game_environment={"composite_score": 30.0}),
    ]
    actual = {"hi1": 15.0, "hi2": 17.0, "lo1": 5.0, "lo2": 7.0}
    verdicts = compute_signal_verdicts(pool, actual)
    assert verdicts["game_environment"]["worked"] is True


def test_stack_context_signal():
    pool = [
        _row("s1", stack_context={"is_primary_stack_candidate": True}),
        _row("s2", stack_context={"is_primary_stack_candidate": True}),
        _row("n1", stack_context={"is_primary_stack_candidate": False}),
        _row("n2", stack_context=None),
    ]
    actual = {"s1": 20.0, "s2": 18.0, "n1": 5.0, "n2": 6.0}
    verdicts = compute_signal_verdicts(pool, actual)
    assert verdicts["primary_stack_candidate"]["worked"] is True


def test_ceiling_multiplier_signal():
    pool = [
        _row("hi1", ceiling_multiplier=1.3),
        _row("hi2", ceiling_multiplier=1.2),
        _row("lo1", ceiling_multiplier=0.9),
        _row("lo2", ceiling_multiplier=0.95),
    ]
    actual = {"hi1": 20.0, "hi2": 22.0, "lo1": 5.0, "lo2": 6.0}
    verdicts = compute_signal_verdicts(pool, actual)
    assert verdicts["ceiling_multiplier"]["worked"] is True


# --- compute_process_grade ---------------------------------------------------------------------


def test_process_grade_na_with_no_verdicts():
    grade = compute_process_grade({})
    assert grade.letter == "N/A"
    assert grade.score is None


def test_process_grade_na_below_min_evaluated():
    verdicts = {"a": {"worked": True, "delta": 5.0}, "b": {"worked": True, "delta": 5.0}}
    grade = compute_process_grade(verdicts)
    assert grade.letter == "N/A"


def test_process_grade_all_hits_scores_high():
    verdicts = {
        "a": {"worked": True, "delta": 10.0, "signal": "A"},
        "b": {"worked": True, "delta": 10.0, "signal": "B"},
        "c": {"worked": True, "delta": 10.0, "signal": "C"},
    }
    grade = compute_process_grade(verdicts)
    assert grade.letter in ("A", "B")
    assert grade.signals_hit == 3
    assert grade.signals_evaluated == 3


def test_process_grade_all_misses_scores_low():
    verdicts = {
        "a": {"worked": False, "delta": -10.0, "signal": "A"},
        "b": {"worked": False, "delta": -10.0, "signal": "B"},
        "c": {"worked": False, "delta": -10.0, "signal": "C"},
    }
    grade = compute_process_grade(verdicts)
    assert grade.letter in ("D", "F")


def test_process_grade_excludes_signals_with_worked_none():
    verdicts = {
        "a": {"worked": True, "delta": 10.0, "signal": "A"},
        "b": {"worked": True, "delta": 10.0, "signal": "B"},
        "c": {"worked": True, "delta": 10.0, "signal": "C"},
        "d": {"worked": None, "delta": None, "signal": "D"},
    }
    grade = compute_process_grade(verdicts)
    assert grade.signals_evaluated == 3


# --- extract_ceiling_patterns -------------------------------------------------------------------


def test_ceiling_patterns_empty():
    assert extract_ceiling_patterns([]).missed_count == 0


def test_ceiling_patterns_salary_bucket_dominance():
    missed = [
        PlayerOutcome("p1", "P1", "AAA", "WR", 3000, 5.0, 22.0, 17.0),
        PlayerOutcome("p2", "P2", "AAA", "WR", 3200, 5.0, 20.0, 15.0),
        PlayerOutcome("p3", "P3", "AAA", "RB", 3300, 5.0, 18.0, 13.0),
        PlayerOutcome("p4", "P4", "AAA", "TE", 7000, 5.0, 16.0, 11.0),
    ]
    patterns = extract_ceiling_patterns(missed)
    assert patterns.missed_count == 4
    assert "<$3.5K" in patterns.salary_bucket_summary
    assert len(patterns.highlights) == 3


def test_ceiling_patterns_highlights_sorted_by_actual_descending():
    missed = [
        PlayerOutcome("p1", "Low", "AAA", "WR", 3000, 5.0, 12.0, 7.0),
        PlayerOutcome("p2", "High", "AAA", "WR", 3000, 5.0, 30.0, 25.0),
    ]
    patterns = extract_ceiling_patterns(missed)
    assert patterns.highlights[0].startswith("High")
