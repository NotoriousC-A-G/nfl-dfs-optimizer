import json
from pathlib import Path

import pytest

from nfl_dfs.ingestion.rotogrinders_resultsdb import (
    CONTEST_DATA_URL_TEMPLATE,
    CONTEST_SOURCES_URL,
    LINEUPS_URL_TEMPLATE,
    LIVE_CONTESTS_URL,
    ContestDataUnavailableError,
    NoPrimaryContestError,
    fetch_contest_data,
    fetch_contest_sources,
    fetch_lineups,
    fetch_live_contests,
    parse_contest_summary,
    parse_dk_draft_groups,
    parse_lineups,
    parse_live_contests,
    parse_player_exposures,
    parse_user_exposures,
    select_millionaire_maker_contest,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self._status_code = status_code
        self.calls: list[dict] = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return _FakeResponse(self._payload, self._status_code)


# ---------------------------------------------------------------------------
# contest-sources
# ---------------------------------------------------------------------------


def test_parse_dk_draft_groups_filters_to_dk_source_only():
    payload = _load("resultsdb_contest_sources.json")
    groups = parse_dk_draft_groups(payload)

    # Real live 2023-09-10 pull: three DK Classic draft groups (main, Early Only, Afternoon Only) --
    # the fixture's second top-level source is "draftkings.com showdown", which must be excluded.
    assert len(groups) == 3
    assert groups[0].contest_group_id == 95302
    assert groups[0].contest_suffix == ""
    suffixes = {g.contest_suffix for g in groups}
    assert "(Early Only)" in suffixes
    assert "(Afternoon Only)" in suffixes


def test_parse_dk_draft_groups_raises_on_missing_key():
    with pytest.raises(ValueError, match="contest-sources"):
        parse_dk_draft_groups({"unexpected": []})


def test_fetch_contest_sources_sends_expected_params_and_headers():
    payload = _load("resultsdb_contest_sources.json")
    fake = _FakeSession(payload)

    groups = fetch_contest_sources("2023-09-10", session=fake)

    assert len(groups) == 3
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == CONTEST_SOURCES_URL
    assert call["params"] == {"sport_id": 1, "date": "2023-09-10"}
    # service.fantasylabs.com 403s the default requests User-Agent (ADR-0023) -- a browser-shaped UA
    # must always be sent, same fix already used for RotoGrinders LineupHQ/Situation Room.
    assert call["headers"]["User-Agent"] == "Mozilla/5.0"


# ---------------------------------------------------------------------------
# live-contests
# ---------------------------------------------------------------------------


def test_parse_live_contests_extracts_real_fields():
    payload = _load("resultsdb_live_contests.json")
    contests = parse_live_contests(payload)

    assert len(contests) == 15  # real live 2023-09-10 count for this draft group
    millionaire = next(c for c in contests if c.contest_id == 147325137)
    assert millionaire.contest_name == "NFL $3M MEGA Millionaire [$1M to 1st + ToC Semifinal Entry]"
    assert millionaire.is_primary is True
    assert millionaire.contest_size == 768


def test_select_millionaire_maker_contest_disambiguates_two_is_primary_contests():
    payload = _load("resultsdb_live_contests.json")
    contests = parse_live_contests(payload)

    # Real, live-confirmed finding (ADR-0023) that reshaped this function: TWO contests are flagged
    # is_primary=True on this real date -- the mass-market "$2.5M Fantasy Football Millionaire"
    # (multi_entry_max=150, 28,029 entries, $100 buy-in) and the high-roller "$3M MEGA Millionaire"
    # (multi_entry_max=23, 768 entries, $4,444 buy-in). PRD Section 2 literally describes the Millionaire
    # Maker as "150-max entries per user" -- that field, not is_primary alone, is the real disambiguator.
    primaries = [c for c in contests if c.is_primary]
    assert len(primaries) == 2

    # The largest-by-entry-count contest in this draft group ("$200K First Down", 237,812 entries) is
    # not is_primary at all -- confirms "biggest" is still the wrong heuristic, same as before.
    largest = max(contests, key=lambda c: c.contest_size)
    assert largest.contest_id != 147325137
    assert not largest.is_primary

    picked = select_millionaire_maker_contest(contests)
    assert picked.contest_id == 147325139
    assert picked.multi_entry_max == 150
    assert picked.contest_size == 28029


def test_select_millionaire_maker_contest_raises_when_none_or_ambiguous():
    from nfl_dfs.ingestion.rotogrinders_resultsdb import LiveContest

    with pytest.raises(NoPrimaryContestError, match="found 0"):
        select_millionaire_maker_contest([])

    two_matches = [
        LiveContest(1, "A", 10, 5.0, 50.0, 150, True, False, 1),
        LiveContest(2, "B", 20, 5.0, 100.0, 150, True, False, 2),
    ]
    with pytest.raises(NoPrimaryContestError, match="found 2"):
        select_millionaire_maker_contest(two_matches)


def test_fetch_live_contests_sends_expected_params():
    payload = _load("resultsdb_live_contests.json")
    fake = _FakeSession(payload)

    contests = fetch_live_contests(95302, session=fake)

    assert len(contests) == 15
    call = fake.calls[0]
    assert call["url"] == LIVE_CONTESTS_URL
    assert call["params"] == {"sport": "NFL", "contest_group_id": 95302}


# ---------------------------------------------------------------------------
# contest data: summary, player exposures, user exposures
# ---------------------------------------------------------------------------


def test_parse_contest_summary():
    payload = _load("resultsdb_contest_data.json")
    summary = parse_contest_summary(payload)

    assert summary.contest_id == 147325137
    assert summary.contest_name == "NFL $3M MEGA Millionaire [$1M to 1st + ToC Semifinal Entry]"
    assert summary.contest_size == 768
    assert summary.duplicate_lineups == 8
    assert summary.unique_lineups == 764


def test_parse_player_exposures_merges_tier_ownership_by_player_key():
    payload = _load("resultsdb_contest_data.json")
    rows = parse_player_exposures(payload)
    by_name = {r.full_name: r for r in rows}

    # Davante Adams: real actual box score + overall ownership, present in the 20% tier only.
    adams = by_name["Davante Adams"]
    assert adams.player_key == "926:0"
    assert adams.actual_points == 12.6
    assert adams.stat_details == "66 RecYds, 6 Rec, "
    assert adams.ownership_overall == 7.42
    assert adams.ownership_top20 == 0.65
    assert adams.ownership_top10 == 0.0  # absent from that tier's exposureCounts entirely
    assert adams.ownership_top1 == 0.0

    # Odell Beckham Jr.: real live finding -- absent from ALL percentile tiers (low-owned enough that
    # zero of the sampled top-20%/10%/1% finishers rostered him), not a null/missing value.
    ob = by_name["Odell Beckham Jr."]
    assert ob.ownership_overall == 0.65
    assert ob.ownership_top20 == 0.0
    assert ob.ownership_top10 == 0.0
    assert ob.ownership_top1 == 0.0

    # Washington Defense: present in every tier including top-1%, a DST chalk play that overperformed.
    wsh = by_name["Washington Defense"]
    assert wsh.position == "D"
    assert wsh.ownership_top1 == 57.14
    assert wsh.stat_details == "3 SACK, 2 DFR,  14-20 Points Allowed "


def test_parse_player_exposures_raises_on_missing_players_key():
    with pytest.raises(ValueError, match="players"):
        parse_player_exposures({"contest": {}, "exposures": {}})


def test_parse_user_exposures_extracts_real_fields():
    payload = _load("resultsdb_contest_data.json")
    rows = parse_user_exposures(payload)
    by_name = {r.username: r for r in rows}

    petr = by_name["PetrGibbons"]
    assert petr.total_rosters == 14
    assert petr.unique_rosters == 14
    assert petr.total_players == 65
    assert petr.roi == -43216.0


def test_fetch_contest_data_reshapes_date_to_yyyymmdd_path_and_raises_on_non_200():
    payload = _load("resultsdb_contest_data.json")
    fake_ok = _FakeSession(payload)
    fetch_contest_data("2023-09-10", 147325137, session=fake_ok)
    call = fake_ok.calls[0]
    assert call["url"] == CONTEST_DATA_URL_TEMPLATE.format(yyyymmdd="20230910", contest_id=147325137)

    fake_missing = _FakeSession({}, status_code=403)
    with pytest.raises(ContestDataUnavailableError, match="date=2018-09-09"):
        fetch_contest_data("2018-09-09", 1, session=fake_missing)


# ---------------------------------------------------------------------------
# lineups (ADR-0032) -- one row per DISTINCT roster, the real dup-count data
# ---------------------------------------------------------------------------


def test_parse_lineups_extracts_real_fields_from_the_dict_keyed_payload():
    # Real, live-confirmed shape (ADR-0032): payload["lineups"] is a DICT keyed by lineupHash, not
    # a flat list -- this fixture is a real sample pulled from a live 2020-09-20 contest.
    payload = _load("resultsdb_lineups.json")
    rows = parse_lineups(payload)
    assert len(rows) == 19

    by_hash = {r.lineup_hash: r for r in rows}
    top = by_hash["26479:33090:51748:75693:8590:54073:4377:6165:8476"]
    assert top.lineup_ct == 1
    assert top.lineup_user_ct == 1
    assert top.points == 248.9
    assert top.total_salary == 49800
    assert top.lineup_rank == 1
    assert top.is_cashing is True
    assert top.lineup_players == {
        "QB1": 26479, "RB1": 33090, "RB2": 51748, "WR1": 75693, "WR2": 8590,
        "WR3": 54073, "TE1": 4377, "FLEX1": 6165, "DST1": 8476,
    }
    assert top.entry_name_list == ["goners"]
    assert isinstance(top.lineup_trends, dict)
    assert "qbPairedWithPassCatcher" in top.lineup_trends


def test_parse_lineups_includes_real_duplicate_groups():
    # The fixture was deliberately sampled to include some lineupCt > 1 rows (real dup groups),
    # not just the lineupCt == 1 majority -- confirm at least one survived the parse with its real
    # count intact.
    payload = _load("resultsdb_lineups.json")
    rows = parse_lineups(payload)
    duplicated = [r for r in rows if r.lineup_ct > 1]
    assert len(duplicated) > 0
    assert all(r.lineup_ct >= r.lineup_user_ct for r in duplicated)  # same user can multi-enter the same roster


def test_parse_lineups_raises_on_missing_lineups_key():
    with pytest.raises(ValueError, match="lineups"):
        parse_lineups({"contest": {}})


def test_fetch_lineups_reshapes_date_to_yyyymmdd_path_and_raises_on_non_200():
    payload = _load("resultsdb_lineups.json")
    fake_ok = _FakeSession(payload)
    fetch_lineups("2020-09-20", 91962454, session=fake_ok)
    call = fake_ok.calls[0]
    assert call["url"] == LINEUPS_URL_TEMPLATE.format(yyyymmdd="20200920", contest_id=91962454)

    fake_missing = _FakeSession({}, status_code=403)
    with pytest.raises(ContestDataUnavailableError, match="date=2018-09-09"):
        fetch_lineups("2018-09-09", 1, session=fake_missing)
