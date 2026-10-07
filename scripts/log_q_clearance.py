"""Records that a Questionable player is cleared to roster this week (see
`storage/injury_clearance_store.py` for the rule and why). Run Friday after reading the injury
reports; a Q player NOT logged here is excluded from lineup generation.

    PYTHONPATH=. .venv/bin/python scripts/log_q_clearance.py --week 5 --name "Breece Hall" --team NYJ --practice Full
    PYTHONPATH=. .venv/bin/python scripts/log_q_clearance.py --week 5 --name "Some WR" --team KC --practice Limited \\
        --note "Schefter: expected to play, no setbacks"

`Limited` requires --note (the positive report). A "game-time decision / will test it pre-game" player
is an avoid -- don't log him. `--list` shows what's on file for the week.
"""

from __future__ import annotations

import argparse

from nfl_dfs.storage.injury_clearance_store import QuestionableClearance, read_clearances, save_clearances

SEASON = 2026


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--season", type=int, default=SEASON)
    ap.add_argument("--name")
    ap.add_argument("--team")
    ap.add_argument("--practice", choices=["Full", "Limited"])
    ap.add_argument("--note", default="")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list or not args.name:
        rows = read_clearances(season=args.season, week=args.week)
        print(f"{len(rows)} clearance(s) for season={args.season} week={args.week}:")
        for c in rows:
            print(f"  {c.name} ({c.team}) -- {c.practice}" + (f" -- {c.note}" if c.note else ""))
        return

    if not args.team or not args.practice:
        ap.error("--team and --practice are required with --name")
    try:
        clearance = QuestionableClearance(args.season, args.week, args.name, args.team, args.practice, args.note)
    except ValueError as exc:
        ap.error(str(exc))
    written = save_clearances([clearance])
    print(f"Logged {args.name} ({args.team.upper()}) as cleared." if written else f"{args.name} already logged for week {args.week}.")


if __name__ == "__main__":
    main()
