from nfl_dfs.ingestion.rotogrinders_injuries import InjuryReportEntry
from nfl_dfs.ingestion.official_injury_report import OfficialInjuryReportEntry
from nfl_dfs.normalization.injury_lookup import overlay_injury_report_exclusions, resolve_questionable_players
from nfl_dfs.storage.injury_clearance_store import QuestionableOverride
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch

EXCLUDED = frozenset({"IR", "OUT", "D", "Q"})


def _identity(cid, dk_id, rg_id):
    return PlayerIdentity(
        canonical_id=cid, display_name=cid, position="RB", team="NYJ",
        sources={
            "draftkings": SourceMatch(native_id=dk_id, method=MatchMethod.NAME_TEAM_POSITION, note=None),
            "rotogrinders": SourceMatch(native_id=rg_id, method=MatchMethod.NAME_TEAM_POSITION, note=None),
        },
        flags=[],
    )


def _entry(rg_id, status):
    return InjuryReportEntry(rotogrinders_player_id=rg_id, name="x", team="NYJ", position="RB",
                             status=status, body_part="Thigh", impact_rating=5)


def test_rotogrinders_doubtful_overrides_dk_questionable():
    out = overlay_injury_report_exclusions({"1": "Q"}, [_identity("a", "1", "9")], [_entry("9", "D")], EXCLUDED)
    assert out["1"] == "D"


def test_rotogrinders_out_and_questionable_both_map_into_the_dk_vocabulary():
    ids = [_identity("a", "1", "9"), _identity("b", "2", "8")]
    out = overlay_injury_report_exclusions({}, ids, [_entry("9", "O"), _entry("8", "Q")], EXCLUDED)
    assert out == {"1": "OUT", "2": "Q"}


def _named_identity(name, team, dk_id, gsis=None):
    return PlayerIdentity(
        canonical_id=gsis or name, display_name=name, position="RB", team=team,
        sources={"draftkings": SourceMatch(native_id=dk_id, method=MatchMethod.NAME_TEAM_POSITION, note=None)},
        flags=[],
    )


FULL = "Full Participation in Practice"
LIMITED = "Limited Participation in Practice"
DNP = "Did Not Participate In Practice"


def _official(gsis, practice, week=5, team="NYJ"):
    return OfficialInjuryReportEntry(gsis_id=gsis, season=2026, week=week, team=team, position="RB",
                                     full_name="x", report_status=None, practice_status=practice, report_primary_injury=None)


def _resolve(status, identities, official=(), overrides=(), week=5):
    return resolve_questionable_players(status, identities, list(official), list(overrides), week=week, as_of="2026-10-09T18:00Z")


def test_q_player_with_full_official_practice_is_cleared_automatically():
    ids = [_named_identity("Breece Hall", "NYJ", "1", gsis="00-1")]
    status, decisions, unmatched = _resolve({"1": "Q"}, ids, [_official("00-1", FULL)])
    assert status == {"1": "Q_CLEARED"} and unmatched == []
    assert decisions[0].decision == "cleared" and "Full" in decisions[0].basis and decisions[0].source == "official_practice"


def test_q_player_with_limited_dnp_or_no_evidence_stays_excluded_with_a_stated_reason():
    ids = [_named_identity("Lim", "NYJ", "1", "00-1"), _named_identity("Dnp", "NYJ", "2", "00-2"), _named_identity("Nobody", "NYJ", "3", "00-3")]
    status, decisions, _ = _resolve({"1": "Q", "2": "Q", "3": "Q"}, ids, [_official("00-1", LIMITED), _official("00-2", DNP)])
    assert status == {"1": "Q", "2": "Q", "3": "Q"}
    by = {d.name: d for d in decisions}
    assert all(d.decision == "excluded" for d in decisions)
    assert "Limited" in by["Lim"].basis and "Did Not Participate" in by["Dnp"].basis and "no official practice evidence" in by["Nobody"].basis


def test_only_the_target_weeks_official_rows_count():
    ids = [_named_identity("Hall", "NYJ", "1", "00-1")]
    status, _, _ = _resolve({"1": "Q"}, ids, [_official("00-1", FULL, week=4)], week=5)
    assert status == {"1": "Q"}  # last week's Full practice is not evidence for this week


def test_override_clear_beats_a_limited_practice_and_records_the_note():
    ids = [_named_identity("Breece Hall", "NYJ", "1", "00-1")]
    o = QuestionableOverride(2026, 5, "Breece Hall", "NYJ", "clear", note="Rapoport: expects to play")
    status, decisions, _ = _resolve({"1": "Q"}, ids, [_official("00-1", LIMITED)], [o])
    assert status == {"1": "Q_CLEARED"}
    assert decisions[0].source == "override" and "Rapoport" in decisions[0].basis


def test_override_bar_excludes_even_a_fully_practicing_or_healthy_player():
    ids = [_named_identity("Hall", "NYJ", "1", "00-1"), _named_identity("Healthy", "NYJ", "2", "00-2")]
    os_ = [QuestionableOverride(2026, 5, "Hall", "NYJ", "bar"), QuestionableOverride(2026, 5, "Healthy", "NYJ", "bar", note="soft tissue")]
    status, decisions, _ = _resolve({"1": "Q"}, ids, [_official("00-1", FULL)], os_)
    assert status == {"1": "BARRED", "2": "BARRED"}
    assert {d.decision for d in decisions} == {"barred"}


def test_override_clear_can_revive_doubtful_but_never_out_or_ir():
    ids = [_named_identity("Dee", "NYJ", "1"), _named_identity("Oh", "NYJ", "2"), _named_identity("Eye", "NYJ", "3")]
    os_ = [QuestionableOverride(2026, 5, n, "NYJ", "clear") for n in ("Dee", "Oh", "Eye")]
    status, _, unmatched = _resolve({"1": "D", "2": "OUT", "3": "IR"}, ids, [], os_)
    assert status == {"1": "Q_CLEARED", "2": "OUT", "3": "IR"}
    assert [u.name for u in unmatched] == ["Oh", "Eye"]


def test_vendor_doubtful_beats_dk_questionable_so_automatic_clearance_never_applies_to_him():
    ids = [PlayerIdentity(canonical_id="00-1", display_name="Breece Hall", position="RB", team="NYJ",
                          sources=_identity("a", "1", "9").sources, flags=[])]
    merged = overlay_injury_report_exclusions({"1": "Q"}, ids, [_entry("9", "D")], EXCLUDED)
    assert merged["1"] == "D"
    status, decisions, _ = _resolve(merged, ids, [_official("00-1", FULL)])
    assert status["1"] == "D" and decisions == []  # not Q anymore -> the Q rule never sees him


def test_clearance_name_match_for_overrides_ignores_suffix_punctuation_and_case():
    ids = [_named_identity("Kenneth Walker III", "KC", "1")]
    status, _, _ = _resolve({"1": "Q"}, ids, [], [QuestionableOverride(2026, 5, "kenneth walker", "kc", "clear")])
    assert status == {"1": "Q_CLEARED"}


def test_an_override_for_an_unknown_player_is_returned_unmatched():
    ids = [_named_identity("Hall", "NYJ", "1")]
    _, _, unmatched = _resolve({}, ids, [], [QuestionableOverride(2026, 5, "Nobody", "NYJ", "bar")])
    assert len(unmatched) == 1


def test_a_more_severe_vendor_status_is_never_downgraded_by_a_less_severe_one():
    # DK already says OUT; RotoGrinders only says Q -> stays OUT. (Severity only moves up.)
    out = overlay_injury_report_exclusions({"1": "OUT"}, [_identity("a", "1", "9")], [_entry("9", "Q")], EXCLUDED)
    assert out["1"] == "OUT"
