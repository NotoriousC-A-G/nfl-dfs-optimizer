import pandas as pd
import pytest

from nfl_dfs.ingestion.snap_share import (
    aggregate_snap_player_week,
    compute_snap_shares_for_week,
    join_snap_counts_to_gsis_id,
)


def _snap_row(**kwargs) -> dict:
    row = {
        "game_id": "2026_01_XXX_YYY",
        "season": 2026,
        "game_type": "REG",
        "week": 1,
        "player": "Some Player",
        "pfr_player_id": "PlayFo00",
        "position": "WR",
        "team": "GB",
        "opponent": "CHI",
        "offense_snaps": 50.0,
        "offense_pct": 0.80,
        "defense_snaps": 0.0,
        "defense_pct": 0.0,
        "st_snaps": 5.0,
        "st_pct": 0.20,
    }
    row.update(kwargs)
    return row


def _crosswalk(rows: list[dict]) -> pd.DataFrame:
    """Minimal crosswalk frame shaped like `normalization/crosswalk.py`'s cached output --
    just the two columns `join_snap_counts_to_gsis_id` reads."""
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------------
# join_snap_counts_to_gsis_id -- the ADR-0022 join-quality concern
# --------------------------------------------------------------------------------------------


def test_join_resolves_matched_pfr_id_to_gsis_id():
    snaps = pd.DataFrame([_snap_row(pfr_player_id="GibbJa01")])
    crosswalk = _crosswalk([{"pfr_id": "GibbJa01", "gsis_id": "00-0038542"}])
    joined = join_snap_counts_to_gsis_id(snaps, crosswalk)
    row = joined.iloc[0]
    assert row["gsis_id"] == "00-0038542"
    assert row["gsis_id_resolved"] is True or bool(row["gsis_id_resolved"]) is True


def test_join_leaves_unresolved_row_present_but_flagged():
    # A player whose pfr_player_id has no crosswalk row at all -- e.g. an offensive lineman, per
    # this module's live finding that the crosswalk barely covers OL. Row must survive the join
    # (left join) rather than silently vanishing, so callers can see/report it.
    snaps = pd.DataFrame([_snap_row(pfr_player_id="NoMatch99", position="T")])
    crosswalk = _crosswalk([{"pfr_id": "GibbJa01", "gsis_id": "00-0038542"}])
    joined = join_snap_counts_to_gsis_id(snaps, crosswalk)
    assert len(joined) == 1
    assert pd.isna(joined.iloc[0]["gsis_id"])
    assert bool(joined.iloc[0]["gsis_id_resolved"]) is False


def test_join_does_not_crash_on_null_pfr_id_crosswalk_rows():
    # The real crosswalk has plenty of rows with a null pfr_id (players with no PFR record at
    # all) -- these must not become a spurious NaN==NaN match against an unresolved snap row.
    snaps = pd.DataFrame([_snap_row(pfr_player_id="NoMatch99")])
    crosswalk = _crosswalk(
        [{"pfr_id": "GibbJa01", "gsis_id": "00-0038542"}, {"pfr_id": None, "gsis_id": "00-0099999"}]
    )
    joined = join_snap_counts_to_gsis_id(snaps, crosswalk)
    assert bool(joined.iloc[0]["gsis_id_resolved"]) is False


# --------------------------------------------------------------------------------------------
# aggregate_snap_player_week
# --------------------------------------------------------------------------------------------


def test_aggregate_snap_player_week_filters_to_reg_and_drops_unresolved():
    snaps = pd.DataFrame(
        [
            _snap_row(pfr_player_id="GibbJa01", week=1, offense_pct=0.75),
            _snap_row(pfr_player_id="NoMatch99", week=1, offense_pct=0.10),
            _snap_row(pfr_player_id="GibbJa01", week=1, game_type="WC", offense_pct=0.99),
        ]
    )
    crosswalk = _crosswalk([{"pfr_id": "GibbJa01", "gsis_id": "00-0038542"}])
    joined = join_snap_counts_to_gsis_id(snaps, crosswalk)
    out = aggregate_snap_player_week(joined)
    # Only the REG-season, resolved row survives.
    assert len(out) == 1
    assert out.iloc[0]["player_id"] == "00-0038542"
    assert out.iloc[0]["offense_pct"] == pytest.approx(0.75)


# --------------------------------------------------------------------------------------------
# compute_snap_shares_for_week -- trailing-only, no-look-ahead, plain mean (no shrinkage)
# --------------------------------------------------------------------------------------------


def _player_week_rows(weeks_pct: list[tuple[int, float]], player_id="00-0038542") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "season": 2026,
                "week": wk,
                "player_id": player_id,
                "pfr_player_id": "GibbJa01",
                "player_name": "Jahmyr Gibbs",
                "team": "DET",
                "position": "RB",
                "offense_pct": pct,
                "defense_pct": 0.0,
                "st_pct": 0.05,
            }
            for wk, pct in weeks_pct
        ]
    )


def test_compute_snap_shares_uses_only_completed_weeks():
    player_week = _player_week_rows([(1, 0.60), (2, 0.70), (3, 0.80)])
    results = compute_snap_shares_for_week(player_week, target_week=4)
    assert len(results) == 1
    r = results[0]
    assert r.player_id == "00-0038542"
    assert r.weeks_played == 3
    assert r.offense_pct_last_week == pytest.approx(0.80)  # most recent trailing week
    assert r.offense_pct_trailing == pytest.approx((0.60 + 0.70 + 0.80) / 3)  # plain mean, no shrinkage


def test_compute_snap_shares_excludes_current_and_future_weeks():
    player_week = _player_week_rows([(1, 0.60), (2, 0.70), (3, 0.99)])
    results = compute_snap_shares_for_week(player_week, target_week=3)
    assert len(results) == 1
    r = results[0]
    assert r.weeks_played == 2
    assert r.offense_pct_last_week == pytest.approx(0.70)
    assert r.offense_pct_trailing == pytest.approx((0.60 + 0.70) / 2)


def test_compute_snap_shares_returns_empty_when_no_trailing_data():
    player_week = _player_week_rows([(1, 0.60)])
    results = compute_snap_shares_for_week(player_week, target_week=1)
    assert results == []


def test_compute_snap_shares_handles_multiple_players_independently():
    player_week = pd.concat(
        [
            _player_week_rows([(1, 0.60), (2, 0.70)], player_id="00-0038542"),
            _player_week_rows([(1, 0.20), (2, 0.30)], player_id="00-0011111"),
        ],
        ignore_index=True,
    )
    results = compute_snap_shares_for_week(player_week, target_week=3)
    by_id = {r.player_id: r for r in results}
    assert set(by_id) == {"00-0038542", "00-0011111"}
    assert by_id["00-0038542"].offense_pct_trailing == pytest.approx(0.65)
    assert by_id["00-0011111"].offense_pct_trailing == pytest.approx(0.25)
