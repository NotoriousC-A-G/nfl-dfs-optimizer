from nfl_dfs.ingestion.footballguys_articles import (
    ARTICLE_CATEGORIES,
    DEFAULT_CATEGORY_IDS,
    parse_article_body,
    parse_article_listing,
)

_LISTING_HTML = """
<html><body>
<a class="article-link text-body text-decoration-none" href="https://www.footballguys.com/article/2026-landing-page-fantasy-football-dfs-week-02">
  <div class="thumb-bg" style="background-image: url(https://example.com/img.jpg);"></div>
  <div class="content-text">
    <h5>Everything Season Long and DFS for Week 2</h5>
    <span class="content-date">Footballguys Staff, Sep 18, 2026</span>
  </div>
</a>
<a class="article-link text-body text-decoration-none" href="https://www.footballguys.com/article/2026-what-we-learned-overreactions-week02">
  <div class="thumb-bg"></div>
  <div class="content-text">
    <h5>Top 5 Things We Learned and Overreactions From Week 1</h5>
    <span class="content-date">Devin Knotts, Sep 14, 2026</span>
  </div>
</a>
<a class="secondary-links" href="https://www.footballguys.com/article/offensive-line-rankings">Weekly Offensive Line Rankings</a>
</body></html>
"""

_ARTICLE_BODY_HTML = """
<html><body>
<h1 class="display-4">Top 5 Things We Learned and Overreactions From Week 1</h1>
<div class="d-flex align-items-center mb-3">
  <a href="https://www.footballguys.com/articles?userId=518560">Devin Knotts</a>
  <span class="ms-2 text-secondary">Published 09/14/2026</span>
</div>
<div class="article-content">
  <p>Top 5 Things We Learned</p>
  <h2>The Bears are going to be extremely fun.</h2>
  <p>Real body paragraph text about the Bears.</p>
  <h2>Top 5 Overreactions</h2>
  <p>Real body paragraph text about overreactions, unlocked by the session cookie.</p>
</div>
<div class="mb-4">
  <h6>Tags:</h6>
  <div class="d-flex flex-wrap gap-2">
    <a class="badge bg-secondary text-decoration-none text-white" href="?tag=overreactions">overreactions</a>
    <a class="badge bg-secondary text-decoration-none text-white" href="?tag=week%202">week 2</a>
    <a class="badge bg-secondary text-decoration-none text-white" href="?tag=DFS">DFS</a>
  </div>
</div>
</body></html>
"""


def test_parse_article_listing_extracts_real_rows_only():
    listings = parse_article_listing(_LISTING_HTML, category_id=7)
    assert len(listings) == 2
    slugs = {listing.slug for listing in listings}
    assert slugs == {
        "2026-landing-page-fantasy-football-dfs-week-02",
        "2026-what-we-learned-overreactions-week02",
    }
    # The "secondary-links" nav anchor to /article/offensive-line-rankings is NOT an
    # `a.article-link` -- correctly excluded, not scraped as a real listed article.
    assert "offensive-line-rankings" not in slugs


def test_parse_article_listing_parses_title_author_date_and_category():
    listings = parse_article_listing(_LISTING_HTML, category_id=7)
    by_slug = {listing.slug: listing for listing in listings}
    overreactions = by_slug["2026-what-we-learned-overreactions-week02"]
    assert overreactions.title == "Top 5 Things We Learned and Overreactions From Week 1"
    assert overreactions.author == "Devin Knotts"
    assert overreactions.published_date == "2026-09-14"
    assert overreactions.category_id == 7
    assert overreactions.url == "https://www.footballguys.com/article/2026-what-we-learned-overreactions-week02"


def test_parse_article_listing_handles_staff_byline():
    listings = parse_article_listing(_LISTING_HTML, category_id=7)
    by_slug = {listing.slug: listing for listing in listings}
    staff_article = by_slug["2026-landing-page-fantasy-football-dfs-week-02"]
    assert staff_article.author == "Footballguys Staff"
    assert staff_article.published_date == "2026-09-18"


def test_parse_article_listing_empty_html_returns_no_rows():
    assert parse_article_listing("<html><body>nothing here</body></html>", category_id=7) == []


def test_parse_article_body_extracts_title_author_date():
    body = parse_article_body(_ARTICLE_BODY_HTML)
    assert body.title == "Top 5 Things We Learned and Overreactions From Week 1"
    assert body.author == "Devin Knotts"
    assert body.published_date == "2026-09-14"


def test_parse_article_body_extracts_full_text_including_gated_section():
    # Confirms the parser captures whatever's actually IN div.article-content -- including a
    # section that would be paywalled without the session cookie -- since fetch_article_body's
    # own job (not this pure parser's) is to always fetch WITH that cookie so the full HTML
    # includes it in the first place.
    body = parse_article_body(_ARTICLE_BODY_HTML)
    assert "Real body paragraph text about the Bears." in body.text
    assert "Real body paragraph text about overreactions, unlocked by the session cookie." in body.text


def test_parse_article_body_extracts_tags():
    body = parse_article_body(_ARTICLE_BODY_HTML)
    assert body.tags == ["overreactions", "week 2", "DFS"]


def test_parse_article_body_missing_elements_degrade_gracefully_not_crash():
    body = parse_article_body("<html><body><p>no structure at all</p></body></html>")
    assert body.title == ""
    assert body.author is None
    assert body.published_date is None
    assert body.text == ""
    assert body.tags == []


def test_default_category_ids_are_a_real_subset_of_all_categories():
    assert set(DEFAULT_CATEGORY_IDS).issubset(set(ARTICLE_CATEGORIES.values()))
    assert ARTICLE_CATEGORIES["Daily Fantasy (DFS)"] in DEFAULT_CATEGORY_IDS
    assert ARTICLE_CATEGORIES["Injuries"] in DEFAULT_CATEGORY_IDS
    assert ARTICLE_CATEGORIES["Strategy"] in DEFAULT_CATEGORY_IDS
    assert ARTICLE_CATEGORIES["Player Spotlights"] in DEFAULT_CATEGORY_IDS
    # Format-mismatched categories deliberately excluded from the default scope.
    assert ARTICLE_CATEGORIES["Dynasty & Keepers"] not in DEFAULT_CATEGORY_IDS
    assert ARTICLE_CATEGORIES["IDP"] not in DEFAULT_CATEGORY_IDS
