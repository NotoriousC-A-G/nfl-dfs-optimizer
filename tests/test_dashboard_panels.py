"""The dashboard's pipeline-fed tabs: Build lineups (roster order, FLEX/DST placement, splice) and Evidence packets (iframe splice)."""

from __future__ import annotations

import pytest

from nfl_dfs.dashboard.panels import render_build_panel, render_packets_panel, roster_slots, splice_panel
from nfl_dfs.dashboard.renderer import render_dashboard_html
from nfl_dfs.output.weekly_output import build_weekly_output


def _p(pid, pos, salary=5000):
    return {"id": pid, "name": pid, "team": "AAA", "position": pos, "salary": salary, "proj": 10.0, "own": 5.0, "opp": "BBB"}


def test_roster_slots_are_in_dk_order_with_flex_before_dst():
    players = [_p("d", "DST", 2500), _p("w4", "WR", 3000), _p("w1", "WR", 9000), _p("w2", "WR", 8000), _p("w3", "WR", 7000),
               _p("r1", "RB", 8000), _p("r2", "RB", 6000), _p("q", "QB", 6000), _p("t", "TE", 4000)]
    assert [s for s, _ in roster_slots(players)] == ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
    assert dict((p["id"], s) for s, p in roster_slots(players))["w4"] == "FLEX"


def _build():
    ids = ["q", "r1", "r2", "w1", "w2", "w3", "t", "w4", "DST_AAA"]
    return {"created_at": "2026-10-24T13:16:54", "snapshot_filename": "snap.json", "expert": {"unavailable": {}, "portfolio_notes": "notes"},
            "agents": {"a1": {"status": "built", "floor_lean": 0.0, "spend_plan": {"RB": "value"}, "warnings": [],
                              "build_thesis": {"reason": "because", "backs": ["G:b1"], "avoids": []},
                              "pools_used": [[("q", "core", "the bet")]],
                              "lineups": [{"views": ["G:b1"], "stack": ["q"], "note": "n", "player_ids": ids, "salary": 49900, "projected": 150.0,
                                           "tiers": {"core": 1}, "unresolved_players": []}]}}}


def test_build_panel_lists_dst_last_and_unknown_dst_gets_a_label():
    table = {pid: {"name": pid, "team": "AAA", "position": pos, "salary": 5000, "proj": 10.0, "own": 5.0, "opp": "BBB"}
             for pid, pos in [("q", "QB"), ("r1", "RB"), ("r2", "RB"), ("w1", "WR"), ("w2", "WR"), ("w3", "WR"), ("t", "TE"), ("w4", "WR")]}
    html = render_build_panel(_build(), table)
    assert html.index(">FLEX<") < html.index(">DST<")
    assert "AAA DST" in html and "the bet" in html and "$49,900" in html


def test_splice_replaces_only_the_named_panel():
    from nfl_dfs.output.weekly_output import WeeklyOutput  # noqa: F401  (empty output renders fine)
    dash = render_dashboard_html(build_weekly_output([], [], {}), [])
    assert "No build yet" in dash and "No evidence packets yet" in dash
    dash = splice_panel(dash, "build", "<p>BUILD</p>")
    dash = splice_panel(dash, "packets", render_packets_panel("<html>x &amp; y</html>"))
    assert "<p>BUILD</p>" in dash and "No build yet" not in dash and "srcdoc=" in dash and "&lt;html&gt;" in dash
    assert splice_panel(dash, "build", "<p>AGAIN</p>").count("<p>BUILD</p>") == 0


def test_splice_without_markers_fails_loudly():
    with pytest.raises(ValueError):
        splice_panel("<html></html>", "build", "x")
