"""`StackProfile` viability scoring and candidate selection (PRD Section 6; Section 5 step 6) --
combination logic over `GameEnvironmentScore`'s already-computed composite scores, the Odds API's
spread data (`ingestion/odds_api.py`'s `GameOdds.home_spread`/`away_spread`), and (new this round)
`ingestion/usage_share.py`'s `RoleShareResult` player-level target-share data. Like
`game_environment/score.py`, this module does not fetch or recompute anything upstream -- it
consumes already-computed objects from those three modules and produces the `StackProfile`
PRD Section 6 specifies.

## This round -- closes the `primary_stack_candidates`/`bring_back_candidates`/`pivot_to` gap

The prior round covered only the two viability formulas (ADR-0004/ADR-0010) that had inputs ready,
leaving `primary_stack_candidates`/`bring_back_candidates`/`pivot_to` explicitly `None` pending
target-share data. ADR-0019 built exactly that data (`RoleShareResult`, `ingestion/usage_share.py`)
and its own text says plainly: "This directly closes `StackProfile`'s already-documented gap ...
`RoleShare` becomes the ranking key `StackProfile` already specifies." This round wires that up:

- `primary_stack_candidates` / `bring_back_candidates`: top 1-2 `PlayerRoleShare` entries (already
  sorted descending by `role_share_blended`) from the relevant team's `"WR"`-role `RoleShareResult`
  -- see `select_stack_candidates`.
- `pivot_to`: a mechanical, templated affirmative-thesis string naming the actual computed inputs
  -- see `build_pivot_to`.

**`MatchupContext` favorability is still NOT implemented anywhere in this pipeline** (per the
walking-skeleton sequencing this task is deliberately not getting ahead of). PRD Section 6 ranks
primary/bring-back candidates by "target share and `MatchupContext` favorability" -- this round
ranks by target share (`role_share_blended`) alone, which is a real, acknowledged partial
implementation of that ranking criterion, not a silent drop of half the spec. See
`select_stack_candidates`'s docstring for the explicit follow-up note.

**Anchor-team choice, flagged for Architect confirmation, not decided unilaterally:** PRD Section 6
describes `primary_stack_candidates`/`bring_back_candidates`/`pivot_to` as singular fields (matching
the prior round's stub exactly), not `_home`/`_away` pairs the way `single_team_viability_home`/
`_away` are split. Because a `StackProfile` bundles both teams of a game in one object, *some* team
has to be picked as the stack's own "anchor" (QB) side for these three fields to mean anything.
This module picks `home_team` as that anchor -- `away_team` supplies `bring_back_candidates`. This
is a reasonable, but real, single-direction design choice: a game could just as validly support a
mirror-image `StackProfile` with `away_team` as the anchor and `home_team` as the bring-back side,
and nothing here builds that second direction. Whether `StackProfile` should instead carry both
directions in one object (`primary_stack_candidates_home`/`_away`, etc., mirroring the viability
fields) or whether callers are expected to construct two directional `StackProfile`s per game is a
real open question, not resolved here -- see this round's report to the Architect.

## Spread-dampener band table -- ADR-0004, tail extended by ADR-0010

ADR-0004 originally specified a 4-band table topping out at ">10 -> 0.40". The Fantasy Football
Expert's round-2 review flagged that tail as too coarse (a 10.5-point spread and a 24-point
blowout were dampened identically), so ADR-0010 replaced it with the 5-band table below -- this is
the table PRD Section 6's `StackProfile` section documents today, and the one implemented here:

    |spread| <=  3   -> 1.00   # pick'em / one-score game, full bring-back support
     3  < |spread| <=  7   -> 0.85   # standard one-score-to-touchdown game
     7  < |spread| <= 10   -> 0.65   # meaningful lean, bring-back thesis weakens
    10  < |spread| <= 14   -> 0.40   # double-digit spread, real blowout risk
         |spread| > 14   -> 0.25   # extreme blowout -- garbage-time production is unreliable,
                                    # not impossible, so this floors at 0.25, not 0.

This same table is reused by `DSTProjection`'s `own_team_script_multiplier` (ADR-0009) and
Section 7's RB/DST pairing-penalty scaling (ADR-0010) -- one table, three consumers -- but this
module only implements the `StackProfile` consumer; the other two are out of scope here.

## ADR-0017's exclusion policy -- the one real judgment call in this module

ADR-0017 (Decision 2, plus its "explicit downstream consequence" note) settled that a team-week
with `GameEnvironmentScore.is_available=False` must be treated as **excluded** from that week's
stack-thesis consideration -- not zeroed out, not imputed, not silently dropped from a `min()`
comparison in a way that could misread as "this team cleared the bar." Concretely for this module:

- `single_team_viability` returns `None` for an unavailable team -- there is no basis for a
  single-team stack thesis without a computable score.
- `game_stack_viability` returns `None` if *either* team is unavailable. This is a stricter
  requirement than a naive `min()` might suggest: `min(GameEnvironmentScore.composite_score, x)`
  when one side is unavailable would need `composite_score` to be some numeric stand-in (e.g. 0,
  or the other team's own score), and *any* such stand-in silently produces a real-looking, if
  low, viability number that misreads as "we evaluated this bring-back and it's an available but
  weak one" rather than "we could not evaluate this bring-back at all." Both this module's
  functions check `is_available` explicitly, up front, before touching `composite_score`, rather
  than passing `composite_score` (which is legitimately `None` on an unavailable team) through
  `min()` and letting a `TypeError` or an accidental `None`-as-zero comparison decide the outcome
  implicitly.

## ADR-0021 -- `bring_back_candidates` gated on `game_stack_viability`, a fourth explicit state

This round wired `select_stack_candidates`/`build_pivot_to`/`build_stack_profile` together
unconditionally: `bring_back_candidates` was populated from the opposing roster's WR-role
candidates whenever they existed, with no read of `game_stack_viability` at all -- so a 20+ point
blowout with a rock-solid opposing WR1 still returned two named bring-back candidates, even though
the game environment itself doesn't support a bring-back thesis. ADR-0021 closes that gap:

- `BRING_BACK_VIABILITY_FLOOR = 20.0` (0-100 `GameEnvironmentScore` scale). `bring_back_candidates`
  is only selected from `away_wr_role_share` when BOTH `GameEnvironmentScore`s are available AND
  `game_stack_viability >= BRING_BACK_VIABILITY_FLOOR`; below that floor it is `None`, regardless
  of what the opposing roster's own WR target-share data would otherwise have produced.
- `bring_back_status` (new field on `StackProfile`) makes this new `None` case
  (`"game_stack_not_viable"`) explicitly distinguishable from the pre-existing
  `"environment_unavailable"` `None` case (ADR-0017) and from the two "environment supports the
  thesis" cases (`"populated"` / `"no_confident_candidate"`) -- see `StackProfile`'s own docstring
  for the full four-state table and `BringBackStatus`.
- `build_pivot_to`'s bring-back clause tracks the same floor, so `pivot_to` never affirmatively
  asserts a bring-back pairing that `bring_back_candidates`/`bring_back_status` report as
  unsupported.
- `game_stack_viability` itself is unchanged by this gate -- it keeps returning its real computed
  value (all the way down to the 0.25-dampener floor) whenever both `GameEnvironmentScore`s are
  available; only the candidate/thesis fields built from it are gated (ADR-0021 Decision 5).
- `primary_stack_candidates` and `single_team_viability_home`/`_away` are explicitly NOT affected
  by this gate -- see ADR-0021 Decision 3's closing note.

**Behavior change for existing consumers (ADR-0021 Consequences):** any caller that previously
treated `bring_back_candidates is None` as meaning only "no `GameEnvironmentScore` data" must be
updated to branch on `bring_back_status` instead -- `None` now has two distinct causes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from nfl_dfs.game_environment.score import GameEnvironmentScore
from nfl_dfs.ingestion.usage_share import (
    PRIOR_UNCONTESTED,
    ROLE_RB,
    ROLE_WR,
    PlayerRoleShare,
    RoleShareResult,
)

# --------------------------------------------------------------------------------------------
# Spread-magnitude dampener (ADR-0004, tail extended by ADR-0010 -- see module docstring)
# --------------------------------------------------------------------------------------------

# (upper_bound_inclusive, dampener) pairs, checked in order. The final band's upper bound is
# +inf so every non-negative spread magnitude matches exactly one band. Boundaries are inclusive
# on the *lower* band at each edge (e.g. exactly 3.0 -> 1.00, exactly 7.0 -> 0.85), matching
# ADR-0004's own `<=`/`<` notation.
SPREAD_DAMPENER_BANDS: tuple[tuple[float, float], ...] = (
    (3.0, 1.00),
    (7.0, 0.85),
    (10.0, 0.65),
    (14.0, 0.40),
    (float("inf"), 0.25),
)

# ADR-0021: below this, bring_back_candidates is gated to None ("game_stack_not_viable") even when
# both GameEnvironmentScores are available and the opposing roster has a confident WR candidate --
# see the ADR for the full derivation (a 20%-of-range floor on GameEnvironmentScore's 0-100 scale,
# the same pattern as ADR-0017's injury-impact low-impact floor). game_stack_viability's own return
# contract is unchanged by this constant (ADR-0021 Decision 5) -- it only gates the candidate/thesis
# fields built from that score, not the score itself.
BRING_BACK_VIABILITY_FLOOR: float = 20.0


def spread_dampener(abs_spread: float) -> float:
    """ADR-0004/ADR-0010's banded spread-magnitude dampener, keyed on `|spread|`.

    Raises `ValueError` for a negative input -- callers should pass `abs(spread)`, and a negative
    value getting this far signals a caller bug (e.g. passing a signed spread directly) rather
    than a real dampener band this table has an answer for.
    """
    if abs_spread < 0:
        raise ValueError(f"spread_dampener expects a non-negative magnitude, got {abs_spread!r}")
    for upper_bound, dampener in SPREAD_DAMPENER_BANDS:
        if abs_spread <= upper_bound:
            return dampener
    raise AssertionError("unreachable -- final band's upper bound is +inf")  # pragma: no cover


GameScriptStance = Literal["favorite", "underdog", "pick_em"]


@dataclass(frozen=True)
class GameScriptLean:
    """Favorite/underdog + intensity classification from ONE team's own SIGNED spread (e.g.
    `ingestion/odds_api.py`'s `GameOdds.home_spread`, or `projection/blend.py`'s
    `team_spreads_from_games` -- negative = favorite, this project's established sign convention).

    Deliberately spread-only (NflAgentConstructor plan, foundation signals): no total/shootout
    logic is duplicated here -- `game_stack_viability` (this module) already covers high-total/
    shootout-viability framing from a different angle (bottleneck environment quality), so a
    caller combines the two rather than this function trying to encode both in one number.

    `stance`: `"favorite"` (`team_spread < 0`), `"underdog"` (`team_spread > 0`), or `"pick_em"`
    (exactly `0.0` -- no favorite either way).
    `intensity`: `SPREAD_DAMPENER_BANDS`' own multiplier at `abs(team_spread)` (1.00 pick'em/close
    down to 0.25 extreme blowout) -- reused as a lean-intensity read, not recomputed (ADR-0011
    "reuse before inventing"): a double-digit spread dampens a bring-back thesis (the table's
    original purpose) for the same underlying reason it signals a lopsided, one-side-dominant
    expected game script here.
    """

    stance: GameScriptStance
    abs_spread: float
    intensity: float


def classify_game_script_lean(team_spread: float) -> GameScriptLean:
    """Classify one team's own SIGNED spread into a `GameScriptLean` (favorite/underdog/pick'em +
    intensity). `team_spread` must be that team's OWN signed spread (negative = favorite) -- for
    the opposing team in the same game, pass the negation, not the same value twice.

    Reuses `SPREAD_DAMPENER_BANDS`' exact magnitude cutoffs (ADR-0004/ADR-0010) for `intensity` --
    see `GameScriptLean`'s own docstring for why this is a new PURPOSE for that table (game-script
    lean intensity), not a new set of numeric thresholds.
    """
    if team_spread < 0:
        stance: GameScriptStance = "favorite"
    elif team_spread > 0:
        stance = "underdog"
    else:
        stance = "pick_em"
    abs_spread = abs(team_spread)
    return GameScriptLean(stance=stance, abs_spread=abs_spread, intensity=spread_dampener(abs_spread))


# --------------------------------------------------------------------------------------------
# Viability formulas (PRD Section 6 `StackProfile`; ADR-0004/ADR-0010)
# --------------------------------------------------------------------------------------------


def single_team_viability(ges: GameEnvironmentScore) -> float | None:
    """PRD Section 6: "Single-team stack viability = the team's own `GameEnvironmentScore`
    directly (0-100). No spread dampening." For a single-team (non-game) stack thesis -- QB + own
    pass-catcher, no bring-back -- this is just `ges.composite_score` as-is.

    Returns `None` when `ges.is_available` is `False` (ADR-0017's exclusion policy: an unavailable
    `GameEnvironmentScore` means no basis for a stack thesis this week, not a score of 0 and not
    an imputed league-average).
    """
    if not ges.is_available:
        return None
    return ges.composite_score


def game_stack_viability(
    ges_a: GameEnvironmentScore, ges_b: GameEnvironmentScore, spread: float
) -> float | None:
    """PRD Section 6 / ADR-0004: `min(GameEnvironmentScore(team_A), GameEnvironmentScore(team_B))
    * spread_dampener(|spread|)`. Bottleneck logic across both teams -- a bring-back stack is only
    as strong as the weaker team's environment -- multiplied by the spread-magnitude dampener,
    since a lopsided spread undermines a bring-back thesis specifically (the trailing team's
    passing volume in a blowout skews to checkdowns, not the vertical/intermediate routes a
    bring-back is usually built on).

    Returns `None` if *either* team's `GameEnvironmentScore` is unavailable (ADR-0017). This is
    checked explicitly before either `composite_score` is read, specifically so a `None`
    `composite_score` on the unavailable side never reaches `min()` and gets compared against a
    real number -- see the module docstring for why that would be a meaningfully different (and
    wrong) failure mode than an explicit `None` return.

    `spread` may be signed (e.g. an `ingestion/odds_api.py` `GameOdds.home_spread`) or already an
    absolute value -- this function takes `abs()` itself, so callers don't need to normalize sign
    before calling.
    """
    if not ges_a.is_available or not ges_b.is_available:
        return None
    # Both are_available here, so both composite_score values are real floats, not None -- safe
    # to hand straight to min() and to the assert below documents that invariant explicitly
    # rather than relying on the type checker alone.
    assert ges_a.composite_score is not None and ges_b.composite_score is not None
    bottleneck = min(ges_a.composite_score, ges_b.composite_score)
    return bottleneck * spread_dampener(abs(spread))


# --------------------------------------------------------------------------------------------
# Candidate selection (ADR-0019 -- RoleShare closes StackProfile's prior data gap)
# --------------------------------------------------------------------------------------------

MAX_PRIMARY_CANDIDATES = 2  # PRD Section 6: "QB + top 1-2 pass-catchers"


def select_stack_candidates(
    wr_role_share: RoleShareResult, *, max_candidates: int = MAX_PRIMARY_CANDIDATES
) -> list[PlayerRoleShare]:
    """Top `max_candidates` pass-catchers for a stack thesis, ranked by `RoleShare`
    (`role_share_blended` -- PRD Section 6 / ADR-0019). Used for both `primary_stack_candidates`
    (the anchor team's own roster) and `bring_back_candidates` (PRD Section 6: "selected the same
    way from the opposing roster" -- i.e. this same function, called with the opposing team's own
    `"WR"`-role `RoleShareResult`).

    **Partial ranking, flagged explicitly (PRD Section 6):** the spec ranks primary/bring-back
    candidates "by target share and `MatchupContext` favorability, not raw season totals alone."
    `MatchupContext` (PRD Section 6) has not been implemented anywhere in this pipeline yet, so
    this function ranks by `role_share_blended` (target share) alone -- a real, acknowledged
    partial implementation of the spec, not a silent drop of the `MatchupContext` half. Follow-up,
    once `MatchupContext` exists: combine its per-receiver favorability multiplier with
    `role_share_blended` here rather than ranking on either alone.

    `wr_role_share.candidates` is already sorted descending by `role_share_blended` (see
    `usage_share.py`'s monotonicity note in its own module docstring) and deliberately contains
    *every* player with any trailing target volume, not just the gate-identified WR1 -- so there is
    more than one name available to rank. An empty `candidates` list (no trailing target volume
    recorded for this team -- bye week(s) so far, or week 1 with no prior season to blend from)
    returns `[]` here too: never a fabricated candidate when none is confidently identified.
    """
    if wr_role_share.role != ROLE_WR:
        raise ValueError(
            f"select_stack_candidates expects a {ROLE_WR!r}-role RoleShareResult, got "
            f"{wr_role_share.role!r} -- StackProfile's primary/bring-back candidates are "
            "pass-catchers only (PRD Section 6: 'QB + top 1-2 pass-catchers')."
        )
    return wr_role_share.candidates[:max_candidates]


def select_rb_stack_candidate(rb_role_share: RoleShareResult) -> PlayerRoleShare | None:
    """The one RB worth naming as part of a QB/defense stack or a bring-back (NflAgentConstructor
    plan, foundation signals): a dominant-back thesis, not a committee split.

    Returns `rb_role_share.identified` only when it's gate-cleared (non-`None`, per
    `RoleShareResult`'s own contract -- `identified` is never a guessed name) AND its
    `role_tier` is `"bell_cow"` or `"mid_tier"` -- i.e. NOT `"committee"`. `role_tier` is derived
    from `role_share_blended` independently of the identification gate (`BELLCOW_THRESHOLD`/
    `MIDTIER_THRESHOLD` vs. the gate's own `RB_GATE_MIN_SHARE`/`RB_GATE_MIN_VOLUME`), so a
    gate-passed RB can still land in `"committee"` tier -- that back cleared the bar for
    "identifiable lead back" but not for "dominant enough to anchor a stack thesis," which is
    the distinction this function exists to make. No new numeric thresholds: reuses the tier cuts
    `usage_share.py` already computes (ADR-0011 "reuse before inventing").

    Raises `ValueError` if `rb_role_share.role != ROLE_RB` -- this is an RB-only signal, matching
    `select_stack_candidates`'s own role-guard convention for `ROLE_WR`.
    """
    if rb_role_share.role != ROLE_RB:
        raise ValueError(
            f"select_rb_stack_candidate expects a {ROLE_RB!r}-role RoleShareResult, got "
            f"{rb_role_share.role!r}."
        )
    identified = rb_role_share.identified
    if identified is None:
        return None
    if identified.role_tier not in ("bell_cow", "mid_tier"):
        return None
    return identified


def _candidate_display_name(candidate: PlayerRoleShare) -> str:
    """`player_name` when nflverse supplied one, else fall back to the raw `player_id` rather than
    silently dropping the candidate from a display string."""
    return candidate.player_name or candidate.player_id


def _format_candidate_list(candidates: list[PlayerRoleShare]) -> str:
    names = [_candidate_display_name(c) for c in candidates]
    return " and ".join(names)


def build_pivot_to(
    ges_home: GameEnvironmentScore,
    away_team: str,
    primary_candidates: list[PlayerRoleShare] | None,
    game_stack_score: float | None,
    home_rb_role_share: RoleShareResult | None,
) -> str | None:
    """The affirmative stack thesis (PRD Section 6: "the affirmative thesis for the stack, not
    just an absence of red flags ... mirrors the field added to the MLB `SlateStrategy` dataclass:
    a contrarian build needs a stated reason, not just constraint-based avoidance").

    **Deliberately mechanical, not generative, this round:** a short templated string naming the
    actual computed inputs -- `GameEnvironmentScore`'s composite, the identified primary
    candidate(s), `game_stack_viability` when computable, and (when it fired) the ADR-0020
    `uncontested_signal` for the anchor team's identified lead RB, since "clear bell-cow with no
    real competition" is itself a real, data-grounded thesis in the sense PRD Section 6 describes.
    No elaborate NLG is attempted -- see this round's report for why a more elaborate generation
    approach would be a Product Owner/Architect design call, not an implementer default.

    **ADR-0021:** the bring-back clause (`"Bring-back viability ... supports pairing with ..."`)
    only fires when `game_stack_score is not None and game_stack_score >= BRING_BACK_VIABILITY_FLOOR`
    -- this is the same gate `build_stack_profile` applies to `bring_back_candidates`/
    `bring_back_status`, so this affirmative-thesis text never asserts a bring-back pairing the
    structured fields report as `None`/`"game_stack_not_viable"`.

    Returns `None` when `ges_home` (the anchor team's `GameEnvironmentScore`) is unavailable
    (ADR-0017 exclusion policy) -- there is no basis for an affirmative thesis without a computable
    game environment for the anchor team.
    """
    if not ges_home.is_available:
        return None
    assert ges_home.composite_score is not None  # is_available implies a real composite_score

    parts: list[str] = []
    if primary_candidates:
        parts.append(
            f"{ges_home.team} stack: QB + {_format_candidate_list(primary_candidates)}, in a "
            f"{ges_home.composite_score:.0f}/100 GameEnvironmentScore game."
        )
    else:
        parts.append(
            f"{ges_home.team} environment-only thesis: {ges_home.composite_score:.0f}/100 "
            "GameEnvironmentScore, but no pass-catcher currently clears a confident trailing "
            "target-share signal to name as the stack's vehicle."
        )

    if game_stack_score is not None and game_stack_score >= BRING_BACK_VIABILITY_FLOOR:
        parts.append(
            f"Bring-back viability {game_stack_score:.1f} supports pairing with {away_team}'s "
            "own pass-catchers."
        )

    if (
        home_rb_role_share is not None
        and home_rb_role_share.gate_passed
        and home_rb_role_share.identified is not None
        and home_rb_role_share.identified.prior_used == PRIOR_UNCONTESTED
    ):
        rb = home_rb_role_share.identified
        parts.append(
            f"{_candidate_display_name(rb)} is a confirmed uncontested backfield lead "
            f"({rb.role_share_blended:.0%} role share, ADR-0020) with no real competition -- a "
            "bell-cow thesis in its own right, independent of the pass-catching stack."
        )

    parts.append(
        "MatchupContext favorability is not yet implemented anywhere in this pipeline (PRD "
        "Section 6) -- the ranking above is by trailing target share (RoleShare) alone; revisit "
        "once MatchupContext exists."
    )
    return " ".join(parts)


# --------------------------------------------------------------------------------------------
# Output shape
# --------------------------------------------------------------------------------------------

# ADR-0021 Decision 3: the four distinguishable reasons `bring_back_candidates` can be what it is.
# "populated" and "no_confident_candidate" are the two sub-cases of "environment supports the
# thesis" (game_stack_viability >= BRING_BACK_VIABILITY_FLOOR, both GameEnvironmentScores
# available); "environment_unavailable" and "game_stack_not_viable" are the two `None` cases,
# distinguished so a consumer never has to infer which one occurred from `None`'s type alone.
BringBackStatus = Literal[
    "populated",
    "no_confident_candidate",
    "environment_unavailable",
    "game_stack_not_viable",
]


@dataclass(frozen=True)
class StackProfile:
    """PRD Section 6's `StackProfile`, correlation-stage output (Section 5 step 6): the two
    viability numbers ADR-0004/ADR-0010 specify a formula for, plus (closed this round, ADR-0019)
    the candidate-selection/thesis fields, with one still-open gap noted below (`MatchupContext`).

    One `StackProfile` per game/matchup (a directional team-pair for a given season/week), not
    per team -- `single_team_viability_home`/`_away` cover both teams' own-team stack theses,
    `game_stack_viability` covers the bring-back thesis for the matchup as a whole.

    **`primary_stack_candidates`/`bring_back_candidates`/`pivot_to` -- closed this round (ADR-0019
    `RoleShare`), with one acknowledged remaining gap:**
    - `primary_stack_candidates`: `home_team`'s top 1-2 `PlayerRoleShare` pass-catchers (see
      `select_stack_candidates`), `home_team` being this object's chosen "anchor" team for these
      three fields -- see the module docstring's "Anchor-team choice" note for why that's a real,
      flagged design choice rather than an obvious given.
    - `bring_back_candidates`: the same lookup against `away_team`'s roster (PRD Section 6:
      "selected the same way from the opposing roster") -- but ONLY when `game_stack_viability`
      also clears `BRING_BACK_VIABILITY_FLOOR` (ADR-0021, new this round -- see below).
    - `pivot_to`: a mechanical, templated affirmative-thesis string (see `build_pivot_to`) naming
      the anchor team's `GameEnvironmentScore`, its primary candidate(s), `game_stack_viability`
      when computable, and the anchor team's ADR-0020 `uncontested_signal` on its identified lead
      RB when it fired. Its bring-back clause is gated by the same ADR-0021 floor as
      `bring_back_candidates`, so `pivot_to` never affirmatively asserts a bring-back pairing that
      `bring_back_candidates`/`bring_back_status` report as unsupported.

    **Remaining, explicitly-flagged gap:** PRD Section 6 ranks these candidates by "target share
    and `MatchupContext` favorability." `MatchupContext` is not implemented anywhere in this
    pipeline yet, so the ranking above is by target share (`role_share_blended`) alone -- a real
    partial implementation of the spec, not a silent drop. `primary_stack_candidates`/
    `bring_back_candidates` are `[]` (not `None`) when a team's `"WR"`-role `RoleShareResult` has
    no trailing-volume candidates at all -- a genuine "no confident candidate this week" result,
    never a fabricated name.

    **`bring_back_candidates` is `None` for one of TWO distinct reasons (ADR-0021), disambiguated
    by `bring_back_status` -- never inferred from `None` alone:**
    - `"environment_unavailable"`: either team's `GameEnvironmentScore.is_available` is `False`
      (ADR-0017 exclusion policy: no basis for a stack thesis at all that week) -- the original,
      pre-ADR-0021 reason.
    - `"game_stack_not_viable"` (**new this round, ADR-0021**): both `GameEnvironmentScore`s ARE
      available, but the computed `game_stack_viability` is below `BRING_BACK_VIABILITY_FLOOR`
      (20.0) -- the combined game-stack thesis itself (bottleneck environment quality x
      spread-magnitude dampener) doesn't clear the bar, independent of whether a specific opposing
      WR would otherwise have been identifiable. `game_stack_viability` itself still reports its
      real computed value in this case (ADR-0021 Decision 5) -- only the candidate/thesis fields
      are gated, not the diagnostic score.
    `primary_stack_candidates` is unaffected by this gate and keeps its original two-state
    (`None`/`[]`) contract -- see `build_stack_profile`.
    """

    season: int
    week: int
    home_team: str
    away_team: str
    spread: float  # signed, as pulled (e.g. home team's own spread) -- kept for QA/display and
    # so `game_stack_viability`'s dampener band is traceable without recomputing `abs()` elsewhere
    single_team_viability_home: float | None
    single_team_viability_away: float | None
    game_stack_viability: float | None

    # ADR-0021: which of the four states (see BringBackStatus) explains bring_back_candidates's
    # current value, populated in every branch of build_stack_profile -- never left to be inferred
    # from bring_back_candidates's own None/[]/list-of-values type alone.
    bring_back_status: BringBackStatus

    # Closed this round (ADR-0019 RoleShare) -- see class docstring for exactly what's ranked on,
    # the acknowledged MatchupContext gap, and the home-team-as-anchor design choice.
    primary_stack_candidates: list[PlayerRoleShare] | None = None
    bring_back_candidates: list[PlayerRoleShare] | None = None
    pivot_to: str | None = None

    # NflAgentConstructor plan, foundation signals -- RB-inclusive stacking + game-script lean.
    # primary_rb_candidate: home_team's select_rb_stack_candidate() result -- None when
    # home_rb_role_share wasn't supplied, or no RB clears the bell_cow/mid_tier bar this week.
    # Unlike primary_stack_candidates (WR), this is NOT gated on ges_home.is_available -- it's a
    # pure read of home_rb_role_share, mirroring build_pivot_to's existing uncontested-RB clause,
    # which makes the same choice today.
    primary_rb_candidate: PlayerRoleShare | None = None
    # bring_back_rb_candidate: away_team's select_rb_stack_candidate() result -- gated on the SAME
    # ADR-0021 game_stack_viability >= BRING_BACK_VIABILITY_FLOOR floor as bring_back_candidates,
    # for the same reason: no basis for asserting a bring-back thesis (RB or WR) when the combined
    # game-stack environment itself doesn't support one.
    bring_back_rb_candidate: PlayerRoleShare | None = None
    # game_script_lean_home/_away: classify_game_script_lean() on each team's own signed spread
    # (home = `spread`, away = `-spread`). Always computed -- a pure function of `spread`, no
    # GameEnvironmentScore/RoleShareResult dependency, so never None the way the RB/WR candidate
    # fields can be.
    game_script_lean_home: GameScriptLean | None = None
    game_script_lean_away: GameScriptLean | None = None

    notes: list[str] = field(default_factory=list)

    @property
    def game_id(self) -> str:
        """Directional game identifier (`away@home`, season, week) -- matches the convention
        `ingestion/odds_api.py`'s `schedule_week_map` already uses for keying a game (directional
        because two division rivals can play twice in a season with home/away swapped)."""
        return f"{self.away_team}@{self.home_team}-{self.season}wk{self.week}"


def build_stack_profile(
    ges_home: GameEnvironmentScore,
    ges_away: GameEnvironmentScore,
    spread: float,
    home_wr_role_share: RoleShareResult,
    away_wr_role_share: RoleShareResult,
    home_rb_role_share: RoleShareResult | None = None,
    away_rb_role_share: RoleShareResult | None = None,
) -> StackProfile:
    """Assemble a full `StackProfile` from both teams' `GameEnvironmentScore`, the game's spread,
    and (new this round, ADR-0019) both teams' `"WR"`-role `RoleShareResult`s. `ges_home`/`ges_away`
    must be the same game/week (not validated here -- this is combination logic over caller-supplied
    inputs, same posture as `game_environment/score.py` and `single_team_viability`/
    `game_stack_viability` above).

    `home_team` is this call's chosen "anchor" team -- `primary_stack_candidates`/`pivot_to` are
    built from `home_wr_role_share` (must be `home_wr_role_share.team == ges_home.team`, asserted
    below), `bring_back_candidates` from `away_wr_role_share` (`== ges_away.team`). See the module
    docstring's "Anchor-team choice" note: building the mirror-image `StackProfile` (away-anchored)
    for the same game is a separate call with the team roles swapped, not something this function
    does for both directions at once.

    **ADR-0021 bring-back viability gate:** `bring_back_candidates` (and `pivot_to`'s bring-back
    clause) are only populated from `away_wr_role_share` when BOTH `GameEnvironmentScore`s are
    available AND the computed `game_stack_viability` is `>= BRING_BACK_VIABILITY_FLOOR` (20.0) --
    below that floor, the combined game-stack thesis itself doesn't clear the bar (some mix of a
    weak bottleneck environment and/or a lopsided-spread dampener), so `bring_back_candidates` is
    `None` with `bring_back_status="game_stack_not_viable"`, regardless of whether the opposing
    roster would otherwise have produced a confident WR candidate. `game_stack_viability` itself
    still returns its real computed value in this case -- only the candidate/thesis fields are
    gated (ADR-0021 Decision 5). `primary_stack_candidates`/`single_team_viability_home`/`_away`
    are unaffected by this gate.

    `home_rb_role_share`, when supplied, must be a `"RB"`-role `RoleShareResult` for `home_team` --
    used to fold an ADR-0020 `uncontested_signal` (a confirmed uncontested lead RB) into
    `pivot_to` when it fired, AND (NflAgentConstructor plan, foundation signals) to populate
    `primary_rb_candidate` via `select_rb_stack_candidate`. Optional: a caller that hasn't
    computed the anchor team's RB role share yet still gets a full `pivot_to` and `StackProfile`,
    just without those specific fields/clause. `away_rb_role_share`, likewise optional, is the
    same lookup against `away_team`'s roster, gated the SAME ADR-0021 `game_stack_viability`
    floor as `bring_back_candidates` -- see `bring_back_rb_candidate`.
    """
    assert home_wr_role_share.team == ges_home.team and home_wr_role_share.role == ROLE_WR, (
        f"home_wr_role_share must be a {ROLE_WR!r}-role RoleShareResult for {ges_home.team!r} "
        f"(the anchor team), got team={home_wr_role_share.team!r} role={home_wr_role_share.role!r}"
    )
    assert away_wr_role_share.team == ges_away.team and away_wr_role_share.role == ROLE_WR, (
        f"away_wr_role_share must be a {ROLE_WR!r}-role RoleShareResult for {ges_away.team!r} "
        f"(the bring-back team), got team={away_wr_role_share.team!r} role={away_wr_role_share.role!r}"
    )
    if home_rb_role_share is not None:
        assert home_rb_role_share.team == ges_home.team and home_rb_role_share.role == ROLE_RB, (
            f"home_rb_role_share must be a {ROLE_RB!r}-role RoleShareResult for {ges_home.team!r}, "
            f"got team={home_rb_role_share.team!r} role={home_rb_role_share.role!r}"
        )
    if away_rb_role_share is not None:
        assert away_rb_role_share.team == ges_away.team and away_rb_role_share.role == ROLE_RB, (
            f"away_rb_role_share must be a {ROLE_RB!r}-role RoleShareResult for {ges_away.team!r}, "
            f"got team={away_rb_role_share.team!r} role={away_rb_role_share.role!r}"
        )

    notes: list[str] = []
    if not ges_home.is_available:
        notes.append(
            f"{ges_home.team} GameEnvironmentScore unavailable this week -- excluded from "
            "stack-thesis consideration per ADR-0017, not zeroed or imputed."
        )
    if not ges_away.is_available:
        notes.append(
            f"{ges_away.team} GameEnvironmentScore unavailable this week -- excluded from "
            "stack-thesis consideration per ADR-0017, not zeroed or imputed."
        )

    # ADR-0017 exclusion policy, extended to candidate selection: a team-week with no computable
    # GameEnvironmentScore has no basis for a stack thesis at all, so its candidate list stays
    # None (not []) -- the same "excluded, not zeroed/imputed" distinction the viability functions
    # already make. An *available* team with zero trailing-volume candidates still gets [] (a real
    # "no confident candidate" result), which is a meaningfully different case -- see
    # select_stack_candidates's docstring. primary_stack_candidates is NOT affected by ADR-0021's
    # bring-back viability gate below -- only bring_back_candidates is.
    primary_candidates = select_stack_candidates(home_wr_role_share) if ges_home.is_available else None

    # NflAgentConstructor plan, foundation signals: primary_rb_candidate is NOT gated on
    # ges_home.is_available -- it's a pure read of home_rb_role_share's own gate-cleared
    # `identified`, mirroring build_pivot_to's existing uncontested-RB clause (below), which makes
    # the same choice today.
    primary_rb_candidate = (
        select_rb_stack_candidate(home_rb_role_share) if home_rb_role_share is not None else None
    )

    # game_stack_viability is computed first (still always a real value whenever both
    # GameEnvironmentScores are available, per ADR-0021 Decision 5) so it can gate the bring-back
    # candidate selection below -- see ADR-0021 for the full decision.
    gsv = game_stack_viability(ges_home, ges_away, spread)

    # ADR-0021 Decision 3's four-state bring_back_status, in the order that's actually computable:
    # GameEnvironmentScore availability is checked first because game_stack_viability (and thus the
    # floor comparison) doesn't exist at all when either score is unavailable.
    if not ges_home.is_available or not ges_away.is_available:
        bring_back_candidates: list[PlayerRoleShare] | None = None
        bring_back_rb_candidate: PlayerRoleShare | None = None
        bring_back_status: BringBackStatus = "environment_unavailable"
    elif gsv is not None and gsv >= BRING_BACK_VIABILITY_FLOOR:
        bring_back_candidates = select_stack_candidates(away_wr_role_share)
        bring_back_rb_candidate = (
            select_rb_stack_candidate(away_rb_role_share) if away_rb_role_share is not None else None
        )
        bring_back_status = "populated" if bring_back_candidates else "no_confident_candidate"
    else:
        # Both GameEnvironmentScores are available, but game_stack_viability < the floor -- ADR-0021,
        # new this round. The game environment itself doesn't support a bring-back thesis this week,
        # independent of whether away_wr_role_share would otherwise have produced a candidate.
        bring_back_candidates = None
        bring_back_rb_candidate = None
        bring_back_status = "game_stack_not_viable"

    pivot_to = build_pivot_to(ges_home, ges_away.team, primary_candidates, gsv, home_rb_role_share)

    # NflAgentConstructor plan, foundation signals: pure function of the signed spread, so always
    # computed -- no GameEnvironmentScore/RoleShareResult dependency, unlike every candidate field
    # above. Away team's own signed spread is the negation of home's (same game, opposite sides).
    game_script_lean_home = classify_game_script_lean(spread)
    game_script_lean_away = classify_game_script_lean(-spread)

    notes.append(
        f"primary_stack_candidates/bring_back_candidates ranked by RoleShare (role_share_blended) "
        f"alone -- MatchupContext favorability (PRD Section 6's other ranking criterion) is not "
        f"yet implemented anywhere in this pipeline. {ges_home.team} is this StackProfile's anchor "
        f"team for these fields; {ges_away.team} supplies bring_back_candidates."
    )
    if ges_home.is_available and not primary_candidates:
        notes.append(
            f"{ges_home.team}: no WR-role trailing-volume candidates this week -- "
            "primary_stack_candidates is [], not a fabricated name."
        )
    if bring_back_status == "no_confident_candidate":
        notes.append(
            f"{ges_away.team}: no WR-role trailing-volume candidates this week -- "
            "bring_back_candidates is [], not a fabricated name."
        )
    if bring_back_status == "game_stack_not_viable":
        notes.append(
            f"bring_back_candidates is None (not evaluated): game_stack_viability "
            f"({gsv:.1f}) is below BRING_BACK_VIABILITY_FLOOR ({BRING_BACK_VIABILITY_FLOOR:.1f}) -- "
            "ADR-0021, new this round -- the combined game-stack thesis for "
            f"{ges_away.team}@{ges_home.team} doesn't clear the bar (weak bottleneck environment "
            "and/or a lopsided-spread dampener), regardless of the opposing roster's own WR "
            "target-share data. single_team_viability_home/_away and primary_stack_candidates are "
            "unaffected by this gate."
        )

    return StackProfile(
        season=ges_home.season,
        week=ges_home.week,
        home_team=ges_home.team,
        away_team=ges_away.team,
        spread=spread,
        single_team_viability_home=single_team_viability(ges_home),
        single_team_viability_away=single_team_viability(ges_away),
        game_stack_viability=gsv,
        bring_back_status=bring_back_status,
        primary_stack_candidates=primary_candidates,
        bring_back_candidates=bring_back_candidates,
        pivot_to=pivot_to,
        primary_rb_candidate=primary_rb_candidate,
        bring_back_rb_candidate=bring_back_rb_candidate,
        game_script_lean_home=game_script_lean_home,
        game_script_lean_away=game_script_lean_away,
        notes=notes,
    )
