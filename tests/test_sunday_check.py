from nfl_dfs.analysis.sunday_check import (
    StatusReading, check_lineup, classify_dk, classify_official, classify_override, classify_rotogrinders,
)
from nfl_dfs.ingestion.official_injury_report import OfficialInjuryReportEntry


def _off(report=None, practice=None):
    return OfficialInjuryReportEntry("00-1", 2026, 5, "NYJ", "RB", "x", report, practice, None)


def test_dk_classification_including_the_literal_none_string():
    assert classify_dk("OUT").severity == 3 and classify_dk("IR").severity == 3
    assert classify_dk("D").severity == 2 and classify_dk("Q").severity == 1
    assert classify_dk("Q_CLEARED").severity == 1  # cleared Friday, still watched Sunday
    assert classify_dk("None") is None and classify_dk(None) is None and classify_dk("") is None


def test_official_game_status_and_dnp_without_game_status():
    assert classify_official(_off("Out", "Did Not Participate In Practice")).severity == 3
    assert classify_official(_off("Doubtful")).severity == 2
    assert classify_official(_off("Questionable", "Limited Participation in Practice")).severity == 1
    assert classify_official(_off(None, "Did Not Participate In Practice")).severity == 1
    assert classify_official(_off(None, "Full Participation in Practice")) is None
    assert classify_official(_off(None, "Limited Participation in Practice")) is None
    assert classify_official(None) is None


def test_rotogrinders_and_override_classification():
    assert [classify_rotogrinders(s).severity for s in ("O", "D", "Q")] == [3, 2, 1]
    assert classify_rotogrinders("X") is None and classify_rotogrinders(None) is None
    assert classify_override("bar", "soft tissue").severity == 3
    assert classify_override("clear") is None


def _pool_row(name, team, pos, sal, proj, own=5.0):
    return {"identity": {"display_name": name, "canonical_id": f"id-{name}"}, "team": team, "position": pos,
            "salary": sal, "projection": proj, "ownership": {"projected_ownership": own}, "slate_window": "early"}


def _lineup():
    return [
        {"name": "QB One", "team": "AAA", "position": "QB", "slot": "QB", "salary": 6000, "canonical_id": "id-QB One"},
        {"name": "Back Hurt", "team": "BBB", "position": "RB", "slot": "RB", "salary": 6000, "canonical_id": "id-Back Hurt"},
        {"name": "Flex WR", "team": "CCC", "position": "WR", "slot": "FLEX", "salary": 5000, "canonical_id": "id-Flex WR"},
        {"name": "Def", "team": "DDD", "position": "DST", "slot": "DST", "salary": 3000, "canonical_id": "DST_DDD"},
    ]


def test_flagged_player_gets_clean_same_position_swaps_within_budget_sorted_by_projection():
    pool = [
        _pool_row("Back Hurt", "BBB", "RB", 6000, 15.0),
        _pool_row("Cheap RB", "EEE", "RB", 4500, 11.0),
        _pool_row("Best RB", "FFF", "RB", 7000, 17.0),  # fits: freed 6000 + cap room (50000-20000=30000)
        _pool_row("Wrong Pos WR", "GGG", "WR", 6000, 20.0),
        _pool_row("Also Hurt RB", "HHH", "RB", 5000, 18.0),
        _pool_row("QB One", "AAA", "QB", 6000, 22.0),
    ]
    readings = {"Back Hurt": [StatusReading("DK", "OUT", 3)], "Also Hurt RB": [StatusReading("RotoGrinders", "Q", 1)]}
    rf = lambda name, team, cid: readings.get(name, [])
    res = check_lineup("L", _lineup(), rf, pool)
    assert [p.name for p in res.flagged] == ["Back Hurt"]
    names = [c.name for c in res.swaps["Back Hurt"]]
    assert names == ["Best RB", "Cheap RB"]  # sorted by projection; excludes flagged, wrong position, himself


def test_flex_swap_accepts_rb_wr_te_and_budget_is_enforced():
    pool = [_pool_row("Flex WR", "CCC", "WR", 5000, 12.0), _pool_row("TE Option", "III", "TE", 40000, 30.0),
            _pool_row("RB Option", "JJJ", "RB", 6500, 14.0)]
    rf = lambda name, team, cid: [StatusReading("DK", "D", 2)] if name == "Flex WR" else []
    res = check_lineup("L", _lineup(), rf, pool)
    assert [c.name for c in res.swaps["Flex WR"]] == ["RB Option"]  # 40000 TE busts the budget (5000 + 30000 left)


def test_dst_is_never_flagged_and_unflagged_lineup_has_no_swaps():
    pool = [_pool_row("Cheap RB", "EEE", "RB", 4500, 11.0)]
    res = check_lineup("L", _lineup(), lambda *a: [], pool)
    assert res.flagged == () and res.swaps == {} and res.salary_used == 20000
