"""Strong-lineup-conditional dup-risk backtest (ADR-0035) -- both the Model Analytics Expert and the
Fantasy Football Expert required this before dup-risk calibration (ADR-0033) becomes anything the
optimizer actually ACTS on (ADR-0034 is descriptive-display only, still true after this module): does
the ownership-vs-duplication relationship -- and its real points cost -- still hold once you condition
on GENUINELY STRONG lineups, not the whole field (which includes a lot of low-quality, low-differentiation
chalk that would never be a real optimizer candidate in the first place)?

**Uses ONLY real, already-settled ResultsDB field data (ADR-0032) -- no period-correct historical
vendor projections needed**, which this project doesn't have (ADR-0018/ADR-0025 already established
that historical vendor projections for past slates aren't recoverable). "Strong" is defined
IN-SAMPLE: within each real contest, the lineups scoring in the top `top_pct` of that CONTEST's own
real point distribution -- a real, actual-outcome proxy for "this was a build worth considering,"
not a modeled/reconstructed one. This is a more rigorous check than a projection-based replay would
be for exactly this reason: it uses what ACTUALLY happened, not what a period-correct model would
have guessed.

Classifies each strong lineup's `avg_own` through the SAME `DupRiskLookupTable`/`classify_avg_ownership`
mechanism (ADR-0033/0034) the live optimizer would actually use -- this backtest answers "if the
optimizer filtered candidates by this exact bucket table, what would strong historical lineups in each
bucket really have scored and really have duplicated at," not a redefined or looser proxy question.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from nfl_dfs.analysis.dup_risk_calibration import DupRiskLookupTable, classify_avg_ownership
from nfl_dfs.storage.resultsdb_store import read_curated_lineups

DEFAULT_TOP_PCT = 0.01  # top 1% of each contest's own real point distribution -- roughly the range
# where a DK Millionaire-Maker-sized field's payout structure starts paying meaningfully, a real
# "was this a build worth considering" proxy, not an arbitrary round number.


@dataclass(frozen=True)
class StrongBucketStats:
    """One ownership bucket's real stats among ONLY the strong (top-`top_pct`-by-real-score)
    lineups -- `n` counts strong lineups, not the whole field."""

    bucket: int
    n: int
    mean_points: float
    dup_rate: float
    mean_lineup_ct: float


@dataclass(frozen=True)
class StrongLineupDupAnalysis:
    season: int | None  # None for a pooled multi-season result (fit_strong_lineup_dup_analysis_pooled)
    top_pct: float
    n_contests: int
    n_strong_rows: int
    n_total_rows: int
    bucket_stats: dict[int, StrongBucketStats]


def _strong_subset(df: pd.DataFrame, top_pct: float) -> pd.DataFrame:
    """Within each real `(date, contest_id)`, keeps only the lineups scoring in the top `top_pct`
    of THAT CONTEST's own real point distribution -- never a global/cross-contest points
    threshold, since raw point totals aren't comparable across different slates."""
    threshold = df.groupby(["date", "contest_id"])["points"].transform(lambda s: s.quantile(1 - top_pct))
    return df[df["points"] >= threshold]


def fit_strong_lineup_dup_analysis(
    season: int, table: DupRiskLookupTable, *, top_pct: float = DEFAULT_TOP_PCT, base_dir: Path | None = None
) -> StrongLineupDupAnalysis:
    """Fits one season's strong-lineup-conditional bucket stats, classifying each strong lineup's
    real `avg_own` through `table` (the same production lookup the live optimizer would use) --
    `dup_rate`/`mean_points` in each bucket are the REAL empirical values among strong lineups
    only, not `table`'s own (whole-field) estimates. Raises if the season has no curated lineups
    data -- never silently fits an empty/placeholder analysis."""
    df = read_curated_lineups(season=season, base_dir=base_dir)
    if df.empty:
        raise ValueError(f"no curated ResultsDB lineups data found for season {season} -- run the lineups backfill first")

    strong = _strong_subset(df, top_pct).copy()
    strong["is_duplicated"] = (strong["lineup_ct"] > 1).astype(int)
    strong["bucket"] = strong["avg_own"].apply(lambda v: classify_avg_ownership(v, table)[0])

    bucket_stats: dict[int, StrongBucketStats] = {}
    for bucket, group in strong.groupby("bucket"):
        bucket_stats[int(bucket)] = StrongBucketStats(
            bucket=int(bucket),
            n=int(len(group)),
            mean_points=float(group["points"].mean()),
            dup_rate=float(group["is_duplicated"].mean()),
            mean_lineup_ct=float(group["lineup_ct"].mean()),
        )

    return StrongLineupDupAnalysis(
        season=season,
        top_pct=top_pct,
        n_contests=int(df[["date", "contest_id"]].drop_duplicates().shape[0]),
        n_strong_rows=int(len(strong)),
        n_total_rows=int(len(df)),
        bucket_stats=bucket_stats,
    )


def fit_strong_lineup_dup_analysis_pooled(
    seasons: list[int], table: DupRiskLookupTable, *, top_pct: float = DEFAULT_TOP_PCT, base_dir: Path | None = None
) -> StrongLineupDupAnalysis:
    """Same as `fit_strong_lineup_dup_analysis`, pooling every requested season's strong lineups
    into one bucket-stats table -- the more decision-relevant view for a production question
    ("across the real production window, what does the strong-lineup trade actually look like"),
    since a single season's strong subset (top 1% of ~150K-400K entries, still tens of thousands
    of rows) is already plenty for per-bucket stats, but pooling several seasons narrows the noise
    further, same posture as `select_production_dup_calibration`'s own multi-season pooling.
    """
    per_season = [fit_strong_lineup_dup_analysis(season, table, top_pct=top_pct, base_dir=base_dir) for season in seasons]

    combined_rows: dict[int, list[StrongBucketStats]] = {}
    for analysis in per_season:
        for bucket, stats in analysis.bucket_stats.items():
            combined_rows.setdefault(bucket, []).append(stats)

    bucket_stats: dict[int, StrongBucketStats] = {}
    for bucket, stats_list in combined_rows.items():
        total_n = sum(s.n for s in stats_list)
        bucket_stats[bucket] = StrongBucketStats(
            bucket=bucket,
            n=total_n,
            mean_points=sum(s.mean_points * s.n for s in stats_list) / total_n,
            dup_rate=sum(s.dup_rate * s.n for s in stats_list) / total_n,
            mean_lineup_ct=sum(s.mean_lineup_ct * s.n for s in stats_list) / total_n,
        )

    return StrongLineupDupAnalysis(
        season=None,
        top_pct=top_pct,
        n_contests=sum(a.n_contests for a in per_season),
        n_strong_rows=sum(a.n_strong_rows for a in per_season),
        n_total_rows=sum(a.n_total_rows for a in per_season),
        bucket_stats=bucket_stats,
    )
