from nfl_dfs.ingestion.rotogrinders_injuries import InjuryReportEntry
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch
from nfl_dfs.normalization.injury_lookup import build_injury_lookup, team_injuries


def _identity(canonical_id: str, team: str, rg_native_id: str | None, method: MatchMethod) -> PlayerIdentity:
    return PlayerIdentity(
        canonical_id=canonical_id,
        display_name=f"Player {canonical_id}",
        position="RB",
        team=team,
        sources={"rotogrinders": SourceMatch(native_id=rg_native_id, method=method)},
    )


def _injury(rg_id: str, status: str = "Q", impact: int = 5) -> InjuryReportEntry:
    return InjuryReportEntry(
        rotogrinders_player_id=rg_id,
        name="Some Player",
        team="GBP",
        position="RB",
        status=status,
        body_part="Knee",
        impact_rating=impact,
    )


def test_build_injury_lookup_joins_on_existing_rotogrinders_native_id():
    identities = [
        _identity("gsis_1", "GB", "973101", MatchMethod.NAME_TEAM_POSITION),
        _identity("gsis_2", "GB", "6228166", MatchMethod.NAME_TEAM_POSITION),
    ]
    injuries = [_injury("973101", status="O", impact=10)]

    lookup = build_injury_lookup(identities, injuries)

    assert lookup.keys() == {"gsis_1"}
    assert lookup["gsis_1"].status == "O"


def test_build_injury_lookup_skips_unresolved_rotogrinders_matches():
    identities = [_identity("gsis_1", "GB", None, MatchMethod.UNRESOLVED)]
    injuries = [_injury("973101")]

    assert build_injury_lookup(identities, injuries) == {}


def test_build_injury_lookup_ignores_players_not_on_the_injury_report():
    identities = [_identity("gsis_1", "GB", "999999", MatchMethod.NAME_TEAM_POSITION)]
    injuries = [_injury("973101")]

    assert build_injury_lookup(identities, injuries) == {}


def test_team_injuries_filters_to_the_requested_team_only():
    identities = [
        _identity("gsis_1", "GB", "973101", MatchMethod.NAME_TEAM_POSITION),
        _identity("gsis_2", "LV", "6228166", MatchMethod.NAME_TEAM_POSITION),
    ]
    injuries = [_injury("973101", status="Q"), _injury("6228166", status="O")]

    gb_only = team_injuries("GB", identities, injuries)

    assert len(gb_only) == 1
    identity, injury = gb_only[0]
    assert identity.canonical_id == "gsis_1"
    assert injury.status == "Q"
