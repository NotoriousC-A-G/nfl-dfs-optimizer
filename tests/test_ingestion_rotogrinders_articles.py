from nfl_dfs.ingestion.rotogrinders_articles import parse_article_body, parse_article_listing_page

_LISTING_HTML = """
<html><body>
<div class="module"><div class="post-card ">
  <div class="post-card-thumbnail"><a href="/articles/nascar-by-the-numbers-4216578"><img alt="x"/></a></div>
  <div class="post-card-body">
    <a class="post-card-title" href="/articles/nascar-by-the-numbers-4216578"><span>NASCAR By The Numbers</span></a>
    <div class="post-card-byline"><a class="post-card-author" href="/profiles/stevietpfl"><span class="post-card-name">stevietpfl</span></a></div>
    <div class="post-card-underbyline"><span class="post-card-date muted"> Sep 19th </span>
      <a class="post-card-access highlight" href="/articles/nascar-by-the-numbers-4216578">Premium Only!</a>
    </div>
  </div>
</div></div>
<div class="module"><div class="post-card ">
  <div class="post-card-thumbnail"><a href="/articles/nfl-dfs-picks-expert-survey-week-2-4216572"><img alt="x"/></a></div>
  <div class="post-card-body">
    <a class="post-card-title" href="/articles/nfl-dfs-picks-expert-survey-week-2-4216572"><span>NFL DFS Picks: DraftKings &amp; FanDuel Expert Survey for Week 2</span></a>
    <div class="post-card-byline"><a class="post-card-author" href="/profiles/Epignosis"><span class="post-card-name">Epignosis</span></a></div>
    <div class="post-card-underbyline"><span class="post-card-date muted"> Sep 19th </span>
      <a class="post-card-access highlight" href="/articles/nfl-dfs-picks-expert-survey-week-2-4216572">Premium Only!</a>
    </div>
  </div>
</div></div>
<div class="module"><div class="post-card ">
  <div class="post-card-thumbnail"><a href="/articles/nfl-free-preview-week-2-4216999"><img alt="x"/></a></div>
  <div class="post-card-body">
    <a class="post-card-title" href="/articles/nfl-free-preview-week-2-4216999"><span>NFL DFS Free Preview for Week 2</span></a>
    <div class="post-card-byline"><a class="post-card-author" href="/profiles/Notorious"><span class="post-card-name">Notorious</span></a></div>
    <div class="post-card-underbyline"><span class="post-card-date muted"> Sep 19th </span></div>
  </div>
</div></div>
</body></html>
"""

_ARTICLE_BODY_HTML_GATED = """
<html><body>
<article class="post" data-role="post">
  <h1>NFL DFS Expert Survey: Week 2</h1>
  <div class="post-author">
    <div class="post-author-meta">
      <span> by <a href="/profiles/Epignosis">Robert Brown (Epignosis)</a> </span>
      <span><div>Created <span>Sep 19, 2026</span></div></span>
    </div>
  </div>
  <p>Real free-preview body text about the Vikings stack.</p>
  <h3>Who's your favorite contrarian play?</h3>
  <div class="cmp roadblock">
    <div class="blk roadblock-content">
      <h3>This content can help you make better NFL DFS picks</h3>
      <a class="btn" href="/premium/nfl">Buy NFL Premium!</a>
    </div>
  </div>
</article>
</body></html>
"""

_ARTICLE_BODY_HTML_FREE = """
<html><body>
<article class="post" data-role="post">
  <h1>NFL DFS Free Preview for Week 2</h1>
  <div class="post-author">
    <div class="post-author-meta">
      <span> by <a href="/profiles/Notorious">Notorious</a> </span>
      <span><div>Created <span>Sep 19, 2026</span></div></span>
    </div>
  </div>
  <p>Real full free body text, no roadblock here.</p>
</article>
</body></html>
"""


def test_parse_article_listing_page_extracts_every_real_card():
    listings = parse_article_listing_page(_LISTING_HTML)
    assert {l.slug for l in listings} == {
        "nascar-by-the-numbers-4216578",
        "nfl-dfs-picks-expert-survey-week-2-4216572",
        "nfl-free-preview-week-2-4216999",
    }


def test_parse_article_listing_page_reads_title_author_and_premium_flag():
    listings = parse_article_listing_page(_LISTING_HTML)
    by_slug = {l.slug: l for l in listings}

    survey = by_slug["nfl-dfs-picks-expert-survey-week-2-4216572"]
    assert survey.title == "NFL DFS Picks: DraftKings & FanDuel Expert Survey for Week 2"
    assert survey.author == "Epignosis"
    assert survey.is_premium is True
    assert survey.published_date is None  # listing date has no year -- never guessed

    free = by_slug["nfl-free-preview-week-2-4216999"]
    assert free.is_premium is False


def test_parse_article_listing_page_url_is_a_real_absolute_rotogrinders_url():
    listings = parse_article_listing_page(_LISTING_HTML)
    survey = next(l for l in listings if l.slug == "nfl-dfs-picks-expert-survey-week-2-4216572")
    assert survey.url == "https://rotogrinders.com/articles/nfl-dfs-picks-expert-survey-week-2-4216572"


def test_parse_article_body_detects_the_real_paywall_roadblock():
    body = parse_article_body(_ARTICLE_BODY_HTML_GATED)
    assert body.title == "NFL DFS Expert Survey: Week 2"
    assert body.author == "Robert Brown (Epignosis)"
    assert body.published_date == "2026-09-19"
    assert body.is_gated is True
    assert "Real free-preview body text about the Vikings stack." in body.text
    # The roadblock's own upsell copy must never be archived as if it were real article content.
    assert "Buy NFL Premium" not in body.text
    assert "This content can help you make better" not in body.text


def test_parse_article_body_is_not_gated_when_no_roadblock_exists():
    body = parse_article_body(_ARTICLE_BODY_HTML_FREE)
    assert body.is_gated is False
    assert "Real full free body text, no roadblock here." in body.text


def test_parse_article_body_handles_a_page_with_no_article_element():
    body = parse_article_body("<html><body><p>not an article page</p></body></html>")
    assert body.title == ""
    assert body.text == ""
    assert body.is_gated is False
