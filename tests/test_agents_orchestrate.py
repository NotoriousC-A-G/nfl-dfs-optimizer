import pytest

from nfl_dfs.agents.constructor import NflAgentConstructor
from nfl_dfs.agents.orchestrate import (
    agents_with_suspiciously_empty_deltas,
    chalk_anchor_matches_baseline,
    generate_agent_lineups,
    pairwise_lineup_overlap,
    summarize_agent_deltas,
)
from nfl_dfs.agents.registry import CHALK_ANCHOR, NFL_AGENTS
from nfl_dfs.agents.signal_bundle import PlayerSignals, SignalBundle
from nfl_dfs.composition.player_detail import StackContext
from nfl_dfs.optimizer.lineup import LineupGenerationError, generate_lineups
from nfl_dfs.ownership.leverage import LeverageAssessment
from nfl_dfs.projection.blend import PlayerProjection


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


def _leverage(ownership_percentile: float) -> LeverageAssessment:
    return LeverageAssessment(
        native_id="rg1", name="t", position="WR", team="AAA", salary=6000, salary_decile=3,
        projected_ownership=15.0, ownership_percentile=ownership_percentile, baseline_ownership=None,
        ownership_vs_baseline=None, is_chalk=False, is_leverage=False, note="",
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
