"""Detect a real, current circumstance change -> synthesize a reliability-aware, grounded point of
view (Chris, 2026-09-19: "I don't think reasoning should be limited to injuries").

`engine.py` is the shared, detector-agnostic core (the `CircumstanceSource` protocol, prompt
scaffold, and the one real Anthropic API call). Each detector module (`injury.py`, and future
`matchup_extreme.py`/`depth_chart.py`) owns its own detection logic and produces a real, typed
result implementing `CircumstanceSource` -- see `engine.py`'s module docstring for the full
rationale and the three real corrections this prompt scaffold encodes.
"""

from nfl_dfs.analysis.circumstance.engine import (
    DEFAULT_MAX_ARTICLE_CHARS,
    DEFAULT_MAX_ARTICLES,
    DEFAULT_MAX_TOKENS,
    CircumstanceAssessment,
    CircumstanceSource,
    MessagesResult,
    build_anthropic_messages_client,
    find_relevant_articles,
    synthesize_circumstance,
)
from nfl_dfs.analysis.circumstance.injury import (
    DEPARTED_SHARE_FLOOR,
    CircumstanceChange,
    detect_injury_circumstance_change,
)

__all__ = [
    "DEFAULT_MAX_ARTICLE_CHARS",
    "DEFAULT_MAX_ARTICLES",
    "DEFAULT_MAX_TOKENS",
    "DEPARTED_SHARE_FLOOR",
    "CircumstanceAssessment",
    "CircumstanceChange",
    "CircumstanceSource",
    "MessagesResult",
    "build_anthropic_messages_client",
    "detect_injury_circumstance_change",
    "find_relevant_articles",
    "synthesize_circumstance",
]
