"""Ceiling signal data layer (PRD Section 6's newest construct, ADR-0028).

**Component A now has a real, backtested, both-experts-signed-off live multiplier
(`component_a_multiplier`) -- Components B and C do not.** The original round built only the data
layer: neither the Model Analytics Expert (draft) nor the Fantasy Football Expert (review) would
propose the scale/cap constants needed to turn a z-scored signal into an actual multiplier without
a real outcome backtest (analogous to ADR-0012's own return-TD-rate correction). That backtest has
since been run live for Component A only (`scripts/ceiling_role_share_backtest.py`, 5 real seasons,
20,376 real player-weeks, a player-level log-space regression of actual-DK-points-relative-to-own-
trailing-median against `shrunk_z_score`) and both experts gave explicit, numbered sign-off on the
result -- see the "Update" section of `docs/adr/0028-ceiling-signal-data-layer.md`. Components B and
C remain exactly as before: real, inspectable signals with no live multiplier, still blocked on
their own backtests.

Three signal-producing functions, one per ADR-0028 component:

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

# Component A's real, backtested, both-experts-signed-off scale constants (ADR-0028 Update) --
# fitted via a player-level log-space regression on 5 real seasons / 20,376 real player-weeks,
# NOT an eyeballed or decile-level number. See docs/adr/0028-ceiling-signal-data-layer.md's
# "Update" section for the full calibration record (95% CIs, the Model Analytics Expert's and
# Fantasy Football Expert's explicit sign-off reasoning). Component B/C have no equivalent
# constant yet -- do not extrapolate these values to those components.
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
