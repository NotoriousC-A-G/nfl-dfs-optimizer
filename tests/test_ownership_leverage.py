import pytest

from nfl_dfs.analysis.ownership_calibration import ProductionCalibration
from nfl_dfs.ingestion.rotogrinders import LineupHqOwnershipRow
from nfl_dfs.ownership.leverage import build_leverage_assessments

# One player per salary decile (0 = highest priced), engineered so exactly one player is chalk (top-decile
# owned) and exactly one is leverage (most underowned-vs-baseline among top-half-salary, non-chalk players).
_SALARIES = [8000, 7500, 7000, 6500, 6000, 5500, 5000, 4500, 4000, 3500]
_PROJECTED_OWNERSHIP = [35.0, 5.0, 25.0, 3.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]
_BASELINE_BY_DECILE = {0: 20.0, 1: 15.0, 2: 10.0, 3: 8.0, 4: 6.0, 5: 5.0, 6: 4.0, 7: 3.0, 8: 2.0, 9: 1.0}


def _wr_rows() -> list[LineupHqOwnershipRow]:
    return [
        LineupHqOwnershipRow(
            native_id=f"wr-{i}", name=f"WR {i}", position="WR", team="KC", salary=salary, projected_ownership=own, slate="MAIN"
        )
        for i, (salary, own) in enumerate(zip(_SALARIES, _PROJECTED_OWNERSHIP))
    ]


def _production() -> dict[str, ProductionCalibration]:
    return {
        "WR": ProductionCalibration(
            position="WR", source_seasons=(2023, 2024, 2025), blended=False, decile_ownership=_BASELINE_BY_DECILE, n_rows=100
        )
    }


def test_build_leverage_assessments_flags_exactly_the_engineered_chalk_and_leverage_players():
    assessments = build_leverage_assessments(_wr_rows(), _production())
    by_name = {a.name: a for a in assessments}

    assert len(assessments) == 10

    chalk = [a for a in assessments if a.is_chalk]
    assert [a.name for a in chalk] == ["WR 0"]
    assert by_name["WR 0"].salary_decile == 0
    assert "chalk" in by_name["WR 0"].note.lower()

    leverage = [a for a in assessments if a.is_leverage]
    assert [a.name for a in leverage] == ["WR 1"]
    assert by_name["WR 1"].ownership_vs_baseline == pytest.approx(-10.0)
    assert "underowned" in by_name["WR 1"].note.lower()

    # WR 5 is priced below the top-half-salary cutoff (decile 5 > LEVERAGE_SALARY_DECILE_MAX) even though
    # its deviation (5.0 - 5.0 == 0) is unremarkable anyway -- never eligible regardless of ownership.
    assert by_name["WR 5"].is_leverage is False


def test_build_leverage_assessments_baseline_and_deviation_are_none_without_a_calibration():
    rows = [
        LineupHqOwnershipRow(
            native_id="qb-1", name="No Calibration QB", position="QB", team="KC", salary=7000, projected_ownership=10.0, slate="MAIN"
        )
    ]
    assessments = build_leverage_assessments(rows, production={})
    assert len(assessments) == 1
    assert assessments[0].baseline_ownership is None
    assert assessments[0].ownership_vs_baseline is None
    assert assessments[0].is_leverage is False


def test_build_leverage_assessments_excludes_non_core_positions():
    rows = _wr_rows() + [
        LineupHqOwnershipRow(native_id="k-1", name="Some Kicker", position="K", team="KC", salary=4500, projected_ownership=5.0, slate="MAIN")
    ]
    assessments = build_leverage_assessments(rows, _production())
    assert "Some Kicker" not in {a.name for a in assessments}
    assert len(assessments) == 10


def test_build_leverage_assessments_empty_input_returns_empty_list():
    assert build_leverage_assessments([], {}) == []


def test_build_leverage_assessments_salary_deciles_and_ownership_percentiles_are_position_scoped():
    # A single, much-cheaper QB pool must not have its salary/ownership ranked against the WR pool.
    qb_rows = [
        LineupHqOwnershipRow(native_id="qb-1", name="Top QB", position="QB", team="KC", salary=9000, projected_ownership=30.0, slate="MAIN"),
        LineupHqOwnershipRow(native_id="qb-2", name="Low QB", position="QB", team="SEA", salary=5000, projected_ownership=5.0, slate="MAIN"),
    ]
    assessments = build_leverage_assessments(_wr_rows() + qb_rows, _production())
    top_qb = next(a for a in assessments if a.name == "Top QB")
    assert top_qb.salary_decile == 0
    assert top_qb.ownership_percentile == 0.0
    assert top_qb.baseline_ownership is None  # no "QB" entry in this test's production dict
