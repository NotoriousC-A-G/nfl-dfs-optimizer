"""Manual, live-network integration check for this round's `StackProfile` closure --
`primary_stack_candidates`/`bring_back_candidates`/`pivot_to`, wired to `ingestion/usage_share.py`'s
`RoleShareResult` (ADR-0019/ADR-0020), plus (this round) ADR-0021's `bring_back_status`/
`BRING_BACK_VIABILITY_FLOOR` gate. NOT part of `pytest` -- same pattern as
`live_integration_check_environment.py`: needs live network access (nflverse pbp/schedule pulls,
plus a configured `ODDS_API_KEY` for the real spread). Run by hand:

    .venv/bin/python scripts/live_integration_check_stack_profile.py

Assembles a REAL, if simplified, `GameEnvironmentScore` per team (live implied-total z-score and
live pace/PROE z-score, both data-confirmed sources this pipeline already has; weather is passed
as neutral -- `WeatherInput(is_indoor=False, ...all None...)` -- since this check is about
`StackProfile`'s new candidate/pivot_to logic, not weather fidelity; `compute_weather_subscore`
already treats `None` legs as neutral, same "unavailable, never imputed" discipline used
elsewhere, not a fabricated reading), a real spread from the Odds API, and real `RoleShareResult`s
from `fetch_role_shares` for the current season's completed week 1 -> then builds real
`StackProfile`s for a couple of real week-2 games and prints `primary_stack_candidates`,
`bring_back_candidates`, and `pivot_to` in full.
"""

from __future__ import annotations

import warnings

from nfl_dfs.correlation.stack_profile import BRING_BACK_VIABILITY_FLOOR, build_stack_profile
from nfl_dfs.game_environment.score import (
    GameEnvironmentScore,
    ImpliedTotalInput,
    PaceProeInput,
    WeatherInput,
    compute_game_environment_score,
)
from nfl_dfs.ingestion.nflverse import fetch_pace_proe
from nfl_dfs.ingestion.odds_api import ODDS_URL, parse_dk_odds_events
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR, fetch_role_shares

SEASON = 2026
WEEK = 2  # week 1 is complete (real trailing data exists for RoleShare); week 2+ is upcoming
NEUTRAL_WEATHER = WeatherInput(is_indoor=False)  # see module docstring -- not this check's focus


def _fetch_real_spreads() -> dict[tuple[str, str], float]:
    """Real DK spreads for whatever not-yet-started games the Odds API returns this pull, keyed by
    (away_team, home_team) -> `home_spread` (signed, negative = home favorite).

    **Live-environment quirk, noted plainly:** this sandbox's mocked nflverse/Odds-API backends
    regenerate their schedule/odds data independently on each call, so cross-referencing this
    pull's games against a separately-pulled `nfl_data_py.import_schedules()` (the way
    `odds_api.fetch_dk_implied_totals` does internally, via `schedule_week_map`) produced
    unreliable week-number matches when tried live for this check -- the same two calls, seconds
    apart, disagreed on which "week" a given matchup belonged to. This function sidesteps that by
    not needing a week label at all: it just returns whatever real matchups+spreads this one pull
    returned, which `main()` pairs directly with `fetch_role_shares`/`fetch_pace_proe` output
    (computed for all 32 teams regardless of which specific games are being demonstrated)."""
    import requests

    from nfl_dfs.config import config

    response = requests.get(
        ODDS_URL,
        params={
            "regions": "us",
            "markets": "spreads,totals",
            "oddsFormat": "american",
            "apiKey": config.odds_api_key,
        },
        timeout=20.0,
    )
    response.raise_for_status()
    games = parse_dk_odds_events(response.json())
    return {
        (g.away_team, g.home_team): g.home_spread
        for g in games
        if g.home_spread is not None
    }


def _build_ges(
    team: str,
    pace_proe_df,
    implied_total_z_by_team: dict[str, float],
) -> GameEnvironmentScore:
    row = pace_proe_df[pace_proe_df["team"] == team].iloc[0]
    pace_proe = PaceProeInput(
        pace_z=float(row["pace_z"]),
        proe_z=float(row["proe_z"]),
        weeks_played=int(row["weeks_played"]),
        shrinkage_weight=float(row["shrinkage_weight"]),
    )
    z = implied_total_z_by_team.get(team)
    implied_total = ImpliedTotalInput(z=z)
    return compute_game_environment_score(team, SEASON, WEEK, implied_total, pace_proe, NEUTRAL_WEATHER)


def main() -> None:
    print(f"=== Live StackProfile check (season={SEASON}, week={WEEK}) ===\n")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pace_proe_df = fetch_pace_proe(SEASON, WEEK)
    for w in caught:
        print(f"  WARNING (pace/PROE): {w.message}")

    from nfl_dfs.ingestion.odds_api import fetch_dk_implied_totals

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        implied_df = fetch_dk_implied_totals(SEASON)
    for w in caught:
        print(f"  WARNING (implied totals): {w.message}")
    # NOT filtered to `WEEK` -- see _fetch_real_spreads's docstring on this sandbox's independent-
    # per-call mock randomization: `implied_df`'s own internal week labels (from the same
    # unreliable schedule_week_map cross-join) can't be trusted to line up with `WEEK` either.
    # One row per team (last wins on a rare duplicate) is enough for this check's purpose -- a
    # real per-team z-score to build a real GameEnvironmentScore from.
    implied_total_z_by_team = dict(zip(implied_df["team"], implied_df["implied_total_z"], strict=False))

    spreads = _fetch_real_spreads()
    print(f"Real spreads found this pull: {len(spreads)} -- {list(spreads.items())}")

    role_share_results = fetch_role_shares(SEASON, WEEK)
    role_share_by_key = {(r.team, r.role): r for r in role_share_results}

    # Pick two real games from this pull, BOTH sides with a real implied-total z-score this pull
    # (so GameEnvironmentScore is actually available, not excluded per ADR-0017), that land in
    # different spread_dampener bands -- a near-pick'em (no bring-back discount) and a real
    # meaningful-lean/blowout-risk game (bring-back thesis measurably weakened) side by side.
    available_games = [
        (away, home, spread)
        for (away, home), spread in spreads.items()
        if away in implied_total_z_by_team and home in implied_total_z_by_team
    ]

    def _has_wr_candidates(team: str) -> bool:
        wr = role_share_by_key.get((team, ROLE_WR))
        return bool(wr and wr.candidates)

    # Prefer games where BOTH sides already have real week-1-completed trailing WR volume (some
    # week-1 games hadn't kicked off yet at this pull's "as of" moment -- their teams correctly
    # show [] candidates, which is its own valid demonstration, but a richer example is more
    # useful here) -- fall back to whatever's available if none qualify.
    rich_games = [g for g in available_games if _has_wr_candidates(g[0]) and _has_wr_candidates(g[1])]
    pool = rich_games or available_games

    by_band = sorted(pool, key=lambda t: abs(t[2]))
    sample_games = []
    if by_band:
        sample_games.append(by_band[0][:2])  # smallest |spread| with both sides available
    if len(by_band) > 1:
        sample_games.append(by_band[-1][:2])  # largest |spread| with both sides available

    # Always also try DET @ BUF specifically when this pull has it available: DET/Jahmyr Gibbs is
    # ADR-0019's own real-world motivating example (Chris's DET game-environment thesis), and DET's
    # RB role reliably shows the ADR-0020 uncontested_signal firing against this sandbox's mocked
    # week-1 data -- worth showing explicitly, not just leaving to chance via the band selection.
    det_buf = next(((a, h) for (a, h, _s) in available_games if {a, h} == {"DET", "BUF"}), None)
    if det_buf and det_buf not in sample_games:
        sample_games.append(det_buf)

    for away, home in sample_games:
        spread = spreads[(away, home)]
        print(f"\n--- {away} @ {home} (home_spread={spread:+.1f}) ---")

        if home not in set(pace_proe_df["team"]) or away not in set(pace_proe_df["team"]):
            print("  (missing pace/PROE row for one side this pull -- skipping)")
            continue

        ges_home = _build_ges(home, pace_proe_df, implied_total_z_by_team)
        ges_away = _build_ges(away, pace_proe_df, implied_total_z_by_team)
        print(f"  {home} GameEnvironmentScore: {ges_home.composite_score}")
        print(f"  {away} GameEnvironmentScore: {ges_away.composite_score}")

        home_wr = role_share_by_key[(home, ROLE_WR)]
        away_wr = role_share_by_key[(away, ROLE_WR)]
        home_rb = role_share_by_key[(home, ROLE_RB)]

        profile = build_stack_profile(ges_home, ges_away, spread, home_wr, away_wr, home_rb)

        print(f"  single_team_viability_home={profile.single_team_viability_home}")
        print(f"  single_team_viability_away={profile.single_team_viability_away}")
        print(f"  game_stack_viability={profile.game_stack_viability} (floor={BRING_BACK_VIABILITY_FLOOR})")
        print(f"  bring_back_status={profile.bring_back_status!r}")  # ADR-0021
        print("  primary_stack_candidates:")
        for c in profile.primary_stack_candidates or []:
            print(f"    - {c.player_name} (role_share_blended={c.role_share_blended:.3f}, tier={c.role_tier})")
        print("  bring_back_candidates:")
        for c in profile.bring_back_candidates or []:
            print(f"    - {c.player_name} (role_share_blended={c.role_share_blended:.3f}, tier={c.role_tier})")
        print(f"  pivot_to: {profile.pivot_to}")
        print(f"  notes: {profile.notes}")

    # Also print the anchor team's RB role-share detail for the printed games, so the
    # uncontested_signal wiring (or its absence) is visible against real numbers, not just
    # inferred from the pivot_to text above.
    print("\n--- RB RoleShare detail for printed anchor teams (uncontested_signal wiring) ---")
    for away, home in sample_games:
        rb = role_share_by_key.get((home, ROLE_RB))
        if rb is None:
            continue
        print(f"{home}: gate_passed={rb.gate_passed}, identified={rb.identified}")

    # Mirror-image demonstration: the DET @ BUF game above anchors on BUF (the schedule's home
    # team), so ADR-0019's own motivating example (Jahmyr Gibbs's uncontested DET backfield) never
    # gets a chance to show up in pivot_to -- it's on the away/bring-back side there. Building the
    # SAME real game with DET as the chosen anchor instead demonstrates both the uncontested_signal
    # wiring end-to-end against real (mocked) data, and this round's flagged "anchor-team choice"
    # design question concretely: the same game legitimately supports either team as the stack's
    # own QB side, and this module only builds one direction at a time.
    if det_buf:
        away, home = det_buf  # ("DET", "BUF")
        spread = spreads[(away, home)]
        print(f"\n--- Mirror-image: {home} @ {away} (DET chosen as anchor instead of BUF) ---")
        det_ges = _build_ges("DET", pace_proe_df, implied_total_z_by_team)
        buf_ges = _build_ges("BUF", pace_proe_df, implied_total_z_by_team)
        det_wr = role_share_by_key[("DET", ROLE_WR)]
        buf_wr = role_share_by_key[("BUF", ROLE_WR)]
        det_rb = role_share_by_key[("DET", ROLE_RB)]
        mirror_profile = build_stack_profile(det_ges, buf_ges, -spread, det_wr, buf_wr, det_rb)
        print("  primary_stack_candidates:")
        for c in mirror_profile.primary_stack_candidates or []:
            print(f"    - {c.player_name} (role_share_blended={c.role_share_blended:.3f}, tier={c.role_tier})")
        print(f"  pivot_to: {mirror_profile.pivot_to}")

    # ------------------------------------------------------------------------------------------
    # ADR-0021 before/after summary -- scan EVERY real game this pull returned (not just the
    # hand-picked samples above) and report which ones lose their bring-back thesis under the new
    # BRING_BACK_VIABILITY_FLOOR gate vs which keep it. "Before" (pre-ADR-0021) would have
    # populated bring_back_candidates from away_wr's candidates unconditionally whenever both
    # GameEnvironmentScores were available and away_wr had any -- this reconstructs that prior
    # behavior for comparison without needing the old code path.
    # ------------------------------------------------------------------------------------------
    print("\n=== ADR-0021 before/after: bring-back thesis gate across all real games this pull ===")
    print(f"BRING_BACK_VIABILITY_FLOOR = {BRING_BACK_VIABILITY_FLOOR}\n")

    kept, lost, unaffected = [], [], []
    for away, home, spread in available_games:
        if home not in set(pace_proe_df["team"]) or away not in set(pace_proe_df["team"]):
            continue
        home_wr = role_share_by_key.get((home, ROLE_WR))
        away_wr = role_share_by_key.get((away, ROLE_WR))
        if home_wr is None or away_wr is None:
            continue

        ges_home = _build_ges(home, pace_proe_df, implied_total_z_by_team)
        ges_away = _build_ges(away, pace_proe_df, implied_total_z_by_team)
        if not ges_home.is_available or not ges_away.is_available:
            continue  # environment_unavailable either way -- not this gate's concern

        profile = build_stack_profile(ges_home, ges_away, spread, home_wr, away_wr)
        would_have_populated_before = bool(away_wr.candidates)  # pre-ADR-0021: unconditional
        gsv = profile.game_stack_viability
        bottleneck = min(ges_home.composite_score, ges_away.composite_score)

        row = {
            "game": f"{away}@{home}",
            "spread": spread,
            "bottleneck": bottleneck,
            "gsv": gsv,
            "status": profile.bring_back_status,
        }
        if not would_have_populated_before:
            unaffected.append(row)  # no candidate existed either way -- gate is moot here
        elif profile.bring_back_status == "game_stack_not_viable":
            lost.append(row)
        else:
            kept.append(row)

    def _print_rows(rows: list[dict]) -> None:
        for r in rows:
            print(
                f"  {r['game']:<12} spread={r['spread']:+6.1f}  bottleneck={r['bottleneck']:6.1f}  "
                f"game_stack_viability={r['gsv']:6.1f}  status={r['status']}"
            )

    print(f"-- LOST bring-back thesis under the new floor ({len(lost)} games) --")
    _print_rows(lost)
    print(f"\n-- KEPT bring-back thesis (still 'populated', {len(kept)} games) --")
    _print_rows(kept)
    print(f"\n-- Unaffected (no opposing WR candidate existed pre- or post-ADR-0021, {len(unaffected)} games) --")
    _print_rows(unaffected)

    print(
        "\nSpot-check: every LOST game above should show a genuinely low game_stack_viability "
        "driven by a weak bottleneck and/or a lopsided |spread| (per the spread_dampener bands: "
        ">10 -> 0.40, >14 -> 0.25) -- not a healthy bottleneck with a near-pick'em spread."
    )


if __name__ == "__main__":
    main()
