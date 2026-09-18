from nfl_dfs.storage.footballguys_article_store import (
    ArchivedArticle,
    article_path,
    has_article,
    list_article_slugs,
    read_all_articles,
    read_article,
    write_article,
)


def _article(slug: str, *, published_date: str | None = "2026-09-14", category_ids: list[int] | None = None) -> ArchivedArticle:
    return ArchivedArticle(
        slug=slug,
        url=f"https://www.footballguys.com/article/{slug}",
        title=f"Title for {slug}",
        author="Devin Knotts",
        published_date=published_date,
        category_ids=category_ids if category_ids is not None else [7],
        tags=["overreactions", "week 2"],
        text="Real article body text.",
        fetched_at="2026-09-18T12:00:00Z",
    )


def test_has_article_false_when_nothing_written(tmp_path):
    assert has_article("some-slug", base_dir=tmp_path) is False


def test_write_then_has_article_true(tmp_path):
    write_article(_article("some-slug"), base_dir=tmp_path)
    assert has_article("some-slug", base_dir=tmp_path) is True


def test_write_article_round_trips(tmp_path):
    article = _article("some-slug")
    write_article(article, base_dir=tmp_path)
    result = read_article("some-slug", base_dir=tmp_path)
    assert result == article


def test_write_article_same_slug_overwrites_not_appends(tmp_path):
    write_article(_article("some-slug", category_ids=[7]), base_dir=tmp_path)
    write_article(_article("some-slug", category_ids=[7, 16]), base_dir=tmp_path)
    result = read_article("some-slug", base_dir=tmp_path)
    assert result.category_ids == [7, 16]


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
    write_article(_article("later", published_date="2026-09-18"), base_dir=tmp_path)
    write_article(_article("earlier", published_date="2026-09-12"), base_dir=tmp_path)
    write_article(_article("no-date", published_date=None), base_dir=tmp_path)
    result = read_all_articles(base_dir=tmp_path)
    assert [a.slug for a in result] == ["no-date", "earlier", "later"]


def test_article_path_uses_default_root_when_base_dir_omitted():
    path = article_path("some-slug")
    assert path.name == "some-slug.json"
    assert "footballguys" in path.parts and "articles" in path.parts
