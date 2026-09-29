import pandas as pd

from nfl_dfs.tracking.postmortem.player_context import build_player_context_by_id


def _weekly_row(**overrides) -> dict:
    base = dict(
        season=2026,
        week=3,
        season_type="REG",
        player_display_name="Test Player",
        recent_team="MIN",
        passing_yards=0,
        passing_tds=0,
        interceptions=0,
        completions=0,
        attempts=0,
        rushing_yards=0,
        rushing_tds=0,
        carries=0,
        receptions=0,
        receiving_yards=0,
        receiving_tds=0,
        targets=0,
        rushing_fumbles_lost=0,
        receiving_fumbles_lost=0,
        sack_fumbles_lost=0,
        passing_2pt_conversions=0,
        rushing_2pt_conversions=0,
        receiving_2pt_conversions=0,
    )
    base.update(overrides)
    return base


def _pool_row(cid, name, team, position, **overrides) -> dict:
    base = {
        "identity": {"canonical_id": cid, "display_name": name},
        "team": team,
        "position": position,
        "ownership": None,
        "game_environment": None,
        "stack_context": None,
        "injury": None,
        "circumstance_assessment": None,
        "ceiling_multiplier": None,
        "red_zone_role_security_discount": None,
        "implied_total": None,
        "slate_window": None,
    }
    base.update(overrides)
    return base


def test_builds_context_from_real_populated_fields():
    pool = [
        _pool_row(
            "wr1",
            "Justin Jefferson",
            "MIN",
            "WR",
            ownership={"is_chalk": True, "is_leverage": False},
            game_environment={"composite_score": 62.0},
            stack_context={"is_primary_stack_candidate": True},
            injury={"status": "Q"},
            ceiling_multiplier=1.4,
            red_zone_role_security_discount=-0.1,
            implied_total=26.5,
            slate_window="early",
            circumstance_assessment={"pov": "Real injury note about the WR2."},
        )
    ]
    weekly = pd.DataFrame([_weekly_row(player_display_name="Justin Jefferson", recent_team="MIN", targets=8, receptions=5, receiving_yards=60)])

    contexts = build_player_context_by_id(pool, season=2026, week=3, weekly=weekly)

    ctx = contexts["wr1"]
    assert ctx.is_chalk is True
    assert ctx.is_leverage is False
    assert ctx.game_environment_score == 62.0
    assert ctx.is_primary_stack_candidate is True
    assert ctx.injury_status == "Q"
    assert ctx.ceiling_multiplier == 1.4
    assert ctx.red_zone_role_security_discount == -0.1
    assert ctx.implied_total == 26.5
    assert ctx.slate_window == "early"
    assert ctx.circumstance_note == "Real injury note about the WR2."
    assert ctx.box_score_line == "5 rec, 60 yds, 0 TD on 8 tgt"


def test_defaults_are_falsy_not_fabricated_when_fields_are_none():
    pool = [_pool_row("qb1", "Some QB", "KC", "QB")]
    weekly = pd.DataFrame([_weekly_row(player_display_name="Someone Else", recent_team="SEA")])
    contexts = build_player_context_by_id(pool, season=2026, week=3, weekly=weekly)

    ctx = contexts["qb1"]
    assert ctx.is_chalk is False
    assert ctx.is_leverage is False
    assert ctx.game_environment_score is None
    assert ctx.is_primary_stack_candidate is False
    assert ctx.ceiling_multiplier is None
    assert ctx.injury_status is None
    assert ctx.box_score_line is None


def test_dst_rows_get_no_box_score_line():
    pool = [_pool_row("dst1", "Jaguars", "JAX", "DST")]
    weekly = pd.DataFrame([_weekly_row(player_display_name="Jaguars", recent_team="JAX")])
    contexts = build_player_context_by_id(pool, season=2026, week=3, weekly=weekly)
    assert contexts["dst1"].box_score_line is None


def test_rows_with_no_canonical_id_are_skipped():
    pool = [{"identity": {}, "team": "MIN", "position": "WR"}]
    weekly = pd.DataFrame([_weekly_row(player_display_name="Someone Else", recent_team="SEA")])
    contexts = build_player_context_by_id(pool, season=2026, week=3, weekly=weekly)
    assert contexts == {}
