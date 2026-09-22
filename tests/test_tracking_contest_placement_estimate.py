from nfl_dfs.storage.contest_results_store import ContestResult, save_contest_results
from nfl_dfs.tracking.contest_placement_estimate import estimate_agent_placements


def _real_entry(label, score, rank, entries=10000, paid=2000, week=2, contest_name="Real Contest"):
    return ContestResult(2026, week, label, contest_name, entries, paid, 50000.0, rank, score, 0.0)


def test_no_placements_when_no_contest_has_two_real_entries(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results([_real_entry("L1", 80.0, 5000)], path=path)
    placements = estimate_agent_placements(2026, 2, {"chalk_anchor": 90.0}, contest_path=path)
    assert placements == []


def test_interpolates_between_two_real_anchors(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results(
        [_real_entry("L1", 80.0, 8000), _real_entry("L2", 120.0, 4000)],
        path=path,
    )
    # score 100 is exactly halfway between 80 and 120 -> rank should be halfway between 8000 and 4000
    placements = estimate_agent_placements(2026, 2, {"agent_x": 100.0}, contest_path=path)
    assert len(placements) == 1
    p = placements[0]
    assert p.estimated_rank == 6000
    assert p.method == "interpolated"


def test_extrapolates_above_the_observed_range(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results(
        [_real_entry("L1", 80.0, 8000), _real_entry("L2", 120.0, 4000)],
        path=path,
    )
    # score 160 is above both real anchors -> extrapolate using the same slope
    placements = estimate_agent_placements(2026, 2, {"agent_x": 160.0}, contest_path=path)
    p = placements[0]
    assert p.method == "extrapolated"
    assert p.estimated_rank < 4000  # better than the best real anchor


def test_extrapolates_below_the_observed_range(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results(
        [_real_entry("L1", 80.0, 8000), _real_entry("L2", 120.0, 4000)],
        path=path,
    )
    placements = estimate_agent_placements(2026, 2, {"agent_x": 40.0}, contest_path=path)
    p = placements[0]
    assert p.method == "extrapolated"
    assert p.estimated_rank > 8000  # worse than the worst real anchor


def test_rank_never_goes_below_one(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results(
        [_real_entry("L1", 80.0, 100), _real_entry("L2", 120.0, 50)],
        path=path,
    )
    placements = estimate_agent_placements(2026, 2, {"agent_x": 500.0}, contest_path=path)
    assert placements[0].estimated_rank >= 1


def test_estimated_cashed_compares_against_positions_paid(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results(
        [_real_entry("L1", 80.0, 8000, paid=2000), _real_entry("L2", 120.0, 500, paid=2000)],
        path=path,
    )
    placements = estimate_agent_placements(2026, 2, {"would_cash": 119.0, "would_miss": 81.0}, contest_path=path)
    by_agent = {p.agent_id: p for p in placements}
    assert by_agent["would_cash"].estimated_cashed is True
    assert by_agent["would_miss"].estimated_cashed is False


def test_uses_three_real_anchors_when_available(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results(
        [
            _real_entry("L1", 80.0, 9000),
            _real_entry("L2", 120.0, 4000),
            _real_entry("L3", 100.0, 6000),
        ],
        path=path,
    )
    placements = estimate_agent_placements(2026, 2, {"agent_x": 110.0}, contest_path=path)
    # 110 falls between L3 (100, 6000) and L2 (120, 4000) -- should interpolate off THAT segment
    p = placements[0]
    assert p.method == "interpolated"
    assert p.estimated_rank == 5000


def test_returns_one_placement_per_agent_per_multi_entry_contest(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results(
        [
            _real_entry("L1", 80.0, 8000, entries=1000, paid=200, contest_name="Contest A"),  # multi-entry
            _real_entry("L2", 120.0, 4000, entries=1000, paid=200, contest_name="Contest A"),
        ],
        path=path,
    )
    save_contest_results(
        [_real_entry("L1", 80.0, 500, entries=1000, paid=200, contest_name="Contest B")],  # single-entry
        path=path,
    )
    placements = estimate_agent_placements(2026, 2, {"a": 90.0, "b": 100.0}, contest_path=path)
    assert len(placements) == 2  # only Contest A qualifies (2 real anchors); Contest B has 1


def test_different_weeks_do_not_mix_anchors(tmp_path):
    path = tmp_path / "contest_results.csv"
    save_contest_results(
        [
            _real_entry("L1", 80.0, 8000, week=1),
            _real_entry("L2", 120.0, 4000, week=1),
        ],
        path=path,
    )
    placements_week1 = estimate_agent_placements(2026, 1, {"agent_x": 100.0}, contest_path=path)
    placements_week2 = estimate_agent_placements(2026, 2, {"agent_x": 100.0}, contest_path=path)
    assert len(placements_week1) == 1
    assert len(placements_week2) == 0
