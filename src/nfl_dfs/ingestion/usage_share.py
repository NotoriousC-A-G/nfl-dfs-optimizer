"""Player-level usage-share ingestion (`RoleShare`, PRD Section 6; ADR-0019) -- carry-share and
target-share aggregation from `nfl_data_py.import_pbp_data()`, plus trailing-only "lead RB"/"WR1"
identification and shrinkage blending. This closes the ingestion gap ADR-0019 flagged: `nflverse.py`
today only aggregates play-by-play to **team-week** grain (pace/PROE) -- nothing there touches
`rusher_player_id`/`receiver_player_id`. This module adds that player-level layer alongside it
(`nflverse.py` itself is untouched) and serves **two** consumers from one computation, per ADR-0019
Decision 1:

1. `BlowoutVolumeDiscount`'s data need -- an identified lead RB's `role_share_blended`, discounted
   by pregame spread magnitude (see `blowout_volume_discount`/`adjusted_lead_rb_carry_share` below).
2. `StackProfile`'s long-standing `primary_stack_candidates`/`bring_back_candidates` blocker
   (`src/nfl_dfs/correlation/stack_profile.py`) -- a per-team ranking of pass-catchers by target
   share instead of raw season totals. Every candidate's `role_share_blended` is computed here, not
   just the single gate-identified "WR1", precisely so `StackProfile` can rank *multiple* pass-
   catchers (PRD: "top 1-2 pass-catchers ... ranked by `RoleShare`"), not just read one name.

**Neither consumer is wired up in this round** (out of scope per the task brief) -- see the two
"Follow-up" notes at the bottom of this docstring for exactly what each needs from this module's
output shape.

## Judgment call 1 -- this module computes its OWN team-week denominators, not `aggregate_team_week`'s

ADR-0019's ingestion-gap note says to reuse "the same team-week denominators `aggregate_team_week`
already computes." Read literally that doesn't hold: `aggregate_team_week` (`nflverse.py`) computes
`neutral_plays` -- a **situation-neutral-filtered** play count (`0.15 <= wp <= 0.85`,
`half_seconds_remaining > 120`) -- built specifically to *exclude* garbage time for the pace metric.
But ADR-0019's own research method is explicit: "a player's `rusher_player_id` play count / team's
total rush plays that week" -- i.e. **every** rush play, including the blowout garbage-time plays
that are the entire subject of `BlowoutVolumeDiscount`. Using `neutral_plays` as the denominator
would definitionally exclude exactly the population this signal exists to measure (a lead RB losing
carry share once a game is out of hand) -- it would silently zero out the effect ADR-0019 found.
So `aggregate_team_week_volume` below is a new, separate team-week aggregation (unfiltered rush
attempts and targets, following the module docstring's own play-selection conventions where they
still apply -- `season_type` filtering, LA->LAR normalization -- but deliberately NOT the
situation-neutral `wp`/`half_seconds_remaining` filter). Flagged explicitly for the Architect: the
literal ADR-0019 text and its own research method disagree on this point; this module follows the
research method (the actual computation that produced the 0.504/0.236 priors and the blowout
findings), not the flag's imprecise paraphrase of it.

## Judgment call 2 -- "lead RB" / "WR1" are volume leaders, not position-filtered roster labels

Neither this module nor ADR-0019's own research joins to a roster/position source before ranking
players by carry or target share -- "lead RB" means "the player with the plurality of a team's rush
attempts" and "WR1" means "the player with the plurality of a team's targets," exactly as ADR-0019's
research script computed them (no position filter is mentioned there either). In the overwhelming
majority of cases this is a true running back / true WR1, but a pass-catching back could in
principle out-earn a WR in targets, or a jet-sweep WR could log rush attempts alongside the real
lead back. This is a known, stated simplification consistent with the source ADR's own methodology,
not an oversight -- adding a position join would require crosswalking every `rusher_player_id`/
`receiver_player_id` to a roster position, a real additional dependency ADR-0019 doesn't ask for and
this round doesn't build.

## Judgment call 3 -- `ConcurrentActivityWindow` (ADR-0020 Decision 2, RB role only): two deviations
## from the ADR's literal wording, both confirmed necessary against real 2025 data, not guesses

ADR-0020 Decision 2 says to exclude a trailing week from the RB role's whole team-week computation
whenever an "established candidate" (>=10 combined RB+WR touches "elsewhere in the trailing window")
is completely blanked that week. Implemented exactly as literally worded, this **fails on the exact
LAC Hampton/Vidal case the ADR is validating against** -- confirmed live against real 2025 pbp, not
a hypothetical:

**(a) "Elsewhere in the trailing window" is read CAUSALLY (prior weeks only), not symmetrically
(the whole window).** A literal whole-window read creates a real chicken-and-egg pair on the LAC
case: Hampton's healthy weeks 1-5 (66 combined carries) and Vidal's replacement stint weeks 6-9 (62
combined carries) are each individually >=10 over the *full* 9-week window, so a symmetric read
flags Vidal as "established" via his own (future, injury-driven) replacement volume and excludes
Hampton's healthy weeks 1-4 right back out again -- collapsing the kept window to a single week.
Reading "elsewhere" causally (a candidate's established-volume total only accumulates from weeks
strictly *before* the week being tested, walking the trailing window chronologically) breaks the
cycle: Vidal has no real prior volume before week 6, so he never gets to retroactively invalidate
Hampton's earlier healthy weeks. This also matches this project's own no-look-ahead posture
(ADR-0003/ADR-0014) more closely than a symmetric read does. Live-confirmed: this reproduces the
ADR's target split almost exactly (Hampton 57.9%, Vidal 3.5% as of the 2025 week-10 case -- see the
Data Integration Engineer's live-verification notes for the exact run).

**(b) Only the top-2 candidates by total trailing combined touches are eligible to be an
"established candidate" at all** -- not every historical rusher who ever touched the ball. Applying
the raw >=10-touch floor to *every* candidate is confirmed, against the same real LAC data, to
cascade badly: an ordinary early-season rotational back (e.g. a committee back who logs a real
15-20-touch stretch over the first few weeks, then loses his role for the rest of the season once a
rookie takes over -- not an injury, just a normal role change) clears the floor and then, once
"established," gets flagged as "blanked" on *every subsequent week for the rest of the season*,
excluding far more of the trailing window than the genuine missed-game case this filter targets.
Restricting eligibility to the top-2 candidates (by total window volume) keeps the check focused on
the two players an injury/return story is actually about, without a bit-part back's normal fade-out
silently gutting the trailing window.

Both deviations are flagged here for the Architect's review, per this Data Integration Engineer's
"report ground truth precisely" mandate -- they are not implied by ADR-0020's text, which describes
the check as "symmetric by construction" and doesn't mention a top-2 restriction. They were adopted
because the literal spec, run against the real case ADR-0020 itself validates against, does not
reproduce that case's own reported numbers, and a documented, tested deviation is preferable to a
faithful-but-broken implementation.

**Also confirmed against the real LAC case (not built as a fix, just reported as ground truth):**
the ADR's own "consistency check" claim -- that the corrected weeks-1-5 data would flag Hampton as
`uncontested_signal = True` because "Vidal's concurrent-window volume (4 carries) clears the <=5
threshold" -- does not hold literally. The actual second-highest-volume non-QB RB candidate in
weeks 1-5 is N.Harris (15 trailing carries, a fading Week 1-3 committee back), not Vidal, so
Decision 1b's second-candidate check (implemented exactly as specified, no deviation) finds a
non-QB candidate with 15 > 5 trailing carries and does NOT fire the uncontested signal for this
particular team-week -- `prior_used` comes back `"league_average"`, not `"uncontested"`, for the
live LAC week-10 case. This is implemented per spec; the discrepancy is in the ADR's own worked
example, not in this module.

## Identification gate (ADR-0006/ADR-0012 pattern, per ADR-0019's explicit instruction)

Plurality-holder + minimum-volume floor, evaluated on the RAW trailing share/volume (not the
shrinkage-blended value) -- same two-gate shape as ADR-0006's slot/perimeter defender
identification: a margin threshold (the leader must clear a real, not razor-thin, share) and a
snap/attempt-count floor (guards against a clean-looking share off a tiny sample). Starting values
are ADR-0019's own draft proposal, explicitly flagged there as "draft, not backtested":

- **RB**: trailing carry share > 40% AND >= 15 trailing season carries.
- **WR**: trailing target share > 18% AND >= 20 trailing season targets.

**Fallback when the gate fails**: `RoleShareResult.identified = None`, `gate_passed = False`, with
`gate_reason` stating which leg failed -- never a guessed name, mirroring ADR-0012's returner-role
fallback and ADR-0006's team-wide-fallback pattern (the "fallback" here is simply "no identified
player this week," since there is no lower-fidelity per-player alternative to fall back to the way
ADR-0006 falls back to a team-wide grade).

## Shrinkage (ADR-0011, reused as-is -- not reimplemented)

`role_share_blended = shrinkage_weight(n, k) * trailing_share + (1 - shrinkage_weight(n, k)) *
league_prior`, via `game_environment_stats.shrinkage_weight`/`blend_toward_prior` -- the exact same
functions `nflverse.py` already uses for pace/PROE, imported here rather than reimplemented.
`n` = weeks played (team-level, i.e. completed weeks through `target_week - 1`; for the WR role this
matches `nflverse.py`'s own `weeks_played` definition exactly -- not a per-player attempt count. For
the RB role, per ADR-0020 Decision 2, `n` is the POST-`ConcurrentActivityWindow` week count instead
-- see Judgment call 3 below; this divergence is RB-specific and does not apply to WR), `k` =
`nflverse.DEFAULT_K` (6.0), reused directly (not a new module-local `6.0` literal) per ADR-0019's
own instruction to reuse pace/PROE's `k` as a first-pass starting point -- ADR-0020 explicitly
leaves `k` untouched too (only the prior swaps, per Decision 1d). League priors are this project's
own live 2023-2025 computation (ADR-0019): RB lead-back share 0.504, WR1 target share 0.236; a
confirmed-uncontested lead RB (ADR-0020 Decision 1) blends toward `UNCONTESTED_RB_PRIOR` (0.56)
instead, same weight/curve.

Because `blend_toward_prior` is linear and monotonic in `trailing_share` for a fixed weight/prior
(`blended = w*share + (1-w)*prior`, `w` identical across every player on the same team-week), sorting
a team's candidates by `role_share_blended` produces the *same order* as sorting by raw
`trailing_share` whenever `w > 0` (weeks_played > 0) -- so `RoleShareResult.candidates` (sorted by
`role_share_blended`, for `StackProfile`'s ranking use) and "who is the plurality leader" (used for
the identification gate, on the raw share) agree on who is #1, by construction, not coincidence.

## RB role tiers (RB only -- PRD Section 6 / ADR-0019, no WR tiers)

Cut points from ADR-0019's own empirical distribution of full-season lead-RB share (roughly the
75th/25th percentiles): bell-cow >= 0.60, mid-tier 0.45-0.60, committee < 0.45. Applied to
`role_share_blended` (the current best estimate), not raw `trailing_share` -- a deliberate choice
so early-season players default toward "presumed mid-tier" (the league-prior 0.504 sits inside the
mid-tier band) until enough of their own trailing data overrides it, consistent with this project's
general shrinkage posture (favor the prior until real signal accumulates). Not computed for WR
(`role_tier` is always `None` on a WR `PlayerRoleShare`) -- ADR-0019 Finding 3/Decision 2 found no
role-concentration-scaled effect to tier for, and no WR tier cut points were ever computed.

## `BlowoutVolumeDiscount` (PRD Section 6 / ADR-0019 Decision 2) -- pure formula only

`blowout_volume_discount`/`adjusted_lead_rb_carry_share` implement the exact banded formula ADR-0019
specifies (applies only to a team's *identified* lead RB, any tier -- Finding 3 showed the effect
isn't reliably gated by role concentration, so tier does not scale this discount). This is the pure
formula only -- multiplying it into an RB's actual final projected volume at Section 5 step 4 is
model-layer follow-on work, not built here (see "Follow-up for BlowoutVolumeDiscount wiring" below).

## Follow-up for `BlowoutVolumeDiscount` wiring (not built this round)

The model/projection layer needs, per team per week: `adjusted_lead_rb_carry_share(role_share_result,
pregame_spread)` where `role_share_result` is this module's `RoleShareResult` for that team's `"RB"`
role. It returns `None` when no lead RB is identified this week (gate failed or no trailing data) --
callers must treat `None` as "no volume-share adjustment this week," never fall back to a raw season
total or a guessed name. `pregame_spread` is already ingested via `ingestion/odds_api.py` (this
module adds no new dependency for it). The multiplication itself (`adjusted_carry_share` combined
with the RB's efficiency-side `MatchupContext` grade, PRD Section 6) is the wiring step this round
does not build.

## Follow-up for `StackProfile` candidate-selection wiring (not built this round)

`src/nfl_dfs/correlation/stack_profile.py`'s `primary_stack_candidates`/`bring_back_candidates`
need, per team per week: `RoleShareResult.candidates` for that team's `"WR"` role (already sorted
descending by `role_share_blended`) -- take the top 1-2 entries' `player_name`/`player_id` as the
ranked pass-catcher list PRD Section 6 specifies, combined with `MatchupContext` favorability (not
yet implemented) for the final ranking. `bring_back_candidates` is the same lookup against the
opposing team's `RoleShareResult`. This module deliberately returns *every* candidate with any
trailing volume (not just the single gate-identified "WR1") specifically so this ranking step has
more than one name to choose from -- the identification gate is a separate, RB-focused
`BlowoutVolumeDiscount` concern, not a filter on what `StackProfile` is allowed to see.

## Red zone usage (ADR-0022 Round A -- player-detail metrics data scoping)

`aggregate_team_week_volume_red_zone`/`aggregate_player_week_red_zone`/
`aggregate_player_trailing_red_zone` near the bottom of this module extend the exact
`aggregate_team_week_volume`/`aggregate_player_week` aggregation pattern above with a
red-zone-filtered (`yardline_100 <= 20`) variant, per ADR-0022's explicit direction ("same
player-grouping logic, just pre-filtered rows"). Purely descriptive/display data for a future
player-detail view -- not wired into `RoleShare`, `BlowoutVolumeDiscount`, `StackProfile`, or any
other formula this round.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from nfl_dfs.ingestion.game_environment_stats import blend_toward_prior, shrinkage_weight
from nfl_dfs.ingestion.nflverse import DEFAULT_K
from nfl_dfs.normalization.team_aliases import CANONICAL_TEAMS, normalize_team

# --------------------------------------------------------------------------------------------
# Roles, priors, gate thresholds, tier cuts -- all named constants, per ADR-0019 (see module
# docstring for the exact citations behind each number).
# --------------------------------------------------------------------------------------------

ROLE_RB = "RB"  # carry-share role (plurality rusher_player_id) -- see module docstring judgment call 2
ROLE_WR = "WR"  # target-share role (plurality receiver_player_id) -- ditto

LEAGUE_PRIOR_SHARE: dict[str, float] = {
    ROLE_RB: 0.504,  # ADR-0019 live 2023-2025 computation: mean full-season lead-RB carry share
    ROLE_WR: 0.236,  # ADR-0019 live 2023-2025 computation: mean full-season WR1 target share
}

# Identification gate (ADR-0006/ADR-0012 pattern) -- draft starting values, ADR-0019's own proposal,
# explicitly flagged there as not yet backtested.
RB_GATE_MIN_SHARE = 0.40  # strictly greater than -- an exact 40.0% does not clear the gate
RB_GATE_MIN_VOLUME = 15  # trailing season carries, >= this floor
WR_GATE_MIN_SHARE = 0.18  # strictly greater than
WR_GATE_MIN_VOLUME = 20  # trailing season targets, >= this floor

# RB role tiers (RB only -- see module docstring). Applied to role_share_blended.
BELLCOW_THRESHOLD = 0.60
MIDTIER_THRESHOLD = 0.45

# ADR-0020 Decision 2 (ConcurrentActivityWindow, RB role only) -- draft, unbacktested per the ADR's
# own disclosure. See module docstring Judgment call 3 for the two implementation deviations from
# the ADR's literal wording this module makes, both confirmed necessary against real 2025 data.
RB_ESTABLISHED_CANDIDATE_TOUCH_FLOOR = 10  # combined RB+WR touches "elsewhere" required

# ADR-0020 Decision 1 (ConfirmedUncontestedPrior, RB role only).
UNCONTESTED_RB_PRIOR = 0.56  # Decision 1a -- validated trailing-window figure (n=16, t=4.25)
UNCONTESTED_RB_SECOND_CANDIDATE_MAX_CARRIES = 5  # Decision 1b -- the validated ≤5 cut
QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS = 5  # Decision 1c -- new, unvalidated heuristic

PRIOR_LEAGUE_AVERAGE = "league_average"
PRIOR_UNCONTESTED = "uncontested"

# BlowoutVolumeDiscount bands (PRD Section 6 / ADR-0019 Decision 2). (upper_bound_inclusive,
# discount) pairs, checked in order, mirroring stack_profile.py's SPREAD_DAMPENER_BANDS style.
BLOWOUT_DISCOUNT_BANDS: tuple[tuple[float, float], ...] = (
    (10.0, 1.00),
    (14.0, 0.97),
    (float("inf"), 0.93),
)


# --------------------------------------------------------------------------------------------
# Output shapes
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerRoleShare:
    """One player's trailing role-share data for one team-week's identification pass. `role_share
    _blended` is the `RoleShare` PRD Section 6/ADR-0019 names -- the shrinkage-blended value both
    `BlowoutVolumeDiscount` and `StackProfile`'s ranking consume.

    `trailing_share`/`trailing_volume` are RAW (unblended) season-to-date-through-`week-1` numbers
    -- what the identification gate is evaluated against. `weeks_played` is the TEAM's count of
    completed weeks through `week-1` -- for the WR role this matches `nflverse.py`'s `weeks_played`
    definition exactly (same value for every player on the same team-week). For the RB role,
    ADR-0020 Decision 2 deliberately diverges: `weeks_played` is the POST-`ConcurrentActivityWindow`
    count (a trailing week where an established RB candidate was completely blanked is excluded from
    the count, not just from the volume totals) -- see module docstring Judgment call 3. This
    divergence is RB-specific by design and must not leak into the WR role or any other module.

    `prior_used` (ADR-0020 Decision 1d) records which prior `role_share_blended` was blended toward
    -- `PRIOR_LEAGUE_AVERAGE` ("league_average") for every WR row and every RB row except a
    gate-passed, confirmed-uncontested lead RB, which gets `PRIOR_UNCONTESTED` ("uncontested")
    instead. Recorded for transparency/QA, per ADR-0020's explicit instruction -- not consumed by
    any formula in this module.
    """

    player_id: str  # nflverse's rusher_player_id/receiver_player_id (e.g. "00-0033553")
    player_name: str | None  # rusher_player_name/receiver_player_name (short form, e.g. "J.Gibbs")
    role: str  # ROLE_RB or ROLE_WR
    weeks_played: int  # n -- team-level completed weeks through target_week - 1; RB role only:
    # post-ConcurrentActivityWindow count (ADR-0020 Decision 2), see docstring above
    trailing_volume: int  # this player's trailing carries (RB) or targets (WR)
    trailing_team_volume: int  # team's trailing total rush attempts (RB) or targets (WR)
    trailing_share: float  # trailing_volume / trailing_team_volume
    shrinkage_weight: float  # w(n) = n / (n + k)
    role_share_blended: float  # the `RoleShare` signal -- see module docstring
    role_tier: str | None  # "bell_cow" / "mid_tier" / "committee" for RB; always None for WR
    prior_used: str  # PRIOR_LEAGUE_AVERAGE or PRIOR_UNCONTESTED (RB only) -- ADR-0020 Decision 1d


@dataclass(frozen=True)
class RoleShareResult:
    """Per (season, week, team, role): every candidate player's `RoleShare` (for `StackProfile`'s
    ranking use) plus the gated identification of a single "lead" player for that role (for
    `BlowoutVolumeDiscount`'s use, RB role only in practice -- PRD Section 6 specifies no WR1
    formula, but this module computes the WR identification too since it's the same mechanism and
    a future WR mechanism shouldn't need to re-derive it).

    `week` is the "as of" week -- the week being projected for; all trailing data covers completed
    weeks `1..week-1` only (ADR-0003/ADR-0014 no-look-ahead discipline), never `week` itself.
    """

    season: int
    week: int
    team: str
    role: str
    candidates: list[PlayerRoleShare]  # sorted descending by role_share_blended; [] if no trailing
    # player volume was recorded for this team-role (bye week(s) so far, or week 1)
    identified: PlayerRoleShare | None  # the gate-cleared plurality leader, or None -- NEVER a
    # guessed name when the gate fails (ADR-0006/ADR-0012 fallback discipline)
    gate_passed: bool
    gate_reason: str = ""  # human-readable trace of which gate leg(s) passed/failed, for QA
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------
# Team-week denominators (this module's OWN aggregation -- see module docstring judgment call 1
# for why this is not aggregate_team_week's neutral_plays)
# --------------------------------------------------------------------------------------------


def aggregate_team_week_volume(pbp: pd.DataFrame, *, season_type: str | None = "REG") -> pd.DataFrame:
    """One row per (season, week, team): `team_rush_attempts` (every `play_type == "run"` play --
    unfiltered by situation-neutral `wp`/`half_seconds_remaining`, deliberately, see module
    docstring) and `team_targets` (every `play_type == "pass"` play with a non-null
    `receiver_player_id` -- excludes sacks/throwaways/spikes, which never target a receiver;
    live-confirmed against 2025 pbp that `play_type == "run"` already excludes `qb_kneel`/
    `qb_spike`, which are their own distinct `play_type` values, so no additional kneel/spike
    filter is needed here).
    """
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]
    rush = df[df["play_type"] == "run"]
    targets = df[(df["play_type"] == "pass") & df["receiver_player_id"].notna()]

    rush_agg = rush.groupby(["season", "week", "posteam"], observed=True).size().rename("team_rush_attempts")
    target_agg = targets.groupby(["season", "week", "posteam"], observed=True).size().rename("team_targets")

    out = pd.concat([rush_agg, target_agg], axis=1).fillna(0).reset_index()
    out = out.rename(columns={"posteam": "team"})
    out["team"] = out["team"].map(lambda t: normalize_team("nflverse_schedule", t))
    out["team_rush_attempts"] = out["team_rush_attempts"].astype(int)
    out["team_targets"] = out["team_targets"].astype(int)
    return out


def aggregate_passer_week(pbp: pd.DataFrame, *, season_type: str | None = "REG") -> pd.DataFrame:
    """One row per (season, week, team, player_id): trailing pass attempts for that passer that
    week -- used only by the RB role's QB-exclusion check (ADR-0020 Decision 1c) to keep a mobile
    QB's `rusher_player_id` rows out of the "second RB candidate" determination (a QB's scrambles
    show up in the RB carry data too; see module docstring). `play_type == "pass"` with a non-null
    `passer_player_id` -- this necessarily counts sack plays too (`passer_player_id` is still
    populated on a sack, not just a completed/incomplete attempt); a deliberate, disclosed
    over-count, same posture as this module's other pbp-derived approximations -- this check only
    needs "is this player clearly a real, established passer," not an exact NFL-official
    pass-attempts count."""
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]
    passes = df[(df["play_type"] == "pass") & df["passer_player_id"].notna()]
    agg = (
        passes.groupby(["season", "week", "posteam", "passer_player_id"], observed=True)
        .size()
        .rename("pass_attempts")
        .reset_index()
        .rename(columns={"posteam": "team", "passer_player_id": "player_id"})
    )
    agg["team"] = agg["team"].map(lambda t: normalize_team("nflverse_schedule", t))
    agg["pass_attempts"] = agg["pass_attempts"].astype(int)
    return agg


def aggregate_player_week(pbp: pd.DataFrame, *, season_type: str | None = "REG") -> pd.DataFrame:
    """One row per (season, week, team, player_id, role): `volume` (this player's carries or
    targets that week), `player_name`, and `share` (`volume` / the matching team-week denominator
    from `aggregate_team_week_volume`, joined once here rather than recomputed per player).
    """
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]

    rush = df[df["play_type"] == "run"]
    rush_player = (
        rush.groupby(["season", "week", "posteam", "rusher_player_id"], observed=True)
        .agg(volume=("play_id", "count"), player_name=("rusher_player_name", "first"))
        .reset_index()
        .rename(columns={"posteam": "team", "rusher_player_id": "player_id"})
    )
    rush_player["role"] = ROLE_RB

    targets = df[(df["play_type"] == "pass") & df["receiver_player_id"].notna()]
    target_player = (
        targets.groupby(["season", "week", "posteam", "receiver_player_id"], observed=True)
        .agg(volume=("play_id", "count"), player_name=("receiver_player_name", "first"))
        .reset_index()
        .rename(columns={"posteam": "team", "receiver_player_id": "player_id"})
    )
    target_player["role"] = ROLE_WR

    player_week = pd.concat([rush_player, target_player], ignore_index=True)
    player_week["team"] = player_week["team"].map(lambda t: normalize_team("nflverse_schedule", t))

    team_week_volume = aggregate_team_week_volume(pbp, season_type=season_type)
    merged = player_week.merge(team_week_volume, on=["season", "week", "team"], how="left")
    merged["team_volume"] = merged.apply(
        lambda r: r["team_rush_attempts"] if r["role"] == ROLE_RB else r["team_targets"], axis=1
    )
    merged["share"] = merged["volume"] / merged["team_volume"]
    return merged.drop(columns=["team_rush_attempts", "team_targets"])


# --------------------------------------------------------------------------------------------
# Trailing-only identification + shrinkage (the core of this module)
# --------------------------------------------------------------------------------------------


def _tier_for_rb_share(role_share_blended: float) -> str:
    """RB role tiers only -- see module docstring. Applied to the blended value, not raw
    trailing_share."""
    if role_share_blended >= BELLCOW_THRESHOLD:
        return "bell_cow"
    if role_share_blended >= MIDTIER_THRESHOLD:
        return "mid_tier"
    return "committee"


def _build_candidates(
    prior_players: pd.DataFrame,
    team: str,
    role: str,
    weeks_played: int,
    team_trailing_volume: int,
    k: float,
) -> list[PlayerRoleShare]:
    """Every player with trailing volume in this role for this team, shrinkage-blended and sorted
    descending by role_share_blended (== descending by raw trailing_share, see module docstring's
    monotonicity note)."""
    rows = prior_players[(prior_players["team"] == team) & (prior_players["role"] == role)]
    if rows.empty:
        return []
    agg = rows.groupby(["player_id", "player_name"], observed=True, dropna=False)["volume"].sum().reset_index()

    prior_value = LEAGUE_PRIOR_SHARE[role]
    weight = shrinkage_weight(weeks_played, k)

    candidates: list[PlayerRoleShare] = []
    for _, r in agg.iterrows():
        trailing_volume = int(r["volume"])
        # team_trailing_volume must be >= trailing_volume here: this player's own plays are a
        # subset of the team's plays that produced the denominator, so it can never be 0 when a
        # candidate row exists -- asserted explicitly rather than silently divided-by-zero.
        assert team_trailing_volume > 0, (
            f"team_trailing_volume=0 but {team}/{role} has a candidate with {trailing_volume} "
            "trailing plays -- denominator/player aggregation are out of sync"
        )
        trailing_share = trailing_volume / team_trailing_volume
        blended = blend_toward_prior(trailing_share, prior_value, weight)
        candidates.append(
            PlayerRoleShare(
                player_id=str(r["player_id"]),
                player_name=str(r["player_name"]) if pd.notna(r["player_name"]) else None,
                role=role,
                weeks_played=weeks_played,
                trailing_volume=trailing_volume,
                trailing_team_volume=team_trailing_volume,
                trailing_share=trailing_share,
                shrinkage_weight=weight,
                role_share_blended=blended,
                role_tier=_tier_for_rb_share(blended) if role == ROLE_RB else None,
                prior_used=PRIOR_LEAGUE_AVERAGE,
            )
        )
    candidates.sort(key=lambda c: c.role_share_blended, reverse=True)
    return candidates


def _rb_concurrent_activity_excluded_weeks(team_role_rows: pd.DataFrame, team_weeks: list[int]) -> set[int]:
    """ADR-0020 Decision 2 -- weeks to exclude from a team's RB-role trailing computation because an
    established candidate was completely blanked (zero combined RB+WR touches) that week while
    having real volume elsewhere -- almost always a missed-game/injury artifact, not a real quiet
    week. See module docstring Judgment call 3 for the two deliberate deviations from ADR-0020's
    literal wording this function makes (both confirmed necessary against real 2025 LAC data).

    `team_role_rows` is this team's `aggregate_player_week` trailing rows, BOTH roles -- combined
    touches sum a candidate's RB-role row AND WR-role row (a receiving back like Hampton needs both
    counted, ADR-0020's explicit instruction). `team_weeks` is the full set of trailing weeks this
    team has any recorded volume for (from `aggregate_team_week_volume`), so a week with zero
    activity from every RB candidate is still considered, not silently skipped."""
    rb_candidate_ids = set(team_role_rows.loc[team_role_rows["role"] == ROLE_RB, "player_id"])
    if not rb_candidate_ids or not team_weeks:
        return set()

    relevant = team_role_rows[team_role_rows["player_id"].isin(rb_candidate_ids)]
    combined = relevant.groupby(["week", "player_id"], observed=True)["volume"].sum().reset_index()
    pivot = combined.pivot_table(
        index="player_id", columns="week", values="volume", fill_value=0, aggfunc="sum"
    ).reindex(columns=sorted(team_weeks), fill_value=0)
    if pivot.empty:
        return set()

    # Judgment call 3b: only the top-2 candidates by total trailing combined touches are eligible
    # to be an "established candidate" -- see module docstring for why a flat floor applied to
    # every historical rusher cascades badly on real data.
    eligible_ids = pivot.sum(axis=1).nlargest(2).index

    # Judgment call 3a: "elsewhere" is read causally -- a candidate's established-volume total only
    # accumulates from weeks strictly BEFORE the week under test, walking the window chronologically.
    excluded: set[int] = set()
    established_prior = dict.fromkeys(eligible_ids, 0.0)
    for week in pivot.columns:
        blanked_established = [
            pid
            for pid in eligible_ids
            if established_prior[pid] >= RB_ESTABLISHED_CANDIDATE_TOUCH_FLOOR and pivot.loc[pid, week] == 0
        ]
        if blanked_established:
            excluded.add(int(week))
        for pid in eligible_ids:
            established_prior[pid] += pivot.loc[pid, week]
    return excluded


def _trailing_qb_ids(prior_passer_week: pd.DataFrame, team: str) -> set[str]:
    """ADR-0020 Decision 1c -- player_ids who are clearly real passers in this team's trailing
    window (>= QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS trailing pass attempts), so a mobile QB's
    rusher_player_id rows don't get mistaken for a competing running back when identifying the RB
    role's "second candidate" (Decision 1b)."""
    rows = prior_passer_week[prior_passer_week["team"] == team]
    if rows.empty:
        return set()
    totals = rows.groupby("player_id", observed=True)["pass_attempts"].sum()
    return set(totals[totals >= QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS].index)


def _second_rb_candidate_trailing_volume(candidates: list[PlayerRoleShare], qb_ids: set[str]) -> int | None:
    """ADR-0020 Decision 1b -- the second-highest-volume RB candidate (post
    ConcurrentActivityWindow filtering, `candidates[1:]` since `candidates[0]` is the already
    gate-passed leader), excluding any player identified as a real passer (Decision 1c). `candidates`
    is already sorted descending by role_share_blended/trailing_share (module docstring's
    monotonicity note). Returns `None` when no non-QB second candidate exists at all -- ADR-0020's
    "or there is no second candidate at all" firing condition."""
    for candidate in candidates[1:]:
        if candidate.player_id not in qb_ids:
            return candidate.trailing_volume
    return None


def _check_gate(top: PlayerRoleShare, role: str) -> tuple[bool, str]:
    """ADR-0006/ADR-0012-pattern gate on the plurality leader's RAW trailing numbers."""
    min_share = RB_GATE_MIN_SHARE if role == ROLE_RB else WR_GATE_MIN_SHARE
    min_volume = RB_GATE_MIN_VOLUME if role == ROLE_RB else WR_GATE_MIN_VOLUME

    share_ok = top.trailing_share > min_share
    volume_ok = top.trailing_volume >= min_volume
    if share_ok and volume_ok:
        return True, (
            f"{top.player_name or top.player_id}: trailing_share={top.trailing_share:.3f} > "
            f"{min_share:.2f} and trailing_volume={top.trailing_volume} >= {min_volume} -- gate passed"
        )
    failed_legs = []
    if not share_ok:
        failed_legs.append(f"trailing_share={top.trailing_share:.3f} <= {min_share:.2f}")
    if not volume_ok:
        failed_legs.append(f"trailing_volume={top.trailing_volume} < {min_volume}")
    return False, (
        f"{top.player_name or top.player_id}: gate failed ({'; '.join(failed_legs)}) -- "
        "no identified player this week, per ADR-0006/ADR-0012 fallback discipline"
    )


def _role_share_result_for_team(
    prior_players: pd.DataFrame,
    prior_team_volume: pd.DataFrame,
    prior_passer_week: pd.DataFrame,
    season: int,
    target_week: int,
    team: str,
    role: str,
    k: float,
) -> RoleShareResult:
    team_volume_rows = prior_team_volume[prior_team_volume["team"] == team]
    candidate_rows = prior_players
    notes: list[str] = []

    if role == ROLE_RB:
        # ADR-0020 Decision 2 -- applied BEFORE Decision 1's gate/prior-swap check, per the ADR's
        # explicit ordering ("Decision 2 cleans the input Decision 1's signal is computed from").
        team_role_rows = prior_players[prior_players["team"] == team]
        team_weeks = sorted(team_volume_rows["week"].unique().tolist())
        excluded_weeks = _rb_concurrent_activity_excluded_weeks(team_role_rows, team_weeks)
        if excluded_weeks:
            notes.append(
                "ADR-0020 ConcurrentActivityWindow excluded trailing week(s) "
                f"{sorted(excluded_weeks)} for the RB role -- an established RB candidate recorded "
                "zero combined RB+WR touches that week while active elsewhere in the trailing "
                "window; treated as a missed-game artifact, not a real quiet week. Both team-week "
                "denominator and weeks_played reflect only the remaining weeks (see module "
                "docstring, PlayerRoleShare.weeks_played)."
            )
            team_volume_rows = team_volume_rows[~team_volume_rows["week"].isin(excluded_weeks)]
            candidate_rows = prior_players[~prior_players["week"].isin(excluded_weeks)]

    weeks_played = int(len(team_volume_rows))
    volume_col = "team_rush_attempts" if role == ROLE_RB else "team_targets"
    team_trailing_volume = int(team_volume_rows[volume_col].sum())

    candidates = _build_candidates(candidate_rows, team, role, weeks_played, team_trailing_volume, k)
    if not candidates:
        return RoleShareResult(
            season=season,
            week=target_week,
            team=team,
            role=role,
            candidates=[],
            identified=None,
            gate_passed=False,
            gate_reason=(
                f"no trailing {role} volume recorded for {team} through week {target_week - 1} "
                "(bye week(s) so far, or target_week=1) -- gate cannot evaluate"
            ),
            notes=notes,
        )

    top = candidates[0]
    gate_passed, gate_reason = _check_gate(top, role)

    if gate_passed and role == ROLE_RB:
        # ADR-0020 Decision 1 -- ConfirmedUncontestedPrior, evaluated only for a team-role that has
        # already cleared the existing identification gate on its RAW trailing numbers.
        qb_ids = _trailing_qb_ids(prior_passer_week, team)
        second_volume = _second_rb_candidate_trailing_volume(candidates, qb_ids)
        uncontested = second_volume is None or second_volume <= UNCONTESTED_RB_SECOND_CANDIDATE_MAX_CARRIES
        if uncontested:
            new_blended = blend_toward_prior(top.trailing_share, UNCONTESTED_RB_PRIOR, top.shrinkage_weight)
            top = replace(
                top,
                role_share_blended=new_blended,
                role_tier=_tier_for_rb_share(new_blended),
                prior_used=PRIOR_UNCONTESTED,
            )
            candidates = [top, *candidates[1:]]
            notes.append(
                "ADR-0020 uncontested_signal fired for "
                f"{top.player_name or top.player_id}: second-highest-volume non-QB RB candidate "
                f"has {second_volume if second_volume is not None else 0} trailing carries (<= "
                f"{UNCONTESTED_RB_SECOND_CANDIDATE_MAX_CARRIES}, or no second candidate at all) -- "
                f"blended toward UNCONTESTED_RB_PRIOR={UNCONTESTED_RB_PRIOR} instead of the generic "
                f"league-average prior ({LEAGUE_PRIOR_SHARE[ROLE_RB]})."
            )

    return RoleShareResult(
        season=season,
        week=target_week,
        team=team,
        role=role,
        candidates=candidates,
        identified=top if gate_passed else None,
        gate_passed=gate_passed,
        gate_reason=gate_reason,
        notes=notes,
    )


def compute_role_shares_for_week(
    current_player_week: pd.DataFrame,
    current_team_week_volume: pd.DataFrame,
    current_passer_week: pd.DataFrame,
    season: int,
    target_week: int,
    *,
    all_teams: frozenset[str] = CANONICAL_TEAMS,
    k: float = DEFAULT_K,
) -> list[RoleShareResult]:
    """Pure computation for one target week: for every team in `all_teams` and both roles
    (`ROLE_RB`, `ROLE_WR`), identify the trailing-only lead player (weeks `1..target_week-1` only,
    never `target_week` itself -- ADR-0003/ADR-0014 no-look-ahead discipline) and shrinkage-blend
    every candidate's role share. Returns `2 * len(all_teams)` results (one per team per role).

    `current_player_week`/`current_team_week_volume`/`current_passer_week` are already-aggregated
    (via `aggregate_player_week`/`aggregate_team_week_volume`/`aggregate_passer_week`) -- this
    function does not touch raw play-by-play, matching `nflverse.py`'s `compute_pace_proe_for_week`
    split between pure computation and the live-pull wrapper below. `current_passer_week` is used
    only for the RB role's QB-exclusion check (ADR-0020 Decision 1c); the WR role ignores it.
    """
    prior_players = current_player_week[current_player_week["week"] < target_week]
    prior_team_volume = current_team_week_volume[current_team_week_volume["week"] < target_week]
    prior_passer_week = current_passer_week[current_passer_week["week"] < target_week]

    results: list[RoleShareResult] = []
    for team in sorted(all_teams):
        for role in (ROLE_RB, ROLE_WR):
            results.append(
                _role_share_result_for_team(
                    prior_players, prior_team_volume, prior_passer_week, season, target_week, team, role, k
                )
            )
    return results


def fetch_role_shares(
    current_season: int,
    target_week: int,
    *,
    k: float = DEFAULT_K,
) -> list[RoleShareResult]:
    """Live pull + full pipeline for one season/week: current season's pbp (through whatever weeks
    are already played), aggregated to player-week, team-week-volume, and passer-week grain, then
    identified and blended for `target_week`. `include_participation=False`, matching `nflverse.py`'s
    own live finding that the participation file lags the pbp release for an in-progress season."""
    import nfl_data_py as nfl  # deferred import -- see nflverse.py's identical pattern/rationale

    current_pbp = nfl.import_pbp_data([current_season], include_participation=False)
    player_week = aggregate_player_week(current_pbp)
    team_week_volume = aggregate_team_week_volume(current_pbp)
    passer_week = aggregate_passer_week(current_pbp)
    return compute_role_shares_for_week(
        player_week, team_week_volume, passer_week, current_season, target_week, k=k
    )


# --------------------------------------------------------------------------------------------
# BlowoutVolumeDiscount (PRD Section 6 / ADR-0019 Decision 2) -- pure formula only, see module
# docstring's "Follow-up for BlowoutVolumeDiscount wiring" for what's NOT built here.
# --------------------------------------------------------------------------------------------


def blowout_volume_discount(abs_spread: float) -> float:
    """ADR-0019's banded pregame-spread discount, keyed on `|spread|`. Applies only to a team's
    identified lead RB (any role tier -- see module docstring). Raises `ValueError` for a negative
    input, mirroring `stack_profile.spread_dampener`'s same guard -- callers pass `abs(spread)`."""
    if abs_spread < 0:
        raise ValueError(f"blowout_volume_discount expects a non-negative magnitude, got {abs_spread!r}")
    for upper_bound, discount in BLOWOUT_DISCOUNT_BANDS:
        if abs_spread <= upper_bound:
            return discount
    raise AssertionError("unreachable -- final band's upper bound is +inf")  # pragma: no cover


# --------------------------------------------------------------------------------------------
# Red zone usage (ADR-0022 Round A) -- a red-zone-filtered SIBLING of aggregate_player_week/
# aggregate_team_week_volume above, per the ADR's explicit instruction: "same player-grouping
# logic, just pre-filtered rows." No new data dependency -- this filters the exact same
# `import_pbp_data()` frame this module already consumes; `yardline_100 <= 20` is the standard
# red-zone definition (confirmed live, ADR-0022: yardline_100 is populated and in range 1-99 on
# the same pbp frame this module already pulls).
# --------------------------------------------------------------------------------------------

RED_ZONE_YARDLINE_100_MAX = 20


def red_zone_mask(pbp: pd.DataFrame) -> pd.Series:
    """Boolean mask for red-zone plays (`yardline_100 <= 20`, ADR-0022)."""
    return pbp["yardline_100"] <= RED_ZONE_YARDLINE_100_MAX


def aggregate_team_week_volume_red_zone(pbp: pd.DataFrame, *, season_type: str | None = "REG") -> pd.DataFrame:
    """Red-zone-filtered sibling of `aggregate_team_week_volume` -- a team's red-zone rush
    attempts/targets that week (the denominator for a red-zone SHARE, per ADR-0022's schema note
    "`rz_share_trailing` -- share of team's own RZ volume", not whole-game volume). Identical
    aggregation logic to `aggregate_team_week_volume`, just run against red-zone-only rows."""
    return aggregate_team_week_volume(pbp[red_zone_mask(pbp)], season_type=season_type)


def aggregate_player_week_red_zone(pbp: pd.DataFrame, *, season_type: str | None = "REG") -> pd.DataFrame:
    """Red-zone-filtered sibling of `aggregate_player_week` -- one row per (season, week, team,
    player_id, role) for red-zone plays only, with `share` already computed against the team's
    red-zone volume (via the same internal `aggregate_team_week_volume` call `aggregate_player_week`
    always makes, here operating on the red-zone-filtered frame). Identical aggregation logic to
    `aggregate_player_week`, just run against red-zone-only rows -- per ADR-0022's explicit
    instruction, no new grouping logic."""
    return aggregate_player_week(pbp[red_zone_mask(pbp)], season_type=season_type)


def aggregate_player_trailing_red_zone(
    pbp: pd.DataFrame, target_week: int, *, season_type: str | None = "REG"
) -> pd.DataFrame:
    """Cumulative red-zone volume/share through `target_week - 1` (ADR-0003/ADR-0014 no-look-ahead
    discipline, same trailing grain `RoleShareResult` uses elsewhere in this module) -- one row per
    (team, player_id, player_name, role) with `rz_trailing_volume` (summed red-zone carries/targets
    across completed weeks), `rz_trailing_team_volume` (the team's own cumulative red-zone volume
    over the same window -- carries for the RB role, targets for the WR role, matching
    `aggregate_player_week`'s existing role-to-column convention), and `rz_trailing_share`
    (`rz_trailing_volume / rz_trailing_team_volume`). This is descriptive aggregation only --
    ADR-0022 flags red zone usage as "no shrinkage/prior-blending needed," so there is
    deliberately no shrinkage weight or league-prior blending here, unlike `PlayerRoleShare`.
    Players/teams with zero trailing red-zone volume are simply absent from the output, mirroring
    `_build_candidates`' empty-case handling elsewhere in this module.
    """
    per_week = aggregate_player_week_red_zone(pbp, season_type=season_type)
    prior_players = per_week[per_week["week"] < target_week]
    if prior_players.empty:
        return pd.DataFrame(
            columns=["team", "player_id", "player_name", "role", "rz_trailing_volume", "rz_trailing_team_volume", "rz_trailing_share"]
        )

    team_week = aggregate_team_week_volume_red_zone(pbp, season_type=season_type)
    prior_team = team_week[team_week["week"] < target_week]

    player_totals = (
        prior_players.groupby(["team", "player_id", "player_name", "role"], observed=True, dropna=False)["volume"]
        .sum()
        .reset_index()
        .rename(columns={"volume": "rz_trailing_volume"})
    )
    team_totals = prior_team.groupby("team", observed=True)[["team_rush_attempts", "team_targets"]].sum().reset_index()

    merged = player_totals.merge(team_totals, on="team", how="left")
    merged["rz_trailing_team_volume"] = merged.apply(
        lambda r: r["team_rush_attempts"] if r["role"] == ROLE_RB else r["team_targets"], axis=1
    )
    merged["rz_trailing_share"] = merged["rz_trailing_volume"] / merged["rz_trailing_team_volume"]
    return merged.drop(columns=["team_rush_attempts", "team_targets"])


def adjusted_lead_rb_carry_share(role_share_result: RoleShareResult, pregame_spread: float) -> float | None:
    """`identified_lead_rb.role_share_blended * blowout_volume_discount(|pregame_spread|)` --
    ADR-0019's exact formula. `role_share_result` must be a `"RB"`-role `RoleShareResult` (raises
    `ValueError` otherwise -- there's no WR1 formula, per ADR-0019 Decision 3).

    Returns `None` when no lead RB is identified this week (`gate_passed=False`, or no trailing
    data at all) -- this is the explicit "no identified player this week" fallback, never a guessed
    name or a raw-season-total stand-in. Callers must treat `None` as "apply no volume-share
    adjustment this week for this team," not as 0 or as "use the league prior instead."

    `pregame_spread` may be signed or already absolute -- this function takes `abs()` itself.
    """
    if role_share_result.role != ROLE_RB:
        raise ValueError(
            f"adjusted_lead_rb_carry_share only applies to the RB role, got {role_share_result.role!r} "
            "-- ADR-0019 Decision 2/3: no WR1 BlowoutVolumeDiscount formula is specified."
        )
    if not role_share_result.gate_passed or role_share_result.identified is None:
        return None
    return role_share_result.identified.role_share_blended * blowout_volume_discount(abs(pregame_spread))
