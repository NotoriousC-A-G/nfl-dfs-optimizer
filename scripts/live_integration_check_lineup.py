"""Manual, live-network integration check for Stage 8 (`optimizer/lineup.py`): pull today's real
DK Classic slate + real RotoGrinders + real Footballguys data, build the real blended-projection
pool (reusing `scripts/live_integration_check_projection.py`'s live-fetch pattern), then solve for
3 real lineups end to end. NOT part of `pytest` -- needs live credentials and a live NFL slate,
not reproducible in CI. Run by hand:

    .venv/bin/python scripts/live_integration_check_lineup.py
"""

from __future__ import annotations

from nfl_dfs.normalization.crosswalk import fetch_crosswalk
from nfl_dfs.normalization.matcher import reconcile_week
from nfl_dfs.normalization.registry import PlayerRegistry
from nfl_dfs.optimizer.lineup import SALARY_CAP, LineupGenerationError, generate_lineups
from nfl_dfs.projection.blend import (
    build_projection_pool,
    extract_dk_salary,
    extract_footballguys_points,
    extract_rotogrinders_fpts,
)

# Reuse the projection round's live-fetch helpers directly rather than re-duplicating the same
# HTTP call sequences a third time -- this script's only new job is what happens after the pool
# is built (the optimizer solve), same "pure consumption" discipline as blend.py's own docstring.
from scripts.live_integration_check_projection import (
    SEASON,
    WEEK,
    fetch_dk_raw,
    fetch_footballguys_raw,
    fetch_rotogrinders_raw,
)
from nfl_dfs.ingestion.pff import fetch_pff_players


def main() -> None:
    print("Fetching DraftKings (anchor)...")
    dk_payload, dk_pool = fetch_dk_raw()
    print(f"  {len(dk_pool)} players")

    print("Fetching PFF (needed by the matcher, not used in the blend)...")
    try:
        pff_pool = fetch_pff_players(season=SEASON, week=WEEK)
        print(f"  {len(pff_pool)} players")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        pff_pool = []

    print("Fetching RotoGrinders...")
    try:
        rg_payload, rg_pool = fetch_rotogrinders_raw()
        print(f"  {len(rg_pool)} players")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        rg_payload, rg_pool = {}, []

    print("Fetching Footballguys...")
    try:
        fbg_html_by_position, fbg_pool = fetch_footballguys_raw(WEEK)
        print(f"  {len(fbg_pool)} players")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        fbg_html_by_position, fbg_pool = {}, []

    print("Loading nflverse crosswalk...")
    crosswalk = fetch_crosswalk()

    registry = PlayerRegistry()
    identities = reconcile_week(dk_pool, pff_pool, rg_pool, fbg_pool, crosswalk, registry)
    print(f"\n{len(identities)} DK anchor players reconciled.\n")

    dk_salary = extract_dk_salary(dk_payload)
    rotogrinders_fpts = extract_rotogrinders_fpts(rg_payload) if rg_payload else {}
    footballguys_points = {}
    for html in fbg_html_by_position.values():
        footballguys_points.update(extract_footballguys_points(html))

    pool = build_projection_pool(identities, dk_salary, rotogrinders_fpts, footballguys_points)
    usable = [p for p in pool if p.blended_projection is not None and p.salary is not None]
    print(
        f"Projection pool: {len(pool)} total DK-eligible players, {len(usable)} usable "
        "(non-null blended_projection AND salary) -- the optimizer's actual candidate pool.\n"
    )

    print("=== Solving for 3 lineups ===")
    try:
        lineups = generate_lineups(pool, n=3)
    except LineupGenerationError as exc:
        print(f"LineupGenerationError: {exc}")
        return

    print(f"Generated {len(lineups)} lineup(s).\n")

    core_stacks = [lu.core_stack for lu in lineups]
    print(f"Distinct core stacks: {len(set(core_stacks))} of {len(lineups)}\n")

    slot_order = ["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST"]
    for i, lineup in enumerate(lineups, start=1):
        print(f"--- Lineup {i} ---")
        unused = SALARY_CAP - lineup.total_salary
        print(f"  total_salary=${lineup.total_salary:,} (${unused:,} unused of ${SALARY_CAP:,})")
        print(f"  total_projected_points={lineup.total_projected_points:.2f}")
        print(f"  core_stack_team={lineup.core_stack_team}, core_stack={sorted(lineup.core_stack)}")
        for slot in slot_order:
            p = lineup.slots[slot]
            print(
                f"    {slot:<5} {p.display_name:<24} {p.team:<4} {p.position:<4} "
                f"${p.salary:>6,}  proj={p.blended_projection:>6.2f}  "
                f"sources={p.source_values}"
            )
        print()


if __name__ == "__main__":
    main()
