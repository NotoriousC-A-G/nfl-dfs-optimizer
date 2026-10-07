"""Shared types for the build pipeline's validators."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Violation:
    """One failed check. `code` is stable (tests and retry prompts key on it); `message` is precise
    enough to send back to a model for one retry. `severity="warning"` is surfaced, never rejects."""

    code: str
    message: str
    path: str = ""
    severity: str = "error"  # "error" | "warning"
