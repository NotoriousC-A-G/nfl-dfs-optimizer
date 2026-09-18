"""Longitudinal Footballguys editorial-article archive.

Chris's explicit request (2026-09-19): "I want this to tweak our lineup decisions. Projections
are fragile." -- narrative/editorial content (weekly overreactions, matchup notes, upgrades/
downgrades, roster-percentage reads, etc.) is a real input to lineup construction this project has
never captured, distinct from the numeric vendor blend (`projection/blend.py`'s
`VENDOR_PROJECTION_SOURCES`). This module is the storage half: durable, local, reusable across
weeks -- so a future session building lineups can read what was actually published, not just
whatever's still live on the site that day.

**Filesystem is the only source of truth, same convention as `resultsdb_store.py`/`injury_
snapshot_store.py` (ADR-0024/0038)** -- no separate index file that can drift from what's really
on disk. `has_article` answers resumability with a single `Path.exists()` check.

**Layout:** `data/raw/articles/footballguys/<slug>.json` -- one file per article, captured ONCE
(not a per-date snapshot the way injury reports are -- an article's own text doesn't change
meaningfully after publication, so idempotent per-slug capture is the right resolution). Both
writes are atomic (`.tmp` sibling + `os.replace`), same mechanism `resultsdb_store.py` uses.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

# repo_root: storage/footballguys_article_store.py -> nfl_dfs -> src -> repo root (same depth as
# storage/resultsdb_store.py's own _REPO_ROOT).
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = _REPO_ROOT / "data" / "raw" / "articles" / "footballguys"


@dataclass(frozen=True)
class ArchivedArticle:
    """One captured Footballguys article -- real, full text (fetched with the authenticated
    session so a paywalled article's full body is captured, not just the free preview; see
    `ingestion/footballguys_articles.py`'s own docstring for that finding)."""

    slug: str
    url: str
    title: str
    author: str | None
    published_date: str | None  # "YYYY-MM-DD", None if the byline's date couldn't be parsed
    category_ids: list[int]  # every category this slug was discovered under (an article can be
    # cross-tagged, e.g. both "Daily Fantasy (DFS)" and "Strategy") -- a list, not a single value,
    # so a second discovery pass under a different category adds to this rather than overwriting it
    tags: list[str]
    text: str
    fetched_at: str  # ISO-8601 timestamp of the actual live fetch


def article_path(slug: str, *, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    return root / f"{slug}.json"


def has_article(slug: str, *, base_dir: Path | None = None) -> bool:
    """The one resumability check the capture script relies on -- no other state is consulted
    (ADR-0024's own convention, reused here)."""
    return article_path(slug, base_dir=base_dir).exists()


def write_article(article: ArchivedArticle, *, base_dir: Path | None = None) -> Path:
    """Writes (or overwrites) one article's envelope. Idempotent -- re-running the same slug just
    overwrites its own file, no append/merge involved. If the article was already archived under a
    different category (`category_ids`), the caller is responsible for merging the category list
    before calling this (see `scripts/footballguys_article_capture.py`) -- this function itself
    always writes exactly the `category_ids` it's given, it does not merge with what's on disk."""
    path = article_path(article.slug, base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(asdict(article)))
    os.replace(tmp_path, path)  # atomic on POSIX and Windows
    return path


def read_article(slug: str, *, base_dir: Path | None = None) -> ArchivedArticle:
    path = article_path(slug, base_dir=base_dir)
    envelope = json.loads(path.read_text())
    return ArchivedArticle(**envelope)


def list_article_slugs(*, base_dir: Path | None = None) -> list[str]:
    """Every slug with an archived article on disk, ascending -- reads the filesystem directly (no
    index file to drift), same posture as `has_article`."""
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    if not root.exists():
        return []
    return sorted(p.stem for p in root.glob("*.json"))


def read_all_articles(*, base_dir: Path | None = None) -> list[ArchivedArticle]:
    """Every archived article, ordered by `published_date` (articles with no parsed date sort
    first) -- the real input a caller reviewing "what's new this week" would want, not just a
    slug list."""
    articles = [read_article(slug, base_dir=base_dir) for slug in list_article_slugs(base_dir=base_dir)]
    return sorted(articles, key=lambda a: a.published_date or "")
