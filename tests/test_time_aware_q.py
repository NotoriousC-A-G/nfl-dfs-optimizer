from datetime import datetime, timezone

import pytest

from nfl_dfs.ingestion.official_injury_report import OfficialInjuryReportEntry
from nfl_dfs.normalization.injury_lookup import (
    CLEARED_QUESTIONABLE_STATUS, UNRESOLVED_STATUS, practice_history, report_stage, resolve_questionable_players, time_aware_decision,
)
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity
from nfl_dfs.storage.official_injury_snapshot_store import OfficialInjurySnapshot

FULL, LIM, DNP = "Full Participation in Practice", "Limited Participation in Practice", "Did Not Participate In Practice"


def _utc(y, m, d, h):  # 2026-10-07 is a Wednesday
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_the_report_stage_follows_the_eastern_weekday():
    assert report_stage(_utc(2026, 10, 7, 15)) == "early"      # Wednesday
    assert report_stage(_utc(2026, 10, 8, 12)) == "midweek"    # Thursday morning
    assert report_stage(_utc(2026, 10, 9, 13)) == "final"      # Friday
    assert report_stage(_utc(2026, 10, 10, 15)) == "gameday"   # Saturday
    assert report_stage(_utc(2026, 10, 11, 14)) == "gameday"   # Sunday
    assert report_stage(_utc(2026, 10, 6, 15)) == "early"      # Tuesday
    assert report_stage(_utc(2026, 10, 9, 3)) == "midweek"     # 11pm Thursday Eastern is still Thursday


H = lambda *pairs: [(d, p, g) for d, p, g in pairs]


def test_early_in_the_week_limited_a_first_dnp_or_no_report_are_unresolved_not_out():
    for hist in (H(("2026-10-07", LIM, None)), H(("2026-10-07", DNP, None)), []):
        d, basis, _ = time_aware_decision(hist, "early")
        assert d == "unresolved" and "too early" in basis
    assert time_aware_decision(H(("2026-10-07", FULL, None)), "early")[0] == "cleared"


def test_midweek_two_dnp_report_days_is_out_one_is_still_unresolved():
    two = H(("2026-10-07", DNP, None), ("2026-10-08", DNP, None))
    d, basis, _ = time_aware_decision(two, "midweek")
    assert d == "excluded" and "2 report days" in basis and "10-07 DNP -> 10-08 DNP" in basis
    assert time_aware_decision(H(("2026-10-08", DNP, None)), "midweek")[0] == "unresolved"
    assert time_aware_decision(H(("2026-10-07", DNP, None), ("2026-10-08", LIM, None)), "midweek")[0] == "unresolved"  # improving
    assert time_aware_decision(H(("2026-10-07", LIM, None), ("2026-10-08", DNP, None)), "midweek")[0] == "unresolved"


def test_by_the_friday_report_the_original_rule_applies_full_clears_everything_else_is_out():
    assert time_aware_decision(H(("2026-10-09", FULL, "Questionable")), "final")[0] == "cleared"
    lim = time_aware_decision(H(("2026-10-08", LIM, None), ("2026-10-09", LIM, "Questionable")), "final")
    assert lim[0] == "excluded" and "positive report" in lim[1]
    assert time_aware_decision(H(("2026-10-09", DNP, "Questionable")), "final")[0] == "excluded"
    none = time_aware_decision([], "final")
    assert none[0] == "excluded" and none[2] == "default"
    assert time_aware_decision(H(("2026-10-11", LIM, None)), "gameday")[0] == "excluded"


def test_an_official_out_or_doubtful_game_status_is_out_at_any_stage():
    for stage in ("early", "midweek", "final"):
        assert time_aware_decision(H(("2026-10-07", LIM, "Out")), stage)[0] == "excluded"
        assert time_aware_decision(H(("2026-10-07", FULL, "Doubtful")), stage)[0] == "excluded"  # game status trumps a full practice


def _entry(gsis, practice, game=None, week=5):
    return OfficialInjuryReportEntry(gsis, 2026, week, "MIN", "WR", "X Y", game, practice, None)


def test_practice_history_keeps_the_last_capture_of_each_eastern_day_oldest_first():
    s1 = OfficialInjurySnapshot("2026-10-07T20:00:00+00:00", 2026, 5, [_entry("g1", DNP)])
    s2 = OfficialInjurySnapshot("2026-10-08T12:00:00+00:00", 2026, 5, [_entry("g1", LIM)])
    s3 = OfficialInjurySnapshot("2026-10-08T22:00:00+00:00", 2026, 5, [_entry("g1", FULL)])  # later the same day wins
    h = practice_history([s3, s1, s2])
    assert h["g1"] == [("2026-10-07", DNP, None), ("2026-10-08", FULL, None)]


def test_resolve_uses_the_time_aware_path_only_when_a_stage_is_given():
    from nfl_dfs.normalization.identity import SourceMatch
    ident = PlayerIdentity("g1", "Pat Q", "WR", "MIN", nflverse_gsis_id="g1", sources={"draftkings": SourceMatch("77", MatchMethod.NAME_TEAM_POSITION)})
    entries = [_entry("g1", LIM)]
    hist = {"g1": [("2026-10-07", LIM, None)]}
    legacy, dec, _ = resolve_questionable_players({"77": "Q"}, [ident], entries, [], week=5)
    assert legacy["77"] == "Q" and dec[0].decision == "excluded"  # unchanged legacy behaviour
    early, dec2, _ = resolve_questionable_players({"77": "Q"}, [ident], entries, [], week=5, history=hist, stage="early")
    assert early["77"] == UNRESOLVED_STATUS and dec2[0].decision == "unresolved"
    final, dec3, _ = resolve_questionable_players({"77": "Q"}, [ident], entries, [], week=5, history=hist, stage="final")
    assert final["77"] == "Q" and dec3[0].decision == "excluded"
    full, dec4, _ = resolve_questionable_players({"77": "Q"}, [ident], [_entry("g1", FULL)], [], week=5, history={"g1": [("d", FULL, None)]}, stage="early")
    assert full["77"] == CLEARED_QUESTIONABLE_STATUS


def test_an_unresolved_player_is_available_and_his_work_is_not_treated_as_vacated():
    from nfl_dfs.optimizer.lineup import EXCLUDED_INJURY_STATUSES
    assert UNRESOLVED_STATUS not in EXCLUDED_INJURY_STATUSES and CLEARED_QUESTIONABLE_STATUS not in EXCLUDED_INJURY_STATUSES
    from nfl_dfs.build.evidence.builder import _unavailable_status
    from nfl_dfs.normalization.injury_lookup import AvailabilityDecision
    rec = {"identity": {"display_name": "Pat Q"}, "team": "MIN", "injury": {"status": "Q"}}
    unresolved = AvailabilityDecision("Pat Q", "MIN", "unresolved", "too early", "official_practice")
    assert _unavailable_status(rec, {("Pat Q", "MIN"): unresolved}) is None
    out = AvailabilityDecision("Pat Q", "MIN", "excluded", "DNP", "official_practice")
    assert _unavailable_status(rec, {("Pat Q", "MIN"): out}) == "excluded"
