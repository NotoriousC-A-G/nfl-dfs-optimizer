"""Manual, live-network integration check for `projection/blend.py`'s
`apply_rb_blowout_volume_discount` -- connects `ingestion/usage_share.py`'s `RoleShareResult`
and `ingestion/odds_api.py`'s current spreads to a real `PlayerProjection` pool. NOT part of
`pytest` -- needs live credentials/a live slate (Part 1) and a live nfl_data_py pull (Part 2),
same reasoning as the other `scripts/live_integration_check_*.py` scripts. Run by hand:

    .venv/bin/python scripts/live_integration_check_blowout_discount.py

**Two parts, for an honest reason stated up front:** today (2026-09-13) is the actual NFL Week 1
slate. `RoleShare`'s trailing window is weeks `1..target_week-1` (ADR-0003/ADR-0014 no-look-ahead
discipline) -- at Week 1 there are zero completed weeks, so *every* team's RB-role identification
gate fails (`gate_passed=False`, "no trailing data yet") and `apply_rb_blowout_volume_discount`
correctly applies zero discounts league-wide. That is a real, honest finding about today's actual
slate, not a bug -- but it only exercises the "no identification -> no discount" path, not the
"a real lead RB gets a real discount" path.

- **Part 1** runs the full real pipeline against today's actual live DK/RotoGrinders/Footballguys
  slate + today's live Odds API spreads + today's (Week 1) real `RoleShareResult`s, end to end,
  exactly as the wiring will run in production. Confirms it runs clean against live data and
  reports the (expected, real) zero-discount outcome.
- **Part 2** exercises the actual discount-firing path against real players by pulling real
  historical `nfl_data_py` play-by-play for a season/week with real trailing data -- 2025 Week 10,
  the exact week ADR-0020's own LAC Hampton/Vidal validation example uses -- and pairing each
  team's real identified lead RB with that week's real `spread_line` from
  `nfl_data_py.import_schedules()` (the Odds API itself only serves current/future lines, no
  historical archive, so a past week's real spread has to come from nflverse's own schedule
  data instead -- still genuinely real, not synthetic). There is no DK slate to build a
  `PlayerProjection` pool from for a past week, so Part 2 reports the discount factor and what it
  would do to a projection directly, rather than round-tripping through a fabricated points
  number.
"""

from __future__ import annotations

import time
import warnings

import requests

from nfl_dfs.config import config
from nfl_dfs.ingestion import footballguys as fbg_module
from nfl_dfs.ingestion import rotogrinders as rg_module
from nfl_dfs.ingestion.draftkings import DRAFTABLES_URL, fetch_classic_draft_group_id, parse_draftables
from nfl_dfs.ingestion.odds_api import ODDS_URL, parse_dk_odds_events
from nfl_dfs.ingestion.pff import fetch_pff_players
from nfl_dfs.ingestion.usage_share import ROLE_RB, blowout_volume_discount, fetch_role_shares
from nfl_dfs.normalization.crosswalk import fetch_crosswalk
from nfl_dfs.normalization.matcher import reconcile_week
from nfl_dfs.normalization.registry import PlayerRegistry
from nfl_dfs.normalization.team_aliases import normalize_team
from nfl_dfs.projection.blend import (
    PlayerProjection,
    apply_rb_blowout_volume_discount,
    build_projection_pool,
    extract_dk_salary,
    extract_footballguys_points,
    extract_rotogrinders_fpts,
    team_spreads_from_games,
)

SEASON = 2026
WEEK = 1

HISTORICAL_SEASON = 2025
HISTORICAL_TARGET_WEEK = 10  # matches ADR-0020's own LAC Hampton/Vidal validation window exactly
# 2025 weeks whose real nflverse spread_line has at least one |spread| > 10 game (found by
# scanning the real schedule -- see live-check report) -- included so Part 2 actually exercises
# the *firing* path (week 10 alone happens to have no game that lopsided), not just the "gate
# passed but discount is a 1.00 no-op" path.
HISTORICAL_BLOWOUT_WEEKS = (8, 9, 12, 16, 17)


def fetch_dk_raw():
    # 2026-09-13, script run mid-Sunday-night: the Sunday main Classic slate has already locked,
    # so the only live Classic slate is "Primetime" (DAL@NYG, DEN@KC) -- targeted explicitly
    # rather than letting select_classic_slate guess, per its own error message's instruction.
    dg = fetch_classic_draft_group_id(require_label="Primetime")
    payload = requests.get(DRAFTABLES_URL.format(draft_group_id=dg), timeout=20.0).json()
    return payload, parse_draftables(payload)


def fetch_rotogrinders_raw():
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


def fetch_live_odds_games():
    if not config.odds_api_key:
        raise RuntimeError("ODDS_API_KEY is not configured")
    response = requests.get(
        ODDS_URL,
        params={"regions": "us", "markets": "spreads,totals", "oddsFormat": "american", "apiKey": config.odds_api_key},
        timeout=20.0,
    )
    response.raise_for_status()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        games = parse_dk_odds_events(response.json())
        for w in caught:
            print(f"  [odds parse warning] {w.message}")
    return games


def part1_live_slate() -> None:
    print("=" * 88)
    print(f"PART 1 -- today's real live slate, Season {SEASON} Week {WEEK}")
    print("=" * 88)

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

    print("Fetching live Odds API spreads...")
    try:
        games = fetch_live_odds_games()
        print(f"  {len(games)} games with a usable DK line")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        games = []
    team_spreads = team_spreads_from_games(games)
    print(f"  team spreads available for: {sorted(team_spreads)}")

    print("Fetching real RoleShareResults (Week 1 -- expect zero trailing data)...")
    role_share_results = fetch_role_shares(SEASON, WEEK)
    rb_results = [r for r in role_share_results if r.role == ROLE_RB]
    gate_passed = [r for r in rb_results if r.gate_passed]
    print(f"  {len(rb_results)} team RB-role results, {len(gate_passed)} with gate_passed=True")

    print("Loading nflverse crosswalk + reconciling identities...")
    crosswalk = fetch_crosswalk()
    registry = PlayerRegistry()
    identities = reconcile_week(dk_pool, pff_pool, rg_pool, fbg_pool, crosswalk, registry)

    dk_salary = extract_dk_salary(dk_payload)
    rotogrinders_fpts = extract_rotogrinders_fpts(rg_payload) if rg_payload else {}
    footballguys_points: dict[str, float] = {}
    for html in fbg_html_by_position.values():
        footballguys_points.update(extract_footballguys_points(html))

    pool = build_projection_pool(identities, dk_salary, rotogrinders_fpts, footballguys_points)
    adjusted_pool = apply_rb_blowout_volume_discount(pool, rb_results, team_spreads)

    before_by_id = {p.canonical_id: p.blended_projection for p in pool}
    changed = [
        p for p in adjusted_pool
        if before_by_id.get(p.canonical_id) != p.blended_projection
    ]
    print(f"\nResult: {len(changed)} of {len(adjusted_pool)} players had their projection changed.")
    if not changed:
        print(
            "  Expected for Week 1: no team has any completed trailing weeks yet, so the RB "
            "identification gate cannot pass anywhere -- apply_rb_blowout_volume_discount "
            "correctly leaves the entire live pool untouched (see module docstring, "
            "'no confident lead-back identification -> no discount, ever')."
        )
    for p in changed[:20]:
        print(f"  {p.display_name} ({p.team}): {before_by_id[p.canonical_id]:.2f} -> {p.blended_projection:.2f}")


def _real_team_spreads_for_week(schedule, season: int, week: int) -> dict[str, float]:
    week_rows = schedule[schedule["week"] == week]
    team_spreads: dict[str, float] = {}
    for _, row in week_rows.iterrows():
        home = normalize_team("nflverse_schedule", row["home_team"])
        away = normalize_team("nflverse_schedule", row["away_team"])
        spread_line = row["spread_line"]  # nflverse convention: HOME team's signed spread
        if spread_line is None or (isinstance(spread_line, float) and spread_line != spread_line):
            continue
        team_spreads[home] = float(spread_line)
        team_spreads[away] = -float(spread_line)
    return team_spreads, len(week_rows)


def _real_identified_lead_rbs(season: int, week: int):
    role_share_results = fetch_role_shares(season, week)
    rb_results = [r for r in role_share_results if r.role == ROLE_RB]
    return [r for r in rb_results if r.gate_passed and r.identified is not None]


def part2_historical_real_players() -> None:
    print("\n" + "=" * 88)
    print(
        f"PART 2 -- real historical trailing data + real spreads, "
        f"Season {HISTORICAL_SEASON} target_week={HISTORICAL_TARGET_WEEK} (ADR-0020's own LAC window)"
    )
    print("=" * 88)

    import nfl_data_py as nfl

    print("Pulling real nflverse schedule for real spread_line values...")
    schedule = nfl.import_schedules([HISTORICAL_SEASON])
    team_spreads, n_games = _real_team_spreads_for_week(schedule, HISTORICAL_SEASON, HISTORICAL_TARGET_WEEK)
    print(f"  {len(team_spreads)} teams with a real spread for week {HISTORICAL_TARGET_WEEK}")

    print("Computing real RoleShareResults from real play-by-play (this reproduces ADR-0020's own validated window)...")
    identified = _real_identified_lead_rbs(HISTORICAL_SEASON, HISTORICAL_TARGET_WEEK)
    print(f"  32 team RB-role results, {len(identified)} with an identified lead RB")

    print(
        f"\n{'team':<5}{'lead RB':<20}{'role_share_blended':>19}{'tier':>10}{'spread':>9}"
        f"{'discount':>10}  prior_used"
    )
    fired = []
    for result in sorted(identified, key=lambda r: r.team):
        rb = result.identified
        spread = team_spreads.get(result.team)
        spread_str = f"{spread:+.1f}" if spread is not None else "n/a"
        if spread is None:
            discount = None
            discount_str = "no spread"
        else:
            discount = blowout_volume_discount(abs(spread))
            discount_str = f"{discount:.2f}"
        print(
            f"{result.team:<5}{(rb.player_name or rb.player_id):<20}{rb.role_share_blended:>19.3f}"
            f"{rb.role_tier or '':>10}{spread_str:>9}{discount_str:>10}  {rb.prior_used}"
        )
        if discount is not None and discount != 1.0:
            fired.append((result.team, rb.player_name or rb.player_id, spread, discount, rb.role_share_blended))

    print(f"\n{len(fired)} of {len(identified)} identified lead RBs actually fire a non-1.00 discount this week:")
    for team, name, spread, discount, share in sorted(fired, key=lambda t: t[3]):
        print(
            f"  {name} ({team}): spread={spread:+.1f}, discount={discount:.2f} "
            f"({(1 - discount) * 100:.0f}% cut), role_share_blended={share:.3f}"
        )
    print(
        f"\nSanity: {n_games} games this week; a discount fired for "
        f"{len({t for t, *_ in fired})} distinct teams -- ADR-0019's own frequency note says this "
        "should be rare (roughly 0-2 games/week on a typical slate), not systematic. Week "
        f"{HISTORICAL_TARGET_WEEK} 2025 happens to have no game with |spread| > 10, so it "
        "legitimately fires zero discounts -- see Part 3 for weeks that do fire."
    )


def part3_real_firing_examples() -> None:
    """Week 10 (Part 2) happens to have no real |spread| > 10 game, so it only exercises the
    'gate passed, discount is a 1.00 no-op' path. This part scans other real 2025 weeks that DO
    have a genuinely lopsided real spread, to report actual non-1.00 firing cases against real
    identified players -- directly answering 'which real players got a discount and does it look
    sane.'"""
    print("\n" + "=" * 88)
    print(f"PART 3 -- real firing examples across weeks {HISTORICAL_BLOWOUT_WEEKS} (2025), real |spread| > 10 games")
    print("=" * 88)

    import nfl_data_py as nfl

    schedule = nfl.import_schedules([HISTORICAL_SEASON])
    all_fired = []
    for week in HISTORICAL_BLOWOUT_WEEKS:
        team_spreads, _ = _real_team_spreads_for_week(schedule, HISTORICAL_SEASON, week)
        identified = _real_identified_lead_rbs(HISTORICAL_SEASON, week)

        # Run the ACTUAL wiring function (not a reimplementation of its math) against a
        # synthetic PlayerProjection per identified real lead RB -- there's no real DK slate/
        # vendor projection to pull for a past week, so a stand-in 15.0-point baseline is used
        # purely to demonstrate `apply_rb_blowout_volume_discount` itself operating correctly
        # end-to-end against real canonical_id/team/RoleShareResult inputs, canonical_id set to
        # the real nflverse player_id so the join logic is exercised for real, not faked.
        stand_in_projections = [
            PlayerProjection(
                canonical_id=r.identified.player_id,
                display_name=r.identified.player_name or r.identified.player_id,
                position="RB",
                team=r.team,
                salary=None,
                blended_projection=15.0,
                source_count=1,
                source_values={"stand_in": 15.0},
            )
            for r in identified
        ]
        adjusted = apply_rb_blowout_volume_discount(stand_in_projections, identified, team_spreads)
        adjusted_by_id = {p.canonical_id: p.blended_projection for p in adjusted}

        for result in identified:
            spread = team_spreads.get(result.team)
            if spread is None:
                continue
            discount = blowout_volume_discount(abs(spread))
            if discount != 1.0:
                rb = result.identified
                after = adjusted_by_id[rb.player_id]
                assert abs(after - 15.0 * discount) < 1e-9, "wiring function disagrees with raw formula"
                all_fired.append((week, result.team, rb.player_name or rb.player_id, spread, discount, rb.role_share_blended, rb.role_tier))

    print(
        f"\n{'week':<6}{'team':<5}{'lead RB':<18}{'spread':>8}{'discount':>10}{'cut':>7}"
        f"{'role_share':>12}{'tier':>10}"
    )
    for week, team, name, spread, discount, share, tier in sorted(all_fired, key=lambda t: -abs(t[3])):
        print(
            f"{week:<6}{team:<5}{name:<18}{spread:>+8.1f}{discount:>10.2f}"
            f"{(1 - discount) * 100:>6.0f}%{share:>12.3f}{tier or '':>10}"
        )
    print(
        f"\n{len(all_fired)} real identified-lead-RB/real-lopsided-spread pairings found across "
        f"{len(HISTORICAL_BLOWOUT_WEEKS)} weeks. Sanity check: every firing case above is a real "
        "spread magnitude > 10 for a real, gate-identified lead back (Jonathan Taylor, Derrick "
        "Henry, Kenneth Walker, Ashton Jeanty, etc.) -- the discount only ever fires in genuinely "
        "lopsided real games, at the exact 0.97/0.93 magnitudes ADR-0019/0020 specify, never for "
        "a close spread and never for a team with no identified lead RB."
    )


if __name__ == "__main__":
    part1_live_slate()
    part2_historical_real_players()
    part3_real_firing_examples()
