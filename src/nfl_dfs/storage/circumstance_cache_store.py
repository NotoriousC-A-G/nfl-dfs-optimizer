"""Content-addressed cache for `CircumstanceAssessment`s (`analysis/circumstance/engine.py`), so
repeated live-script runs within the same week don't burn real Anthropic API tokens resynthesizing
an UNCHANGED real circumstance.

**Real problem this fixes, confirmed live 2026-09-19**: the live dashboard script was re-run ~8
times in one day during development, and every detected circumstance (7 that week) was fully
resynthesized from scratch every single time, even though nothing about the underlying facts had
changed between runs -- ~35-63k tokens burned per run for output identical to the previous run.

**Filesystem is the only source of truth, same convention as `injury_snapshot_store.py`/
`footballguys_article_store.py` (ADR-0024/ADR-0038)** -- no separate index file that can drift.
`has_cached_assessment` answers resumability with a single `Path.exists()` check.

**Layout:** `data/cache/circumstance_pov/<season>/<week>/<kind>/<cache_key>.json` -- `<cache_key>`
is a SHA-256 hash of `{"kind": ..., "team": ..., **source.circumstance_facts()}` (sorted-key JSON),
computed by `cache_key()`. Because the key is a hash of the real facts a circumstance was detected
from (not just a stable identifier like a player id), it **naturally invalidates the moment a fact
genuinely changes** -- an injury status flipping Q->OUT, a matchup multiplier shifting -- with no
TTL/expiry logic needed: a changed fact simply produces a different key, a cache miss, and a fresh
real synthesis.

**Freshness (2026-10-04):** a live caller can pass `extra` (`FilesystemCircumstanceCache.with_extra`)
-- the team's injury statuses on both vendors plus the matched article slugs -- so a status change or a
newly archived article is a cache miss. Found when week-4 depth-chart POVs written 2026-09-30 were reused
on 10-04 with Mike Evans' Questionable status never in their key. Without `extra` the paragraph below
still describes the behavior:

**Non-invalidation without `extra`**: the cache key does NOT include the matched article set (`find_relevant_articles`'s own output) --
`circumstance_facts()` is defined per-detector to cover the facts that would change the ANSWER, and
newly-archived Footballguys coverage isn't one of them by this design. A newly-archived article
relevant to an already-cached circumstance will NOT trigger resynthesis on its own. A caller that
wants a fresh read regardless of the cache (e.g. because they know new coverage landed) should pass
`force_refresh=True` to `analysis.circumstance.engine.synthesize_circumstance` for that run.

Both writes are atomic (`.tmp` sibling + `os.replace`), same mechanism every other `storage/*.py`
module in this project uses.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from nfl_dfs.analysis.circumstance.engine import CircumstanceAssessment, CircumstanceSource

# repo_root: storage/circumstance_cache_store.py -> nfl_dfs -> src -> repo root (same depth as
# every other storage/*.py module's own _REPO_ROOT).
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = _REPO_ROOT / "data" / "cache" / "circumstance_pov"


def cache_key(source: CircumstanceSource, extra: dict | None = None) -> str:
    """A SHA-256 hash of this circumstance's real, defining facts -- see this module's own
    docstring for why the key is derived from the facts themselves (self-invalidating) rather than
    a stable identifier plus a separate expiry mechanism."""
    payload = {
        "kind": source.circumstance_kind(),
        "team": source.circumstance_team(),
        **source.circumstance_facts(),
    }
    if extra:
        # Freshness facts the detector's own facts don't cover (injury statuses on both vendors,
        # the matched article set) -- supplied by the live caller, so a changed status or newly
        # archived article is a cache miss. Absent when None, so existing keys are unchanged.
        payload["extra"] = extra
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def assessment_path(source: CircumstanceSource, *, base_dir: Path | None = None, extra: dict | None = None) -> Path:
    root = base_dir if base_dir is not None else DEFAULT_ROOT
    return (
        root
        / str(source.circumstance_season())
        / str(source.circumstance_week())
        / source.circumstance_kind()
        / f"{cache_key(source, extra)}.json"
    )


def has_cached_assessment(source: CircumstanceSource, *, base_dir: Path | None = None, extra: dict | None = None) -> bool:
    """The one resumability check callers rely on -- no other state is consulted (this project's
    established `storage/*.py` convention, reused here)."""
    return assessment_path(source, base_dir=base_dir, extra=extra).exists()


def write_cached_assessment(
    source: CircumstanceSource, assessment: CircumstanceAssessment, *, base_dir: Path | None = None, extra: dict | None = None
) -> Path:
    """Writes (or overwrites) one circumstance's cached assessment. Idempotent -- re-running the
    same real circumstance just overwrites its own file (same content, in practice, since the same
    facts produce the same cache key)."""
    path = assessment_path(source, base_dir=base_dir, extra=extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(asdict(assessment)))
    os.replace(tmp_path, path)  # atomic on POSIX and Windows
    return path


def read_cached_assessment(
    source: CircumstanceSource, *, base_dir: Path | None = None, extra: dict | None = None
) -> CircumstanceAssessment:
    path = assessment_path(source, base_dir=base_dir, extra=extra)
    envelope = json.loads(path.read_text())
    return CircumstanceAssessment(**envelope)


class FilesystemCircumstanceCache:
    """Real `analysis.circumstance.engine.CircumstanceCache`-shaped implementation over this
    module's filesystem functions -- the object a live caller passes as `synthesize_circumstance`'s
    `cache=` argument. `base_dir` is exposed (not hardcoded) so tests can point it at a temp
    directory instead of the real `data/cache/` tree."""

    def __init__(self, *, base_dir: Path | None = None, extra: dict | None = None) -> None:
        self._base_dir = base_dir
        self._extra = extra

    def with_extra(self, extra: dict | None) -> "FilesystemCircumstanceCache":
        """Same cache, keyed additionally on `extra` (see `cache_key`)."""
        return FilesystemCircumstanceCache(base_dir=self._base_dir, extra=extra)

    def has(self, source: CircumstanceSource) -> bool:
        return has_cached_assessment(source, base_dir=self._base_dir, extra=self._extra)

    def read(self, source: CircumstanceSource) -> CircumstanceAssessment:
        return read_cached_assessment(source, base_dir=self._base_dir, extra=self._extra)

    def write(self, source: CircumstanceSource, assessment: CircumstanceAssessment) -> None:
        write_cached_assessment(source, assessment, base_dir=self._base_dir, extra=self._extra)
