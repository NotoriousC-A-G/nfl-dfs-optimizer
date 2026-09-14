"""Manual, live-network integration check for Stage 3 (`projection/blend.py`): pull today's real
DK Classic slate + real RotoGrinders + real Footballguys data, run the actual
identity-reconciliation + blending pipeline end to end, and report real coverage numbers. NOT
part of `pytest` -- same reasoning as `scripts/live_integration_check.py`: needs live credentials
and a live NFL slate, not reproducible in CI. Run by hand:

    .venv/bin/python scripts/live_integration_check_projection.py

This script deliberately duplicates a few lines of each ingestion module's live HTTP-call
sequence (using each module's already-exported helper functions/constants) rather than modifying
those modules -- `blend.py`'s scope note applies here too: those modules' `fetch_*` functions
return only the matcher's `SourcePlayer` identity shape (ADR-0013), not the raw payload/HTML the
projection values live in, and this round is pure consumption, not an ingestion-layer change.
"""

from __future__ import annotations

import time
from collections import Counter

import requests

from nfl_dfs.config import config
from nfl_dfs.ingestion import footballguys as fbg_module
from nfl_dfs.ingestion import rotogrinders as rg_module
from nfl_dfs.ingestion.draftkings import (
    DRAFTABLES_URL,
    fetch_classic_draft_group_id,
    parse_draftables,
)
from nfl_dfs.ingestion.pff import fetch_pff_players
from nfl_dfs.normalization.crosswalk import fetch_crosswalk
from nfl_dfs.normalization.matcher import reconcile_week
from nfl_dfs.normalization.registry import PlayerRegistry
from nfl_dfs.projection.blend import (
    build_projection_pool,
    extract_dk_avg_points_per_game,
    extract_dk_salary,
    extract_footballguys_points,
    extract_rotogrinders_fpts,
)

SEASON = 2026
WEEK = 1


def fetch_dk_raw():
    """Same live sequence as `fetch_draftkings_players`, but also returns the raw payload."""
    dg = fetch_classic_draft_group_id()
    payload = requests.get(DRAFTABLES_URL.format(draft_group_id=dg), timeout=20.0).json()
    return payload, parse_draftables(payload)


def fetch_rotogrinders_raw():
    """Same live sequence as `fetch_rotogrinders_players`, but also returns the raw JSON payload
    (needed for `extract_rotogrinders_fpts`, which `SourcePlayer` doesn't carry)."""
    cookie = config.rotogrinders_session_cookie
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}

    page = requests.get(rg_module.LINEUPHQ_PAGE_URL, headers=headers, timeout=20.0)
    page.raise_for_status()
    user, token = rg_module.extract_user_and_token(page.text)

    info = requests.get(
        rg_module.USER_INFO_URL, headers=headers, params={"user": user, "token": token}, timeout=20.0
    )
    info.raise_for_status()
    info_data = info.json()["data"]
    account_user_id, storage = info_data["id"], info_data["cloud_storage_key"]

    grids_response = requests.get(
        rg_module.PROJECTIONS_URL,
        headers=headers,
        params={
            "sport": "nfl", "site": "draftkings", "user_id": account_user_id, "storage": storage,
            "timestamp": int(time.time() * 1000), "list": 1,
        },
        timeout=20.0,
    )
    grids_response.raise_for_status()
    grid_id = rg_module.select_grid_id(rg_module.parse_available_grids(grids_response.json()))

    projections_response = requests.get(
        rg_module.PROJECTIONS_URL,
        headers=headers,
        params={
            "sport": "nfl", "site": "draftkings", "user_id": account_user_id, "storage": storage,
            "timestamp": int(time.time() * 1000), "source": grid_id,
        },
        timeout=20.0,
    )
    projections_response.raise_for_status()
    payload = projections_response.json()
    return payload, rg_module.parse_user_projections(payload)


def fetch_footballguys_raw(week: int):
    """Same live sequence as `fetch_footballguys_players`, but also returns each position pull's
    raw HTML (needed for `extract_footballguys_points`, which `SourcePlayer` doesn't carry)."""
    cookie = config.footballguys_session_cookie
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}

    html_by_position: dict[str, str] = {}
    by_id = {}
    for position in fbg_module.POSITIONS:
        response = requests.get(
            fbg_module.PROJECTIONS_URL,
            headers=headers,
            params={
                "componentIdNum": 1, "week": week, "nflTeam": "all", "pos": position,
                "durationTypeKey": "weekly", "posGroupKey": "all", "dfsSite": "draftkings", "reload": 1,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        html_by_position[position] = response.text
        for player in fbg_module.parse_projection_rows(response.text):
            by_id.setdefault(player.native_id, player)
    return html_by_position, list(by_id.values())


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
    dk_avg_ppg = extract_dk_avg_points_per_game(dk_payload)
    rotogrinders_fpts = extract_rotogrinders_fpts(rg_payload) if rg_payload else {}
    footballguys_points = {}
    for html in fbg_html_by_position.values():
        footballguys_points.update(extract_footballguys_points(html))

    print(f"DK salary rows extracted: {len(dk_salary)}")
    print(f"DK draftStatAttributes id==90 rows extracted: {len(dk_avg_ppg)}")
    print(f"RotoGrinders FPTS rows extracted: {len(rotogrinders_fpts)}")
    print(f"Footballguys Points rows extracted: {len(footballguys_points)}\n")

    pool = build_projection_pool(identities, dk_salary, rotogrinders_fpts, footballguys_points)

    # --- Coverage report ---------------------------------------------------------------
    by_count = Counter(p.source_count for p in pool)
    total = len(pool)
    print("=== Blended-projection coverage across the full DK-eligible pool ===")
    for count in sorted(by_count, reverse=True):
        pct = by_count[count] / total if total else 0.0
        label = {2: "full (both sources)", 1: "partial (1 of 2 sources)", 0: "none (zero sources)"}.get(
            count, f"{count} sources"
        )
        print(f"  {label:>28}: {by_count[count]:>4} / {total} ({pct:.1%})")

    dst_rows = [p for p in pool if p.position == "DST"]
    print(f"\nDST rows: {len(dst_rows)}")
    for row in dst_rows:
        print(
            f"  {row.team:>4} DST -- blended={row.blended_projection}, "
            f"sources={row.source_values}, salary={row.salary}"
        )

    zero_source = [p for p in pool if p.source_count == 0 and p.position != "DST"]
    if zero_source:
        print(f"\n{len(zero_source)} non-DST players with zero vendor coverage (sample up to 10):")
        for p in zero_source[:10]:
            print(f"  {p.display_name} ({p.team} {p.position}), salary={p.salary}")

    # --- DK id==90 investigation: does it look like a projection or a trailing average? -----
    print("\n=== DK draftStatAttributes id==90 vs. the real vendor blend (sanity check) ===")
    print(f"{'player':<24}{'team':<5}{'pos':<5}{'salary':>7}  {'blend':>7}  {'dk_id90':>8}  sources")
    sample = sorted(
        [p for p in pool if p.blended_projection is not None],
        key=lambda p: p.blended_projection,
        reverse=True,
    )[:15]
    for p in sample:
        dk_match = None
        identity = next(i for i in identities if i.canonical_id == p.canonical_id)
        dk_source = identity.sources.get("draftkings")
        if dk_source is not None and dk_source.native_id is not None:
            dk_match = dk_avg_ppg.get(dk_source.native_id)
        print(
            f"{p.display_name:<24}{p.team:<5}{p.position:<5}{p.salary or 0:>7}  "
            f"{p.blended_projection:>7.2f}  {str(dk_match):>8}  {p.source_values}"
        )

    # --- Named sanity checks on a few well-known players ---------------------------------
    print("\n=== Sanity check: a few well-known players ===")
    watch_list = ["Jefferson", "Gibbs", "McCaffrey", "Burrow", "Herbert", "Jackson", "Mahomes", "Allen"]
    for name_fragment in watch_list:
        matches = [p for p in pool if name_fragment.lower() in p.display_name.lower()]
        for p in matches:
            print(
                f"  {p.display_name} ({p.team} {p.position}): blended={p.blended_projection}, "
                f"salary={p.salary}, sources={p.source_values}"
            )


if __name__ == "__main__":
    main()
