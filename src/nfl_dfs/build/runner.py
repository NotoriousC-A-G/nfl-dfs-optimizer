"""File-contract runner for the LLM stages (game analysts, expert) -- ADR-0046, architect review.

The runner never calls a model. It writes a request, something else produces the answer, and the runner
validates it. On Friday that something is a Claude Code session subagent reading `prompt.md` and writing
`response.json`; later an API client can fill the same slot without touching anything else here.

    <root>/<season>/<week>/<stage>/<item_id>__<key12>/
        prompt.md            what to answer (the whole request, self-contained)
        context.json         the evidence it was built from (for audit)
        meta.json            cache-key parts: prompt version, model, freshness token
        response.json        WRITTEN BY THE BACKEND -- raw JSON answer
        attempts.json        every rejected attempt with its violations
        retry_prompt.md      written after a rejected first attempt: the same prompt + the exact errors
    <root>/<season>/<week>/<stage>/<key>.json       content-addressed cache of a VALIDATED response

**Reproducibility is persisted artifacts, not deterministic reruns.** The cache key is a sha256 of the
evidence hash, the prompt version, the model id and a `freshness` token (e.g. a hash of the Q-override
file and the injury capture), so a changed fact is a different key. A cache hit is RE-PARSED and
RE-VALIDATED against the current evidence rather than trusted, so a stale or invalid entry can never
slip through. Nothing is silently re-asked: a missing answer is `awaiting`, a first bad answer is
`retry` (one retry, with the exact errors), a second is `rejected`. `require_all_ok` raises with the full
list -- a stage that cannot complete halts the run loudly (Chris, 2026-10-07: no silent fallback).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from nfl_dfs.build.common import Violation

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = _REPO_ROOT / "data" / "cache" / "build_llm"
MAX_ATTEMPTS = 2  # the original answer + one retry

STATUS_OK, STATUS_CACHED, STATUS_AWAITING, STATUS_RETRY, STATUS_REJECTED = "ok", "cached", "awaiting", "retry", "rejected"


@dataclass(frozen=True)
class StageSpec:
    stage: str  # "analyst" | "expert"
    item_id: str  # e.g. a game id
    key: str  # content-addressed cache key
    prompt: Callable[[list[str] | None], str]  # retry errors (or None) -> the full prompt text
    parse: Callable[[str], tuple[Any | None, list[Violation]]]  # raw response text -> (result, violations)
    context: dict[str, Any]
    meta: dict[str, Any]


@dataclass(frozen=True)
class StageResult:
    item_id: str
    status: str
    result: Any | None
    violations: tuple[Violation, ...]
    directory: Path

    @property
    def errors(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity == "error")


class StageFailure(RuntimeError):
    def __init__(self, stage: str, results: list[StageResult]):
        bad = [r for r in results if r.status not in (STATUS_OK, STATUS_CACHED)]
        lines = [f"  {r.item_id}: {r.status}" + (f" -- {r.errors[0].code}: {r.errors[0].message}" if r.errors else "") for r in bad]
        super().__init__(f"{stage}: {len(bad)} of {len(results)} item(s) not complete:\n" + "\n".join(lines))
        self.stage, self.results = stage, results


def cache_key(**parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def _stage_dir(root: Path, season: int, week: int, stage: str) -> Path:
    return root / str(season) / str(week) / stage


def item_dir(spec: StageSpec, root: Path, season: int, week: int) -> Path:
    safe = spec.item_id.replace("@", "_at_").replace("/", "_")
    return _stage_dir(root, season, week, spec.stage) / f"{safe}__{spec.key[:12]}"


def _cache_path(spec: StageSpec, root: Path, season: int, week: int) -> Path:
    return _stage_dir(root, season, week, spec.stage) / f"{spec.key}.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def prepare_requests(specs: list[StageSpec], *, season: int, week: int, root: Path | None = None) -> list[Path]:
    """Write `prompt.md`/`context.json`/`meta.json` for every item that has no valid cache entry and no
    pending retry; returns the directories awaiting an answer."""
    root = root or DEFAULT_ROOT
    pending: list[Path] = []
    for spec in specs:
        if _cache_path(spec, root, season, week).exists():
            continue
        d = item_dir(spec, root, season, week)
        if not (d / "prompt.md").exists():
            _atomic_write(d / "prompt.md", spec.prompt(None))
            _atomic_write(d / "context.json", json.dumps(spec.context, indent=2, default=str))
            _atomic_write(d / "meta.json", json.dumps({**spec.meta, "key": spec.key, "item_id": spec.item_id}, indent=2, default=str))
        pending.append(d)
    return pending


def collect_results(specs: list[StageSpec], *, season: int, week: int, root: Path | None = None, max_attempts: int = MAX_ATTEMPTS) -> list[StageResult]:
    root = root or DEFAULT_ROOT
    out: list[StageResult] = []
    for spec in specs:
        d = item_dir(spec, root, season, week)
        cache = _cache_path(spec, root, season, week)
        if cache.exists():
            result, violations = spec.parse(json.loads(cache.read_text())["response_text"])
            if result is not None and not [v for v in violations if v.severity == "error"]:
                out.append(StageResult(spec.item_id, STATUS_CACHED, result, tuple(violations), d))
                continue
            cache.unlink()  # the entry no longer validates against current evidence/code -- never trust it
        response = d / "response.json"
        if not response.exists():
            out.append(StageResult(spec.item_id, STATUS_AWAITING, None, (), d))
            continue
        text = response.read_text()
        result, violations = spec.parse(text)
        errors = [v for v in violations if v.severity == "error"]
        if result is not None and not errors:
            _atomic_write(cache, json.dumps({"response_text": text, "meta": spec.meta, "key": spec.key}))
            out.append(StageResult(spec.item_id, STATUS_OK, result, tuple(violations), d))
            continue
        attempts_path = d / "attempts.json"
        attempts = json.loads(attempts_path.read_text()) if attempts_path.exists() else []
        attempts.append({"errors": [{"code": v.code, "message": v.message, "path": v.path} for v in errors]})
        _atomic_write(attempts_path, json.dumps(attempts, indent=2))
        response.rename(d / f"response.attempt{len(attempts)}.json")  # a fresh answer must be written for the next attempt
        if len(attempts) < max_attempts:
            _atomic_write(d / "retry_prompt.md", spec.prompt([f"{v.code}: {v.message}" for v in errors]))
            out.append(StageResult(spec.item_id, STATUS_RETRY, None, tuple(errors), d))
        else:
            out.append(StageResult(spec.item_id, STATUS_REJECTED, None, tuple(errors), d))
    return out


def require_all_ok(stage: str, results: list[StageResult]) -> None:
    if any(r.status not in (STATUS_OK, STATUS_CACHED) for r in results):
        raise StageFailure(stage, results)
