import math

import pandas as pd
import pytest

from nfl_dfs.ingestion.game_environment_stats import (
    blend_toward_prior,
    cross_sectional_zscore,
    cross_sectional_zscore_by_group,
    shrinkage_weight,
)


def test_shrinkage_weight_matches_adr_0011_worked_comparison_table():
    # ADR-0011's own worked table for k=6 (pace/PROE): weeks_played -> w(n).
    cases = {1: 0.143, 3: 0.333, 6: 0.5, 12: 0.667, 17: 0.739}
    for n, expected in cases.items():
        assert shrinkage_weight(n, k=6) == pytest.approx(expected, abs=0.001)


def test_shrinkage_weight_zero_n_is_zero():
    assert shrinkage_weight(0, k=6) == 0.0


def test_shrinkage_weight_rejects_bad_inputs():
    with pytest.raises(ValueError):
        shrinkage_weight(-1, k=6)
    with pytest.raises(ValueError):
        shrinkage_weight(5, k=0)


def test_blend_toward_prior_basic():
    # w=0.5: halfway between current (10) and prior (20).
    assert blend_toward_prior(10.0, 20.0, 0.5) == pytest.approx(15.0)


def test_blend_toward_prior_zero_weight_ignores_current_value():
    assert blend_toward_prior(999.0, 20.0, 0.0) == pytest.approx(20.0)


def test_blend_toward_prior_none_current_with_zero_weight_is_fine():
    assert blend_toward_prior(None, 20.0, 0.0) == pytest.approx(20.0)


def test_blend_toward_prior_raises_if_no_prior_and_weight_below_one():
    with pytest.raises(ValueError, match="no prior-season baseline"):
        blend_toward_prior(10.0, None, 0.5)


def test_blend_toward_prior_no_prior_but_full_weight_is_fine():
    assert blend_toward_prior(10.0, None, 1.0) == pytest.approx(10.0)


def test_cross_sectional_zscore_basic():
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    z = cross_sectional_zscore(values)
    assert z.mean() == pytest.approx(0.0, abs=1e-9)
    # population std (ddof=0) of [1..5] is sqrt(2); the endpoints should be +/- 2/sqrt(2).
    assert z.iloc[0] == pytest.approx(-2 / math.sqrt(2))
    assert z.iloc[-1] == pytest.approx(2 / math.sqrt(2))


def test_cross_sectional_zscore_nan_when_population_too_small():
    assert cross_sectional_zscore(pd.Series([5.0])).isna().all()


def test_cross_sectional_zscore_nan_when_no_variance():
    assert cross_sectional_zscore(pd.Series([5.0, 5.0, 5.0])).isna().all()


def test_cross_sectional_zscore_by_group_is_independent_per_week():
    df = pd.DataFrame(
        {
            "week": [1, 1, 1, 2, 2, 2],
            "value": [10.0, 20.0, 30.0, 100.0, 100.0, 130.0],
        }
    )
    z = cross_sectional_zscore_by_group(df, "value", "week")
    # Week 1 and week 2 each z-score against only their own 3 rows.
    week1 = z[df["week"] == 1]
    week2 = z[df["week"] == 2]
    assert week1.mean() == pytest.approx(0.0, abs=1e-9)
    assert week2.mean() == pytest.approx(0.0, abs=1e-9)
    # Week 2's first two rows are tied (100, 100) -- both below the week's own mean.
    assert week2.iloc[0] == pytest.approx(week2.iloc[1])
