"""Manual, live-network integration check for the Data Integration Engineer's urgent
slate-selection correctness fix (`nfl_dfs.ingestion.draftkings.select_classic_slate`).

Shows every live Classic draftGroupId this week (label, player count, actual game list, and
whether its games are day-coherent), what the OLD "most total players wins" heuristic would have
picked (reimplemented here for comparison -- that logic no longer exists in `draftkings.py`), and
what the NEW `select_classic_slate` picks (or why it refuses to guess). NOT part of `pytest` --
needs live network access and a live NFL week, same reasoning as `live_integration_check.py`. Run
by hand:

    .venv/bin/python scripts/live_integration_check_slate_selection.py
"""

from __future__ import annotations

import requests

from nfl_dfs.ingestion.draftkings import (
    CONTESTS_URL,
    SlateSelectionError,
    _candidate_slates,
    _describe_slate,
    select_classic_slate,
)


def _old_most_players_heuristic(candidates):
    """Reimplementation of the pre-fix `fetch_classic_draft_group_id` logic (removed from
    `draftkings.py` by this fix) purely for this side-by-side comparison report: picked whichever
    live Classic draftGroupId had the most total draftables rows, with zero awareness of which
    games or calendar days it actually covered.
    """
    live = [s for s in candidates if s.player_count > 0]
    if not live:
        return None
    return max(live, key=lambda s: s.player_count)


def main() -> None:
    print("Fetching live DK contest listing...")
    contests_payload = requests.get(CONTESTS_URL, timeout=20.0).json()
    candidates = _candidate_slates(contests_payload, requests)

    print(f"\n{len(candidates)} live Classic draftGroupId(s) found this week:")
    for slate in candidates:
        print(f"  {_describe_slate(slate)}")
        if slate.games:
            dates = sorted(slate.eastern_dates)
            coherent = "coherent (1 day)" if len(dates) == 1 else f"INCOHERENT ({len(dates)} days)"
            print(f"    -> eastern date(s): {dates} [{coherent}]")

    old_pick = _old_most_players_heuristic(candidates)
    print("\n=== OLD behavior: most total players, no slate awareness ===")
    if old_pick is None:
        print("  would have raised: no live candidate had any players")
    else:
        print(f"  would have picked: {_describe_slate(old_pick)}")
        if len(old_pick.eastern_dates) > 1:
            print(
                f"  *** this candidate's games span {len(old_pick.eastern_dates)} distinct "
                f"calendar days {sorted(old_pick.eastern_dates)} -- NOT a coherent single slate, "
                "but the old heuristic would have silently accepted it as 'the' slate anyway ***"
            )

    print("\n=== NEW behavior: select_classic_slate (this fix) ===")
    new_pick = None
    new_error = None
    try:
        new_pick = select_classic_slate()
    except SlateSelectionError as exc:
        new_error = exc
        print("  raised SlateSelectionError -- a loud, specific refusal instead of a silent guess:")
        print(f"  {exc}")
    else:
        print(f"  selected: {_describe_slate(new_pick)}")

    print("\n=== Comparison ===")
    if old_pick is None:
        print("  Old heuristic had nothing to pick either -- no regression either way this run.")
    elif new_error is not None:
        print(
            f"  Old heuristic would have silently built lineups against {_describe_slate(old_pick)}.\n"
            "  New behavior correctly refuses instead (see error above) -- this is the exact bug "
            "this fix closes."
        )
    elif new_pick.draft_group_id == old_pick.draft_group_id:
        print("  Old and new agree this run (old heuristic happened to be right this time too).")
    else:
        print(
            f"  Old heuristic would have picked {_describe_slate(old_pick)}; new logic instead "
            f"picked {_describe_slate(new_pick)}."
        )


if __name__ == "__main__":
    main()
