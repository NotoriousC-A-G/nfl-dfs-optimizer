"""Local `player_registry` store for UUID-fallback canonical IDs (ADR-0013 decision 1).

Only used when the crosswalk has no `gsis_id` for a player (undrafted rookie, crosswalk refresh
lag). Keyed by normalized (name, team, position) so the same player resolves to the same
project-minted canonical ID in later weeks — "first-seen wins" per the ADR, not a fresh UUID
every run.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "data" / "cache" / "player_registry.json"


class PlayerRegistry:
    def __init__(self, path: Path = DEFAULT_REGISTRY_PATH) -> None:
        self._path = path
        self._entries: dict[str, str] = {}
        if path.exists():
            self._entries = json.loads(path.read_text())

    @staticmethod
    def _key(normalized_name: str, canonical_team: str | None, canonical_position: str) -> str:
        return f"{normalized_name}|{canonical_team or ''}|{canonical_position}"

    def get_or_create(self, normalized_name: str, canonical_team: str | None, canonical_position: str) -> str:
        key = self._key(normalized_name, canonical_team, canonical_position)
        if key not in self._entries:
            self._entries[key] = f"local_{uuid.uuid4().hex}"
        return self._entries[key]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._entries, indent=2, sort_keys=True))
