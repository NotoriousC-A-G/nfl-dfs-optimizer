"""Stage 9: lineup rationale (PRD Section 5 step 9; Section 8: "A short rationale per lineup,
tying it back to the StackProfile thesis that drove it").

## The join-key gap between `Lineup` and `StackProfile`, flagged for the Architect

`Lineup.core_stack_team` (`optimizer/lineup.py`) is just a plain team abbreviation -- whichever
team the ILP's selected QB+pass-catcher combination belongs to. `StackProfile`
(`correlation/stack_profile.py`) is keyed by an entire *game* (`home_team`, `away_team`, `season`,
`week`), and per that module's own documented "Anchor-team choice" design note, only **one**
directional thesis is ever built per game: `home_team` is always `pivot_to`'s anchor,
`away_team` only ever supplies `bring_back_candidates`. There is no shared join key between the
two objects at all -- no game id, no directional flag, nothing -- so connecting a `Lineup` to
"the right `StackProfile`" here means scanning every available `StackProfile` for the current
week and matching `Lineup.core_stack_team` against its `home_team`/`away_team` by plain string
equality (`_find_stack_profile_for_team` below).

**This has a real, unavoidable consequence, not papered over:** a lineup whose core stack team
happens to be a game's `away_team` has **no home-anchored `pivot_to` text of its own** -- the
`StackProfile` that exists for that game was built with the *other* team as the affirmative-
thesis anchor (`correlation/stack_profile.py` never builds the mirror-image direction). This
module does not fabricate an equivalent-looking thesis to hide that gap -- constructing one would
mean re-running `build_pivot_to` with the teams' roles swapped, which is
`correlation/stack_profile.py`'s job, not this output-stage module's, and out of this round's
"pure consumption" scope. Instead, the rationale states plainly that no anchor-side thesis exists
for that team and surfaces whatever *is* independently known about the game
(`single_team_viability_away`, `game_stack_viability`, `bring_back_status`) rather than either
inventing text or silently reusing the wrong team's thesis.

**Recommended fix for the Architect (not decided here):** either (a) have `StackProfile` carry
both directions (`pivot_to_home`/`pivot_to_away`, mirroring how `single_team_viability_home`/
`_away` are already split), or (b) have callers construct both directional `StackProfile`s per
game up front, so an output-stage join like this one always finds an anchored thesis for
whichever team a lineup's core stack actually is. Either fix belongs in `correlation/
stack_profile.py` or its caller, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.correlation.stack_profile import StackProfile
from nfl_dfs.optimizer.lineup import Lineup


@dataclass(frozen=True)
class LineupRationale:
    """One lineup's rationale text, plus enough structure for a caller to trace which (if any)
    `StackProfile` it was built from.
    """

    lineup_index: int
    core_stack_team: str
    text: str
    # The connected StackProfile's game_id (correlation/stack_profile.py's own directional
    # "away@home-season-wkweek" identifier), or None if no StackProfile could be connected at all
    # (see module docstring / _find_stack_profile_for_team).
    stack_profile_game_id: str | None
    # True only when the connected StackProfile was anchored on this lineup's own core_stack_team
    # (i.e. a real pivot_to exists for this team specifically) -- False for the "away-side, no
    # anchored thesis" case and for the "no StackProfile at all" case (where it's meaningless).
    thesis_is_anchored: bool


def _core_stack_display(lineup: Lineup) -> str:
    """`"QB + WR/TE names"`, QB first -- same rendering convention as `output/exposure.py`'s
    `_stack_display`, duplicated rather than imported since it's a two-line, module-local
    formatting detail, not shared logic worth coupling the two modules over.
    """
    by_id = {p.canonical_id: p.display_name for p in lineup.players}
    qb = lineup.slots["QB"]
    catcher_names = sorted(
        by_id[cid] for cid in lineup.core_stack if cid in by_id and cid != qb.canonical_id
    )
    return " + ".join([qb.display_name, *catcher_names])


def _find_stack_profile_for_team(
    team: str, stack_profiles: list[StackProfile]
) -> tuple[StackProfile, bool] | None:
    """Finds the `StackProfile` (if any) for the game `team` played in this week, and whether
    `team` is that profile's anchor (`home_team`, real `pivot_to` available) or not (`away_team`
    only -- see module docstring).

    Home-anchor matches are preferred: if `team` is somehow both a home_team in one profile and
    an away_team in another (shouldn't happen for one team in one real week's slate, but never
    assumed impossible), the anchored match is returned. Returns `None` if `team` appears in
    neither role in any supplied `StackProfile` -- e.g. `stack_profiles` doesn't cover that game
    this week at all.
    """
    for profile in stack_profiles:
        if profile.home_team == team:
            return profile, True
    for profile in stack_profiles:
        if profile.away_team == team:
            return profile, False
    return None


def build_lineup_rationale(
    lineup: Lineup, lineup_index: int, stack_profiles: list[StackProfile]
) -> LineupRationale:
    """Build one lineup's rationale. `lineup_index` is a caller-supplied 1-based label (e.g.
    position in the generated set) used only for the text's own "Lineup N:" prefix -- purely
    cosmetic, not a join key.

    Three real outcomes, each stated plainly rather than blurred together:
    1. A home-anchored `StackProfile` exists for `lineup.core_stack_team` -- reuse its real
       `pivot_to` text verbatim (or, if `pivot_to` is itself `None` because that team's
       `GameEnvironmentScore` was unavailable, say so).
    2. `lineup.core_stack_team` only appears as an `away_team` in the available `StackProfile`s --
       no anchored thesis exists for it; state that plainly and surface the game's other computed
       numbers instead of fabricating an equivalent thesis (see module docstring).
    3. No `StackProfile` at all covers `lineup.core_stack_team` this week -- state that plainly;
       this lineup's stack was chosen by the optimizer's blended-projection objective alone, with
       no correlation/game-environment thesis available to cite.
    """
    stack_display = _core_stack_display(lineup)
    header = (
        f"Lineup {lineup_index}: {stack_display} ({lineup.core_stack_team} core stack, "
        f"${lineup.total_salary:,} salary, {lineup.total_projected_points:.1f} projected pts)."
    )

    match = _find_stack_profile_for_team(lineup.core_stack_team, stack_profiles)

    if match is None:
        body = (
            f"No StackProfile was computed for {lineup.core_stack_team} this week -- none of the "
            "supplied StackProfiles cover a game involving this team. This stack was chosen by "
            "the optimizer's blended-projection objective alone; no correlation/game-environment "
            "thesis is available to cite here. Not fabricated."
        )
        return LineupRationale(
            lineup_index=lineup_index,
            core_stack_team=lineup.core_stack_team,
            text=f"{header} {body}",
            stack_profile_game_id=None,
            thesis_is_anchored=False,
        )

    profile, is_anchor = match

    if is_anchor:
        if profile.pivot_to is not None:
            body = profile.pivot_to
        else:
            body = (
                f"{lineup.core_stack_team}'s GameEnvironmentScore was unavailable this week "
                "(ADR-0017's exclusion policy) -- StackProfile could not compute an affirmative "
                "thesis for this team. This stack was chosen by blended projection alone."
            )
    else:
        body = (
            f"No anchor-side StackProfile thesis exists for {lineup.core_stack_team} directly -- "
            f"this game's StackProfile was built with {profile.home_team} as the affirmative-"
            f"thesis anchor (correlation/stack_profile.py's single-direction-per-game design; see "
            "output/rationale.py's module docstring). What IS known about this game: "
            f"single_team_viability({lineup.core_stack_team}) = "
            f"{profile.single_team_viability_away!r}, game_stack_viability = "
            f"{profile.game_stack_viability!r} (bring_back_status={profile.bring_back_status!r}). "
            "No thesis beyond these computed numbers is asserted."
        )

    return LineupRationale(
        lineup_index=lineup_index,
        core_stack_team=lineup.core_stack_team,
        text=f"{header} {body}",
        stack_profile_game_id=profile.game_id,
        thesis_is_anchored=is_anchor,
    )


def build_lineup_rationales(
    lineups: list[Lineup], stack_profiles: list[StackProfile]
) -> list[LineupRationale]:
    """`build_lineup_rationale` for a full generated set, 1-indexed in generation order."""
    return [
        build_lineup_rationale(lineup, i, stack_profiles) for i, lineup in enumerate(lineups, start=1)
    ]
