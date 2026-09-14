# ADR-0021: `bring_back_candidates` gated on `game_stack_viability` — a third, distinguishable empty-state

**Status:** Accepted (draft spec), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 6 (`StackProfile`), ADR-0004 (`game_stack_viability` formula and spread dampener),
ADR-0010 (5-band dampener tail extension), ADR-0017 (`GameEnvironmentScore.is_available` exclusion policy),
ADR-0019 (`RoleShare`, `select_stack_candidates`, the WR confident-candidate gate in `usage_share.py`),
`src/nfl_dfs/correlation/stack_profile.py` (implementation this ADR directs, not modified by this ADR)

## Context

PRD Section 6 already says, in two places, that a full game-stack (bring-back) build should be *conditionally*
favored, not unconditionally offered:

- "Game-stack viability score = a direct function of `GameEnvironmentScore` for both teams ... the higher the
  combined score, the more a full game-stack build is favored over a single-team stack."
- "Bring-back candidates (opposing pass-catcher in a game-stack build), selected the same way from the opposing
  roster."

`game_stack_viability(ges_a, ges_b, spread)` (ADR-0004, tail extended by ADR-0010) already implements the scoring
half of this: `min(GameEnvironmentScore(team_A), GameEnvironmentScore(team_B)) * spread_dampener(|spread|)`,
returning `None` only when either team's `GameEnvironmentScore` is unavailable (ADR-0017). But the most recent
`stack_profile.py` round (`select_stack_candidates` / `build_pivot_to` / `build_stack_profile`) populates
`bring_back_candidates` unconditionally whenever the opposing team has confident WR-role candidates (the
`usage_share.py` gate) — with no read of `game_stack_viability` at all. The two halves of the spec were built
correctly in isolation and never wired together: a 24-point-spread game with a rock-solid opposing WR1 by target
share still returns two named bring-back candidates today, with nothing in the data structure signaling that the
game environment itself doesn't support the thesis.

Chris's point, directly: this should be a **gate producing `None`** (no bring-back thesis at all this week), not a
populated list with a caveat appended to `notes`. A caveat still reads, to a consumer scanning the object, as "here
are your bring-back options, minus some fine print" — the opposite of what a 24-point blowout's actual bring-back
prospects deserve.

## Decision 1 — gate on `game_stack_viability` itself, not the dampener alone and not the raw bottleneck alone

Three candidate signals were considered:

1. **The dampener factor alone** (`spread_dampener(|spread|)`, i.e., spread magnitude only). This is exactly the
   game-script mechanism ADR-0004 was written to capture — a lopsided spread skews the trailing team's passing
   volume toward checkdowns — and it's tempting because the band table's own qualitative labels already shift at a
   specific edge ("meaningful lean, bring-back thesis weakens" at the 0.65 band → "real blowout risk" at the 0.40
   band, i.e., exactly the `|spread| = 10` line). But gating on spread alone ignores absolute environment quality:
   a pick'em game (dampener = 1.00) between two offenses with genuinely poor `GameEnvironmentScore`s (bad implied
   totals, slow pace, negative PROE) would sail through this gate untouched, even though neither team's environment
   supports *any* stack thesis, single-team or bring-back.
2. **The raw combined bottleneck** (`min(GameEnvironmentScore(team_A), GameEnvironmentScore(team_B))`, before
   damping). This captures absolute quality but ignores game script entirely — exactly the gap ADR-0004 was
   written to close in the first place. Gating on this alone would let a strong-bottleneck, 20-point-spread game
   through, which is precisely the case Chris is pointing at.
3. **`game_stack_viability` itself** (bottleneck × dampener, the value ADR-0004/ADR-0010 already fully specify and
   `game_stack_viability()` already computes). This is a single number that already combines both mechanisms —
   absolute two-team environment quality and game-script risk — into the one quantity Section 6 names as the thing
   that answers "is a full game-stack build favored here." It requires no new formula, no second independent
   threshold system running in parallel with an already-accepted one, and no judgment call about how to combine two
   separate gates if they disagree.

**Decision: gate on `game_stack_viability` (option 3).** It is the metric Section 6 already designates for exactly
this question ("the higher the combined score, the more a full game-stack build is favored"), it is already
formula-specified and implemented, and it is strictly more informative than either input alone — a low value can
correctly suppress the thesis whether the cause is a bad bottleneck, a harsh dampener, or (most often) some mix of
both, without this ADR having to adjudicate which mechanism "counts more." Reusing the existing composite also
avoids the two-gates-disagree problem a dual-signal design would create (e.g., dampener says "fine," bottleneck
says "suppress" — which wins?).

## Decision 2 — threshold: `game_stack_viability < 20.0` (on the existing 0–100 `GameEnvironmentScore` scale)

**`BRING_BACK_VIABILITY_FLOOR = 20.0`.** Below this, `bring_back_candidates` becomes `None` (see Decision 3 for how
this is distinguished from `bring_back_candidates`'s two pre-existing empty states). At or above it, candidate
selection proceeds exactly as it does today (`select_stack_candidates` against the opposing roster's `"WR"`-role
`RoleShareResult`).

Reasoning for `20.0`, grounded in what's already accepted rather than picked from nowhere:

- **Scale anchor.** `GameEnvironmentScore`'s CDF mapping (ADR-0017 Decision 1) means a component at its z-scored
  mean contributes exactly half its weight (`Φ(0) = 0.5`); with weather contributing near its full weight under
  neutral conditions rather than half (score.py's asymmetric weather multiplier), a fully average team-week
  composite lands somewhat above the arithmetic midpoint — roughly the mid-50s to low-60s, not exactly 50. `20.0`
  sits well below that average-team-week range: it is not "below-average gets suppressed," it is "meaningfully,
  not marginally, below average gets suppressed."
- **What actually produces a sub-20 result.** Because `game_stack_viability` is a product of a bottleneck (0–100)
  and a dampener (0.25–1.00), a sub-20 result only happens through some combination of a weak bottleneck and/or a
  real-or-worse blowout dampener — e.g., an exactly-average bottleneck (~50) crosses below 20 only once the
  dampener has already fallen to the 0.40 band (`|spread| > 10`, ADR-0010's "real blowout risk" tier) or worse; a
  materially below-average bottleneck (~30) crosses below 20 once the dampener drops to the 0.65 band (`|spread| >
  7`, "bring-back thesis weakens"). A strong bottleneck (~80, two above-average environments) does not cross below
  20 even at the extreme-blowout 0.25 floor (`80 * 0.25 = 20.0`, right at the line) — which is a deliberate,
  defensible outcome, not an oversight: two genuinely strong offenses that end up in a running-clock blowout are
  exactly the case where real garbage-time bring-back production (a trailing team forced into a fast-paced passing
  script) has historically shown up in this sport, and ADR-0004's own dampener design explicitly floors at 0.25
  rather than 0 for this reason ("garbage-time volume is a real, if unreliable, source of production — not a case
  for excluding the bring-back thesis outright"). A hard floor that also swallowed elite-bottleneck blowout games
  would contradict that already-accepted design intent.
- **Consistency with this project's existing floor-setting pattern.** ADR-0017 Decision 3 set a "low-impact floor"
  at `2` on a 0–10 injury-impact scale — 20% of that scale's practical range — specifically to filter out cases
  that are technically nonzero but not fantasy-relevant, without disturbing the meaningful mid/high split above it.
  `20.0` on `GameEnvironmentScore`'s 0–100 scale is the same 20%-of-range floor, applied to the same kind of
  question ("is there enough real signal here to say something specific, or is this noise not worth surfacing as
  an actionable recommendation").

This is a stated v1 starting point, not a backtested constant — flagged for the standing backtesting queue
(`ProjectionAccuracyRecord`, ADR-0018) exactly like the dampener bands themselves, the injury-impact thresholds,
and every other Section 6 numeric constant sourced this way. **Explicitly flagged tension for that backtest, not
resolved here:** the elite-bottleneck/extreme-blowout edge case above (`~80 * 0.25 ≈ 20`) sits right at the
boundary by construction, and whether real outcomes support "still populate" or "still suppress" at that specific
corner is exactly the kind of question that needs realized results, not a priori reasoning, to settle.

## Decision 3 — a fourth explicit state, distinguishable from the two that already exist

Before this ADR, `bring_back_candidates` could already be empty for two distinct, differently-meaning reasons
(`stack_profile.py`'s existing docstrings), collapsed into two Python values:

- `None` — the anchor or opposing team's `GameEnvironmentScore` is unavailable (ADR-0017): **no data to evaluate a
  thesis with at all.**
- `[]` — both `GameEnvironmentScore`s are available, but the opposing roster's `"WR"`-role `RoleShareResult` has no
  player clearing `usage_share.py`'s confident-candidate gate: **data is fine, but no specific player can be
  confidently named.**

This ADR adds a third reason `bring_back_candidates` can be non-populated — **the game environment is available and
a confident opposing WR is identified, but the combined game-stack thesis itself doesn't clear the bar** — which,
if represented the same way as the first case (`None`, no distinguishing signal), would silently collapse two
meaningfully different situations ("we have no idea" vs. "we know exactly who it'd be, and we're deliberately not
recommending it") into one ambiguous value. That ambiguity is precisely the kind of thing ADR-0017's own exclusion
policy was written to avoid one level up (`is_available=False` vs. a silently-imputed low score) — the same
discipline applies here.

**Fix: an explicit reason field, not a second overloaded `None`.** Add a `bring_back_status` field to
`StackProfile` (a small enum or literal string; implementation's choice), populated in all cases, not only the
non-populated ones — so a consumer never has to infer meaning from `bring_back_candidates`'s type alone:

```
bring_back_status:
    "populated"                  -- bring_back_candidates is select_stack_candidates()'s real result
                                     (possibly [] is NOT valid here -- see below)
    "no_confident_candidate"     -- bring_back_candidates == [] : both GameEnvironmentScores available,
                                     game_stack_viability >= floor, but no opposing WR clears the
                                     usage_share.py confident-candidate gate
    "environment_unavailable"    -- bring_back_candidates is None : ADR-0017, either team's
                                     GameEnvironmentScore.is_available is False
    "game_stack_not_viable"      -- bring_back_candidates is None : NEW this ADR -- both
                                     GameEnvironmentScores available, game_stack_viability < 20.0
```

`"populated"` covers a real, possibly-empty-if-no-candidate-clears-the-role-share-gate list — to keep the enum
strictly informative rather than redundant with `bring_back_candidates`'s own value, implementation should treat
`"populated"` and `"no_confident_candidate"` as the two sub-cases of "environment supports the thesis" (both require
`game_stack_viability >= 20.0` and both `GameEnvironmentScore`s available) and reserve `"environment_unavailable"`/
`"game_stack_not_viable"` for the two `None` cases. This gives every one of the four real situations a distinct,
machine-checkable label instead of relying on a reader to cross-reference `notes` prose or infer intent from
`None` vs. `[]` alone.

**Ordering when more than one condition is true.** `GameEnvironmentScore` unavailability is checked first (matches
`build_stack_profile`'s existing structure, where `ges_home.is_available`/`ges_away.is_available` gate everything
else, including whether `game_stack_viability` is even computable) — `"environment_unavailable"` takes priority
over `"game_stack_not_viable"` simply because the latter requires a real `game_stack_viability` number to compare
against the floor, which doesn't exist when either score is unavailable. This isn't a judgment call; it's the only
order that's computable.

**`single_team_viability_home`/`_away` and `primary_stack_candidates` are unaffected.** This gate applies only to
the bring-back side of `StackProfile`. Per ADR-0004's own original reasoning (restated in the PRD): "a lopsided
spread doesn't undermine a plain single-team stack the way it undermines a bring-back" — single-team viability was
deliberately never spread-dampened, so there is no analogous "does this even clear a bar" question for it to
answer, and this ADR does not introduce one. `primary_stack_candidates` keeps its existing two-state (`None` /
`[]`) semantics untouched.

## Decision 4 — `pivot_to`'s bring-back clause must track the same gate

`build_pivot_to` currently appends a "Bring-back viability `{score}` supports pairing with `{away_team}`'s own
pass-catchers" clause whenever `game_stack_score is not None` — i.e., whenever both `GameEnvironmentScore`s are
available, regardless of how low that score is. Left unchanged, this would produce a genuinely self-contradictory
`StackProfile` once Decision 1–3 land: `bring_back_candidates` (and `bring_back_status`) correctly reporting
`None`/`"game_stack_not_viable"`, while `pivot_to`'s own text still affirmatively claims the bring-back pairing is
"supported." `pivot_to` is specifically the *affirmative thesis* field (PRD Section 6: "not just an absence of red
flags") — it must not affirmatively assert a pairing the structured fields say doesn't clear the bar.

**Fix:** the bring-back clause's condition changes from `game_stack_score is not None` to `game_stack_score is not
None and game_stack_score >= BRING_BACK_VIABILITY_FLOOR` (equivalently: only when `bring_back_status` is
`"populated"` or `"no_confident_candidate"` — i.e., whenever the environment clears the bar, whether or not a
specific name was findable). `game_stack_viability` itself is **not** changed by this decision — see Decision 5.

## Decision 5 — `game_stack_viability` keeps returning its real computed value; only the candidate/thesis fields are gated

The gate governs whether `bring_back_candidates` is populated and whether `pivot_to` asserts the pairing — it does
**not** change `game_stack_viability`'s own return contract. `game_stack_viability` stays a pure diagnostic score:
still computed and returned as a real float whenever both `GameEnvironmentScore`s are available, all the way down
to the 0.25-dampener floor, exactly as ADR-0004/ADR-0010 already specify. Two reasons:

1. **It's the input the gate reads.** Gating the score on itself would be circular.
2. **Diagnostic value vs. actionable recommendation are different jobs.** An analyst or QA surface reviewing why a
   bring-back got suppressed this week needs to see the actual number (`game_stack_viability = 11.3`, say) to
   confirm the gate fired correctly and to see *how far* below the floor it landed — that visibility is lost if the
   score itself goes to `None` alongside the candidates. This mirrors ADR-0017's own separation of concerns:
   `is_available` (a flag) governs downstream consumption policy without deleting `composite_score`'s ability to
   exist as a real number when it is available; here, `bring_back_status` (a flag) governs downstream consumption
   policy without touching `game_stack_viability`'s own always-computed value.

## Alternatives considered

- **Gate on the dampener alone.** Rejected — see Decision 1, point 1: ignores absolute environment quality
  entirely, so a pick'em game between two poor offenses would incorrectly clear the gate.
- **Gate on the raw bottleneck alone.** Rejected — see Decision 1, point 2: ignores game script entirely, which is
  the exact mechanism Chris is pointing at (a strong bottleneck with a 20-point spread would incorrectly clear).
- **Two independent gates (a bottleneck floor and a dampener-band floor), both must pass.** Considered as a way to
  make each mechanism's contribution separately inspectable. Rejected for v1: it introduces a second arbitrary
  threshold alongside a second arbitrary "and/or" combination rule, when the single already-accepted composite
  metric already encodes both mechanisms without needing a combination policy invented for this ADR. Revisit only
  if backtesting shows `game_stack_viability` alone under- or over-suppresses relative to realized bring-back
  outcomes in a way a single number can't fix.
- **Keep `bring_back_candidates` populated with a stronger caveat in `notes`.** This is the status quo Chris is
  explicitly rejecting — a prose caveat still reads as "here are your options, with fine print," not "this game
  doesn't support the thesis." Rejected for exactly that reason.
- **Overload the existing `None` for the new case, distinguished only by a `notes` string.** Rejected — see
  Decision 3: this is exactly the ambiguity ADR-0017's exclusion-policy discipline (an explicit flag, not a
  same-shaped silent stand-in) was written to prevent one level up in the pipeline; the same reasoning applies
  here.
- **Zero out `game_stack_viability` itself below the floor (instead of a separate status field).** Rejected — see
  Decision 5: this destroys real diagnostic information (how far below the floor a game landed) for no benefit,
  and contradicts ADR-0004's own explicit design choice to never let the dampener floor a real score to a
  meaningless sentinel.

## Consequences

- `stack_profile.py` needs: a new module-level constant `BRING_BACK_VIABILITY_FLOOR = 20.0`; `build_stack_profile`
  reading `game_stack_viability`'s already-computed value before deciding whether to call
  `select_stack_candidates` for the opposing roster at all; a new `bring_back_status` field on `StackProfile`
  (populated in every branch, per Decision 3's four-state table); and `build_pivot_to`'s bring-back clause
  condition updated per Decision 4. No new data dependency — `game_stack_viability` is already computed from
  existing inputs before this gate would ever need to run.
- This is a behavior change for any existing consumer that currently treats `bring_back_candidates is None` as
  meaning only "no `GameEnvironmentScore` data" (ADR-0017's original single reason) — it now has two possible
  causes, distinguished only by `bring_back_status`. Any such consumer (lineup construction, QA/reporting surfaces)
  must be updated to branch on `bring_back_status` rather than re-deriving a reason from `None` alone.
  `primary_stack_candidates` is unaffected and keeps its original two-state contract.
- `20.0` joins the standing backtesting queue (`ProjectionAccuracyRecord`, ADR-0018) alongside the dampener bands
  it's built from — flagged, not blocking, per this project's consistent practice for every Section 6/7 numeric
  constant introduced ahead of real outcome data.
