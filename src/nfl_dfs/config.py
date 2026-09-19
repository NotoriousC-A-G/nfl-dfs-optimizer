"""Environment-backed config for data source credentials (PRD Section 4)."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass(frozen=True)
class Config:
    pff_api_key: str | None = os.getenv("PFF_API_KEY") or None
    rotogrinders_session_cookie: str | None = os.getenv("ROTOGRINDERS_SESSION_COOKIE") or None
    footballguys_session_cookie: str | None = os.getenv("FOOTBALLGUYS_SESSION_COOKIE") or None
    odds_api_key: str | None = os.getenv("ODDS_API_KEY") or None
    # Weather: Open-Meteo (primary) + NWS api.weather.gov (storm cross-check), same as the
    # MLB build — both are free/keyless, so there's no weather API key to configure.
    # analysis/circumstance/engine.py's synthesis step only (Chris, 2026-09-19) — every other data
    # source above is required for the live scripts to run at all; this one isn't. It still shows
    # up in missing() like any other field (no special-casing there), but that module's own caller
    # checks for it explicitly and degrades to "no synthesis" rather than crashing when it's unset.
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY") or None

    def missing(self) -> list[str]:
        return [f.name for f in fields(self) if getattr(self, f.name) is None]


config = Config()
