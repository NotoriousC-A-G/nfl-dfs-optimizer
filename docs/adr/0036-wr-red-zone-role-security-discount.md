# ADR-0036: WR red-zone role-security discount -- a standalone downside-only construct

**Status:** Accepted (implemented, unit-tested, live-verified against a real slate pull)
**Date:** 2026-09-15
**Owner:** Chris, picking up ADR-0028's own named future candidate ("WR red-zone role-security")
**Related:** ADR-0028 (Component B, the original finding this construct expresses), ADR-0019
(`BlowoutVolumeDiscount`, the only prior downside-discount precedent this mirrors), ADR-0011
(shared shrinkage form), `src/nfl_dfs/ceiling/signals.py`, `src/nfl_dfs/composition/player_detail.py`

## Context

`CeilingMultiplier` Component B (ADR-0028) found a real, statistically significant NEGATIVE
relationship for WR: a red-zone-target-share "spike" (boom-rate on trailing red-zone share, the
same z-scored/shrunk construction Component A uses for role-share) predicts WORSE subsequent
relative performance, not better -- the opposite direction from every other real finding in this
project. Both experts converged this was real (survived a volume-tier robustness check across all
three tiers, independent of Component A's own signal, r=-0.0175) but architecturally impossible to
ship: `CeilingMultiplier`'s formula, `m_i = max(1.0, exp(scale_i * shrunk_z_score))`, is one-sided
and floored at 1.0 FROM BELOW specifically so it can only ever raise a projection -- a negative
`scale_i` just floors back to 1.0 for every player it would otherwise fire on and silently does
nothing. Both experts named this a candidate for "a future role-security/downside-risk construct,"
explicitly not another `CeilingMultiplier` leg. Chris chose to build it.

## Decision

### Design review (both experts, jointly, before any code)

The Model Analytics Expert specified: banded discrete tiers (mirroring `BlowoutVolumeDiscount`'s
shape, this project's only prior downside-discount precedent), not a continuous exponential curve
-- a downside construct has no self-limiting floor the way Component A's upside floor makes an
overconfident scale cheap to be wrong about, so a banded cap bounds the worst-case error. Damped to
roughly HALF the fitted slope on first ship (following `BlowoutVolumeDiscount`'s own "halve and
round to a clean number" precedent for a first-of-its-kind downside construct with no live track
record), not the raw backtested effect.

**A real gap the Model Analytics Expert caught mid-review, not assumed away:** the WR slope had
never actually received the cluster-robust SE check RB's comparably-borderline result got -- the
original backtest script gated that check to `role == ROLE_RB` only, and the "cluster-robust SEs
mandatory by default" bar was set in this project's methodology only AFTER Component B's original
run, never retroactively applied to WR's already-shipped-to-the-ADR numbers. Closed live: extended
`scripts/ceiling_red_zone_backtest.py`'s cluster-robust check to WR. Result: slope=-0.0784,
cluster-robust SE=0.0188 (G=698 player clusters), 95% CI=[-0.1153, -0.0415] -- still clears zero
cleanly, barely moved from the non-clustered CI.

**A second real check the Model Analytics Expert required before approving multiplicative
composition with Component A:** a joint regression (`log(relative_performance) ~ a_shrunk_z +
b_shrunk_z + a_shrunk_z*b_shrunk_z`, WR only) -- the original univariate A/B correlation check only
tested whether the two z-scores move together in general, not whether the log-space effect stays
additive when both signals are real and elevated simultaneously. Built and run live: n=13,442,
G=698 clusters. `a_shrunk_z` (controlling for B + interaction): +0.0735, CI=[0.0358,0.1111].
`b_shrunk_z` (controlling for A + interaction): -0.0758, CI=[-0.1129,-0.0388]. Interaction term:
+0.0386, CI=[-0.0317,0.1089] -- does NOT clear zero. Both signals retain ~92-97% of their
univariate effect size and the interaction is not statistically real -- multiplicative composition
(`final = base * component_a_multiplier * this_discount`) is safe.

### Final spec, both experts fully signed off

```python
WR_RED_ZONE_ROLE_SECURITY_DISCOUNT_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 1.00),          # shrunk_z_score <= 0 -- no discount at/below baseline
    (1.0, 0.96),          # 0 < shrunk_z_score <= 1.0 -- exp(-0.04 * 1.0)
    (float("inf"), 0.94), # shrunk_z_score > 1.0 -- exp(-0.04 * 1.5)
)
```

Damped slope `-0.04` (half of the fitted -0.0784, rounded clean -- the Model Analytics Expert noted
this lands almost exactly on the cluster-robust CI's own conservative bound of -0.0415, a useful
independent cross-check, not the sole justification). The top band is anchored at `z=1.5`, not a
further tail value, because Component A's own finding already established `shrunk_z >= 1.5` is
"structurally hard to reach" given this project's shrinkage math (`CEILING_SHRINKAGE_K=6.0`,
`MIN_TRAILING_WEEKS=3`) -- 1.5 is the real, actually-occupied practical ceiling, not an understated
extreme. `z <= 0` gets no discount, one-sided by construction: the football mechanism (a
single-game matchup exploit that gets scouted and shut down the following week) has no analog for
a player at or below their own baseline.

**The Fantasy Football Expert's final football-plausibility pass, fully approved as specified, no
change required:** `z=1.0` is a real, non-arbitrary "elevated" threshold given the shrinkage math
(not just a round statistical cut); the 4%/5.8% magnitudes read as "temper your expectations on
this specific input," the right register for a matchup-driven mechanism, matching
`BlowoutVolumeDiscount`'s own single-digit-percent range for a comparably single-game-driven
effect; the multiplicative stack with Component A is football-sensible (a player whose overall role
is genuinely growing per Component A, but whose specific red-zone number this week outran that
trend, is exactly the case both signals firing together should say something real about) with one
named non-blocking watch item (see Consequences); WR-only gating is correct and should NOT extend
to TE by analogy (TE red-zone usage is mechanically scheme-embedded/checkdown-driven rather than
the boundary-fade exploit dynamic that makes a WR spike scoutable -- an empirical question this
project hasn't asked, and extending by analogy here would repeat the exact shortcut that
necessitated Component B's own original independent review in the first place).

### Implementation

`ceiling/signals.py`: `WR_RED_ZONE_ROLE_SECURITY_DISCOUNT_BANDS` constant +
`wr_red_zone_role_security_discount(signal: CeilingSignal) -> float | None` (mirrors
`component_a_multiplier`'s "unknown is not neutral" discipline -- `None`, never a fabricated 1.0,
when `signal.shrunk_z_score is None`). No new data layer: reuses `red_zone_ceiling_signals(pbp,
target_week, ROLE_WR)` verbatim, the exact signal Component B already computes but never shipped a
live consumer for.

`composition/player_detail.py`: new `red_zone_role_security_discount`/`_reason` fields on
`PlayerDetailRecord`, gated strictly to `position == "WR"`, joined via the same
`identity.nflverse_gsis_id` key every other ceiling-adjacent field already uses. `ceiling_projection`
(the derived property) now composes both: `projection * ceiling_multiplier *
red_zone_role_security_discount` -- but the discount defaults to a neutral 1.0 when absent (RB/TE/
QB/DST, or no signal this week) rather than blocking the property the way a missing
`ceiling_multiplier` does, since the discount is only ever an ADDITIONAL downward adjustment, never
a second precondition for computing a ceiling read at all.

`dashboard/renderer.py`: the existing "Ceiling" column's cell gains a second sub-line
("0.94x red-zone role-security") whenever the discount is real and actually below 1.0 -- the
already-composed `ceiling_projection` main number reflects both factors; this line makes the
discount itself visible, not just its already-applied result.

`scripts/live_integration_check_dashboard.py`: wired `red_zone_ceiling_signals(pbp, WEEK, ROLE_WR)`
alongside the existing Component A computation, passed through as `red_zone_signals_by_gsis_id`.

## Consequences

- **Live-verified against a real slate pull**, not just unit-tested -- confirms the wiring end to
  end (signal computation, gsis_id join, composition into `ceiling_projection`, dashboard render).
- Closes ADR-0028's own explicitly-named gap: a real, validated finding that was architecturally
  impossible to ship as a `CeilingMultiplier` leg now has a real, live construct that expresses it
  correctly, rather than remaining a dead end in an ADR's "what this leaves behind" section.
- **One non-blocking watch item, named by the Fantasy Football Expert for live monitoring, not a
  pre-ship blocker:** the near-zero population-level A/B correlation doesn't guarantee a correct
  read on every individual player-week -- a WR whose red-zone spike is the leading edge of a real,
  ongoing role expansion (not a one-off matchup exploit) could get dinged by this discount even
  though Component A is correctly crediting the same underlying growth. If early live usage shows
  the discount consistently firing on players who then KEEP an elevated red-zone share for multiple
  subsequent weeks (rather than reverting, the pattern the mechanism assumes), that's the signal to
  revisit -- not before ship, but as the first thing to check if live reads feel off.
- This is a genuinely new architectural category for this project: the first standalone,
  non-`CeilingMultiplier` downside-discount construct built from a `ceiling/signals.py` signal.
  Structurally distinct from both `CeilingMultiplier` (upside-only) and `BlowoutVolumeDiscount`
  (different predictor -- pregame spread, not a ceiling z-score) -- flagged for the record as its
  own category, not silently filed under either existing one.
