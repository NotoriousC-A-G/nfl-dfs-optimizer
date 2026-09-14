"""Team-abbreviation normalization (ADR-0013 decision 2, fallback matcher team normalization).

Canonical vocabulary is DraftKings' own team abbreviations — DK is the week's anchor/master
list per ADR-0013's outcome policy, so every other source's codes are aliased *to* DK's
vocabulary rather than to some fourth invented standard.

Per-source alias tables, live-verified against Phase 0 samples where a live pull exists
(`docs/phase0/data-availability.md`, this module's own live checks) — not assumed:

- **nflverse crosswalk** (`nfl_data_py.import_ids()`'s `team` column): confirmed live to use
  `GBP, LVR, KCC, JAC, NEP, NOS, SFO, TBB` for eight active franchises (ADR-0013's own finding,
  re-confirmed here), plus retired/relocated codes `OAK, SDC, STL, RAM, FA, FA*` for historical
  and free-agent rows. `FA`/`FA*` (no current team) alias to `None` rather than a guessed code —
  a player with no current team can't be resolved to a DK slate team, and guessing one would be
  exactly the kind of silent wrong-match this ADR exists to prevent.
- **PFF** (`/v1/facet/rushing/summary` live sample, `db_season` 2026 wk 1): confirmed live to
  use its *own* non-standard codes beyond the crosswalk's — `ARZ, BLT, CLV, HST, LA` — where DK
  uses `ARI, BAL, CLE, HOU, LAR`. This was not flagged in ADR-0013 (which only checked PFF's
  position label) and is a genuine additional finding from this implementation pass: PFF needs
  its own team alias table, not just a position alias table.
- **DraftKings**: confirmed live (draftGroupId 153070) to already use the canonical vocabulary
  (`ARI, GB, LAC, LV, MIA, MIN, PHI, WAS` all matched with no aliasing needed) — DK is the
  canonical target, so this table is intentionally empty.
- **RotoGrinders**: live-verified via `ingestion/rotogrinders.py`'s real pull against the "NFL DFS
  Projections" house grid (467 players, 2026 wk1) — uses the exact same non-canonical vocabulary
  as the nflverse crosswalk for the same eight franchises: `GBP, JAC, KCC, LVR, NEP, NOS, SFO,
  TBB`. All other observed codes (`ARI, ATL, ..., HOU, LAC, LAR, ...`) already matched canonical.
  This closes the gap this module previously left as `UNVERIFIED_SOURCES`.
- **Footballguys**: live-verified via `ingestion/footballguys.py`'s real pull (a QB-position pull
  plus a team-defense/`td`-position pull, 2026 wk1) — every team code observed (`ARI, ATL, BAL,
  ..., JAX, KC, LV, NE, NO, SF, TB, WAS`, the team-defense pull's own 32 franchise codes included)
  already matched canonical. **Confirmed empty, not just unverified**: Footballguys needs no team
  alias table at all.
- **nflverse pbp/schedule** (`nfl_data_py.import_pbp_data()`'s `posteam`/`defteam` columns and
  `import_schedules()`'s `home_team`/`away_team` columns — added for `ingestion/nflverse.py` and
  `ingestion/odds_api.py`, the game/team-level sources, not the player-ID crosswalk covered above):
  live-verified (2025/2026 pbp pull, 2025+2026 schedule pull) to use canonical codes for every
  franchise *except* the Rams, which appear as `LA` rather than `LAR` — the one non-canonical code
  across 32 teams in both the play-by-play and schedule tables.
"""

from __future__ import annotations

CANONICAL_TEAMS: frozenset[str] = frozenset(
    {
        "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
        "DET", "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA",
        "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
        "TEN", "WAS",
    }
)

# Both sources this used to flag as unverified are now confirmed live (see module docstring) —
# RotoGrinders genuinely needs the aliases below; Footballguys genuinely needs none. Kept as an
# empty frozenset (rather than deleted) so a caller checking "is this source's table trustworthy
# yet" has a stable name to check against as new sources are added later.
UNVERIFIED_SOURCES: frozenset[str] = frozenset()

_ALIASES: dict[str, dict[str, str | None]] = {
    "crosswalk": {
        "GBP": "GB",
        "LVR": "LV",
        "KCC": "KC",
        "JAC": "JAX",
        "NEP": "NE",
        "NOS": "NO",
        "SFO": "SF",
        "TBB": "TB",
        "OAK": "LV",
        "SDC": "LAC",
        "STL": "LAR",
        "RAM": "LAR",
        "FA": None,
        "FA*": None,
    },
    "pff": {
        "ARZ": "ARI",
        "BLT": "BAL",
        "CLV": "CLE",
        "HST": "HOU",
        "LA": "LAR",
    },
    "draftkings": {},
    "rotogrinders": {
        "GBP": "GB",
        "JAC": "JAX",
        "KCC": "KC",
        "LVR": "LV",
        "NEP": "NE",
        "NOS": "NO",
        "SFO": "SF",
        "TBB": "TB",
    },
    "footballguys": {},
    "nflverse_schedule": {
        "LA": "LAR",
    },
}


def normalize_team(source: str, raw: str | None) -> str | None:
    if raw is None or (isinstance(raw, float) and raw != raw):  # NaN check without importing math/pandas here
        return None
    code = str(raw).strip().upper()
    aliases = _ALIASES.get(source, {})
    if code in aliases:
        return aliases[code]
    # No alias entry: pass through unchanged. True for the common case (code is already
    # canonical) and also the fallback for an unmapped code from an unverified source — callers
    # that care should check the result against CANONICAL_TEAMS themselves.
    return code
