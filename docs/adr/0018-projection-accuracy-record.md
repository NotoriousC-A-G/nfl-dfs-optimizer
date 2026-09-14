# ADR-0018: `ProjectionAccuracyRecord` — decomposed projection-vs-actual tracking, live and backtested

**Status:** Accepted (draft spec, new construct), pending Model Analytics Expert confirmation on the attribution mechanism; scope question flagged for Product Owner (see Consequences)
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 6 (`GameEnvironmentScore`, `MatchupContext`, `DSTProjection`), PRD Section 10 (Performance Analytics), PRD Section 11 (Open Questions), ADR-0003, ADR-0005, ADR-0007, ADR-0009, ADR-0011, ADR-0012, ADR-0014, ADR-0015, ADR-0016, ADR-0017

## Context

Every ADR from 0003 through 0017 ends with the same move: a threshold, cap, or weight is set to a specific, stated starting value, and a "backtesting prerequisite" or "validation queue" item is logged to check it once real results exist. That queue is now long — 15+ distinct items across `GameEnvironmentScore`, `MatchupContext`, and `DSTProjection` (compiled in full below) — and nothing in the codebase or spec runs any of them. Separately, Chris asked for real projection-accuracy tracking: not a single blended projected-vs-actual number, but one decomposed by which specific formula component drove the gap.

These are the same problem. A backtest queue item like "correlate the coverage-adjustment's implied efficiency delta against real observed target efficiency" is not a new analysis to design — it's a query against a table that doesn't exist yet, containing exactly the decomposed projection this ADR specifies. Building that table both closes the backtest queue and gives Chris the per-player-week accuracy breakdown he asked for, because they're the same underlying need: a persisted, component-level record of what the model believed and why, joined against what actually happened.

## Decision

### 1. Two records, not one: `ProjectionSnapshot` + `ActualResult`, joined into `ProjectionAccuracyRecord`

A `ProjectionAccuracyRecord` is keyed on `(canonical_player_id, season, week)` — `DST_<team>` for defenses, per ADR-0013's canonical ID scheme — and has two halves that populate at different times:

- **`ProjectionSnapshot`** — written at the moment the pipeline computes a player's projection for that week (pre-lock, live; or as part of a backtest run for a historical week). Immutable once written. Never overwritten by a later re-run for the same `(player, season, week)` — a second run gets a new `snapshot_id` and `computed_at` timestamp, so a formula change mid-week doesn't silently erase what was actually used to build that week's lineups. Exactly one snapshot per `(player, season, week)` is flagged `is_final=True` (the one that actually fed the optimizer, for live weeks) or `is_backtest=True` (for historical reconstruction runs, which aren't "final" in the same sense but are the record's whole point).
- **`ActualResult`** — written once the game is final, from real box-score data (source below). Never estimated or backfilled from projections.

The parent record is the two joined on the same key, plus derived residual fields computed once both halves exist. A record with only a `ProjectionSnapshot` (game not yet played) is valid and expected — that's the live, forward-looking accuracy-tracking case, not an error state.

### 2. `ProjectionSnapshot` decomposition — every Section 6 component, not just the final number

Every field below traces to a specific, already-specified Section 6/ADR formula. Nothing new is computed here — this is a persistence layer over calculations the pipeline already does and currently throws away after producing the final projection.

```
ProjectionSnapshot:
  # Identity and provenance
  snapshot_id, canonical_player_id, player_name, position, team, opponent, game_id
  season, week, is_final, is_backtest, computed_at
  formula_versions: { game_environment_score: "ADR-0017", matchup_context: "ADR-0010",
                       dst_projection: "ADR-0012", shrinkage_form: "ADR-0011", ... }
    # see section 5 below — why this matters

  # Section 5 step 3 baseline
  vendor_baseline:
    sources_used: [...]           # which of PFF/RotoGrinders/Footballguys published a number
    per_source_values: {...}
    blended_value: float
    blend_weights: {...}

  # GameEnvironmentScore (denormalized onto both teams' player-weeks in this game — ADR-0003/0016/0017)
  game_environment:
    implied_total: { value, z_score, points_contribution, source_tier }  # source_tier: "live" | "cached" | "unavailable" (ADR-0016)
    pace:  { current_to_date, prior_season_baseline, blended_value, w_shrinkage, n, z_score, points_contribution }
    proe:  { current_to_date, prior_season_baseline, blended_value, w_shrinkage, n, z_score, points_contribution }
    weather:
      applicable, wind_mph, temp_f, precip_type, precip_rate_in_hr
      m_wind, m_temp, m_precip, combined_log, capped_log, cap_bound_hit
      points_contribution
    injury_flag: { worst_unresolved_player_id, worst_impact_rating, flag_value, flag_source }  # flag_source: "rotogrinders_situation_room" | "nflverse_injury_report_backtest_proxy"
    composite_score, is_available
    spread, game_total

  # MatchupContext (skill positions; DST rows leave this block null and use dst_projection below)
  matchup_context:
    run_game:          { rb_grade_z, run_def_grade_z, differential, multiplier_raw, n_snaps }
    pass_protection:   { pass_block_grade_z, pass_rush_grade_z, differential, multiplier_raw, n_snaps }
    coverage:
      scheme:    { man_grade, zone_grade, man_rate, zone_rate, multiplier_raw }
      alignment: { slot_defender_id, perimeter_defender_id, slot_snap_share,
                   gates_passed, shadow_flagged, fallback_used, multiplier_raw }
      combined_multiplier_raw
    protection_coverage_combination:   # ADR-0005
      log_sum, capped_log, cap_bound_hit, combined_multiplier
    per_leg_uncapped_log_contribution: { run_game, pass_protection, coverage }
    weeks_of_current_season_grade_data: int   # W-1 -- for ADR-0014's noise-vs-week backtest

  # DSTProjection (DST rows only)
  dst_projection:
    baseline: { sources_used, blended_value }
    sack_opportunity:      { value, multiplier }
    turnover_opportunity:  { opponent_turnover_rate_leg, pressure_rate_leg,
                              qb_continuity_applied, w_qb, blended_qb_turnover_rate, multiplier }
    core_combination:      { log_sum, capped_log, multiplier, cap_bound_hit }        # ADR-0009, ±20%
    opponent_implied_total: { z, multiplier }
    own_team_script:       { spread_band, multiplier }
    context_combination:   { log_sum, capped_log, combined_defensive_multiplier, cap_bound_hit }  # ±30%
    return_opportunity:
      punt:    { role_gate_passed, identified_returner_id, volume_z, explosiveness_z, multiplier, n_attempts }
      kickoff: { role_gate_passed, identified_returner_id, volume_z, explosiveness_z, multiplier, n_attempts }
      combined_multiplier, bonus_points
    per_leg_uncapped_log_contribution: { sack_opportunity, turnover_opportunity,
                                          opponent_implied_total, own_team_script }
    final_dst_projection

  # The number that actually shaped a lineup decision
  final_projection: float
```

**Exact, not approximate, per-leg attribution.** A naive approach to "how much did the coverage adjustment move this projection" is to compute `final_projection − counterfactual(coverage multiplier = 1.0)` after the fact — but that's expensive (a second full formula run per leg per player-week) and, worse, ambiguous once a combination step's cap binds (removing one leg changes whether the cap would have bound at all, so the counterfactual isn't a clean isolation of that leg alone). Instead, since every Section 6 combination step is already `capped_log_combine` (sum of log-multipliers, then clip), attribution falls out of the combination arithmetic for free: store each leg's own `ln(multiplier)` (the `per_leg_uncapped_log_contribution` fields above) alongside the combination's `log_sum` and `capped_log`. Because `log_sum = Σ per_leg_log_contribution` exactly, a leg's *uncapped* point contribution is always `baseline × (exp(leg_log) − 1)` — a clean, additive, always-reconcilable quantity — while `cap_absorption = log_sum − capped_log` is itself a stored, queryable field answering "how much did the cap suppress this week, in aggregate," which is exactly what several queued backtest items (ADR-0005, ADR-0008/0009) ask about the caps directly. No counterfactual re-run is needed; the decomposition is a byproduct of computing the formula the way it's already specified, just retaining the intermediate log terms instead of discarding them after `exp()`.

### 3. `ActualResult` — source, confirmed live

**Finding, checked live, not assumed:** nflverse's `stats_player` GitHub release (`nflverse-data` repo, tag `stats_player` — a **different** release tag from `player_stats`, the one Phase 0 already confirmed stale through only the 2024 season) is live, current, and weekly-grain:

- `stats_player_week_<season>.csv` — one row per player per week, with full box-score columns: `passing_yards`, `passing_tds`, `passing_interceptions`, `rushing_yards`, `rushing_tds`, `rushing_fumbles_lost`, `receptions`, `targets`, `receiving_yards`, `receiving_tds`, `receiving_fumbles_lost`, `special_teams_tds`, `punt_return_yards`, `kickoff_return_yards`, plus full individual defensive stat lines (`def_sacks`, `def_interceptions`, `def_tds`, `def_fumbles`, `def_safeties`, etc.) — everything needed to compute DK actual fantasy points for offensive skill players and, summed to team level, for DST.
- **Confirmed current, not stale, via a live pull today:** the release's `stats_player_week_2026.csv` asset was last updated `2026-09-13T13:50:13Z` — today, matching this session's date — and contains real week-1 2026 rows (e.g., Matthew Stafford, 155 passing yards; Davante Adams, 26 receiving yards). This is a materially different freshness situation from `player_stats`, which Phase 0 found frozen since mid-2025.
- **Important build note for the Data Integration Engineer:** the installed `nfl_data_py` package's `import_weekly_data()` convenience function does **not** read from this release — checked directly against the package source (`nfl_data_py/__init__.py`), `import_weekly_data()` and `import_seasonal_data()` both hard-code the URL to `.../releases/download/player_stats/player_stats_{year}.parquet`, the stale tag. There is currently no `nfl_data_py` function that reads the fresher `stats_player` tag. Two ways to get it, same pattern already established for pace/PROE: (a) fetch the `stats_player_week_<season>.csv`/`.parquet` asset directly from the GitHub release, bypassing `nfl_data_py` entirely (mirrors how this project already treats `import_weekly_data` as unusable and reads pace/PROE from `import_pbp_data()` instead); or (b) aggregate actual box-score stats directly from `import_pbp_data()` (already pulled, already fresh, already the project's established pattern) rather than depending on a second nflverse release cadence at all. **Recommendation: option (b) for the primary path** — it reuses data already being pulled for `GameEnvironmentScore`/`MatchupContext` with no new dependency, and `stats_player_week_*` is itself computed from the same underlying play-by-play (via `nflfastR::calculate_stats()`), so pbp aggregation and the `stats_player` release should agree by construction. Use the `stats_player` release as a **cross-check/QA source** (a completely independently-published aggregate to catch a pbp-aggregation bug), not the primary path — one less thing this project depends on nflverse's convenience layer, rather than more.
- **Team-level DST actuals** (points allowed, for the DK DST scoring formula) are not in a player-level file at all — they come from final game scores, already available via `import_schedules()` (see section 4 below) or by summing the opposing offense's points from pbp. No new dependency.

`dk_actual_points` is computed from these raw stats by a single, versioned, pure function (`dk_scoring.py`) implementing DK's own published Classic scoring rules (yardage/TD/reception/turnover rates, the 300/100/100-yard bonuses, DST's sack/turnover/TD/safety/points-allowed-tier table) — not a value read from any vendor. This keeps `ActualResult` auditable against DK's own rulebook rather than trusting a third party's fantasy-point computation, and versioned in case DK changes scoring mid-project.

```
ActualResult:
  canonical_player_id, season, week, game_status  # "final" | "in_progress" | "not_played"
  raw_stats: { ...full box-score line, per section above... }
  team_points_allowed: int   # DST rows only, from final score
  dk_actual_points: float    # computed via dk_scoring.py, versioned
  source: "nflverse_pbp_aggregation"   # or "nflverse_stats_player_crosscheck" if that path is used instead
  data_quality_flags: [...]  # e.g., missing snap participation, box-score/pbp mismatch on cross-check
```

### 4. Backtesting without look-ahead bias: reuse-vs-generalize audit of every existing "as-of" mechanism

Chris's framing is right that ADR-0014's trailing-grade design is *already* the right shape for this. Checked directly, component by component, rather than assumed — this is the actual audit requested, not a restatement of the request:

| Component | "As of" mechanism | Reusable as-is for historical week `W`, season `S`? |
|---|---|---|
| `MatchupContext` PFF grades (run/protection/coverage) | ADR-0014: cumulative `week=1,...,W-1` via PFF's facet endpoints, server-side aggregated | **Yes, directly** — the endpoint already takes `week`/`season` as parameters, not an implicit "now." Point the same call at `season=S&week=1,...,W-1`. **Live-check gap, not resolved here:** I could not verify live in this session (no PFF API credential available to me) whether PFF's facet endpoints actually serve full historical seasons back through 2023, or only a rolling recent window — this is a concrete, first Data Integration Engineer check before backtesting further back than whatever PFF's API actually retains. The week-1-of-season fallback (prior-season grade by `player_id`) also needs PFF history reaching one season further back than the earliest backtest year (e.g., 2022 grades to backtest 2023 week 1) — same open check. |
| `GameEnvironmentScore` z-scoring, cutoff, shrinkage blend | ADR-0003: cross-sectional per week, weeks `1..W-1` only, `n/(n+k)` blend toward prior-season baseline (ADR-0011) | **Yes, directly, by construction** — this was already designed as "as of week W," never "as of now." No generalization needed; run the identical computation against historical `import_pbp_data()` seasons, which already cover 2023–2025 (and further back). |
| Shrinkage forms generally (ADR-0011, ADR-0012) | `w = n/(n+k)`, pure function of a sample-size `n` | **Yes, trivially** — no time-dependence at all beyond `n` itself, which is already computed relative to the target week. |
| Return-opportunity gating (ADR-0012) | Season-to-date within a season, per role | **Yes, directly** — same reasoning as `GameEnvironmentScore`. |
| CDF z→points mapping, injury-rollup thresholds (ADR-0017) | Pure functions | **Yes, trivially.** |
| `GameEnvironmentScore` implied total (Vegas line) | ADR-0016: live-or-cached Odds API pull, built for a *live weekly polling cadence* | **No — needs a different historical source, not a generalization of the live one.** The Odds API's caching-for-in-play-games design solves a live-polling problem that doesn't exist for historical weeks. **Confirmed live, direct replacement exists with no new dependency:** `nfl_data_py.import_schedules()` (the same function `odds_api.py` already imports for week-mapping) is backed by `nflverse-data`'s `schedules` release, which carries `spread_line` and `total_line` per historical game — checked directly against a live pull of `games.csv`: **100% coverage (855/855) across the 2023–2025 regular season** for both `spread_line` and `total_line`, plus final `home_score`/`away_score` (useful for DST's team-points-allowed context, same source, no second pull). Backtest mode should read `import_schedules()`'s historical lines directly, not attempt to replay ADR-0016's live-caching logic against historical data — there is no "in-play" ambiguity for a game that finished years ago. |
| `GameEnvironmentScore` weather sub-component (ADR-0002/0007/0015) | Open-Meteo's **forecast** API (`api.open-meteo.com/v1/forecast`), which only serves current/near-future data | **No — same endpoint family, different endpoint, needs pointing at a different URL, not new curve logic.** Confirmed live: Open-Meteo's **historical Archive API** (`archive-api.open-meteo.com/v1/archive?...&start_date=...&end_date=...`) returns the identical field set (`temperature_2m`, `wind_speed_10m`, `precipitation`, `rain`, `snowfall`) for a specified past date — tested directly against 2023-09-07 (a real historical game date) and got populated hourly values back. The wind/temperature/precipitation curves themselves (ADR-0002/0007/0015) don't change at all for backtesting — only the ingestion endpoint does. Minor, incidental finding: `import_schedules()`'s own `temp`/`wind` columns exist but are only ~62% populated (530/855, presumably domes/missing games) in the same live pull — not reliable enough as the primary historical weather source, but a free cross-check where present. |
| Injury/role uncertainty flag (ADR-0017, sourced from RotoGrinders "Situation Room") | A live, current-state vendor product with (as far as this project has confirmed) no historical archive | **No general replacement confirmed — a real, structural gap, not resolved here.** RotoGrinders' injury report reflects the state of the world *today*; there's no indication it exposes "what did the Week 6, 2024 report say." A plausible substitute — **not verified live in this session, flagged for Data Integration Engineer** — is `nfl_data_py.import_injuries(years)`, which does carry historical weekly injury-report designations (Questionable/Doubtful/Out, practice participation) back across full seasons. If confirmed, backtest-mode injury flags should be computed from that source instead, but note it lacks RotoGrinders' graded 0–10 `IMPACTRTG` severity score entirely — ADR-0017's `<2 / 2–4 / 5+` rollup has no equivalent input in a Q/D/O-only historical report, so a backtested injury flag will necessarily be structurally cruder (probably collapsing to a binary "any unresolved starter-tier status" rather than a graded severity) than the live one. Store `flag_source` on every snapshot (section 2 above) precisely so this known asymmetry between live and backtested injury-flag quality is visible in the data, not silently averaged away. |
| Vendor baseline blend (`vendor_baseline`, Section 5 step 3; PFF/RotoGrinders/Footballguys DFS projections) | Live, current-week vendor projection pages/APIs | **Not confirmed reusable — a real, structural gap, flagged rather than papered over.** RotoGrinders and Footballguys' projection surfaces are current-slate pages with no indication of a historical-projection archive; a backtest cannot ask "what did RotoGrinders project for this player in Week 6, 2024" after the fact. PFF's grade facets are confirmed historically queryable (see row 1), but that's a different product from PFF's own DFS-projection facet, which was **not** checked for historical-week support in this pass — a concrete Data Integration Engineer follow-up. **Practical consequence, stated plainly:** unless PFF's projection facet turns out to support historical pulls, `ProjectionSnapshot.vendor_baseline` cannot be honestly reconstructed for historical weeks, and backtest-mode snapshots should either (a) omit it and clearly flag `vendor_baseline: unavailable_backtest`, or (b) substitute an explicit, clearly-labeled proxy baseline (e.g., a simple trailing-average-of-actuals baseline, computed the same way for every backtest player-week) — never silently reuse a *current* vendor number as a stand-in for a past week's, which would itself be a look-ahead-bias leak. **This does not block most of the queued backtest items below** — the large majority ask about a specific `MatchupContext`/`GameEnvironmentScore`/`DSTProjection` *multiplier's* correlation with actual outcomes, which needs the multiplier and the actual result, not the vendor baseline at all. It does mean full projected-total-points-vs-actual-total-points backtesting (as opposed to component-level backtesting) is gated on this open question. |

**Net finding for Chris's specific question:** ADR-0014's trailing-grade design, ADR-0003's z-score/shrinkage design, and every pure shrinkage/mapping function are directly reusable with zero new logic — they were already built "as of week W," not "as of now," which is exactly why they generalize for free. The two live-data-polling mechanisms built for a *weekly cadence problem* (ADR-0016's Odds API cache, and implicitly the weather forecast endpoint) need a different historical data source, not new logic — both have a confirmed, live-checked, zero-new-dependency replacement. The two genuinely open gaps are vendor baseline projections and RotoGrinders' injury severity grading, both because they're live-only vendor products with no confirmed historical archive — flagged for Data Integration Engineer, not resolved here.

### 5. Formula versioning — a prerequisite the decomposition depends on

Section 6's formulas have changed shape multiple times already (ADR-0008→0009 restructured `DSTProjection` entirely; ADR-0003→0011 changed the shrinkage curve under the same weights). A `ProjectionAccuracyRecord` spanning a multi-season backtest window will contain snapshots computed under genuinely different formula versions for the same construct. Analysis that pools "was the coverage adjustment accurate" across an ADR-0001-only period and a post-ADR-0006-confidence-gates period without distinguishing them would conflate two different formulas' track records into one misleading number. Every snapshot's `formula_versions` block (section 2) records which ADR-level spec was in effect for each of `GameEnvironmentScore`, `MatchupContext`, and `DSTProjection` at computation time — a small, mandatory field, not an afterthought — so backtest queries can and should filter or group by formula version rather than assuming stationarity across the whole window.

### 6. Answering three queued backtest items directly, as concrete queries

**(a) ADR-0003 — is 44.4/22.2/22.2/11.1 double-counting implied total against pace/PROE?** This one needs no `ActualResult` at all — it's a query purely against `ProjectionSnapshot.game_environment`: `corr(implied_total.z_score, pace.z_score)` and `corr(implied_total.z_score, proe.z_score)`, grouped by `formula_versions.game_environment_score`, across every team-week in the backtest window. Compare `|r|` against the 0.3–0.4 action threshold exactly as specified.

**(b) ADR-0005 — does pass-rush pressure contaminate coverage grading enough to justify the ±20% cap (and is that cap even binding)?** Two linked queries now possible: (1) `corr(matchup_context.pass_protection.differential, matchup_context.coverage.scheme.multiplier_raw)` across all receiver-weeks — the correlation ADR-0005 asked for, computable from snapshot data alone; (2) join to `ActualResult`: `corr(matchup_context.coverage.combined_multiplier_raw, actual_target_efficiency)` where `actual_target_efficiency` is derived from `ActualResult.raw_stats` (e.g., `receiving_yards / targets`, or catch rate) — this is the literal "coverage-adjustment's implied efficiency delta against real observed target efficiency" query from the prompt, now a straightforward join instead of a vague intention. Separately, `mean(protection_coverage_combination.cap_bound_hit)` directly answers how often the cap is actually doing work, informing whether ±20% should widen toward ±25% per ADR-0005's own stated revisit condition.

**(c) ADR-0012 — is the 4.5-point return-opportunity bonus scale (and the underlying `k=10`/`k=25` shrinkage) still right once live 2025–2026 data exists beyond the original 2024–2025 sample?** ADR-0012's own calibration was a one-time manual `nflverse` pbp query. This construct turns it into a standing, re-runnable check: bucket every DST-week by `dst_projection.return_opportunity.combined_multiplier` (or the underlying `volume_z`/`explosiveness_z`) into the same top-decile/rest split ADR-0012 used, and compute `P(ActualResult.raw_stats.special_teams_tds >= 1 | bucket) × 6` per bucket, exactly reproducing ADR-0012's method but now over the accumulating live+historical window rather than a frozen 2024–2025 snapshot — and automatically re-runnable every time more weeks of data land, rather than requiring a fresh one-off pbp query each time someone wants to check it.

## Compiled backtest queue, reorganized: before vs. after this construct

Every queued backtest/validation item found across ADR-0003 through ADR-0017 and PRD Section 6/11, reorganized by whether `ProjectionAccuracyRecord` directly enables it (as opposed to still needing new data ingestion or a decision the Product Owner/Chris hasn't made):

**Directly enabled — a query against stored snapshot + actual data, no new ingestion needed:**
1. ADR-0003: implied-total vs. pace/PROE weight-independence check (`|r| > 0.3–0.4`).
2. ADR-0005: pass-rush/pressure vs. coverage-grade correlation; whether the ±20% cap is binding/right; coverage-multiplier-vs-actual-target-efficiency correlation.
3. ADR-0007: whether 60% weather-magnitude damping is right (vs. 50%/75%), via weather-multiplier-vs-actual-passing-efficiency correlation by band.
4. ADR-0008/0009: sack-opportunity vs. turnover-opportunity multiplier correlation; whether the DST core's ±20% cap is sized right; decomposing `turnover_opportunity_multiplier`'s variance into pressure-driven vs. independent legs (both legs are separately stored).
5. ADR-0009: whether the 150-career-attempt QB-continuity threshold is right, via `qb_continuity_applied`/`w_qb`-bucketed residual analysis; whether the 6-week early-season shrinkage window is adequate for return-volume z specifically.
6. ADR-0011: whether the adopted `k` values (6, 150, 10, 25) are calibrated correctly, via residual analysis bucketed by `w_shrinkage`/`n`.
7. ADR-0012: the 4.5-point return-bonus scale and `k=10`/`k=25` constants (walked through above); whether equal-weighting punt/kickoff roles should move to attempt-share weighting; the known baseline/core-multiplier "double-discount" tension for mediocre-defense-elite-returner teams — now directly measurable as a residual pattern rather than a suspected-but-unverified interaction.
8. ADR-0014: whether full player-level PFF-grade shrinkage (a flagged v1.1 candidate) is actually justified — directly testable by bucketing residuals on the now-stored `weeks_of_current_season_grade_data` field and checking whether early/mid-season buckets show materially worse `MatchupContext` accuracy than late-season ones.
9. ADR-0015: heavy-rain extrapolation shape (additive 4.5pp vs. multiplicative 5.0pp) and the wind×precipitation interaction check — both are direct bucketed-residual queries against the stored weather sub-component snapshot.
10. ADR-0017: whether the Φ (CDF) z→points mapping is well-calibrated (a distributional check on stored z-scores, no actuals needed); the `2`/`5` injury-impact thresholds (bounded by the injury-flag-source caveat in section 4).
11. PRD Section 6 open questions: whether the 0.85x–1.15x multiplier caps generally, and the ADR-0006 confidence-gate thresholds (>50% margin, 15-snap floor, >70% shadow-match) specifically, are set right — the latter via comparing residual variance between the `gates_passed=true` cohort and the `fallback_used=true` cohort.
12. Section 11 item 7: whether vendor DST baselines already price in returner history — now a direct query (does `dst_projection.baseline.blended_value` move when `return_opportunity`'s identified primary returner changes, holding the defensive-grade legs roughly constant), rather than a one-off investigation only Data Integration Engineer could run by hand.

**Not enabled by this construct — needs new data ingestion first, still a real gap:**
13. PRD Section 6 open question: whether the run-game gap/zone split, or shell-level (Cover 1/2/3/quarters) coverage grading, is worth promoting to v1 — `ProjectionAccuracyRecord` would readily capture and backtest either once ingested, but can't manufacture a signal that isn't pulled today.
14. ADR-0013: QA spot-check of `gsis_id` coverage among slate-relevant players — a data-matching QA task, not a Section 6 scoring backtest; out of this construct's scope entirely.

**Not a technical gap — a scope/data-source question outside this ADR's authority:**
15. Section 11 item 6 (coordinator-level scheme attribution) — needs a currently-unconfirmed external data source (a coaching-history table); this construct doesn't touch that question either way.
16. Section 11 item 5 (dome-to-cold-outdoor-travel `MatchupContext` adjustment, v1 vs. v1.1) — this is an unbuilt formula, not a calibration question about an existing one. Once built, it would slot into this same tracking construct automatically; deciding whether to build it now is unchanged, still the Product Owner's/Chris's call.

## Alternatives considered

- **A single blended `predicted_points` vs. `actual_points` table, no component decomposition.** This is what "Performance Analytics" reads as a literal implementation of Section 10's one-line description. Rejected as insufficient — it answers "is the tool working" in aggregate but cannot answer any of the ~15 queued backtest items above, every one of which is a claim about a specific formula component, not the blended total.
- **Re-run the full formula with each leg individually zeroed out, to get an exact counterfactual attribution, rather than the log-space arithmetic decomposition in section 2.** Rejected — more expensive (one extra formula evaluation per leg per player-week, at scale), and, as section 2 explains, actually less clean once a combination step's cap binds, since removing one leg changes whether the cap binds for the others. The log-arithmetic approach is exact by construction and free as a byproduct of the formula as already specified.
- **Wait for PFF's historical projection-facet support and RotoGrinders' historical injury data to be confirmed before specifying this construct at all**, since full projected-vs-actual backtesting is gated on them. Rejected — the large majority of the queued backtest items (compiled list above) need only the multiplier/z-score components plus real actuals, neither of which is blocked by those two open items. Specifying the full record now, with the two gaps explicitly flagged rather than silently assumed away, unblocks 12 of 16 queue items immediately rather than blocking all of them on two unresolved vendor-data questions.
- **Fold this into Performance Analytics' existing Section 10 mandate outright, since it's obviously "tracking accuracy."** Not decided here — see Consequences. The Architect's decision rights cover the technical spec, not whether building it now changes Section 10's phase gating; that's flagged for the Product Owner below, not resolved unilaterally.

## Consequences

- **Implementation scope for the Data Integration Engineer:** (a) modify the existing `GameEnvironmentScore`/`MatchupContext`/`DSTProjection` computation code to retain and emit intermediate log-terms and z-scores rather than discarding them after producing the final number — a real but bounded refactor of already-working code, not new formula logic; (b) build the `ActualResult` pipeline (pbp-aggregated actuals, `dk_scoring.py`, the `stats_player`-release cross-check); (c) build the backtest-mode data adapters — `import_schedules()` for historical spread/total, Open-Meteo's archive endpoint for historical weather, and resolve the two open live-checks flagged in section 4 (PFF historical facet coverage, `import_injuries()` as an injury-flag substitute, PFF's projection-facet historical support).
- **This is a genuinely new piece of standing infrastructure**, not a one-off script — it needs to run every live week (writing `ProjectionSnapshot` before lock, `ActualResult` after games finish) and needs a backtest-mode runner capable of stepping through historical weeks 2023–2025 (bounded by whatever the PFF-history live-check in section 4 finds).
- **Model Analytics Expert should confirm the log-space attribution mechanism (section 2)** before this is treated as final — it's a new analytical technique (using stored log-terms for exact leg-level attribution) layered on top of already-approved combination formulas, not itself a Section 6 scoring formula, but it is the mechanism every downstream backtest query in section 6 depends on being correct.
- **Scope flag for the Product Owner, not decided here:** Section 10 assigns "how the tool is performing" to Performance Analytics, explicitly gated to "can't run meaningfully until Phase 2–3 produce real output" (full lineup ROI, contest-level results). But per-player projection-vs-actual tracking — what this ADR specifies — needs only projections (Phase 1 output, already in scope) and real game results (already available, historically and as each live week completes); it does not need the optimizer, generated lineups, or contest entries at all. Building `ProjectionAccuracyRecord` now is therefore not blocked by the same gate Section 10 describes, even though it's clearly in the same conceptual territory as Performance Analytics' eventual mandate. Two live options, genuinely undecided here: (1) this is Architect/Data Integration Engineer scope now, feeding Performance Analytics' dashboard once Phase 2–3 exist, with no phasing change needed since it was never actually blocked; or (2) building real accuracy-tracking infrastructure during Phase 1 is itself a phasing change to Section 10 that should get Chris's explicit sign-off before proceeding, even though it's technically unblocked, because Section 10's phase column is a scope statement Chris set, not just a technical dependency note. The Architect is flagging this distinction, not resolving it — see the corresponding PRD Section 11 addition.
