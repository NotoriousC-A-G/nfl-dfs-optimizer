"""Longitudinal RotoGrinders editorial-article archive -- the second article-evidence source
alongside `storage/footballguys_article_store.py`, same filesystem-is-the-only-source-of-truth
convention (ADR-0024/0038), same one-file-per-slug idempotent-capture shape.

**One real, deliberate schema difference from `ArchivedArticle`, at Chris's explicit request
(2026-09-20):** `platform_mixed`. RotoGrinders' real DFS-picks content routinely covers
DraftKings AND FanDuel in the same piece with no way to tell which specific pick applies to which
platform (confirmed live on a real article: "NFL DFS Expert Survey" mixes both with no
per-platform split) -- a real risk for a DraftKings-only project (Chris: "I primarily play on
DraftKings", the same concern that drove `analysis/circumstance/engine.py`'s existing
`_is_fanduel_specific` exclusion for Footballguys). Rather than silently trusting or blanket-
excluding every RotoGrinders article, each one is tagged `platform_mixed` at capture time
(`ingestion/rotogrinders_articles.py` doesn't compute this -- see
`scripts/rotogrinders_article_capture.py`'s own title-based heuristic) so the circumstance-
synthesis prompt can disclose it and weigh the evidence accordingly, never treating it as clean
DK-only evidence the way a Footballguys article is.

Also carries `is_gated` (this project has no RotoGrinders Premium session, so a paywalled
article's archived `text` is its real free-preview only, never a fabricated "full" body -- see
`ingestion/rotogrinders_articles.py`'s own docstring).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = _REPO_ROOT / "data" / "raw" / "articles" / "rotogrinders"


@dataclass(frozen=True)
class RotoGrindersArchivedArticle:
    """One captured RotoGrinders article. `tags` is always `[]` (RotoGrinders has no tag list at
    all, confirmed live -- kept as a field only so this type carries the same shape
    `analysis/circumstance/engine.py`'s `ArticleEvidence` Protocol expects from every article
    source, not because real tag data exists here to store).
    """

    slug: str
    url: str
    title: str
    author: str | None
    published_date: str | None  # "YYYY-MM-DD", from the article body page (real, year-qualified)
    tags: list[str]  # always [] for this source -- see class docstring
    text: str
    fetched_at: str
    is_gated: bool  # True when a real paywall roadblock was found -- text is a partial preview
    platform_mixed: bool  # True unless the title is unambiguously DK-only -- see module docstring


def article_path(slug: str, *, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    return root / f"{slug}.json"


def has_article(slug: str, *, base_dir: Path | None = None) -> bool:
    return article_path(slug, base_dir=base_dir).exists()


def write_article(article: RotoGrindersArchivedArticle, *, base_dir: Path | None = None) -> Path:
    path = article_path(article.slug, base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(asdict(article)))
    os.replace(tmp_path, path)  # atomic on POSIX and Windows
    return path


def read_article(slug: str, *, base_dir: Path | None = None) -> RotoGrindersArchivedArticle:
    path = article_path(slug, base_dir=base_dir)
    envelope = json.loads(path.read_text())
    return RotoGrindersArchivedArticle(**envelope)


def list_article_slugs(*, base_dir: Path | None = None) -> list[str]:
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    if not root.exists():
        return []
    return sorted(p.stem for p in root.glob("*.json"))


def read_all_articles(*, base_dir: Path | None = None) -> list[RotoGrindersArchivedArticle]:
    """Every archived article, ordered by `published_date` (undated articles sort first) -- same
    convention as `footballguys_article_store.read_all_articles`.
    """
    articles = [read_article(slug, base_dir=base_dir) for slug in list_article_slugs(base_dir=base_dir)]
    return sorted(articles, key=lambda a: a.published_date or "")
