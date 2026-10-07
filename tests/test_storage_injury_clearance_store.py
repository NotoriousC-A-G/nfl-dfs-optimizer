import pytest

from nfl_dfs.storage.injury_clearance_store import QuestionableClearance, read_clearances, save_clearances


def test_limited_practice_without_a_positive_report_is_rejected():
    with pytest.raises(ValueError, match="positive report"):
        QuestionableClearance(2026, 5, "Some Back", "NYJ", "Limited")
    with pytest.raises(ValueError, match="positive report"):
        QuestionableClearance(2026, 5, "Some Back", "NYJ", "Limited", note="   ")


def test_limited_with_a_note_and_full_without_one_are_accepted():
    QuestionableClearance(2026, 5, "Some Back", "NYJ", "Limited", note="Rapoport: expected to play")
    QuestionableClearance(2026, 5, "Some Back", "NYJ", "Full")


def test_unknown_practice_status_is_rejected():
    for bad in ("DNP", "full", "", "Did not practice"):
        with pytest.raises(ValueError):
            QuestionableClearance(2026, 5, "Some Back", "NYJ", bad)


def test_save_is_idempotent_and_read_filters_by_week(tmp_path):
    path = tmp_path / "q.csv"
    a = QuestionableClearance(2026, 5, "A Back", "nyj", "Full")
    b = QuestionableClearance(2026, 6, "B Back", "KC", "Limited", note="beat reporter: full-go")
    assert len(save_clearances([a, b], path=path)) == 2
    assert save_clearances([a], path=path) == []  # already present (team case-insensitive)
    assert [c.name for c in read_clearances(season=2026, week=5, path=path)] == ["A Back"]
    assert read_clearances(path=path)[0].team == "NYJ"


def test_missing_file_reads_as_no_clearances(tmp_path):
    assert read_clearances(path=tmp_path / "nope.csv") == []
