import pandas as pd
import pytest

from nfl_dfs.ingestion.qb_rushing_profile import aggregate_trailing_qb_rushing_profile, trailing_qb_rushing_profiles


def _pbp_row(**kwargs) -> dict:
    row = {
        "season": 2026,
        "week": 1,
        "season_type": "REG",
        "posteam": "GB",
        "play_type": "pass",
        "rusher_player_id": None,
        "rusher_player_name": None,
        "qb_scramble": None,
        "rushing_yards": None,
        "rush_touchdown": None,
        "passer_player_id": None,
        "yardline_100": 50,
        "play_id": 1,
    }
    row.update(kwargs)
    return row


def _pass_attempts(week: int, team: str, player_id: str, n: int, play_id_start: int) -> list[dict]:
    return [
        _pbp_row(week=week, posteam=team, play_type="pass", passer_player_id=player_id, play_id=play_id_start + i)
        for i in range(n)
    ]


def _qb_rush(
    week: int, team: str, player_id: str, name: str, play_id: int, *, scramble: bool,
    yards: float = 0, td: int = 0, yardline_100: int = 50,
) -> dict:
    return _pbp_row(
        week=week, posteam=team, play_type="run", rusher_player_id=player_id, rusher_player_name=name,
        play_id=play_id, qb_scramble=1 if scramble else 0, rushing_yards=yards, rush_touchdown=td,
        yardline_100=yardline_100,
    )


# A real, gate-clearing trailing passer needs >= QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS (5) trailing
# pass attempts (usage_share.py's existing convention, reused here) -- every fixture below gives its
# QB exactly 5 trailing pass attempts unless testing that gate directly.
def _qualifying_passer_rows(week: int, team: str, player_id: str, play_id_start: int) -> list[dict]:
    return _pass_attempts(week, team, player_id, 5, play_id_start)


def test_aggregates_designed_runs_and_scrambles_separately():
    rows = (
        _qualifying_passer_rows(1, "GB", "QB1", 1)
        + [
            _qb_rush(1, "GB", "QB1", "Mobile QB", 100, scramble=False, yards=5),
            _qb_rush(1, "GB", "QB1", "Mobile QB", 101, scramble=False, yards=3),
            _qb_rush(1, "GB", "QB1", "Mobile QB", 102, scramble=True, yards=8),
            _qb_rush(1, "GB", "QB1", "Mobile QB", 103, scramble=True, yards=2),
            _qb_rush(1, "GB", "QB1", "Mobile QB", 104, scramble=True, yards=-1),
        ]
    )
    profiles = trailing_qb_rushing_profiles(pd.DataFrame(rows), target_week=2)
    p = profiles["QB1"]
    assert p.trailing_rush_attempts == 5
    assert p.trailing_designed_runs == 2
    assert p.trailing_scrambles == 3
    assert p.designed_run_rate == pytest.approx(0.4)
    assert p.trailing_rushing_yards == 17  # 5+3+8+2-1


def test_computes_redzone_and_goalline_rush_attempts():
    rows = (
        _qualifying_passer_rows(1, "GB", "QB2", 1)
        + [
            _qb_rush(1, "GB", "QB2", "Sneak QB", 100, scramble=False, yardline_100=3, td=1),  # goal-line + RZ
            _qb_rush(1, "GB", "QB2", "Sneak QB", 101, scramble=False, yardline_100=15),  # RZ only
            _qb_rush(1, "GB", "QB2", "Sneak QB", 102, scramble=True, yardline_100=45),  # neither
        ]
    )
    profiles = trailing_qb_rushing_profiles(pd.DataFrame(rows), target_week=2)
    p = profiles["QB2"]
    assert p.trailing_redzone_rush_attempts == 2
    assert p.trailing_goalline_rush_attempts == 1
    assert p.trailing_rush_tds == 1


def test_excludes_rushers_who_never_clear_the_trailing_passer_gate():
    # A jet-sweep WR or a real RB who has never thrown a pass must not show up here, even with real
    # rush volume -- this module only covers identified trailing passers (usage_share.py's own
    # QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS convention), not every rusher.
    rows = [
        _qb_rush(1, "GB", "WR_JET", "Jet Sweep WR", 1, scramble=False, yards=12),
        _qb_rush(1, "GB", "WR_JET", "Jet Sweep WR", 2, scramble=False, yards=8),
    ] + _pass_attempts(1, "GB", "QB3", 4, 100)  # one short of the 5-attempt gate
    profiles = trailing_qb_rushing_profiles(pd.DataFrame(rows), target_week=2)
    assert "WR_JET" not in profiles
    assert "QB3" not in profiles


def test_excludes_future_weeks():
    rows = (
        _qualifying_passer_rows(1, "GB", "QB4", 1)
        + [_qb_rush(1, "GB", "QB4", "Week1 QB", 100, scramble=False, yards=4)]
        + [_qb_rush(3, "GB", "QB4", "Week1 QB", 200, scramble=False, yards=99)]  # future, excluded
    )
    profiles = trailing_qb_rushing_profiles(pd.DataFrame(rows), target_week=2)
    p = profiles["QB4"]
    assert p.trailing_rush_attempts == 1
    assert p.trailing_rushing_yards == 4


def test_qualifying_passer_with_zero_trailing_rushes_is_absent_from_the_lookup():
    rows = _qualifying_passer_rows(1, "GB", "QB5", 1)  # passes only, never rushes
    profiles = trailing_qb_rushing_profiles(pd.DataFrame(rows), target_week=2)
    assert "QB5" not in profiles


def test_aggregate_trailing_qb_rushing_profile_returns_a_dataframe():
    rows = _qualifying_passer_rows(1, "GB", "QB6", 1) + [
        _qb_rush(1, "GB", "QB6", "Six QB", 100, scramble=False, yards=1)
    ]
    df = aggregate_trailing_qb_rushing_profile(pd.DataFrame(rows), target_week=2)
    assert set(df["player_id"]) == {"QB6"}
    assert "trailing_designed_runs" in df.columns
