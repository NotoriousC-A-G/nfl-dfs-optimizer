"""The actual matching logic ADR-0013 designed and left as follow-on work: crosswalk-ID probe
gated by mandatory name verification, then the composite name+team+position fallback matcher,
then the conflict/no-match outcome policy (ADR-0013 decisions 2-3). Produces `PlayerIdentity`
records against the interface scaffolded in `identity.py` — that file's dataclass shapes are not
modified here.

DraftKings is the week's anchor/master list (ADR-0013 decision 3): every DK player gets a
`PlayerIdentity`, and PFF/RotoGrinders/Footballguys are resolved *against* that DK row, not the
reverse.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from nfl_dfs.normalization.crosswalk import CROSSWALK_ID_COLUMNS, find_row_by_name_team_position
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity, SourceMatch, VENDOR_SOURCES
from nfl_dfs.normalization.name_utils import normalize_name
from nfl_dfs.normalization.position_aliases import normalize_position
from nfl_dfs.normalization.registry import PlayerRegistry
from nfl_dfs.normalization.team_aliases import normalize_team


@dataclass(frozen=True)
class SourcePlayer:
    """One player row as a vendor source represents it — the matcher's input shape, distinct
    from PlayerIdentity (the output shape). `native_id` is always that source's own raw ID,
    unreformatted, matching SourceMatch.native_id's contract in identity.py.
    """

    native_id: str
    name: str
    team: str | None
    position: str
    jersey_number: str | None = None
    birthdate: str | None = None


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return value == ""


def _combine_note(primary: str | None, extra: str | None) -> str | None:
    if primary and extra:
        return f"{primary}; {extra}"
    return primary or extra


def _fallback_match(
    source: str,
    norm_name: str,
    canonical_team: str | None,
    canonical_position: str,
    pool: list[SourcePlayer],
    anchor_jersey: str | None = None,
    anchor_birthdate: str | None = None,
    note: str | None = None,
) -> SourceMatch:
    name_team_matches = [
        p for p in pool if normalize_name(p.name) == norm_name and normalize_team(source, p.team) == canonical_team
    ]
    if not name_team_matches:
        return SourceMatch(native_id=None, method=MatchMethod.UNRESOLVED, note=note)

    position_matches = [p for p in name_team_matches if normalize_position(source, p.position) == canonical_position]
    if not position_matches:
        seen_positions = sorted({normalize_position(source, p.position) or "" for p in name_team_matches})
        return SourceMatch(
            native_id=None,
            method=MatchMethod.AMBIGUOUS,
            note=_combine_note(
                f"name+team matched but position disagreed (expected {canonical_position}, "
                f"source has {seen_positions}); check position alias table",
                note,
            ),
        )

    if len(position_matches) == 1:
        match = position_matches[0]
        return SourceMatch(native_id=str(match.native_id), method=MatchMethod.NAME_TEAM_POSITION, note=note)

    return _resolve_collision(position_matches, anchor_jersey, anchor_birthdate, note)


def _resolve_collision(
    candidates: list[SourcePlayer],
    anchor_jersey: str | None,
    anchor_birthdate: str | None,
    note: str | None,
) -> SourceMatch:
    """ADR-0013 decision 3 tiebreak order for a genuine name+team+position collision: jersey
    number, then birthdate, else AMBIGUOUS with a name_collision note — never auto-picked.
    """
    if anchor_jersey:
        jersey_matches = [p for p in candidates if p.jersey_number == anchor_jersey]
        if len(jersey_matches) == 1:
            match = jersey_matches[0]
            return SourceMatch(
                native_id=str(match.native_id),
                method=MatchMethod.NAME_TEAM_POSITION,
                note=_combine_note("disambiguated by jersey number", note),
            )
        if jersey_matches:
            candidates = jersey_matches

    if anchor_birthdate:
        birthdate_matches = [p for p in candidates if p.birthdate == anchor_birthdate]
        if len(birthdate_matches) == 1:
            match = birthdate_matches[0]
            return SourceMatch(
                native_id=str(match.native_id),
                method=MatchMethod.NAME_TEAM_POSITION,
                note=_combine_note("disambiguated by birthdate", note),
            )

    return SourceMatch(
        native_id=None,
        method=MatchMethod.AMBIGUOUS,
        note=_combine_note(
            f"name_collision: {len(candidates)} candidates tied on name+team+position "
            f"({[c.native_id for c in candidates]})",
            note,
        ),
    )


def resolve_vendor_match(
    source: str,
    anchor_norm_name: str,
    anchor_team: str | None,
    anchor_position: str,
    pool: list[SourcePlayer],
    crosswalk_row: pd.Series | None,
    anchor_jersey: str | None = None,
    anchor_birthdate: str | None = None,
) -> SourceMatch:
    """ADR-0013 decision 2: crosswalk-ID probe (currently PFF only, via pff_id), gated by
    mandatory name verification, then fallback to composite name+team+position matching.
    """
    crosswalk_column = CROSSWALK_ID_COLUMNS.get(source)
    if crosswalk_column is not None and crosswalk_row is not None:
        native_id = crosswalk_row.get(crosswalk_column)
        if not _is_missing(native_id):
            candidate = next((p for p in pool if str(p.native_id) == str(native_id)), None)
            if candidate is not None:
                candidate_norm_name = normalize_name(candidate.name)
                candidate_team = normalize_team(source, candidate.team)
                candidate_position = normalize_position(source, candidate.position)
                crosswalk_names = {
                    normalize_name(str(crosswalk_row.get("name") or "")),
                    normalize_name(str(crosswalk_row.get("merge_name") or "")),
                }
                if (
                    candidate_norm_name in crosswalk_names
                    and candidate_team == anchor_team
                    and candidate_position == anchor_position
                ):
                    return SourceMatch(native_id=str(candidate.native_id), method=MatchMethod.CROSSWALK)
                return _fallback_match(
                    source,
                    anchor_norm_name,
                    anchor_team,
                    anchor_position,
                    pool,
                    anchor_jersey,
                    anchor_birthdate,
                    note=(
                        f"crosswalk {crosswalk_column}={native_id} rejected by name-verification "
                        f"gate (candidate name {candidate.name!r} vs crosswalk {crosswalk_names})"
                    ),
                )

    return _fallback_match(source, anchor_norm_name, anchor_team, anchor_position, pool, anchor_jersey, anchor_birthdate)


def _resolve_dst(dk_player: SourcePlayer, anchor_team: str | None, pools: dict[str, list[SourcePlayer]]) -> PlayerIdentity:
    """DST canonical ID and matching is pure team-abbreviation normalization (ADR-0013 decision
    1) — no name-based matching, since a team defense isn't a person.
    """
    sources: dict[str, SourceMatch] = {
        "draftkings": SourceMatch(native_id=str(dk_player.native_id), method=MatchMethod.NAME_TEAM_POSITION),
    }
    for source in VENDOR_SOURCES:
        if source == "draftkings":
            continue
        pool = pools.get(source, [])
        match = next(
            (
                p
                for p in pool
                if normalize_position(source, p.position) == "DST" and normalize_team(source, p.team) == anchor_team
            ),
            None,
        )
        sources[source] = (
            SourceMatch(native_id=str(match.native_id), method=MatchMethod.NAME_TEAM_POSITION)
            if match is not None
            else SourceMatch(native_id=None, method=MatchMethod.UNRESOLVED)
        )

    identity = PlayerIdentity(
        canonical_id=f"DST_{anchor_team}",
        display_name=dk_player.name,
        position="DST",
        team=anchor_team or dk_player.team,
        sources=sources,
    )
    missing = identity.missing_from_sources()
    return replace(identity, flags=[f"missing_from_sources: {missing}"]) if missing else identity


def resolve_player_identity(
    dk_player: SourcePlayer,
    pools: dict[str, list[SourcePlayer]],
    crosswalk: pd.DataFrame,
    registry: PlayerRegistry,
) -> PlayerIdentity:
    anchor_norm_name = normalize_name(dk_player.name)
    anchor_team = normalize_team("draftkings", dk_player.team)
    anchor_position = normalize_position("draftkings", dk_player.position)

    if anchor_position == "DST":
        return _resolve_dst(dk_player, anchor_team, pools)

    crosswalk_row = find_row_by_name_team_position(crosswalk, anchor_norm_name, anchor_team, anchor_position)
    gsis_id = crosswalk_row.get("gsis_id") if crosswalk_row is not None else None
    pfr_id = crosswalk_row.get("pfr_id") if crosswalk_row is not None else None
    anchor_birthdate = crosswalk_row.get("birthdate") if crosswalk_row is not None else None

    canonical_id = (
        str(gsis_id)
        if not _is_missing(gsis_id)
        else registry.get_or_create(anchor_norm_name, anchor_team, anchor_position)
    )

    sources: dict[str, SourceMatch] = {
        "draftkings": SourceMatch(
            native_id=str(dk_player.native_id),
            method=MatchMethod.NAME_TEAM_POSITION,
            note="anchor row: DK is the week's master list, not itself matched against anything",
        )
    }
    for source in VENDOR_SOURCES:
        if source == "draftkings":
            continue
        pool = pools.get(source, [])
        sources[source] = resolve_vendor_match(
            source,
            anchor_norm_name,
            anchor_team,
            anchor_position,
            pool,
            crosswalk_row,
            anchor_jersey=dk_player.jersey_number,
            anchor_birthdate=None if _is_missing(anchor_birthdate) else str(anchor_birthdate),
        )

    identity = PlayerIdentity(
        canonical_id=canonical_id,
        display_name=dk_player.name,
        position=anchor_position,
        team=anchor_team or dk_player.team,
        nflverse_gsis_id=None if _is_missing(gsis_id) else str(gsis_id),
        nflverse_pfr_id=None if _is_missing(pfr_id) else str(pfr_id),
        sources=sources,
    )
    flags = []
    missing = identity.missing_from_sources()
    if missing:
        flags.append(f"missing_from_sources: {missing}")
    if identity.has_ambiguous_matches():
        flags.append("has_ambiguous_matches: needs QA review before blending")
    return replace(identity, flags=flags) if flags else identity


def reconcile_week(
    dk_pool: list[SourcePlayer],
    pff_pool: list[SourcePlayer],
    rotogrinders_pool: list[SourcePlayer],
    footballguys_pool: list[SourcePlayer],
    crosswalk: pd.DataFrame,
    registry: PlayerRegistry | None = None,
) -> list[PlayerIdentity]:
    """Entry point: DK's weekly draftables pull is the anchor list (ADR-0013 decision 3) — the
    practical traversal is "for each DK player this week, resolve PFF/RotoGrinders/Footballguys."
    """
    registry = registry if registry is not None else PlayerRegistry()
    pools = {"pff": pff_pool, "rotogrinders": rotogrinders_pool, "footballguys": footballguys_pool}
    identities = [resolve_player_identity(p, pools, crosswalk, registry) for p in dk_pool]
    registry.save()
    return identities
