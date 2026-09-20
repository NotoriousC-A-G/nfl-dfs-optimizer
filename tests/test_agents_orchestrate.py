import pytest

from nfl_dfs.agents.constructor import NflAgentConstructor
from nfl_dfs.agents.orchestrate import (
    agents_with_suspiciously_empty_deltas,
    chalk_anchor_matches_baseline,
    distinct_core_stack_count,
    generate_agent_lineups,
    pairwise_lineup_overlap,
    summarize_agent_deltas,
)
from nfl_dfs.agents.registry import CHALK_ANCHOR, NFL_AGENTS
from nfl_dfs.agents.signal_bundle import PlayerSignals, SignalBundle
from nfl_dfs.analysis.dup_risk_calibration import DupRiskLookupTable
from nfl_dfs.composition.player_detail import StackContext
from nfl_dfs.optimizer.lineup import LineupGenerationError, generate_lineups
from nfl_dfs.ownership.leverage import LeverageAssessment
from nfl_dfs.projection.blend import PlayerProjection


def _fake_dup_risk_table(bucket_upper_bounds: tuple[float, ...]) -> DupRiskLookupTable:
    n_buckets = len(bucket_upper_bounds) + 1
    return DupRiskLookupTable(
        seasons=(2023, 2024, 2025), n_rows=1000, bucket_upper_bounds=bucket_upper_bounds,
        bucket_dup_rate={i: 0.01 * i for i in range(n_buckets)}, bucket_mean_lineup_ct={i: 1.0 for i in range(n_buckets)},
    )


def _p(canonical_id: str, position: str, team: str, salary: int, projection: float) -> PlayerProjection:
    return PlayerProjection(
        canonical_id=canonical_id,
        display_name=canonical_id,
        position=position,
        team=team,
        salary=salary,
        blended_projection=projection,
        source_count=2,
        source_values={"rotogrinders": projection, "footballguys": projection},
    )


def _synthetic_pool() -> list[PlayerProjection]:
    """Same shape as tests/test_lineup.py's own fixture (2 real teams, real pass-catcher depth on
    each, plus cheap filler) -- reused here rather than re-derived so a legal 9-player lineup is
    always constructible under the $50,000 cap.
    """
    return [
        _p("qb_a", "QB", "AAA", 7500, 22.0),
        _p("wr_a1", "WR", "AAA", 7000, 18.0),
        _p("wr_a2", "WR", "AAA", 6000, 14.0),
        _p("te_a1", "TE", "AAA", 4500, 10.0),
        _p("rb_a1", "RB", "AAA", 6500, 15.0),
        _p("rb_a2", "RB", "AAA", 5000, 11.0),
        _p("dst_a", "DST", "AAA", 3000, 8.0),
        _p("qb_b", "QB", "BBB", 7200, 21.0),
        _p("wr_b1", "WR", "BBB", 6800, 17.0),
        _p("wr_b2", "WR", "BBB", 5800, 13.5),
        _p("te_b1", "TE", "BBB", 4200, 9.5),
        _p("rb_b1", "RB", "BBB", 6200, 14.5),
        _p("rb_b2", "RB", "BBB", 4800, 10.5),
        _p("dst_b", "DST", "BBB", 2800, 7.5),
        _p("wr_c1", "WR", "CCC", 3500, 8.0),
        _p("wr_c2", "WR", "CCC", 3200, 7.0),
        _p("te_c1", "TE", "CCC", 2800, 5.0),
        _p("rb_c1", "RB", "CCC", 3800, 8.5),
        _p("rb_d1", "RB", "DDD", 3600, 7.5),
    ]


def _many_teams_pool(n_teams: int = 8) -> list[PlayerProjection]:
    """`n_teams` distinct teams, each with a full QB+2WR+TE+2RB+DST set at slate-legal salaries --
    deep enough real diversity that 6 agents can each land on a genuinely distinct real
    QB+pass-catcher core stack, unlike the small 2-team `_synthetic_pool` above (which is used
    deliberately for the diversity-exhausted fallback test instead).
    """
    players: list[PlayerProjection] = []
    for i in range(n_teams):
        team = f"T{i}"
        base = 20.0 - i * 0.3  # slight spread so each team's stack is a genuinely different optimum
        players += [
            _p(f"qb_{team}", "QB", team, 7000, base),
            _p(f"wr1_{team}", "WR", team, 6500, base - 3),
            _p(f"wr2_{team}", "WR", team, 5500, base - 6),
            _p(f"te_{team}", "TE", team, 4000, base - 9),
            _p(f"rb1_{team}", "RB", team, 6000, base - 4),
            _p(f"rb2_{team}", "RB", team, 4500, base - 8),
            _p(f"dst_{team}", "DST", team, 2500, base - 12),
        ]
    return players


def _leverage(ownership_percentile: float) -> LeverageAssessment:
    return LeverageAssessment(
        native_id="rg1", name="t", position="WR", team="AAA", salary=6000, salary_decile=3,
        projected_ownership=15.0, ownership_percentile=ownership_percentile, baseline_ownership=None,
        ownership_vs_baseline=None, is_chalk=False, is_leverage=False, note="",
    )


def _leverage_with_ownership(projected_ownership: float) -> LeverageAssessment:
    return LeverageAssessment(
        native_id="rg1", name="t", position="WR", team="AAA", salary=6000, salary_decile=3,
        projected_ownership=projected_ownership, ownership_percentile=0.5, baseline_ownership=None,
        ownership_vs_baseline=None, is_chalk=False, is_leverage=False, note="",
    )


def _bundle_with_real_ownership_for_every_player(pool: list[PlayerProjection], own: float = 10.0) -> SignalBundle:
    """Every player carries a real `leverage.projected_ownership` -- enough real coverage for
    `_avg_projected_ownership` to clear `MIN_OWNERSHIP_COVERAGE` on any 9-player lineup drawn from
    `pool`, so `achieved_bucket`/`gpp_grade` can be computed for real in a test.
    """
    return SignalBundle(
        signals_by_canonical_id={
            p.canonical_id: PlayerSignals(None, _leverage_with_ownership(own), None, None) for p in pool
        }
    )


def _stack_context(**overrides) -> StackContext:
    defaults = dict(
        home_team="AAA", away_team="BBB", home_spread=-2.0, single_team_viability=None,
        game_stack_viability=80.0, is_primary_stack_candidate=False, primary_stack_rank=None,
        is_bring_back_candidate=False, bring_back_status="none", pivot_to=None,
        is_primary_rb_stack_candidate=False, is_bring_back_rb_candidate=False, game_script_lean=None,
    )
    defaults.update(overrides)
    return StackContext(**defaults)


def _bundle_with_real_signal_for_every_agent(pool: list[PlayerProjection]) -> SignalBundle:
    """Gives every non-inert axis at least one player with a real, applicable signal, so every
    NFL_AGENTS entry (except the deliberately-inert Chalk Anchor) produces a nonempty delta.
    """
    signals = {}
    for p in pool:
        if p.canonical_id == "wr_a1":
            signals[p.canonical_id] = PlayerSignals(
                ceiling_multiplier=1.3,
                leverage=_leverage(ownership_percentile=1.0),
                matchup=None,
                stack_context=_stack_context(is_primary_stack_candidate=True, is_bring_back_candidate=True),
            )
        elif p.canonical_id == "rb_a1":
            signals[p.canonical_id] = PlayerSignals(
                ceiling_multiplier=None,
                leverage=None,
                matchup=None,
                stack_context=_stack_context(is_primary_rb_stack_candidate=True, is_bring_back_rb_candidate=True),
            )
        else:
            signals[p.canonical_id] = PlayerSignals(None, None, None, None)
    return SignalBundle(signals_by_canonical_id=signals)


def test_generate_agent_lineups_returns_one_result_per_agent():
    pool = _synthetic_pool()
    bundle = _bundle_with_real_signal_for_every_agent(pool)
    results = generate_agent_lineups(pool, bundle)
    assert [r.agent.agent_id for r in results] == [a.agent_id for a in NFL_AGENTS]
    for r in results:
        assert len(r.lineup.players) == 9


def test_generate_agent_lineups_defaults_to_nfl_agents_when_none_supplied():
    pool = _synthetic_pool()
    bundle = SignalBundle(signals_by_canonical_id={p.canonical_id: PlayerSignals(None, None, None, None) for p in pool})
    results = generate_agent_lineups(pool, bundle)
    assert len(results) == len(NFL_AGENTS)


def test_generate_agent_lineups_accepts_a_custom_agent_subset():
    pool = _synthetic_pool()
    bundle = SignalBundle(signals_by_canonical_id={p.canonical_id: PlayerSignals(None, None, None, None) for p in pool})
    custom = [CHALK_ANCHOR, NflAgentConstructor(agent_id="custom", display_name="Custom", ceiling_lean=1.0)]
    results = generate_agent_lineups(pool, bundle, agents=custom)
    assert [r.agent.agent_id for r in results] == ["chalk_anchor", "custom"]


def test_generate_agent_lineups_propagates_lineup_generation_error_for_an_infeasible_pool():
    tiny_pool = [_p("qb_a", "QB", "AAA", 7500, 22.0)]
    bundle = SignalBundle(signals_by_canonical_id={})
    with pytest.raises(LineupGenerationError):
        generate_agent_lineups(tiny_pool, bundle)


# --------------------------------------------------------------------------------------------
# Cross-agent core-stack diversity (Chris, 2026-09-20: 4 of 6 agents converged onto just 2
# distinct core stacks on the first real live run -- "you're modeling outcomes and the range of
# outcomes, not that narrow"). generate_agent_lineups must forbid a later agent from repeating an
# earlier agent's exact QB+pass-catcher combination whenever the pool has enough real diversity to
# support it, and must degrade gracefully (never crash the whole run) when it doesn't.
# --------------------------------------------------------------------------------------------


def test_generate_agent_lineups_gives_every_agent_a_distinct_core_stack_on_a_diverse_pool():
    pool = _many_teams_pool(n_teams=8)
    bundle = SignalBundle(signals_by_canonical_id={p.canonical_id: PlayerSignals(None, None, None, None) for p in pool})
    results = generate_agent_lineups(pool, bundle)
    assert len(results) == len(NFL_AGENTS)
    assert distinct_core_stack_count(results) == len(results)
    assert all(r.core_stack_forced_unique for r in results)


def test_generate_agent_lineups_falls_back_gracefully_when_diversity_is_exhausted():
    # A single QB with exactly 3 WR + 1 TE (its whole team's pass-catcher room, no substitutes
    # anywhere in the pool) forces WR=3/TE=1/RB=3 every solve, per the roster-count constraints --
    # so this pool has exactly ONE real core stack achievable, ever. Confirms the run still
    # completes for all 6 agents (never raises) and marks every agent after the first via
    # core_stack_forced_unique=False rather than silently pretending the cut succeeded.
    only_stack_pool = [
        _p("qb_x", "QB", "XXX", 7000, 24.0),
        _p("wr_x1", "WR", "XXX", 6500, 18.0),
        _p("wr_x2", "WR", "XXX", 5500, 14.0),
        _p("wr_x3", "WR", "XXX", 4500, 11.0),
        _p("te_x", "TE", "XXX", 4000, 9.0),
        _p("rb_y1", "RB", "YYY", 5500, 13.0),
        _p("rb_y2", "RB", "YYY", 4500, 10.0),
        _p("rb_z1", "RB", "ZZZ", 3500, 8.0),
        _p("dst_y", "DST", "YYY", 2500, 7.0),
    ]
    bundle = SignalBundle(
        signals_by_canonical_id={p.canonical_id: PlayerSignals(None, None, None, None) for p in only_stack_pool}
    )
    results = generate_agent_lineups(only_stack_pool, bundle)
    assert len(results) == len(NFL_AGENTS)
    assert distinct_core_stack_count(results) == 1
    assert any(not r.core_stack_forced_unique for r in results)
    assert results[0].core_stack_forced_unique is True  # Chalk Anchor is never cut


def test_chalk_anchor_always_solves_unconstrained_first():
    # Chalk Anchor must stay the TRUE best-projection baseline -- never diversity-adjusted, even
    # though it's still the first entry a later agent's cut is seeded from.
    pool = _many_teams_pool(n_teams=8)
    bundle = SignalBundle(signals_by_canonical_id={p.canonical_id: PlayerSignals(None, None, None, None) for p in pool})
    results = generate_agent_lineups(pool, bundle)
    baseline = generate_lineups(pool, n=1)[0]
    chalk = next(r for r in results if r.agent.agent_id == "chalk_anchor")
    assert chalk.core_stack_forced_unique is True
    assert {p.canonical_id for p in chalk.lineup.players} == {p.canonical_id for p in baseline.players}


# --------------------------------------------------------------------------------------------
# Real, descriptive achieved_bucket/gpp_grade (2026-09-20: agent lineups became the dashboard's
# primary lineup set, replacing the separate ownership-bucket-TARGETING mechanism -- Chris: "if we
# have 6 agents, in theory we should get 6 lineups". These reads are descriptive only: an agent's
# own preference axes already produced whatever ownership tier its lineup landed in; nothing here
# searches for or targets a bucket the way the retired mechanism did.
# --------------------------------------------------------------------------------------------


def test_generate_agent_lineups_leaves_bucket_and_grade_none_when_not_requested():
    pool = _many_teams_pool(n_teams=8)
    bundle = _bundle_with_real_ownership_for_every_player(pool)
    results = generate_agent_lineups(pool, bundle)  # dup_risk_table/game_count both omitted
    assert all(r.achieved_bucket is None for r in results)
    assert all(r.gpp_grade is None for r in results)


def test_generate_agent_lineups_computes_a_real_achieved_bucket_when_dup_risk_table_supplied():
    pool = _many_teams_pool(n_teams=8)
    bundle = _bundle_with_real_ownership_for_every_player(pool, own=10.0)
    table = _fake_dup_risk_table((5.0, 15.0, 25.0, 35.0, 45.0, 55.0, 65.0, 75.0, 85.0))
    results = generate_agent_lineups(pool, bundle, dup_risk_table=table)
    # Every player is at a flat 10.0% ownership -> every lineup's real average is 10.0 -> bucket 1.
    assert all(r.achieved_bucket == 1 for r in results)
    assert all(r.gpp_grade is None for r in results)  # game_count still omitted


def test_generate_agent_lineups_computes_a_real_gpp_grade_when_game_count_supplied():
    pool = _many_teams_pool(n_teams=8)
    bundle = _bundle_with_real_ownership_for_every_player(pool)
    results = generate_agent_lineups(pool, bundle, game_count=13)
    assert all(r.gpp_grade is not None for r in results)
    assert all(r.gpp_grade.grade in {"A", "B", "C", "D"} for r in results)
    assert all(r.achieved_bucket is None for r in results)  # dup_risk_table still omitted


def test_generate_agent_lineups_gpp_grade_reflects_no_real_ceiling_signal_when_none_supplied():
    # No PlayerSignals here carry a real ceiling_multiplier -- every grade's ceiling component
    # must honestly disclose that, not silently assume a value.
    pool = _many_teams_pool(n_teams=8)
    bundle = _bundle_with_real_ownership_for_every_player(pool)
    results = generate_agent_lineups(pool, bundle, game_count=13)
    for r in results:
        assert r.gpp_grade.ceiling_ratio is None
        assert r.gpp_grade.ceiling_reason is not None


# --------------------------------------------------------------------------------------------
# Verification-first diagnostics
# --------------------------------------------------------------------------------------------


def test_chalk_anchor_matches_baseline_is_true_for_real_generation():
    pool = _synthetic_pool()
    bundle = _bundle_with_real_signal_for_every_agent(pool)
    results = generate_agent_lineups(pool, bundle)
    baseline = generate_lineups(pool, n=1)[0]
    assert chalk_anchor_matches_baseline(results, baseline) is True


def test_chalk_anchor_matches_baseline_is_false_when_chalk_anchor_is_missing():
    pool = _synthetic_pool()
    bundle = _bundle_with_real_signal_for_every_agent(pool)
    custom = [a for a in NFL_AGENTS if a.agent_id != "chalk_anchor"]
    results = generate_agent_lineups(pool, bundle, agents=custom)
    baseline = generate_lineups(pool, n=1)[0]
    assert chalk_anchor_matches_baseline(results, baseline) is False


def test_summarize_agent_deltas_reports_real_stats_and_zero_for_an_empty_delta():
    pool = _synthetic_pool()
    bundle = _bundle_with_real_signal_for_every_agent(pool)
    results = generate_agent_lineups(pool, bundle)
    stats = summarize_agent_deltas(results)
    by_id = {s.agent_id: s for s in stats}
    assert by_id["chalk_anchor"].n_players_affected == 0
    assert by_id["chalk_anchor"].delta_sum == 0.0
    non_inert = by_id["matchup_purist"]
    # matchup_purist has no matchup signal in this fixture at all -- also legitimately empty.
    assert non_inert.n_players_affected == 0


def test_agents_with_suspiciously_empty_deltas_flags_a_real_wiring_gap_but_not_chalk_anchor():
    pool = _synthetic_pool()
    bundle = SignalBundle(signals_by_canonical_id={p.canonical_id: PlayerSignals(None, None, None, None) for p in pool})
    results = generate_agent_lineups(pool, bundle)
    flagged = agents_with_suspiciously_empty_deltas(results)
    assert "chalk_anchor" not in flagged
    # every non-inert agent has zero applicable signal in this fixture, so all of them should flag.
    assert set(flagged) == {a.agent_id for a in NFL_AGENTS if a.agent_id != "chalk_anchor"}


def test_agents_with_suspiciously_empty_deltas_is_empty_when_signals_are_present():
    pool = _synthetic_pool()
    bundle = _bundle_with_real_signal_for_every_agent(pool)
    results = generate_agent_lineups(pool, bundle)
    flagged = agents_with_suspiciously_empty_deltas(results)
    # arbitrageur (ownership_stance) and explosion_shootout/volatility_engine (ceiling_lean) all
    # have a real applicable signal in this fixture (wr_a1's leverage/ceiling_multiplier).
    assert "arbitrageur" not in flagged
    assert "explosion_shootout" not in flagged
    assert "volatility_engine" not in flagged


def test_pairwise_lineup_overlap_covers_every_distinct_pair():
    pool = _synthetic_pool()
    bundle = _bundle_with_real_signal_for_every_agent(pool)
    results = generate_agent_lineups(pool, bundle)
    overlap = pairwise_lineup_overlap(results)
    n = len(results)
    assert len(overlap) == n * (n - 1) // 2
    for (a_id, b_id), count in overlap.items():
        assert a_id != b_id
        assert 0 <= count <= 9
