"""Overrides the system's automatic Questionable-player call (see `storage/injury_clearance_store.py`).
The system decides every Q player from the official practice report and prints its basis; run this
only when you have additional information or disagree.

    PYTHONPATH=. .venv/bin/python scripts/log_q_override.py --week 5 --name "Breece Hall" --team NYJ --decision clear --note "Rapoport: expects to play"
    PYTHONPATH=. .venv/bin/python scripts/log_q_override.py --week 5 --name "Some WR" --team KC --decision bar --note "limited all week, soft tissue"

`--list` shows what is on file for the week. Edit `data/overrides/q_overrides.csv` by hand to change
or remove one.
"""

from __future__ import annotations

import argparse

from nfl_dfs.storage.injury_clearance_store import QuestionableOverride, read_overrides, save_overrides

SEASON = 2026


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--season", type=int, default=SEASON)
    ap.add_argument("--name")
    ap.add_argument("--team")
    ap.add_argument("--decision", choices=["clear", "bar"])
    ap.add_argument("--note", default="")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list or not args.name:
        rows = read_overrides(season=args.season, week=args.week)
        print(f"{len(rows)} override(s) for season={args.season} week={args.week}:")
        for o in rows:
            print(f"  {o.decision.upper():5} {o.name} ({o.team})" + (f" -- {o.note}" if o.note else ""))
        return

    if not args.team or not args.decision:
        ap.error("--team and --decision are required with --name")
    try:
        override = QuestionableOverride(args.season, args.week, args.name, args.team, args.decision, args.note)
    except ValueError as exc:
        ap.error(str(exc))
    written = save_overrides([override])
    print(f"Logged {args.decision} for {args.name} ({args.team.upper()})." if written else f"{args.name} already has an override for week {args.week}.")


if __name__ == "__main__":
    main()
