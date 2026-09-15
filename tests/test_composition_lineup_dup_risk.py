import pytest

from nfl_dfs.analysis.dup_risk_calibration import DupRiskLookupTable
from nfl_dfs.composition.lineup_dup_risk import MIN_PLAYERS_COVERED, assess_lineup_dup_risk
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch
from nfl_dfs.optimizer.lineup import Lineup
from nfl_dfs.ownership.leverage import LeverageAssessment
from nfl_dfs.projection.blend import PlayerProjection

_TABLE = DupRiskLookupTable(
    seasons=(2024, 2025),
    n_rows=1000,
    bucket_upper_bounds=(10.0, 20.0, 30.0),
    bucket_dup_rate={0: 0.01, 1: 0.02, 2: 0.05, 3: 0.20},
    bucket_mean_lineup_ct={0: 1.01, 1: 1.02, 2: 1.05, 3: 1.30},
)


def _player(canonical_id: str) -> PlayerProjection:
    return PlayerProjection(
        canonical_id=canonical_id, display_name=canonical_id, position="WR", team="GB",
        salary=6000, blended_projection=12.0, source_count=2, source_values={},
    )


def _lineup(canonical_ids: list[str]) -> Lineup:
    players = tuple(_player(cid) for cid in canonical_ids)
    return Lineup(
        slots={f"SLOT{i}": p for i, p in enumerate(players)},
        players=players,
        total_salary=50000,
        total_projected_points=150.0,
        core_stack=frozenset(),
        core_stack_team="GB",
    )


def _identity(canonical_id: str, native_id: str | None) -> PlayerIdentity:
    sources = {}
    if native_id is not None:
        sources["rotogrinders"] = SourceMatch(native_id=native_id, method=MatchMethod.CROSSWALK)
    return PlayerIdentity(canonical_id=canonical_id, display_name=canonical_id, position="WR", team="GB", sources=sources)


def _leverage(native_id: str, projected_ownership: float) -> LeverageAssessment:
    return LeverageAssessment(
        native_id=native_id, name=native_id, position="WR", team="GB", salary=6000, salary_decile=1,
        projected_ownership=projected_ownership, ownership_percentile=0.5, baseline_ownership=10.0,
        ownership_vs_baseline=0.0, is_chalk=False, is_leverage=False, note="",
    )


def test_assess_lineup_dup_risk_averages_real_projected_ownership_and_classifies():
    ids = [f"p{i}" for i in range(9)]
    lineup = _lineup(ids)
    identities = [_identity(cid, f"rg-{cid}") for cid in ids]
    # 9 players, all at 15.0% projected ownership -> avg=15.0 -> bucket 1 (10.0 < 15.0 <= 20.0).
    leverage_by_native_id = {f"rg-{cid}": _leverage(f"rg-{cid}", 15.0) for cid in ids}

    assessment = assess_lineup_dup_risk(lineup, identities, leverage_by_native_id, _TABLE)
    assert assessment.players_covered == 9
    assert assessment.avg_projected_ownership == pytest.approx(15.0)
    assert assessment.bucket == 1
    assert assessment.dup_rate == pytest.approx(0.02)
    assert assessment.mean_lineup_ct == pytest.approx(1.02)
    assert assessment.reason is None


def test_assess_lineup_dup_risk_averages_only_resolvable_players():
    ids = [f"p{i}" for i in range(9)]
    lineup = _lineup(ids)
    # Only 6 of 9 players have a resolvable identity+leverage row (still clears MIN_PLAYERS_COVERED).
    identities = [_identity(cid, f"rg-{cid}") for cid in ids[:6]]
    leverage_by_native_id = {f"rg-{cid}": _leverage(f"rg-{cid}", 25.0) for cid in ids[:6]}

    assessment = assess_lineup_dup_risk(lineup, identities, leverage_by_native_id, _TABLE)
    assert assessment.players_covered == 6
    assert assessment.avg_projected_ownership == pytest.approx(25.0)
    assert assessment.bucket == 2
    assert assessment.reason is None


def test_assess_lineup_dup_risk_gates_below_min_players_covered():
    ids = [f"p{i}" for i in range(9)]
    lineup = _lineup(ids)
    # Only 2 resolvable players -- below MIN_PLAYERS_COVERED=5.
    identities = [_identity(cid, f"rg-{cid}") for cid in ids[:2]]
    leverage_by_native_id = {f"rg-{cid}": _leverage(f"rg-{cid}", 10.0) for cid in ids[:2]}

    assessment = assess_lineup_dup_risk(lineup, identities, leverage_by_native_id, _TABLE)
    assert assessment.players_covered == 2
    assert assessment.avg_projected_ownership is None
    assert assessment.bucket is None
    assert assessment.dup_rate is None
    assert "2/9" in assessment.reason
    assert str(MIN_PLAYERS_COVERED) in assessment.reason


def test_assess_lineup_dup_risk_excludes_unresolved_rotogrinders_match():
    ids = [f"p{i}" for i in range(9)]
    lineup = _lineup(ids)
    identities = [
        _identity(cid, f"rg-{cid}") if i < 7 else PlayerIdentity(
            canonical_id=cid, display_name=cid, position="WR", team="GB",
            sources={"rotogrinders": SourceMatch(native_id=None, method=MatchMethod.UNRESOLVED)},
        )
        for i, cid in enumerate(ids)
    ]
    leverage_by_native_id = {f"rg-{cid}": _leverage(f"rg-{cid}", 5.0) for cid in ids}

    assessment = assess_lineup_dup_risk(lineup, identities, leverage_by_native_id, _TABLE)
    assert assessment.players_covered == 7  # the 2 UNRESOLVED-match players excluded


def test_assess_lineup_dup_risk_handles_none_leverage_dict():
    ids = [f"p{i}" for i in range(9)]
    lineup = _lineup(ids)
    identities = [_identity(cid, f"rg-{cid}") for cid in ids]
    assessment = assess_lineup_dup_risk(lineup, identities, None, _TABLE)
    assert assessment.players_covered == 0
    assert assessment.reason is not None
