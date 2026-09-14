from pathlib import Path

import pytest

from nfl_dfs.ingestion.rotogrinders_injuries import (
    INJURY_GRID_ID,
    INJURY_REPORT_CSV_URL,
    fetch_injury_report,
    parse_injury_report_csv,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_grid_id_and_url_constants():
    # See module docstring's "Grid ID stability" finding: treated as a stable, hardcoded, season-
    # long constant, not re-discovered per pull the way LineupHQ's grid id is.
    assert INJURY_GRID_ID == "3102500"
    assert INJURY_REPORT_CSV_URL == "https://rotogrinders.com/grids/3102500.csv"


def test_parse_injury_report_csv_drops_malformed_trailing_row():
    csv_text = (FIXTURES / "rotogrinders_injury_report.csv").read_text()
    entries = parse_injury_report_csv(csv_text)

    # 23 real rows -- the fixture's 24th line is the malformed trailing row (empty PLAYERID)
    # confirmed live; it must be dropped, not parsed as a bogus 24th player.
    assert len(entries) == 23
    assert all(entry.rotogrinders_player_id for entry in entries)


def test_parse_injury_report_csv_extracts_all_fields():
    csv_text = (FIXTURES / "rotogrinders_injury_report.csv").read_text()
    entries = parse_injury_report_csv(csv_text)

    jacobs = next(e for e in entries if e.name == "Josh Jacobs")
    assert jacobs.rotogrinders_player_id == "973101"
    assert jacobs.team == "GBP"
    assert jacobs.position == "RB"
    assert jacobs.status == "O"
    assert jacobs.body_part == "Personal"
    assert jacobs.impact_rating == 10

    nabers = next(e for e in entries if e.name == "Malik Nabers")
    assert nabers.status == "Q"
    assert nabers.impact_rating == 10

    # A quoted name containing an apostrophe -- exercise csv.DictReader's own quoting handling
    # rather than a naive split.
    thornton = next(e for e in entries if "Thornton" in e.name)
    assert thornton.name == "Dont'e Thornton"


def test_parse_injury_report_csv_status_codes_seen_are_o_and_q_only():
    # Documents this round's live-pull finding directly in a test, not just in a comment: the
    # fixture (captured from a real live pull) only contains "O" and "Q" -- "D" was flagged as
    # "likely" by the task brief but was NOT observed live.
    csv_text = (FIXTURES / "rotogrinders_injury_report.csv").read_text()
    entries = parse_injury_report_csv(csv_text)
    assert {e.status for e in entries} == {"O", "Q"}


def test_parse_injury_report_csv_raises_on_missing_expected_column():
    with pytest.raises(RuntimeError, match="missing expected column"):
        parse_injury_report_csv("PLAYERID,PLAYER,TEAM,POS\n1,Foo,KC,QB\n")


def test_fetch_injury_report_raises_without_configured_cookie(monkeypatch):
    # This dev machine's own .env may have a real ROTOGRINDERS_SESSION_COOKIE configured (needed
    # for the manual live check), so the "no cookie" case has to patch the module's imported
    # `config` reference directly rather than relying on an unconfigured environment.
    monkeypatch.setattr(
        "nfl_dfs.ingestion.rotogrinders_injuries.config",
        type("StubConfig", (), {"rotogrinders_session_cookie": None})(),
    )
    with pytest.raises(RuntimeError, match="ROTOGRINDERS_SESSION_COOKIE"):
        fetch_injury_report(session_cookie=None, session=_ExplodingSession())


class _ExplodingSession:
    """A stand-in `requests.Session` that fails the test if ever called -- proves
    `fetch_injury_report` short-circuits on the missing-cookie check before touching the network.
    """

    def get(self, *args, **kwargs):  # noqa: D401 -- test double
        raise AssertionError("network should not be reached when the cookie is missing")


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return _FakeResponse(self.text)


def test_fetch_injury_report_sends_cookie_header_and_site_param_and_parses_response():
    csv_text = (FIXTURES / "rotogrinders_injury_report.csv").read_text()
    fake = _FakeSession(csv_text)

    entries = fetch_injury_report(site="draftkings", session_cookie="rg_session=abc123", session=fake)

    assert len(entries) == 23
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == INJURY_REPORT_CSV_URL
    assert call["headers"]["Cookie"] == "rg_session=abc123"
    assert call["params"] == {"site": "draftkings"}
