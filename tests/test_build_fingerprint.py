from dataclasses import replace

from nfl_dfs.build.evidence.contracts import AvailabilityItem, with_sha
from nfl_dfs.build.evidence.fingerprint import material_fingerprint
from nfl_dfs.build.thesis.stage import analyst_specs
from tests._build_fixtures import _packet, league_fn


def _fp(p):
    return material_fingerprint(with_sha(p))


def test_projection_ownership_salary_and_capture_time_drift_does_not_change_the_fingerprint():
    p = _packet()
    drift = replace(p, players=tuple(replace(pl, projection=(pl.projection or 0) + 0.4, ownership_pct=(pl.ownership_pct or 0) + 1.0, salary=(pl.salary or 0) + 100) for pl in p.players))
    assert with_sha(p).packet_sha != with_sha(drift).packet_sha  # the hash moves...
    assert _fp(p) == _fp(drift)  # ...the material facts do not


def test_a_changed_line_availability_vacated_role_unit_metric_or_weather_does_change_it():
    from nfl_dfs.build.evidence.opportunity import VacatedRole
    p = _packet()
    base = _fp(p)
    assert _fp(replace(p, lines=replace(p.lines, abs_spread=p.lines.abs_spread + 1.0))) != base
    assert _fp(replace(p, lines=replace(p.lines, total=(p.lines.total or 40.0) + 1.0))) != base
    assert _fp(replace(p, availability=p.availability + (AvailabilityItem("X Y", p.home, "barred", "gtd", "override", "t"),))) != base
    assert _fp(replace(p, vacated=(VacatedRole("id1", "X Y", p.home, 0.1, 0.2, "Q"),))) != base
    assert _fp(replace(p, weather={"wind_mph": 22.0, "is_indoor": False})) != base
    first = p.players[0]
    assert _fp(replace(p, players=(replace(first, status_after_q_pass="BARRED"),) + p.players[1:])) != base


def test_analyst_cache_keys_survive_projection_drift_but_not_a_new_vacated_role():
    from nfl_dfs.build.evidence.opportunity import VacatedRole
    p = with_sha(_packet())
    drift = with_sha(replace(p, players=tuple(replace(pl, projection=(pl.projection or 0) + 0.7) for pl in p.players)))
    changed = with_sha(replace(p, vacated=(VacatedRole("id1", "X Y", p.home, 0.1, 0.2, "OUT"),)))
    key = lambda pk: analyst_specs({pk.game_id: pk}, league_fn, model="m", freshness="f")[0].key
    assert key(p) == key(drift)
    assert key(p) != key(changed)
