from pathlib import Path

from nfl_dfs.ingestion.footballguys import parse_projection_rows

FIXTURES = Path(__file__).parent / "fixtures" / "footballguys"


def test_parse_qb_rows_extracts_native_id_name_team_position():
    html = (FIXTURES / "qb.html").read_text()
    players = parse_projection_rows(html)

    assert len(players) == 5
    burrow = next(p for p in players if p.name == "Joe Burrow")
    assert burrow.native_id == "BurrJo01"
    assert burrow.team == "CIN"
    assert burrow.position == "QB"


def test_parse_team_defense_rows_use_td_label_not_dst():
    html = (FIXTURES / "td.html").read_text()
    players = parse_projection_rows(html)

    assert len(players) == 5
    jax = next(p for p in players if p.name == "Jacksonville Jaguars")
    assert jax.native_id == "jaxxxx99"
    assert jax.team == "JAX"
    # Raw Footballguys label is "TD", not "DST" -- normalization is the matcher's job, via the
    # position_aliases.py "footballguys": {"TD": "DST"} entry this ingestion pass added.
    assert jax.position == "TD"


def test_parse_rb_wr_te_rows_use_canonical_labels_already():
    for filename, expected_position in [("rb.html", "RB"), ("wr.html", "WR"), ("te.html", "TE")]:
        players = parse_projection_rows((FIXTURES / filename).read_text())
        assert len(players) == 5
        assert all(p.position == expected_position for p in players)


def test_parse_projection_rows_skips_rows_without_a_position_span():
    html = '<tr data-playerid="x1" data-playername="No Position Guy"><td>1</td></tr>'
    assert parse_projection_rows(html) == []
