import csv
import io

import pytest

from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch
from nfl_dfs.optimizer.lineup import Lineup
from nfl_dfs.output.csv_export import (
    DK_ROSTER_COLUMNS,
    MissingDraftKingsIdError,
    dk_ids_from_identities,
    export_lineups_to_dk_csv,
    format_player_cell,
    lineup_to_dk_row,
)
from nfl_dfs.projection.blend import PlayerProjection


def _identity(canonical_id: str, dk_native_id: str | None) -> PlayerIdentity:
    sources = {}
    if dk_native_id is not None:
        sources["draftkings"] = SourceMatch(native_id=dk_native_id, method=MatchMethod.NAME_TEAM_POSITION)
    return PlayerIdentity(
        canonical_id=canonical_id,
        display_name=canonical_id,
        position="WR",
        team="AAA",
        sources=sources,
    )


def _player(canonical_id: str, position: str, team: str, salary: int, name: str | None = None) -> PlayerProjection:
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


def _lineup() -> Lineup:
    """A synthetic, roster-legal 9-player lineup with distinct, easily-traced canonical_ids."""
    qb = _player("qb1", "QB", "AAA", 7000, "Pat Mahomes")
    rb1 = _player("rb1", "RB", "AAA", 6000, "Lead Back")
    rb2 = _player("rb2", "RB", "BBB", 5000, "Second Back")
    wr1 = _player("wr1", "WR", "AAA", 6500, "Top Target")
    wr2 = _player("wr2", "WR", "BBB", 5500, "Number Two")
    wr3 = _player("wr3", "WR", "CCC", 4000, "Slot Guy")
    te1 = _player("te1", "TE", "AAA", 4000, "Big Tight End")
    flex = _player("flex1", "RB", "DDD", 3500, "Flex Back")
    dst = _player("dst1", "DST", "EEE", 2500, "Some Defense")

    slots = {
        "QB": qb,
        "RB1": rb1,
        "RB2": rb2,
        "WR1": wr1,
        "WR2": wr2,
        "WR3": wr3,
        "TE": te1,
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


def _identities_for(lineup: Lineup) -> list[PlayerIdentity]:
    """One resolved DraftKings-sourced PlayerIdentity per player in `lineup`, native_id derived
    deterministically from canonical_id (e.g. "qb1" -> "1000001") purely so tests can assert on
    exact expected cell text.
    """
    return [
        _identity(p.canonical_id, dk_native_id=f"{p.canonical_id}-dkid")
        for p in lineup.players
    ]


# --- format_player_cell / dk_ids_from_identities --------------------------------------------


def test_format_player_cell_matches_confirmed_dk_convention():
    # Confirmed against a real exported DKSalaries.csv row (module docstring): "Name (ID)".
    assert format_player_cell("Rory McIlroy", "17527360") == "Rory McIlroy (17527360)"


def test_dk_ids_from_identities_skips_unresolved_and_missing_sources():
    resolved = _identity("p1", dk_native_id="111")
    unresolved = _identity("p2", dk_native_id=None)  # sources dict has no "draftkings" key at all
    no_native_id = PlayerIdentity(
        canonical_id="p3",
        display_name="p3",
        position="WR",
        team="AAA",
        sources={"draftkings": SourceMatch(native_id=None, method=MatchMethod.UNRESOLVED)},
    )
    result = dk_ids_from_identities([resolved, unresolved, no_native_id])
    assert result == {"p1": "111"}


# --- lineup_to_dk_row / export_lineups_to_dk_csv --------------------------------------------


def test_lineup_to_dk_row_orders_cells_qb_rb_rb_wr_wr_wr_te_flex_dst():
    lineup = _lineup()
    ids = {p.canonical_id: f"{p.canonical_id}-dkid" for p in lineup.players}
    row = lineup_to_dk_row(lineup, ids)
    assert row == [
        "Pat Mahomes (qb1-dkid)",
        "Lead Back (rb1-dkid)",
        "Second Back (rb2-dkid)",
        "Top Target (wr1-dkid)",
        "Number Two (wr2-dkid)",
        "Slot Guy (wr3-dkid)",
        "Big Tight End (te1-dkid)",
        "Flex Back (flex1-dkid)",
        "Some Defense (dst1-dkid)",
    ]


def test_lineup_to_dk_row_raises_on_missing_dk_id():
    lineup = _lineup()
    ids = {p.canonical_id: f"{p.canonical_id}-dkid" for p in lineup.players if p.canonical_id != "dst1"}
    with pytest.raises(MissingDraftKingsIdError, match="Some Defense"):
        lineup_to_dk_row(lineup, ids)


def test_export_header_matches_dk_confirmed_roster_column_order():
    assert DK_ROSTER_COLUMNS == ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")


def test_export_produces_one_header_row_and_one_row_per_lineup():
    lineup = _lineup()
    identities = _identities_for(lineup)
    export = export_lineups_to_dk_csv([lineup, lineup], identities)

    rows = list(csv.reader(io.StringIO(export.csv_text)))
    assert rows[0] == list(DK_ROSTER_COLUMNS)
    assert len(rows) == 3  # header + 2 lineup rows
    assert rows[1] == rows[2]  # same lineup passed twice -> identical rows
    assert rows[1][0] == "Pat Mahomes (qb1-dkid)"
    assert export.lineup_count == 2
    assert export.notes  # confidence/scope notes are always present, never silently omitted


def test_export_with_include_contest_columns_prepends_empty_leading_columns():
    lineup = _lineup()
    identities = _identities_for(lineup)
    export = export_lineups_to_dk_csv([lineup], identities, include_contest_columns=True)

    rows = list(csv.reader(io.StringIO(export.csv_text)))
    assert rows[0][:4] == ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
    assert rows[0][4:] == list(DK_ROSTER_COLUMNS)
    assert rows[1][:4] == ["", "", "", ""]  # never fabricated -- see module docstring
    assert rows[1][4] == "Pat Mahomes (qb1-dkid)"


def test_export_raises_when_a_player_has_no_dk_identity_at_all():
    lineup = _lineup()
    identities = [i for i in _identities_for(lineup) if i.canonical_id != "wr2"]
    with pytest.raises(MissingDraftKingsIdError, match="Number Two"):
        export_lineups_to_dk_csv([lineup], identities)
