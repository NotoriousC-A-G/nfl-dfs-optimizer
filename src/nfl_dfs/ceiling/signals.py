"""Ceiling signal data layer (PRD Section 6's newest construct, ADR-0028).

**Component A now has a real, backtested, both-experts-signed-off live multiplier
(`component_a_multiplier`) -- Components B, C, D, and E do not.** The original round built only the
data layer: neither the Model Analytics Expert (draft) nor the Fantasy Football Expert (review)
would propose the scale/cap constants needed to turn a z-scored signal into an actual multiplier
without a real outcome backtest (analogous to ADR-0012's own return-TD-rate correction). That
backtest has since been run live for Component A only (`scripts/ceiling_role_share_backtest.py`, 5
real seasons, 20,376 real player-weeks, a player-level log-space regression of actual-DK-points-
relative-to-own-trailing-median against `shrunk_z_score`) and both experts gave explicit, numbered
sign-off on the result -- see the "Update" section of `docs/adr/0028-ceiling-signal-data-layer.md`.
Components B, C, D, and E each got their own backtest too, and all four ship nothing live: real,
inspectable signals with no live multiplier, closed out as either a real-but-insufficient/
architecturally-inexpressible finding (B) or a clean, thoroughly-verified null (C, D, E) -- see that
same ADR's Update sections for each. **Both experts explicitly recommend stopping the QB-rushing
line of work at Component E** -- three independent hypotheses (D's two legs, E) all nulled under
this project's strongest available methodology; `ingestion/qb_rushing_profile.py`'s descriptive
dashboard data (ADR-0030) is the permanent, final answer for that axis, not a placeholder.

Five signal-producing functions, one per ADR-0028 component:

- `role_share_ceiling_signals` -- Component A, RB/WR role-share "boom rate" (fraction of trailing
  weeks a player's share spiked above their own trailing median). Built as a boom-rate, not raw
  variance (coefficient of variation), per the Fantasy Football Expert's specific correction to the
  Model Analytics Expert's original draft: a symmetric variance measure can't distinguish a real
  upside spike from `BlowoutVolumeDiscount`-shaped downside benching, and would have misfired
  (read as high-ceiling) for exactly the blowout-prone-offense running back that discount already
  exists to penalize.
- `red_zone_ceiling_signals` -- Component B, the same boom-rate construction applied to red-zone
  share. Extends the Fantasy Football Expert's Component A fix here by direct structural analogy
  (the underlying symmetric-CV concern isn't role-share-specific) -- **not independently reviewed
  by the Fantasy Football Expert**, flagged for the record per ADR-0028.
- `adot_ceiling_signals` -- Component C, WR/TE trailing depth-of-target (aDOT), a LEVEL signal (not
  a boom-rate) per the Model Analytics Expert's original framing -- a receiver who is consistently
  thrown deep has real ceiling regardless of week-to-week volatility in that depth. Population split
  by position label (WR pool, TE pool) is a disclosed degradation from the Fantasy Football Expert's
  requested true alignment split (joker/flex TE pooled with WR, in-line Y kept separate) -- that
  split needs PFF's `slot_coverage` alignment data (ADR-0001), confirmed NOT ingested anywhere in
  this pipeline (`matchup/coverage.py`'s own documented gap), not something this pass invents a
  workaround for.
- `qb_rushing_ceiling_signals` -- Component D (ADR-0030), trailing DESIGNED-RUN COUNT boom-rate for
  identified trailing passers (scrambles explicitly excluded from the predictor -- see this
  function's own section docstring below for the full design rationale). Backtested and closed as
  a clean null (both the designed-run boom-rate primary test and a scramble-rate secondary test) --
  see `docs/adr/0028-ceiling-signal-data-layer.md`'s Component D Update section. Kept in this module
  for traceability/reproducibility, same posture as B/C.
- `qb_explosive_rush_rate_signals` -- Component E (ADR-0028/0030), a LEVEL signal on trailing
  pooled (designed + scramble) explosive-rush rate (rushes clearing `EXPLOSIVE_RUSH_YARDS_THRESHOLD`
  yards), the Fantasy Football Expert's named successor to Component D's null. Backtested and
  closed as a clean null -- see `docs/adr/0028-ceiling-signal-data-layer.md`'s Component E Update
  section. **Both experts explicitly recommend stopping the QB-rushing CeilingMultiplier line of
  work here** -- kept in this module for traceability only.

Every constant below (`BOOM_THRESHOLD`, `MIN_TRAILING_WEEKS`, `CEILING_SHRINKAGE_K`,
`ADOT_MIN_TARGETS`) is an explicit, unvalidated starting placeholder -- named as such in both
experts' review, not derived from backtested data. See `docs/adr/0028-ceiling-signal-data-layer.md`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from nfl_dfs.ingestion.game_environment_stats import blend_toward_prior, shrinkage_weight
from nfl_dfs.ingestion.usage_share import (
    QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS,
    ROLE_RB,
    ROLE_WR,
    _trailing_qb_ids,
    aggregate_passer_week,
    aggregate_player_week,
    aggregate_player_week_red_zone,
    aggregate_team_week_volume_red_zone,
)
from nfl_dfs.normalization.team_aliases import normalize_team

# Every constant here is an explicit, unvalidated placeholder (ADR-0028) -- a starting proposal
# from the Model-Analytics-Expert/Fantasy-Football-Expert review, not backtested fact.
BOOM_THRESHOLD = 1.35
MIN_TRAILING_WEEKS = 3
CEILING_SHRINKAGE_K = 6.0  # borrowed from pace/PROE per ADR-0011's "reuse before inventing"
# convention -- not re-derived for this use, per the Model Analytics Expert's own review.
ADOT_MIN_TARGETS = 8  # deliberately well under usage_share.py's WR_GATE_MIN_VOLUME=20 (a
# "lead receiver" gate built for a different purpose) -- paired with shrinkage, not a hard cutoff.

# ADR-0028/0030 Component D (QB rushing) -- backtested and CLOSED as a clean null (both experts
# signed off, see docs/adr/0028-ceiling-signal-data-layer.md's Component D Update section); no live
# multiplier. Kept for traceability -- reuses ADOT_MIN_TARGETS's value directly per the Model
# Analytics Expert's explicit instruction ("the closest existing precedent for how much repeated
# opportunity before a per-opportunity rate is trustworthy at all"), not a re-derived constant.
QB_DESIGNED_RUN_MIN_TRAILING_VOLUME = 8

# ADR-0028/0030 Component E (QB explosive-rush rate) -- design jointly reviewed by both experts
# following Component D's closure (see qb_explosive_rush_rate_signals' docstring below for the
# full rationale). EXPLOSIVE_RUSH_YARDS_THRESHOLD=15 is the Model-Analytics-Expert-recommended
# primary cut (20+ collapses toward near-binary at realistic pooled trailing attempt counts; 10+
# is run as a required sensitivity check by the BACKTEST script, not shipped as a second production
# constant here). QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME=30 is DERIVED, not asserted-by-analogy (the
# Model Analytics Expert's explicit instruction, after flagging that simply doubling Component D's
# floor would conflate "more raw opportunities" with "a more reliable rate estimate"): solving
# `SE(rate) = sqrt(p*(1-p)/n)` for `n` at a target SE of ~0.05 (5 percentage points) and a plausible
# population explosive-rush rate `p=0.08` (midpoint of the Fantasy Football Expert's estimated 5-12%
# range) gives `n = 0.08*0.92/0.05**2 ≈ 29.4`, rounded up to 30.
EXPLOSIVE_RUSH_YARDS_THRESHOLD = 15
QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME = 30

# Component A's real, backtested, both-experts-signed-off scale constants (ADR-0028 Update) --
# fitted via a player-level log-space regression on 5 real seasons / 20,376 real player-weeks,
# NOT an eyeballed or decile-level number. See docs/adr/0028-ceiling-signal-data-layer.md's
# "Update" section for the full calibration record (95% CIs, the Model Analytics Expert's and
# Fantasy Football Expert's explicit sign-off reasoning). Components B/C/D all closed with no
# equivalent live constant -- do not extrapolate Component A's values to those components.
COMPONENT_A_SCALE: dict[str, float] = {ROLE_RB: 0.1177, ROLE_WR: 0.0798}


@dataclass(frozen=True)
class CeilingSignal:
    """One shrunk, cross-sectionally z-scored ceiling-relevant signal for one player -- not yet a
    multiplier, see module docstring.

    `sample_size` is the axis `shrinkage_weight`'s `n` is computed against for this signal type --
    trailing weeks for the two boom-rate signals, trailing target count for the aDOT signal.
    `raw_value` is the boom rate (0-1) or trailing mean aDOT (yards), signal-type-dependent. `None`
    fields mean this player didn't clear `MIN_TRAILING_WEEKS`/`ADOT_MIN_TARGETS` -- never a
    fabricated zero or an imputed value.
    """

    player_id: str
    player_name: str | None
    team: str
    sample_size: int
    raw_value: float | None
    z_score: float | None
    shrinkage_weight: float | None
    shrunk_z_score: float | None


def _boom_rate_per_player(weekly: pd.DataFrame, *, value_col: str = "share") -> pd.DataFrame:
    """`weekly` must already be filtered to the trailing window and the desired role/pool --
    columns `week, player_id, player_name, team, <value_col>`. One output row per `player_id`:
    `sample_size` (trailing weeks with recorded volume), `raw_value` (boom rate, `None` below
    `MIN_TRAILING_WEEKS`).

    A player whose trailing median is exactly 0 can't be measured against "1.35x of zero" -- for
    those players, ANY week with real (nonzero) involvement counts as a boom relative to their own
    zero baseline, rather than flatly assigning `raw_value=0.0` regardless of how many real spike
    weeks they actually had (Model Analytics Expert's required fix, ADR-0028 Component B
    interpretation round: the flat-0.0 version pinned a large, non-random slice of a sparse-volume
    population -- e.g. red-zone touches, common post-zero-fill -- to zero regardless of real
    boom weeks, exactly the boom/bust players a boom-rate signal exists to catch).
    """
    rows = []
    for player_id, group in weekly.groupby("player_id", observed=True):
        sample_size = len(group)
        player_name = group["player_name"].iloc[0]
        team = group["team"].iloc[-1]  # most recent trailing team, in case of a mid-window trade
        if sample_size < MIN_TRAILING_WEEKS:
            rows.append(
                {"player_id": player_id, "player_name": player_name, "team": team, "sample_size": sample_size, "raw_value": None}
            )
            continue
        median = group[value_col].median()
        if median <= 0:
            boom_weeks = int((group[value_col] > 0).sum())
        else:
            boom_weeks = int((group[value_col] > BOOM_THRESHOLD * median).sum())
        raw_value = boom_weeks / sample_size
        rows.append(
            {"player_id": player_id, "player_name": player_name, "team": team, "sample_size": sample_size, "raw_value": raw_value}
        )
    return pd.DataFrame(rows, columns=["player_id", "player_name", "team", "sample_size", "raw_value"])


def _z_score_and_shrink(df: pd.DataFrame, *, k: float = CEILING_SHRINKAGE_K) -> list[CeilingSignal]:
    """`df` must have `player_id, player_name, team, sample_size, raw_value` (`raw_value` may be
    `None`). Z-scores `raw_value` cross-sectionally against every row in `df` with a real value
    (the caller has already scoped `df` to the right population -- e.g. one role, one week, one
    position pool), then shrinks the z-score toward 0 (neutral) via ADR-0011's shared
    `shrinkage_weight`/`blend_toward_prior` form, reusing `game_environment_stats.py`'s exact
    functions rather than restating the math.
    """
    valid = df[df["raw_value"].notna()]
    if len(valid) >= 2 and valid["raw_value"].std() > 0:
        pop_mean = valid["raw_value"].mean()
        pop_std = valid["raw_value"].std()
    else:
        pop_mean = pop_std = None

    signals = []
    for row in df.itertuples(index=False):
        # A gated-out raw_value can arrive here as either Python None or pandas/numpy NaN
        # (assigning None into a float64 column silently becomes NaN) -- pd.isna() catches both,
        # `is None` alone would miss the NaN case and let a "None" value leak through as a real
        # float, corrupting every downstream check that only compares against None.
        raw_value = None if pd.isna(row.raw_value) else row.raw_value
        if raw_value is None or pop_std is None:
            z_score = None
        else:
            z_score = (raw_value - pop_mean) / pop_std
        weight = shrinkage_weight(row.sample_size, k) if z_score is not None else None
        shrunk = blend_toward_prior(z_score, 0.0, weight) if z_score is not None else None
        signals.append(
            CeilingSignal(
                player_id=row.player_id,
                player_name=row.player_name,
                team=row.team,
                sample_size=int(row.sample_size),
                raw_value=raw_value,
                z_score=z_score,
                shrinkage_weight=weight,
                shrunk_z_score=shrunk,
            )
        )
    return signals


def _exclude_trailing_qbs(weekly: pd.DataFrame, pbp: pd.DataFrame, target_week: int, *, season_type: str | None) -> pd.DataFrame:
    """Same exclusion this project already trusts for `RoleShare` (ADR-0020 Decision 1c,
    `usage_share.py`'s `_trailing_qb_ids`) -- a mobile QB's scramble rows land in the raw RB-role
    data too (no position filter is applied anywhere in `aggregate_player_week`), and this reuses
    the established trailing-pass-attempt heuristic rather than inventing a second exclusion
    mechanism.
    """
    passer_week = aggregate_passer_week(pbp, season_type=season_type)
    prior_passer_week = passer_week[passer_week["week"] < target_week]
    excluded_ids: set[str] = set()
    for team in weekly["team"].unique():
        excluded_ids |= _trailing_qb_ids(prior_passer_week, team)
    return weekly[~weekly["player_id"].isin(excluded_ids)]


def role_share_ceiling_signals(
    pbp: pd.DataFrame, target_week: int, role: str, *, season_type: str | None = "REG"
) -> list[CeilingSignal]:
    """ADR-0028 Component A: role-share boom-rate ceiling signal, RB or WR only. RB role excludes
    QB scramblers (see `_exclude_trailing_qbs`)."""
    if role not in (ROLE_RB, ROLE_WR):
        raise ValueError(f"role_share_ceiling_signals only supports {ROLE_RB!r}/{ROLE_WR!r}, got {role!r}")
    weekly = aggregate_player_week(pbp, season_type=season_type)
    weekly = weekly[(weekly["week"] < target_week) & (weekly["role"] == role)]
    if role == ROLE_RB:
        weekly = _exclude_trailing_qbs(weekly, pbp, target_week, season_type=season_type)
    boom = _boom_rate_per_player(weekly)
    return _z_score_and_shrink(boom)


def component_a_multiplier(signal: CeilingSignal, role: str) -> float | None:
    """The real, backtested, both-experts-signed-off Component A live multiplier (ADR-0028 Update):
    `m_i = max(1.0, exp(scale_i * shrunk_z_score))`, `scale_i` from `COMPONENT_A_SCALE`. One-sided
    by design (Model Analytics Expert's draft, Fantasy Football Expert's sign-off) -- a below-
    average signal never lowers a player's read, it just contributes no ceiling credit.

    Returns `None`, never a fabricated neutral `1.0`, when `signal.shrunk_z_score is None` (this
    player didn't clear `MIN_TRAILING_WEEKS`) -- this project's established "unknown is not the
    same as neutral" discipline (the same requirement the Fantasy Football Expert set for QB's
    still-uncalibrated ceiling read).
    """
    if role not in COMPONENT_A_SCALE:
        raise ValueError(f"component_a_multiplier only supports {sorted(COMPONENT_A_SCALE)}, got {role!r}")
    if signal.shrunk_z_score is None:
        return None
    return max(1.0, math.exp(COMPONENT_A_SCALE[role] * signal.shrunk_z_score))


def _zero_fill_red_zone_weekly(
    pbp: pd.DataFrame, red_zone_weekly: pd.DataFrame, role: str, *, season_type: str | None
) -> pd.DataFrame:
    """Fantasy Football Expert's required fix before any Component B backtest (ADR-0028): a player
    who recorded overall volume that week (a real, active candidate for the role) but zero
    red-zone volume, on a team that DID have red-zone plays that week, is a real `share=0.0`
    "red-zone shutout" observation -- not the same thing as a week the team never reached the red
    zone at all, which correctly stays excluded either way. Without this,
    `aggregate_player_week_red_zone`'s own "absent row = zero volume" convention silently drops
    exactly the bust weeks a boom-rate statistic needs to see to be meaningful, inflating boom rate
    for the boom/bust, opportunity-dependent red-zone role players this component exists to catch.
    """
    overall = aggregate_player_week(pbp, season_type=season_type)
    overall = overall[overall["role"] == role][["season", "week", "team", "player_id", "player_name"]]

    team_rz_volume = aggregate_team_week_volume_red_zone(pbp, season_type=season_type)
    volume_col = "team_rush_attempts" if role == ROLE_RB else "team_targets"
    team_rz_volume_lookup = {
        (row.season, row.week, row.team): getattr(row, volume_col)
        for row in team_rz_volume.itertuples(index=False)
        if getattr(row, volume_col) > 0
    }

    existing_keys = set(red_zone_weekly[["season", "week", "player_id"]].itertuples(index=False, name=None))

    zero_rows = []
    for row in overall.itertuples(index=False):
        team_volume = team_rz_volume_lookup.get((row.season, row.week, row.team))
        if team_volume is None:  # team had no red-zone plays this role cares about that week
            continue
        if (row.season, row.week, row.player_id) in existing_keys:
            continue
        zero_rows.append(
            {
                "season": row.season, "week": row.week, "team": row.team,
                "player_id": row.player_id, "player_name": row.player_name, "role": role,
                "volume": 0, "team_volume": team_volume, "share": 0.0,
            }
        )
    if not zero_rows:
        return red_zone_weekly
    return pd.concat([red_zone_weekly, pd.DataFrame(zero_rows)], ignore_index=True)


def red_zone_ceiling_signals(
    pbp: pd.DataFrame, target_week: int, role: str, *, season_type: str | None = "REG"
) -> list[CeilingSignal]:
    """ADR-0028 Component B: red-zone-share boom-rate ceiling signal, RB or WR/pass-catcher pool.
    Zero-fills real "red-zone shutout" weeks via `_zero_fill_red_zone_weekly` (Fantasy Football
    Expert's required design fix) before computing boom rate.

    **Known, disclosed limitation, not fixed by the zero-fill above:** this pipeline's red-zone
    aggregation (same as role-share) buckets every pass-catcher -- WR and TE alike -- into one
    `ROLE_WR`-labeled pool; there is no position split, so a TE's signal is z-scored against the
    combined WR+TE population, not a TE-specific one. Splitting this would need the same position
    join `adot_ceiling_signals` takes from its caller -- a real, separate follow-up (the Fantasy
    Football Expert flagged this as a hard blocker before any TE-specific live multiplier ships,
    not before the RB/WR design itself), not solved here.
    """
    if role not in (ROLE_RB, ROLE_WR):
        raise ValueError(f"red_zone_ceiling_signals only supports {ROLE_RB!r}/{ROLE_WR!r}, got {role!r}")
    weekly = aggregate_player_week_red_zone(pbp, season_type=season_type)
    weekly = weekly[weekly["role"] == role]
    weekly = _zero_fill_red_zone_weekly(pbp, weekly, role, season_type=season_type)
    weekly = weekly[weekly["week"] < target_week]
    if role == ROLE_RB:
        weekly = _exclude_trailing_qbs(weekly, pbp, target_week, season_type=season_type)
    boom = _boom_rate_per_player(weekly)
    return _z_score_and_shrink(boom)


# --------------------------------------------------------------------------------------------
# ADR-0036: WR red-zone role-security discount -- a standalone, downside-only construct
# expressing Component B's real, validated NEGATIVE WR finding above (`red_zone_ceiling_signals`,
# `role=ROLE_WR`) -- architecturally NOT part of `CeilingMultiplier` (that formula is one-sided,
# floored at 1.0 FROM BELOW, so a negative `scale_i` just floors back to 1.0 and does nothing;
# both experts confirmed this at Component B's original closure, ADR-0028). This construct is the
# floored-from-ABOVE mirror image instead -- can only ever discount a projection or leave it
# unchanged, never raise it.
#
# Design (both experts' joint review, fully signed off, ADR-0036):
# - Banded discrete tiers, not a continuous exp(scale*z) curve -- mirrors `usage_share.py`'s
#   `BlowoutVolumeDiscount` shape (this project's only prior downside-discount precedent) rather
#   than `CeilingMultiplier`'s shape, specifically because a downside construct has no
#   self-limiting floor the way Component A's upside floor makes an overconfident scale cheap to
#   be wrong about -- a banded cap bounds the worst-case single-step error.
# - Damped to roughly HALF the fitted slope (-0.0784 -> -0.04) on first ship, following
#   `BlowoutVolumeDiscount`'s own "halve and round to a clean number" precedent for a first-of-
#   its-kind downside construct with no live track record yet -- not the raw backtested effect.
# - `shrunk_z_score <= 0` gets NO discount (1.00) -- one-sided by construction: this only fires on
#   the *elevated*-red-zone-share side (the football mechanism, a single-game matchup exploit that
#   gets scouted and shut down the following week, has no analog for a player at/below baseline).
# - Both bands anchored to real, actually-occupied z-values (1.0 and 1.5), not arbitrary or
#   rarely-hit tail values -- `shrunk_z_score >= 1.5` was already established (Component A's own
#   docstring/backtest) as "structurally hard to reach" given this project's shrinkage math
#   (`CEILING_SHRINKAGE_K=6.0`, `MIN_TRAILING_WEEKS=3`), so 1.5 is the real practical ceiling, not
#   an understated extreme.
# - Required joint-composition check (both signals fit together: `log(relative_performance) ~
#   a_shrunk_z + b_shrunk_z + a_shrunk_z*b_shrunk_z`, WR only): both components retained ~92-97%
#   of their univariate effect size and the interaction term did NOT clear zero -- confirms
#   multiplicative composition with `component_a_multiplier` is statistically safe, no
#   cross-term correction needed (`composition/player_detail.py`'s `ceiling_projection` property
#   multiplies both together for exactly this reason).
# - WR ONLY, explicitly -- never extended to TE by analogy (Component B's own red-zone-share pool
#   combines WR+TE with no position split, a disclosed limitation `red_zone_ceiling_signals`
#   already carries; TE was never independently backtested for this specific negative-relationship
#   claim, and both experts required it stay WR-gated rather than repeat the exact "extended from
#   a sibling role by analogy, never independently reviewed" shortcut that necessitated Component
#   B's own original independent review in the first place).
# --------------------------------------------------------------------------------------------

WR_RED_ZONE_ROLE_SECURITY_DISCOUNT_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 1.00),  # shrunk_z_score <= 0 -- no discount at or below a player's own trailing baseline
    (1.0, 0.96),  # 0 < shrunk_z_score <= 1.0 -- exp(-0.04 * 1.0), a real but mild elevation
    (float("inf"), 0.94),  # shrunk_z_score > 1.0 -- exp(-0.04 * 1.5), anchored at the practical ceiling
)


def wr_red_zone_role_security_discount(signal: CeilingSignal) -> float | None:
    """The real, backtested, both-experts-signed-off WR red-zone role-security discount (ADR-0036):
    a banded, downside-only multiplier on `signal.shrunk_z_score` (from `red_zone_ceiling_signals`,
    `role=ROLE_WR`) -- see module section docstring above for the full design/calibration record.

    Returns `None`, never a fabricated neutral `1.0`, when `signal.shrunk_z_score is None` (this
    player didn't clear `MIN_TRAILING_WEEKS`) -- this project's established "unknown is not the
    same as neutral" discipline, same as `component_a_multiplier`. A negative `shrunk_z_score` is a
    real, valid, EXPECTED input here (a genuine cross-sectional z-score, never `abs()`-transformed)
    and correctly resolves to the `1.00` (no-discount) band -- this function does not raise on a
    negative input the way `blowout_volume_discount` does for its own, differently-shaped `abs_spread`
    argument.
    """
    if signal.shrunk_z_score is None:
        return None
    z = signal.shrunk_z_score
    for upper_bound, discount in WR_RED_ZONE_ROLE_SECURITY_DISCOUNT_BANDS:
        if z <= upper_bound:
            return discount
    raise AssertionError("unreachable -- final band's upper bound is +inf")  # pragma: no cover


def trailing_red_zone_share_by_week(
    pbp: pd.DataFrame, target_week: int, role: str, *, season_type: str | None = "REG"
) -> dict[str, list[tuple[int, float]]]:
    """Real, descriptive (not predictive) per-week trailing red-zone share sequence, `{player_id:
    [(week, share), ...]}` ordered by week -- ADR-0029's "give the person the real inputs, not a
    black-box score" posture, applied to Component B's real, bug-fixed data (the same zero-fill-
    corrected weekly rows `red_zone_ceiling_signals` uses for its own now-shelved boom-rate
    calibration) without collapsing them into a single summary statistic. A raw week-by-week
    sequence like `[(1, 0.6), (2, 0.0), (3, 1.0)]` shows the actual consistency-vs-spikiness
    pattern the Fantasy Football Expert's Component B review named directly, in a way a single
    boom-rate number can't -- same RB/WR role scope and zero-fill/QB-exclusion treatment as
    `red_zone_ceiling_signals`.
    """
    if role not in (ROLE_RB, ROLE_WR):
        raise ValueError(f"trailing_red_zone_share_by_week only supports {ROLE_RB!r}/{ROLE_WR!r}, got {role!r}")
    weekly = aggregate_player_week_red_zone(pbp, season_type=season_type)
    weekly = weekly[weekly["role"] == role]
    weekly = _zero_fill_red_zone_weekly(pbp, weekly, role, season_type=season_type)
    weekly = weekly[weekly["week"] < target_week]
    if role == ROLE_RB:
        weekly = _exclude_trailing_qbs(weekly, pbp, target_week, season_type=season_type)

    result: dict[str, list[tuple[int, float]]] = {}
    for player_id, group in weekly.groupby("player_id", observed=True):
        pairs = sorted(zip(group["week"], group["share"]), key=lambda pair: pair[0])
        result[player_id] = [(int(week), float(share)) for week, share in pairs]
    return result


def _aggregate_trailing_adot(pbp: pd.DataFrame, target_week: int, *, season_type: str | None = "REG") -> pd.DataFrame:
    """Trailing (`week < target_week`) mean `air_yards` per target, one row per receiver --
    `air_yards` rides the same `import_pbp_data()` pull `usage_share.py` already consumes for
    `receiver_player_id` (confirmed present, Phase 0), no new ingestion."""
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]
    trailing = df[
        (df["week"] < target_week)
        & (df["play_type"] == "pass")
        & df["receiver_player_id"].notna()
        & df["air_yards"].notna()
    ]
    agg = (
        trailing.groupby(["posteam", "receiver_player_id"], observed=True)
        .agg(
            sample_size=("play_id", "count"),
            raw_value=("air_yards", "mean"),
            player_name=("receiver_player_name", "first"),
        )
        .reset_index()
        .rename(columns={"posteam": "team", "receiver_player_id": "player_id"})
    )
    agg["team"] = agg["team"].map(lambda t: normalize_team("nflverse_schedule", t))
    return agg


def adot_ceiling_signals(
    pbp: pd.DataFrame, target_week: int, position_by_player_id: dict[str, str], *, season_type: str | None = "REG"
) -> dict[str, list[CeilingSignal]]:
    """ADR-0028 Component C: trailing depth-of-target ceiling signal, WR and TE, split into
    separate position populations (see module docstring for why this is a position-label split,
    not the Fantasy Football Expert's requested true alignment split). `position_by_player_id`
    must be supplied by the caller (e.g. from the reconciled `PlayerIdentity` pool's own
    `position` field) -- this pbp-derived aggregation carries no position field for receivers.

    Volume handling: a low floor (`ADOT_MIN_TARGETS`) PAIRED WITH shrinkage toward the position
    population's own mean, per the Fantasy Football Expert's explicit correction to the original
    draft -- never a hard exclusionary gate, so a thin-but-real low-target-share deep-threat
    sample gets dampened, not thrown away.
    """
    trailing = _aggregate_trailing_adot(pbp, target_week, season_type=season_type)
    trailing = trailing.copy()
    # Below the volume floor: keep the row (never silently drop a player from the output), just
    # null the value -- same "gate nulls the value, never omits the row" discipline
    # `_boom_rate_per_player` uses for MIN_TRAILING_WEEKS above.
    trailing.loc[trailing["sample_size"] < ADOT_MIN_TARGETS, "raw_value"] = None
    trailing["position"] = trailing["player_id"].map(position_by_player_id)

    results: dict[str, list[CeilingSignal]] = {}
    for position in ("WR", "TE"):
        pool = trailing[trailing["position"] == position][["player_id", "player_name", "team", "sample_size", "raw_value"]]
        results[position] = _z_score_and_shrink(pool)
    return results


# --------------------------------------------------------------------------------------------
# ADR-0028/0030 Component D (QB rushing) -- BACKTESTED AND CLOSED AS A CLEAN NULL, no live
# multiplier. Kept in this module for traceability/reproducibility, same posture as B/C. Full
# backtest result + both experts' interpretation: docs/adr/0028-ceiling-signal-data-layer.md's
# "Update (2026-09-14): Component D (QB rushing)" section.
#
# Design jointly reviewed and conditionally signed off by both experts (docs/adr/0030-qb-rushing-
# opportunity-profile.md's "Update" section) before this was written, per this project's standing
# design-before-backtest discipline:
#
# - raw_value = boom-rate on trailing DESIGNED-RUN COUNT only (`qb_scramble == 0`), never
#   scrambles or rushing points -- Model Analytics Expert's explicit call: designed runs are the
#   scheme-driven, plausibly-persistent part of a QB's rushing role (the same "opportunity, not
#   outcome" logic Component A already applies to role share), while scramble count over a short
#   trailing window is dominated by pass-rush/pressure noise, too unrepeatable to safely gate an
#   ungated-upside multiplier on. Rushing POINTS were rejected outright as a raw_value candidate
#   for smuggling the outcome's own noisiest component back in as the predictor.
# - Fantasy Football Expert's conditional sign-off requires two things the BACKTEST script (not
#   this signal function) must run before any live-ship decision: (1) scramble RATE as an
#   equally-rigorous secondary test (scrambles are a real, persistent driver of QB rushing
#   ceiling for the Lamar Jackson/Hurts archetype -- excluding them from the gate is not the same
#   as concluding they contribute nothing), (2) a goal-line-share diagnostic split on this same
#   designed-run population (does the signal hold for open-field rushers, or is it diluted by
#   short-yardage sneak specialists whose designed runs are a level/floor stat, not a boom stat --
#   the exact level-vs-variance distinction ADR-0028 already drew for Component B).
# - Population gate: QB_DESIGNED_RUN_MIN_TRAILING_VOLUME=8 total trailing designed runs, applied
#   as a raw_value-nulling gate (never a dropped row) -- which, per `_z_score_and_shrink`'s own
#   "population stats computed only from non-null raw_value rows" behavior, ALSO removes a
#   structurally-near-zero pocket passer from the cross-sectional z-scoring reference population,
#   exactly the Model Analytics Expert's explicit second requirement (a reference distribution
#   dominated by pure pocket passers who essentially never design-run would distort every real
#   rusher's z-score).
# --------------------------------------------------------------------------------------------


def _aggregate_qb_designed_runs_weekly(pbp: pd.DataFrame, *, season_type: str | None = "REG") -> pd.DataFrame:
    """One row per (season, week, team, player_id) for every week a real passer had recorded pass
    attempts that week (`aggregate_passer_week`'s `pass_attempts >= 1`, i.e. this QB actually
    played that week) -- `designed_runs` (`play_type == "run"` with `qb_scramble == 0`) zero-filled
    for a real week where this QB had zero designed runs, same "a played week is a real
    observation, never a fabricated shutout OR a silently-dropped one" discipline
    `_zero_fill_red_zone_weekly` established for the team-reached-the-red-zone precondition --
    simpler here since a QB who played always had the standing opportunity to be called a designed
    run (no team-level precondition to check, unlike red zone).
    """
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]

    passer_names = (
        df[df["passer_player_id"].notna()]
        .groupby("passer_player_id", observed=True)["passer_player_name"]
        .first()
    )
    passer_week = aggregate_passer_week(df, season_type=season_type)
    played = passer_week[passer_week["pass_attempts"] >= 1][["season", "week", "team", "player_id"]].copy()
    played["player_name"] = played["player_id"].map(passer_names)

    rushes = df[(df["play_type"] == "run") & df["rusher_player_id"].notna() & (df["qb_scramble"] == 0)]
    designed = (
        rushes.groupby(["season", "week", "posteam", "rusher_player_id"], observed=True)
        .size()
        .rename("designed_runs")
        .reset_index()
        .rename(columns={"posteam": "team", "rusher_player_id": "player_id"})
    )
    designed["team"] = designed["team"].map(lambda t: normalize_team("nflverse_schedule", t))

    merged = played.merge(designed, on=["season", "week", "team", "player_id"], how="left")
    merged["designed_runs"] = merged["designed_runs"].fillna(0).astype(int)
    return merged


def qb_rushing_ceiling_signals(pbp: pd.DataFrame, target_week: int, *, season_type: str | None = "REG") -> list[CeilingSignal]:
    """ADR-0028/0030 Component D: boom-rate on trailing designed-run count (see module section
    docstring above for the full design rationale/sign-off). **BACKTESTED AND CLOSED AS A CLEAN
    NULL -- no `component_a_multiplier`-style live multiplier exists or will be added for this.**
    Kept for traceability/reproducibility (`scripts/ceiling_qb_rushing_backtest.py` still consumes
    it) -- see `docs/adr/0028-ceiling-signal-data-layer.md`'s Component D Update section for the
    full backtest record and both experts' sign-off on closing it out.
    """
    weekly = _aggregate_qb_designed_runs_weekly(pbp, season_type=season_type)
    weekly = weekly[weekly["week"] < target_week]
    boom = _boom_rate_per_player(weekly, value_col="designed_runs")

    trailing_volume = weekly.groupby("player_id", observed=True)["designed_runs"].sum()
    boom["_trailing_volume"] = boom["player_id"].map(trailing_volume).fillna(0)
    boom.loc[boom["_trailing_volume"] < QB_DESIGNED_RUN_MIN_TRAILING_VOLUME, "raw_value"] = None
    boom = boom.drop(columns=["_trailing_volume"])
    return _z_score_and_shrink(boom)


# --------------------------------------------------------------------------------------------
# ADR-0028/0030 Component E (QB explosive-rush rate) -- BACKTESTED AND CLOSED AS A CLEAN NULL, no
# live multiplier, both experts explicitly recommending the QB-rushing CeilingMultiplier line of
# work stop here (three independent hypotheses -- Component D's two legs, this one -- all nulled).
# Full backtest result + both experts' interpretation: docs/adr/0028-ceiling-signal-data-layer.md's
# "Update (2026-09-14): Component E (QB explosive-rush rate)" section. Kept in this module for
# traceability/reproducibility only.
#
# Design jointly reviewed by both experts following Component D's closure, before any backtest
# code was written, per this project's standing design-before-backtest discipline:
#
# - A LEVEL signal (trailing pooled explosive-rush rate, z-scored + shrunk the same way
#   `adot_ceiling_signals`/Component D's scramble-rate secondary test were built), explicitly NOT
#   a week-to-week boom-rate comparison. The Model Analytics Expert's reasoning, confirmed by the
#   Fantasy Football Expert (who originally proposed this construct and clarified their own
#   ambiguous "boom-shaped statistic" phrasing meant this): QB rush volume is too low (3-8 attempts
#   a week) for a per-week boom-rate on an already-rare threshold event to mean anything --
#   stacking two layers of thresholding (a rare per-week rate, boom-compared against its own
#   trailing median) would compound, not fix, Component D's exact quantization failure. Pooling the
#   whole trailing window into one rate (rather than binning by week) is what makes this axis usable
#   at all, the same reason Component C's low-floor-plus-shrinkage approach beat a hard weekly gate.
# - Pooled (designed + scramble) rush attempts, NOT designed-run-only -- DK scoring doesn't care
#   whether a given rush originated as a called run or a broken-pocket improvisation (Fantasy
#   Football Expert's explicit reasoning), so the denominator here is every rush attempt by an
#   identified trailing passer, not the Component D-style designed-only population.
# - `EXPLOSIVE_RUSH_YARDS_THRESHOLD=15` (not 20+, which the Model Analytics Expert flagged would
#   collapse toward a near-binary indicator at realistic pooled trailing attempt counts) --
#   `scripts/ceiling_qb_explosive_rush_backtest.py` runs a required 10+ yard sensitivity check
#   alongside the 15+ primary, not shipped as a second production constant here.
# - `QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME=30`, DERIVED from a target standard error (see the
#   constant's own comment above), not asserted by simply doubling Component D's floor -- applied
#   as a raw_value-nulling gate that also removes near-zero-rushing pocket passers from the
#   cross-sectional z-scoring reference population, same mechanism Component D introduced.
# - **Both experts explicitly flagged this construct sits on shakier "opportunity, not outcome"
#   ground than Component D did** -- Component D's designed-run count was scheme-visible before the
#   snap; whether a given run clears 15 yards is partly opportunity (blocking, space) and partly
#   pure single-play outcome variance (a missed tackle, a lucky bounce), the same category of noise
#   rushing points/yards were rejected as a Component D `raw_value` candidate for smuggling back in.
#   Both experts accepted this as a conscious, disclosed tradeoff, not an inherited exemption from
#   Component D's own rejection logic -- and the Fantasy Football Expert set an explicit prior going
#   in (not just after the fact, the way Component D's quantization risk was only found reactively):
#   given scramble rate already nulled with a sign flip, and scrambles are football-plausibly more
#   likely than designed runs to produce a long gain (broken-pocket improvisation vs. a blocked,
#   defined running lane), this construct is at real risk of simply re-deriving the already-nulled
#   scramble-rate finding through a more outcome-adjacent lens, not finding something independent.
# - Required diagnostics (Fantasy Football Expert, run by the backtest script, not this function):
#   a scramble-SHARE tercile split AND a regression residualized against scramble share as a
#   continuous covariate (the tercile split alone could miss the confound a continuous control
#   catches, per their explicit request); a goal-line-share diagnostic split carried forward from
#   Component D, now correcting a structural confound rather than a role-conflation risk --
#   `yardline_100` mechanically caps how long a run near the goal line CAN be, so a short-yardage/
#   goal-line-specialist QB would show a mechanically DEPRESSED explosive-rate for a structural
#   reason having nothing to do with real explosiveness (the reverse direction of Component D's own
#   goal-line concern, which risked inflating a level read rather than deflating a rate read).
# --------------------------------------------------------------------------------------------


def _aggregate_trailing_qb_explosive_rush(
    pbp: pd.DataFrame, target_week: int, *, season_type: str | None = "REG", yards_threshold: int = EXPLOSIVE_RUSH_YARDS_THRESHOLD
) -> pd.DataFrame:
    """Trailing (`week < target_week`) pooled (designed + scramble) rush-attempt explosive rate,
    one row per (team, player_id) for every identified trailing passer (`usage_share.py`'s existing
    `>= QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS` convention, reused directly rather than a second
    identification mechanism). `sample_size` is the pooled trailing rush-attempt count (the
    denominator `raw_value` itself is computed against, matching `ADOT_MIN_TARGETS`'s own "gate on
    the signal's own denominator" convention); `raw_value` is the trailing explosive-rush rate
    (rushes with `rushing_yards >= yards_threshold`, divided by `sample_size`). `yards_threshold`
    is exposed as a parameter (not hardcoded) specifically so the backtest script's required 10+
    yard sensitivity check can reuse this same aggregation rather than a duplicated one.
    """
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]
    trailing = df[df["week"] < target_week]

    passer_week = aggregate_passer_week(df, season_type=season_type)
    prior_passer_week = passer_week[passer_week["week"] < target_week]
    qb_totals = prior_passer_week.groupby("player_id", observed=True)["pass_attempts"].sum()
    qb_ids = set(qb_totals[qb_totals >= QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS].index)

    rushes = trailing[
        (trailing["play_type"] == "run") & trailing["rusher_player_id"].notna() & trailing["rusher_player_id"].isin(qb_ids)
    ]
    agg = (
        rushes.groupby(["posteam", "rusher_player_id"], observed=True)
        .agg(
            sample_size=("play_id", "count"),
            explosive=("rushing_yards", lambda s: int((s >= yards_threshold).sum())),
            player_name=("rusher_player_name", "first"),
        )
        .reset_index()
        .rename(columns={"posteam": "team", "rusher_player_id": "player_id"})
    )
    agg["team"] = agg["team"].map(lambda t: normalize_team("nflverse_schedule", t))
    agg["raw_value"] = agg["explosive"] / agg["sample_size"]
    return agg.drop(columns=["explosive"])


def qb_explosive_rush_rate_signals(
    pbp: pd.DataFrame, target_week: int, *, season_type: str | None = "REG", yards_threshold: int = EXPLOSIVE_RUSH_YARDS_THRESHOLD
) -> list[CeilingSignal]:
    """ADR-0028/0030 Component E: trailing explosive-rush-rate LEVEL signal (see module section
    docstring above for the full design rationale/sign-off). **BACKTESTED AND CLOSED AS A CLEAN
    NULL -- no `component_a_multiplier`-style live multiplier exists or will be added for this.**
    Kept for traceability/reproducibility (`scripts/ceiling_qb_explosive_rush_backtest.py` still
    consumes it) -- see `docs/adr/0028-ceiling-signal-data-layer.md`'s Component E Update section
    for the full backtest record and both experts' sign-off on closing it (and the whole
    QB-rushing CeilingMultiplier line of work) out.
    """
    trailing = _aggregate_trailing_qb_explosive_rush(pbp, target_week, season_type=season_type, yards_threshold=yards_threshold)
    trailing = trailing.copy()
    trailing.loc[trailing["sample_size"] < QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME, "raw_value"] = None
    pool = trailing[["player_id", "player_name", "team", "sample_size", "raw_value"]]
    return _z_score_and_shrink(pool)
