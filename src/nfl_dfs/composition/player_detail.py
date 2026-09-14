"""`PlayerDetailRecord` composition/join layer (ADR-0022 Round C -- player-detail metrics view).

This module builds ADR-0022's `PlayerDetailRecord` for one player in one week by joining the
outputs of four already-built, independently-owned modules:

- `ingestion/usage_share.py` -- `RoleShareResult`/`PlayerRoleShare` (carry/target share, role
  tier, gate identification) and the red-zone trailing aggregation
  (`aggregate_player_trailing_red_zone`).
- `ingestion/snap_share.py` -- `PlayerSnapShare` (offense/defense/special-teams snap share).
- `ingestion/pff.py` -- `PffFacetGrades`/`resolve_grade` for the `receiving/scheme` man/zone
  facet, and `TeamCoverageTendency` for the opponent's team-level man/zone rollup.
- `game_environment/score.py` -- `GameEnvironmentScore` for the player's team's game this week.

**This is a pure composition/read-model layer, per ADR-0022's own framing** ("a read-model over
existing per-player-week facts, not a new source of truth"): it does not fetch, recompute, or
duplicate any number those four modules already own. It does exactly two things no other module
does today: (1) resolves the cross-module join keys (see "The join problem" below), and (2)
produces one coherent, nullable-with-a-reason record per player, mirroring `StackProfile`'s
`bring_back_status` discipline -- "no data" and "not applicable for this position" are always
kept distinguishable, never collapsed into a bare `None`.

## The join problem this module solves

Three different join keys are in play across the four source modules, and this module is the
one place that reconciles them:

1. **`RoleShareResult`/`PlayerRoleShare.player_id`, `PlayerSnapShare.player_id`, and the
   red-zone trailing frame's `player_id`** are all already the canonical nflverse `gsis_id`
   (confirmed by each module's own docstring -- `usage_share.py`'s player_id is nflverse's own
   `rusher_player_id`/`receiver_player_id`, already gsis_id-shaped; `snap_share.py`'s
   `PlayerSnapShare.player_id` is explicitly documented as "canonical gsis_id" after its own
   `pfr_player_id` -> crosswalk join). So these three join directly against
   `PlayerIdentity.nflverse_gsis_id` with no further translation -- the one case ADR-0022 asked
   to be spot-checked (`snap_share.py`'s own join) was already solved and verified there, not
   here; this module just reuses its already-canonical output key.
2. **PFF's `receiving/scheme` facet (`PffFacetGrades.by_player_id`) is keyed by PFF's own native
   `player_id`, not `gsis_id`.** Per ADR-0022 Decision 1, this is "already the crosswalk's
   directly-verified path (ADR-0013)" -- the same `pff_id` column `normalization/crosswalk.py`
   already exposes and the full ADR-0013 matcher already resolves into
   `PlayerIdentity.sources["pff"].native_id` when the full normalization pipeline has run for a
   player. This module prefers that already-resolved value when present
   (`_pff_native_id_for_identity`), and falls back to a direct `gsis_id -> pff_id` reverse lookup
   over the cached crosswalk frame (`build_gsis_to_pff_id_map`) when it isn't -- the same crosswalk
   file `snap_share.py` already reads, just resolved in the other column direction. Neither path
   is new ingestion; both reuse `normalization/crosswalk.py`'s already-cached frame as-is.
3. **`TeamCoverageTendency` and `GameEnvironmentScore` are keyed by team**, not by player -- joined
   here via the player's own team (`GameEnvironmentScore`) or the opponent's team
   (`TeamCoverageTendency`), both plain string equality against this project's canonical team
   vocabulary (already normalized upstream by each source module).

## Shared per-player-week lookups, not an ad hoc join per call

Every source is passed in as one week-scoped lookup collection (a `dict` keyed by the source's
own natural key, or the source module's own trailing-aggregation `DataFrame`), built once per
(season, week) by the caller and reused across every player. `build_player_detail_record` then
does a real lookup against each collection for one player at a time -- this is the "does this
actually connect" discipline the task brief called out (the `Lineup`/`StackProfile` join-key gap
from the output-stage round): every join key above is checked explicitly, and a miss produces a
distinguishable reason rather than a silently wrong or silently empty field.

## Partial data -- every field nullable, every `None` has a stated reason

Every nested section below pairs its optional value(s) with a `reason: str | None` that is
populated exactly when the value(s) are `None`/empty, distinguishing (at minimum) three cases:
"this player has no resolvable `gsis_id` at all" (their `PlayerIdentity` never resolved one --
none of usage/snap/red-zone can ever join for them), "not applicable for this position" (e.g.
`own_scheme_splits` for a QB/DST, `role_share` for anyone but RB/WR/TE), and "applicable but no
data was found/supplied" (a real, ordinary miss -- bye week, unmatched PFF row, no
`GameEnvironmentScore` computed yet, etc). This is the same discipline
`StackProfile.bring_back_status` already established for this pipeline -- see that module's
docstring.

## Round B (`matchup_this_week.own_unit_grade`/`opponent_unit_grade`) -- now wired to MatchupContext

Per ADR-0022, this section was previously blocked on `MatchupContext`'s defender-identification/
grading logic not existing anywhere in this pipeline (`stack_profile.py`'s own words). Now that
`matchup/context.py` implements it, `own_unit_grade`/`opponent_unit_grade` are populated via
`matchup.context.resolve_own_opponent_unit_grades` -- the *same* team-level snap-weighted
aggregates `MatchupContext`'s own Run game/Pass protection rows compute for their multipliers,
read a second time into ADR-0022's originally-sketched `ResolvedGrade` shape (a team-level unit
has no single PFF `player_id`, so these are synthetic `TEAM_<abbr>`-keyed rows, not real per-
player facet rows -- see `resolve_own_opponent_unit_grades`'s own docstring for exactly which
unit each position maps to). This is still position-dependent and still `None` with a stated
reason when the underlying facet data or the opponent isn't available -- same "every `None` has a
reason" discipline as every other section of this module, and the same source module both the
projection-blend wiring (`projection/blend.py`'s `apply_matchup_context`) and this composition
layer now both draw from, so the two never present a different number for the same underlying
grade. `coverage_tendency_faced` (the opponent's team-level man/zone snap-rate rollup) remains
real, already-available Round A data, populated independently of the above.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from nfl_dfs.game_environment.score import GameEnvironmentScore
from nfl_dfs.ingestion.pff import PffFacetGrades, ResolvedGrade, TeamCoverageTendency, resolve_grade
from nfl_dfs.ingestion.snap_share import PlayerSnapShare
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR, PlayerRoleShare, RoleShareResult
from nfl_dfs.matchup.context import MatchupFacetInputs, resolve_own_opponent_unit_grades
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity
from nfl_dfs.projection.blend import PlayerProjection

# `own_scheme_splits` (PFF `receiving/scheme`) is scoped to WR/TE/receiving-back per ADR-0022's
# schema sketch ("WR/TE/receiving-back only"). "Receiving-back" is this project's canonical `RB`
# label (PFF's own HB/FB labels are already aliased to RB upstream, per position_aliases.py) --
# there is no separate "receiving back" position in this project's five-position vocabulary.
SCHEME_SPLIT_POSITIONS: frozenset[str] = frozenset({"WR", "TE", "RB"})

_NO_GSIS_ID_REASON = (
    "no nflverse gsis_id resolved for this player (identity.nflverse_gsis_id is None -- a "
    "local-UUID canonical identity, per ADR-0013 decision 1) -- RoleShare/snap-share/red-zone "
    "data is only joinable through nflverse's own gsis_id space, so none of it can be looked up "
    "for this player regardless of what the caller supplied."
)

_NO_SALARY_REASON = (
    "no DK salary found for this player -- no PlayerProjection was supplied/matched for this "
    "canonical_id, or the matched PlayerProjection's own salary field is unresolved (no "
    "identity.sources['draftkings'] match, per projection/blend.py's own _salary_for)."
)

_MATCHUP_GRADE_NOTE = (
    "own_unit_grade/opponent_unit_grade are resolved via MatchupContext (matchup.context."
    "resolve_own_opponent_unit_grades, ADR-0022 Round B, now implemented) -- team-level "
    "snap-weighted aggregates (synthetic TEAM_<abbr>-keyed ResolvedGrade rows, since a unit has "
    "no single PFF player_id of its own), position-dependent: RB=own run-block/opponent "
    "run-defense, QB=own pass-block/opponent pass-rush, WR/TE=opponent's coverage aggregate only "
    "(own_unit_grade is None for WR/TE -- see own_scheme_splits for that player's own man/zone "
    "performance instead). None (with a stated reason) when matchup_facets wasn't supplied, the "
    "opponent isn't known, or the underlying grade facet has no rows for the relevant team this "
    "week -- never a fabricated number. coverage_tendency_faced is separate, already-available "
    "Round A team-level descriptive data (the opponent defense's aggregate man/zone snap-rate)."
)


# --------------------------------------------------------------------------------------------
# Nested sections
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleShareUsage:
    """This player's own `PlayerRoleShare` row (carry-share for RB, target-share for WR/TE),
    plus whether they're the team's gate-identified plurality leader for that role this week.

    `role_share` and `reason` are mutually exclusive-populated: exactly one of
    `role_share is not None` / `reason is not None` holds.
    """

    role_share: PlayerRoleShare | None
    is_team_identified_leader: bool
    reason: str | None = None


@dataclass(frozen=True)
class SnapShareUsage:
    """This player's `PlayerSnapShare` (offense/defense/special-teams trailing + last-week
    percentages), straight from `ingestion/snap_share.py` -- no recomputation here."""

    snap_share: PlayerSnapShare | None
    reason: str | None = None


@dataclass(frozen=True)
class RedZoneUsage:
    """This player's trailing red-zone usage, split by role since a pass-catching back can carry
    real volume in both (`aggregate_player_trailing_red_zone` produces one row per role, not one
    row per player) -- `carries_trailing`/`carry_share_trailing` come from that player's `"RB"`-
    role row (if any), `targets_trailing`/`target_share_trailing` from their `"WR"`-role row (if
    any). A player can legitimately have one, both, or neither populated; `reason` is set only
    when all four are `None` (no trailing red-zone row of either role at all).
    """

    carries_trailing: int | None
    carry_share_trailing: float | None
    targets_trailing: int | None
    target_share_trailing: float | None
    reason: str | None = None


@dataclass(frozen=True)
class PlayerDetailUsage:
    role_share: RoleShareUsage
    snap_share: SnapShareUsage
    red_zone: RedZoneUsage


@dataclass(frozen=True)
class OwnSchemeSplits:
    """PFF `receiving/scheme` man-vs-zone performance split for this player (ADR-0022's
    previously-unfilled "does Player X perform better against man or zone" gap). `grades` carries
    the facet's full field set as-is (`man_targets`, `zone_targets`, `man_yprr`, `zone_yprr`,
    `man_grades_pass_route`, `zone_grades_pass_route`, ... -- not narrowed here, per ADR-0022).

    `applicable=False` (position outside `SCHEME_SPLIT_POSITIONS`) is always distinguishable from
    `applicable=True, grades={}` (position-eligible but no data resolvable/found) via `reason`.
    """

    applicable: bool
    grades: dict[str, float]
    population: str | None  # ResolvedGrade.population when grades is non-empty, else None
    pff_native_id: str | None  # for traceability/QA, even when grades ends up empty
    reason: str | None = None


@dataclass(frozen=True)
class MatchupThisWeek:
    """See module docstring's "Round B" section. `own_unit_grade`/`opponent_unit_grade` are
    resolved from `MatchupContext`'s own team-level grade aggregates
    (`matchup.context.resolve_own_opponent_unit_grades`) when the composer is given
    `matchup_facets`; `unit_grade_reason` is populated whenever either comes back `None` (no
    `matchup_facets` supplied, no opponent known this week, or the underlying facet has no rows
    for the relevant team) -- kept separate from `coverage_tendency_reason` since the two sections
    can independently succeed or fail. `coverage_tendency_faced` is real Round A data (opponent's
    team-level man/zone coverage-snap rollup) when supplied."""

    opponent_team: str | None
    own_unit_grade: ResolvedGrade | None
    opponent_unit_grade: ResolvedGrade | None
    coverage_tendency_faced: TeamCoverageTendency | None
    coverage_tendency_reason: str | None
    # New (this round, ADR-0022 Round B wiring) -- defaulted to None so existing call sites that
    # construct MatchupThisWeek directly (e.g. dashboard/test fixtures built before own_unit_grade/
    # opponent_unit_grade were ever populated) don't need updating just to add this field.
    unit_grade_reason: str | None = None
    matchup_grade_note: str = _MATCHUP_GRADE_NOTE


@dataclass(frozen=True)
class PlayerDetailRecord:
    """ADR-0022's player-detail read-model: grain is (season, week, canonical_player_id),
    "as of week W" built from completed weeks 1..W-1 only, matching `RoleShareResult`/
    `PffFacetGrades`'s existing trailing-population discipline exactly (this record does not
    invent its own trailing-window policy).

    `salary` is DraftKings salary, sourced from an already-built `PlayerProjection`
    (`projection/blend.py`) via the shared `canonical_id` join key -- not recomputed or re-fetched
    here, mirroring every other section's "read-model over an existing fact, not a new source of
    truth" discipline. `salary_reason` is populated exactly when `salary is None` (no matching
    `PlayerProjection` supplied, or one was found but its own `salary` is unresolved).
    """

    season: int
    week: int
    identity: PlayerIdentity
    team: str
    position: str
    salary: int | None
    salary_reason: str | None
    opponent_team_this_week: str | None

    usage: PlayerDetailUsage
    own_scheme_splits: OwnSchemeSplits
    matchup_this_week: MatchupThisWeek

    game_environment: GameEnvironmentScore | None
    game_environment_reason: str | None

    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------
# gsis_id <-> PFF native player_id join (see module docstring, join problem item 2)
# --------------------------------------------------------------------------------------------


def build_gsis_to_pff_id_map(crosswalk: pd.DataFrame) -> dict[str, str]:
    """`gsis_id -> pff_id` over the nflverse ID crosswalk (`normalization/crosswalk.py`'s already-
    cached frame) -- the same `pff_id` column ADR-0013's matcher already trusts as a directly-
    probeable vendor ID, just resolved in the opposite direction (by `gsis_id` instead of by
    `pff_id`). Mirrors `snap_share.py`'s `join_snap_counts_to_gsis_id` in spirit (a same-family
    nflverse-crosswalk lookup, not a new ID scheme) but as a plain dict since this module only
    ever needs a single-player point lookup, not a DataFrame join.

    Rows missing either column, or a `gsis_id` that appears more than once (shouldn't happen for
    a per-player crosswalk row, but guarded the same defensive way `snap_share.py`'s own lookup
    is via `drop_duplicates`), are excluded -- first-seen wins, consistent with this project's
    general "first-seen wins" posture (`pff.py`'s `fetch_pff_players` dedupe) rather than raising.
    """
    lookup = crosswalk[["gsis_id", "pff_id"]].dropna(subset=["gsis_id", "pff_id"]).drop_duplicates(
        subset=["gsis_id"]
    )
    return dict(zip(lookup["gsis_id"], lookup["pff_id"]))


def _pff_native_id_for_identity(identity: PlayerIdentity, gsis_to_pff_id: dict[str, str] | None) -> str | None:
    """Prefer the full ADR-0013 matcher's own resolution (`identity.sources["pff"]`) when it
    exists and is a real match (not `UNRESOLVED`/`AMBIGUOUS`) -- that's the definitive,
    name-verified join this pipeline already trusts elsewhere. Fall back to the crosswalk's own
    `gsis_id -> pff_id` reverse lookup only when the caller hasn't run (or didn't pass) the full
    per-source matcher for this player -- e.g. a composer working directly off nflverse-space
    identities without having re-run the four-vendor-source matcher.
    """
    pff_match = identity.sources.get("pff")
    if pff_match is not None and pff_match.method not in (MatchMethod.UNRESOLVED, MatchMethod.AMBIGUOUS):
        if pff_match.native_id is not None:
            return pff_match.native_id
    if gsis_to_pff_id is not None and identity.nflverse_gsis_id is not None:
        return gsis_to_pff_id.get(identity.nflverse_gsis_id)
    return None


# --------------------------------------------------------------------------------------------
# Per-section composers
# --------------------------------------------------------------------------------------------


def _role_share_usage(
    gsis_id: str | None,
    team: str,
    position: str,
    role_share_results: dict[tuple[str, str], RoleShareResult] | None,
) -> RoleShareUsage:
    if gsis_id is None:
        return RoleShareUsage(role_share=None, is_team_identified_leader=False, reason=_NO_GSIS_ID_REASON)

    if position == "RB":
        role = ROLE_RB
    elif position in ("WR", "TE"):
        role = ROLE_WR
    else:
        return RoleShareUsage(
            role_share=None,
            is_team_identified_leader=False,
            reason=(
                f"RoleShare covers RB carry-share and WR/TE target-share only (ADR-0019) -- not "
                f"applicable for position {position!r}."
            ),
        )

    result = (role_share_results or {}).get((team, role))
    if result is None:
        return RoleShareUsage(
            role_share=None,
            is_team_identified_leader=False,
            reason=f"no {role!r}-role RoleShareResult supplied for {team} this week.",
        )

    for candidate in result.candidates:
        if candidate.player_id == gsis_id:
            is_leader = result.identified is not None and result.identified.player_id == gsis_id
            return RoleShareUsage(role_share=candidate, is_team_identified_leader=is_leader, reason=None)

    return RoleShareUsage(
        role_share=None,
        is_team_identified_leader=False,
        reason=(
            f"no trailing {role!r}-role volume recorded for this player on {team} through the "
            f"last completed week ({result.gate_reason!r} describes the team's overall gate "
            "outcome, not this specific player's own absence from the candidate list)."
        ),
    )


def _snap_share_usage(
    gsis_id: str | None, snap_shares_by_player: dict[str, PlayerSnapShare] | None
) -> SnapShareUsage:
    if gsis_id is None:
        return SnapShareUsage(snap_share=None, reason=_NO_GSIS_ID_REASON)
    snap_share = (snap_shares_by_player or {}).get(gsis_id)
    if snap_share is None:
        return SnapShareUsage(
            snap_share=None,
            reason=(
                "no snap-share data for this player this week (unresolved pfr_player_id -> "
                "gsis_id crosswalk join, a bye week, no trailing snap-count row yet, or simply "
                "not supplied to the composer)."
            ),
        )
    return SnapShareUsage(snap_share=snap_share, reason=None)


def _red_zone_usage(gsis_id: str | None, red_zone_trailing: pd.DataFrame | None) -> RedZoneUsage:
    if gsis_id is None:
        return RedZoneUsage(
            carries_trailing=None,
            carry_share_trailing=None,
            targets_trailing=None,
            target_share_trailing=None,
            reason=_NO_GSIS_ID_REASON,
        )
    if red_zone_trailing is None or red_zone_trailing.empty:
        return RedZoneUsage(
            carries_trailing=None,
            carry_share_trailing=None,
            targets_trailing=None,
            target_share_trailing=None,
            reason="no red-zone trailing data supplied to the composer for this week.",
        )

    rows = red_zone_trailing[red_zone_trailing["player_id"] == gsis_id]
    if rows.empty:
        return RedZoneUsage(
            carries_trailing=None,
            carry_share_trailing=None,
            targets_trailing=None,
            target_share_trailing=None,
            reason=(
                "no trailing red-zone volume recorded for this player through the last "
                "completed week (no red-zone carries or targets, or a bye week so far)."
            ),
        )

    rb_row = rows[rows["role"] == ROLE_RB]
    wr_row = rows[rows["role"] == ROLE_WR]
    carries = int(rb_row.iloc[0]["rz_trailing_volume"]) if not rb_row.empty else None
    carry_share = float(rb_row.iloc[0]["rz_trailing_share"]) if not rb_row.empty else None
    targets = int(wr_row.iloc[0]["rz_trailing_volume"]) if not wr_row.empty else None
    target_share = float(wr_row.iloc[0]["rz_trailing_share"]) if not wr_row.empty else None
    return RedZoneUsage(
        carries_trailing=carries,
        carry_share_trailing=carry_share,
        targets_trailing=targets,
        target_share_trailing=target_share,
        reason=None,
    )


def _own_scheme_splits(
    identity: PlayerIdentity,
    position: str,
    receiving_scheme_grades: PffFacetGrades | None,
    gsis_to_pff_id: dict[str, str] | None,
) -> OwnSchemeSplits:
    if position not in SCHEME_SPLIT_POSITIONS:
        return OwnSchemeSplits(
            applicable=False,
            grades={},
            population=None,
            pff_native_id=None,
            reason=(
                "own_scheme_splits (PFF receiving/scheme man-vs-zone data) is scoped to "
                f"WR/TE/receiving-back positions only (ADR-0022) -- not applicable for position "
                f"{position!r}."
            ),
        )

    pff_native_id = _pff_native_id_for_identity(identity, gsis_to_pff_id)
    if pff_native_id is None:
        return OwnSchemeSplits(
            applicable=True,
            grades={},
            population=None,
            pff_native_id=None,
            reason=(
                "no PFF player_id resolved for this player -- identity.sources['pff'] is unset/"
                "unresolved and no gsis_id -> pff_id crosswalk fallback match was found."
            ),
        )

    if receiving_scheme_grades is None:
        return OwnSchemeSplits(
            applicable=True,
            grades={},
            population=None,
            pff_native_id=pff_native_id,
            reason="no receiving/scheme PffFacetGrades pull supplied to the composer this week.",
        )

    resolved = resolve_grade(pff_native_id, position, receiving_scheme_grades)
    if resolved.population == "unmatched" or not resolved.grades:
        return OwnSchemeSplits(
            applicable=True,
            grades={},
            population=None,
            pff_native_id=pff_native_id,
            reason=(
                f"no man/zone scheme-split row found for PFF player_id {pff_native_id!r} in the "
                "receiving/scheme facet pull (population='unmatched')."
            ),
        )

    return OwnSchemeSplits(
        applicable=True,
        grades=resolved.grades,
        population=resolved.population,
        pff_native_id=pff_native_id,
        reason=None,
    )


def _unit_grades(
    team: str,
    position: str,
    opponent_team_this_week: str | None,
    matchup_facets: MatchupFacetInputs | None,
) -> tuple[ResolvedGrade | None, ResolvedGrade | None, str | None]:
    """`own_unit_grade`/`opponent_unit_grade` -- see `MatchupThisWeek`'s docstring. `None` for all
    three (including `reason`) exactly when `matchup_facets` wasn't supplied at all -- a distinct
    "this composer call didn't ask for it" case from `resolve_own_opponent_unit_grades`'s own
    "asked, but couldn't compute" reasons (no opponent, no facet rows for the team).
    """
    if matchup_facets is None:
        return None, None, "no MatchupContext grade facets (matchup_facets) supplied to the composer."
    return resolve_own_opponent_unit_grades(position, team, opponent_team_this_week, matchup_facets)


def _matchup_this_week(
    team: str,
    position: str,
    opponent_team_this_week: str | None,
    team_coverage_tendency: dict[str, TeamCoverageTendency] | None,
    matchup_facets: MatchupFacetInputs | None,
) -> MatchupThisWeek:
    own_unit_grade, opponent_unit_grade, unit_grade_reason = _unit_grades(
        team, position, opponent_team_this_week, matchup_facets
    )

    if opponent_team_this_week is None:
        return MatchupThisWeek(
            opponent_team=None,
            own_unit_grade=own_unit_grade,
            opponent_unit_grade=opponent_unit_grade,
            unit_grade_reason=unit_grade_reason,
            coverage_tendency_faced=None,
            coverage_tendency_reason=(
                "no opponent identified for this week (a bye week, or opponent not supplied to "
                "the composer)."
            ),
        )

    tendency = (team_coverage_tendency or {}).get(opponent_team_this_week)
    if tendency is None:
        return MatchupThisWeek(
            opponent_team=opponent_team_this_week,
            own_unit_grade=own_unit_grade,
            opponent_unit_grade=opponent_unit_grade,
            unit_grade_reason=unit_grade_reason,
            coverage_tendency_faced=None,
            coverage_tendency_reason=(
                f"no TeamCoverageTendency computed for {opponent_team_this_week}'s defense this "
                "week (insufficient defense/coverage_scheme snap-count data, or not supplied to "
                "the composer)."
            ),
        )

    return MatchupThisWeek(
        opponent_team=opponent_team_this_week,
        own_unit_grade=own_unit_grade,
        opponent_unit_grade=opponent_unit_grade,
        unit_grade_reason=unit_grade_reason,
        coverage_tendency_faced=tendency,
        coverage_tendency_reason=None,
    )


def _game_environment(
    team: str, game_environment_by_team: dict[str, GameEnvironmentScore] | None
) -> tuple[GameEnvironmentScore | None, str | None]:
    ges = (game_environment_by_team or {}).get(team)
    if ges is None:
        return None, f"no GameEnvironmentScore supplied for {team} this week."
    # ges may itself have is_available=False -- that is a real, already-self-documented outcome
    # (GameEnvironmentScore.is_available/.notes), not a second "missing" case this module should
    # re-explain; the object is passed through as-is, with no separate reason attached.
    return ges, None


def _salary(
    identity: PlayerIdentity, projections_by_canonical_id: dict[str, PlayerProjection] | None
) -> tuple[int | None, str | None]:
    """DK salary, joined via the shared `canonical_id` key against an already-built
    `PlayerProjection` pool (`projection/blend.py`'s `build_projection_pool`) -- not recomputed or
    re-parsed from the raw DK payload here. Distinguishes "no PlayerProjection supplied/matched
    for this canonical_id" from "a PlayerProjection was found but its own `salary` is `None`"
    (that vendor row's own `identity.sources['draftkings']` never resolved) only via the shared
    `_NO_SALARY_REASON` text -- both are "no usable DK salary for this player," and neither is
    actionable differently by a caller of this module, unlike the gsis_id/PFF-id join misses above
    which really do call for different follow-ups.
    """
    projection = (projections_by_canonical_id or {}).get(identity.canonical_id)
    if projection is None or projection.salary is None:
        return None, _NO_SALARY_REASON
    return projection.salary, None


# --------------------------------------------------------------------------------------------
# Top-level composer
# --------------------------------------------------------------------------------------------


def build_player_detail_record(
    identity: PlayerIdentity,
    season: int,
    week: int,
    team: str,
    position: str,
    opponent_team_this_week: str | None,
    *,
    role_share_results: dict[tuple[str, str], RoleShareResult] | None = None,
    snap_shares_by_player: dict[str, PlayerSnapShare] | None = None,
    red_zone_trailing: pd.DataFrame | None = None,
    receiving_scheme_grades: PffFacetGrades | None = None,
    gsis_to_pff_id: dict[str, str] | None = None,
    team_coverage_tendency: dict[str, TeamCoverageTendency] | None = None,
    game_environment_by_team: dict[str, GameEnvironmentScore] | None = None,
    projections_by_canonical_id: dict[str, PlayerProjection] | None = None,
    matchup_facets: MatchupFacetInputs | None = None,
) -> PlayerDetailRecord:
    """Join one player's ADR-0022 `PlayerDetailRecord` for one (season, week) out of already-
    computed, week-scoped lookup collections -- see module docstring for the exact join keys used
    for each and the "shared per-player-week lookup" shape this signature is built around.

    `team`/`position`/`opponent_team_this_week` are passed explicitly rather than read off
    `identity` (per ADR-0022's schema sketch, which lists them as separate top-level fields
    alongside `identity` itself) -- `identity.team`/`identity.position` reflect that player's
    Normalization-stage record, which a caller may reasonably want to keep distinct from "this
    week's" roster team/position/opponent (e.g. a just-traded player, or a position group change).

    No fetching happens here -- every lookup collection is optional (defaults to `None`, treated
    as "caller has nothing for this source"), so a caller can build a partial record over
    whatever subset of Round A's modules it has already run this week.

    `projections_by_canonical_id` is the one lookup sourced from Stage 3 (`projection/blend.py`)
    rather than Round A -- an already-built `PlayerProjection` pool keyed by `canonical_id`
    (`{p.canonical_id: p for p in build_projection_pool(...)}`), joined here purely for its
    `salary` field via the same `identity.canonical_id` every other stage of this pipeline already
    treats as the canonical join key. Optional, same "caller has nothing for this source" shape as
    every other lookup above.

    `matchup_facets` (new, ADR-0022 Round B) is Stage 4's already-fetched `MatchupContext` grade
    facets (`matchup.context.MatchupFacetInputs` -- the same `run_blocking`/`run_defense`/
    `pass_blocking`/`pass_rush`/`coverage_scheme`/`receiving_scheme` pulls `matchup/context.py`'s
    `build_matchup_context_pool` consumes for the actual multiplier computation), used here only
    to populate `matchup_this_week.own_unit_grade`/`opponent_unit_grade` -- a second, independent
    read of the same team-level aggregates, not a re-fetch and not the multiplier itself.
    """
    gsis_id = identity.nflverse_gsis_id

    role_share_usage = _role_share_usage(gsis_id, team, position, role_share_results)
    snap_share_usage = _snap_share_usage(gsis_id, snap_shares_by_player)
    red_zone_usage = _red_zone_usage(gsis_id, red_zone_trailing)
    own_scheme_splits = _own_scheme_splits(identity, position, receiving_scheme_grades, gsis_to_pff_id)
    matchup_this_week = _matchup_this_week(
        team, position, opponent_team_this_week, team_coverage_tendency, matchup_facets
    )
    game_environment, game_environment_reason = _game_environment(team, game_environment_by_team)
    salary, salary_reason = _salary(identity, projections_by_canonical_id)

    notes: list[str] = []
    if gsis_id is None:
        notes.append(
            "identity.nflverse_gsis_id is None for this player -- role_share/snap_share/red_zone "
            "are all unresolvable regardless of what lookups were supplied (see each section's "
            "own reason)."
        )
    if matchup_facets is None:
        notes.append(
            "matchup_facets was not supplied to this composer call -- "
            "matchup_this_week.own_unit_grade/opponent_unit_grade are None (see "
            "matchup_this_week.matchup_grade_note / unit_grade_reason)."
        )

    return PlayerDetailRecord(
        season=season,
        week=week,
        identity=identity,
        team=team,
        position=position,
        salary=salary,
        salary_reason=salary_reason,
        opponent_team_this_week=opponent_team_this_week,
        usage=PlayerDetailUsage(role_share=role_share_usage, snap_share=snap_share_usage, red_zone=red_zone_usage),
        own_scheme_splits=own_scheme_splits,
        matchup_this_week=matchup_this_week,
        game_environment=game_environment,
        game_environment_reason=game_environment_reason,
        notes=notes,
    )
