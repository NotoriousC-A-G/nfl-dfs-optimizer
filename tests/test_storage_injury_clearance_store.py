import pytest

from nfl_dfs.storage.injury_clearance_store import QuestionableOverride, read_overrides, save_overrides


def test_decision_must_be_clear_or_bar():
    for bad in ("", "Full", "CLEAR", "keep"):
        with pytest.raises(ValueError):
            QuestionableOverride(2026, 5, "Some Back", "NYJ", bad)


def test_name_and_team_required_note_optional():
    with pytest.raises(ValueError):
        QuestionableOverride(2026, 5, " ", "NYJ", "clear")
    QuestionableOverride(2026, 5, "Some Back", "NYJ", "bar")  # no note is fine: an override is Chris's call


def test_save_is_idempotent_and_read_filters_by_week(tmp_path):
    path = tmp_path / "q.csv"
    a = QuestionableOverride(2026, 5, "A Back", "nyj", "clear")
    b = QuestionableOverride(2026, 6, "B Back", "KC", "bar", note="limited all week")
    assert len(save_overrides([a, b], path=path)) == 2
    assert save_overrides([a], path=path) == []  # already present (team case-insensitive)
    assert [o.name for o in read_overrides(season=2026, week=5, path=path)] == ["A Back"]
    assert read_overrides(path=path)[0].team == "NYJ"
    assert read_overrides(season=2026, week=6, path=path)[0].note == "limited all week"


def test_missing_file_reads_as_no_overrides(tmp_path):
    assert read_overrides(path=tmp_path / "nope.csv") == []
