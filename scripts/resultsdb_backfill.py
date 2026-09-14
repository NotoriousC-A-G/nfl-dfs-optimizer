"""CLI entry point for the ResultsDB historical backfill (ADR-0024).

Walks real NFL regular-season game dates for the given seasons, fetching each date's
Millionaire-Maker-equivalent contest via `nfl_dfs.ingestion.rotogrinders_resultsdb` and writing
raw/curated data via `nfl_dfs.storage.resultsdb_store`. Resumable: killing this process and re-running the
same command picks up exactly where it left off (ADR-0024's filesystem-is-the-only-state-file design) --
no flag needed to "resume", that's just what re-running does.

Usage:
    .venv/bin/python scripts/resultsdb_backfill.py 2024
    .venv/bin/python scripts/resultsdb_backfill.py 2020 2021 2022 2023 2024 2025
"""

from __future__ import annotations

import argparse
import sys

from nfl_dfs.ingestion.resultsdb_backfill import run_backfill


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seasons", type=int, nargs="+", help="NFL season year(s) to backfill, e.g. 2024")
    args = parser.parse_args()

    stats = run_backfill(args.seasons)

    print("\n=== Backfill complete ===")
    print(f"  seasons: {args.seasons}")
    print(f"  fetched: {stats.fetched}")
    print(f"  skipped (already captured): {stats.skipped}")
    print(f"  no primary contest: {stats.no_primary_contest}")
    print(f"  no draft groups: {stats.no_draft_groups}")
    print(f"  unavailable: {stats.unavailable}")
    print(f"  failed (retry next run): {stats.failed}")
    print(f"  total player-exposure rows written: {stats.player_rows}")

    if stats.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
