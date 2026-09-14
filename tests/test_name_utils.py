from nfl_dfs.normalization.name_utils import names_match, normalize_name


def test_strips_suffixes():
    assert normalize_name("Michael Pittman Jr.") == "michael pittman"
    assert normalize_name("Odell Beckham Jr.") == "odell beckham"
    assert normalize_name("Marvin Harrison III") == "marvin harrison"


def test_strips_punctuation_and_periods():
    assert normalize_name("D.J. Moore") == "dj moore"


def test_strips_apostrophes():
    assert normalize_name("De'Von Achane") == "devon achane"


def test_transliterates_accents():
    assert normalize_name("Öllie Górdon") == "ollie gordon"
    assert normalize_name("Ollie Gordon") == "ollie gordon"


def test_case_insensitive():
    assert normalize_name("JUSTIN JEFFERSON") == normalize_name("justin jefferson")


def test_names_match_requires_exact_normalized_equality():
    assert names_match("Jahmyr Gibbs", "jahmyr   gibbs") is True
    # Deliberately no initials tolerance: "J. Gibbs" is NOT treated as equivalent to
    # "Jahmyr Gibbs" -- see name_utils.names_match's docstring for the reasoning.
    assert names_match("Jahmyr Gibbs", "J. Gibbs") is False
    assert names_match("Jahmyr Gibbs", "Jack Gibbens") is False
