from nfl_dfs.config import Config


def test_config_reports_missing_credentials_by_field_name():
    cfg = Config(pff_api_key="x", odds_api_key=None)
    assert "pff_api_key" not in cfg.missing()
    assert "odds_api_key" in cfg.missing()
