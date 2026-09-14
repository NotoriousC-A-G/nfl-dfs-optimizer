# ADR-0006: Confidence gates and shadow-coverage guardrail for the ADR-0001 alignment approximation

**Status:** Accepted (draft spec), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** ADR-0001 (`docs/adr/0001-matchup-context-alignment-coverage.md`), Model Analytics Expert review (item 4), Fantasy Football Expert review (item 1)

## Context

ADR-0001 approximates a missing PFF field (coverage grade scoped to slot/perimeter alignment) by identifying, per team per week, which defender takes the plurality of slot-coverage snaps and which takes the plurality of perimeter-coverage snaps, then using that defender's overall coverage grade as the matchup grade for routes run from that alignment. Both reviewers accepted the approach in principle but flagged it breaks in predictable, specific ways that ADR-0001 didn't yet guard against:

- **Weak plurality margin** (Model Analytics Expert) — a defender who "wins" the identification by a narrow margin (e.g., 34% vs. 30% of snaps) gets treated as fully representative of "the" slot or perimeter defender, with no discount for how close that call actually was.
- **Small-sample grade instability** (Model Analytics Expert) — a defender identified off low snap volume (a rotational piece, a backup pressed into duty) has an unstable underlying grade, and the approximation doesn't currently check volume before trusting it.
- **Shadow coverage** (Fantasy Football Expert) — a defense that shadows its CB1 onto the opponent's true #1 receiver will show that CB1 taking most of his snaps at perimeter on base downs, but traveling into the slot specifically on the passing downs that matter most, against the receiver whose target share actually drives the stack thesis. Plurality-by-total-snap-volume assigns the wrong grade on exactly those downs — this isn't a sample-size problem, it's a broken assumption (a fixed slot/perimeter role) for a specific class of defender.

## Decision

### 1. Two statistical gates, both required, before the identified defender's grade is trusted

**Margin threshold — absolute majority (>50%).** The plurality defender must account for more than 50% of the team's coverage snaps in that alignment, over the identification window (see below), before the identification is treated as reliable.

A fixed percentage-point margin (e.g., "leads the second-most-used defender by >10pp") was considered and rejected in favor of an absolute majority: a fixed-margin rule can still pass in a genuinely ambiguous case — e.g., 36% vs. 25%, with a third and fourth defender splitting the remaining 39% — where no single defender is really "the" alignment defender for that team-week even though the margin looks satisfied. Requiring a true majority (>50% of snaps) guarantees the identified defender was actually on the field for more than half of the relevant reps, which is a materially stronger and simpler claim to defend than "won by some margin over whoever was second."

**Snap-count floor — 15 coverage snaps in that alignment, within the identification window.** The identified defender must have logged at least 15 slot-coverage (or perimeter-coverage) snaps within the trailing window before their grade is used at all — regardless of what share of the team's snaps that represents. This catches the case a pure percentage-margin gate misses: a team with very low pass volume, or early in a rotation change, could produce a clean majority share off a tiny absolute sample (e.g., 8 of 14 total slot snaps charted so far). 15 is a deliberately low floor, not a claim that PFF grades are fully stable at that sample — it's meant to filter out the most extreme small-sample noise (single-digit snap counts) without being so strict that it fails the gate for most rotational-but-real starters. The Model Analytics Expert should tighten or loosen this exact number once backtested grade-stability data exists; it's a starting floor, not a derived constant.

**Identification window — trailing 3 weeks (current + prior 2 completed weeks), not single-week.** ADR-0001's original design re-identifies per team per week; both this snap-count floor and the margin threshold are easier to satisfy meaningfully, and far less prone to single-week noise flipping the identified defender for the same real-world personnel situation, over a rolling 3-week window. This directly addresses the "week-to-week churn" risk ADR-0001 itself flagged as a consequence. A 3-week window was chosen (over, say, season-to-date) to stay responsive to real personnel changes — an injury or a benching should be reflected within a few weeks, not smoothed away by a full-season history.

**Fallback when either gate fails:** degrade to ADR-0001's own previously-rejected alternative — the team-wide overall coverage grade, undifferentiated by alignment, weighted only by the receiver's own slot/perimeter snap-share split. This was ADR-0001's fallback-of-record already (see its Alternatives section); this ADR promotes it from "the thing we'd fall back to if the Model Analytics Expert judges the defender-identification step too noisy" to an explicit, automatic, per-team-week fallback triggered by these two gates rather than a manual judgment call made once at sign-off time.

### 2. Shadow-coverage confidence downgrade

Independent of the two gates above (a shadow corner can pass both gates and still be misidentified for the plays that matter): downgrade confidence — i.e., force the fallback in (1) — when a team's primary corner's snap-location pattern correlates with the opponent's #1 receiver's alignment specifically, rather than with a fixed slot/perimeter role.

Computable heuristic, built from the same snap-location data already pulled for the plurality identification: for each of a defender's games over a trailing window (e.g., the same 3-week window as above, extended to a longer lookback like 6 weeks specifically for this behavioral classification, since detecting a *pattern* needs more games than identifying *this week's* plurality role does), compute the per-play match rate between that defender's alignment (slot vs. perimeter, or field-side vs. boundary) and the opposing offense's #1 receiver's (by season target share) alignment on the same play. A defender whose alignment matches that game's opposing WR1's alignment on a high share of coverage snaps (e.g., >70%) across multiple recent games is flagged as a likely shadow corner.

When this week's opponent's identified "primary corner" for an alignment carries this flag, treat the gates as failed for that team-week regardless of whether the margin/snap-count numbers would otherwise pass, and use the team-wide fallback from (1) instead. This is a real numeric input (a computed per-play alignment match-rate against WR1, not a narrative "this guy is a shadow corner" judgment call), consistent with the Architect's ground rule that every `MatchupContext` adjustment trace to an actual pulled stat.

## Alternatives considered

- **A fixed percentage-point margin instead of an absolute majority.** Rejected — see margin-threshold rationale above.
- **Season-to-date identification window instead of trailing 3-week.** Rejected as the primary window — a season-long window would make the gates easier to pass (more cumulative snaps) but reintroduces the staleness problem ADR-0001's churn concern was about: a personnel change in week 8 would still be diluted by 7 weeks of prior-role snaps well into the back half of the season.
- **Drop the shadow-coverage guardrail and rely on the two statistical gates alone.** Rejected per the Fantasy Football Expert's review — a shadow corner can easily clear both the majority-share and snap-count gates (he may still be the plurality perimeter defender by raw count, just wrong on the downs that matter), so the gates alone don't catch this failure mode; it needs its own check.

## Consequences

- The identification step now requires a rolling 3-week (or 6-week, for the shadow check) snap-location history per defender, not just the current week's — a real but modest addition to what ADR-0001 already required the Data Integration Engineer to build.
- The team-wide fallback path (weighted only by receiver's own snap-share split) needs to be implemented and kept live in the pipeline as a real fallback, not a documented-but-unbuilt alternative — it will trigger routinely, not just in edge cases, especially for backup/rotational corners and any team with a shadow-coverage scheme.
- This doesn't change the multiplier cap range (0.85x–1.15x) or the man/zone scheme adjustment — both unaffected, as in ADR-0001.
- Model Analytics Expert should validate the 15-snap floor and the >70% shadow-match threshold against real distributional data once backtesting is possible; both are starting points, stated explicitly rather than left as open questions, per the Architect's decision-rights mandate to make the call rather than defer it further.
