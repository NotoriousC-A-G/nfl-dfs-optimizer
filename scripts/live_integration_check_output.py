"""Manual, live-network integration check for Stage 9 (`output/`): pull today's real DK Classic
slate + real RotoGrinders + real Footballguys data, build the real blended-projection pool, solve
3 real lineups, build real `StackProfile`s for the real games those lineups' core stacks belong
to, and produce all three PRD Section 8 deliverables (DK bulk-upload CSV, exposure report,
per-lineup rationale) end to end. NOT part of `pytest` -- needs live credentials and a live NFL
slate, not reproducible in CI. Run by hand:

    PYTHONPATH=. .venv/bin/python scripts/live_integration_check_output.py

Reuses `scripts/live_integration_check_lineup.py`'s live-fetch pattern for the pool/lineups, and
`scripts/live_integration_check_stack_profile.py`'s pattern for building real `StackProfile`s --
this script's only new job is what happens after both exist (Stage 9), same "pure consumption"
discipline the rest of this round's scripts follow.

**Real-world timing note, not a bug:** run live on 2026-09-13 (a Saturday evening per this
session), the Sunday afternoon main slate had already dropped out of `getcontests` (see
`ingestion/draftkings.py`'s module docstring) -- the only live Classic slate is DK's "Primetime"
slate (DAL @ NYG Sunday night, DEN @ KC Monday night), so this check targets that slate via
`require_label="Primetime"` rather than failing. A genuine, separate real-world consequence
surfaces from this: `RoleShareResult` (ADR-0019) needs *trailing* weeks 1..W-1 of play-by-play,
and week 1 has no completed prior week to trail -- so both games' `primary_stack_candidates`/
`bring_back_candidates` are expected to come back as `[]` (a real "no confident candidate yet"
result per `usage_share.py`'s own documented gate, not a bug in this script or in `StackProfile`).
`GameEnvironmentScore` itself is unaffected (pace/PROE has an early-season shrinkage-blend
fallback toward prior-season baseline, PRD Section 6/ADR-0003), so viability numbers and
`pivot_to`'s environment-only branch should still populate normally.
"""

from __future__ import annotations

import time
import warnings

import requests

from nfl_dfs.config import config
from nfl_dfs.correlation.stack_profile import build_stack_profile
from nfl_dfs.game_environment.score import (
    GameEnvironmentScore,
    ImpliedTotalInput,
    PaceProeInput,
    WeatherInput,
    compute_game_environment_score,
)
from nfl_dfs.ingestion.draftkings import (
    DRAFTABLES_URL,
    fetch_classic_draft_group_id,
    parse_draftables,
    select_classic_slate,
)
from nfl_dfs.ingestion.nflverse import fetch_pace_proe
from nfl_dfs.ingestion.odds_api import ODDS_URL, fetch_dk_implied_totals, parse_dk_odds_events
from nfl_dfs.ingestion.pff import fetch_pff_players
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR, fetch_role_shares
from nfl_dfs.normalization.crosswalk import fetch_crosswalk
from nfl_dfs.normalization.matcher import reconcile_week
from nfl_dfs.normalization.registry import PlayerRegistry
from nfl_dfs.optimizer.lineup import LineupGenerationError, generate_lineups
from nfl_dfs.output.weekly_output import build_weekly_output
from nfl_dfs.projection.blend import (
    build_projection_pool,
    extract_dk_salary,
    extract_footballguys_points,
    extract_rotogrinders_fpts,
)

from scripts.live_integration_check_projection import (
    fetch_footballguys_raw,
    fetch_rotogrinders_raw,
)

SEASON = 2026
WEEK = 1
NEUTRAL_WEATHER = WeatherInput(is_indoor=False)


def fetch_dk_raw_for_live_slate():
    """Same idea as `live_integration_check_projection.fetch_dk_raw`, but resilient to the "main
    slate already locked" real-world case this round's ingestion fix documents (module docstring)
    -- falls back to the "Primetime" slate rather than letting `SlateSelectionError` end the whole
    check. Also returns the resolved `DraftKingsSlate` (with its real `.games` list) so the
    StackProfile-building step below can scope itself to *this slate's actual games* rather than
    any game anywhere that happens to share a team name -- see main()'s comment on why that
    distinction turned out to matter live.
    """
    try:
        slate = select_classic_slate()
    except Exception as exc:  # noqa: BLE001
        print(f"  Main slate not resolvable ({exc}); falling back to require_label='Primetime'.")
        slate = select_classic_slate(require_label="Primetime")
    payload = requests.get(DRAFTABLES_URL.format(draft_group_id=slate.draft_group_id), timeout=20.0).json()
    return payload, parse_draftables(payload), slate


def _fetch_real_spreads() -> dict[tuple[str, str], float]:
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
    return {(g.away_team, g.home_team): g.home_spread for g in games if g.home_spread is not None}


def _build_ges(team: str, pace_proe_df, implied_total_z_by_team: dict[str, float]) -> GameEnvironmentScore:
    """Raises (via a plain `IndexError`/`KeyError`) if `team` has no pace/PROE row this pull --
    left uncaught here deliberately; the one call site below wraps each game in its own try/except
    so one missing team skips just that game's StackProfile rather than aborting the whole check.
    """
    row = pace_proe_df[pace_proe_df["team"] == team].iloc[0]
    pace_proe = PaceProeInput(
        pace_z=float(row["pace_z"]),
        proe_z=float(row["proe_z"]),
        weeks_played=int(row["weeks_played"]),
        shrinkage_weight=float(row["shrinkage_weight"]),
    )
    implied_total = ImpliedTotalInput(z=implied_total_z_by_team.get(team))
    return compute_game_environment_score(team, SEASON, WEEK, implied_total, pace_proe, NEUTRAL_WEATHER)


def main() -> None:
    print(f"=== Live Stage 9 (output) integration check -- season={SEASON}, week={WEEK} ===\n")

    print("Fetching DraftKings (anchor)...")
    dk_payload, dk_pool, dk_slate = fetch_dk_raw_for_live_slate()
    print(f"  {len(dk_pool)} players, slate games: {[(g.away_team, g.home_team) for g in dk_slate.games]}")

    print("Fetching PFF...")
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
    print(f"Projection pool: {len(pool)} total, {len(usable)} usable by the optimizer.\n")

    print("=== Solving for 3 lineups ===")
    try:
        lineups = generate_lineups(pool, n=3)
    except LineupGenerationError as exc:
        print(f"LineupGenerationError: {exc}")
        return
    print(f"Generated {len(lineups)} lineup(s). Core stack teams: {[lu.core_stack_team for lu in lineups]}\n")

    # ------------------------------------------------------------------------------------------
    # Build real StackProfiles for the actual games the generated lineups' core stacks belong to.
    #
    # **Scoped to THIS slate's real games specifically (dk_slate.games), not "any game anywhere
    # that happens to share a team name" -- a first draft of this script built a StackProfile for
    # every Odds-API game featuring a core-stack team, and it live-demonstrated exactly the
    # join-key gap `output/rationale.py`'s module docstring warns about: DAL's real game tonight
    # is DAL@NYG (DAL as *away*, no home-anchored thesis of its own for this game), but the Odds
    # API also had an unrelated WAS@DAL game on its board (a different week/matchup, nothing to do
    # with this slate) where DAL happens to be the home team -- team-name-only matching silently
    # grabbed that irrelevant game's thesis instead of correctly reporting "no anchored thesis for
    # DAL in tonight's real game." Restricting to `dk_slate.games` here is this script's fix;
    # `output/rationale.py` itself still can't tell the difference on its own if a caller hands it
    # StackProfiles from outside the current slate -- see this round's report for that flag.
    # ------------------------------------------------------------------------------------------
    print("=== Building real StackProfiles for this slate's actual games ===")
    slate_games = {(g.away_team, g.home_team) for g in dk_slate.games}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pace_proe_df = fetch_pace_proe(SEASON, WEEK)
    for w in caught:
        print(f"  WARNING (pace/PROE): {w.message}")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        implied_df = fetch_dk_implied_totals(SEASON)
    for w in caught:
        print(f"  WARNING (implied totals): {w.message}")
    implied_total_z_by_team = dict(zip(implied_df["team"], implied_df["implied_total_z"], strict=False))

    spreads = _fetch_real_spreads()
    print(f"Real spreads this pull: {spreads}")

    role_share_results = fetch_role_shares(SEASON, WEEK)
    role_share_by_key = {(r.team, r.role): r for r in role_share_results}

    stack_profiles = []
    for (away, home), spread in spreads.items():
        if (away, home) not in slate_games:
            continue  # not one of this slate's actual games -- see the comment block above
        home_wr = role_share_by_key.get((home, ROLE_WR))
        away_wr = role_share_by_key.get((away, ROLE_WR))
        home_rb = role_share_by_key.get((home, ROLE_RB))
        if home_wr is None or away_wr is None:
            print(f"  {away}@{home}: missing RoleShareResult for one side -- skipping StackProfile")
            continue
        try:
            ges_home = _build_ges(home, pace_proe_df, implied_total_z_by_team)
            ges_away = _build_ges(away, pace_proe_df, implied_total_z_by_team)
        except (IndexError, KeyError) as exc:
            print(f"  {away}@{home}: missing pace/PROE row for one side ({exc}) -- skipping")
            continue
        profile = build_stack_profile(ges_home, ges_away, spread, home_wr, away_wr, home_rb)
        stack_profiles.append(profile)
        print(
            f"  Built StackProfile for {away}@{home}: single_team_viability_home="
            f"{profile.single_team_viability_home}, game_stack_viability={profile.game_stack_viability}, "
            f"bring_back_status={profile.bring_back_status!r}, primary_stack_candidates="
            f"{profile.primary_stack_candidates}"
        )

    print(f"\n{len(stack_profiles)} StackProfile(s) built for this slate's relevant games.\n")

    # ------------------------------------------------------------------------------------------
    # Stage 9 orchestration: all three PRD Section 8 deliverables in one call.
    # ------------------------------------------------------------------------------------------
    print("=== build_weekly_output ===\n")
    weekly = build_weekly_output(lineups, identities, stack_profiles)

    print("--- Exposure report ---")
    print(weekly.exposure_report.render_text())

    print("\n--- Rationales ---")
    for r in weekly.rationales:
        print(f"\n{r.text}")
        print(f"  (stack_profile_game_id={r.stack_profile_game_id}, thesis_is_anchored={r.thesis_is_anchored})")

    print("\n--- DK bulk-upload CSV ---")
    print(weekly.dk_csv.csv_text)
    print("Notes:")
    for note in weekly.dk_csv.notes:
        print(f"  - {note}")

    out_path = "scripts/_live_output_check_lineups.csv"
    with open(out_path, "w") as f:
        f.write(weekly.dk_csv.csv_text)
    print(f"\nWrote real DK CSV export to {out_path}")


if __name__ == "__main__":
    main()
