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

SEASON/WEEK, OPERATOR_LINEUPS, and CONTEST_RESULTS below must be kept current by hand each week,
same convention as every other live script in this project.
"""

from __future__ import annotations

from nfl_dfs.storage.agent_results_store import AgentResultRow, save_agent_results
from nfl_dfs.storage.contest_results_store import ContestResult, save_contest_results
from nfl_dfs.storage.slate_snapshot_store import load_latest_slate_snapshot

SEASON = 2026
WEEK = 3

# (display_name, position_label, team) -- position_label is the DK roster-slot label (FLEX for
# the flex slot, not the player's real position), matching every other writer of this field
# (see `tracking/name_matching.parse_player_token`'s "Name (POS-TEAM)" contract).
OPERATOR_LINEUPS: dict[str, list[tuple[str, str, str]]] = {
    "L1": [
        ("Tyler Shough", "QB", "NO"),
        ("Jahmyr Gibbs", "RB", "DET"),
        ("Kenneth Walker III", "RB", "KC"),
        ("Denzel Boston", "WR", "CLE"),
        ("Devaughn Vele", "WR", "NO"),
        ("Garrett Wilson", "WR", "NYJ"),
        ("Dalton Schultz", "TE", "HOU"),
        ("Parker Washington", "FLEX", "JAX"),
        ("Bengals", "DST", "CIN"),
    ],
    "L2": [
        ("Josh Allen", "QB", "BUF"),
        ("Derrick Henry", "RB", "BAL"),
        ("Kenneth Walker III", "RB", "KC"),
        ("Ryan Flournoy", "WR", "DAL"),
        ("Jalen Coker", "WR", "CAR"),
        ("Ladd McConkey", "WR", "LAC"),
        ("Dalton Kincaid", "TE", "BUF"),
        ("Oronde Gadsden II", "FLEX", "LAC"),
        ("Titans", "DST", "TEN"),
    ],
    "L3": [
        ("Brock Purdy", "QB", "SF"),
        ("Christian McCaffrey", "RB", "SF"),
        ("Breece Hall", "RB", "NYJ"),
        ("Devaughn Vele", "WR", "NO"),
        ("Parker Washington", "WR", "JAX"),
        ("Xavier Hutchinson", "WR", "HOU"),
        ("George Kittle", "TE", "SF"),
        ("Trey McBride", "FLEX", "ARI"),
        ("Jaguars", "DST", "JAX"),
    ],
}

# Transcribed by hand from DK's GameCenter, 2026-09-28 (see the week 3 handoff for context).
# (lineup_label, contest_name, entries, positions_paid, total_prizes, rank, fpts, winnings)
CONTEST_RESULTS: list[tuple[str, str, int, int, float, int, float, float]] = [
    ("L1", "$125K First Down [20 Entry Max]", 148632, 40125, 125000, 19335, 156.20, 1.50),
    ("L2", "$125K First Down [20 Entry Max]", 148632, 40125, 125000, 137292, 94.06, 0.0),
    ("L3", "$125K First Down [20 Entry Max]", 148632, 40125, 125000, 29730, 148.48, 1.50),
    ("L1", "$750K Play-Action [20 Entry Max]", 297265, 71627, 750000, 34018, 156.20, 5.0),
    ("L2", "$750K Play-Action [20 Entry Max]", 297265, 71627, 750000, 273078, 94.06, 0.0),
    ("L3", "$750K Play-Action [20 Entry Max]", 297265, 71627, 750000, 53633, 148.48, 5.0),
    ("L1", "$2.75M Fantasy Football Millionaire [$1M to 1st]", 161764, 37425, 2750000, 22258, 156.20, 30.0),
    ("L2", "$2.75M Fantasy Football Millionaire [$1M to 1st]", 161764, 37425, 2750000, 150263, 94.06, 0.0),
    ("L3", "$2.75M Fantasy Football Millionaire [$1M to 1st]", 161764, 37425, 2750000, 33746, 148.48, 30.0),
    ("L1", "$40K Pylon [Single Entry]", 15854, 3996, 40000, 2277, 156.20, 5.0),
    ("L2", "$75K Fair Catch [Single Entry]", 7352, 1524, 75000, 6920, 94.06, 0.0),
    ("L2", "$20K Pylon [Single Entry]", 7927, 2011, 20000, 7415, 94.06, 0.0),
    ("L1", "$175K Fair Catch [Single Entry]", 17156, 4135, 175000, 2689, 156.20, 20.0),
    ("L3", "$50K Fair Catch [Single Entry]", 4901, 1046, 50000, 1350, 148.48, 0.0),
    ("L1", "$25K Triple Option [5 Entry Max]", 9908, 2576, 25000, 1919, 156.20, 5.0),
    ("L2", "$25K Triple Option [5 Entry Max]", 9908, 2576, 25000, 9383, 94.06, 0.0),
    ("L3", "$25K Triple Option [5 Entry Max]", 9908, 2576, 25000, 2801, 148.48, 0.0),
    ("L1", "$100K Huddle [Single Entry]", 23781, 5805, 100000, 3194, 156.20, 9.0),
    ("L1", "$400K Flea Flicker [$50K to 1st]", 95124, 23150, 400000, 15478, 156.20, 8.0),
    ("L2", "$400K Flea Flicker [$50K to 1st]", 95124, 23150, 400000, 89350, 94.06, 0.0),
    ("L3", "$400K Flea Flicker [$50K to 1st]", 95124, 23150, 400000, 23185, 148.48, 0.0),
    ("L2", "$8K Huddle [Single Entry]", 1902, 432, 8000, 1797, 94.06, 0.0),
    ("L3", "$8K Huddle [Single Entry]", 1902, 432, 8000, 508, 148.48, 0.0),
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
