"""RotoGrinders editorial ARTICLE ingestion -- a second article-evidence source alongside
`ingestion/footballguys_articles.py`, added at Chris's request (he flagged "I'm going to share
some articles from rotogrinders too" earlier, then shared two real URLs 2026-09-20). A genuinely
different site shape from Footballguys, confirmed live, not assumed -- see the real differences
called out below, all of which shape `storage/rotogrinders_article_store.py`'s
`RotoGrindersArchivedArticle` schema too.

**Real, confirmed-live differences from Footballguys' article site:**
1. **No sport-specific listing/category filter.** `/articles?page=N` (confirmed live: real
   pagination, no `?sport=` param that works on this endpoint) mixes EVERY sport RotoGrinders
   covers -- NFL, MLB, PGA, NASCAR, CFB, MMA, etc. -- in one reverse-chronological feed. This
   module filters to NFL-relevant entries by a real, confirmed-reliable signal instead: RotoGrinders
   titles consistently capitalize the sport as its own word ("NFL DFS Picks: ...", "The RotoGrinders
   Sunday Best: DraftKings & FanDuel NFL DFS Picks...") -- `_NFL_TITLE_RE` matches `\bNFL\b`.
2. **No authenticated session available.** This project has no RotoGrinders Premium credentials
   (`config.py` carries `footballguys_session_cookie` for that site only) -- so unlike Footballguys,
   every fetch here is anonymous, and a real, confirmed-live paywall roadblock
   (`div.cmp.roadblock`, replacing the rest of the article with a subscribe CTA) is detected and
   the article is archived as `is_gated=True` with only its real free-preview text -- never a
   fabricated "full" body.
3. **No tag list at all** (confirmed live: no `Tags:`-labeled element anywhere on a real article
   page, unlike Footballguys' `h6`/`a.badge` tags). `ArticleBody.tags` is always `[]` here --
   `analysis/circumstance/engine.py`'s `_article_week` already has a title-mention fallback for
   exactly this case (its primary path, a clean `"week N"` tag, simply never fires for this
   source), so week-awareness still works without fabricating tags that don't exist on the page.
4. **The listing's own date has no year** ("Sep 19th", confirmed live) -- guessing a year near a
   real December/January boundary risks a wrong one, so `ArticleListing.published_date` is always
   `None`; the real, year-qualified date comes from the article BODY page instead ("Created Sep 19,
   2026", confirmed live) via `ArticleBody.published_date`, the same "body page's date wins over
   the listing's" precedence `scripts/rotogrinders_article_capture.py` already inherits from the
   Footballguys capture script's own `body.published_date or listing.published_date` pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

ARTICLES_URL = "https://rotogrinders.com/articles"
ARTICLE_URL_TEMPLATE = "https://rotogrinders.com{path}"

# Real, confirmed-live default crawl depth -- generous enough to reach real NFL content through
# RotoGrinders' mixed-sport feed within this project's own DEFAULT_RECENCY_WINDOW_DAYS (21 days,
# analysis/circumstance/engine.py) without crawling indefinitely into stale pages every run.
DEFAULT_MAX_PAGES = 5

_TIMEOUT = 20.0
_NFL_TITLE_RE = re.compile(r"\bNFL\b")
_CREATED_DATE_RE = re.compile(r"([A-Za-z]{3,9}) (\d{1,2}), (\d{4})")
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_created_date(text: str) -> str | None:
    """"Sep 19, 2026" -> "2026-09-19". `None` if unparseable -- never a fabricated date."""
    match = _CREATED_DATE_RE.search(text)
    if match is None:
        return None
    month_name, day, year = match.groups()
    month = _MONTHS.get(month_name[:3])
    if month is None:
        return None
    return f"{year}-{month:02d}-{int(day):02d}"


@dataclass(frozen=True)
class ArticleListing:
    """One row from an `/articles?page=N` listing -- enough to decide whether to fetch the full
    body (`has_article` in the storage module) and whether it's worth an anonymous fetch at all
    (`is_premium`, a pre-fetch signal from the listing's own "Premium Only!" badge -- the real,
    ground-truth gating check still happens at body-fetch time via the roadblock div, this is just
    an early, cheap heads-up).
    """

    slug: str
    title: str
    author: str | None
    published_date: None  # always None at listing grain -- see module docstring point 4
    is_premium: bool
    url: str


def parse_article_listing_page(html: str) -> list[ArticleListing]:
    """Pure parse of one `/articles?page=N` page into EVERY article listed (every sport) --
    filtering to NFL-relevant entries is `fetch_article_listings`' job, not this function's, so it
    stays independently testable against a real saved page.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings: list[ArticleListing] = []
    for card in soup.select("div.post-card"):
        title_link = card.select_one("a.post-card-title")
        if title_link is None or not title_link.get("href"):
            continue
        href = title_link["href"]
        slug = href.rstrip("/").rsplit("/articles/", 1)[-1] if "/articles/" in href else None
        if not slug:
            continue
        title_span = title_link.select_one("span")
        title = title_span.get_text(strip=True) if title_span is not None else title_link.get_text(strip=True)
        author_el = card.select_one("a.post-card-author span.post-card-name")
        author = author_el.get_text(strip=True) if author_el is not None else None
        is_premium = card.select_one("a.post-card-access") is not None
        listings.append(
            ArticleListing(
                slug=slug,
                title=title,
                author=author,
                published_date=None,
                is_premium=is_premium,
                url=ARTICLE_URL_TEMPLATE.format(path=f"/articles/{slug}"),
            )
        )
    return listings


def fetch_article_listings(
    *, max_pages: int = DEFAULT_MAX_PAGES, session: requests.Session | None = None
) -> dict[str, ArticleListing]:
    """Crawls `/articles?page=1..max_pages` (confirmed live: no sport filter exists on this
    endpoint, so every page mixes every sport) and returns only the NFL-relevant entries
    (`_NFL_TITLE_RE` on the title -- see module docstring point 1), deduped by slug. Confirmed
    live: this listing needs no authentication.
    """
    http = session or requests
    by_slug: dict[str, ArticleListing] = {}
    for page in range(1, max_pages + 1):
        response = http.get(
            ARTICLES_URL, headers={"User-Agent": "Mozilla/5.0"}, params={"page": page}, timeout=_TIMEOUT
        )
        response.raise_for_status()
        for listing in parse_article_listing_page(response.text):
            if _NFL_TITLE_RE.search(listing.title):
                by_slug.setdefault(listing.slug, listing)
    return by_slug


@dataclass(frozen=True)
class ArticleBody:
    """The full parsed content of one `/articles/<slug>` page -- `text` is the real free-preview
    body only (everything inside `article.post` up to, but not including, a `div.cmp.roadblock`
    paywall marker when one exists); `is_gated` is `True` exactly when that roadblock was found, so
    a caller never mistakes a partial preview for the complete article.
    """

    title: str
    author: str | None
    published_date: str | None
    text: str
    is_gated: bool


def parse_article_body(html: str) -> ArticleBody:
    """Pure parse of one article page. `article.post` (confirmed live) is the real content
    container; its own `h1` the title; `div.post-author-meta` carries the byline link and a
    "Created <span>Mon DD, YYYY</span>" block; a `div.cmp.roadblock` (confirmed live), when
    present, marks the paywall boundary -- its own text (a subscribe CTA, not article content) is
    excluded, and everything before it in reading order is kept.
    """
    soup = BeautifulSoup(html, "html.parser")

    article_el = soup.select_one("article.post")
    if article_el is None:
        return ArticleBody(title="", author=None, published_date=None, text="", is_gated=False)

    h1 = article_el.select_one("h1")
    title = h1.get_text(strip=True) if h1 is not None else ""

    author = None
    author_link = article_el.select_one(".post-author-meta a[href^='/profiles/']")
    if author_link is not None:
        author = author_link.get_text(strip=True)

    published_date = None
    created_label = article_el.find(string=re.compile(r"^\s*Created\s*$"))
    if created_label is not None:
        date_span = created_label.parent.find("span")
        if date_span is not None:
            published_date = _parse_created_date(date_span.get_text(strip=True))

    roadblock = article_el.select_one("div.cmp.roadblock")
    is_gated = roadblock is not None
    if roadblock is not None:
        roadblock.extract()  # never included in `text` -- it's a subscribe CTA, not real content

    # Body paragraphs live as direct <p>/<h3>/<h5>/... siblings inside article.post, after the
    # author-meta header block -- excluding that header (already captured above) and the (now
    # removed) roadblock, everything else textual in the article is the real body.
    author_meta = article_el.select_one(".post-author")
    if author_meta is not None:
        author_meta.extract()
    text = article_el.get_text("\n", strip=True)

    return ArticleBody(title=title, author=author, published_date=published_date, text=text, is_gated=is_gated)


def fetch_article_body(slug: str, *, session: requests.Session | None = None) -> ArticleBody:
    """Live, always-anonymous pull of one article's body (see module docstring point 2 for why --
    no RotoGrinders session is configured anywhere in this project)."""
    http = session or requests
    response = http.get(
        ARTICLE_URL_TEMPLATE.format(path=f"/articles/{slug}"), headers={"User-Agent": "Mozilla/5.0"}, timeout=_TIMEOUT
    )
    response.raise_for_status()
    return parse_article_body(response.text)
