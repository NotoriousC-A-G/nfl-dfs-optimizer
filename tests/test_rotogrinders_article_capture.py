from scripts.rotogrinders_article_capture import _platform_mixed


def test_platform_mixed_true_when_title_names_both_platforms():
    assert _platform_mixed("NFL DFS Picks: DraftKings & FanDuel Expert Survey for Week 2") is True


def test_platform_mixed_true_when_title_names_neither_platform():
    # The real, common case -- RotoGrinders NFL titles often don't name a platform at all
    # ("NFL DFS Picks: Noto's Key Personnel for Week 2") but still cover both in the body.
    # Defaults to True (mixed/uncertain) per Chris's explicit request, not to False.
    assert _platform_mixed("NFL DFS Picks: Noto's Key Personnel for Week 2") is True


def test_platform_mixed_false_only_when_title_names_draftkings_and_not_fanduel():
    assert _platform_mixed("NFL DFS Picks: DraftKings-Only Cash Game Plays") is False


def test_platform_mixed_true_when_title_names_fanduel_only():
    assert _platform_mixed("NFL DFS Picks: FanDuel-Only Cash Game Plays") is True
