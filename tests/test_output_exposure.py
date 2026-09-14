from nfl_dfs.optimizer.lineup import Lineup
from nfl_dfs.output.exposure import build_exposure_report
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


def _lineup(
    *,
    qb: PlayerProjection,
    wr1: PlayerProjection,
    wr2: PlayerProjection,
    extra: list[PlayerProjection],
    core_stack_team: str,
) -> Lineup:
    """Builds a roster-legal-shaped Lineup for exposure-counting purposes only -- this module
    never re-derives roster legality, so `extra` just needs to bring total player count to 9 with
    plausible positions; exact salary-cap/roster-composition legality isn't exercised here (that's
    optimizer/lineup.py's own test suite's job).
    """
    players = (qb, wr1, wr2, *extra)
    slots = {"QB": qb, "WR1": wr1, "WR2": wr2}
    for i, p in enumerate(extra):
        slots[f"SLOT{i}"] = p
    return Lineup(
        slots=slots,
        players=players,
        total_salary=sum(p.salary for p in players),
        total_projected_points=sum(p.blended_projection for p in players),
        core_stack=frozenset({qb.canonical_id, wr1.canonical_id}),
        core_stack_team=core_stack_team,
    )


def _filler(n: int, team: str = "ZZZ") -> list[PlayerProjection]:
    return [_player(f"filler{team}{i}", "RB", team) for i in range(n)]


# --- empty input -----------------------------------------------------------------------------


def test_empty_lineup_list_produces_empty_report():
    report = build_exposure_report([])
    assert report.lineup_count == 0
    assert report.players == []
    assert report.stacks == []


# --- all-unique-players case -------------------------------------------------------------------


def test_all_unique_players_each_report_count_one_and_full_exposure_pct_is_wrong():
    """Every player appears in exactly one of two lineups that share no players at all -- each
    should report count=1 out of lineup_count=2, i.e. 50% exposure, not 100%."""
    qb1, wr1a, wr1b = _player("qb1", "QB", "AAA"), _player("wr1a", "WR", "AAA"), _player("wr1b", "WR", "AAA")
    qb2, wr2a, wr2b = _player("qb2", "QB", "BBB"), _player("wr2a", "WR", "BBB"), _player("wr2b", "WR", "BBB")

    lineup_1 = _lineup(qb=qb1, wr1=wr1a, wr2=wr1b, extra=_filler(6, "AAA"), core_stack_team="AAA")
    lineup_2 = _lineup(qb=qb2, wr1=wr2a, wr2=wr2b, extra=_filler(6, "BBB"), core_stack_team="BBB")

    report = build_exposure_report([lineup_1, lineup_2])
    assert report.lineup_count == 2
    by_id = {pe.canonical_id: pe for pe in report.players}
    for canonical_id in ("qb1", "wr1a", "wr1b", "qb2", "wr2a", "wr2b"):
        assert by_id[canonical_id].count == 1
        assert by_id[canonical_id].exposure_pct == 0.5

    assert len(report.stacks) == 2
    for se in report.stacks:
        assert se.count == 1
        assert se.exposure_pct == 0.5


# --- repeated-player edge case -----------------------------------------------------------------


def test_repeated_player_across_lineups_is_counted_correctly():
    """A non-stack filler player (e.g. a cheap DST or RB) legitimately repeats across all 3
    lineups even though core stacks are distinct -- exposure must reflect the real repeat count,
    not assume every player is unique."""
    shared_dst = _player("shared_dst", "DST", "ZZZ")

    qb1, wr1 = _player("qb1", "QB", "AAA"), _player("wr1", "WR", "AAA")
    qb2, wr2 = _player("qb2", "QB", "BBB"), _player("wr2", "WR", "BBB")
    qb3, wr3 = _player("qb3", "QB", "CCC"), _player("wr3", "WR", "CCC")

    lineup_1 = _lineup(qb=qb1, wr1=wr1, wr2=_player("x1", "WR", "AAA"), extra=[shared_dst, *_filler(5, "AAA")], core_stack_team="AAA")
    lineup_2 = _lineup(qb=qb2, wr1=wr2, wr2=_player("x2", "WR", "BBB"), extra=[shared_dst, *_filler(5, "BBB")], core_stack_team="BBB")
    lineup_3 = _lineup(qb=qb3, wr1=wr3, wr2=_player("x3", "WR", "CCC"), extra=[shared_dst, *_filler(5, "CCC")], core_stack_team="CCC")

    report = build_exposure_report([lineup_1, lineup_2, lineup_3])
    assert report.lineup_count == 3

    by_id = {pe.canonical_id: pe for pe in report.players}
    assert by_id["shared_dst"].count == 3
    assert by_id["shared_dst"].exposure_pct == 1.0
    assert by_id["qb1"].count == 1
    assert by_id["qb1"].exposure_pct == 1 / 3

    # Players sorted descending by count -- the 3x-repeated DST should be first.
    assert report.players[0].canonical_id == "shared_dst"

    # 3 distinct core stacks -> 3 stack rows, each count=1 -- the "genuinely general, not
    # hardcoded to always-1" counting logic still reports the real (here, trivial) count.
    assert len(report.stacks) == 3
    assert all(se.count == 1 for se in report.stacks)


# --- repeated stack edge case (the task's explicit "not hardcoded" requirement) -----------------


def test_repeated_core_stack_is_counted_not_assumed_unique():
    """optimizer/lineup.py's no-good-cut design means real generated sets never repeat a core
    stack today, but this module must not assume that -- feed it two lineups with the identical
    core_stack/core_stack_team (e.g. a hand-edited or future non-diverse lineup set) and confirm
    the stack exposure count is a real 2, not silently collapsed to 1 or split into two rows.
    """
    qb, wr = _player("qb1", "QB", "AAA"), _player("wr1", "WR", "AAA")
    # Two lineups sharing the identical QB+WR core stack but differing elsewhere.
    lineup_1 = _lineup(qb=qb, wr1=wr, wr2=_player("x1", "WR", "AAA"), extra=_filler(6, "AAA"), core_stack_team="AAA")
    lineup_2 = _lineup(qb=qb, wr1=wr, wr2=_player("x2", "WR", "AAA"), extra=_filler(6, "BBB"), core_stack_team="AAA")

    report = build_exposure_report([lineup_1, lineup_2])
    assert len(report.stacks) == 1
    assert report.stacks[0].count == 2
    assert report.stacks[0].exposure_pct == 1.0


# --- text rendering ----------------------------------------------------------------------------


def test_render_text_includes_player_and_stack_sections():
    qb, wr = _player("qb1", "QB", "AAA", name="Josh Allen"), _player("wr1", "WR", "AAA", name="Stefon Diggs")
    lineup = _lineup(qb=qb, wr1=wr, wr2=_player("x1", "WR", "AAA"), extra=_filler(6, "AAA"), core_stack_team="AAA")
    report = build_exposure_report([lineup])
    text = report.render_text()
    assert "Josh Allen" in text
    assert "Players:" in text
    assert "Stacks:" in text
