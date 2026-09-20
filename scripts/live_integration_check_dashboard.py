"""Manual, live-network integration check for `dashboard/renderer.py`: run the full real pipeline
end to end -- real DK slate, real vendor projections, real lineups, real `WeeklyOutput` (reusing
`scripts/live_integration_check_output.py`'s pattern) -- then build real `PlayerDetailRecord`s
(`composition/player_detail.py`) for this slate's full reconciled player pool and render a real
dashboard HTML file to disk. NOT part of `pytest` -- needs live credentials and a live NFL slate,
not reproducible in CI. Run by hand:

    PYTHONPATH=. .venv/bin/python scripts/live_integration_check_dashboard.py

**Real-world timing note (inherited from `live_integration_check_output.py`):** run in week 1,
before any games have been played -- `RoleShareResult`/`PlayerSnapShare`/red-zone trailing data
all need completed weeks `1..W-1`, and week 1 has none, so those three sections are expected to
come back as real, honest "no trailing data yet" reasons for most players, not populated numbers
-- a genuine (not synthetic) exercise of this dashboard's nullability discipline. PFF's grade
facets (`own_scheme_splits`, opponent coverage tendency) use ADR-0014's week-1 prior-season
fallback instead, so those two sections DO populate with real 2024/2025 PFF grades.
"""

from __future__ import annotations

import warnings

from nfl_dfs.analysis.dup_risk_calibration import DEFAULT_RECENT_WINDOW, build_dup_risk_lookup_table
from nfl_dfs.analysis.ownership_calibration import run_full_calibration
from nfl_dfs.ceiling.signals import (
    red_zone_ceiling_signals,
    role_share_ceiling_signals,
    trailing_red_zone_share_by_week,
    wr_red_zone_role_security_discount,
)
from nfl_dfs.ingestion.qb_rushing_profile import trailing_qb_rushing_profiles
from nfl_dfs.ingestion.receiving_profile import trailing_receiving_profiles
from nfl_dfs.composition.lineup_dup_risk import assess_lineup_dup_risk, build_projected_ownership_by_canonical_id
from nfl_dfs.composition.player_detail import build_gsis_to_pff_id_map, build_player_detail_record
from nfl_dfs.config import config
from nfl_dfs.dashboard.renderer import SlateGameRow, write_dashboard_html
from nfl_dfs.game_environment.score import (
    GameEnvironmentScore,
    ImpliedTotalInput,
    PaceProeInput,
    PlayerInjuryStatus,
    WeatherInput,
    compute_game_environment_score,
)
from nfl_dfs.ingestion.nflverse import fetch_pace_proe
from nfl_dfs.ingestion.odds_api import ODDS_URL, fetch_dk_implied_totals, implied_team_totals, parse_dk_odds_events
from nfl_dfs.ingestion.pff import fetch_matchup_grades, team_coverage_tendency
from nfl_dfs.ingestion.rotogrinders import filter_to_main_slate, parse_projected_ownership
from nfl_dfs.ingestion.rotogrinders_injuries import fetch_injury_report
from nfl_dfs.ingestion.snap_share import fetch_snap_shares
from nfl_dfs.ingestion.usage_share import ROLE_RB, ROLE_WR, aggregate_player_trailing_red_zone, fetch_role_shares
from nfl_dfs.ingestion.weather import WeatherReading, fetch_weather_reading
from nfl_dfs.matchup.context import MatchupFacetInputs, PlayerMatchupInput, build_matchup_context_pool
from nfl_dfs.normalization.crosswalk import fetch_crosswalk
from nfl_dfs.normalization.injury_lookup import team_injuries
from nfl_dfs.normalization.matcher import reconcile_week
from nfl_dfs.normalization.registry import PlayerRegistry
from nfl_dfs.optimizer.lineup import EXCLUDED_INJURY_STATUSES, LineupGenerationError
from nfl_dfs.output.weekly_output import build_weekly_output
from nfl_dfs.ownership.leverage import build_leverage_assessments
from nfl_dfs.composition.player_detail import _pff_native_id_for_identity
from nfl_dfs.projection.blend import (
    apply_matchup_context,
    build_projection_pool,
    extract_dk_injury_status,
    extract_dk_salary,
    extract_footballguys_points,
    extract_rotogrinders_fpts,
)

from scripts.live_integration_check_output import _build_ges, _fetch_real_spreads, fetch_dk_raw_for_live_slate
from scripts.live_integration_check_projection import fetch_footballguys_raw, fetch_rotogrinders_raw

SEASON = 2026
WEEK = 2
# Set to a specific DK draftGroupId to target that exact slate directly, bypassing auto-detection
# entirely -- required once DK is serving more than one plausible main-shaped slate at once (a
# real, live 2026-09-19 case; see ingestion.draftkings.fetch_slate_by_draft_group_id's own
# docstring). Leave None to auto-detect (works fine when only one real main slate is live) -- if
# auto-detection hits real ambiguity, it now fails loudly with the real candidate ids to choose
# from here, rather than silently substituting an unrelated slate.
DRAFT_GROUP_ID: int | None = 153428  # confirmed live 2026-09-19: the real 13-game Sunday main slate


def main() -> None:
    print(f"=== Live dashboard integration check -- season={SEASON}, week={WEEK} ===\n")

    print("Fetching DraftKings (anchor)...")
    dk_payload, dk_pool, dk_slate = fetch_dk_raw_for_live_slate(draft_group_id=DRAFT_GROUP_ID)
    slate_teams = sorted(dk_slate.teams)
    print(f"  draft_group_id={dk_slate.draft_group_id} '{dk_slate.slate_label}' -- {len(dk_pool)} players, "
          f"slate games: {[(g.away_team, g.home_team) for g in dk_slate.games]}")

    print("Fetching PFF...")
    from nfl_dfs.ingestion.pff import fetch_pff_players

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

    # gsis_to_pff_id and opponent_of only depend on crosswalk/dk_slate, both already available --
    # built here (not down with the rest of the Player Detail section, where they used to live)
    # specifically so MatchupContext can be computed BEFORE lineup generation, not after. Same
    # "moved up, not duplicated" pattern ADR-0035/0037's dup-risk-aware wiring already established.
    gsis_to_pff_id = build_gsis_to_pff_id_map(crosswalk)
    opponent_of: dict[str, str] = {}
    for g in dk_slate.games:
        opponent_of[g.away_team] = g.home_team
        opponent_of[g.home_team] = g.away_team

    print("Fetching real PFF grade facets for MatchupContext (ADR-0014/0022, all 6 inputs)...")
    receiving_scheme_grades = fetch_matchup_grades("receiving/scheme", WEEK, SEASON)
    coverage_scheme_grades = fetch_matchup_grades("defense/coverage_scheme", WEEK, SEASON)
    run_blocking_grades = fetch_matchup_grades("offense/run_blocking", WEEK, SEASON)
    run_defense_grades = fetch_matchup_grades("defense/run", WEEK, SEASON)
    pass_blocking_grades = fetch_matchup_grades("offense/pass_blocking", WEEK, SEASON)
    pass_rush_grades = fetch_matchup_grades("defense/pass_rush", WEEK, SEASON)
    coverage_tendency_by_team = team_coverage_tendency(coverage_scheme_grades)
    print(
        f"  receiving/scheme population={receiving_scheme_grades.population!r}, "
        f"{len(receiving_scheme_grades.by_player_id)} player row(s)"
    )
    print(f"  {len(coverage_tendency_by_team)} team(s) with a coverage tendency rollup")

    print("Building real MatchupContext for every reconciled identity (ADR-0035 wiring -- now actually applied to projections)...")
    matchup_facets = MatchupFacetInputs(
        run_blocking=run_blocking_grades,
        run_defense=run_defense_grades,
        pass_blocking=pass_blocking_grades,
        pass_rush=pass_rush_grades,
        coverage_scheme=coverage_scheme_grades,
        receiving_scheme=receiving_scheme_grades,
    )
    matchup_players = [
        PlayerMatchupInput(
            canonical_player_id=identity.canonical_id,
            team=identity.team,
            position=identity.position,
            pff_native_id=_pff_native_id_for_identity(identity, gsis_to_pff_id),
        )
        for identity in identities
    ]
    matchup_context_by_canonical_id = build_matchup_context_pool(matchup_players, opponent_of, matchup_facets)
    print(f"  {len(matchup_context_by_canonical_id)} identit(y/ies) with a real MatchupContext this week")

    dk_salary = extract_dk_salary(dk_payload)
    dk_injury_status = extract_dk_injury_status(dk_payload)
    rotogrinders_fpts = extract_rotogrinders_fpts(rg_payload) if rg_payload else {}
    footballguys_points = {}
    for html in fbg_html_by_position.values():
        footballguys_points.update(extract_footballguys_points(html))

    pool = build_projection_pool(identities, dk_salary, rotogrinders_fpts, footballguys_points, dk_injury_status)
    pool_before_matchup_context = {p.canonical_id: p.blended_projection for p in pool}
    pool = apply_matchup_context(pool, matchup_context_by_canonical_id)
    n_adjusted = sum(
        1
        for p in pool
        if p.blended_projection is not None
        and pool_before_matchup_context.get(p.canonical_id) is not None
        and p.blended_projection != pool_before_matchup_context[p.canonical_id]
    )
    print(f"  {n_adjusted} player(s) had their blended_projection actually rescaled by a real MatchupContext multiplier\n")

    usable = [p for p in pool if p.blended_projection is not None and p.salary is not None]
    from collections import Counter

    status_counts = Counter(p.dk_injury_status for p in pool if p.dk_injury_status is not None)
    print(f"Projection pool: {len(pool)} total, {len(usable)} usable by the optimizer.")
    print(f"  DK injury/roster status (excluded from lineup generation: {sorted(EXCLUDED_INJURY_STATUSES)}): {dict(status_counts)}\n")

    projections_by_canonical_id = {p.canonical_id: p for p in pool}

    print("Building real chalk/leverage assessments (ADR-0025/0026)...")
    leverage_by_native_id: dict[str, object] = {}
    if rg_payload:
        all_ownership_rows = parse_projected_ownership(rg_payload)
        main_slate_rows = filter_to_main_slate(all_ownership_rows)
        print(f"  {len(all_ownership_rows)} players across every slate window, {len(main_slate_rows)} on the main slate")
        try:
            calibration_bundle = run_full_calibration()
            assessments = build_leverage_assessments(main_slate_rows, calibration_bundle.production)
            leverage_by_native_id = {a.native_id: a for a in assessments}
            n_chalk = sum(1 for a in assessments if a.is_chalk)
            n_leverage = sum(1 for a in assessments if a.is_leverage)
            print(f"  {len(assessments)} assessments built: {n_chalk} chalk, {n_leverage} leverage")
        except ValueError as exc:
            print(f"  FAILED to build production calibration: {exc}")
    else:
        print("  no RotoGrinders payload this pull -- skipping ownership/leverage")

    print("Building real dup-risk lookup table (ADR-0032/0033) and live ownership join (ADR-0035/0037)...")
    dup_risk_table = None
    try:
        # Same recent-window scoping rationale as the (now-removed) post-hoc build below: a real,
        # already-computed production window (2023-2025), not a re-run of the full leave-one-out
        # stability test on every dashboard build (that's a periodic/offline analysis,
        # scripts/dup_risk_calibration_report.py, not something a live per-slate build should redo).
        dup_risk_table = build_dup_risk_lookup_table(seasons=DEFAULT_RECENT_WINDOW)
        print(f"  dup-risk table built from {dup_risk_table.n_rows} real lineup row(s), seasons={dup_risk_table.seasons}")
    except ValueError as exc:
        print(f"  SKIPPED: {exc}")
    projected_ownership_by_canonical_id = build_projected_ownership_by_canonical_id(identities, leverage_by_native_id)
    print(f"  {len(projected_ownership_by_canonical_id)} identit(y/ies) with a live projected-ownership read")

    # ------------------------------------------------------------------------------------------
    # NflAgentConstructor Phase B2: StackProfiles, real circumstance detection, and the pbp fetch
    # + Component A ceiling signals are all built here -- BEFORE lineup generation, not after --
    # so a future agent-preference objective delta (Phase B3+) can read real stack/circumstance/
    # ceiling signals at generation time instead of only after lineups already exist. Pure
    # reordering: every block below is unchanged from its prior position further down this
    # function, just moved up. `role_share_results`/`pbp`/`snap_shares_by_player`/
    # `red_zone_trailing`/`ceiling_signals_by_gsis_id` are computed once here and reused, not
    # duplicated, by the (still-in-place) Player Detail section later in this function.
    # ------------------------------------------------------------------------------------------

    print("=== Building real StackProfiles (for rationale text) ===")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        pace_proe_df = fetch_pace_proe(SEASON, WEEK)
        implied_df = fetch_dk_implied_totals(SEASON)
    implied_total_z_by_team = dict(zip(implied_df["team"], implied_df["implied_total_z"], strict=False))

    slate_games_pairs = {(g.away_team, g.home_team) for g in dk_slate.games}
    spreads = _fetch_real_spreads()

    role_share_results = fetch_role_shares(SEASON, WEEK)
    role_share_by_key = {(r.team, r.role): r for r in role_share_results}

    from nfl_dfs.correlation.stack_profile import build_stack_profile

    stack_profiles = []
    for (away, home), spread in spreads.items():
        if (away, home) not in slate_games_pairs:
            continue
        home_wr = role_share_by_key.get((home, ROLE_WR))
        away_wr = role_share_by_key.get((away, ROLE_WR))
        home_rb = role_share_by_key.get((home, ROLE_RB))
        away_rb = role_share_by_key.get((away, ROLE_RB))
        if home_wr is None or away_wr is None:
            continue
        try:
            ges_home = _build_ges(home, pace_proe_df, implied_total_z_by_team)
            ges_away = _build_ges(away, pace_proe_df, implied_total_z_by_team)
        except (IndexError, KeyError):
            continue
        stack_profiles.append(
            build_stack_profile(
                ges_home,
                ges_away,
                spread,
                home_wr,
                away_wr,
                home_rb,
                away_rb,
                matchup_context_by_player_id=matchup_context_by_canonical_id,
            )
        )
    print(f"  {len(stack_profiles)} StackProfile(s) built.\n")
    matchup_ranked_count = sum(
        1 for p in stack_profiles if any("combined with MatchupContext" in note for note in p.notes)
    )
    print(f"  {matchup_ranked_count} StackProfile(s) with WR candidates ranked using real MatchupContext.\n")
    rb_candidate_count = sum(
        1 for p in stack_profiles if p.primary_rb_candidate is not None or p.bring_back_rb_candidate is not None
    )
    print(f"  {rb_candidate_count} StackProfile(s) with a real RB stack/bring-back candidate.\n")

    print("=== Detecting real circumstance changes (analysis/circumstance/) ===")
    import os

    from nfl_dfs.analysis.circumstance import (
        build_anthropic_messages_client,
        detect_depth_chart_divergence,
        detect_injury_circumstance_change,
        detect_matchup_extreme_circumstance,
        find_relevant_articles,
        synthesize_circumstance,
    )
    from nfl_dfs.ingestion.nflverse_depth_charts import fetch_current_depth_chart
    from nfl_dfs.storage.circumstance_cache_store import FilesystemCircumstanceCache
    from nfl_dfs.storage.footballguys_article_store import read_all_articles as read_all_footballguys_articles
    from nfl_dfs.storage.rotogrinders_article_store import read_all_articles as read_all_rotogrinders_articles

    # FORCE_CIRCUMSTANCE_REFRESH=1 bypasses the cache READ for this run (still writes the fresh
    # result) -- for when a caller knows new evidence landed that the cache key deliberately
    # doesn't track (circumstance_cache_store.py's own disclosed non-invalidation).
    force_circumstance_refresh = os.environ.get("FORCE_CIRCUMSTANCE_REFRESH") == "1"
    circumstance_cache = FilesystemCircumstanceCache()
    circumstance_stats = {"real_calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0}
    circumstance_assessments_by_gsis_id: dict[str, object] = {}

    def _run_synthesis(source, articles_for_source, *, label: str) -> None:
        """Shared bookkeeping for every detector type: cache check, the real synthesize_circumstance
        call, per-run stats, and the assessment dict every detector writes its subjects into. `label`
        is just this call's own print-line prefix (each detector's own real inputs, e.g. "MIN RB:
        J.Mason OUT" or "J.Gibbs (RB) vs CHI") -- everything else here is detector-agnostic."""
        was_cached = not force_circumstance_refresh and circumstance_cache.has(source)
        try:
            assessment = synthesize_circumstance(
                source,
                articles_for_source,
                client=anthropic_client,
                cache=circumstance_cache,
                force_refresh=force_circumstance_refresh,
            )
        except Exception as exc:  # noqa: BLE001 -- one bad synthesis call must not kill the run
            print(f"  {label}: synthesis FAILED -- {exc}")
            return
        for subject_id in source.circumstance_subjects():
            circumstance_assessments_by_gsis_id[subject_id] = assessment
        if was_cached:
            circumstance_stats["cache_hits"] += 1
            print(f"  {label} -> cached POV reused (no new API call)")
        else:
            circumstance_stats["real_calls"] += 1
            circumstance_stats["input_tokens"] += assessment.input_tokens
            circumstance_stats["output_tokens"] += assessment.output_tokens
            circumstance_stats["thinking_tokens"] += assessment.thinking_tokens
            print(
                f"  {label} -> synthesized POV ({len(articles_for_source)} article(s) used, "
                f"{assessment.input_tokens}in/{assessment.output_tokens}out/{assessment.thinking_tokens}think tokens)"
            )

    dk_status_by_canonical_id = {p.canonical_id: p.dk_injury_status for p in pool}
    # usage_share.py's RB/WR role computation is plurality-of-volume, not position-filtered (that
    # module's own disclosed "Judgment call 2") -- a backup QB's scramble/kneel carries can show up
    # in an RB-role RoleShareResult (confirmed live: Carson Wentz, MIN). Real position data (already
    # reconciled, identities) is used two ways below: as a correctness gate on whether a circumstance
    # fires at all (detect_injury_circumstance_change's departed check), and as real context handed
    # to the synthesis step so it can reason about a same-role anomaly itself -- never as a silent
    # pre-filter deciding which candidates the model gets to see at all.
    position_by_gsis_id = {i.nflverse_gsis_id: i.position for i in identities if i.nflverse_gsis_id}
    circumstance_changes = [
        change
        for role_share in role_share_results
        if (
            change := detect_injury_circumstance_change(
                role_share, dk_status_by_canonical_id, position_by_player_id=position_by_gsis_id
            )
        )
        is not None
    ]

    display_name_by_canonical_id = {i.canonical_id: i.display_name for i in identities}
    matchup_extreme_changes = [
        extreme
        for identity in identities
        if (matchup := matchup_context_by_canonical_id.get(identity.canonical_id)) is not None
        and (
            extreme := detect_matchup_extreme_circumstance(
                matchup, display_name_by_canonical_id.get(identity.canonical_id, identity.canonical_id), SEASON, WEEK
            )
        )
        is not None
    ]

    print("  Fetching real current NFL depth chart (nflverse)...")
    depth_chart_entries = fetch_current_depth_chart(SEASON)
    print(f"  {len(depth_chart_entries)} real skill-position depth-chart entries (most recent snapshot).")
    depth_chart_divergences = [
        divergence
        for role_share in role_share_results
        if (
            divergence := detect_depth_chart_divergence(
                depth_chart_entries, role_share, position_by_player_id=position_by_gsis_id
            )
        )
        is not None
    ]

    print(
        f"  {len(circumstance_changes)} real injury circumstance(s), "
        f"{len(matchup_extreme_changes)} real matchup-extreme circumstance(s), "
        f"{len(depth_chart_divergences)} real depth-chart divergence(s) detected."
    )

    if circumstance_changes or matchup_extreme_changes or depth_chart_divergences:
        if config.anthropic_api_key:
            anthropic_client = build_anthropic_messages_client(config.anthropic_api_key)
            fbg_articles = read_all_footballguys_articles()
            rg_articles = read_all_rotogrinders_articles()
            archived_articles = [*fbg_articles, *rg_articles]
            n_platform_mixed = sum(1 for a in rg_articles if a.platform_mixed)
            print(
                f"  {len(fbg_articles)} archived Footballguys + {len(rg_articles)} archived "
                f"RotoGrinders ({n_platform_mixed} platform-mixed) article(s) available as evidence."
            )
            for change in circumstance_changes:
                relevant_articles = find_relevant_articles(change.team, archived_articles, week=WEEK)
                remaining_names = ", ".join(r.player_name or r.player_id for r in change.remaining)
                label = (
                    f"{change.team} {change.role}: {change.departed.player_name or change.departed.player_id} "
                    f"{change.departed_status} -> {remaining_names}"
                )
                _run_synthesis(change, relevant_articles, label=label)
            for extreme in matchup_extreme_changes:
                relevant_articles = find_relevant_articles(extreme.team, archived_articles, week=WEEK)
                direction = "favorable" if extreme.matchup.combined_multiplier > 1.0 else "unfavorable"
                label = (
                    f"{extreme.team} {extreme.position} matchup: {extreme.player_name} vs {extreme.matchup.opponent} "
                    f"({extreme.matchup.combined_multiplier:.3f}x, {direction})"
                )
                _run_synthesis(extreme, relevant_articles, label=label)
            for divergence in depth_chart_divergences:
                relevant_articles = find_relevant_articles(divergence.team, archived_articles, week=WEEK)
                label = (
                    f"{divergence.team} {divergence.role} depth-chart divergence: depth chart says "
                    f"{divergence.depth_chart_leader.player_name}, usage leads with "
                    f"{divergence.usage_leader.player_name or divergence.usage_leader.player_id}"
                )
                _run_synthesis(divergence, relevant_articles, label=label)
            print(
                f"  {circumstance_stats['real_calls']} real API call(s), {circumstance_stats['cache_hits']} cache "
                f"hit(s) -- {circumstance_stats['input_tokens']} input / {circumstance_stats['output_tokens']} "
                f"output / {circumstance_stats['thinking_tokens']} thinking tokens spent this run "
                f"({circumstance_stats['input_tokens'] + circumstance_stats['output_tokens']} total)."
            )
        else:
            print("  ANTHROPIC_API_KEY not configured -- circumstance(s) detected but not synthesized:")
            for change in circumstance_changes:
                remaining_names = ", ".join(r.player_name or r.player_id for r in change.remaining)
                print(
                    f"    {change.team} {change.role}: "
                    f"{change.departed.player_name or change.departed.player_id} {change.departed_status}, "
                    f"remaining: {remaining_names}"
                )
            for extreme in matchup_extreme_changes:
                print(
                    f"    {extreme.team} {extreme.position}: {extreme.player_name} vs {extreme.matchup.opponent}, "
                    f"{extreme.matchup.combined_multiplier:.3f}x"
                )
            for divergence in depth_chart_divergences:
                print(
                    f"    {divergence.team} {divergence.role}: depth chart says "
                    f"{divergence.depth_chart_leader.player_name}, usage leads with "
                    f"{divergence.usage_leader.player_name or divergence.usage_leader.player_id}"
                )
    print()

    print("Fetching real RoleShare/snap-share/red-zone trailing data...")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        snap_shares = fetch_snap_shares(SEASON, WEEK)
    for w in caught:
        print(f"  WARNING (snap shares): {w.message}")
    snap_shares_by_player = {s.player_id: s for s in snap_shares}
    print(f"  {len(role_share_results)} RoleShareResult(s), {len(snap_shares)} PlayerSnapShare(s)")

    import nfl_data_py as nfl

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        pbp = nfl.import_pbp_data([SEASON], include_participation=False)
    red_zone_trailing = aggregate_player_trailing_red_zone(pbp, WEEK)
    print(f"  {len(red_zone_trailing)} trailing red-zone player-role row(s)")

    print("Building real Component A ceiling signals (ADR-0028)...")
    ceiling_signals_by_gsis_id = {}
    for role in (ROLE_RB, ROLE_WR):
        for signal in role_share_ceiling_signals(pbp, WEEK, role):
            ceiling_signals_by_gsis_id[signal.player_id] = signal
    n_with_real_z = sum(1 for s in ceiling_signals_by_gsis_id.values() if s.shrunk_z_score is not None)
    print(f"  {len(ceiling_signals_by_gsis_id)} player(s) with a Component A signal, {n_with_real_z} with a real (gated-in) shrunk_z_score\n")

    # ------------------------------------------------------------------------------------------
    # 2026-09-20: the 6 NflAgentConstructor lineups are now the dashboard's PRIMARY lineup set,
    # replacing the separate ownership-bucket-TARGETING mechanism (`generate_dup_risk_aware_
    # lineups`, ADR-0035/0037) that used to feed it. Chris: "if we have 6 agents, in theory we
    # should get 6 lineups" -- rather than two parallel, uncoordinated candidate sets (an arbitrary
    # N from blind oversample-then-classify, and 6 from real, thesis-driven agents), each agent's
    # own real preference axes now organically produce whatever ownership/ceiling profile its
    # lineup lands in -- read DESCRIPTIVELY (achieved_bucket, gpp_grade below), never targeted by a
    # blind search. `generate_dup_risk_aware_lineups`/`_select_dup_risk_aware_lineups` and
    # `analysis/gpp_grade.py` are NOT deleted -- both are real, tested, reusable (the grading
    # module in particular is reused directly below) -- just no longer the live script's primary
    # generation path.
    # ------------------------------------------------------------------------------------------
    print("=== Solving 6 NflAgentConstructor lineups (now the dashboard's primary lineup set) ===")
    from nfl_dfs.agents import (
        agents_with_suspiciously_empty_deltas,
        build_signal_bundle,
        chalk_anchor_matches_baseline,
        distinct_core_stack_count,
        generate_agent_lineups,
        pairwise_lineup_overlap,
        summarize_agent_deltas,
    )

    # implied_total_by_team isn't fetched until the Odds API section below (it needs the real
    # spreads/totals pull that section makes) -- passed empty here, a real, disclosed limitation:
    # every agent's edge_condition="high_total" gate (Explosion/Shootout) fails closed this run,
    # same "never fabricate" posture as every other nullable signal in this project. Every other
    # signal this bundle needs (ceiling/leverage/matchup/stack context, including
    # close_spread-gated candidates, which read StackContext.home_spread -- already real by this
    # point via the StackProfile block above) is genuinely available here.
    agent_signal_bundle = build_signal_bundle(
        identities,
        ceiling_signals_by_gsis_id=ceiling_signals_by_gsis_id,
        leverage_by_native_id=leverage_by_native_id,
        matchup_context_by_canonical_id=matchup_context_by_canonical_id,
        stack_profiles=stack_profiles,
        implied_total_by_team={},
    )
    try:
        agent_results = generate_agent_lineups(
            pool, agent_signal_bundle, dup_risk_table=dup_risk_table, game_count=len(dk_slate.games)
        )
    except LineupGenerationError as exc:
        print(f"LineupGenerationError: {exc}")
        return

    for stats in summarize_agent_deltas(agent_results):
        print(
            f"  {stats.agent_id}: {stats.n_players_affected} player(s) affected, "
            f"delta sum={stats.delta_sum:+.2f} min={stats.delta_min:+.2f} "
            f"max={stats.delta_max:+.2f} mean={stats.delta_mean:+.2f}"
        )
    empty_agents = agents_with_suspiciously_empty_deltas(agent_results)
    if empty_agents:
        print(
            f"  WARNING: {empty_agents} produced an all-zero delta this run -- likely a wiring "
            "gap, not a legitimate 'nothing to say this week' (check signal_bundle inputs above)."
        )
    overlap = pairwise_lineup_overlap(agent_results)
    print(f"  Pairwise lineup player-overlap: {overlap}")
    from nfl_dfs.optimizer.lineup import generate_lineups

    baseline_lineup = generate_lineups(pool, n=1)[0]
    chalk_ok = chalk_anchor_matches_baseline(agent_results, baseline_lineup)
    print(f"  Chalk Anchor matches plain best-projection baseline: {chalk_ok}")
    if not chalk_ok:
        print("  WARNING: Chalk Anchor should be provably inert -- this indicates a real bug.")
    n_distinct = distinct_core_stack_count(agent_results)
    print(f"  Distinct core stacks: {n_distinct}/{len(agent_results)}")
    not_forced = [r.agent.agent_id for r in agent_results if not r.core_stack_forced_unique]
    if not_forced:
        print(
            f"  NOTE: {not_forced} couldn't get a real distinct core stack this run -- the "
            "cross-agent diversity cut was dropped for these (diversity genuinely exhausted "
            "on this pool, not a wiring bug -- see generate_agent_lineups' own docstring)."
        )
    print()
    for r in agent_results:
        slot_order = ("QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST")
        roster = ", ".join(
            f"{slot}:{r.lineup.slots[slot].display_name}({r.lineup.slots[slot].team})"
            for slot in slot_order
        )
        grade_desc = f"grade {r.gpp_grade.grade} ({r.gpp_grade.detail})" if r.gpp_grade is not None else "grade n/a"
        print(
            f"  [{r.agent.display_name}] ${r.lineup.total_salary} / "
            f"{r.lineup.total_projected_points:.1f}pts / bucket={r.achieved_bucket} / {grade_desc} -- {roster}"
        )
    print()

    lineups = [r.lineup for r in agent_results]  # the 6 agent lineups ARE the dashboard's lineup
    # set now -- every downstream consumer (build_weekly_output, dup-risk reads, the HTML lineup
    # tab) is agnostic to how a Lineup was generated, so nothing further needs to change below.
    weekly = build_weekly_output(lineups, identities, stack_profiles)

    # ------------------------------------------------------------------------------------------
    # Fetched early (ADR-0027) so PlayerDetailRecords below can join real StackProfile/injury/
    # slate-window/implied-total data, not just the Slate Overview tab further down -- this is the
    # SAME odds/injury pull the Slate Overview section already made, just moved earlier and reused
    # rather than fetched twice.
    # ------------------------------------------------------------------------------------------
    print("=== Fetching real Odds API spreads/totals and injury data (also feeds Player Detail) ===")

    import requests

    odds_response = requests.get(
        ODDS_URL,
        params={
            "regions": "us",
            "markets": "spreads,totals",
            "oddsFormat": "american",
            "apiKey": config.odds_api_key,
        },
        timeout=20.0,
    )
    odds_response.raise_for_status()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        odds_games = parse_dk_odds_events(odds_response.json())
    for w in caught:
        print(f"  WARNING (odds): {w.message}")
    odds_by_pair = {(g.away_team, g.home_team): g for g in odds_games}
    print(f"  {len(odds_games)} game(s) with a DraftKings odds line.")

    print("Fetching real injury data (RotoGrinders Situation Room)...")
    try:
        injury_entries = fetch_injury_report()
        print(f"  {len(injury_entries)} injury report row(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        injury_entries = []

    def _injury_statuses_for(team: str) -> list[PlayerInjuryStatus]:
        return [
            PlayerInjuryStatus(status=entry.status, impact_rating=entry.impact_rating)
            for _, entry in team_injuries(team, identities, injury_entries)
        ]

    injury_by_canonical_id = {
        identity.canonical_id: entry
        for team in slate_teams
        for identity, entry in team_injuries(team, identities, injury_entries)
    }
    print(f"  {len(injury_by_canonical_id)} reconciled player(s) matched to an injury report row.\n")

    kickoff_utc_by_team: dict[str, str] = {}
    implied_total_by_team: dict[str, float] = {}
    for game in dk_slate.games:
        kickoff_utc_by_team[game.away_team] = game.start_time_utc
        kickoff_utc_by_team[game.home_team] = game.start_time_utc
        odds = odds_by_pair.get((game.away_team, game.home_team))
        if odds is not None and odds.home_spread is not None and odds.away_spread is not None and odds.total is not None:
            implied_total_by_team.update(implied_team_totals(odds))

    # ------------------------------------------------------------------------------------------
    # Player Detail: build real PlayerDetailRecords for this slate's FULL reconciled player pool
    # (not just the 27 lineup slots) -- a "browsable" dashboard tab is more representative of the
    # real deliverable with the whole slate's pool than with just the 3 lineups' worth of names.
    # ------------------------------------------------------------------------------------------
    print("=== Building real PlayerDetailRecords for the full slate pool ===")

    print("Building real WR red-zone role-security signals (ADR-0036)...")
    red_zone_signals_by_gsis_id = {s.player_id: s for s in red_zone_ceiling_signals(pbp, WEEK, ROLE_WR)}
    n_with_real_discount = sum(
        1 for s in red_zone_signals_by_gsis_id.values() if wr_red_zone_role_security_discount(s) is not None
    )
    print(f"  {len(red_zone_signals_by_gsis_id)} player(s) with a red-zone-share signal, {n_with_real_discount} with a real (gated-in) discount read")

    print("Building real trailing receiving-opportunity profiles (ADR-0029, descriptive only)...")
    receiving_profile_by_gsis_id = trailing_receiving_profiles(pbp, WEEK)
    print(f"  {len(receiving_profile_by_gsis_id)} player(s) with a trailing receiving profile")

    print("Building real trailing red-zone weekly share sequences (ADR-0029 addendum, descriptive only)...")
    carry_share_by_week_by_gsis_id = trailing_red_zone_share_by_week(pbp, WEEK, ROLE_RB)
    target_share_by_week_by_gsis_id = trailing_red_zone_share_by_week(pbp, WEEK, ROLE_WR)
    print(
        f"  {len(carry_share_by_week_by_gsis_id)} RB(s), {len(target_share_by_week_by_gsis_id)} WR/TE(s) "
        "with a trailing red-zone weekly share sequence"
    )

    print("Building real trailing QB rushing-opportunity profiles (ADR-0030, descriptive only)...")
    qb_rushing_profile_by_gsis_id = trailing_qb_rushing_profiles(pbp, WEEK)
    print(f"  {len(qb_rushing_profile_by_gsis_id)} player(s) with a trailing QB rushing profile")

    # receiving_scheme_grades/coverage_scheme_grades/coverage_tendency_by_team/gsis_to_pff_id/
    # opponent_of, and (NflAgentConstructor Phase B2) role_share_results/role_share_by_key,
    # stack_profiles, circumstance_assessments_by_gsis_id, pbp, snap_shares_by_player,
    # red_zone_trailing, and ceiling_signals_by_gsis_id were all already built earlier, before
    # lineup generation -- MatchupContext, ADR-0035/0037's dup-risk-aware selection, and a future
    # agent-preference objective delta all need them before generation runs, not after, so those
    # blocks were moved up rather than duplicated here.

    print("Building real GameEnvironmentScores for this slate's teams...")
    game_environment_by_team: dict[str, GameEnvironmentScore] = {}
    for team in slate_teams:
        try:
            game_environment_by_team[team] = _build_ges(team, pace_proe_df, implied_total_z_by_team)
        except (IndexError, KeyError) as exc:
            print(f"  {team}: no pace/PROE row this pull ({exc}) -- skipping GameEnvironmentScore")

    player_details = []
    for identity in identities:
        if identity.team not in slate_teams:
            continue  # defensive only -- every reconciled identity should be from this slate
        record = build_player_detail_record(
            identity,
            SEASON,
            WEEK,
            team=identity.team,
            position=identity.position,
            opponent_team_this_week=opponent_of.get(identity.team),
            role_share_results=role_share_by_key,
            snap_shares_by_player=snap_shares_by_player,
            red_zone_trailing=red_zone_trailing,
            receiving_scheme_grades=receiving_scheme_grades,
            gsis_to_pff_id=gsis_to_pff_id,
            team_coverage_tendency=coverage_tendency_by_team,
            game_environment_by_team=game_environment_by_team,
            projections_by_canonical_id=projections_by_canonical_id,
            leverage_by_native_id=leverage_by_native_id,
            stack_profiles=stack_profiles,
            injury_by_canonical_id=injury_by_canonical_id,
            kickoff_utc_by_team=kickoff_utc_by_team,
            implied_total_by_team=implied_total_by_team,
            ceiling_signals_by_gsis_id=ceiling_signals_by_gsis_id,
            red_zone_signals_by_gsis_id=red_zone_signals_by_gsis_id,
            receiving_profile_by_gsis_id=receiving_profile_by_gsis_id,
            carry_share_by_week_by_gsis_id=carry_share_by_week_by_gsis_id,
            target_share_by_week_by_gsis_id=target_share_by_week_by_gsis_id,
            qb_rushing_profile_by_gsis_id=qb_rushing_profile_by_gsis_id,
            circumstance_assessments_by_gsis_id=circumstance_assessments_by_gsis_id,
        )
        player_details.append(record)

    print(f"  Built {len(player_details)} PlayerDetailRecord(s) for the full slate pool.\n")

    populated_role_share = sum(1 for r in player_details if r.usage.role_share.role_share is not None)
    populated_own_scheme = sum(1 for r in player_details if r.own_scheme_splits.reason is None and r.own_scheme_splits.applicable)
    populated_ge = sum(1 for r in player_details if r.game_environment is not None and r.game_environment.is_available)
    populated_ownership = sum(1 for r in player_details if r.ownership is not None)
    populated_ceiling = sum(1 for r in player_details if r.ceiling_multiplier is not None)
    print(
        f"  Populated: role_share={populated_role_share}/{len(player_details)}, "
        f"own_scheme_splits={populated_own_scheme}/{len(player_details)}, "
        f"game_environment={populated_ge}/{len(player_details)}, "
        f"ownership={populated_ownership}/{len(player_details)}, "
        f"ceiling={populated_ceiling}/{len(player_details)}"
    )

    # ------------------------------------------------------------------------------------------
    # Slate Overview: one row per game -- real Vegas odds/implied totals, real weather, real
    # injury data, and a GameEnvironmentScore per team that wires in *real* weather. This is a
    # separate, additive computation from `game_environment_by_team` above (which -- unchanged,
    # matching `live_integration_check_output.py`'s own existing `_build_ges` pattern this script
    # otherwise reuses as-is -- always passes `NEUTRAL_WEATHER` and no injury data, since that
    # dict feeds the Player Detail tab, out of scope to touch per this task). The two tabs' composite
    # scores for the same team can therefore differ slightly this week by the (small, 11.1%-
    # weighted) weather component and by the injury flag, which the Player Detail tab's path never
    # populates today. Real, not synthetic: real Odds API line, real Open-Meteo/NWS weather, real
    # RotoGrinders Situation Room injury data.
    # ------------------------------------------------------------------------------------------
    print("=== Building real Slate Overview rows (odds, weather, GameEnvironmentScore) ===")
    print("  (reusing the Odds API/injury pull already fetched above for Player Detail)")

    print("Fetching real weather (Open-Meteo primary, NWS cross-check)...")
    weather_by_home_team: dict[str, WeatherReading] = {}
    weather_reason_by_home_team: dict[str, str] = {}
    for game in dk_slate.games:
        try:
            weather_by_home_team[game.home_team] = fetch_weather_reading(game.home_team, game.start_time_utc)
        except Exception as exc:  # noqa: BLE001
            weather_reason_by_home_team[game.home_team] = f"weather fetch failed: {exc}"
            print(f"  {game.home_team}: weather fetch FAILED ({exc})")
    print(f"  {len(weather_by_home_team)}/{len(dk_slate.games)} game(s) with a weather reading.")

    def _ges_with_real_weather(team: str, weather_reading: WeatherReading | None) -> GameEnvironmentScore | None:
        rows = pace_proe_df[pace_proe_df["team"] == team]
        if rows.empty:
            return None
        row = rows.iloc[0]
        pace_proe = PaceProeInput(
            pace_z=float(row["pace_z"]),
            proe_z=float(row["proe_z"]),
            weeks_played=int(row["weeks_played"]),
            shrinkage_weight=float(row["shrinkage_weight"]),
        )
        implied_total = ImpliedTotalInput(z=implied_total_z_by_team.get(team))
        if weather_reading is None:
            weather_input = WeatherInput(is_indoor=False)
        else:
            weather_input = WeatherInput(
                is_indoor=weather_reading.is_indoor,
                wind_any_a_relative_drop=weather_reading.wind_any_a_effect_pct,
                temperature_relative_drop=weather_reading.temperature_effect_pct,
                precipitation_relative_drop=weather_reading.precipitation_relative_drop,
            )
        return compute_game_environment_score(
            team,
            SEASON,
            WEEK,
            implied_total,
            pace_proe,
            weather_input,
            team_injuries=_injury_statuses_for(team),
        )

    slate_games: list[SlateGameRow] = []
    for game in dk_slate.games:
        away, home = game.away_team, game.home_team
        odds = odds_by_pair.get((away, home))
        odds_reason = None
        home_spread = away_spread = total = home_implied = away_implied = None
        if odds is None:
            odds_reason = (
                "no DraftKings odds line for this game -- either already underway and dropped "
                "from the Odds API feed, or a team-name mapping gap (odds_api.py's module "
                "docstring's documented known limitation)"
            )
        else:
            home_spread, away_spread, total = odds.home_spread, odds.away_spread, odds.total
            if home_spread is not None and away_spread is not None and total is not None:
                implied = implied_team_totals(odds)
                home_implied, away_implied = implied.get(home), implied.get(away)
            else:
                odds_reason = "DraftKings line present but missing a spread or total field this pull"

        weather_reading = weather_by_home_team.get(home)
        weather_reason = weather_reason_by_home_team.get(home) if weather_reading is None else None

        home_ges = _ges_with_real_weather(home, weather_reading)
        away_ges = _ges_with_real_weather(away, weather_reading)

        slate_games.append(
            SlateGameRow(
                away_team=away,
                home_team=home,
                kickoff_utc=game.start_time_utc,
                kickoff_reason=None,
                home_spread=home_spread,
                away_spread=away_spread,
                total=total,
                odds_reason=odds_reason,
                home_implied_total=home_implied,
                away_implied_total=away_implied,
                weather=weather_reading,
                weather_reason=weather_reason,
                home_environment=home_ges,
                home_environment_reason=None if home_ges is not None else f"no pace/PROE row this pull for {home}",
                away_environment=away_ges,
                away_environment_reason=None if away_ges is not None else f"no pace/PROE row this pull for {away}",
            )
        )
    print(f"  Built {len(slate_games)} SlateGameRow(s) for the Slate Overview tab.\n")

    # Report the actual conviction ranking this pull produces, reusing the renderer's own sort
    # key (not a re-derived copy) so this printed summary can never drift from what the rendered
    # HTML actually shows.
    from nfl_dfs.dashboard.renderer import _conviction_sort_key, _game_composite_scores, _worst_injury_flag

    print("=== Conviction ranking this pull produced ===")
    for rank, g in enumerate(sorted(slate_games, key=_conviction_sort_key), start=1):
        scores = _game_composite_scores(g)
        flag = _worst_injury_flag(g)
        avg = f"{sum(scores) / len(scores):.1f}" if scores else "n/a"
        print(
            f"  {rank}. {g.away_team} @ {g.home_team}: avg composite={avg} "
            f"(scores={[f'{s:.1f}' for s in scores]}), injury_flag={flag!r}, "
            f"weather_indoor={g.weather.is_indoor if g.weather else 'unknown'}"
        )

    print("Building real dup-risk read for each generated lineup (ADR-0033/0034)...")
    dup_risk_by_lineup: dict[int, object] = {}
    if dup_risk_table is not None:
        # Reuses the same dup-risk table `generate_agent_lineups` already used for its own
        # descriptive `achieved_bucket` read above -- this is a second, independent read (a real
        # `LineupDupRiskAssessment` with historical dup-rate context, not just a bucket index), not
        # a redundant rebuild of the table itself.
        dup_risk_by_lineup = {
            i: assess_lineup_dup_risk(lineup, identities, leverage_by_native_id, dup_risk_table)
            for i, lineup in enumerate(lineups)
        }
        n_real = sum(1 for a in dup_risk_by_lineup.values() if a.reason is None)
        print(f"  {n_real}/{len(dup_risk_by_lineup)} lineup(s) with a real dup-risk read")
    else:
        print("  SKIPPED: no dup-risk table available this pull")

    out_path = "dashboard_output/weekly_dashboard.html"
    import os

    os.makedirs("dashboard_output", exist_ok=True)
    write_dashboard_html(out_path, weekly, player_details, slate_games, dup_risk_by_lineup=dup_risk_by_lineup)
    print(f"\nWrote real dashboard HTML to {out_path}")


if __name__ == "__main__":
    main()
