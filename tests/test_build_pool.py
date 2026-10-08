from dataclasses import replace

from nfl_dfs.build.pool.contracts import BuildThesis, ExpertAgentOutput, GroupTier, Pool, PoolEntry, PoolRules, PlayerRef
from nfl_dfs.build.pool.expand import expand_pool
from nfl_dfs.build.pool.validate import core_overlap, promote_at_failing, relax_min_core, validate_pool

TEAMS = ("T1", "T2", "T3", "T4")
THESIS = BuildThesis(("g1:b0",), (), (), (), "test")


def _universe() -> list[PlayerRef]:
    """Realistic shape: higher-priced players project higher, so the top-projection roster spends ~the cap."""
    out = []
    for i, t in enumerate(TEAMS):
        g = f"g{i // 2}"
        out.append(PlayerRef(f"qb_{t}", f"QB {t}", t, "QB", 6000 + 400 * i, 20.0 - i, None, g))
        for k in range(2):
            out.append(PlayerRef(f"rb{k}_{t}", f"RB{k} {t}", t, "RB", 5000 + 1200 * k, 11.0 + 2.5 * k - i * 0.1, None, g))
        for k in range(3):
            out.append(PlayerRef(f"wr{k}_{t}", f"WR{k} {t}", t, "WR", 4500 + 1000 * k, 10.0 + 2.0 * k - i * 0.1, None, g))
        out.append(PlayerRef(f"te_{t}", f"TE {t}", t, "TE", 3500 + 300 * i, 9.0 - i * 0.2, None, g))
        out.append(PlayerRef(f"dst_{t}", f"DST {t}", t, "DST", 2500 + 100 * i, 7.0 - i * 0.2, None, g))
    return out


def _pool(universe, *, default="eligible", core=(), rules=PoolRules(min_core=4), agent="a1") -> Pool:
    entries = tuple(PoolEntry(p.canonical_id, "core" if p.canonical_id in core else default, "r" if p.canonical_id in core else "") for p in universe)
    return Pool(1, agent, THESIS, entries, rules)


CORE = ("qb_T1", "wr2_T1", "rb0_T2", "wr2_T2")


def _codes(report):
    return {x.code for x in report.errors}


def test_expand_applies_default_then_groups_then_overrides_with_later_winning():
    u = _universe()
    out = ExpertAgentOutput(
        "a1", THESIS, "exclude",
        group_tiers=(GroupTier("eligible", "team T1 is live", team="T1"), GroupTier("core", "T1 QB", team="T1", position="QB")),
        overrides=(PoolEntry("wr0_T1", "exclude", "known bust"), PoolEntry("ghost", "core", "typo")),
    )
    pool = expand_pool(out, u)
    tier = {e.canonical_id: e.tier for e in pool.entries}
    assert tier["qb_T1"] == "core" and tier["rb0_T1"] == "eligible" and tier["wr0_T1"] == "exclude" and tier["qb_T2"] == "exclude"
    assert tier["ghost"] == "core"  # kept so the validator can flag it
    assert len([e for e in pool.entries if e.canonical_id == "wr0_T1"]) == 1


def test_a_healthy_deep_pool_validates_cleanly():
    u = _universe()
    report = validate_pool(_pool(u, core=CORE), u)
    assert report.ok, [x.message for x in report.errors]
    assert report.core_count == 4 and report.stripped == ()


def test_entries_must_resolve_have_one_tier_and_a_reason_when_core_or_exclude():
    u = _universe()
    p = _pool(u, core=CORE)
    bad = replace(p, entries=p.entries + (PoolEntry("ghost", "eligible", ""), PoolEntry("qb_T1", "exclude", "x"), PoolEntry("wr0_T2", "core", " ")))
    codes = _codes(validate_pool(bad, u))
    assert {"unknown_player", "contradictory_tiers", "reason_required"} <= codes
    assert "tier" in _codes(validate_pool(replace(p, entries=p.entries + (PoolEntry("wr1_T2", "maybe", ""),)), u))
    dup = replace(p, entries=p.entries + (p.entries[0],))
    assert "duplicate_entry" in _codes(validate_pool(dup, u))


def test_an_unavailable_player_is_stripped_and_a_core_one_is_a_hard_error():
    u = _universe()
    u = [replace(p, status="OUT") if p.canonical_id in ("wr0_T3", "qb_T1") else p for p in u]
    report = validate_pool(_pool(u, core=CORE), u)
    stripped = dict(report.stripped)
    assert "wr0_T3" in stripped and "qb_T1" in stripped
    assert "core_unavailable" in _codes(report)
    assert "wr0_T3" not in report.playable_ids


def test_a_cleared_questionable_player_is_playable_but_plain_q_is_stripped():
    u = _universe()
    u = [replace(p, status="Q_CLEARED") if p.canonical_id == "wr0_T3" else replace(p, status="Q") if p.canonical_id == "wr1_T3" else p for p in u]
    report = validate_pool(_pool(u, core=CORE), u)
    assert "wr0_T3" in report.playable_ids and "wr1_T3" not in report.playable_ids
    assert ("wr1_T3", "status Q") in report.stripped


def test_missing_salary_or_projection_is_stripped_not_crashed():
    u = [replace(p, salary=None) if p.canonical_id == "wr0_T3" else replace(p, projection=None) if p.canonical_id == "wr1_T3" else p for p in _universe()]
    report = validate_pool(_pool(u, core=CORE), u)
    assert {i for i, _ in report.stripped} == {"wr0_T3", "wr1_T3"}


def test_feasibility_depth_floors_per_position():
    u = _universe()
    thin = [p for p in u if not (p.position == "WR" and p.team in ("T2", "T3", "T4"))]  # leaves 3 WR (< 6)
    assert "feasibility_WR" in _codes(validate_pool(_pool(thin, core=CORE), thin))
    one_qb = [p for p in u if not (p.position == "QB" and p.team in ("T2", "T3", "T4"))]
    assert "feasibility_QB" in _codes(validate_pool(_pool(one_qb, core=CORE), one_qb))
    at_the_floor = [p for p in u if not (p.position == "WR" and p.team in ("T3", "T4"))]  # exactly 6 WR: allowed
    assert "feasibility_WR" not in _codes(validate_pool(_pool(at_the_floor, core=CORE), at_the_floor))


def test_at_least_two_qb_teams_must_be_stackable():
    u = [p for p in _universe() if not (p.position in ("WR", "TE") and p.team in ("T2", "T3", "T4"))]
    # T1 keeps 3 WR + TE; the others have no catchers -> only one stackable team
    codes = _codes(validate_pool(_pool(u, core=CORE), u))
    assert "stack_teams" in codes


def test_cap_floor_and_salary_starved_pools_are_flagged():
    u = _universe()
    pricey = [replace(p, salary=(p.salary or 0) + 9000) for p in u]
    assert "cap_floor" in _codes(validate_pool(_pool(pricey, core=CORE), pricey))
    cheap = [replace(p, salary=2500) for p in u]
    assert "salary_starved" in _codes(validate_pool(_pool(cheap, core=CORE), cheap))


def test_min_core_is_enforced_after_stripping():
    u = _universe()
    assert "min_core" in _codes(validate_pool(_pool(u, core=CORE[:2]), u))
    out = [replace(p, status="OUT") if p.canonical_id == "qb_T1" else p for p in u]
    assert "min_core" in _codes(validate_pool(_pool(out, core=CORE), out))  # 3 playable core < 4


def test_dry_solve_must_find_enough_distinct_stacks():
    u = _universe()
    assert "distinct_stacks" in _codes(validate_pool(_pool(u, core=CORE), u, distinct_stack_count=lambda players: 2))
    assert validate_pool(_pool(u, core=CORE), u, distinct_stack_count=lambda players: 5).ok


def test_relax_min_core_steps_down_logs_it_and_stops_at_the_floor():
    u = _universe()
    p = _pool(u, core=CORE, rules=PoolRules(min_core=2))
    p1 = relax_min_core(p)
    assert p1.rules.min_core == 1 and p1.widened_steps == ("relaxed min_core 2 -> 1",)
    assert relax_min_core(p1) is p1


def test_promote_at_failing_adds_only_at_the_failing_position_never_injured_players():
    u = _universe()
    in_pool = [e for e in _pool(u, core=CORE).entries if not e.canonical_id.startswith("wr") or e.canonical_id.endswith(("T1",))]
    pool = Pool(1, "a1", THESIS, tuple(in_pool), PoolRules(min_core=4))
    uni = [replace(p, status="OUT") if p.canonical_id == "wr2_T3" else p for p in u]
    report = validate_pool(pool, uni)
    assert "feasibility_WR" in _codes(report)
    wider = promote_at_failing(pool, uni, report)
    added = {e.canonical_id for e in wider.entries} - {e.canonical_id for e in pool.entries}
    assert added and all(i.startswith("wr") for i in added)  # only WR was failing
    assert "wr2_T3" not in added  # injured, never promoted
    assert all(e.reason.startswith("widened:") for e in wider.entries if e.canonical_id in added)
    assert any("WR" in s for s in wider.widened_steps)


def test_promote_at_failing_is_a_no_op_for_a_passing_pool():
    u = _universe()
    pool = _pool(u, core=CORE)
    assert promote_at_failing(pool, u, validate_pool(pool, u)) is pool


def test_core_overlap_warns_on_near_duplicate_pools_only():
    u = _universe()
    a = _pool(u, core=CORE, agent="a1")
    b = _pool(u, core=CORE, agent="a2")
    c = _pool(u, core=("rb1_T3", "rb1_T4", "wr0_T4", "te_T4"), agent="a3")
    codes = [(x.message.split()[0], x.severity) for x in core_overlap([a, b, c])]
    assert codes == [("a1", "warning")]


def _wr_starved(u, reason):
    """A pool where every non-T1 WR is in the exclude tier with the given reason (leaving only 3 live WR)."""
    pool = _pool(u, core=CORE)
    victims = {p.canonical_id for p in u if p.position == "WR" and p.team != "T1" and p.canonical_id not in CORE}
    return replace(pool, entries=tuple(PoolEntry(e.canonical_id, "exclude", reason) if e.canonical_id in victims else e for e in pool.entries)), victims


def test_widening_never_undoes_an_exclusion_the_expert_made_on_purpose():
    u = _universe()
    pool, victims = _wr_starved(u, "expert: bars the chalk game's top-owned players")
    report = validate_pool(pool, u)
    assert "feasibility_WR" in _codes(report)
    wider = promote_at_failing(pool, u, report)
    assert wider is pool  # nothing promotable: every other WR was deliberately excluded -> the build must fail loudly instead


def test_default_tier_players_are_retiered_in_place_without_a_duplicate_entry():
    u = _universe()
    pool, victims = _wr_starved(u, "default tier")
    report = validate_pool(pool, u)
    wider = promote_at_failing(pool, u, report)
    ids = [e.canonical_id for e in wider.entries]
    assert len(ids) == len(set(ids))  # never a second entry for the same player
    retiered = {e.canonical_id for e in wider.entries if e.reason.startswith("widened:")}
    assert retiered and retiered <= victims
    assert validate_pool(wider, u).ok  # and the widened pool now validates (no contradictory_tiers)
