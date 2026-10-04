import os
import time
from dataclasses import dataclass

from nfl_dfs.analysis.availability import filter_depth_chart, filter_role_share_results
from nfl_dfs.ingestion.footballguys_ownership import FbgOwnershipRow, parse_roster_percentages
from nfl_dfs.ingestion.nflverse_depth_charts import DepthChartEntry
from nfl_dfs.ingestion.rotogrinders import LineupHqOwnershipRow
from nfl_dfs.ingestion.usage_share import ROLE_RB, PlayerRoleShare, RoleShareResult
from nfl_dfs.normalization.crosswalk import _cache_is_fresh
from nfl_dfs.ownership.blend import blend_ownership_rows, normalize_name
from nfl_dfs.storage.circumstance_cache_store import cache_key


def _row(name, pos, team, own, nid=None):
    return LineupHqOwnershipRow(
        native_id=nid or name, name=name, position=pos, team=team, salary=5000, projected_ownership=own, slate="MAIN"
    )


def test_blend_averages_when_both_sources_have_a_read():
    rows = [_row("Garrett Wilson", "WR", "NYJ", 27.0), _row("Other", "WR", "NYJ", 5.0)]
    out, rep = blend_ownership_rows(rows, [FbgOwnershipRow("Garrett Wilson", "WR", 19.0)])
    assert out[0].projected_ownership == 23.0 and rep.blended == 1 and rep.rg_only == 1


def test_blank_team_uses_fbg_and_unlisted_blank_stays_zero():
    rows = [_row("Aaron Jones", "RB", "MIN", 0.0), _row("Nobody", "WR", "MIN", 0.0), _row("X", "WR", "CHI", 8.0)]
    out, rep = blend_ownership_rows(rows, [FbgOwnershipRow("Aaron Jones Sr.", "RB", 27.1)])
    assert out[0].projected_ownership == 27.1 and out[1].projected_ownership == 0.0
    assert rep.blank_teams == ["MIN"] and rep.fbg_only == 1 and rep.unresolved_blank == 1


def test_name_normalization():
    assert normalize_name("T.J. Hockenson") == normalize_name("TJ Hockenson")
    assert normalize_name("Aaron Jones Sr.") == "aaron jones"


def test_parse_roster_percentages_requires_five_tables():
    table = "<table><tr><td>A B</td><td>1.5%</td></tr></table>"
    rows = parse_roster_percentages(table * 5)
    assert [r.position for r in rows] == ["QB", "RB", "WR", "TE", "DST"]
    try:
        parse_roster_percentages(table * 4)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def _prs(pid, share):
    return PlayerRoleShare(
        player_id=pid, player_name=pid, role=ROLE_RB, weeks_played=3, trailing_volume=40, trailing_team_volume=80,
        trailing_share=share, shrinkage_weight=0.3, role_share_blended=share, role_tier="mid_tier",
        prior_used="league_average",
    )


def test_unavailable_candidate_is_dropped_and_not_left_as_leader():
    top, second = _prs("mason", 0.5), _prs("jones", 0.4)
    result = RoleShareResult(
        season=2026, week=4, team="MIN", role=ROLE_RB, candidates=[top, second], identified=top, gate_passed=True
    )
    (out,) = filter_role_share_results([result], {"mason"})
    assert [c.player_id for c in out.candidates] == ["jones"]
    assert out.identified is None or out.identified.player_id == "jones"
    assert any("mason" in n for n in out.notes)


def test_depth_chart_drops_unavailable_and_promotes_next():
    entries = [
        DepthChartEntry(
            team="CHI", player_id=pid, player_name=pid, position="QB", depth_rank=r, snapshot_at="2026-10-04T06:00:00Z"
        )
        for r, pid in [(1, "williams"), (2, "bagent"), (3, "keenum")]
    ]
    out = filter_depth_chart(entries, {"williams"})
    assert [(e.player_name, e.depth_rank) for e in out] == [("bagent", 1), ("keenum", 2)]


@dataclass
class _Src:
    def circumstance_kind(self):
        return "k"

    def circumstance_team(self):
        return "SF"

    def circumstance_facts(self):
        return {"a": 1}


def test_cache_key_changes_with_extra_freshness_facts_but_not_without():
    s = _Src()
    assert cache_key(s) == cache_key(s, None) == cache_key(s, {})
    assert cache_key(s, {"team_status": [["Evans", None, "Q"]]}) != cache_key(s, {"team_status": [["Evans", None, "O"]]})


def test_crosswalk_cache_expires(tmp_path):
    f = tmp_path / "ids.csv"
    f.write_text("x")
    assert _cache_is_fresh(f, 24)
    old = time.time() - 3 * 86400
    os.utime(f, (old, old))
    assert not _cache_is_fresh(f, 24)
