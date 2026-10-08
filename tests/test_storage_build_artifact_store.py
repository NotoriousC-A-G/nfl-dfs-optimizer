from nfl_dfs.storage.build_artifact_store import load_latest_build_artifact, save_build_artifact


def test_saves_without_overwriting_and_loads_the_latest(tmp_path):
    a = save_build_artifact(2026, 5, {"agents": {"x": {"status": "awaiting_repair"}}}, base_dir=tmp_path)
    b = save_build_artifact(2026, 5, {"agents": {"x": {"status": "built"}}}, base_dir=tmp_path)
    assert a != b and a.exists() and b.exists()
    latest = load_latest_build_artifact(2026, 5, base_dir=tmp_path)
    assert latest["agents"]["x"]["status"] == "built" and latest["week"] == 5


def test_no_artifact_is_none_not_an_empty_record(tmp_path):
    assert load_latest_build_artifact(2026, 5, base_dir=tmp_path) is None
    save_build_artifact(2026, 4, {}, base_dir=tmp_path)
    assert load_latest_build_artifact(2026, 5, base_dir=tmp_path) is None
