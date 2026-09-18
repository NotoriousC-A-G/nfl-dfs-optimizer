"""Captures new Footballguys editorial articles into the longitudinal archive (Chris's "I want
this to tweak our lineup decisions -- projections are fragile" request, 2026-09-19).

Discovers every article listed under `DEFAULT_CATEGORY_IDS` (Daily Fantasy/DFS, Injuries,
Strategy, Player Spotlights -- the categories actually relevant to weekly DK Classic lineup
construction, see `ingestion/footballguys_articles.py`'s own docstring for why this subset, not
every category Footballguys publishes), skips slugs already archived
(`storage/footballguys_article_store.has_article`), and fetches+stores every new one -- always
WITH the authenticated session cookie, since some articles gate part of their content behind a
PRO-subscription roadblock (confirmed live; see the ingestion module's docstring).

**Meant to be run on a recurring cadence** (daily, or whenever picking lineups back up) -- new
articles publish continuously through the week, so a single run only ever captures whatever's new
since the last one.

**"Consuming" this archive is a separate, deliberate non-step here**: this script only builds the
durable record. Reading it to actually inform lineup decisions is Claude's own job each week
(`storage.footballguys_article_store.read_all_articles`, or just `Read` the JSON files under
`data/raw/articles/footballguys/`) -- auto-extracting structured "signals" from free text via NLP
was explicitly not what Chris asked for; see the module's own docstring.

NOT part of `pytest` -- a live network pull. Run by hand (or on a schedule):

    PYTHONPATH=. .venv/bin/python scripts/footballguys_article_capture.py
"""

from __future__ import annotations

import datetime as dt

from nfl_dfs.ingestion.footballguys_articles import (
    DEFAULT_CATEGORY_IDS,
    fetch_article_body,
    fetch_article_listings,
)
from nfl_dfs.storage.footballguys_article_store import (
    ArchivedArticle,
    has_article,
    read_article,
    write_article,
)


def main() -> None:
    print(f"=== Footballguys article capture -- categories={DEFAULT_CATEGORY_IDS} ===")

    by_slug = fetch_article_listings(DEFAULT_CATEGORY_IDS)
    print(f"  {len(by_slug)} distinct article(s) found across every requested category")

    new_slugs = [slug for slug in by_slug if not has_article(slug)]
    already_archived = [slug for slug in by_slug if has_article(slug)]
    print(f"  {len(already_archived)} already archived, {len(new_slugs)} new")

    # An already-archived article discovered again under a DIFFERENT category gets that category
    # merged in, not silently dropped -- an article can be legitimately cross-tagged (e.g. both
    # "Daily Fantasy (DFS)" and "Strategy"), and this script's own category scope can also change
    # over time.
    for slug in already_archived:
        listing = by_slug[slug]
        existing = read_article(slug)
        if listing.category_id not in existing.category_ids:
            merged = ArchivedArticle(
                slug=existing.slug,
                url=existing.url,
                title=existing.title,
                author=existing.author,
                published_date=existing.published_date,
                category_ids=sorted({*existing.category_ids, listing.category_id}),
                tags=existing.tags,
                text=existing.text,
                fetched_at=existing.fetched_at,
            )
            write_article(merged)
            print(f"  {slug}: added category {listing.category_id} to an already-archived article")

    for slug in new_slugs:
        listing = by_slug[slug]
        try:
            body = fetch_article_body(slug)
        except Exception as exc:  # noqa: BLE001
            print(f"  {slug}: FAILED to fetch body ({exc})")
            continue
        article = ArchivedArticle(
            slug=slug,
            url=listing.url,
            title=body.title or listing.title,
            author=body.author or listing.author,
            published_date=body.published_date or listing.published_date,
            category_ids=[listing.category_id],
            tags=body.tags,
            text=body.text,
            fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        write_article(article)
        print(f"  {slug}: captured ({len(body.text)} chars, tags={body.tags})")

    print(f"\nDone. {len(new_slugs)} new article(s) archived to data/raw/articles/footballguys/")


if __name__ == "__main__":
    main()
