# ADR-0001: Approximating alignment-split coverage grades in MatchupContext

**Status:** Accepted (methodology), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 6 (`MatchupContext` — Coverage row), PRD Section 4 (PFF field-level availability note), `docs/phase0/data-availability.md` (PFF section)

## Context

The PRD's draft `MatchupContext` coverage formula (Section 6) assumed PFF exposes a coverage grade broken out by both scheme (man vs. zone) *and* alignment (slot vs. perimeter), matched to a receiver's own alignment split.

Phase 0 confirmed the scheme split directly: `GET /v1/facet/defense/coverage_scheme` returns `man_grades_coverage_defense` / `zone_grades_coverage_defense` per defender, exactly as assumed.

The alignment split does not exist as a graded field anywhere in PFF's API. `GET /v1/facet/signature/defense/slot_coverage` returns per-defender slot-coverage volume — snaps, targets, receptions, yards, `yards_per_coverage_snap` — but no `grades_coverage_defense`-equivalent scoped to slot alignment. There is no `perimeter_coverage` counterpart endpoint either. The only graded coverage number PFF exposes (`grades_coverage_defense` on `/v1/facet/defense/coverage_matchup`) is overall — it does not separate a corner's slot performance from their perimeter performance.

The Architect's own ground rule (PRD Section 6, and the Architect role definition) is explicit: every `MatchupContext` adjustment must trace to a specific stat or grade the pipeline actually pulls and computes against — never a narrative judgment with no numeric input — and if the data doesn't support an adjustment, the adjustment is dropped rather than implemented on a best-effort basis.

## Decision

Keep the alignment-sensitive coverage adjustment, but compute it as a derived approximation from two confirmed, real data sources rather than pulling a nonexistent PFF-native alignment-scoped grade:

1. Use `slot_coverage` volume data (coverage snaps) to identify which specific defender(s) on a team take the plurality of slot-coverage snaps vs. perimeter-coverage snaps. This is a real, computed classification — not a narrative judgment — built directly from PFF snap-count data.
2. Pull *that identified defender's* overall (non-alignment-split) man/zone coverage grade from `coverage_scheme`, rather than a team-wide aggregate, as the matchup grade for routes run from that alignment.
3. Blend a receiver's final coverage multiplier by the receiver's own slot-vs-perimeter snap-share split (also pulled from the offense side of the same volume data): a receiver who lines up 70% slot gets 70% weight on the identified slot-defender's grade and 30% on the identified perimeter-defender's grade.

This was judged different from the "no adjustment" case the ground rule describes. The ground rule exists to block adjustments with *no numeric input* — a grade invented from narrative scouting judgment at build time. Here, every number in the chain (snap volume, grade, snap share) is a real, pulled PFF value; what's missing is only a single pre-joined field that would have made the join trivial. Doing the join ourselves is engineering work, not a stand-in for missing data.

## Alternatives considered

- **Drop the alignment adjustment entirely, keep only the scheme (man/zone) adjustment.** Rejected for now. Slot vs. perimeter matchups are one of the more predictive real-world coverage signals in DFS (a possession slot receiver facing a weak nickel corner is a materially different matchup than the same receiver facing the same team's shutdown boundary corner) — dropping it loses real signal that the confirmed data can still approximately support. This remains the fallback if the Model Analytics Expert judges the defender-identification-by-volume step too noisy (e.g., if snap counts are too split across multiple corners to confidently identify a "slot defender" for a given team-week).
- **Use the team-wide overall coverage grade, undifferentiated by alignment, weighted only by the receiver's own slot/perimeter snap share.** This was Phase 0's literal suggested wording but is weaker than the decision above — it dampens the adjustment's confidence without actually approximating an alignment-specific *matchup*. It was upgraded to the defender-identification approach above so the adjustment still reflects which specific defender the receiver is likely to see, not just how much of the receiver's routes are run from the slot in the abstract.

## Consequences

- The coverage row in PRD Section 6 is written against this approximation, not a native PFF field. If PFF later adds an alignment-scoped grade endpoint, this ADR should be revisited and the formula simplified back to a direct pull.
- This adds a defender-identification step to the pipeline (classify each defense's primary slot vs. perimeter coverage defender by snap volume, per team, per week) that didn't exist in the original draft formula. This is Data Integration Engineer / implementation scope, not a new external data dependency.
- Open risk for Fantasy Football Expert review: some teams rotate corners situationally (base vs. nickel packages, matchup-based assignment rather than fixed slot/perimeter roles), which could make "the plurality slot-snap defender" a noisy or even misleading proxy in a given week. The FFE should sanity-check this against real personnel usage before sign-off, and the Model Analytics Expert should check how sensitive the resulting multiplier is to weeks with a near-even snap split (i.e., no clearly dominant slot or perimeter defender).
- No change to the multiplier cap range (0.85x–1.15x) or to the man/zone scheme adjustment, which remains a direct data pull with no approximation.
