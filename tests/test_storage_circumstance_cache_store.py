from nfl_dfs.analysis.circumstance.engine import CircumstanceAssessment
from nfl_dfs.storage.circumstance_cache_store import (
    FilesystemCircumstanceCache,
    assessment_path,
    cache_key,
    has_cached_assessment,
    read_cached_assessment,
    write_cached_assessment,
)


class _FakeSource:
    def __init__(self, *, kind="injury", team="MIN", season=2026, week=2, facts=None):
        self._kind = kind
        self._team = team
        self._season = season
        self._week = week
        self._facts = facts if facts is not None else {"departed_status": "OUT"}

    def circumstance_kind(self):
        return self._kind

    def circumstance_team(self):
        return self._team

    def circumstance_season(self):
        return self._season

    def circumstance_week(self):
        return self._week

    def circumstance_facts(self):
        return self._facts


def _assessment(pov: str = "Real synthesized text.") -> CircumstanceAssessment:
    return CircumstanceAssessment(
        kind="injury",
        pov=pov,
        model="claude-sonnet-5",
        generated_at="2026-09-19T12:00:00+00:00",
        evidence_article_titles=["Vikings backfield notes"],
        input_tokens=5074,
        output_tokens=770,
        thinking_tokens=0,
    )


# --------------------------------------------------------------------------------------------
# cache_key -- content-addressed, self-invalidating
# --------------------------------------------------------------------------------------------


def test_cache_key_is_stable_for_the_same_facts():
    assert cache_key(_FakeSource()) == cache_key(_FakeSource())


def test_cache_key_changes_when_facts_change():
    a = _FakeSource(facts={"departed_status": "Q"})
    b = _FakeSource(facts={"departed_status": "OUT"})
    assert cache_key(a) != cache_key(b)


def test_cache_key_changes_when_kind_or_team_changes():
    base = cache_key(_FakeSource())
    assert cache_key(_FakeSource(kind="matchup_extreme")) != base
    assert cache_key(_FakeSource(team="BAL")) != base


def test_cache_key_is_insensitive_to_dict_insertion_order():
    a = _FakeSource(facts={"x": 1, "y": 2})
    b = _FakeSource(facts={"y": 2, "x": 1})
    assert cache_key(a) == cache_key(b)


# --------------------------------------------------------------------------------------------
# has_cached_assessment / write_cached_assessment / read_cached_assessment
# --------------------------------------------------------------------------------------------


def test_has_cached_assessment_false_when_nothing_written(tmp_path):
    assert has_cached_assessment(_FakeSource(), base_dir=tmp_path) is False


def test_write_then_has_cached_assessment_true(tmp_path):
    write_cached_assessment(_FakeSource(), _assessment(), base_dir=tmp_path)
    assert has_cached_assessment(_FakeSource(), base_dir=tmp_path) is True


def test_write_then_read_round_trips(tmp_path):
    source = _FakeSource()
    assessment = _assessment("Jones should see an expanded role.")
    write_cached_assessment(source, assessment, base_dir=tmp_path)

    read_back = read_cached_assessment(source, base_dir=tmp_path)

    assert read_back == assessment


def test_a_different_fact_is_a_real_cache_miss(tmp_path):
    # The whole point: a genuine status flip must never serve a stale cached assessment.
    write_cached_assessment(_FakeSource(facts={"departed_status": "Q"}), _assessment(), base_dir=tmp_path)
    assert has_cached_assessment(_FakeSource(facts={"departed_status": "OUT"}), base_dir=tmp_path) is False


def test_assessment_path_is_partitioned_by_season_week_kind(tmp_path):
    path = assessment_path(_FakeSource(season=2026, week=2, kind="injury"), base_dir=tmp_path)
    assert path.parent.parent.parent.parent == tmp_path
    assert path.parent.name == "injury"
    assert path.parent.parent.name == "2"
    assert path.parent.parent.parent.name == "2026"


# --------------------------------------------------------------------------------------------
# FilesystemCircumstanceCache -- the CircumstanceCache-shaped object synthesize_circumstance uses
# --------------------------------------------------------------------------------------------


def test_filesystem_cache_has_read_write_round_trip(tmp_path):
    cache = FilesystemCircumstanceCache(base_dir=tmp_path)
    source = _FakeSource()
    assert cache.has(source) is False

    assessment = _assessment()
    cache.write(source, assessment)

    assert cache.has(source) is True
    assert cache.read(source) == assessment
