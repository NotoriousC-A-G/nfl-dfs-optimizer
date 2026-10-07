from nfl_dfs.ingestion.rotogrinders_injuries import InjuryReportEntry
from nfl_dfs.normalization.injury_lookup import apply_questionable_clearances, overlay_injury_report_exclusions
from nfl_dfs.storage.injury_clearance_store import QuestionableClearance
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


def _named_identity(name, team, dk_id):
    return PlayerIdentity(
        canonical_id=name, display_name=name, position="RB", team=team,
        sources={"draftkings": SourceMatch(native_id=dk_id, method=MatchMethod.NAME_TEAM_POSITION, note=None)},
        flags=[],
    )


def test_clearance_rewrites_a_questionable_player_to_q_cleared():
    ids = [_named_identity("Breece Hall", "NYJ", "1")]
    c = QuestionableClearance(2026, 5, "Breece Hall", "NYJ", "Full")
    out, unmatched = apply_questionable_clearances({"1": "Q"}, ids, [c])
    assert out == {"1": "Q_CLEARED"} and unmatched == []


def test_clearance_can_never_override_doubtful_out_or_ir():
    ids = [_named_identity("A Back", "NYJ", "1"), _named_identity("B Back", "NYJ", "2"), _named_identity("C Back", "NYJ", "3")]
    cs = [QuestionableClearance(2026, 5, n, "NYJ", "Full") for n in ("A Back", "B Back", "C Back")]
    out, unmatched = apply_questionable_clearances({"1": "D", "2": "OUT", "3": "IR"}, ids, cs)
    assert out == {"1": "D", "2": "OUT", "3": "IR"} and len(unmatched) == 3


def test_clearance_for_a_typo_or_non_questionable_player_is_returned_unmatched():
    ids = [_named_identity("Healthy Guy", "NYJ", "1")]
    cs = [QuestionableClearance(2026, 5, "Healthy Guy", "NYJ", "Full"), QuestionableClearance(2026, 5, "Nobody", "NYJ", "Full")]
    out, unmatched = apply_questionable_clearances({}, ids, cs)
    assert out == {} and len(unmatched) == 2


def test_clearance_name_match_ignores_suffix_punctuation_and_case():
    ids = [_named_identity("Kenneth Walker III", "KC", "1")]
    c = QuestionableClearance(2026, 5, "kenneth walker", "kc", "Full")
    out, _ = apply_questionable_clearances({"1": "Q"}, ids, [c])
    assert out == {"1": "Q_CLEARED"}


def test_existing_dk_excluded_status_is_kept_and_input_not_mutated():
    src = {"1": "IR"}
    out = overlay_injury_report_exclusions(src, [_identity("a", "1", "9")], [_entry("9", "D")], EXCLUDED)
    assert out["1"] == "IR" and src == {"1": "IR"}


def test_a_more_severe_vendor_status_is_never_downgraded_by_a_less_severe_one():
    # DK already says OUT; RotoGrinders only says Q -> stays OUT. (Severity only moves up.)
    out = overlay_injury_report_exclusions({"1": "OUT"}, [_identity("a", "1", "9")], [_entry("9", "Q")], EXCLUDED)
    assert out["1"] == "OUT"


def test_vendor_doubtful_beats_dk_questionable_so_a_q_clearance_cannot_resurrect_him():
    ids = [_identity("a", "1", "9")]
    ids[0] = PlayerIdentity(canonical_id="a", display_name="Breece Hall", position="RB", team="NYJ", sources=ids[0].sources, flags=[])
    merged = overlay_injury_report_exclusions({"1": "Q"}, ids, [_entry("9", "D")], EXCLUDED)
    assert merged["1"] == "D"
    out, unmatched = apply_questionable_clearances(merged, ids, [QuestionableClearance(2026, 5, "Breece Hall", "NYJ", "Full")])
    assert out["1"] == "D" and len(unmatched) == 1
