"""Footballguys EDITORIAL ARTICLE ingestion -- a genuinely different content type from
`ingestion/footballguys.py`'s numeric weekly point projections. Chris's explicit request
(2026-09-19): "I want this to tweak our lineup decisions. Projections are fragile." Narrative
content (overreactions, matchup notes, upgrades/downgrades, roster-percentage reads, general
strategy columns like "Gut Check") is real signal this pipeline has never captured -- this module
discovers and fetches it; `storage/footballguys_article_store.py` is the durable side.

**Two real pages, checked live, not assumed:**
1. `/articles?category=<id>` -- a real, PUBLIC (no auth needed, confirmed live) reverse-
   chronological listing per category. 12 real categories confirmed (`ARTICLE_CATEGORIES` below,
   scraped live from the index page's own category filter links) -- this module defaults to
   `DEFAULT_CATEGORY_IDS`, the subset actually relevant to WEEKLY DK CLASSIC lineup construction
   (Daily Fantasy/DFS, Injuries, Strategy, Player Spotlights), not every category Footballguys
   publishes (Dynasty/Rookies/Best Ball/IDP/Salary-Cap-Drafts/Sports-Wagering/Beginner-Series are
   format- or timescale-mismatched for this project's weekly redraft DK scope) -- a caller can
   still pass any subset of `ARTICLE_CATEGORIES`' values explicitly.
2. `/article/<slug>` -- the article body. **A real, confirmed finding: some articles are
   PARTIALLY PAYWALLED** (a "PRO subscription" roadblock gates part of the body -- confirmed live
   on a real article: 3,584 chars of body text with no session cookie vs. 6,516 chars with
   `config.footballguys_session_cookie`, the SAME cookie this project's `footballguys.py`
   projections module already uses). This module always fetches article bodies WITH that cookie,
   never anonymously, so an archived article is never silently missing its paywalled half.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from nfl_dfs.config import config

ARTICLES_URL = "https://www.footballguys.com/articles"
ARTICLE_URL_TEMPLATE = "https://www.footballguys.com/article/{slug}"

# Every real category id, scraped live (2026-09-18) from the articles index page's own category
# filter links (`a[href*="articles?category="]`) -- kept here for the record/future reference,
# not because every caller needs the full set.
ARTICLE_CATEGORIES: dict[str, int] = {
    "Beginner Series": 5,
    "Best Ball": 6,
    "Daily Fantasy (DFS)": 7,
    "Dynasty & Keepers": 8,
    "High Stakes": 9,
    "IDP": 10,
    "Injuries": 11,
    "Sports Wagering": 12,
    "Rookies": 13,
    "Salary Cap Drafts": 14,
    "Player Spotlights": 15,
    "Strategy": 16,
}

# The categories actually relevant to weekly DK Classic lineup construction (Chris's "broad, not
# DFS-only" direction, 2026-09-19) -- covers the shape of every example article he linked (DFS
# matchup/GPP pieces, a general "Gut Check" strategy column, "Upgrades and Downgrades",
# roster-percentage reads), while excluding categories mismatched to this project's actual scope
# (Dynasty/Rookies: long-term value, not this week's lineup; Best Ball/High Stakes/Salary Cap
# Drafts: different contest formats; IDP: this project is DK Classic offense+DST, not IDP leagues;
# Sports Wagering/Beginner Series: tangential or not weekly-analysis content).
DEFAULT_CATEGORY_IDS: tuple[int, ...] = (
    ARTICLE_CATEGORIES["Daily Fantasy (DFS)"],
    ARTICLE_CATEGORIES["Injuries"],
    ARTICLE_CATEGORIES["Strategy"],
    ARTICLE_CATEGORIES["Player Spotlights"],
)

_TIMEOUT = 20.0
_DATE_RE = re.compile(r"([A-Za-z]{3,9}) (\d{1,2}), (\d{4})")
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_listing_date(text: str) -> str | None:
    """"Footballguys Staff, Sep 18, 2026" -> "2026-09-18". `None` if the trailing date can't be
    parsed -- never a fabricated date."""
    match = _DATE_RE.search(text)
    if match is None:
        return None
    month_name, day, year = match.groups()
    month = _MONTHS.get(month_name[:3])
    if month is None:
        return None
    return f"{year}-{month:02d}-{int(day):02d}"


@dataclass(frozen=True)
class ArticleListing:
    """One row from an `/articles?category=N` index page -- just enough to decide whether to
    fetch the full body (`has_article` in the storage module)."""

    slug: str
    title: str
    author: str | None
    published_date: str | None
    category_id: int
    url: str


def parse_article_listing(html: str, category_id: int) -> list[ArticleListing]:
    """Pure parse of one category's `/articles?category=N` listing page into `ArticleListing`
    rows. `a.article-link` (confirmed live) wraps a thumbnail div, an `h5` title, and a
    `span.content-date` carrying "Author, Mon DD, YYYY" -- the real structure, not guessed."""
    soup = BeautifulSoup(html, "html.parser")
    listings: list[ArticleListing] = []
    for a in soup.select("a.article-link[href]"):
        href = a["href"]
        slug = href.rstrip("/").rsplit("/article/", 1)[-1] if "/article/" in href else None
        if not slug:
            continue
        title_el = a.select_one("h5")
        date_el = a.select_one("span.content-date")
        title = title_el.get_text(strip=True) if title_el is not None else slug
        date_text = date_el.get_text(strip=True) if date_el is not None else ""
        # "Devin Knotts, Sep 14, 2026" -- split on the DATE MATCH's own start position, not a
        # naive rsplit on "," (the date itself contains a comma, "Sep 14, 2026", so a comma-based
        # split grabs too much -- a real bug caught by this module's own tests).
        date_match = _DATE_RE.search(date_text)
        author = date_text[: date_match.start()].rstrip(", ").strip() or None if date_match is not None else None
        listings.append(
            ArticleListing(
                slug=slug,
                title=title,
                author=author,
                published_date=_parse_listing_date(date_text),
                category_id=category_id,
                url=ARTICLE_URL_TEMPLATE.format(slug=slug),
            )
        )
    return listings


def fetch_article_listings(
    category_ids: tuple[int, ...] = DEFAULT_CATEGORY_IDS, *, session: requests.Session | None = None
) -> dict[str, ArticleListing]:
    """Live pull across every requested category, deduped by slug (first-seen category wins the
    listing metadata; a caller that needs every category a slug appeared under should inspect
    `category_ids` per-category separately -- this function's return value is one row per real
    article, matching `storage.footballguys_article_store`'s own one-file-per-slug shape).
    Confirmed live: this listing page needs NO authentication at all.
    """
    http = session or requests
    by_slug: dict[str, ArticleListing] = {}
    for category_id in category_ids:
        response = http.get(
            ARTICLES_URL, headers={"User-Agent": "Mozilla/5.0"}, params={"category": category_id}, timeout=_TIMEOUT
        )
        response.raise_for_status()
        for listing in parse_article_listing(response.text, category_id):
            by_slug.setdefault(listing.slug, listing)
    return by_slug


@dataclass(frozen=True)
class ArticleBody:
    """The full parsed content of one `/article/<slug>` page."""

    title: str
    author: str | None
    published_date: str | None
    text: str
    tags: list[str]


def parse_article_body(html: str) -> ArticleBody:
    """Pure parse of one article page. `div.article-content` (confirmed live) is the real body
    container; `h1.display-4` the title; the byline block (`div.d-flex.align-items-center.mb-3`,
    confirmed live) carries the author link and a "Published MM/DD/YYYY" span; tags live under the
    `h6` literally reading "Tags:" as a list of `a.badge` elements in its next sibling div.
    """
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1")
    title = h1.get_text(strip=True) if h1 is not None else ""

    author = None
    published_date = None
    published_span = soup.find(string=re.compile(r"Published \d{2}/\d{2}/\d{4}"))
    if published_span is not None:
        byline = published_span.parent.parent
        author_link = byline.select_one("a")
        if author_link is not None:
            author = author_link.get_text(strip=True)
        match = re.search(r"Published (\d{2})/(\d{2})/(\d{4})", published_span)
        if match is not None:
            mm, dd, yyyy = match.groups()
            published_date = f"{yyyy}-{mm}-{dd}"

    content_el = soup.select_one("div.article-content")
    text = content_el.get_text("\n", strip=True) if content_el is not None else ""

    tags: list[str] = []
    tags_label = soup.find("h6", string=re.compile(r"^\s*Tags:?\s*$"))
    if tags_label is not None:
        tags_container = tags_label.find_next_sibling("div")
        if tags_container is not None:
            tags = [a.get_text(strip=True) for a in tags_container.select("a.badge")]

    return ArticleBody(title=title, author=author, published_date=published_date, text=text, tags=tags)


def fetch_article_body(
    slug: str, *, session_cookie: str | None = None, session: requests.Session | None = None
) -> ArticleBody:
    """Live pull of one article's full body -- ALWAYS with the authenticated session cookie
    (confirmed live: some articles gate part of their content behind a PRO-subscription
    roadblock; fetching anonymously would silently archive only the free preview half). Same
    cookie-auth pattern `ingestion/footballguys.py`'s projections pull already uses.
    """
    cookie = session_cookie or config.footballguys_session_cookie
    if not cookie:
        raise RuntimeError("FOOTBALLGUYS_SESSION_COOKIE is not configured")
    http = session or requests
    response = http.get(
        ARTICLE_URL_TEMPLATE.format(slug=slug), headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0"}, timeout=_TIMEOUT
    )
    response.raise_for_status()
    return parse_article_body(response.text)
