from nfl_dfs.storage.rotogrinders_article_store import (
    RotoGrindersArchivedArticle,
    article_path,
    has_article,
    list_article_slugs,
    read_all_articles,
    read_article,
    write_article,
)


def _article(
    slug: str,
    *,
    published_date: str | None = "2026-09-19",
    platform_mixed: bool = True,
    is_gated: bool = False,
) -> RotoGrindersArchivedArticle:
    return RotoGrindersArchivedArticle(
        slug=slug,
        url=f"https://rotogrinders.com/articles/{slug}",
        title=f"Title for {slug}",
        author="Epignosis",
        published_date=published_date,
        tags=[],
        text="Real article body text.",
        fetched_at="2026-09-20T12:00:00Z",
        is_gated=is_gated,
        platform_mixed=platform_mixed,
    )


def test_has_article_false_when_nothing_written(tmp_path):
    assert has_article("some-slug", base_dir=tmp_path) is False


def test_write_then_has_article_true(tmp_path):
    write_article(_article("some-slug"), base_dir=tmp_path)
    assert has_article("some-slug", base_dir=tmp_path) is True


def test_write_article_round_trips(tmp_path):
    article = _article("some-slug", platform_mixed=True, is_gated=True)
    write_article(article, base_dir=tmp_path)
    result = read_article("some-slug", base_dir=tmp_path)
    assert result == article


def test_write_article_same_slug_overwrites_not_appends(tmp_path):
    write_article(_article("some-slug", is_gated=True), base_dir=tmp_path)
    write_article(_article("some-slug", is_gated=False), base_dir=tmp_path)
    result = read_article("some-slug", base_dir=tmp_path)
    assert result.is_gated is False


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    write_article(_article("some-slug"), base_dir=tmp_path)
    assert list(tmp_path.glob("*.tmp")) == []
    assert [p.name for p in tmp_path.glob("*.json")] == ["some-slug.json"]


def test_list_article_slugs_empty_directory(tmp_path):
    assert list_article_slugs(base_dir=tmp_path) == []


def test_list_article_slugs_sorted(tmp_path):
    write_article(_article("zzz-slug"), base_dir=tmp_path)
    write_article(_article("aaa-slug"), base_dir=tmp_path)
    assert list_article_slugs(base_dir=tmp_path) == ["aaa-slug", "zzz-slug"]


def test_read_all_articles_ordered_by_published_date(tmp_path):
    write_article(_article("later", published_date="2026-09-19"), base_dir=tmp_path)
    write_article(_article("earlier", published_date="2026-09-12"), base_dir=tmp_path)
    write_article(_article("no-date", published_date=None), base_dir=tmp_path)
    result = read_all_articles(base_dir=tmp_path)
    assert [a.slug for a in result] == ["no-date", "earlier", "later"]


def test_article_path_uses_default_root_when_base_dir_omitted():
    path = article_path("some-slug")
    assert path.name == "some-slug.json"
    assert "rotogrinders" in path.parts and "articles" in path.parts


def test_platform_mixed_and_is_gated_round_trip_independently(tmp_path):
    write_article(_article("mixed-gated", platform_mixed=True, is_gated=True), base_dir=tmp_path)
    write_article(_article("clean-open", platform_mixed=False, is_gated=False), base_dir=tmp_path)
    mixed_gated = read_article("mixed-gated", base_dir=tmp_path)
    clean_open = read_article("clean-open", base_dir=tmp_path)
    assert (mixed_gated.platform_mixed, mixed_gated.is_gated) == (True, True)
    assert (clean_open.platform_mixed, clean_open.is_gated) == (False, False)
