# ADR-0020: `RoleShare` corrections — a confirmed-uncontested prior and a concurrent-activity trailing window

**Status:** Accepted (draft spec), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** ADR-0019 (`RoleShare`/`BlowoutVolumeDiscount`, corrected here), ADR-0009 (QB-continuity confirmed-change
pattern, the precedent this ADR follows for Issue 1), ADR-0011 (shared shrinkage form, reused unchanged),
ADR-0006/ADR-0012 (identification-gate pattern), `src/nfl_dfs/ingestion/usage_share.py` (the implementation being
corrected)

## Context

Chris reviewed `usage_share.py` (the Data Integration Engineer's just-landed implementation of ADR-0019) and raised
two sharp, concrete catches. Both are corrections to this Architect's own ADR-0019 design, not new feature requests
— this ADR documents both findings and specifies the fix for each, following the same discipline ADR-0019 itself
used: check against real data before designing anything, and say so plainly if a hypothesis doesn't hold up.

## Issue 1 — shrinkage doesn't distinguish "genuinely uncertain" from "confirmed but data-thin"

### The problem

`role_share_blended = w(n) * trailing_share + (1 - w(n)) * 0.504` (RB) treats every thin trailing sample the same
way, whether it's a real coin-flip committee that hasn't resolved yet or a confirmed, uncontested lead back who
simply hasn't accumulated many current-season weeks yet (e.g., a competing back who left the roster in the
offseason). Chris's example: a Jahmyr-Gibbs-caliber back with no real competing back on the roster gets his
early-season trailing share discounted ~85% toward the generic, committee-inclusive 0.504 league average — the
same treatment a genuine 50/50 committee would get. This is the same class of problem ADR-0009's QB-continuity fix
already solved for a different construct: when a change is *confirmed*, blend toward a more specific prior that
reflects the confirmed situation, not a generic average that assumes the old uncertainty still holds.

### Research method

Live-queried `nfl_data_py.import_pbp_data()` for 2023–2025 regular season (same window and denominator conventions
as ADR-0019 and `usage_share.py`'s own `aggregate_team_week_volume`/`aggregate_player_week`). Two checks:

1. **Is "no real competing back" actually a rare, identifiable population?** Segmented all 96 team-seasons (same
   population ADR-0019's 0.504 prior was computed from) by the *season-total* share of the team's RB2 (second-most
   carries player). At progressively looser thresholds:

   | RB2 season share threshold | n | RB1 mean share | RB1 std |
   |---|---|---|---|
   | < 0.05 | 0 | — | — |
   | < 0.10 | 3 | 0.690 | 0.091 |
   | < 0.15 | 15 | 0.600 | 0.117 |
   | < 0.18 | 29 | 0.570 | 0.098 |

   No team-season in three years had a truly zero-competition RB2 across a *full* season (makes sense — a
   change-of-pace back always logs a handful of series over 17 games). But a real, identifiable population exists
   at looser cuts, and it clusters around known true workhorse situations (2025 J.Taylor, A.Jeanty; 2025 D.Henry;
   2024 K.Williams, C.Hubbard; 2023 T.Etienne — real names, not noise).

2. **Does a live-computable, trailing-only version of this signal actually predict the rest of the season?** This
   is the version that matters — Chris's example is specifically about *early*-season weeks, not full-season
   hindsight. For every team-season, took the first 2 trailing weeks, identified the non-QB rush leader ("RB1")
   and the non-QB runner-up's ("RB2") trailing carries, then checked RB1's **actual share over the remaining
   season** (≥3 further weeks required):

   | RB2 trailing carries (weeks 1–2) | n | RB1 rest-of-season mean share | std |
   |---|---|---|---|
   | ≤ 0 | 1 | 0.590 | — |
   | ≤ 2 | 4 | 0.588 | 0.099 |
   | ≤ 3 | 8 | 0.543 | 0.141 |
   | ≤ 5 | **16** | **0.560** | **0.117** |
   | > 5 (contested) | 80 | 0.412 | 0.171 |

   Welch's t-test, ≤5 vs. >5: **t = 4.25, diff = 14.8 percentage points** — a real, strong effect, not noise. The
   two independent methods (full-season segmentation and trailing-window prediction) land in the same 0.56–0.60
   neighborhood, which is meaningful cross-validation of the same underlying number from two different angles.

   **A necessary correction found while building this check, not an afterthought:** the naive "second-most-carries
   player" is sometimes the team's own QB (mobile-QB scrambles show up as `rusher_player_id` too — A.Richardson,
   C.Williams, K.Murray, and J.Fields all appear as a team's "RB2" in the raw ranking at various thresholds). A QB
   scrambling is not a competing running back. This check excluded any `rusher_player_id` who also had ≥50
   season pass attempts before ranking RB2 — the live version needs an analogous, trailing-computable exclusion
   (see Decision 1c below).

   **Named counterexample, stated plainly:** 2024 NYG (D.Singletary), which fired "uncontested" at the ≤3 and ≤5
   thresholds (RB2 = D.Singletary's teammate had 3 trailing carries), but Singletary's actual rest-of-season share
   fell to 0.236 — Tyrone Tracy Jr. emerged and took the job away mid-season. The signal is a real, strong average
   effect (t=4.25), not a guarantee for every team. This is an accepted, bounded failure mode — see Decision 1d.

### Decision 1 — `ConfirmedUncontestedPrior`: swap which prior gets blended toward, not the shrinkage curve

Following ADR-0009's precedent exactly (a confirmed situation changes which prior is used, not the shrinkage
form/`k` itself — ADR-0011's shared `n/(n+k)` curve and `k=6` are left untouched, deliberately, per Decision 1d):

**(a) New constant:** `UNCONTESTED_RB_PRIOR = 0.56` — this ADR's live trailing-window predictive computation
(0.560, n=16), cross-validated against the independent full-season segmentation (0.600, n=15 at the <0.15
threshold). Flagged, same as every other threshold in this module, as a **draft starting value from a small
sample (n=16), pending backtesting** — not a settled constant.

**(b) Firing condition (`uncontested_signal`):** for a team-role that has already cleared the *existing*
RB identification gate (trailing_share > 0.40 AND trailing_volume ≥ 15 — unchanged), additionally check the
second-highest-volume *non-QB* candidate in the same trailing window: if that candidate's `trailing_volume` is
**≤ 5** (or there is no second candidate at all), fire `uncontested_signal = True` for the identified leader.
5 is the exact cut this ADR's predictive check validated (n=16, t=4.25) — not a rounder or more convenient number
picked after the fact.

**(c) QB exclusion (new, necessary plumbing):** a rusher candidate is excluded from the "competing back" count
(step b) if that same `player_id` also appears as `passer_player_id` with **≥ 5 trailing pass attempts** for the
same team in the same trailing window. This is self-contained — `passer_player_id` is a column on the exact same
`pbp` DataFrame `aggregate_player_week` already consumes, so this is new code (a small additional aggregation),
not a new data dependency. 5 attempts is a deliberately low, conservative bar (any real starting QB clears it by
week 2; a genuine trick-play thrower would not), stated as a new, unvalidated heuristic constant like everything
else in this section.

**(d) When it fires:** use `blend_toward_prior(trailing_share, UNCONTESTED_RB_PRIOR, weight)` in place of
`blend_toward_prior(trailing_share, LEAGUE_PRIOR_SHARE["RB"], weight)` — same `weight = shrinkage_weight(n, k=6)`,
unchanged. **`k` is deliberately not changed.** ADR-0011's own framing ("no single season, even a complete one,
fully resolves uncertainty") is a project-wide conservative posture, not specific to the committee-vs-uncontested
question — a confirmed uncontested role is still a confirmed role in *this specific matchup context*, not a
guarantee against the Singletary-style counterexample found above. Swapping only the prior (not `k`) is the
narrower, better-evidenced fix; a future backtesting pass can revisit `k` if the swap-only version proves
insufficient, with real data behind that change rather than a guess.

**(e) Self-correcting, not sticky:** this is recomputed fresh every week from that week's trailing data. The
moment a real competing back accumulates >5 trailing carries (a new committee mate, a Singletary-style outcome),
`uncontested_signal` stops firing on its own and the team-role reverts to the generic `LEAGUE_PRIOR_SHARE`
blend — no separate "unflag" logic needed.

**New field:** `PlayerRoleShare` gets a new field, `prior_used: str` (`"league_average"` or `"uncontested"`), so
downstream consumers and QA can see which prior was applied to any given number, rather than inferring it.

### Alternatives considered (Issue 1)

- **Reduce `k` instead of (or in addition to) swapping the prior.** Considered per the task's own framing.
  Rejected for now — no data in this round derives a specific, defensible alternate `k` for this subpopulation
  (n=16 is too thin to fit a second curve on top of fitting a new prior), and inventing one without derivation
  violates the same "trace to a real numeric input" rule the swap-only fix respects. Flagged as an open
  backtesting question, not built.
- **A share-based threshold for "no competing back"** (e.g., RB2 trailing share < 10%) instead of a raw-count
  floor. Rejected — at 2 trailing weeks, team rushing volume is small enough (roughly 14–45 attempts) that a
  share-based cut is noisier than an absolute-count floor at exactly the sample sizes this signal needs to work
  at; the existing RB/WR identification gate already uses share+volume as two separate legs for the same reason.
- **Position-join to exclude QBs cleanly (roster data) instead of the passer-count heuristic.** Rejected — adds a
  real new dependency (crosswalking `rusher_player_id` to a roster position) that ADR-0019 explicitly declined to
  add for the same reason (module docstring's "Judgment call 2"); the in-pbp passer-count check is self-contained
  and sufficient for the observed contamination cases (all four are recognizable, high-volume starting QBs).

## Issue 2 — trailing-window aggregation doesn't account for missed games

### The question, checked against real data

Chris's question: is the LAC 2025 week-10 validation number — Omarion Hampton and Kimani Vidal each at ~29% trailing
carry share — a genuine even committee, or an artifact of Hampton missing time?

**Confirmed: it is an artifact, not a genuine committee — checked three independent ways.**

1. **Play-by-play touches (rush + targets), `nfl_data_py.import_pbp_data([2025])`, LAC, weeks 1–18:** Hampton
   recorded **zero rush attempts and zero targets in every one of weeks 6, 7, 8, 9, 10, 11, and 13** — a complete
   sever, not a slow stretch. Vidal recorded 0 carries in weeks 1–4, then took over as the every-down back exactly
   in that window (18, 9, 23, 12, 25, 5, 25 carries in weeks 6–11/13). Hampton returned in week 14 (13 carries) and
   was back to a clear lead role by weeks 15–17 (15, 16, 14 carries).
2. **Snap counts, `nfl_data_py.import_snap_counts([2025])`:** Hampton's offensive-snap % was 80/62/79/89/58 in weeks
   1–5, then **absent from the snap-count table entirely for weeks 6–13** (not "low," literally not on the field),
   then 31/36/55/81% weeks 14–17. Vidal's snap % rose from 3%/21% (weeks 4–5, token role) to 52–93% for exactly the
   same weeks 6–13 window Hampton disappeared.
3. **nflverse weekly injury report, `nfl_data_py.import_injuries([2025])`:** Hampton listed `Out` (week 13),
   `Questionable` (week 14, matching his practice return), `Out` (week 18). A web search independently confirms
   the mechanism: Hampton fractured his ankle in Week 5 and was placed on IR, missing the next several games before
   returning ahead of the Chargers' game against the Eagles — consistent, to the week, with the pbp/snap-count
   blackout above. ([Chargers.com](https://www.chargers.com/news/omarion-hampton-injury-report-raiders-week-13-2025),
   [SI.com](https://www.si.com/onsi/fantasy/injuries/los-angeles-chargers-place-rb-omarion-hampton-on-ir-following-week-5-ankle-injury),
   [NFL.com](https://www.nfl.com/news/omarion-hampton-chargers-activate-rookie-rb-injured-reserve-eagles-ankle))

**The exact ~29%/29% figure is reproduced, and explained, by the current formula:** trailing weeks 1–9 (as of a
week-10 projection), LAC's team rush-attempt total is 227; Hampton = 66/227 = **29.07%**; Vidal = 66/227 =
**29.07%** — an exact tie, matching the validation example. This is arithmetic, not a coincidence: Hampton's 66
carries all came in weeks 1–5 (before the injury), Vidal's 66 carries all came in weeks 6–9 (entirely *because of*
the injury) — averaging both across the same 9-week denominator manufactures a "tied committee" appearance out of
two sequential, non-overlapping single-back stretches.

**What the real split looks like when restricted to the weeks both backs were actually on the field (weeks
1–5, team total 114 rush attempts):** Hampton = 66/114 = **57.9%**, Vidal = 4/114 = **3.5%**. This is a
dominant-starter-with-a-token-complement picture, not anything resembling a genuine committee.

### Decision 2 — `ConcurrentActivityWindow`: exclude a "blanked" established candidate's weeks from the whole team-role's trailing computation

**Why not the two options as literally stated in the task, and what to build instead.** Two framings were
considered and both were tested against the LAC numbers above before choosing:

- *"Exclude a player's own zero/near-zero weeks from the OTHER player's denominator"* — correct in spirit, but
  needs to be made symmetric and generalizable to state precisely.
- *"Recompute share only across weeks the player in question was actually active"*, applied naively per-player —
  **tested directly and rejected**: filtering Vidal's own trailing window to "weeks Vidal himself had any touch"
  gives 155 carries / 359 team-attempts = **43.2%** — still badly inflated, because Vidal's own active weeks
  *are* the injury-replacement stretch. Filtering to "weeks the player in question was active" fixes Hampton's
  number (his active weeks correctly exclude his own injury absence, giving 124/234 = 53.0% — much closer to his
  true role) but does nothing for Vidal's, because the confound is about the *other* player's absence, not his own.

**The fix that actually matches both numbers to the concurrent-window ground truth:** exclude a trailing week from
the **entire team-role's** computation — the shared team-week denominator, every candidate's numerator, and the
week count feeding shrinkage — whenever an **established candidate** (defined below) recorded **zero combined
touches** (RB carries + WR targets, summed across both role rows for that `player_id` — a receiving back like
Hampton needs both counted, not just his RB-role row) in that week, while that same candidate has real volume
elsewhere in the trailing window. This is symmetric by construction (it doesn't matter which name gets called
"the primary" — it flags weeks that look abnormal for *any* established player, including the one asking to be
projected) and requires no chicken-and-egg "who's the leader" pre-identification step.

- **"Established candidate" floor:** `≥ 10` combined (RB+WR) touches elsewhere in the trailing window (excluding
  the week being tested). Stated explicitly as a **new, draft, unbacktested constant** — the same disclosure
  posture `usage_share.py` already uses for its gate thresholds — not empirically derived the way
  `UNCONTESTED_RB_PRIOR` was; a real second RB with only 3–4 mop-up carries all season shouldn't be able to zero
  out otherwise-good weeks.
- **"Blanked" is 0 touches, not "fewer than usual"** — deliberately a hard floor at exactly zero, matching the
  task's own framing ("a player with zero offensive snaps/touches ... is a strong signal of missed time"). A
  merely-quiet healthy game (e.g., 3 carries instead of 15) is not filtered; only a complete blank is.
- **Applied to `weeks_played` too:** the shrinkage-weight `n` for a team-role must be the **post-filter** week
  count, not `nflverse.py`'s raw `weeks_played` — a deliberate, stated divergence from `usage_share.py`'s current
  docstring claim that it "matches `nflverse.py`'s own `weeks_played` definition exactly." Using the raw count
  while summing only filtered weeks would understate real uncertainty (claiming more weeks of signal than were
  actually used).
- **Scope: RB role only, this round.** The concrete evidence (LAC) and the validated fix are both RB-specific.
  WR timeshares can have analogous injury-driven distortions, but nothing here validates a WR-specific version —
  flagged as an open question for a future round, not built now.

**Consistency check, not a coincidence:** re-running Issue 1's `uncontested_signal` check (Decision 1) against the
*corrected* LAC weeks-1–5 numbers, Vidal's concurrent-window volume (4 carries) clears the `≤5` uncontested
threshold — so the corrected data would also flag Hampton as `uncontested_signal = True` for a hypothetical
healthy week. That is the right answer: before the injury, LAC's backfield genuinely did look like a clear starter
with a buried backup, not a committee. The two fixes reinforce a single, more-accurate picture rather than
fighting each other. Apply Decision 2's window filter **before** Decision 1's gate/prior-swap check — Decision 2
cleans the input Decision 1's signal is computed from.

**Explicit scoping note, to prevent a different confusion:** `RoleShare` still answers "if this player suits up
this week, what's his expected share of team volume" — it is *not* the layer that decides whether Hampton is
actually active for a given week's slate (that is the separate injury/active-roster ingestion, e.g.
`rotogrinders_injuries.py`). Fixing the trailing-window artifact does not mean `RoleShare` should have "known"
Hampton was out for week 10 — a live pipeline gates that separately, upstream of ever consulting `RoleShare`.

### Alternatives considered (Issue 2)

- **Per-player "active weeks only" filter** (naive Option (b) from the task). Rejected with numbers above — fixes
  the injured player's own number but not the replacement's, because the confound is about someone *else's*
  absence.
- **A hand-identified "primary back" whose absent weeks get excluded from everyone else.** Rejected —
  requires solving the identification problem before you have clean data to solve it with (circular); the
  symmetric "any established candidate, blanked" rule needs no leader pre-identification.
- **Weight each week by the *number* of active established candidates that week (a continuous down-weighting
  instead of a binary exclude/include).** Considered as a smoother alternative. Rejected for this round as
  more complex to specify and validate without more data than a binary exclude — a good candidate for a future,
  backtested refinement, not a v1 blocker.
- **Do nothing, argue the ~29%/29% number is already "correct" as a season-to-date fact.** Rejected — the data
  confirms Chris's suspicion directly (three independent sources, a strong exact-number match, and outside
  corroboration); "correct as a literal average" and "useful for predicting a normal-health week's volume split"
  are different claims, and `RoleShare`'s whole purpose (per its own docstring, feeding `BlowoutVolumeDiscount`
  and `StackProfile`'s candidate ranking) is the second one.

## Edge cases and reliability concerns, stated plainly (per this task's own instruction)

- **QB-scramble contamination (Issue 1)** is real and was found, not hypothesized — four different real starting
  QBs showed up as a team's "RB2" in the raw ranking at various thresholds before exclusion. The ≥5-trailing-pass-
  attempts exclusion fixes the cases found; it is a new, unvalidated heuristic, not a proven-robust one.
- **The Singletary counterexample (Issue 1)** is a real, accepted failure mode: the average effect is strong
  (t=4.25) but not universal. Self-correction via weekly recomputation (Decision 1e) is the mitigation, not a
  claim the signal is always right in the moment it first fires.
- **Multiple overlapping injuries within one season (Issue 2)** could, in a chaotic backfield, exclude most of the
  trailing window under the concurrent-activity filter. This degrades gracefully (fewer weeks → smaller `n` →
  more shrinkage toward whichever prior applies, or an empty-candidates fallback if truly nothing is left) rather
  than failing unsafely — but it's a real scenario worth the Data Integration Engineer watching for in testing,
  not one this ADR has live examples of yet.
- **The `≥10`-touch "established candidate" floor (Issue 2) is not empirically derived** the way `5` (Issue 1) and
  `0.56` (Issue 1) are — it is a reasoned starting guess, flagged as such, and should be an early backtesting
  target once `ProjectionAccuracyRecord` (ADR-0018) has enough decomposed history to check it against.
- **Both fixes require new code in `usage_share.py`** (a passer-attempt aggregation for QB exclusion; a
  combined-touches-per-player-week check and a pre-filtering pass over the trailing window) — not new ingestion
  sources. Both operate on data `aggregate_player_week`'s parent `pbp` DataFrame already contains.

## Consequences

- `usage_share.py` needs, in order: (1) the `ConcurrentActivityWindow` filter (Decision 2) applied to the RB
  role's trailing window before candidate aggregation, changing `weeks_played` to the post-filter count for RB;
  (2) the QB-exclusion check (Decision 1c) when determining the "second candidate" for the uncontested-signal gate;
  (3) the `uncontested_signal`/`prior_used` logic (Decision 1b/1d) layered on top of the existing RB identification
  gate, unchanged otherwise. WR role is untouched by both fixes this round.
- Two new named constants enter Section 6's threshold list, both explicitly flagged as draft/unbacktested like
  every other value in this area: `UNCONTESTED_RB_PRIOR = 0.56` and the uncontested-signal's `≤5`
  trailing-carries cut (Issue 1); the `≥10`-touch established-candidate floor (Issue 2).
- `PlayerRoleShare` gains a `prior_used` field. No other public shape changes; `RoleShareResult`,
  `blowout_volume_discount`, and `adjusted_lead_rb_carry_share` are unaffected by this ADR.
- ADR-0019 is not rewritten in place — this ADR is a correction/addendum, same pattern as ADR-0008→ADR-0009 and
  ADR-0009→ADR-0011's shrinkage-form addendum.
- No Phase 4 concepts introduced (no discrete-outcome modeling, no roster/depth-chart ingestion added) — both
  fixes are pure aggregation-logic corrections to data already in scope.
