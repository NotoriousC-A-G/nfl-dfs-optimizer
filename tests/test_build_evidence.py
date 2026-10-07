from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
import pytest

from nfl_dfs.build.evidence.anchors import (
    base_rate, favorite_win_probability, lead_anchor, make_percentile_fn, percentile_of_threshold, question_anchor,
)
from nfl_dfs.build.evidence.builder import MAX_PLAYERS_PER_TEAM, build_evidence_packets
from nfl_dfs.build.evidence.contracts import flatten, packet_keys, with_sha
from nfl_dfs.build.evidence.metrics import league_values, season_to_date, team_game_metrics
from nfl_dfs.build.thesis.contracts import PivotalQuestion


# ---------------------------------------------------------------------------------------------
# metrics: a tiny hand-checkable game, HOM vs AWY
# ---------------------------------------------------------------------------------------------
def _play(pid, posteam, defteam, play_type, *, dropback=0, sack=0, hit=0, pass_att=0, rush=0, yards=0, epa=0.0, oe=None,
          diff=0, qtr=1, home=0, away=0, drive=1, gsr=3000.0, week=1):
    return dict(season=2026, season_type="REG", week=week, game_id=f"g{week}", home_team="HOM", away_team="AWY",
                posteam=posteam, defteam=defteam, play_type=play_type, qb_dropback=dropback, sack=sack, qb_hit=hit,
                pass_attempt=pass_att, rush=rush, yards_gained=yards, epa=epa, pass_oe=oe, score_differential=diff, qtr=qtr,
                total_home_score=home, total_away_score=away, drive=drive, play_id=pid, game_seconds_remaining=gsr)


def _pbp():
    rows = [
        # HOM offense: 4 dropbacks (1 sack+hit, 1 explosive 25-yd completion, 2 short), 2 rushes
        _play(1, "HOM", "AWY", "pass", dropback=1, pass_att=1, yards=25, epa=1.0, oe=10.0, diff=0, qtr=1, gsr=3000),
        _play(2, "HOM", "AWY", "pass", dropback=1, sack=1, hit=1, yards=-7, epa=-2.0, oe=20.0, diff=0, qtr=1, gsr=2970),
        _play(3, "HOM", "AWY", "pass", dropback=1, pass_att=1, yards=5, epa=0.0, oe=-10.0, diff=0, qtr=1, gsr=2940),
        _play(4, "HOM", "AWY", "pass", dropback=1, pass_att=1, yards=3, epa=1.0, oe=0.0, diff=0, qtr=1, gsr=2910),
        _play(5, "HOM", "AWY", "run", rush=1, yards=4, epa=0.5, oe=-30.0, diff=7, qtr=3, home=14, away=7, gsr=1700, drive=2),
        _play(6, "HOM", "AWY", "run", rush=1, yards=2, epa=-0.5, oe=-30.0, diff=7, qtr=3, home=14, away=7, gsr=1670, drive=2),
        # a Q4 first play: HOM leads 14-7 entering Q4
        _play(7, "HOM", "AWY", "run", rush=1, yards=1, epa=0.0, oe=-30.0, diff=7, qtr=4, home=14, away=7, gsr=900, drive=3),
        # AWY offense: 2 dropbacks, no sacks; the opponent (HOM defense) got 0 sacks, so def_sack_rate for HOM = 0
        _play(8, "AWY", "HOM", "pass", dropback=1, pass_att=1, yards=8, epa=0.2, oe=5.0, diff=-7, qtr=3, home=14, away=7, gsr=1600),
        _play(9, "AWY", "HOM", "pass", dropback=1, pass_att=1, yards=0, epa=-0.2, oe=5.0, diff=-7, qtr=3, home=14, away=7, gsr=1575),
    ]
    return pd.DataFrame(rows)


def test_team_game_metrics_match_hand_computed_values():
    tg = team_game_metrics(_pbp())
    h = tg[tg["team"] == "HOM"].iloc[0]
    assert h["dropbacks"] == 4 and h["sacks"] == 1 and h["qb_hits"] == 1
    assert h["sack_rate"] == pytest.approx(0.25) and h["qb_hit_rate"] == pytest.approx(0.25)
    assert h["explosive_pass_rate"] == pytest.approx(1 / 3)  # 1 of 3 pass attempts went 20+
    assert h["pass_epa"] == pytest.approx((1.0 - 2.0 + 0.0 + 1.0) / 4)
    assert h["rush_epa"] == pytest.approx((0.5 - 0.5 + 0.0) / 3)
    assert h["total_plays"] == 7
    assert h["rush_attempts_leading"] == 3  # plays 5, 6 (Q3) and 7 (Q4) are all rushes with a 4+ point lead
    assert h["lead_at_q4"] == 1.0 and h["lead_at_q3_start"] == 1.0
    a = tg[tg["team"] == "AWY"].iloc[0]
    assert a["lead_at_q4"] == 0.0 and a["sack_rate"] == 0.0


def test_defensive_pass_rush_is_the_opponents_suffering():
    tg = team_game_metrics(_pbp()).set_index("team")
    assert tg.loc["AWY", "def_sack_rate"] == pytest.approx(0.25)  # AWY's defense sacked HOM on 1 of 4 dropbacks
    assert tg.loc["HOM", "def_sack_rate"] == 0.0


def test_pace_is_seconds_between_consecutive_plays_in_a_drive():
    tg = team_game_metrics(_pbp()).set_index("team")
    assert tg.loc["HOM", "pace_seconds_per_play"] == pytest.approx(30.0)  # 3000->2970->2940->2910 in drive 1; 1700->1670 in drive 2


def test_season_to_date_pools_rates_and_reports_real_sample_sizes():
    one = _pbp()
    two = one.copy()
    two["week"] = 2
    two["game_id"] = "g2"
    tg = team_game_metrics(pd.concat([one, two]))
    std = season_to_date(tg, "HOM", 2026, through_week=2)
    assert std["sack_rate"] == (pytest.approx(0.25), 8)  # 2 sacks / 8 dropbacks, n = dropbacks
    assert std["pass_epa"][1] == 8
    only_w1 = season_to_date(tg, "HOM", 2026, through_week=1)
    assert only_w1["sack_rate"][1] == 4
    assert season_to_date(tg, "HOM", 2026, through_week=0) == {}  # nothing yet -> empty, never fabricated


def test_league_values_returns_team_game_distribution_and_empty_for_unknown_metric():
    tg = team_game_metrics(_pbp())
    assert sorted(league_values(tg, "sack_rate")) == [0.0, 0.25]
    assert len(league_values(tg, "nope")) == 0


# ---------------------------------------------------------------------------------------------
# anchors
# ---------------------------------------------------------------------------------------------
def test_favorite_win_probability_from_the_spread():
    assert favorite_win_probability(0.0) == pytest.approx(0.5)
    assert 0.69 < favorite_win_probability(7.0) < 0.72
    assert favorite_win_probability(14.0) > favorite_win_probability(3.0)


def test_base_rate_is_the_share_on_the_yes_side_and_clipped():
    vals = np.array([0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20])
    assert base_rate(vals, 0.10, "gte") == pytest.approx(0.6)
    assert base_rate(vals, 0.10, "lte") == pytest.approx(0.5)
    assert base_rate(vals, 5.0, "gte") == 0.05  # clipped, never 0
    assert base_rate(vals, -1.0, "gte") == 0.95  # clipped, never 1
    assert base_rate(np.array([]), 0.1, "gte") is None


def test_lead_anchor_rises_with_the_spread_and_is_half_for_a_pickem():
    assert lead_anchor("lead_at_q4", 0.0) == pytest.approx(0.5)
    assert lead_anchor("lead_at_q4", 7.0) > lead_anchor("lead_at_q4", 3.0) > 0.5
    assert lead_anchor("lead_at_q4", -7.0) < 0.5
    assert 0.6 < lead_anchor("lead_at_q4", 7.0) < 0.8  # a 7-pt favorite leads entering Q4 roughly 2/3 to 3/4 of the time
    assert lead_anchor("lead_at_q4", 40.0) == 0.95


def _q(metric, threshold, direction="gte", source="units.PHI.sack_rate"):
    return PivotalQuestion("q1", "t", "pass_protection", metric, source, threshold, direction, 30, "adds")


def test_question_anchor_uses_league_base_rate_or_the_margin_model_and_none_when_unanchorable():
    league = lambda m: np.array([0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20])
    a = question_anchor(_q("sack_rate", 0.10), league_value_fn=league, team_spread={}, question_team="PHI")
    assert a == pytest.approx(0.6)
    lead = question_anchor(_q("lead_at_q4", 0.5), league_value_fn=league, team_spread={"LAR": 6.5}, question_team="LAR")
    assert lead is not None and lead > 0.6
    flip = question_anchor(replace(_q("lead_at_q4", 0.5), direction="lte"), league_value_fn=league, team_spread={"LAR": 6.5}, question_team="LAR")
    assert flip == pytest.approx(1 - lead)
    assert question_anchor(_q("lead_at_q4", 0.5), league_value_fn=league, team_spread={}, question_team="LAR") is None
    assert question_anchor(_q("sack_rate", 0.1), league_value_fn=lambda m: np.array([]), team_spread={}, question_team="PHI") is None


def test_percentile_fn_adapts_the_league_distribution_for_the_thesis_validator():
    vals = np.arange(1, 11) / 10
    fn = make_percentile_fn(lambda m: vals)
    assert fn("sack_rate", 0.7, "gte") == pytest.approx(0.7)
    assert percentile_of_threshold(np.array([]), 1.0) is None


# ---------------------------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------------------------
@dataclass
class _Decision:
    name: str
    team: str
    decision: str
    basis: str
    source: str


def _rec(cid, name, team, pos, sal, proj, *, implied=24.0, own=5.0, inj=None, ges=55.0, viab=50.0):
    return {
        "identity": {"canonical_id": cid, "display_name": name}, "team": team, "position": pos, "salary": sal,
        "projection": proj, "implied_total": implied, "slate_window": "early",
        "ownership": {"projected_ownership": own, "ownership_vs_baseline": 1.5, "is_chalk": own > 20, "is_leverage": False} if own is not None else None,
        "game_environment": {"composite_score": ges},
        "stack_context": {"single_team_viability": viab, "game_script_lean": {"stance": "underdog", "intensity": 0.8}},
        "ceiling_multiplier": 1.04 if pos == "WR" else None, "red_zone_role_security_discount": None,
        "usage": {"red_zone": {"carry_share_trailing": 0.3, "target_share_trailing": None}},
        "injury": inj, "circumstance_assessment": None, "qb_rushing_profile": {"designed_run_rate": 0.2} if pos == "QB" else None,
    }


def _pool():
    recs = []
    for team, implied in (("AAA", 21.0), ("BBB", 24.5)):
        recs.append(_rec(f"{team}_qb", f"QB {team}", team, "QB", 6000, 20.0, implied=implied))
        for k in range(14):
            recs.append(_rec(f"{team}_p{k}", f"Skill{k} {team}", team, "WR" if k % 2 else "RB", 4000 + 100 * k, 5.0 + k, implied=implied, own=(None if k == 13 else 5.0)))  # the top-projected skill player has no ownership
        recs.append(_rec(f"{team}_dst", f"{team} D", team, "DST", 2500, 3.0, implied=implied))
    recs.append(_rec("AAA_noproj", "No Proj", "AAA", "WR", 4000, None))
    return recs


def _sp():
    return [{"home_team": "AAA", "away_team": "BBB", "spread": 3.0}]  # home +3 => AAA is the underdog, BBB the favorite


def test_packet_basics_lines_and_favorite_orientation():
    packets = build_evidence_packets(_pool(), _sp(), None, season=2026, week=5)
    p = packets["BBB@AAA"]
    assert p.home == "AAA" and p.away == "BBB"
    assert p.lines.favorite == "BBB" and p.lines.abs_spread == 3.0 and p.lines.total == pytest.approx(45.5)
    assert 0.5 < p.lines.favorite_win_probability < 0.65
    assert p.teams["AAA"].opponent == "BBB" and p.teams["BBB"].implied_total == 24.5
    assert p.teams["AAA"].game_environment_score == 55.0 and p.teams["AAA"].game_script_stance == "underdog"
    assert p.slate_window == "early" and len(p.packet_sha) == 64


def test_players_capped_at_ten_per_team_best_by_projection_plus_the_dst():
    p = build_evidence_packets(_pool(), _sp(), None, season=2026, week=5)["BBB@AAA"]
    for team in ("AAA", "BBB"):
        mine = [x for x in p.players if x.team == team]
        assert len(mine) == MAX_PLAYERS_PER_TEAM
        assert sum(1 for x in mine if x.position == "DST") == 1
        skill = sorted(x.projection for x in mine if x.position != "DST")
        assert skill == sorted(x.projection for x in mine if x.position != "DST")
    assert "AAA_noproj" not in {x.canonical_id for x in p.players}  # no projection -> not listed, never invented


def test_missing_ownership_and_metrics_and_weather_become_named_data_gaps():
    p = build_evidence_packets(_pool(), _sp(), None, season=2026, week=5)["BBB@AAA"]
    gaps = " | ".join(p.data_gaps)
    assert "availability-unaware" in gaps and "no participation data" in gaps
    assert "no weather reading" in gaps and "no season-to-date proxy metrics" in gaps
    assert "ownership is populated for" in gaps


def test_availability_decisions_and_injury_listings_are_carried_and_attached_to_players():
    pool = _pool()
    pool[1]["injury"] = {"status": "Q", "body_part": "Hamstring", "impact_rating": 6}
    decisions = [_Decision("QB AAA", "AAA", "cleared", "official practice: Full", "official_practice")]
    p = build_evidence_packets(pool, _sp(), None, season=2026, week=5, availability=decisions, as_of="2026-10-09T18:00Z")["BBB@AAA"]
    assert any(a.name == "QB AAA" and a.decision == "cleared" and a.as_of == "2026-10-09T18:00Z" for a in p.availability)
    assert any(a.source == "RotoGrinders" and "Hamstring" in a.basis for a in p.availability)
    assert next(x for x in p.players if x.canonical_id == "AAA_qb").status_after_q_pass == "cleared"


def test_flatten_exposes_citeable_keys_and_drops_nulls_from_packet_keys():
    p = build_evidence_packets(_pool(), _sp(), None, season=2026, week=5, weather_by_home_team={"AAA": {"wind_mph": 12.0}})["BBB@AAA"]
    flat = flatten(p)
    assert flat["lines.home_spread"] == 3.0 and flat["teams.AAA.implied_total"] == 21.0 and flat["weather.wind_mph"] == 12.0
    assert flat["players.AAA_qb.projection"] == 20.0 and flat["meta.packet_sha"] == p.packet_sha
    keys = packet_keys(p)
    assert "players.AAA_qb.projection" in keys and "players.AAA_p0.ownership_pct" not in keys  # null -> not citeable
    assert "weather.wind_mph" in keys


def test_sha_is_deterministic_and_changes_when_evidence_changes():
    a = build_evidence_packets(_pool(), _sp(), None, season=2026, week=5)["BBB@AAA"]
    b = build_evidence_packets(_pool(), _sp(), None, season=2026, week=5)["BBB@AAA"]
    assert a.packet_sha == b.packet_sha
    pool = _pool()
    pool[0]["projection"] = 20.5
    c = build_evidence_packets(pool, _sp(), None, season=2026, week=5)["BBB@AAA"]
    assert c.packet_sha != a.packet_sha
    assert with_sha(a).packet_sha == a.packet_sha  # idempotent


def test_team_metrics_flow_into_the_packet_with_league_percentiles():
    one = _pbp()
    tg = team_game_metrics(one)
    pool = [_rec("HOM_qb", "QB HOM", "HOM", "QB", 6000, 20.0), _rec("AWY_qb", "QB AWY", "AWY", "QB", 6000, 19.0)]
    p = build_evidence_packets(pool, [{"home_team": "HOM", "away_team": "AWY", "spread": -2.0}], tg, season=2026, week=2)["AWY@HOM"]
    mv = p.teams["HOM"].metrics["sack_rate"]
    assert mv.value == pytest.approx(0.25) and mv.n == 4
    assert mv.league_percentile == pytest.approx(1.0)  # HOM's 0.25 is the highest of the two teams
    assert "sack_rate" in {k.split(".")[-1] for k in packet_keys(p) if k.startswith("units.HOM.")}
    assert p.teams["HOM"].def_metrics["def_sack_rate"].value == 0.0
