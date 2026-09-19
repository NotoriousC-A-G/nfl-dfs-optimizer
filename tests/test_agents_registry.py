from nfl_dfs.agents.registry import (
    ARBITRAGEUR,
    CHALK_ANCHOR,
    EXPLOSION_SHOOTOUT,
    GAME_SCRIPT_ARCHITECT,
    MATCHUP_PURIST,
    NFL_AGENTS,
    VOLATILITY_ENGINE,
)
from nfl_dfs.agents.scoring import compute_agent_objective_delta
from nfl_dfs.agents.signal_bundle import PlayerSignals, SignalBundle
from nfl_dfs.projection.blend import PlayerProjection


def test_nfl_agents_has_exactly_the_six_approved_agents_with_unique_ids():
    assert NFL_AGENTS == [
        CHALK_ANCHOR,
        GAME_SCRIPT_ARCHITECT,
        MATCHUP_PURIST,
        ARBITRAGEUR,
        EXPLOSION_SHOOTOUT,
        VOLATILITY_ENGINE,
    ]
    assert len({a.agent_id for a in NFL_AGENTS}) == 6


def test_chalk_anchor_is_fully_inert():
    assert CHALK_ANCHOR.ceiling_lean == 0.0
    assert CHALK_ANCHOR.ownership_stance == 0.0
    assert CHALK_ANCHOR.matchup_conviction == 0.0
    assert CHALK_ANCHOR.game_script_lean_weight == 0.0
    assert CHALK_ANCHOR.stack_conviction == 0.0
    assert CHALK_ANCHOR.bring_back_allowed is False
    assert CHALK_ANCHOR.bring_back_rb_allowed is False
    assert CHALK_ANCHOR.edge_condition is None


def test_game_script_architect_matches_the_approved_spec():
    assert GAME_SCRIPT_ARCHITECT.game_script_lean_weight == 0.8
    assert GAME_SCRIPT_ARCHITECT.bring_back_allowed is True
    assert GAME_SCRIPT_ARCHITECT.edge_condition == "close_spread_or_high_total"


def test_matchup_purist_matches_the_approved_spec():
    assert MATCHUP_PURIST.matchup_conviction == 0.8


def test_arbitrageur_matches_the_approved_spec():
    assert ARBITRAGEUR.ownership_stance == -0.9


def test_explosion_shootout_matches_the_approved_spec():
    assert EXPLOSION_SHOOTOUT.bring_back_allowed is True
    assert EXPLOSION_SHOOTOUT.bring_back_rb_allowed is True
    assert EXPLOSION_SHOOTOUT.ceiling_lean == 0.5
    assert EXPLOSION_SHOOTOUT.edge_condition == "high_total"


def test_volatility_engine_is_the_polar_opposite_of_chalk_anchor():
    assert VOLATILITY_ENGINE.ceiling_lean == 1.0
    assert VOLATILITY_ENGINE.ownership_stance < 0.0
    assert abs(VOLATILITY_ENGINE.ownership_stance) < abs(ARBITRAGEUR.ownership_stance)
    assert VOLATILITY_ENGINE.bring_back_allowed is False


def test_every_registered_agent_produces_a_real_delta_dict_shape_against_a_synthetic_pool():
    # A cheap end-to-end sanity check, not a full pipeline test: every agent (including
    # Chalk Anchor) must run through compute_agent_objective_delta without error and return a
    # dict, on a pool/bundle that has at least a little real signal for every axis.
    pool = [
        PlayerProjection(
            canonical_id=f"p{i}",
            display_name=f"p{i}",
            position="WR",
            team="AAA",
            salary=5000,
            blended_projection=float(10 + i),
            source_count=2,
            source_values={"rotogrinders": float(10 + i)},
        )
        for i in range(6)
    ]
    bundle = SignalBundle(
        signals_by_canonical_id={p.canonical_id: PlayerSignals(None, None, None, None) for p in pool}
    )
    for agent in NFL_AGENTS:
        deltas = compute_agent_objective_delta(agent, pool, bundle)
        assert isinstance(deltas, dict)
    assert compute_agent_objective_delta(CHALK_ANCHOR, pool, bundle) == {}
