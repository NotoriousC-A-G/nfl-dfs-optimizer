"""Small, disclosed name-matching helpers for `agent_results_collector.py`'s join between
`agent_results.csv`'s free-text `players` field and nflverse's real settled box-score rows.

**Why a fallback matcher, not a canonical-ID join.** `agent_results_store.py`'s `players` field is
stored as plain "Name (POS-TEAM)" text (see that module's own docstring on why -- no per-week
`PlayerIdentity` snapshot is persisted for it to join against at write time). Matching those
strings back to nflverse's `player_display_name` is the same tier of reliability this project
already accepts for DK/RotoGrinders in `normalization/identity.py`'s own NAME_TEAM_POSITION
fallback -- not a new, weaker standard invented here.
"""

from __future__ import annotations

import re

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_player_name(name: str) -> str:
    """Lowercase, drop periods/apostrophes, drop a trailing generational suffix, collapse
    whitespace -- enough to match "K.C. Concepcion Jr." against nflverse's own spelling without
    guessing at a full fuzzy-match scheme."""
    cleaned = name.lower().replace(".", "").replace("'", "").replace("-", " ")
    tokens = [t for t in cleaned.split() if t]
    if tokens and tokens[-1] in _SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)


_PLAYER_TOKEN_RE = re.compile(r"^(?P<name>.+) \((?P<position>[A-Z]+)-(?P<team>[A-Z]{1,3})\)$")


def parse_player_token(token: str) -> tuple[str, str, str]:
    """Parses one `AgentResultRow.players` entry, e.g. "Trevor Lawrence (QB-JAX)" or
    "Jaguars (DST-JAX)", into `(display_name, position, team)`. Raises `ValueError` on a token
    that doesn't match the "Name (POS-TEAM)" shape every writer of this field is expected to use.
    """
    m = _PLAYER_TOKEN_RE.match(token.strip())
    if not m:
        raise ValueError(f"player token does not match 'Name (POS-TEAM)' shape: {token!r}")
    return m.group("name"), m.group("position"), m.group("team")
