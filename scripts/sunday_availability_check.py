"""Sunday-morning availability check: run ~90 minutes before the first lock (inactives land then).

Re-pulls every status source -- DraftKings' live `status` (flips to OUT for inactives), the official
NFL injury report, RotoGrinders' Situation Room -- plus Chris's overrides, and checks every player
in every lineup built Friday. Anyone at risk from ANY source is flagged with the source and wording,
and each flagged slot gets a short menu of clean same-slot replacements that fit the salary.
It decides nothing: confirm the replacement's game has not locked before swapping in DK.

    PYTHONPATH=. .venv/bin/python scripts/sunday_availability_check.py
    PYTHONPATH=. .venv/bin/python scripts/sunday_availability_check.py --played data/overrides/played_wk5.txt

Default: every lineup in the week's latest slate snapshot. `--played FILE` instead checks the
lineups Chris actually entered, one per line, `L1: Name (QB-TEAM)|Name (RB-TEAM)|...|Name (FLEX-TEAM)|Team (DST-TEAM)`
(the same token format as `agent_results.csv`; the position label is the DK slot, FLEX for the flex).

SEASON/WEEK/DRAFT_GROUP_ID come from `live_integration_check_dashboard.py` -- keep those current.
NOT part of `pytest` (live network). Read-only apart from archiving the official-report pull.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re

from nfl_dfs.analysis.sunday_check import (
    LineupCheck, check_lineup, classify_dk, classify_official, classify_override, classify_rotogrinders,
)
from nfl_dfs.ingestion.draftkings import fetch_slate_by_draft_group_id, parse_draftables
from nfl_dfs.ingestion.official_injury_report import fetch_official_injury_report
from nfl_dfs.ingestion.rotogrinders_injuries import fetch_injury_report
from nfl_dfs.normalization.team_aliases import normalize_team
from nfl_dfs.projection.blend import extract_dk_injury_status
from nfl_dfs.storage.injury_clearance_store import read_overrides
from nfl_dfs.storage.official_injury_snapshot_store import OfficialInjurySnapshot, write_snapshot
from nfl_dfs.storage.slate_snapshot_store import load_latest_slate_snapshot
from nfl_dfs.tracking.name_matching import normalize_player_name, parse_player_token
from scripts.live_integration_check_dashboard import DRAFT_GROUP_ID, SEASON, WEEK


def _key(name: str, team: str) -> tuple[str, str]:
    return (normalize_player_name(name), (team or "").upper())


def _snapshot_lineups(snapshot: dict) -> list[tuple[str, list[dict]]]:
    out = []
    for entry in snapshot.get("agent_lineups", []):
        label = (entry.get("agent") or {}).get("display_name", "agent")
        players = []
        for slot, p in ((entry.get("lineup") or {}).get("slots") or {}).items():
            players.append(
                {"name": p["display_name"], "team": p["team"], "position": p["position"], "slot": re.sub(r"\d+$", "", slot),
                 "salary": p["salary"], "canonical_id": p.get("canonical_id")}
            )
        out.append((label, players))
    return out


def _played_lineups(path: str, pool: list[dict]) -> list[tuple[str, list[dict]]]:
    by_key = {_key(r["identity"]["display_name"], r["team"]): r for r in pool}
    out = []
    for i, line in enumerate(open(path).read().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        label, _, body = line.partition(":") if re.match(r"^[A-Za-z0-9 _-]{1,20}:", line) else (f"Lineup {i}", "", line)
        players = []
        for token in body.split("|"):
            name, slot, team = parse_player_token(token.strip())
            row = by_key.get(_key(name, team))
            if row is None:
                raise SystemExit(f"{label.strip()}: no slate-pool match for {name} ({team}) -- check spelling/team")
            players.append(
                {"name": name, "team": team, "position": row["position"], "slot": slot, "salary": row["salary"],
                 "canonical_id": (row.get("identity") or {}).get("canonical_id")}
            )
        out.append((label.strip(), players))
    return out


def _print_lineup(lc: LineupCheck) -> None:
    flagged = lc.flagged
    print(f"\n=== {lc.label}  (salary used ${lc.salary_used:,}) ===")
    if not flagged:
        print("  all clear -- no source flags any player")
        return
    for p in sorted(flagged, key=lambda p: -p.severity):
        print(f"  !! {p.name} ({p.team}, {p.slot}, ${p.salary:,})")
        for r in p.readings:
            print(f"       {r.source}: {r.label}")
        options = lc.swaps.get(p.name, ())
        if options:
            print("       swap options (clean on every source, fits the salary):")
            for c in options:
                own = f"{c.ownership:.1f}%" if c.ownership is not None else "n/a"
                proj = f"{c.projection:.1f}" if c.projection is not None else "n/a"
                print(f"         {c.name} ({c.team}, {c.position}) ${c.salary:,}  proj {proj}  own {own}  {c.slate_window or ''}")
        elif p.slot != "DST":
            print("       no clean same-slot option fits the salary -- consider re-balancing around this slot")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--played", help="file of the lineups you actually entered (default: every snapshot lineup)")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    print(f"=== Sunday availability check -- season {SEASON} week {WEEK}, {now.strftime('%Y-%m-%d %H:%MZ')} ===")
    snapshot = load_latest_slate_snapshot(SEASON, WEEK)
    if snapshot is None:
        raise SystemExit(f"No slate snapshot for season={SEASON} week={WEEK} -- nothing to check.")
    print(f"  slate snapshot: {snapshot.get('snapshot_filename')} ({snapshot.get('timestamp')})")
    pool = snapshot["player_pool"]
    lineups = _played_lineups(args.played, pool) if args.played else _snapshot_lineups(snapshot)

    problems: list[str] = []

    dk_status: dict[str, str] = {}
    dk_id_by_key: dict[tuple[str, str], str] = {}
    try:
        payload, _slate = fetch_slate_by_draft_group_id(DRAFT_GROUP_ID)
        dk_status = extract_dk_injury_status(payload)
        dk_id_by_key = {_key(p.name, p.team or ""): p.native_id for p in parse_draftables(payload)}
        print(f"  DK draftables: {len(dk_id_by_key)} players, {len(dk_status)} with a status (draft group {DRAFT_GROUP_ID})")
        if not dk_status:
            problems.append(
                "DraftKings returned ZERO player statuses -- on a live slate that is suspicious (wrong draft group, or a "
                "finished slate). Do not read 'all clear' as safe; verify DRAFT_GROUP_ID in live_integration_check_dashboard.py"
            )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"DraftKings status unavailable ({exc}) -- inactives are NOT being checked against DK")

    official_by_gsis = {}
    try:
        entries = [e for e in fetch_official_injury_report(SEASON) if e.week == WEEK]
        official_by_gsis = {e.gsis_id: e for e in entries}
        write_snapshot(OfficialInjurySnapshot(now.isoformat(), SEASON, WEEK, entries))
        print(f"  official report: {len(entries)} week-{WEEK} rows across {len({e.team for e in entries})} teams (archived)")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"official injury report unavailable ({exc})")

    rg_by_key: dict[tuple[str, str], str] = {}
    try:
        for e in fetch_injury_report():
            rg_by_key[_key(e.name, normalize_team("rotogrinders", e.team) or e.team)] = e.status
        print(f"  RotoGrinders: {len(rg_by_key)} rows")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"RotoGrinders unavailable ({exc})")

    overrides = {_key(o.name, o.team): o for o in read_overrides(season=SEASON, week=WEEK)}

    def readings_for(name: str, team: str, canonical_id: str | None):
        k = _key(name, team)
        dk_id = dk_id_by_key.get(k)
        found = [
            classify_dk(dk_status.get(dk_id)) if dk_id else None,
            classify_official(official_by_gsis.get(canonical_id)) if canonical_id else None,
            classify_rotogrinders(rg_by_key.get(k)),
            classify_override(overrides[k].decision, overrides[k].note) if k in overrides else None,
        ]
        return [r for r in found if r is not None]

    for label, players in lineups:
        _print_lineup(check_lineup(label, players, readings_for, pool))

    print()
    for msg in problems:
        print(f"  WARNING: {msg}")
    print("  Reminder: confirm each replacement's game has not locked before swapping; the check does not model kickoff times.")


if __name__ == "__main__":
    main()
