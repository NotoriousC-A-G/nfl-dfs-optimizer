import numpy as np
import pandas as pd

from nfl_dfs.ingestion.official_injury_report import (
    latest_week_entries,
    parse_official_injury_report,
)


def _row(**kwargs) -> dict:
    row = {
        "season": 2026,
        "week": 1,
        "season_type": "REG",
        "game_type": "REG",
        "team": "ARI",
        "gsis_id": "00-0001",
        "position": "QB",
        "full_name": "Test Player",
        "first_name": "Test",
        "last_name": "Player",
        "report_primary_injury": np.nan,
        "report_secondary_injury": np.nan,
        "report_status": np.nan,
        "practice_primary_injury": np.nan,
        "practice_secondary_injury": np.nan,
        "practice_status": np.nan,
    }
    row.update(kwargs)
    return row


def test_parse_official_injury_report_extracts_real_fields():
    df = pd.DataFrame(
        [
            _row(
                gsis_id="00-0039007", team="ARI", position="CB", full_name="Garrett Williams",
                report_status="Out", practice_status="Limited Participation in Practice",
                report_primary_injury="Achilles",
            )
        ]
    )
    entries = parse_official_injury_report(df)
    assert len(entries) == 1
    e = entries[0]
    assert e.gsis_id == "00-0039007"
    assert e.season == 2026
    assert e.week == 1
    assert e.team == "ARI"
    assert e.position == "CB"
    assert e.full_name == "Garrett Williams"
    assert e.report_status == "Out"
    assert e.practice_status == "Limited Participation in Practice"
    assert e.report_primary_injury == "Achilles"


def test_parse_official_injury_report_none_not_fabricated_for_unset_status():
    # A player can appear on the practice report with no game-status designation yet (real, common
    # mid-week case) -- report_status must come back None, never a fabricated empty string.
    df = pd.DataFrame([_row(gsis_id="00-0002", report_status=np.nan, practice_status="Full Participation in Practice")])
    entries = parse_official_injury_report(df)
    assert entries[0].report_status is None
    assert entries[0].practice_status == "Full Participation in Practice"
    assert entries[0].report_primary_injury is None


def test_parse_official_injury_report_drops_rows_with_no_gsis_id():
    # nflverse's own feed can carry an unresolved-player row (null gsis_id) -- this project can
    # never join that row against anything, so it's dropped rather than kept with a fabricated id.
    df = pd.DataFrame([_row(gsis_id="00-0003"), _row(gsis_id=np.nan)])
    entries = parse_official_injury_report(df)
    assert len(entries) == 1
    assert entries[0].gsis_id == "00-0003"


def test_latest_week_entries_returns_only_the_most_recent_week():
    df = pd.DataFrame(
        [
            _row(gsis_id="00-0001", week=1, report_status="Out"),
            _row(gsis_id="00-0002", week=2, report_status="Questionable"),
            _row(gsis_id="00-0003", week=2, report_status="Doubtful"),
        ]
    )
    entries = parse_official_injury_report(df)
    week, latest = latest_week_entries(entries)
    assert week == 2
    assert {e.gsis_id for e in latest} == {"00-0002", "00-0003"}


def test_latest_week_entries_empty_input():
    assert latest_week_entries([]) == (None, [])
