from nfl_dfs.storage.played_lineups_store import (
    LineupEdit,
    PlayedLineup,
    PlayedPlayer,
    has_played_lineups,
    list_played_lineup_weeks,
    played_lineups_path,
    read_played_lineups,
    write_played_lineups,
)


def _player(name: str, position: str = "WR", team: str = "GB", salary: int | None = None) -> PlayedPlayer:
    return PlayedPlayer(display_name=name, position=position, team=team, salary=salary)


def _lineup(label: str, source_agent_label: str | None = None, edits: tuple[LineupEdit, ...] = ()) -> PlayedLineup:
    return PlayedLineup(
        label=label,
        players=(
            _player("QB Guy", "QB", "GB"),
            _player("RB Guy", "RB", "GB"),
            _player("WR Guy", "WR", "GB"),
        ),
        source_agent_label=source_agent_label,
        edits=edits,
        total_salary=49500,
    )


def test_has_played_lineups_false_when_nothing_written(tmp_path):
    assert has_played_lineups(2026, 3, base_dir=tmp_path) is False


def test_write_then_has_played_lineups_true(tmp_path):
    write_played_lineups(2026, 3, [_lineup("L1")], logged_at="2026-09-20T12:00:00Z", base_dir=tmp_path)
    assert has_played_lineups(2026, 3, base_dir=tmp_path) is True


def test_write_played_lineups_round_trips(tmp_path):
    edit = LineupEdit(player_out="Caleb Douglas", player_in="Jaylin Noel", note="swapped before lock")
    lineups = [_lineup("L1", source_agent_label="Volatility Engine", edits=(edit,)), _lineup("L2")]
    write_played_lineups(2026, 3, lineups, logged_at="2026-09-20T12:00:00Z", base_dir=tmp_path)

    week = read_played_lineups(2026, 3, base_dir=tmp_path)
    assert week.season == 2026
    assert week.week == 3
    assert week.logged_at == "2026-09-20T12:00:00Z"
    assert [l.label for l in week.lineups] == ["L1", "L2"]
    assert week.lineups[0].source_agent_label == "Volatility Engine"
    assert week.lineups[0].edits == (edit,)
    assert week.lineups[0].players[0] == _player("QB Guy", "QB", "GB")
    assert week.lineups[1].source_agent_label is None
    assert week.lineups[1].edits == ()


def test_write_played_lineups_same_week_overwrites_not_appends(tmp_path):
    write_played_lineups(2026, 3, [_lineup("L1")], logged_at="2026-09-20T08:00:00Z", base_dir=tmp_path)
    write_played_lineups(2026, 3, [_lineup("L1"), _lineup("L2"), _lineup("L3")], logged_at="2026-09-20T20:00:00Z", base_dir=tmp_path)

    week = read_played_lineups(2026, 3, base_dir=tmp_path)
    assert [l.label for l in week.lineups] == ["L1", "L2", "L3"]
    assert week.logged_at == "2026-09-20T20:00:00Z"


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    write_played_lineups(2026, 3, [_lineup("L1")], logged_at="2026-09-20T12:00:00Z", base_dir=tmp_path)
    leftover_tmp = list(tmp_path.glob("*.tmp"))
    assert leftover_tmp == []
    final = list(tmp_path.glob("*.json"))
    assert [p.name for p in final] == ["2026-3.json"]


def test_list_played_lineup_weeks_empty_directory(tmp_path):
    assert list_played_lineup_weeks(base_dir=tmp_path) == []


def test_list_played_lineup_weeks_sorted(tmp_path):
    write_played_lineups(2026, 3, [_lineup("L1")], logged_at="t", base_dir=tmp_path)
    write_played_lineups(2026, 1, [_lineup("L1")], logged_at="t", base_dir=tmp_path)
    write_played_lineups(2026, 2, [_lineup("L1")], logged_at="t", base_dir=tmp_path)
    assert list_played_lineup_weeks(base_dir=tmp_path) == ["2026-1", "2026-2", "2026-3"]


def test_played_lineups_path_uses_default_root_when_base_dir_omitted():
    path = played_lineups_path(2026, 3)
    assert path.name == "2026-3.json"
    assert "played_lineups" in path.parts
