from dataclasses import replace

from nfl_dfs.build.pool.contracts import PlayerRef
from nfl_dfs.build.pool.correlation import LEAD_MARGIN, LEAD_PROTECT_BONUS, lead_protect_pairs
from nfl_dfs.build.thesis.contracts import Branch, GameThesis
from tests._build_fixtures import _packet


def _world(margin):
    packet = _packet()  # LAR @ PHI
    gid = packet.game_id
    ahead_branch = Branch("b0", (("q1", True),), False, 0.5, "d", margin_shift=margin)
    other = Branch("res", (), True, 0.10, "d")
    thesis = GameThesis(1, gid, "sha", "p", "m", "h", (), (), (), (ahead_branch, other), "res", (), ())
    universe = []
    for t in (packet.home, packet.away):
        universe += [PlayerRef(f"{t}_rb{k}", f"RB{k} {t}", t, "RB", 5000, 12.0 - k, None, gid) for k in range(3)]
        universe.append(PlayerRef(f"{t}_dst", f"{t} D", t, "DST", 2800, 7.0, None, gid))
        universe.append(PlayerRef(f"{t}_qb", f"QB {t}", t, "QB", 6000, 19.0, None, gid))
    return packet, thesis, universe


def test_a_view_with_a_team_clearly_ahead_links_its_top_backs_to_its_own_defense():
    packet, thesis, universe = _world(margin=4.0)  # margin_shift is favorite-perspective: the favorite is ahead
    fav = packet.lines.favorite
    pairs = lead_protect_pairs(universe, {packet.game_id: thesis}, {packet.game_id: packet}, [f"{packet.game_id}:b0"])
    assert pairs == [(f"{fav}_rb0", f"{fav}_dst", LEAD_PROTECT_BONUS), (f"{fav}_rb1", f"{fav}_dst", LEAD_PROTECT_BONUS)]


def test_a_negative_margin_means_the_underdog_is_ahead():
    packet, thesis, universe = _world(margin=-4.0)
    fav = packet.lines.favorite
    dog = packet.away if fav == packet.home else packet.home
    pairs = lead_protect_pairs(universe, {packet.game_id: thesis}, {packet.game_id: packet}, [f"{packet.game_id}:b0"])
    assert {a.split("_")[0] for a, _, _ in pairs} == {dog} and all(b == f"{dog}_dst" for _, b, _ in pairs)


def test_no_link_when_the_lead_is_small_the_game_has_no_view_or_the_branch_is_unknown():
    packet, thesis, universe = _world(margin=LEAD_MARGIN - 0.5)
    t, p = {packet.game_id: thesis}, {packet.game_id: packet}
    assert lead_protect_pairs(universe, t, p, [f"{packet.game_id}:b0"]) == []
    packet, thesis, universe = _world(margin=4.0)
    t, p = {packet.game_id: thesis}, {packet.game_id: packet}
    assert lead_protect_pairs(universe, t, p, []) == []
    assert lead_protect_pairs(universe, t, p, [f"{packet.game_id}:nope"]) == []
    assert lead_protect_pairs(universe, t, p, ["zz@yy:b0"]) == []
