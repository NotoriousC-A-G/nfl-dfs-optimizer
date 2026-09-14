import pandas as pd
import pytest

from nfl_dfs.ingestion.nflverse import (
    aggregate_team_week,
    compute_pace_proe_for_week,
    prior_season_baselines_from,
    season_baseline,
    situation_neutral_mask,
)


def _pbp_row(**kwargs) -> dict:
    row = {
        "season": 2026,
        "week": 1,
        "season_type": "REG",
        "posteam": "GB",
        "play_type": "pass",
        "wp": 0.5,
        "half_seconds_remaining": 900,
        "pass": 1,
        "pass_oe": 0.0,
        "xpass": 0.5,
        "play_id": 1,
    }
    row.update(kwargs)
    return row


def test_situation_neutral_mask_excludes_non_neutral_plays():
    df = pd.DataFrame(
        [
            _pbp_row(play_id=1),  # neutral: kept
            _pbp_row(play_id=2, play_type="kickoff"),  # not pass/run
            _pbp_row(play_id=3, wp=0.97),  # garbage time (blowout)
            _pbp_row(play_id=4, wp=0.02),  # garbage time (other side blowout)
            _pbp_row(play_id=5, half_seconds_remaining=60),  # final 2 min of half
            _pbp_row(play_id=6, xpass=None),  # missing xpass
            _pbp_row(play_id=7, pass_oe=None),  # missing pass_oe
        ]
    )
    mask = situation_neutral_mask(df)
    assert mask.tolist() == [True, False, False, False, False, False, False]


def test_aggregate_team_week_computes_pace_and_proe_and_normalizes_la_to_lar():
    rows = [
        _pbp_row(play_id=i, posteam="GB", pass_oe=v) for i, v in enumerate([10.0, -5.0, 0.0, 20.0, -25.0])
    ] + [
        _pbp_row(play_id=100 + i, posteam="LA", pass_oe=5.0) for i in range(4)
    ] + [
        # a non-neutral row that should not count toward GB's aggregate
        _pbp_row(play_id=200, posteam="GB", play_type="punt"),
    ]
    df = pd.DataFrame(rows)
    agg = aggregate_team_week(df)

    gb = agg[agg["team"] == "GB"].iloc[0]
    assert gb["neutral_plays"] == 5
    assert gb["proe"] == pytest.approx(0.0)

    # "LA" (nflverse's own Rams code) normalizes to the DK-canonical "LAR".
    assert "LA" not in agg["team"].values
    lar = agg[agg["team"] == "LAR"].iloc[0]
    assert lar["neutral_plays"] == 4
    assert lar["proe"] == pytest.approx(5.0)


def test_aggregate_team_week_excludes_postseason_by_default():
    rows = [_pbp_row(play_id=1, season_type="REG"), _pbp_row(play_id=2, season_type="POST")]
    agg = aggregate_team_week(pd.DataFrame(rows))
    assert agg["neutral_plays"].sum() == 1


def test_aggregate_team_week_can_include_all_season_types():
    rows = [_pbp_row(play_id=1, season_type="REG"), _pbp_row(play_id=2, season_type="POST")]
    agg = aggregate_team_week(pd.DataFrame(rows), season_type=None)
    assert agg["neutral_plays"].sum() == 2


def test_season_baseline_averages_across_weeks():
    team_week = pd.DataFrame(
        [
            {"season": 2024, "team": "GB", "neutral_plays": 60, "proe": 5.0},
            {"season": 2024, "team": "GB", "neutral_plays": 62, "proe": 3.0},
            {"season": 2024, "team": "KC", "neutral_plays": 55, "proe": -2.0},
        ]
    )
    baseline = season_baseline(team_week)
    gb = baseline[baseline["team"] == "GB"].iloc[0]
    assert gb["pace_baseline"] == pytest.approx(61.0)
    assert gb["proe_baseline"] == pytest.approx(4.0)


def test_prior_season_baselines_from_averages_two_seasons_or_falls_back_to_one():
    season_baseline_df = pd.DataFrame(
        [
            {"season": 2024, "team": "GB", "pace_baseline": 60.0, "proe_baseline": 4.0},
            {"season": 2025, "team": "GB", "pace_baseline": 64.0, "proe_baseline": 2.0},
            {"season": 2025, "team": "KC", "pace_baseline": 58.0, "proe_baseline": 1.0},
        ]
    )
    baselines = prior_season_baselines_from(season_baseline_df, prior_seasons=(2024, 2025))
    assert baselines["GB"] == pytest.approx((62.0, 3.0))
    # KC only has one prior season available -- falls back to that single value, per ADR-0003.
    assert baselines["KC"] == pytest.approx((58.0, 1.0))


def test_compute_pace_proe_for_week_blends_and_zscores():
    # Two teams' current-season-to-date data through week 3 (target_week=4, so weeks 1-3 count).
    current_team_week = pd.DataFrame(
        [
            {"season": 2026, "week": 1, "team": "AAA", "neutral_plays": 60.0, "proe": 10.0},
            {"season": 2026, "week": 2, "team": "AAA", "neutral_plays": 60.0, "proe": 10.0},
            {"season": 2026, "week": 3, "team": "AAA", "neutral_plays": 60.0, "proe": 10.0},
            {"season": 2026, "week": 1, "team": "BBB", "neutral_plays": 50.0, "proe": -10.0},
        ]
    )
    prior_baselines = {"AAA": (50.0, 0.0), "BBB": (50.0, 0.0)}
    all_teams = frozenset({"AAA", "BBB"})

    out = compute_pace_proe_for_week(
        current_team_week, target_week=4, prior_season_baselines=prior_baselines, all_teams=all_teams, k=6.0
    )

    aaa = out[out["team"] == "AAA"].iloc[0]
    assert aaa["weeks_played"] == 3
    assert aaa["shrinkage_weight"] == pytest.approx(3 / 9)
    assert aaa["pace_current_to_date"] == pytest.approx(60.0)
    # blended = (3/9)*60 + (6/9)*50 = 20 + 33.333... = 53.333...
    assert aaa["pace_blended"] == pytest.approx(3 / 9 * 60.0 + 6 / 9 * 50.0)
    assert aaa["proe_blended"] == pytest.approx(3 / 9 * 10.0 + 6 / 9 * 0.0)

    bbb = out[out["team"] == "BBB"].iloc[0]
    # BBB only has a week-1 row, which still counts toward "completed weeks < target_week=4".
    assert bbb["weeks_played"] == 1
    assert bbb["shrinkage_weight"] == pytest.approx(1 / 7)
    assert bbb["pace_current_to_date"] == pytest.approx(50.0)
    # current (50.0) happens to equal the prior baseline (50.0), so the blend is 50 regardless of weight.
    assert bbb["pace_blended"] == pytest.approx(50.0)
    assert bbb["proe_blended"] == pytest.approx(1 / 7 * -10.0 + 6 / 7 * 0.0)

    # With exactly 2 teams, AAA (higher blended pace) and BBB (lower) should be mirror z-scores.
    assert out["pace_z"].sum() == pytest.approx(0.0, abs=1e-9)
    assert aaa["pace_z"] > 0
    assert bbb["pace_z"] < 0


def test_compute_pace_proe_for_week_includes_every_team_in_all_teams_even_with_no_data():
    out = compute_pace_proe_for_week(
        pd.DataFrame(columns=["season", "week", "team", "neutral_plays", "proe"]),
        target_week=1,
        prior_season_baselines={"AAA": (55.0, 1.0), "BBB": (45.0, -1.0)},
        all_teams=frozenset({"AAA", "BBB"}),
    )
    assert set(out["team"]) == {"AAA", "BBB"}
    assert (out["weeks_played"] == 0).all()
