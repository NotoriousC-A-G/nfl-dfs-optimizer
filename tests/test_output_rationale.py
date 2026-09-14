from nfl_dfs.correlation.stack_profile import StackProfile
from nfl_dfs.optimizer.lineup import Lineup
from nfl_dfs.output.rationale import build_lineup_rationale, build_lineup_rationales
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


def _lineup(core_stack_team: str, qb_name: str = "QB Guy", wr_name: str = "WR Guy") -> Lineup:
    qb = _player("qb1", "QB", core_stack_team, name=qb_name)
    wr = _player("wr1", "WR", core_stack_team, name=wr_name)
    filler = [_player(f"filler{i}", "RB", "ZZZ") for i in range(7)]
    players = (qb, wr, *filler)
    slots = {"QB": qb, "WR1": wr}
    for i, p in enumerate(filler):
        slots[f"SLOT{i}"] = p
    return Lineup(
        slots=slots,
        players=players,
        total_salary=sum(p.salary for p in players),
        total_projected_points=sum(p.blended_projection for p in players),
        core_stack=frozenset({"qb1", "wr1"}),
        core_stack_team=core_stack_team,
    )


def _profile(
    home_team: str,
    away_team: str,
    *,
    pivot_to: str | None = "SOME_HOME_ANCHORED_THESIS_TEXT",
    single_team_viability_away: float | None = 42.0,
    game_stack_viability: float | None = 55.0,
    bring_back_status: str = "populated",
) -> StackProfile:
    return StackProfile(
        season=2026,
        week=1,
        home_team=home_team,
        away_team=away_team,
        spread=-3.0,
        single_team_viability_home=80.0,
        single_team_viability_away=single_team_viability_away,
        game_stack_viability=game_stack_viability,
        bring_back_status=bring_back_status,
        pivot_to=pivot_to,
    )


# --- home-anchored match: reuse pivot_to verbatim --------------------------------------------


def test_home_anchored_lineup_reuses_real_pivot_to_text():
    lineup = _lineup(core_stack_team="KC")
    profile = _profile(home_team="KC", away_team="BUF", pivot_to="KC stack: QB + WR, in a strong game.")

    rationale = build_lineup_rationale(lineup, 1, [profile])

    assert "KC stack: QB + WR, in a strong game." in rationale.text
    assert rationale.thesis_is_anchored is True
    assert rationale.stack_profile_game_id == profile.game_id


def test_home_anchored_but_pivot_to_none_states_unavailable_environment():
    lineup = _lineup(core_stack_team="KC")
    profile = _profile(home_team="KC", away_team="BUF", pivot_to=None)

    rationale = build_lineup_rationale(lineup, 1, [profile])

    assert "GameEnvironmentScore was unavailable" in rationale.text
    assert rationale.thesis_is_anchored is True


# --- away-side match: no anchored thesis, no fabrication -------------------------------------


def test_away_side_lineup_does_not_fabricate_a_thesis():
    lineup = _lineup(core_stack_team="BUF")
    profile = _profile(
        home_team="KC",
        away_team="BUF",
        pivot_to="KC stack: QB + WR.",
        single_team_viability_away=37.5,
        game_stack_viability=61.0,
        bring_back_status="populated",
    )

    rationale = build_lineup_rationale(lineup, 1, [profile])

    assert rationale.thesis_is_anchored is False
    assert "No anchor-side StackProfile thesis exists for BUF" in rationale.text
    # The KC-anchored pivot_to text must NOT be presented as BUF's own thesis.
    assert "KC stack: QB + WR." not in rationale.text
    # The game's real, independently-known numbers should still be surfaced.
    assert "37.5" in rationale.text
    assert "61.0" in rationale.text
    assert rationale.stack_profile_game_id == profile.game_id


# --- no matching StackProfile at all: the fallback case -----------------------------------------


def test_no_matching_stack_profile_states_that_plainly():
    lineup = _lineup(core_stack_team="MIA")
    unrelated_profile = _profile(home_team="KC", away_team="BUF")

    rationale = build_lineup_rationale(lineup, 1, [unrelated_profile])

    assert rationale.stack_profile_game_id is None
    assert rationale.thesis_is_anchored is False
    assert "No StackProfile was computed for MIA" in rationale.text
    assert "chosen by the optimizer's blended-projection objective alone" in rationale.text


def test_no_matching_stack_profile_with_empty_profile_list():
    lineup = _lineup(core_stack_team="MIA")
    rationale = build_lineup_rationale(lineup, 1, [])
    assert rationale.stack_profile_game_id is None
    assert "No StackProfile was computed for MIA" in rationale.text


# --- build_lineup_rationales: 1-indexed, one per lineup -----------------------------------------


def test_build_lineup_rationales_is_one_indexed_and_covers_every_lineup():
    lineup_a = _lineup(core_stack_team="KC")
    lineup_b = _lineup(core_stack_team="MIA")
    profile = _profile(home_team="KC", away_team="BUF")

    rationales = build_lineup_rationales([lineup_a, lineup_b], [profile])

    assert [r.lineup_index for r in rationales] == [1, 2]
    assert rationales[0].text.startswith("Lineup 1:")
    assert rationales[1].text.startswith("Lineup 2:")
    assert rationales[0].thesis_is_anchored is True
    assert rationales[1].stack_profile_game_id is None
