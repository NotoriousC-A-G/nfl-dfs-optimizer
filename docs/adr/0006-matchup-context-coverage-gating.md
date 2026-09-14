# ADR-0006: Confidence gating for per-receiver coverage-alignment matching

**Status:** Accepted

## Context

PRD Section 6's coverage row wants a multiplier "based on the grade gap specific to *their* alignment, not the defense's grade as a whole." That requires confidently identifying which specific defender is "the" slot (or perimeter) defender for a given team in a given week — not always possible from snap-share data alone:

- **Rotational/committee coverage**: some teams rotate multiple corners through the slot with no clear primary.
- **Small samples**: a defender with a handful of slot snaps (garbage time, one series covering for an injury) isn't a meaningful "slot specialist."
- **Shadow coverage**: some defenses assign their best corner to follow a specific receiver across the formation regardless of alignment, which breaks the assumption that alignment predicts coverage responsibility — that corner will show up with significant snaps in *both* alignments, not because they're equally good at zone-covering both, but because they're following one man.

Section 6 also explicitly asks: "what is the fallback behavior when PFF's alignment-level splits aren't available at the granularity assumed" — this ADR is that answer for the coverage row specifically.

## Decision

`identify_alignment_defender(receiver, defenders)` in `matchup/coverage.py` applies three gates, in order, against the receiver's opponent's defenders. Any failing gate returns `None` (no confident match); the caller falls back to the team-wide grade differential instead.

1. **Snap floor (15).** A defender's snap count in the *target* alignment (the receiver's own dominant alignment — whichever of `slot_share`/`perimeter_share` is higher) must be at least 15 before that defender is treated as a specialist there. Below that, a handful of snaps is more likely noise than an assignment.
2. **Margin threshold (>50%).** Among defenders clearing the snap floor, the leading candidate's snap count in the target alignment must exceed the second-ranked candidate's by more than 50% relative margin: `(top - second) / top > 0.5`. Below that margin, coverage responsibility looks like a rotation, and naming the nominal leader as "the" defender would overstate confidence in a call that's really a coin flip.
3. **Shadow-coverage guardrail.** If the same defender who leads the target alignment *also* leads the opposite alignment (after the same snap-floor filtering), decline to attribute. A defender who tops both the slot and perimeter snap counts on a defense isn't a zone/alignment specialist — they're logging snaps wherever their assignment takes them, which is the signature of shadowing a specific receiver rather than owning a formation-based assignment. Attributing a "slot defender" match to them would misrepresent why they're on the field for those snaps.

The three states this produces — `confident`, `team_wide_fallback`, `no_data` — are the `CoverageConfidence` returned by `compute_coverage_multiplier`, consumed by `build_matchup_context_pool` and surfaced downstream (e.g. `scripts/live_integration_check_matchup.py`, and eventually Section 8's per-lineup rationale text).

## Consequences

- Both thresholds (15 snaps, 50% margin) are starting points, not calibrated against a season of data — per PRD Section 6's general note, thresholds need Model Analytics Expert sign-off before being treated as final. They're deliberately conservative (more likely to fall back to team-wide than to misattribute).
- The shadow-coverage guardrail is a heuristic derived only from `DefenderAlignmentSnaps` (no direct man-coverage/matchup data is ingested yet — PFF's `facet/defense/coverage_matchup` report would give a more direct signal here and is a candidate follow-up, not part of this change).
- `identify_alignment_defender` does not filter `defenders` by team itself — `ReceiverAlignmentShare` carries no team field, so the caller (`build_matchup_context_pool`) is responsible for passing in only the receiver's *opponent's* defenders.
