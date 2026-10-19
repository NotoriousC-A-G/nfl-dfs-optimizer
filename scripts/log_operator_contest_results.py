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
WEEK = 6

# (display_name, position_label, team) -- position_label is the DK roster-slot label (FLEX for
# the flex slot, not the player's real position), matching every other writer of this field
# (see `tracking/name_matching.parse_player_token`'s "Name (POS-TEAM)" contract).
OPERATOR_LINEUPS: dict[str, list[tuple[str, str, str]]] = {
    "L1": [
        ("Caleb Williams", "QB", "CHI"),
        ("Bijan Robinson", "RB", "ATL"),
        ("Kyren Williams", "RB", "LAR"),
        ("Germie Bernard", "WR", "PIT"),
        ("Luther Burden III", "WR", "CHI"),
        ("Wan'Dale Robinson", "WR", "TEN"),
        ("Isaiah Likely", "TE", "NYG"),
        ("Brock Bowers", "FLEX", "LV"),
        ("Chargers", "DST", "LAC"),
    ],
    "L2": [
        ("Matthew Stafford", "QB", "LAR"),
        ("Kenneth Walker III", "RB", "KC"),
        ("Jaylen Warren", "RB", "PIT"),
        ("Germie Bernard", "WR", "PIT"),
        ("Puka Nacua", "WR", "LAR"),
        ("Xavier Worthy", "WR", "KC"),
        ("Isaiah Likely", "TE", "NYG"),
        ("Kendrick Bourne", "FLEX", "ARI"),
        ("Raiders", "DST", "LV"),
    ],
    "L3": [
        ("Drake Maye", "QB", "NE"),
        ("James Cook III", "RB", "BUF"),
        ("Chuba Hubbard", "RB", "CAR"),
        ("A.J. Brown", "WR", "NE"),
        ("Drake London", "WR", "ATL"),
        ("Malik Nabers", "WR", "NYG"),
        ("Dallas Goedert", "TE", "PHI"),
        ("Tyler Higbee", "FLEX", "LAR"),
        ("Panthers", "DST", "CAR"),
    ],
}

# Transcribed by hand from DK's GameCenter, 2026-10-19 (week 6, main slate 10/18 only).
# (lineup_label, contest_name, entries, positions_paid, total_prizes, rank, fpts, winnings)
CONTEST_RESULTS: list[tuple[str, str, int, int, float, int, float, float]] = [
    ("L3", "$6K Pylon [Single Entry]", 2378, 618, 6000, 529, 140.90, 5.0),
    ("L2", "$15K Pylon [Single Entry]", 5945, 1523, 15000, 1010, 146.18, 5.0),
    ("L2", "$75K Fair Catch [Single Entry]", 7352, 1524, 75000, 1354, 146.18, 25.0),
    ("L3", "$50K Fair Catch [Single Entry]", 4901, 1046, 50000, 1216, 140.90, 0.0),
    ("L1", "$30K Pylon [Single Entry]", 11890, 3053, 30000, 6247, 123.80, 0.0),
    ("L1", "$125K Fair Catch [Single Entry]", 12254, 2563, 125000, 6779, 123.80, 0.0),
    ("L1", "$100K Huddle [Single Entry]", 23781, 5805, 100000, 12432, 123.80, 0.0),
    ("L1", "$100K First Down [20 Entry Max]", 118906, 32096, 100000, 56815, 123.80, 0.0),
    ("L2", "$100K First Down [20 Entry Max]", 118906, 32096, 100000, 16357, 146.18, 1.5),
    ("L3", "$100K First Down [20 Entry Max]", 118906, 32096, 100000, 23509, 140.90, 1.5),
    ("L1", "$2.75M Fantasy Football Millionaire [$1M to 1st]", 158904, 37425, 2750000, 80107, 123.80, 0.0),
    ("L2", "$2.75M Fantasy Football Millionaire [$1M to 1st]", 158904, 37425, 2750000, 24282, 146.18, 30.0),
    ("L3", "$2.75M Fantasy Football Millionaire [$1M to 1st]", 158904, 37425, 2750000, 34448, 140.90, 30.0),
    ("L1", "$600K Play-Action [20 Entry Max]", 237812, 59650, 600000, 114822, 123.80, 0.0),
    ("L2", "$600K Play-Action [20 Entry Max]", 237812, 59650, 600000, 33955, 146.18, 6.0),
    ("L3", "$600K Play-Action [20 Entry Max]", 237812, 59650, 600000, 48675, 140.90, 5.0),
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
