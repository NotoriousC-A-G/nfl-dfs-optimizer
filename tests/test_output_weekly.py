from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch
from nfl_dfs.optimizer.lineup import Lineup
from nfl_dfs.output.weekly_output import build_weekly_output
from nfl_dfs.projection.blend import PlayerProjection


def _player(canonical_id: str, position: str, team: str, salary: int = 5000, name: str | None = None) -> PlayerProjection:
    return PlayerProjection(
        canonical_id=canonical_id,
        display_name=name or canonical_id,
        position=position,
        team=team,
        salary=salary,
        blended_projection=10.0,
        source_count=1,
        source_values={"rotogrinders": 10.0},
    )


def _identity_for(p: PlayerProjection) -> PlayerIdentity:
    return PlayerIdentity(
        canonical_id=p.canonical_id,
        display_name=p.display_name,
        position=p.position,
        team=p.team,
        sources={
            "draftkings": SourceMatch(
                native_id=f"{p.canonical_id}-dkid", method=MatchMethod.NAME_TEAM_POSITION
            )
        },
    )


def _lineup() -> Lineup:
    qb = _player("qb1", "QB", "AAA", name="QB Guy")
    wr = _player("wr1", "WR", "AAA", name="WR Guy")
    rb1 = _player("rb1", "RB", "AAA", name="RB One")
    rb2 = _player("rb2", "RB", "BBB", name="RB Two")
    wr2 = _player("wr2", "WR", "BBB", name="WR Two")
    wr3 = _player("wr3", "WR", "CCC", name="WR Three")
    te = _player("te1", "TE", "AAA", name="TE One")
    flex = _player("flex1", "RB", "DDD", name="Flex")
    dst = _player("dst1", "DST", "EEE", name="Defense")

    slots = {
        "QB": qb,
        "RB1": rb1,
        "RB2": rb2,
        "WR1": wr,
        "WR2": wr2,
        "WR3": wr3,
        "TE": te,
        "FLEX": flex,
        "DST": dst,
    }
    players = tuple(slots.values())
    return Lineup(
        slots=slots,
        players=players,
        total_salary=sum(p.salary for p in players),
        total_projected_points=sum(p.blended_projection for p in players),
        core_stack=frozenset({"qb1", "wr1"}),
        core_stack_team="AAA",
    )


def test_build_weekly_output_bundles_all_three_artifacts_with_no_stack_profiles():
    lineup = _lineup()
    identities = [_identity_for(p) for p in lineup.players]

    result = build_weekly_output([lineup], identities)

    assert result.lineups == [lineup]
    assert result.exposure_report.lineup_count == 1
    assert len(result.rationales) == 1
    assert "No StackProfile was computed" in result.rationales[0].text
    assert "QB Guy (qb1-dkid)" in result.dk_csv.csv_text
    assert result.dk_csv.lineup_count == 1

    summary = result.render_text_summary()
    assert "Players:" in summary
    assert "Rationales:" in summary
