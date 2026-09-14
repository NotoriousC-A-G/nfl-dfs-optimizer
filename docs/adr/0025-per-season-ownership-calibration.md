# ADR-0025: Per-season ownership-propensity calibration and the blend/recent stability test

**Status:** Accepted (implemented, run live against the full 2020-2025 ResultsDB backfill; see Consequences)
**Date:** 2026-09-14
**Owner:** Data Integration Engineer / Model Analytics Expert
**Related:** ADR-0023 (`docs/adr/0023-resultsdb-contest-history.md`, section 5's recommendation and section 4's
citation of the MLB build's `analysis/ownership_calibration.py`/`analysis/stack_calibration.py` precedent),
ADR-0024 (the storage layer this reads from), PRD Section 5 step 7 (ownership/leverage layer, still unbuilt),
Section 11 item 4, `src/nfl_dfs/storage/resultsdb_store.py`

## Context

ADR-0023 recommended fitting field-composition/ownership-calibration parameters **per season, not blended**,
across the confirmed 2020-2025 ResultsDB window, then running the MLB build's own stability test: compare
fitted parameters across seasons, blend only if statistically indistinguishable, otherwise let recent seasons
(2023-2025, the post-regime-change window ADR-0023 section 3 identified) drive production with older seasons
held out as validation. ADR-0023 explicitly deferred this as its own follow-up work, scoped separately from
the backfill orchestrator (ADR-0024) and from the actual Section 5 step 7 leverage-layer design.

Chris scoped this pass explicitly: fit **ownership-propensity only** (not dup-risk — that needs the `lineups/`
endpoint, which is still uningested, a real separate scope per ADR-0023's own "what was built this pass"
list), and **include 2020 in the fit and the stability comparison, flagged as a regime outlier** rather than
silently excluded — matching ADR-0023 section 5 point 2's own recommendation and the MLB build's treatment of
its own edge season.

## Decision

### 1. What "ownership propensity" means here, given what the data actually supports

ADR-0023 section 6 named the eventual target question: does RotoGrinders' *projected* ownership (LineupHQ,
already ingested live) track real field behavior. That join needs a live projection matched to a live slate —
it is not something six seasons of settled contests can answer by themselves, because (per ADR-0018 section 4,
already confirmed) historical vendor projections for those specific slates don't exist to pull.

What the historical data *can* answer, and what this ADR builds: **how does real DK GPP field ownership
actually distribute by salary, within position, and how consistent is that distribution season over season.**
This is the field-composition baseline the eventual leverage layer needs as its denominator — "is this
player's current projected ownership higher or lower than where a player at this salary/position typically
sits in a real field" — independent of whether a specific vendor projection turns out to be accurate.

### 2. The model: salary-decile ownership curves, fit per position per season

For each of the five DK-roster-relevant positions (`QB`, `RB`, `WR`, `TE`, `D` — the only positions with
non-trivial row counts in the real data; `FB`/`LS`/`K`/`LB`/`CB`/`DL`/`DE`/`MLB` appear as single-digit-to-low-
hundreds noise rows, an artifact of the payload's raw position tagging, not a real DK-rosterable pool, and are
excluded from fitting):

1. Within each `(date, contest_id, position)` group, rank rostered players by salary descending and assign a
   salary decile (0 = highest-salary tenth of that position's pool in that contest, 9 = lowest).
2. Per `(season, position, decile)`, take the mean `ownership_overall` across every player-contest row that
   fell in that decile — the empirical "how much of the field rosters the Nth salary tenth at this position."
3. Also compute the Pearson correlation between raw salary and `ownership_overall` per `(season, position)` —
   a single summary statistic the stability test compares, alongside the full decile curve.

This is deliberately the simplest model that's still a real field-composition signal, not a placeholder: it
answers "chalk concentrates at the top of the salary scale, and by how much" per position, which is exactly
the shape of question Section 5 step 7 needs a baseline for. It intentionally does not use `projected_points`
(present in the payload for a handful of vendor-covered players but sparse and not what this ADR is
calibrating) or attempt to model *which specific players* get chalky — that's the live leverage layer's job,
not this offline historical fit's.

### 3. Stability test: leave-one-out deviation on the salary-ownership correlation, per position

For each position, across the six fitted seasons' salary-ownership correlations: compute each season's
deviation from the mean of the *other five* seasons (leave-one-out, so one outlier season can't just pull the
group mean toward itself and hide its own deviation). A season is flagged unstable if that deviation exceeds
1.5x the leave-one-out standard deviation. This is a simple, stated-as-such heuristic — not a formal
hypothesis test — matching the honesty ADR-0023 itself modeled about not overclaiming statistical rigor beyond
what a six-point sample supports. 2020 is fit and included in this comparison (not excluded going in), then
its own flag carries a separate, permanent `is_regime_flagged=True` annotation regardless of whether the
leave-one-out test happens to also catch it — the regime caveat is a domain judgment (COVID-season NFL, per
ADR-0023 section 5 point 2), not something a six-point statistical test should be relied on alone to detect.

### 4. Production selection: blend if stable, else recent-window-only with older seasons held out

Per position: if the stability test finds no unstable seasons, blend all six seasons' decile curves (weighted
by each season's row count) into one production curve. If any season is flagged unstable, production uses only
the 2023-2025 window (ADR-0023's identified post-regime-change seasons), with the excluded older seasons kept
in the fitted-calibrations output as validation reference, not discarded — directly implementing ADR-0023
section 5 point 3's transplant of the MLB build's own resolved method.

## What was built this pass

`src/nfl_dfs/analysis/ownership_calibration.py`:

- `fit_season_calibration(season, *, base_dir=None)` → `SeasonCalibration` (per-position
  `SalaryOwnershipCurve`: decile-ownership map, row/contest counts, salary-ownership correlation; plus
  `is_regime_flagged`/`regime_note` for 2020).
- `compare_season_calibrations(calibrations)` → per-position `CalibrationStabilityResult` (leave-one-out
  deviations, `is_stable`, `unstable_seasons`).
- `select_production_calibration(calibrations, stability, *, recent_window=(2023, 2024, 2025))` → per-position
  `ProductionCalibration` (blended-or-recent decile curve, `source_seasons`, `blended` flag).
- `run_full_calibration(*, base_dir=None)` — convenience wrapper: fits all six seasons present in curated
  storage, runs the stability test, selects production calibrations, returns all three as one bundle.

`scripts/ownership_calibration_report.py` — CLI that runs `run_full_calibration()` against the real curated
data and prints a per-position report (each season's correlation, the regime flag, the stability verdict, and
the resulting production source).

**Live-run confirmation (not just unit-tested):** run against the real 71-contest, 39,239-row 2020-2025
backfill (ADR-0024) via `scripts/ownership_calibration_report.py`. Real per-season salary-ownership
correlations, QB/RB/WR/TE: consistently `+0.53` to `+0.69` across all six seasons — chalk really does
concentrate at the top of the salary scale for skill positions, every season, not a data artifact. `D`
(DST) is qualitatively different and much weaker/noisier: `+0.20` (2020) down to `-0.08` (2022), consistent
with the football reality that DST selection tracks matchup/opponent-implied-total far more than raw salary
tier, unlike the offensive skill positions.

**A real finding from the stability test, not tuned away:** on this actual data, the leave-one-out test
flags at least one season unstable for **every** position, including QB/WR/TE where the six seasons' own
correlations only span roughly 0.03-0.07 points — the six real seasons are qualitatively very consistent, but
not "statistically indistinguishable" by this heuristic's 1.5x-leave-one-out-std bar, which is tight relative
to how tightly clustered real NFL ownership-by-salary behavior actually is. Practical effect: **every
position's production calibration currently falls back to the 2023-2025 recent window rather than blending
all six seasons** — the "blend" branch is implemented and unit-tested (see
`test_select_production_calibration_blends_when_stable`) but does not currently trigger on the real data.
This is treated as an honest result, not adjusted after the fact to force a blend: it is also the more
conservative choice by construction, and lines up independently with ADR-0023 section 3's own finding that
the DK contest structure itself changed after 2021-2022 (the flagship Millionaire shrinking from ~28,000 to
~600-1,000 entries) — so favoring the post-regime-change seasons for production is defensible on football
grounds even before the statistical heuristic's own (arguably over-sensitive) verdict is considered.

**Deliberately not built this pass:**
- Dup-risk calibration (needs `lineups/` ingestion — ADR-0023's own explicit follow-up, unchanged by this ADR).
- The actual Section 5 step 7 leverage layer (joining this production calibration against live LineupHQ
  `POWN` projections to flag leverage spots) — this ADR produces the calibration *input* that stage needs, not
  the stage itself.
- Any dashboard surface for this — historical/offline analysis output only, consumed by future code, not yet
  by a person via a UI.

## Consequences

- Section 11 item 4's per-season-calibration follow-up (ADR-0023's own deferred item) is now resolved and
  implemented, not just recommended.
- The eventual Section 5 step 7 leverage-layer design has a real, live-data-backed baseline to build against
  instead of starting from nothing.
- Dup-risk calibration and the leverage layer itself remain explicitly open, tracked here rather than
  silently implied by this ADR's title.
