"""Builds the evidence packets for a week from the latest saved slate snapshot + 2026 play-by-play and
writes a browsable page and one JSON file per game -- so you can see exactly what a game analyst would
be given. Read-only apart from the files below.

    PYTHONPATH=. .venv/bin/python scripts/render_evidence_packets.py
    -> dashboard_output/evidence_packets.html   (open via the dashboard preview server)
    -> dashboard_output/evidence_packets/<AWAY@HOME>.json

SEASON/WEEK come from `live_integration_check_dashboard.py`. Availability decisions and weather are
supplied by the live pipeline (they need live pulls); when run standalone this page shows the packets
without them and says so under each game's data gaps.
NOT part of `pytest` (loads play-by-play over the network).
"""

from __future__ import annotations

import datetime as dt
import json
import warnings
from dataclasses import asdict
from pathlib import Path

import nfl_data_py as nfl

from nfl_dfs.build.availability import availability_from_snapshot
from nfl_dfs.build.evidence.builder import build_evidence_packets
from nfl_dfs.build.evidence.opportunity import trailing_shares
from nfl_dfs.build.evidence.metrics import team_game_metrics
from nfl_dfs.build.evidence.render import render_evidence_page
from nfl_dfs.storage.slate_snapshot_store import load_latest_slate_snapshot
from scripts.live_integration_check_dashboard import SEASON, WEEK

OUT_DIR = Path("dashboard_output")


def main() -> None:
    snapshot = load_latest_slate_snapshot(SEASON, WEEK)
    if snapshot is None:
        raise SystemExit(f"No slate snapshot for season={SEASON} week={WEEK} -- run live_integration_check_dashboard.py first.")
    print(f"Snapshot {snapshot.get('snapshot_filename')} ({snapshot.get('timestamp')}); loading {SEASON} play-by-play...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pbp = nfl.import_pbp_data([SEASON], include_participation=False)
    tg = team_game_metrics(pbp)
    availability, _warn = availability_from_snapshot(snapshot)
    if _warn:
        print(f"WARNING: {_warn}")
    packets = build_evidence_packets(snapshot["player_pool"], snapshot["stack_profiles"], tg, season=SEASON, week=WEEK, availability=availability, opportunity=trailing_shares(pbp, season=SEASON, through_week=WEEK - 1))

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "evidence_packets").mkdir(exist_ok=True)
    for game_id, packet in packets.items():
        (OUT_DIR / "evidence_packets" / f"{game_id}.json").write_text(json.dumps(asdict(packet), indent=2, default=str))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    (OUT_DIR / "evidence_packets.html").write_text(
        render_evidence_page(packets, title=f"Evidence packets -- {SEASON} week {WEEK}", generated_at=f"built {stamp} from snapshot {snapshot.get('snapshot_filename')}")
    )
    print(f"Wrote {len(packets)} packets -> {OUT_DIR / 'evidence_packets.html'} (+ JSON per game)")


if __name__ == "__main__":
    main()
