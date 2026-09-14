"""Position-label normalization (ADR-0013 decision 2, fallback matcher position normalization).

Canonical vocabulary is this project's five DFS-relevant roster positions — confirmed live from
DK's own draftables payload (`docs/phase0/data-availability.md`: "QB/RB/WR/TE/DST, no CPT").

- **PFF**: confirmed live (`/v1/facet/rushing/summary`) to use `HB` for running backs where the
  rest of the pipeline uses `RB` — the exact mismatch ADR-0013 flagged. The same live pull's
  other positions (`QB, TE, WR`) already matched canonical, so only `HB` needs aliasing; PFF's
  facet endpoints don't carry a DST-equivalent row (team defenses aren't "offense/defense
  players" in PFF's schema), so there's no PFF DST alias to confirm one way or the other.
- **crosswalk**: uses `RB` natively (confirmed from `import_ids()`'s own position column) — no
  alias needed. Its other position values (`C, CB, DB, DE, DL, DT, LB, OT, PK, PN, S, T, XX,
  "? "`) are outside this project's five-position vocabulary entirely (offense-line/defense/
  special-teams detail this project doesn't roster) and are intentionally left unmapped rather
  than forced into a canonical bucket.
- **DraftKings**: confirmed live to already use the canonical vocabulary directly, DST included
  — no aliasing needed; empty table is deliberate, not an oversight.
- **DEF/D → DST**: included per ADR-0013's decision text, which calls this out as a needed alias
  even though no live vendor payload showing a bare `DEF`/`D` label was captured in Phase 0 —
  kept as a documented forward-looking alias since the ADR explicitly specifies it, but flagged
  here as *not independently live-verified* the way `HB` was.
- **RotoGrinders**: live-verified via `ingestion/rotogrinders.py`'s real pull (467 players, 2026
  wk1) — raw `POS` values seen: `QB, RB, WR, TE, DST, K`. `DST` is already canonical; `K` falls
  outside this project's five-position vocabulary the same way the crosswalk's non-skill
  positions do, and is intentionally left unmapped rather than forced into one of the five. **No
  RotoGrinders position alias is needed** — confirmed, not left unverified — but the previously
  guessed `DEF`/`D` entries are also kept below since a K-only or DST-relabeling change on RG's
  side is plausible and harmless to alias defensively.
- **Footballguys**: live-verified via `ingestion/footballguys.py`'s real pull across all five of
  its position-filter values (`qb, rb, wr, te, td`, 2026 wk1). `QB/RB/WR/TE` are already
  canonical. **Team defenses are labeled `TD`, not `DEF`/`D`/`DST`** — this is a genuine finding
  that contradicts this table's own prior placeholder guess (which assumed `DEF`/`D` "even
  though no live vendor payload showing a bare `DEF`/`D` label was captured in Phase 0"). That
  guess is now known wrong for Footballguys specifically: its position-select UI's own radio
  value is `td` and every team-defense row rendered `<span class="pos-TD">TD</span>`. `DEF`/`D`
  are left in the table below in case some other Footballguys view uses them, but `TD` is the
  alias that actually matters for the projections endpoint this project ingests from.
"""

from __future__ import annotations

CANONICAL_POSITIONS: frozenset[str] = frozenset({"QB", "RB", "WR", "TE", "DST"})

# Both sources this used to flag as unverified are now confirmed live (see module docstring).
UNVERIFIED_SOURCES: frozenset[str] = frozenset()

_ALIASES: dict[str, dict[str, str]] = {
    "pff": {
        "HB": "RB",
        "DEF": "DST",
        "D": "DST",
    },
    "crosswalk": {
        "DEF": "DST",
        "D": "DST",
    },
    "draftkings": {
        "DEF": "DST",
        "D": "DST",
    },
    "rotogrinders": {
        "DEF": "DST",
        "D": "DST",
    },
    "resultsdb": {
        "DEF": "DST",
        "D": "DST",
    },
    "footballguys": {
        "TD": "DST",
        "DEF": "DST",
        "D": "DST",
    },
}


def normalize_position(source: str, raw: str | None) -> str | None:
    if raw is None or (isinstance(raw, float) and raw != raw):  # NaN check, mirrors team_aliases
        return None
    code = str(raw).strip().upper()
    return _ALIASES.get(source, {}).get(code, code)
