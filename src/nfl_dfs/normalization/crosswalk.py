"""nflverse ID crosswalk fetch/cache (ADR-0013 decision 2 step 1; live-verified findings in
`docs/adr/0013-player-id-reconciliation.md`).

`nfl_data_py.import_ids()` hits a remote GitHub-hosted CSV on every call and the data "doesn't
change intra-week" per the ADR, so this caches the raw frame to a local file instead of
re-fetching per matcher invocation. Cache format is CSV (not parquet) to avoid adding a parquet
engine dependency the project doesn't otherwise need.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[3] / "data" / "cache" / "nflverse_ids.csv"

CROSSWALK_ID_COLUMNS: dict[str, str] = {
    # Vendor source -> crosswalk column carrying that source's native ID. Only PFF's is
    # currently trusted as probeable (ADR-0013 decision 2 step 1) — pfr_id exists for
    # Footballguys but is deliberately excluded here, not merely unlisted; see matcher.py.
    "pff": "pff_id",
}


def fetch_crosswalk(*, cache_path: Path = DEFAULT_CACHE_PATH, force_refresh: bool = False) -> pd.DataFrame:
    if not force_refresh and cache_path.exists():
        return pd.read_csv(cache_path, dtype=str)

    import nfl_data_py as nfl

    df = nfl.import_ids()
    # nfl_data_py loads several *_id columns (pff_id, mfl_id, sleeper_id, ...) as float64
    # purely because the column contains NaNs for players missing that ID — round-tripping
    # through CSV as-is turns a real id like 124305 into the string "124305.0", which then
    # fails a strict string-equality ID comparison against a vendor payload's "124305". Cast
    # float columns to nullable Int64 first so the cache holds clean digit strings.
    for column in df.columns:
        if column.endswith("_id") and pd.api.types.is_float_dtype(df[column]):
            df[column] = df[column].astype("Int64")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    return pd.read_csv(cache_path, dtype=str)


def find_row_by_source_id(crosswalk: pd.DataFrame, source: str, native_id: str) -> pd.Series | None:
    column = CROSSWALK_ID_COLUMNS.get(source)
    if column is None or column not in crosswalk.columns:
        return None
    matches = crosswalk[crosswalk[column] == str(native_id)]
    if matches.empty:
        return None
    return matches.iloc[0]


def find_row_by_name_team_position(
    crosswalk: pd.DataFrame, normalized_name: str, canonical_team: str | None, canonical_position: str
) -> pd.Series | None:
    from nfl_dfs.normalization.name_utils import normalize_name
    from nfl_dfs.normalization.position_aliases import normalize_position
    from nfl_dfs.normalization.team_aliases import normalize_team

    candidates = crosswalk.copy()
    candidates["_norm_name"] = candidates["name"].fillna("").map(normalize_name)
    candidates["_norm_team"] = candidates["team"].map(lambda t: normalize_team("crosswalk", t))
    candidates["_norm_position"] = candidates["position"].map(lambda p: normalize_position("crosswalk", p))

    matches = candidates[
        (candidates["_norm_name"] == normalized_name) & (candidates["_norm_position"] == canonical_position)
    ]
    if canonical_team is not None:
        team_matches = matches[matches["_norm_team"] == canonical_team]
        if not team_matches.empty:
            matches = team_matches

    if matches.empty:
        return None
    return matches.iloc[0]
