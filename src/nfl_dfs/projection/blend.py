"""Stage 3: blend vendor projections into one number per player (PRD Section 5 step 3).

**Walking-skeleton scope (Chris's pivot, see the Product Owner audit that prompted it):** this
module is deliberately the crude first pass, not the "in-house model built on nflverse data"
half of Section 5 step 3's description. That in-house model doesn't exist yet -- nothing in the
codebase computes a projection from `nflverse` play-by-play. Building it is real, separate work
for a later round. What this module does today is the other half of step 3 that's actually
buildable right now: combine the vendor projections the ingestion stage already pulls into one
blended number per player, so the optimizer (next round) has something real to sit on top of.

**Weighting rationale -- equal weight, stated explicitly, not "an arbitrary average":** the PRD
asks for "a defensible weighting rationale rather than an arbitrary average." There is no
backtested per-source accuracy data yet -- that is exactly what `ProjectionAccuracyRecord`
(PRD Section 6, ADR-0018), once built and run for a season, will eventually produce. Until that
exists, there is no principled basis for weighting one vendor's number over another's for a given
player -- weighting RotoGrinders higher than Footballguys (or vice versa) with no evidence behind
it would be the arbitrary choice, not the equal one. Equal-weighting whatever sources have a
valid number for a player is therefore the defensible v1 starting point: unbiased, stated, and
explicitly provisional. **Real weight calibration is deferred to the projection-accuracy /
backtesting work, not invented here.** Once `ProjectionAccuracyRecord` has enough live weeks of
`(source value, actual result)` pairs, this function's equal weights should be replaced with
calibrated ones -- that is future work, not a gap in this round's scope.

**Sources blended, and why PFF is not one of them this round:** the PRD's Section 4 table notes
PFF also publishes "PFF's own fantasy/DFS projections," but that facet was never pulled by
`ingestion/pff.py` (which only ingests PFF's *grades*, for `MatchupContext`) -- wiring up a whole
new PFF endpoint is out of scope for this thin round. So the sources actually blended here are:

- **RotoGrinders `FPTS`** -- RotoGrinders' own already-blended vendor projection
  (`ingestion/rotogrinders.py`'s `fetch_rotogrinders_players`/`parse_user_projections` only keeps
  identity fields for the matcher; the raw `user-projections` payload's `FPTS` field per player
  is read again here, by `extract_rotogrinders_fpts`).
- **Footballguys `Points`** -- same situation: `ingestion/footballguys.py`'s
  `parse_projection_rows` only keeps identity fields; the points-per-game value lives in the
  second `td.ppg` cell of each row (the first `td.ppg` cell is DK salary, confirmed live -- see
  `extract_footballguys_points`), read again here.
- **DraftKings' own `draftStatAttributes` id==90 value -- investigated, NOT used.** Phase 0's
  report flagged this field's presence without confirming what it represents. Live-checked here
  (`scripts/live_integration_check_projection.py`, run against a real slate): id==90's value
  diverges substantially from the RotoGrinders/Footballguys blend for several players in a way
  that doesn't track this week's specific matchup -- e.g. a live pull showed Patrick Mahomes at
  blend=16.9 vs. id90=21.6, and Rashee Rice at blend=14.2 vs. id90=19.1, both well above what
  either vendor projects for this week's specific opponent, while other players' id90 values
  track the blend closely. That pattern -- diverging most for players whose recent/career output
  runs hot relative to this week's specific matchup -- is consistent with id==90 being DK's own
  **trailing average-points-per-game stat** (the figure commonly labeled "Avg Pts Per Game"
  elsewhere in DK's own product), not a forward-looking, matchup-aware projection; it is not
  independently confirmed against DK's UI copy in this pass. Given that, and given no positive
  evidence it *is* a projection, it is deliberately excluded from `blend_player_projection`'s
  inputs rather than assumed safe to blend in as a fourth independent opinion.
  `extract_dk_avg_points_per_game` is kept around for diagnostics/QA only.

**Missing-source handling -- visible, not silently dropped:** a player with 1 of 2 (or 0 of 2)
available vendor numbers is still blended and still appears in the output pool -- `source_count`
and `source_values` on `PlayerProjection` make exactly how many/which sources contributed visible
for QA, rather than hiding a thin blend behind a number that looks the same as a fully-sourced
one. A player with zero vendor coverage still gets a `PlayerProjection` row (salary/position/team
carried through from DraftKings, `blended_projection=None`) -- the optimizer needs the full
DK-eligible pool to know such a player exists at all, even though a null projection makes them
effectively unrosterable in practice.

**DST -- explicitly the simple vendor-blend baseline, not `DSTProjection`:** `blend_dst_baseline`
below runs the *same* equal-weight vendor blend as offensive players. It is **not** the
sack-opportunity / turnover-opportunity / return-opportunity-bonus formula specified in
ADR-0008/0009/0011/0012 and PRD Section 6's `DSTProjection` section -- that sophistication is
explicitly deferred until the walking skeleton proves out end-to-end. This function exists only
to fill DK's mandatory DST roster slot (PRD Section 3) with a real, non-fabricated number for
this round.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, replace

from bs4 import BeautifulSoup

from nfl_dfs.ingestion.odds_api import GameOdds
from nfl_dfs.ingestion.usage_share import ROLE_RB, RoleShareResult, blowout_volume_discount
from nfl_dfs.matchup.context import MatchupContextResult
from nfl_dfs.normalization.identity import PlayerIdentity

# The vendor sources actually blended this round (see module docstring for why PFF and DK's own
# id==90 figure are not in this list).
VENDOR_PROJECTION_SOURCES: tuple[str, ...] = ("rotogrinders", "footballguys")

# DraftKings' `draftStatAttributes` numeric id confirmed (live, see module docstring) to carry a
# trailing average-points-per-game figure, not a projection -- not part of VENDOR_PROJECTION_SOURCES.
_DK_AVG_POINTS_PER_GAME_ATTRIBUTE_ID = 90


@dataclass(frozen=True)
class PlayerProjection:
    """One row of the Stage 3 output -- the shape the optimizer (next round) consumes directly.

    Covers the FULL DK-eligible player pool, not just players with vendor coverage: a player with
    no matched vendor projection still gets a row here (`blended_projection=None`,
    `source_count=0`) with salary/position/team intact, because the optimizer needs to know the
    full DK slate exists, not just the subset of it that happens to have outside projections.
    """

    canonical_id: str
    display_name: str
    position: str
    team: str
    salary: int | None
    blended_projection: float | None
    source_count: int
    # Which vendor source(s) contributed, and their raw (pre-blend) value -- exposed so QA/
    # debugging can see exactly what a "thin" (1-of-N-source) blend was built from, rather than a
    # blended number that looks identical whether it came from one source or every source.
    source_values: dict[str, float] = field(default_factory=dict)


def extract_rotogrinders_fpts(payload: dict) -> dict[str, float]:
    """Pure parse of a RotoGrinders `user-projections` response (the same raw payload
    `ingestion.rotogrinders.parse_user_projections` reads) into `native_id -> FPTS`.

    Kept separate from `ingestion/rotogrinders.py` rather than added to it: `SourcePlayer` (the
    matcher's join shape, ADR-0013) intentionally carries only identity fields, and this module's
    job is pure consumption of the raw payload for the one extra field (`FPTS`) the matcher never
    needed. `PLAYERID` is used as the key -- the same field `parse_user_projections` uses as
    `native_id` -- so results here join directly against `PlayerIdentity.sources["rotogrinders"]
    .native_id`.
    """
    source = payload.get("data", {}).get("source", {})
    result: dict[str, float] = {}
    for player_id, row in source.items():
        native_id = str(row.get("PLAYERID") or player_id)
        fpts = row.get("FPTS")
        if fpts in (None, ""):
            continue
        try:
            result[native_id] = float(fpts)
        except (TypeError, ValueError):
            continue
    return result


def extract_footballguys_points(html: str) -> dict[str, float]:
    """Pure parse of one Footballguys projections HTML fragment (the same markup
    `ingestion.footballguys.parse_projection_rows` reads) into `native_id -> Points`.

    Live-confirmed row shape (checked against real qb/rb/wr/te/td pulls): each `tr[data-playerid]`
    row has exactly two `td.ppg`-classed cells (BeautifulSoup/soupsieve match `class="ppg"` and
    `class="ppg "` identically -- both parse to the single class token `ppg`) -- the first is the
    player's DK salary (matches `ingestion.draftkings`'s salary for the same player), the second
    is the projected fantasy points for this `dfsSite`/`week` pull. Only the second is a
    projection; the first is not read here (DK's own `draftables` payload is the salary source of
    truth -- see `extract_dk_salary`).
    """
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, float] = {}
    for row in soup.select("tr[data-playerid]"):
        native_id = row.get("data-playerid")
        if native_id is None:
            continue
        cells = row.select("td.ppg")
        if len(cells) < 2:
            continue
        text = cells[1].get_text(strip=True)
        try:
            result[str(native_id)] = float(text)
        except ValueError:
            continue
    return result


def extract_dk_salary(payload: dict) -> dict[str, int]:
    """Pure parse of a DraftKings `draftables` response into `playerDkId (str) -> salary`.

    `ingestion.draftkings.parse_draftables`'s `SourcePlayer` output intentionally carries only
    identity fields (ADR-0013's join shape), not salary -- this reads the raw payload a second
    time for the one extra field the optimizer needs. Duplicate rows per player (one per eligible
    roster slot -- see that module's docstring) carry the same salary, so no dedupe-order concern
    here the way `parse_draftables` has for identity rows.
    """
    result: dict[str, int] = {}
    for d in payload.get("draftables", []):
        salary = d.get("salary")
        if salary is None:
            continue
        result[str(d["playerDkId"])] = int(salary)
    return result


def extract_dk_avg_points_per_game(payload: dict) -> dict[str, float]:
    """Pure parse of DraftKings `draftables`' `draftStatAttributes` id==90 value, per player.

    **Diagnostic/QA only -- not a `VENDOR_PROJECTION_SOURCES` input.** See the module docstring's
    live-check finding: this figure's divergence pattern from the real vendor blend is consistent
    with a trailing average-points stat rather than a forward-looking, matchup-aware projection,
    so it is not blended in `blend_player_projection`. Kept as its own function so a future
    backtest can still compare it against the real vendor blend without re-parsing the raw
    payload.
    """
    result: dict[str, float] = {}
    for d in payload.get("draftables", []):
        native_id = str(d["playerDkId"])
        for attribute in d.get("draftStatAttributes", []):
            if attribute.get("id") == _DK_AVG_POINTS_PER_GAME_ATTRIBUTE_ID:
                value = attribute.get("value")
                try:
                    result[native_id] = float(value)
                except (TypeError, ValueError):
                    pass
                break
    return result


def _collect_source_values(
    identity: PlayerIdentity,
    rotogrinders_fpts: dict[str, float],
    footballguys_points: dict[str, float],
) -> dict[str, float]:
    source_values: dict[str, float] = {}

    rg_match = identity.sources.get("rotogrinders")
    if rg_match is not None and rg_match.native_id is not None:
        value = rotogrinders_fpts.get(rg_match.native_id)
        if value is not None:
            source_values["rotogrinders"] = value

    fbg_match = identity.sources.get("footballguys")
    if fbg_match is not None and fbg_match.native_id is not None:
        value = footballguys_points.get(fbg_match.native_id)
        if value is not None:
            source_values["footballguys"] = value

    return source_values


def _salary_for(identity: PlayerIdentity, dk_salary: dict[str, int]) -> int | None:
    dk_match = identity.sources.get("draftkings")
    if dk_match is None or dk_match.native_id is None:
        return None
    return dk_salary.get(dk_match.native_id)


def blend_player_projection(
    identity: PlayerIdentity,
    dk_salary: dict[str, int],
    rotogrinders_fpts: dict[str, float] | None = None,
    footballguys_points: dict[str, float] | None = None,
) -> PlayerProjection:
    """Blend one canonical player's available vendor projections into a `PlayerProjection`.

    Equal-weighted average across whichever of `VENDOR_PROJECTION_SOURCES` have a resolved match
    (via `identity.sources`, ADR-0013) *and* a numeric value in the corresponding source map --
    see the module docstring for the weighting rationale. A player with zero contributing sources
    still gets a row back (`blended_projection=None`, `source_count=0`) rather than being
    dropped, with salary/position/team carried through from `identity`/`dk_salary` so the
    optimizer still knows this DK-eligible player exists.
    """
    rotogrinders_fpts = rotogrinders_fpts or {}
    footballguys_points = footballguys_points or {}

    source_values = _collect_source_values(identity, rotogrinders_fpts, footballguys_points)
    blended = sum(source_values.values()) / len(source_values) if source_values else None

    return PlayerProjection(
        canonical_id=identity.canonical_id,
        display_name=identity.display_name,
        position=identity.position,
        team=identity.team,
        salary=_salary_for(identity, dk_salary),
        blended_projection=blended,
        source_count=len(source_values),
        source_values=source_values,
    )


def blend_dst_baseline(
    identity: PlayerIdentity,
    dk_salary: dict[str, int],
    rotogrinders_fpts: dict[str, float] | None = None,
    footballguys_points: dict[str, float] | None = None,
) -> PlayerProjection:
    """DST roster-slot baseline for the walking skeleton -- a simple equal-weighted vendor blend,
    identical mechanics to `blend_player_projection`.

    **This is explicitly NOT `DSTProjection`** (PRD Section 6; ADR-0008/0009/0011/0012's
    sack-opportunity / turnover-opportunity / return-opportunity-bonus formula). That formula is
    real, specified, reviewed work for a later round -- this function's only job is to put a
    real, non-fabricated number in DK's mandatory DST slot (PRD Section 3) so the skeleton can
    produce a complete, uploadable lineup now.
    """
    if identity.position != "DST":
        raise ValueError(f"blend_dst_baseline called on a non-DST identity: {identity.position!r}")
    return blend_player_projection(identity, dk_salary, rotogrinders_fpts, footballguys_points)


def build_projection_pool(
    identities: list[PlayerIdentity],
    dk_salary: dict[str, int],
    rotogrinders_fpts: dict[str, float] | None = None,
    footballguys_points: dict[str, float] | None = None,
) -> list[PlayerProjection]:
    """Blend the full DK-eligible pool (every `PlayerIdentity` from `matcher.reconcile_week`) in
    one pass -- the direct entry point the optimizer (next round) should consume. DST identities
    route through `blend_dst_baseline`, everyone else through `blend_player_projection`; both
    produce the same `PlayerProjection` shape.
    """
    rotogrinders_fpts = rotogrinders_fpts or {}
    footballguys_points = footballguys_points or {}

    projections = []
    for identity in identities:
        blend_fn = blend_dst_baseline if identity.position == "DST" else blend_player_projection
        projections.append(blend_fn(identity, dk_salary, rotogrinders_fpts, footballguys_points))
    return projections


# --------------------------------------------------------------------------------------------
# BlowoutVolumeDiscount wiring (PRD Section 6 / ADR-0019 Decision 2, corrected by ADR-0020) --
# this is the "model/projection layer" follow-on `usage_share.py`'s own module docstring flags
# as not built there ("Follow-up for BlowoutVolumeDiscount wiring"). Connects `RoleShareResult`
# (`ingestion/usage_share.py`) and a team's current pregame spread (`ingestion/odds_api.py`) to
# this stage's `PlayerProjection` pool.
# --------------------------------------------------------------------------------------------


def team_spreads_from_games(games: list[GameOdds]) -> dict[str, float]:
    """`team -> that team's own signed pregame spread` (DK line, negative = favorite), one entry
    per team appearing in `games`. Mirrors `odds_api.implied_team_totals`'s per-team fan-out
    shape, just for the raw spread instead of the derived implied total -- `odds_api.py` itself
    is not modified; this is pure consumption of `GameOdds`.

    A team missing from `games` (its game already kicked off before this pull ran, per
    `odds_api.py`'s documented "known limitation," and ADR-0016's cached-pre-kickoff-line
    fallback chain -- not implemented in this module -- came up empty too) simply has no key
    here. Callers (`apply_rb_blowout_volume_discount` below) must treat a missing team as "no
    spread available," never a guessed/default spread.
    """
    spreads: dict[str, float] = {}
    for game in games:
        if game.home_spread is not None:
            spreads[game.home_team] = game.home_spread
        if game.away_spread is not None:
            spreads[game.away_team] = game.away_spread
    return spreads


def apply_rb_blowout_volume_discount(
    projections: list[PlayerProjection],
    role_share_results: list[RoleShareResult],
    team_spreads: dict[str, float],
) -> list[PlayerProjection]:
    """Apply `blowout_volume_discount(|pregame_spread|)` (`ingestion/usage_share.py`) as a
    multiplier on `blended_projection`, for each team's identified lead RB only.

    **Simplifying assumption, stated plainly, not left implicit:** `BlowoutVolumeDiscount` was
    designed and validated (ADR-0019/ADR-0020) against **rushing carry-share volume**, not
    fantasy points. This function multiplies it directly onto a RB's already-blended **points**
    projection (`PlayerProjection.blended_projection`), which folds together rushing volume,
    receiving volume, and both legs' efficiency into one number -- it does not decompose the
    projection into a rushing-points component (the part the discount actually models) and a
    receiving-points component (which a lead RB's blowout-risk carry-share loss doesn't directly
    speak to) and discount only the former. Neither `rotogrinders`/`footballguys` source data
    (`extract_rotogrinders_fpts`/`extract_footballguys_points`, see module docstring) exposes a
    rushing/receiving split to decompose against -- both vendors return one already-blended
    points total per player -- so a more precise decomposition isn't available from what this
    pipeline currently ingests, not merely skipped for convenience. This is a pragmatic, stated
    walking-skeleton simplification (a real, if damped, blowout-risk signal applied a bit more
    bluntly than its own validation supports), not a claim that this multiplier is exactly as
    precise against points as it is against carry share.

    For each `role_share_results` entry:
    - Non-RB roles (`role != usage_share.ROLE_RB`) are ignored entirely -- no WR formula exists
      (ADR-0019 Decision 3).
    - A team whose RB role gate failed (`gate_passed=False` / `identified=None` -- no confident
      lead-back identification, including "no trailing data yet") gets **no discount** for any
      of that team's RBs. Never a guessed/fallback discount for an unidentified backfield.
    - A team missing from `team_spreads` (no live line and no cached pre-kickoff fallback left --
      ADR-0016) gets **no discount**, not a guessed/default spread and not an error.
    - The identified lead RB is matched into `projections` by `canonical_id == identified.
      player_id`. Per `normalization/matcher.py`'s `canonical_id` selection (ADR-0013 decision
      1), `canonical_id` *is* the nflverse `gsis_id` -- the same ID space as `PlayerRoleShare.
      player_id` -- whenever the crosswalk resolves one, which it does for essentially every
      rostered skill player with recorded rush attempts. A lead RB who, unusually, has no
      crosswalk-resolved `gsis_id` (and so got a project-minted registry UUID as `canonical_id`
      instead) will not be found by this exact-ID match; this function does not fall back to a
      fuzzy name/team match for that rare case (`PlayerRoleShare.player_name` is a short/
      abbreviated form, e.g. "J.Gibbs," not reliably matchable against `PlayerProjection.
      display_name`'s full form without risking a wrong match) -- it is silently skipped for
      that one player rather than guessed. A `blended_projection=None` player (zero vendor
      coverage) is likewise skipped -- there's nothing to multiply.

    Returns a new list (input `projections` is not mutated -- `PlayerProjection` is frozen);
    every non-discounted player's row is returned unchanged (same object), so callers can rely
    on identity-equality for anyone this function didn't touch.
    """
    by_canonical_id = {projection.canonical_id: index for index, projection in enumerate(projections)}
    out = list(projections)

    for result in role_share_results:
        if result.role != ROLE_RB:
            continue
        if not result.gate_passed or result.identified is None:
            continue  # no confident lead-back identification this week -- apply no discount, ever
        spread = team_spreads.get(result.team)
        if spread is None:
            continue  # no live line, no cached pre-kickoff fallback left (ADR-0016) -- no discount
        discount = blowout_volume_discount(abs(spread))
        if discount == 1.0:
            continue  # |spread| <= 10 -- no-op, skip the copy for clarity

        index = by_canonical_id.get(result.identified.player_id)
        if index is None:
            continue  # lead RB not present in this pool under this exact canonical_id -- see docstring
        projection = out[index]
        if projection.position != "RB":
            warnings.warn(
                f"BlowoutVolumeDiscount matched {projection.display_name!r} ({projection.canonical_id}) "
                f"as {result.team}'s identified lead RB, but their DK position is {projection.position!r}, "
                "not 'RB' -- usage_share.py identifies by rush-attempt plurality, not a roster position "
                "join (see its module docstring, Judgment call 2), so this is a rare, known edge case, "
                "not a bug. Discount applied anyway.",
                stacklevel=2,
            )
        if projection.blended_projection is None:
            continue  # zero vendor coverage -- nothing to discount
        out[index] = replace(projection, blended_projection=projection.blended_projection * discount)

    return out


# --------------------------------------------------------------------------------------------
# MatchupContext wiring (PRD Section 6, Section 5 step 4 -- "Matchup adjustment ... apply
# MatchupContext to the blended projection, so trench and coverage matchups shape the number
# rather than sitting next to it as a footnote").
#
# **This is the wiring that finally lets `blended_projection` diverge from pure vendor
# consensus.** Every other input to `blend_player_projection` above is a straight equal-weighted
# average of external vendor numbers (RotoGrinders/Footballguys) -- this pipeline has, until now,
# never applied any in-house adjustment on top of that blend. `apply_matchup_context` is the first
# step in the pipeline (Section 5) that actually moves a player's number based on this project's
# own computed grade differentials (run-block vs. run-defense, pass-block vs. pass-rush, coverage
# scheme/alignment) rather than just averaging what outside sources already say. Everything
# upstream of this function is vendor consensus; everything from here on is this project's own
# model.
# --------------------------------------------------------------------------------------------


def apply_matchup_context(
    projections: list[PlayerProjection],
    matchup_context_by_canonical_id: dict[str, MatchupContextResult],
) -> list[PlayerProjection]:
    """Apply each player's `MatchupContext.combined_multiplier` (`matchup/context.py`) onto
    `blended_projection`, mirroring `apply_rb_blowout_volume_discount`'s exact wiring pattern
    above: skip gracefully whenever data is missing, never guess or substitute a neutral value for
    a player this function couldn't actually compute a real adjustment for.

    - A player with no entry in `matchup_context_by_canonical_id` at all (no `MatchupContext` was
      computed for them this week -- e.g. their team wasn't in this week's schedule, or the
      caller's PFF grade pulls didn't cover them) is returned unchanged, same object -- not
      multiplied by an assumed-neutral `1.0`, which would look identical to a real, computed,
      neutral result and hide the fact that no computation happened at all.
    - A player with `blended_projection is None` (zero vendor coverage, per
      `blend_player_projection`'s own contract) is skipped -- there is nothing to multiply.
    - Otherwise, `blended_projection` is scaled by `MatchupContextResult.combined_multiplier`
      directly. That field is never `None` by construction (`context.py`'s builders default to
      `1.0` -- an explicit, reasoned neutral -- whenever a specific row couldn't be computed), so
      no additional `None`-handling is needed here; a `combined_multiplier` of exactly `1.0` from
      a fully-neutral `MatchupContextResult` is a real no-op, not a sentinel for missing data.

    Returns a new list (input `projections` is not mutated -- `PlayerProjection` is frozen, same
    posture as `apply_rb_blowout_volume_discount`); every player this function doesn't touch is
    returned as the same object, so callers can rely on identity-equality for anyone unaffected.
    """
    out = list(projections)
    for index, projection in enumerate(out):
        result = matchup_context_by_canonical_id.get(projection.canonical_id)
        if result is None:
            continue  # no MatchupContext computed for this player this week -- no-op, not neutral
        if projection.blended_projection is None:
            continue  # zero vendor coverage -- nothing to adjust
        out[index] = replace(
            projection, blended_projection=projection.blended_projection * result.combined_multiplier
        )
    return out
