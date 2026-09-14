import pandas as pd
import pytest

from nfl_dfs.normalization.identity import MatchMethod
from nfl_dfs.normalization.matcher import SourcePlayer, reconcile_week, resolve_player_identity
from nfl_dfs.normalization.name_utils import normalize_name
from nfl_dfs.normalization.registry import PlayerRegistry


def _crosswalk(rows: list[dict]) -> pd.DataFrame:
    columns = ["name", "merge_name", "position", "team", "gsis_id", "pff_id", "pfr_id", "birthdate"]
    return pd.DataFrame([{c: row.get(c) for c in columns} for row in rows], columns=columns)


def _registry(tmp_path) -> PlayerRegistry:
    return PlayerRegistry(path=tmp_path / "registry.json")


def test_gibbs_gibbens_crosswalk_id_match_fails_name_gate_and_falls_back(tmp_path):
    # Mirrors ADR-0013's live finding: a crosswalk-ID probe (pff_id, the only source this
    # implementation actually probes by ID) can point at a real PFF row that is NOT the same
    # player as the crosswalk row it came from. Team+position are deliberately kept matching
    # here so the rejection is isolated to the name-verification gate itself, not conflated
    # with a team/position mismatch that would reject it for an unrelated reason.
    crosswalk = _crosswalk(
        [
            {
                "name": "Jahmyr Gibbs",
                "merge_name": "jahmyrgibbs",
                "position": "RB",
                "team": "DET",
                "gsis_id": "00-1111",
                "pff_id": "122474",
                "pfr_id": "GibbJa01",
                "birthdate": "2002-03-20",
            }
        ]
    )
    dk_player = SourcePlayer(native_id="dk1", name="Jahmyr Gibbs", team="DET", position="RB")
    pff_pool = [
        # The pff_id=122474 row the crosswalk points to -- but it's a different real player.
        SourcePlayer(native_id="122474", name="Jack Gibbens", team="DET", position="RB"),
        # The actual Jahmyr Gibbs row in PFF's own payload, under a different native id.
        SourcePlayer(native_id="999999", name="Jahmyr Gibbs", team="DET", position="RB"),
    ]

    identity = resolve_player_identity(
        dk_player, {"pff": pff_pool, "rotogrinders": [], "footballguys": []}, crosswalk, _registry(tmp_path)
    )

    pff_match = identity.sources["pff"]
    assert pff_match.method == MatchMethod.NAME_TEAM_POSITION
    assert pff_match.native_id == "999999"
    assert "rejected by name-verification gate" in pff_match.note
    assert identity.canonical_id == "00-1111"


def test_clean_fallback_match_with_no_crosswalk_coverage(tmp_path):
    # RotoGrinders has no crosswalk ID column at all (ADR-0013) -- every match is fallback.
    crosswalk = _crosswalk([])
    dk_player = SourcePlayer(native_id="dk2", name="Justin Jefferson", team="MIN", position="WR")
    rg_pool = [SourcePlayer(native_id="rg55", name="Justin Jefferson", team="MIN", position="WR")]

    identity = resolve_player_identity(
        dk_player, {"pff": [], "rotogrinders": rg_pool, "footballguys": []}, crosswalk, _registry(tmp_path)
    )

    rg_match = identity.sources["rotogrinders"]
    assert rg_match.method == MatchMethod.NAME_TEAM_POSITION
    assert rg_match.native_id == "rg55"


def test_team_abbreviation_alias_lets_pff_fallback_match_resolve(tmp_path):
    # PFF's own team code for Cleveland is "CLV", not the canonical "CLE" DK uses.
    crosswalk = _crosswalk([])
    dk_player = SourcePlayer(native_id="dk3", name="Nick Chubb", team="CLE", position="RB")
    pff_pool = [SourcePlayer(native_id="pff77", name="Nick Chubb", team="CLV", position="RB")]

    identity = resolve_player_identity(
        dk_player, {"pff": pff_pool, "rotogrinders": [], "footballguys": []}, crosswalk, _registry(tmp_path)
    )

    assert identity.sources["pff"].method == MatchMethod.NAME_TEAM_POSITION
    assert identity.sources["pff"].native_id == "pff77"


def test_position_alias_lets_pff_hb_match_canonical_rb(tmp_path):
    crosswalk = _crosswalk([])
    dk_player = SourcePlayer(native_id="dk4", name="Chase Brown", team="CIN", position="RB")
    pff_pool = [SourcePlayer(native_id="pff88", name="Chase Brown", team="CIN", position="HB")]

    identity = resolve_player_identity(
        dk_player, {"pff": pff_pool, "rotogrinders": [], "footballguys": []}, crosswalk, _registry(tmp_path)
    )

    assert identity.sources["pff"].method == MatchMethod.NAME_TEAM_POSITION
    assert identity.sources["pff"].native_id == "pff88"


def test_genuine_collision_is_flagged_ambiguous_not_auto_resolved(tmp_path):
    crosswalk = _crosswalk([])
    dk_player = SourcePlayer(native_id="dk5", name="Mike Williams", team="NYJ", position="WR")
    fbg_pool = [
        SourcePlayer(native_id="fbg1", name="Mike Williams", team="NYJ", position="WR"),
        SourcePlayer(native_id="fbg2", name="Mike Williams", team="NYJ", position="WR"),
    ]

    identity = resolve_player_identity(
        dk_player, {"pff": [], "rotogrinders": [], "footballguys": fbg_pool}, crosswalk, _registry(tmp_path)
    )

    match = identity.sources["footballguys"]
    assert match.method == MatchMethod.AMBIGUOUS
    assert match.native_id is None
    assert "name_collision" in match.note
    assert identity.has_ambiguous_matches() is True
    assert any("has_ambiguous_matches" in flag for flag in identity.flags)


def test_collision_disambiguated_by_jersey_number(tmp_path):
    crosswalk = _crosswalk(
        [
            {
                "name": "Mike Williams",
                "merge_name": "mikewilliams",
                "position": "WR",
                "team": "NYJ",
                "gsis_id": "00-2222",
            }
        ]
    )
    dk_player = SourcePlayer(native_id="dk6", name="Mike Williams", team="NYJ", position="WR", jersey_number="81")
    fbg_pool = [
        SourcePlayer(native_id="fbg1", name="Mike Williams", team="NYJ", position="WR", jersey_number="81"),
        SourcePlayer(native_id="fbg2", name="Mike Williams", team="NYJ", position="WR", jersey_number="17"),
    ]

    identity = resolve_player_identity(
        dk_player, {"pff": [], "rotogrinders": [], "footballguys": fbg_pool}, crosswalk, _registry(tmp_path)
    )

    match = identity.sources["footballguys"]
    assert match.method == MatchMethod.NAME_TEAM_POSITION
    assert match.native_id == "fbg1"
    assert "disambiguated by jersey number" in match.note


def test_no_match_is_unresolved_not_dropped(tmp_path):
    crosswalk = _crosswalk([])
    dk_player = SourcePlayer(native_id="dk7", name="Some Rookie", team="ARI", position="WR")

    identity = resolve_player_identity(
        dk_player, {"pff": [], "rotogrinders": [], "footballguys": []}, crosswalk, _registry(tmp_path)
    )

    for source in ("pff", "rotogrinders", "footballguys"):
        assert identity.sources[source].method == MatchMethod.UNRESOLVED
        assert identity.sources[source].native_id is None
    assert set(identity.missing_from_sources()) == {"pff", "rotogrinders", "footballguys"}
    assert any("missing_from_sources" in flag for flag in identity.flags)


def test_uuid_fallback_used_when_crosswalk_has_no_gsis_id(tmp_path):
    crosswalk = _crosswalk([])
    dk_player = SourcePlayer(native_id="dk8", name="Undrafted Rookie", team="SEA", position="RB")

    identity = resolve_player_identity(
        dk_player, {"pff": [], "rotogrinders": [], "footballguys": []}, crosswalk, _registry(tmp_path)
    )

    assert identity.canonical_id.startswith("local_")
    assert identity.nflverse_gsis_id is None


def test_dst_resolution_uses_team_normalization_only(tmp_path):
    crosswalk = _crosswalk([])
    dk_player = SourcePlayer(native_id="dk9", name="Broncos", team="DEN", position="DST")
    pff_pool = [SourcePlayer(native_id="pffdst", name="Broncos", team="DEN", position="DST")]

    identity = resolve_player_identity(
        dk_player, {"pff": pff_pool, "rotogrinders": [], "footballguys": []}, crosswalk, _registry(tmp_path)
    )

    assert identity.canonical_id == "DST_DEN"
    assert identity.sources["pff"].native_id == "pffdst"
    assert identity.sources["rotogrinders"].method == MatchMethod.UNRESOLVED


def test_registry_reuses_canonical_id_across_weeks_for_same_player(tmp_path):
    registry_path = tmp_path / "registry.json"
    crosswalk = _crosswalk([])
    dk_player = SourcePlayer(native_id="dk10", name="Undrafted Rookie Two", team="LAR", position="WR")

    week1_registry = PlayerRegistry(path=registry_path)
    first = resolve_player_identity(
        dk_player, {"pff": [], "rotogrinders": [], "footballguys": []}, crosswalk, week1_registry
    )
    week1_registry.save()

    # A fresh PlayerRegistry instance loaded from the same path (simulating a later week's run)
    # must resolve the same normalized (name, team, position) key to the same canonical id.
    week2_registry = PlayerRegistry(path=registry_path)
    second_id = week2_registry.get_or_create(normalize_name(dk_player.name), "LAR", "WR")
    assert first.canonical_id == second_id


def test_reconcile_week_end_to_end_over_a_small_synthetic_slate(tmp_path):
    crosswalk = _crosswalk(
        [
            {
                "name": "Christian McCaffrey",
                "merge_name": "christianmccaffrey",
                "position": "RB",
                "team": "SF",
                "gsis_id": "00-0033280",
                "pff_id": "11763",
            }
        ]
    )
    dk_pool = [
        SourcePlayer(native_id="1", name="Christian McCaffrey", team="SF", position="RB"),
        SourcePlayer(native_id="2", name="Some Rookie", team="ARI", position="WR"),
    ]
    pff_pool = [SourcePlayer(native_id="11763", name="Christian McCaffrey", team="SF", position="HB")]

    identities = reconcile_week(dk_pool, pff_pool, [], [], crosswalk, PlayerRegistry(path=tmp_path / "reg.json"))

    assert len(identities) == 2
    cmc = next(i for i in identities if i.display_name == "Christian McCaffrey")
    assert cmc.sources["pff"].method == MatchMethod.CROSSWALK
    assert cmc.sources["pff"].native_id == "11763"
    assert cmc.canonical_id == "00-0033280"

    rookie = next(i for i in identities if i.display_name == "Some Rookie")
    assert rookie.canonical_id.startswith("local_")
    assert rookie.missing_from_sources() == ["pff", "rotogrinders", "footballguys"]
