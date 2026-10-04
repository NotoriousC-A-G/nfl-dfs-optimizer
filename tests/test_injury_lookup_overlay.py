from nfl_dfs.ingestion.rotogrinders_injuries import InjuryReportEntry
from nfl_dfs.normalization.injury_lookup import overlay_injury_report_exclusions
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch

EXCLUDED = frozenset({"IR", "OUT", "D"})


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


def test_rotogrinders_out_maps_to_dk_out_and_questionable_is_ignored():
    ids = [_identity("a", "1", "9"), _identity("b", "2", "8")]
    out = overlay_injury_report_exclusions({}, ids, [_entry("9", "O"), _entry("8", "Q")], EXCLUDED)
    assert out == {"1": "OUT"}


def test_existing_dk_excluded_status_is_kept_and_input_not_mutated():
    src = {"1": "IR"}
    out = overlay_injury_report_exclusions(src, [_identity("a", "1", "9")], [_entry("9", "D")], EXCLUDED)
    assert out["1"] == "IR" and src == {"1": "IR"}
