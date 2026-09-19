import pytest

from nfl_dfs.agents.constructor import NflAgentConstructor
from nfl_dfs.agents.scoring import (
    CLOSE_SPREAD_THRESHOLD_POINTS,
    HIGH_TOTAL_THRESHOLD_POINTS,
    _pool_iqr,
    compute_agent_objective_delta,
)
from nfl_dfs.agents.signal_bundle import PlayerSignals, SignalBundle, build_signal_bundle
from nfl_dfs.composition.player_detail import StackContext
from nfl_dfs.correlation.stack_profile import GameScriptLean
from nfl_dfs.matchup.context import MatchupContextResult
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch
from nfl_dfs.ownership.leverage import LeverageAssessment
from nfl_dfs.projection.blend import PlayerProjection


def _pool(*projections: float) -> list[PlayerProjection]:
    return [
        PlayerProjection(
            canonical_id=f"p{i}",
            display_name=f"p{i}",
            position="WR",
            team="AAA",
            salary=5000,
            blended_projection=proj,
            source_count=2,
            source_values={"rotogrinders": proj, "footballguys": proj},
        )
        for i, proj in enumerate(projections)
    ]


def _signals(
    *,
    ceiling_multiplier: float | None = None,
    leverage: LeverageAssessment | None = None,
    matchup: MatchupContextResult | None = None,
    stack_context: StackContext | None = None,
) -> PlayerSignals:
    return PlayerSignals(
        ceiling_multiplier=ceiling_multiplier, leverage=leverage, matchup=matchup, stack_context=stack_context
    )


def _leverage(ownership_percentile: float) -> LeverageAssessment:
    return LeverageAssessment(
        native_id="rg1",
        name="Test Player",
        position="WR",
        team="AAA",
        salary=6000,
        salary_decile=3,
        projected_ownership=15.0,
        ownership_percentile=ownership_percentile,
        baseline_ownership=None,
        ownership_vs_baseline=None,
        is_chalk=False,
        is_leverage=False,
        note="",
    )


def _matchup(combined_multiplier: float) -> MatchupContextResult:
    return MatchupContextResult(
        canonical_player_id="p0",
        team="AAA",
        opponent="BBB",
        position="WR",
        combined_multiplier=combined_multiplier,
        run_game=None,
        pass_protection=None,
        coverage=None,
        coverage_confidence=None,
    )


def _stack_context(
    *,
    home_team: str = "AAA",
    away_team: str = "BBB",
    home_spread: float = -2.0,
    is_primary_stack_candidate: bool = False,
    is_bring_back_candidate: bool = False,
    is_primary_rb_stack_candidate: bool = False,
    is_bring_back_rb_candidate: bool = False,
    game_script_lean: GameScriptLean | None = None,
    game_stack_viability: float | None = None,
) -> StackContext:
    return StackContext(
        home_team=home_team,
        away_team=away_team,
        home_spread=home_spread,
        single_team_viability=None,
        game_stack_viability=game_stack_viability,
        is_primary_stack_candidate=is_primary_stack_candidate,
        primary_stack_rank=1 if is_primary_stack_candidate else None,
        is_bring_back_candidate=is_bring_back_candidate,
        bring_back_status="candidate" if is_bring_back_candidate else "none",
        pivot_to=None,
        is_primary_rb_stack_candidate=is_primary_rb_stack_candidate,
        is_bring_back_rb_candidate=is_bring_back_rb_candidate,
        game_script_lean=game_script_lean,
    )


def _bundle(signals_by_canonical_id: dict[str, PlayerSignals], implied_total_by_team: dict[str, float] | None = None) -> SignalBundle:
    return SignalBundle(signals_by_canonical_id=signals_by_canonical_id, implied_total_by_team=implied_total_by_team or {})


# --------------------------------------------------------------------------------------------
# NflAgentConstructor validation
# --------------------------------------------------------------------------------------------


def test_agent_constructor_rejects_a_slider_outside_negative_one_to_one():
    with pytest.raises(ValueError):
        NflAgentConstructor(agent_id="bad", display_name="Bad", ceiling_lean=1.5)


def test_agent_constructor_accepts_the_boundary_values():
    NflAgentConstructor(agent_id="ok", display_name="OK", ceiling_lean=1.0, ownership_stance=-1.0)


# --------------------------------------------------------------------------------------------
# _pool_iqr
# --------------------------------------------------------------------------------------------


def test_pool_iqr_computes_a_real_quartile_spread():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0)
    iqr = _pool_iqr(pool)
    assert iqr > 0.0


def test_pool_iqr_is_zero_for_too_few_players():
    assert _pool_iqr(_pool(10.0, 12.0, 14.0)) == 0.0


# --------------------------------------------------------------------------------------------
# compute_agent_objective_delta -- inert-by-default and empty-pool behavior
# --------------------------------------------------------------------------------------------


def test_chalk_anchor_produces_no_delta_at_all():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    bundle = _bundle({p.canonical_id: _signals(ceiling_multiplier=1.3, leverage=_leverage(0.9)) for p in pool})
    chalk_anchor = NflAgentConstructor(agent_id="chalk_anchor", display_name="Chalk Anchor")
    assert compute_agent_objective_delta(chalk_anchor, pool, bundle) == {}


def test_compute_agent_objective_delta_returns_empty_for_a_too_small_pool():
    pool = _pool(10.0, 12.0)
    bundle = _bundle({p.canonical_id: _signals(ceiling_multiplier=1.3) for p in pool})
    agent = NflAgentConstructor(agent_id="a", display_name="A", ceiling_lean=1.0)
    assert compute_agent_objective_delta(agent, pool, bundle) == {}


def test_a_player_with_no_applicable_signal_is_absent_from_the_delta_dict():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    bundle = _bundle({p.canonical_id: _signals() for p in pool})  # no signals at all
    agent = NflAgentConstructor(agent_id="a", display_name="A", ceiling_lean=1.0, ownership_stance=-1.0)
    assert compute_agent_objective_delta(agent, pool, bundle) == {}


# --------------------------------------------------------------------------------------------
# ceiling_lean axis
# --------------------------------------------------------------------------------------------


def test_ceiling_lean_boosts_a_high_ceiling_multiplier_player():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    bundle = _bundle({p.canonical_id: _signals(ceiling_multiplier=1.3) for p in pool})
    agent = NflAgentConstructor(agent_id="explosion", display_name="Explosion", ceiling_lean=1.0)
    deltas = compute_agent_objective_delta(agent, pool, bundle)
    assert all(v > 0 for v in deltas.values())
    assert len(deltas) == len(pool)


def test_ceiling_lean_contributes_nothing_for_a_player_with_no_ceiling_signal():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    bundle = _bundle(
        {
            "p0": _signals(ceiling_multiplier=None),
            **{p.canonical_id: _signals(ceiling_multiplier=1.2) for p in pool[1:]},
        }
    )
    agent = NflAgentConstructor(agent_id="a", display_name="A", ceiling_lean=1.0)
    deltas = compute_agent_objective_delta(agent, pool, bundle)
    assert "p0" not in deltas


# --------------------------------------------------------------------------------------------
# ownership_stance axis -- LeverageAssessment.ownership_percentile is 0.0=most owned, 1.0=least
# owned (inverted from the intuitive reading), so this is the one axis worth double-checking sign.
# --------------------------------------------------------------------------------------------


def test_contrarian_ownership_stance_boosts_the_least_owned_player_and_fades_the_most_owned():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    least_owned, most_owned, *rest = pool
    bundle = _bundle(
        {
            least_owned.canonical_id: _signals(leverage=_leverage(ownership_percentile=1.0)),
            most_owned.canonical_id: _signals(leverage=_leverage(ownership_percentile=0.0)),
            **{p.canonical_id: _signals() for p in rest},
        }
    )
    arbitrageur = NflAgentConstructor(agent_id="arbitrageur", display_name="Arbitrageur", ownership_stance=-0.9)
    deltas = compute_agent_objective_delta(arbitrageur, pool, bundle)
    assert deltas[least_owned.canonical_id] > 0
    assert deltas[most_owned.canonical_id] < 0


def test_chalk_seeking_ownership_stance_boosts_the_most_owned_player():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    most_owned, *rest = pool
    bundle = _bundle(
        {
            most_owned.canonical_id: _signals(leverage=_leverage(ownership_percentile=0.0)),
            **{p.canonical_id: _signals() for p in rest},
        }
    )
    chalk_seeker = NflAgentConstructor(agent_id="chalk", display_name="Chalk", ownership_stance=0.9)
    deltas = compute_agent_objective_delta(chalk_seeker, pool, bundle)
    assert deltas[most_owned.canonical_id] > 0


# --------------------------------------------------------------------------------------------
# matchup_conviction axis
# --------------------------------------------------------------------------------------------


def test_matchup_conviction_boosts_a_favorable_matchup_and_fades_an_unfavorable_one():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    favorable, unfavorable, *rest = pool
    bundle = _bundle(
        {
            favorable.canonical_id: _signals(matchup=_matchup(1.15)),
            unfavorable.canonical_id: _signals(matchup=_matchup(0.85)),
            **{p.canonical_id: _signals() for p in rest},
        }
    )
    matchup_purist = NflAgentConstructor(agent_id="matchup_purist", display_name="Matchup Purist", matchup_conviction=0.8)
    deltas = compute_agent_objective_delta(matchup_purist, pool, bundle)
    assert deltas[favorable.canonical_id] > 0
    assert deltas[unfavorable.canonical_id] < 0


# --------------------------------------------------------------------------------------------
# stack axes -- game_script_lean_weight / stack_conviction, bring-back gating, edge_condition
# --------------------------------------------------------------------------------------------


def test_stack_conviction_boosts_a_real_primary_stack_candidate():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    candidate, *rest = pool
    bundle = _bundle(
        {
            candidate.canonical_id: _signals(
                stack_context=_stack_context(is_primary_stack_candidate=True, game_stack_viability=80.0)
            ),
            **{p.canonical_id: _signals() for p in rest},
        }
    )
    agent = NflAgentConstructor(agent_id="a", display_name="A", stack_conviction=0.8)
    deltas = compute_agent_objective_delta(agent, pool, bundle)
    assert deltas[candidate.canonical_id] > 0


def test_stack_conviction_ignores_a_non_candidate_even_with_a_real_stack_context():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    non_candidate, *rest = pool
    bundle = _bundle(
        {
            non_candidate.canonical_id: _signals(
                stack_context=_stack_context(is_primary_stack_candidate=False, game_stack_viability=80.0)
            ),
            **{p.canonical_id: _signals() for p in rest},
        }
    )
    agent = NflAgentConstructor(agent_id="a", display_name="A", stack_conviction=0.8)
    deltas = compute_agent_objective_delta(agent, pool, bundle)
    assert non_candidate.canonical_id not in deltas


def test_bring_back_candidate_only_boosted_when_bring_back_allowed_is_true():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    bring_back, *rest = pool
    bundle = _bundle(
        {
            bring_back.canonical_id: _signals(
                stack_context=_stack_context(is_bring_back_candidate=True, game_stack_viability=80.0)
            ),
            **{p.canonical_id: _signals() for p in rest},
        }
    )
    not_allowed = NflAgentConstructor(agent_id="a", display_name="A", stack_conviction=0.8, bring_back_allowed=False)
    allowed = NflAgentConstructor(agent_id="b", display_name="B", stack_conviction=0.8, bring_back_allowed=True)

    assert bring_back.canonical_id not in compute_agent_objective_delta(not_allowed, pool, bundle)
    assert bring_back.canonical_id in compute_agent_objective_delta(allowed, pool, bundle)


def test_bring_back_rb_candidate_only_boosted_when_bring_back_rb_allowed_is_true():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    rb_bring_back, *rest = pool
    bundle = _bundle(
        {
            rb_bring_back.canonical_id: _signals(
                stack_context=_stack_context(is_bring_back_rb_candidate=True, game_stack_viability=80.0)
            ),
            **{p.canonical_id: _signals() for p in rest},
        }
    )
    not_allowed = NflAgentConstructor(agent_id="a", display_name="A", stack_conviction=0.8, bring_back_rb_allowed=False)
    allowed = NflAgentConstructor(agent_id="b", display_name="B", stack_conviction=0.8, bring_back_rb_allowed=True)

    assert rb_bring_back.canonical_id not in compute_agent_objective_delta(not_allowed, pool, bundle)
    assert rb_bring_back.canonical_id in compute_agent_objective_delta(allowed, pool, bundle)


def test_game_script_lean_weight_boosts_a_close_game_stack_candidate():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    close_game_candidate, blowout_candidate, *rest = pool
    close_lean = GameScriptLean(stance="favorite", abs_spread=1.0, intensity=1.0)
    blowout_lean = GameScriptLean(stance="favorite", abs_spread=20.0, intensity=0.25)
    bundle = _bundle(
        {
            close_game_candidate.canonical_id: _signals(
                stack_context=_stack_context(is_primary_stack_candidate=True, game_script_lean=close_lean)
            ),
            blowout_candidate.canonical_id: _signals(
                stack_context=_stack_context(is_primary_stack_candidate=True, game_script_lean=blowout_lean)
            ),
            **{p.canonical_id: _signals() for p in rest},
        }
    )
    architect = NflAgentConstructor(agent_id="architect", display_name="Architect", game_script_lean_weight=0.8)
    deltas = compute_agent_objective_delta(architect, pool, bundle)
    assert deltas[close_game_candidate.canonical_id] > 0
    assert deltas[blowout_candidate.canonical_id] < 0


def test_edge_condition_close_spread_gates_the_stack_boost_to_close_games_only():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    close_candidate, wide_candidate, *rest = pool
    bundle = _bundle(
        {
            close_candidate.canonical_id: _signals(
                stack_context=_stack_context(
                    is_primary_stack_candidate=True,
                    home_spread=-1.0,
                    game_stack_viability=80.0,
                )
            ),
            wide_candidate.canonical_id: _signals(
                stack_context=_stack_context(
                    is_primary_stack_candidate=True,
                    home_spread=-14.0,
                    game_stack_viability=80.0,
                )
            ),
            **{p.canonical_id: _signals() for p in rest},
        }
    )
    assert abs(-1.0) <= CLOSE_SPREAD_THRESHOLD_POINTS < abs(-14.0)
    agent = NflAgentConstructor(
        agent_id="architect", display_name="Architect", stack_conviction=0.8, edge_condition="close_spread"
    )
    deltas = compute_agent_objective_delta(agent, pool, bundle)
    assert close_candidate.canonical_id in deltas
    assert wide_candidate.canonical_id not in deltas


def test_edge_condition_high_total_fails_closed_when_implied_totals_are_missing():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    candidate, *rest = pool
    bundle = _bundle(
        {
            candidate.canonical_id: _signals(
                stack_context=_stack_context(is_primary_stack_candidate=True, game_stack_viability=80.0)
            ),
            **{p.canonical_id: _signals() for p in rest},
        },
        implied_total_by_team={},  # no real implied totals this pull
    )
    agent = NflAgentConstructor(
        agent_id="explosion", display_name="Explosion", stack_conviction=0.8, edge_condition="high_total"
    )
    deltas = compute_agent_objective_delta(agent, pool, bundle)
    assert candidate.canonical_id not in deltas


def test_edge_condition_high_total_boosts_when_the_real_game_total_clears_the_threshold():
    pool = _pool(10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
    candidate, *rest = pool
    bundle = _bundle(
        {
            candidate.canonical_id: _signals(
                stack_context=_stack_context(is_primary_stack_candidate=True, game_stack_viability=80.0)
            ),
            **{p.canonical_id: _signals() for p in rest},
        },
        implied_total_by_team={"AAA": 26.0, "BBB": 24.0},  # sums to 50.0 >= HIGH_TOTAL_THRESHOLD_POINTS
    )
    assert 26.0 + 24.0 >= HIGH_TOTAL_THRESHOLD_POINTS
    agent = NflAgentConstructor(
        agent_id="explosion", display_name="Explosion", stack_conviction=0.8, edge_condition="high_total"
    )
    deltas = compute_agent_objective_delta(agent, pool, bundle)
    assert candidate.canonical_id in deltas


# --------------------------------------------------------------------------------------------
# build_signal_bundle -- real join reuse over composition/player_detail.py's private helpers
# --------------------------------------------------------------------------------------------


def test_build_signal_bundle_joins_real_leverage_via_rotogrinders_native_id():
    identity = PlayerIdentity(
        canonical_id="p0",
        display_name="Test Player",
        position="WR",
        team="AAA",
        sources={"rotogrinders": SourceMatch(native_id="rg1", method=MatchMethod.NAME_TEAM_POSITION)},
    )
    leverage_by_native_id = {"rg1": _leverage(ownership_percentile=0.75)}
    bundle = build_signal_bundle([identity], leverage_by_native_id=leverage_by_native_id)
    signals = bundle.signals_by_canonical_id["p0"]
    assert signals.leverage is not None
    assert signals.leverage.ownership_percentile == 0.75


def test_build_signal_bundle_leaves_leverage_none_when_no_rotogrinders_match():
    identity = PlayerIdentity(canonical_id="p0", display_name="Test Player", position="WR", team="AAA")
    bundle = build_signal_bundle([identity], leverage_by_native_id={"rg1": _leverage(0.5)})
    assert bundle.signals_by_canonical_id["p0"].leverage is None


def test_build_signal_bundle_game_total_sums_both_teams_real_implied_totals():
    bundle = _bundle({}, implied_total_by_team={"AAA": 24.0, "BBB": 20.0})
    assert bundle.game_total("AAA", "BBB") == 44.0


def test_build_signal_bundle_game_total_is_none_when_either_side_is_missing():
    bundle = _bundle({}, implied_total_by_team={"AAA": 24.0})
    assert bundle.game_total("AAA", "BBB") is None
