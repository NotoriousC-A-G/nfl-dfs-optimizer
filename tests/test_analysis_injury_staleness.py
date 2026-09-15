from nfl_dfs.analysis.injury_staleness import compare_injury_sources
from nfl_dfs.ingestion.official_injury_report import OfficialInjuryReportEntry
from nfl_dfs.ingestion.rotogrinders_injuries import InjuryReportEntry
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch


def _identity(canonical_id: str, native_id: str | None, gsis_id: str | None, *, unresolved: bool = False) -> PlayerIdentity:
    if unresolved:
        sources = {"rotogrinders": SourceMatch(native_id=None, method=MatchMethod.UNRESOLVED)}
    elif native_id is not None:
        sources = {"rotogrinders": SourceMatch(native_id=native_id, method=MatchMethod.CROSSWALK)}
    else:
        sources = {}
    return PlayerIdentity(
        canonical_id=canonical_id, display_name=canonical_id, position="WR", team="GB",
        sources=sources, nflverse_gsis_id=gsis_id,
    )


def _rg_entry(native_id: str, status: str) -> InjuryReportEntry:
    return InjuryReportEntry(
        rotogrinders_player_id=native_id, name=native_id, team="GB", position="WR",
        status=status, body_part="Knee", impact_rating=5,
    )


def _official_entry(gsis_id: str, report_status: str | None, week: int = 2) -> OfficialInjuryReportEntry:
    return OfficialInjuryReportEntry(
        gsis_id=gsis_id, season=2026, week=week, team="GB", position="WR", full_name=gsis_id,
        report_status=report_status, practice_status=None, report_primary_injury="Knee",
    )


def test_compare_injury_sources_classifies_agreement():
    identity = _identity("p1", "rg1", "gsis1")
    result = compare_injury_sources([identity], [_rg_entry("rg1", "Q")], [_official_entry("gsis1", "Questionable")])
    assert len(result.agree) == 1
    assert result.disagree == []
    assert result.match_rate == 1.0


def test_compare_injury_sources_classifies_disagreement():
    identity = _identity("p1", "rg1", "gsis1")
    result = compare_injury_sources([identity], [_rg_entry("rg1", "O")], [_official_entry("gsis1", "Questionable")])
    assert len(result.disagree) == 1
    assert result.agree == []
    assert result.match_rate == 0.0


def test_compare_injury_sources_only_rg_when_no_official_status():
    identity = _identity("p1", "rg1", "gsis1")
    result = compare_injury_sources([identity], [_rg_entry("rg1", "Q")], [])
    assert len(result.only_rg) == 1
    assert result.both == []
    assert result.match_rate is None


def test_compare_injury_sources_only_official_when_no_rg_row():
    identity = _identity("p1", "rg1", "gsis1")
    result = compare_injury_sources([identity], [], [_official_entry("gsis1", "Out")])
    assert len(result.only_official) == 1


def test_compare_injury_sources_excludes_unresolved_rotogrinders_match():
    identity = _identity("p1", None, "gsis1", unresolved=True)
    result = compare_injury_sources([identity], [_rg_entry("rg1", "Q")], [_official_entry("gsis1", "Questionable")])
    assert result.agree == result.disagree == result.only_rg == result.only_official == []


def test_compare_injury_sources_excludes_identity_with_no_gsis_id():
    identity = _identity("p1", "rg1", None)
    result = compare_injury_sources([identity], [_rg_entry("rg1", "Q")], [_official_entry("gsis1", "Questionable")])
    assert result.agree == result.disagree == result.only_rg == result.only_official == []


def test_compare_injury_sources_official_status_none_treated_as_no_official_row():
    # A row with a null report_status (e.g. only a practice_status this week) must not count as
    # "on the official report" for coverage/agreement purposes.
    identity = _identity("p1", "rg1", "gsis1")
    result = compare_injury_sources([identity], [_rg_entry("rg1", "Q")], [_official_entry("gsis1", None)])
    assert len(result.only_rg) == 1
    assert result.both == []


def test_compare_injury_sources_unmapped_official_status_never_auto_agrees():
    # An official status not present in OFFICIAL_TO_RG_STATUS (hypothetically a new vocabulary
    # term) must be classified as a disagreement, never silently skipped or auto-matched.
    identity = _identity("p1", "rg1", "gsis1")
    result = compare_injury_sources([identity], [_rg_entry("rg1", "Q")], [_official_entry("gsis1", "SomeNewStatus")])
    assert len(result.disagree) == 1
    assert result.agree == []


def test_injury_source_comparison_match_rate_none_when_nothing_comparable():
    result = compare_injury_sources([], [], [])
    assert result.match_rate is None
    assert result.both == []
