"""Forward-logs Chris's own manually-built lineups (L1/L2/L3, or however many are live a given
week) into `agent_results.csv` under `agent_id="operator"`, plus the real DK contest results for
every entry those lineups were played into, into `contest_results.csv`.

This is the manual-entry point the week 3 handoff flagged as missing ("no existing mechanism to
log the outcomes of Chris's own 3 manually-built DK entries"). It exists because DK exposes no
results API -- every row here is transcribed by hand from DK's GameCenter screenshots, same as
week 2's real contest_results.csv rows already on disk.

`proj_total`/`salary` per lineup are computed from the real slate snapshot (`storage/
slate_snapshot_store.py`), not hardcoded -- each ROSTER (which real players, which real DK
salary-slot label) is the one thing transcribed by hand below; everything numeric is looked up.

`total_dk_score` is deliberately left blank here, same "forward log now, join later" split
`agent_results_store.py`'s own docstring establishes -- `tracking/postmortem/replay.py` computes
the real settled actual (and full per-player delta) live, by joining `players` against nflverse,
when `scripts/run_postmortem.py` runs. Nothing here needs to duplicate that join.

Run by hand once a week's rosters/results are known (not part of pytest -- writes real files):
    PYTHONPATH=. .venv/bin/python scripts/log_operator_contest_results.py

Entry fees are not transcribed here: after running this, run `scripts/backfill_contest_entry_fees.py`
against a fresh DK contest-entry-history export to fill `entry_fee` on the new rows.

SEASON/WEEK, OPERATOR_LINEUPS, and CONTEST_RESULTS below must be kept current by hand each week,
same convention as every other live script in this project.
"""

from __future__ import annotations

from nfl_dfs.storage.agent_results_store import AgentResultRow, save_agent_results
from nfl_dfs.storage.contest_results_store import ContestResult, save_contest_results
from nfl_dfs.storage.slate_snapshot_store import load_latest_slate_snapshot

SEASON = 2026
WEEK = 5

# (display_name, position_label, team) -- position_label is the DK roster-slot label (FLEX for
# the flex slot, not the player's real position), matching every other writer of this field
# (see `tracking/name_matching.parse_player_token`'s "Name (POS-TEAM)" contract).
OPERATOR_LINEUPS: dict[str, list[tuple[str, str, str]]] = {
    "L1": [
        ("Jordan Love", "QB", "GB"),
        ("Braelon Allen", "RB", "NYJ"),
        ("Jahmyr Gibbs", "RB", "DET"),
        ("Matthew Golden", "WR", "GB"),
        ("Christian Watson", "WR", "GB"),
        ("Michael Wilson", "WR", "ARI"),
        ("Isaiah Likely", "TE", "NYG"),
        ("Roman Wilson", "FLEX", "PIT"),
        ("Raiders", "DST", "LV"),
    ],
    "L2": [
        ("Jared Goff", "QB", "DET"),
        ("Aaron Jones Sr.", "RB", "MIN"),
        ("Jaylen Warren", "RB", "PIT"),
        ("Keenan Allen", "WR", "IND"),
        ("Rome Odunze", "WR", "CHI"),
        ("Amon-Ra St. Brown", "WR", "DET"),
        ("Trey McBride", "TE", "ARI"),
        ("Michael Mayer", "FLEX", "LV"),
        ("Vikings", "DST", "MIN"),
    ],
    "L3": [
        ("Tyson Bagent", "QB", "CHI"),
        ("Braelon Allen", "RB", "NYJ"),
        ("Jahmyr Gibbs", "RB", "DET"),
        ("Rome Odunze", "WR", "CHI"),
        ("Jaxon Smith-Njigba", "WR", "SEA"),
        ("Michael Wilson", "WR", "ARI"),
        ("George Kittle", "TE", "SF"),
        ("Greg Dulcich", "FLEX", "MIA"),
        ("Titans", "DST", "TEN"),
    ],
}

# Transcribed by hand from DK's GameCenter, 2026-10-12 (week 5).
# (lineup_label, contest_name, entries, positions_paid, total_prizes, rank, fpts, winnings)
CONTEST_RESULTS: list[tuple[str, str, int, int, float, int, float, float]] = [
    ("L2", "$6K Huddle [Single Entry]", 1426, 300, 6000, 311, 153.64, 0.0),
    ("L2", "$40K Front Four [20 Entry Max]", 11804, 3091, 40000, 3224, 153.64, 0.0),
    ("L1", "$40K Front Four [20 Entry Max]", 11804, 3091, 40000, 292, 187.86, 10.0),
    ("L3", "$40K Front Four [20 Entry Max]", 11804, 3091, 40000, 8729, 123.66, 0.0),
    ("L3", "$50K Fair Catch [Single Entry]", 4901, 1046, 50000, 3276, 123.66, 0.0),
    ("L1", "$150K Fair Catch [Single Entry]", 14659, 2956, 150000, 214, 187.86, 50.0),
    ("L2", "$75K Fair Catch [Single Entry]", 7094, 1524, 75000, 1575, 153.64, 0.0),
    ("L2", "$20K Pylon [Single Entry]", 7906, 2011, 20000, 1597, 153.64, 5.0),
    ("L1", "$30K Pylon [Single Entry]", 11716, 3053, 30000, 175, 187.86, 10.0),
    ("L1", "$100K Huddle [Single Entry]", 23781, 5805, 100000, 319, 187.86, 20.0),
    ("L1", "$700K Play-Action [20 Entry Max]", 259115, 68927, 700000, 4067, 187.86, 10.0),
    ("L2", "$700K Play-Action [20 Entry Max]", 259115, 68927, 700000, 47460, 153.64, 5.0),
    ("L3", "$700K Play-Action [20 Entry Max]", 259115, 68927, 700000, 152695, 123.66, 0.0),
    ("L1", "$100K First Down [20 Entry Max]", 118906, 32096, 100000, 2049, 187.86, 4.0),
    ("L2", "$100K First Down [20 Entry Max]", 118906, 32096, 100000, 24181, 153.64, 1.5),
    ("L3", "$100K First Down [20 Entry Max]", 118906, 32096, 100000, 74004, 123.66, 0.0),
    ("L1", "$2.75M Fantasy Football Millionaire [$1M to 1st]", 161557, 37425, 2750000, 2803, 187.86, 50.0),
    ("L2", "$2.75M Fantasy Football Millionaire [$1M to 1st]", 161557, 37425, 2750000, 32495, 153.64, 30.0),
    ("L3", "$2.75M Fantasy Football Millionaire [$1M to 1st]", 161557, 37425, 2750000, 100897, 123.66, 0.0),
]


def _build_agent_result_row(strategy_name: str, roster: list[tuple[str, str, str]], pool_by_key: dict) -> AgentResultRow:
    players: list[str] = []
    proj_total = 0.0
    salary_total = 0
    for name, position_label, team in roster:
        key = (name, team)
        pool_row = pool_by_key.get(key)
        if pool_row is None:
            raise ValueError(f"{strategy_name}: no snapshot player_pool match for {name} ({team}) -- check spelling/team")
        proj_total += pool_row["projection"] or 0.0
        salary_total += pool_row["salary"]
        players.append(f"{name} ({position_label}-{team})")

    return AgentResultRow(
        season=SEASON,
        week=WEEK,
        agent_id="operator",
        strategy_name=strategy_name,
        proj_total=round(proj_total, 2),
        salary=salary_total,
        players=tuple(players),
    )


def main() -> None:
    snapshot = load_latest_slate_snapshot(SEASON, WEEK)
    if snapshot is None:
        print(f"No slate snapshot found for season={SEASON} week={WEEK} -- run live_integration_check_dashboard.py first.")
        return

    pool_by_key = {(row["identity"]["display_name"], row["team"]): row for row in snapshot["player_pool"]}

    agent_rows = []
    for strategy_name, roster in OPERATOR_LINEUPS.items():
        row = _build_agent_result_row(strategy_name, roster, pool_by_key)
        agent_rows.append(row)
        print(f"{strategy_name}: proj_total={row.proj_total} salary={row.salary} ({len(row.players)} players)")
        if row.salary > 50000:
            raise ValueError(f"{strategy_name}: salary {row.salary} exceeds the $50,000 cap -- check the roster")

    written = save_agent_results(agent_rows)
    print(f"agent_results.csv: {'wrote' if written else 'nothing new to write (already logged)'} -> {written}")

    contest_rows = [
        ContestResult(
            season=SEASON,
            week=WEEK,
            lineup_label=label,
            contest_name=contest_name,
            entries=entries,
            positions_paid=positions_paid,
            total_prizes=total_prizes,
            rank=rank,
            fpts=fpts,
            winnings=winnings,
        )
        for label, contest_name, entries, positions_paid, total_prizes, rank, fpts, winnings in CONTEST_RESULTS
    ]
    written = save_contest_results(contest_rows)
    print(f"contest_results.csv: {'wrote' if written else 'nothing new to write (already logged)'} -> {written}")

    total_winnings = sum(c.winnings for c in contest_rows)
    cashed = sum(1 for c in contest_rows if c.winnings > 0)
    print(f"Real: {len(contest_rows)} entries, {cashed} cashed, ${total_winnings:.2f} total winnings.")


if __name__ == "__main__":
    main()
