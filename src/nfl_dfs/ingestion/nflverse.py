"""nflverse pace/PROE ingestion for `GameEnvironmentScore`'s pace (22.2%) and PROE (22.2%)
components (PRD Section 6; population/window spec ADR-0003; shrinkage form ADR-0011).

## PROE: `xpass`/`pass_oe` checked live, not assumed -- the standard computation applies

Before building anything, this module's first job (per the task brief) was checking live whether
`nfl_data_py.import_pbp_data()` already carries an expected-pass-probability column. **It does.**
A live pull of 2025 and 2026 play-by-play (`nfl.import_pbp_data([season], include_participation=
False)`) has both `xpass` (nflfastR's model-estimated pass probability for the play, continuous,
observed range ~0.01-0.99) and `pass_oe` (`= 100 * (pass - xpass)`, i.e. pass-over-expected
already computed per play, on a plus/minus-100 percentage-point scale). Team-week aggregates
(`mean(pass_oe)` across a team's situation-neutral plays that week) land in a sane, expected range
(2025 wk1 sample: team-week PROE from -33.6 to +18.1, std ~7.7) -- this is the standard, defensible
PROE computation (actual pass rate minus the mean of a model's expected pass rate for those same
plays), not something invented for this project. **No new expected-pass-rate model was built or
needed** -- the task's own fallback instruction (don't invent a down/distance/score-bucketed
league-average model without Architect/Model Analytics Expert review) doesn't apply here because
it wasn't necessary. `pass_oe` is used directly (mathematically identical to computing `100 *
(actual_team_pass_rate - mean(xpass))` over the same play set, just already done by nflfastR).

## Pace: "situation-neutral" filter -- a mechanical play-selection choice, not a new model

Pace is defined (PRD Section 6) as "situation-neutral plays-per-game." `import_pbp_data()` has no
single "is this garbage time" flag, but does have the fields needed to build one: `wp` (pre-snap
home-team win probability) and `half_seconds_remaining`. This module filters to:

- `play_type in {"pass", "run"}` -- excludes kickoffs, punts, FG/XP tries, no-plays, kneels,
  spikes (none of these are "plays" in the pace sense the PRD means).
- `0.15 <= wp <= 0.85` -- excludes plays where the game is already functionally decided (the
  standard-ish neutral-script band used across public NFL analytics; ADR-0003/ADR-0007 don't
  specify this themselves, so this is this module's own mechanical filter choice).
- `half_seconds_remaining > 120` -- excludes the final two minutes of each half, where hurry-up
  (inflates raw play count) and clock-killing (deflates it) both distort "pace" away from a
  team's true situation-neutral tempo, independent of the `wp` band.

This is a play-*selection* filter, not a statistical model of anything -- it doesn't predict or
estimate a value the way an invented expected-pass-rate model would. Still flagging it explicitly
per the task's instruction to state what filter was used and why, since the exact `wp`/
`half_seconds_remaining` thresholds are this module's own choice, not sourced from an ADR, and
the Architect/Model Analytics Expert should be able to revisit them like every other Section 6
threshold.

## Z-score population/window (ADR-0003) and shrinkage (ADR-0011)

Implemented via `game_environment_stats.py` (shared with `odds_api.py`): cross-sectional per
week, all 32 teams, a team's blended value at week `W` uses only completed weeks `1..W-1`, blended
toward that team's simple-average 2024/2025 baseline with `w(n) = n / (n + 6)` before z-scoring
(`n` = weeks played through `W-1`).
"""

from __future__ import annotations

import pandas as pd

from nfl_dfs.ingestion.game_environment_stats import (
    blend_toward_prior,
    cross_sectional_zscore,
    shrinkage_weight,
)
from nfl_dfs.normalization.team_aliases import CANONICAL_TEAMS, normalize_team

NEUTRAL_PLAY_TYPES = {"pass", "run"}
NEUTRAL_WP_LOWER = 0.15
NEUTRAL_WP_UPPER = 0.85
NEUTRAL_MIN_HALF_SECONDS_REMAINING = 120

DEFAULT_K = 6.0  # ADR-0011's pace/PROE k, matching ADR-0003's original weeks_played=6 reference point
DEFAULT_PRIOR_SEASONS: tuple[int, ...] = (2024, 2025)


def situation_neutral_mask(pbp: pd.DataFrame) -> pd.Series:
    """Boolean mask onto a raw `import_pbp_data()` frame -- see module docstring for the filter
    and rationale. Also drops rows with a null `xpass`/`pass_oe` (observed live: ~0.4% of
    pass/run plays, e.g. plays nflfastR's win-probability/expected-pass models can't score) so
    downstream aggregation never silently averages over a missing value as if it were 0."""
    return (
        pbp["play_type"].isin(NEUTRAL_PLAY_TYPES)
        & pbp["wp"].between(NEUTRAL_WP_LOWER, NEUTRAL_WP_UPPER)
        & (pbp["half_seconds_remaining"] > NEUTRAL_MIN_HALF_SECONDS_REMAINING)
        & pbp["xpass"].notna()
        & pbp["pass_oe"].notna()
    )


def aggregate_team_week(pbp: pd.DataFrame, *, season_type: str | None = "REG") -> pd.DataFrame:
    """Pure aggregation: raw play-by-play -> one row per (season, week, team) with `neutral_plays`
    (situation-neutral play count that week -- the pace metric) and `proe` (mean `pass_oe` over
    those same plays, percentage-point scale). `season_type="REG"` (default) excludes postseason,
    since GameEnvironmentScore is a weekly-slate tool that only runs during the regular season;
    pass `None` to keep every `season_type` (e.g. for a prior-season baseline that should include
    playoff games too -- callers decide, not this function).

    Team codes are normalized to the DK canonical vocabulary (`normalize_team("nflverse_schedule",
    ...)`) -- nflverse's own pbp/schedule tables use `LA` for the Rams where DK uses `LAR`,
    live-confirmed (see `normalization/team_aliases.py`'s `nflverse_schedule` entry).
    """
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]
    neutral = df[situation_neutral_mask(df)]
    agg = (
        neutral.groupby(["season", "week", "posteam"], observed=True)
        .agg(neutral_plays=("play_id", "count"), proe=("pass_oe", "mean"))
        .reset_index()
        .rename(columns={"posteam": "team"})
    )
    agg["team"] = agg["team"].map(lambda t: normalize_team("nflverse_schedule", t))
    return agg


def season_baseline(team_week: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, team): that team's full-season average pace (`pace_baseline`) and
    PROE (`proe_baseline`), each the mean of its own team-week values that season."""
    return (
        team_week.groupby(["season", "team"], observed=True)
        .agg(pace_baseline=("neutral_plays", "mean"), proe_baseline=("proe", "mean"))
        .reset_index()
    )


def prior_season_baselines_from(
    season_baseline_df: pd.DataFrame, prior_seasons: tuple[int, ...] = DEFAULT_PRIOR_SEASONS
) -> dict[str, tuple[float | None, float | None]]:
    """`team -> (pace_prior_baseline, proe_prior_baseline)`, ADR-0003's "simple average of that
    team's full-season 2024 and 2025 values (or the single most recent season's value if only one
    is available)" -- a plain mean over whichever of `prior_seasons` are present in
    `season_baseline_df` for that team collapses correctly to both cases (mean of two values, or
    mean of one)."""
    sub = season_baseline_df[season_baseline_df["season"].isin(prior_seasons)]
    out: dict[str, tuple[float | None, float | None]] = {}
    for team, grp in sub.groupby("team", observed=True):
        out[str(team)] = (float(grp["pace_baseline"].mean()), float(grp["proe_baseline"].mean()))
    return out


def compute_pace_proe_for_week(
    current_team_week: pd.DataFrame,
    target_week: int,
    prior_season_baselines: dict[str, tuple[float | None, float | None]],
    *,
    all_teams: frozenset[str] = CANONICAL_TEAMS,
    k: float = DEFAULT_K,
) -> pd.DataFrame:
    """Pure computation for one target week: for every team in `all_teams`, blend that team's
    current-season-to-date (weeks `< target_week` only, per ADR-0003's "never the current week's
    own in-progress data") pace/PROE toward its prior-season baseline using ADR-0011's `n/(n+k)`
    shrinkage, then z-score the blended values cross-sectionally across `all_teams` for this week
    (ADR-0003 population: all 32 teams).

    Output columns, one row per team: `team`, `week`, `weeks_played` (n), `shrinkage_weight`
    (w(n)), `pace_current_to_date`/`pace_prior_baseline`/`pace_blended`/`pace_z`, and the same
    four for `proe`. `pace_current_to_date`/`proe_current_to_date` are `None` when
    `weeks_played == 0` (week 1, or a team that hasn't played yet) -- the blend still produces a
    real `pace_blended`/`proe_blended` value (100% prior-baseline weight at `n=0`), so this is
    metadata for QA/downstream consumers to see how much of the number is prior vs. current
    season, not a gap in the output. `pace_z`/`proe_z` are `NaN` only if the whole `all_teams`
    population collapses to a single value (see `cross_sectional_zscore`) -- not expected in
    practice with 32 real teams, but surfaced rather than silently coerced to 0.
    """
    prior_weeks = current_team_week[current_team_week["week"] < target_week]
    rows = []
    for team in sorted(all_teams):
        team_rows = prior_weeks[prior_weeks["team"] == team]
        weeks_played = len(team_rows)
        pace_current = float(team_rows["neutral_plays"].mean()) if weeks_played else None
        proe_current = float(team_rows["proe"].mean()) if weeks_played else None
        weight = shrinkage_weight(weeks_played, k)
        prior_pace, prior_proe = prior_season_baselines.get(team, (None, None))
        pace_blended = blend_toward_prior(pace_current, prior_pace, weight)
        proe_blended = blend_toward_prior(proe_current, prior_proe, weight)
        rows.append(
            {
                "team": team,
                "week": target_week,
                "weeks_played": weeks_played,
                "shrinkage_weight": weight,
                "pace_current_to_date": pace_current,
                "pace_prior_baseline": prior_pace,
                "pace_blended": pace_blended,
                "proe_current_to_date": proe_current,
                "proe_prior_baseline": prior_proe,
                "proe_blended": proe_blended,
            }
        )
    out = pd.DataFrame(rows)
    out["pace_z"] = cross_sectional_zscore(out["pace_blended"])
    out["proe_z"] = cross_sectional_zscore(out["proe_blended"])
    return out


def fetch_pace_proe(
    current_season: int,
    target_week: int,
    *,
    prior_seasons: tuple[int, ...] = DEFAULT_PRIOR_SEASONS,
    k: float = DEFAULT_K,
) -> pd.DataFrame:
    """Live pull + full pipeline: current season's pbp (through whatever weeks are already
    played), the two prior seasons' pbp for the baseline, aggregated, blended, and z-scored for
    `target_week`. `include_participation=False` on every call -- Phase 0's live finding that the
    participation-file release lags the pbp release and 404s for an in-progress season (confirmed
    again live this pass for 2026 week 1)."""
    import nfl_data_py as nfl  # deferred import -- keeps this module importable without the
    # (large, slow-to-import-on-first-use) nfl_data_py dependency for pure-function unit tests

    current_pbp = nfl.import_pbp_data([current_season], include_participation=False)
    current_team_week = aggregate_team_week(current_pbp)

    prior_pbp = nfl.import_pbp_data(list(prior_seasons), include_participation=False)
    prior_team_week = aggregate_team_week(prior_pbp)
    prior_baselines = prior_season_baselines_from(season_baseline(prior_team_week), prior_seasons)

    return compute_pace_proe_for_week(current_team_week, target_week, prior_baselines, k=k)
