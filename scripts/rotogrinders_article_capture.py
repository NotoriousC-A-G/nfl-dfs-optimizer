"""Captures new RotoGrinders NFL DFS articles into the longitudinal archive -- the second article
source alongside `scripts/footballguys_article_capture.py`, added 2026-09-20 at Chris's request.

Discovers NFL-relevant articles across RotoGrinders' real, mixed-sport `/articles?page=N` listing
(`ingestion/rotogrinders_articles.py`'s own title-based `_NFL_TITLE_RE` filter -- no sport-specific
listing endpoint exists on this site), skips slugs already archived
(`storage/rotogrinders_article_store.has_article`), and fetches+stores every new one. Always
anonymous (no RotoGrinders session is configured anywhere in this project) -- a paywalled article
is archived with `is_gated=True` and only its real free-preview text, never a fabricated full body.

**`platform_mixed` computed here, at capture time** (Chris's explicit request: "flag every
captured article as platform-mixed by default... so the synthesis step can weigh it appropriately
rather than treating it as clean DK evidence"). `True` unless the title unambiguously identifies
DK-only content (contains "DraftKings" and NOT "FanDuel") -- every real RotoGrinders NFL article
seen so far covers both platforms together with no per-pick split (confirmed live: "NFL DFS Picks:
DraftKings & FanDuel Expert Survey"), so this defaults to distrust rather than defaulting to
trust, the opposite posture from Footballguys' `_is_fanduel_specific` (which defaults to INCLUDING
an article unless it's confirmed FanDuel-only).

Meant to be run on a recurring cadence, same as the Footballguys capture script. NOT part of
`pytest` -- a live network pull. Run by hand (or on a schedule):

    PYTHONPATH=. .venv/bin/python scripts/rotogrinders_article_capture.py
"""

from __future__ import annotations

import datetime as dt
import re

from nfl_dfs.ingestion.rotogrinders_articles import DEFAULT_MAX_PAGES, fetch_article_body, fetch_article_listings
from nfl_dfs.storage.rotogrinders_article_store import RotoGrindersArchivedArticle, has_article, write_article

_DK_ONLY_RE = re.compile(r"(?i)\bdraftkings\b")
_FD_RE = re.compile(r"(?i)\bfanduel\b")


def _platform_mixed(title: str) -> bool:
    """See module docstring's `platform_mixed` section -- defaults to `True` (mixed/uncertain),
    `False` only when the title names DraftKings and does not also name FanDuel.
    """
    return not (_DK_ONLY_RE.search(title) and not _FD_RE.search(title))


def main() -> None:
    print(f"=== RotoGrinders NFL article capture -- crawling {DEFAULT_MAX_PAGES} listing page(s) ===")

    by_slug = fetch_article_listings()
    print(f"  {len(by_slug)} NFL-relevant article(s) found")

    new_slugs = [slug for slug in by_slug if not has_article(slug)]
    print(f"  {len(by_slug) - len(new_slugs)} already archived, {len(new_slugs)} new")

    for slug in new_slugs:
        listing = by_slug[slug]
        try:
            body = fetch_article_body(slug)
        except Exception as exc:  # noqa: BLE001
            print(f"  {slug}: FAILED to fetch body ({exc})")
            continue
        title = body.title or listing.title
        article = RotoGrindersArchivedArticle(
            slug=slug,
            url=listing.url,
            title=title,
            author=body.author or listing.author,
            published_date=body.published_date,  # listing's own date has no year -- see ingestion
            # module's docstring point 4; body page's real, year-qualified date is the only one used.
            tags=[],
            text=body.text,
            fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            is_gated=body.is_gated,
            platform_mixed=_platform_mixed(title),
        )
        write_article(article)
        gated_note = " [GATED, preview only]" if body.is_gated else ""
        mixed_note = " [platform-mixed]" if article.platform_mixed else " [DK-only]"
        print(f"  {slug}: captured ({len(body.text)} chars){gated_note}{mixed_note}")

    print(f"\nDone. {len(new_slugs)} new article(s) archived to data/raw/articles/rotogrinders/")


if __name__ == "__main__":
    main()
