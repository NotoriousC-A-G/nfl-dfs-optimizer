"""Output shape for player ID reconciliation (PRD Section 5 step 2; design in ADR-0013,
`docs/adr/0013-player-id-reconciliation.md`).

This module defines the *interface* the Normalization stage produces — a canonical
`PlayerIdentity` per player plus a native-ID map, so downstream stages and QA can read
coverage/match-quality straight off this table instead of joining a separate mapping table.

**Not implemented here:** the crosswalk fetch/cache (`nfl_data_py.import_ids()`), the
team/position alias tables, and the actual fallback matcher (name normalization + composite
key lookup). Those are Data Integration Engineer implementation scope per ADR-0013. This file
is deliberately just the reviewable data shape.

Live findings behind this shape (see ADR-0013 for full detail):
- The nflverse/ffverse ID crosswalk directly covers PFF (`pff_id`, verified against PFF's own
  live API) and provides nflverse's own `gsis_id`, used here as the preferred canonical ID.
- It does NOT cover DraftKings or RotoGrinders at all (no such columns exist).
- Its Footballguys-shaped `pfr_id` field is NOT reliable as a join key — a live check found
  Footballguys' own `data-playerid` for Jahmyr Gibbs ("GibbJa00") actually belongs to a
  different real player (Jack Gibbens) in the authoritative crosswalk. Footballguys is
  therefore treated the same as DK and RotoGrinders: matched by name + team + position, not by
  a trusted native-ID join.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# The four vendor sources the Normalization stage reconciles (Section 4/5). nflverse-derived
# IDs (gsis_id, pfr_id) are tracked separately on PlayerIdentity itself — they come from the
# crosswalk lookup step, not the per-source fallback matcher below.
VENDOR_SOURCES: tuple[str, ...] = ("draftkings", "pff", "rotogrinders", "footballguys")


class MatchMethod(Enum):
    """How a given vendor source's native ID was resolved for a player. Recorded per source,
    not just once per player, since e.g. PFF might resolve via CROSSWALK while RotoGrinders for
    the same player resolves via NAME_TEAM_POSITION.
    """

    # Resolved via the nflverse crosswalk's own ID field (currently: pff_id for PFF only) AND
    # confirmed by the mandatory name-verification gate (ADR-0013 decision 2, step 2).
    CROSSWALK = "crosswalk"
    # Resolved via the normalized name + team + position fallback matcher (ADR-0013 decision 2,
    # step 3) — the path every DK and RotoGrinders match takes, and the path Footballguys takes
    # despite its data-playerid superficially looking like a PFR ID.
    NAME_TEAM_POSITION = "name_team_position"
    # No candidate found for this source at all. Contributes to PlayerIdentity.flags via
    # missing_from_sources().
    UNRESOLVED = "unresolved"
    # Multiple equally-plausible candidates, or a suspected genuine name collision that couldn't
    # be broken by position/jersey number/birthdate. Never auto-resolved — always surfaced for
    # QA. See ADR-0013 decision 3.
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class SourceMatch:
    """The result of matching one player against one vendor source."""

    native_id: str | None  # e.g. DK's playerDkId, PFF's player_id, RG's PLAYERID/RGID,
    # Footballguys' data-playerid — always the source's own raw ID string, never reformatted.
    method: MatchMethod
    # Free-text context for anything other than a clean CROSSWALK/NAME_TEAM_POSITION hit — e.g.
    # which candidates tied for an AMBIGUOUS match, or why a crosswalk candidate was rejected by
    # the name-verification gate. None for a routine, unambiguous resolution.
    note: str | None = None


@dataclass(frozen=True)
class PlayerIdentity:
    """Canonical identity record for one player (or one DST team-defense entity), produced by
    the Normalization stage (PRD Section 5 step 2). One row per player per week — vendor IDs
    and match quality can change week to week (new primary starter, a source's payload changes
    shape, etc.), so this is not assumed stable across weeks except via canonical_id itself.

    canonical_id selection (ADR-0013 decision 1):
      - nflverse gsis_id, when the crosswalk resolves one for this player — preferred because
        the foundation layer (Section 5 step 1, import_pbp_data() aggregation) is already keyed
        on gsis_id-shaped columns, so reusing it avoids a second mapping hop for that data.
      - else a project-minted UUID, persisted in a local player_registry store so the same
        player resolves to the same canonical_id in later weeks (first-seen wins).
      - for DST entities: a fixed synthetic key, "DST_<normalized_team_abbr>" — not a person,
        not in gsis_id, resolved purely by team-abbreviation normalization.
    """

    canonical_id: str
    display_name: str
    # Normalized to this project's canonical vocabulary (e.g. "RB", not PFF's "HB"; "DST" for
    # team defenses) — see ADR-0013's position alias table note. Raw per-source position labels
    # are not retained here; if a per-source discrepancy needs debugging, it belongs in a
    # SourceMatch.note, not a new field on this dataclass.
    position: str
    # Normalized to this project's canonical team-abbreviation vocabulary (see ADR-0013's team
    # alias table note — DK's LV/GB/WAS vs. the crosswalk's LVR/GBP/WAS, etc.).
    team: str

    # nflverse crosswalk-derived reference IDs. Populated by the crosswalk lookup step itself,
    # not the per-source fallback matcher — kept distinct from `sources` below because they
    # aren't "vendor" sources this pipeline ingests projections/grades from.
    nflverse_gsis_id: str | None = None
    nflverse_pfr_id: str | None = None  # Informational only — per ADR-0013, do NOT treat
    # equality between this and a Footballguys data-playerid as confirmed identity on its own.

    # One entry per vendor source in VENDOR_SOURCES once matching has run. A source absent from
    # this dict (rather than present with method=UNRESOLVED) means matching hasn't been
    # attempted yet for that source this week — implementations should populate all four keys
    # once normalization completes so `missing_from_sources()` below is meaningful.
    sources: dict[str, SourceMatch] = field(default_factory=dict)

    # Free-text flags for anything QA should be able to filter on directly without re-deriving
    # it from `sources` — e.g. "name_collision_suspected: Mike Williams (WR) vs Mike Williams
    # (TE), same team". Kept as plain strings rather than a closed enum since the space of
    # reasons something looks off is open-ended and QA-facing, unlike MatchMethod.
    flags: list[str] = field(default_factory=list)

    def missing_from_sources(self) -> list[str]:
        """Vendor sources with no resolved match this week — the direct answer to Section 5
        step 2's "flag players missing from any source" requirement. QA can filter the weekly
        PlayerIdentity table on this directly rather than joining a separate coverage table.
        """
        return [
            source
            for source in VENDOR_SOURCES
            if source not in self.sources or self.sources[source].method == MatchMethod.UNRESOLVED
        ]

    def has_ambiguous_matches(self) -> bool:
        """True if any vendor source's match needs human (QA) resolution before this player's
        data can be safely blended (ADR-0013 decision 3) — an ambiguous match or a suspected
        name collision that wasn't auto-resolved.
        """
        return any(match.method == MatchMethod.AMBIGUOUS for match in self.sources.values())
