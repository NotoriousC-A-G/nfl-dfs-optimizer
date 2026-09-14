# ADR-0019: Player-level game-script usage-share signal (`RoleShare` + `BlowoutVolumeDiscount`)

**Status:** Accepted (draft spec), pending Model Analytics Expert + Fantasy Football Expert sign-off
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 6 (`StackProfile`, `MatchupContext`), ADR-0004/ADR-0010 (spread-magnitude
band table), ADR-0006 (identification-gate pattern), ADR-0009/ADR-0012 (`DSTProjection`
`own_team_script_multiplier`, return-opportunity identification), ADR-0011 (shared shrinkage form)

## Context

Chris compared the walking-skeleton optimizer's output (pure vendor-blended projection, no
`MatchupContext`/`StackProfile` adjustment applied yet) against his own real Week-1 lineups and
flagged a real gap: "if we go on projection alone, we are following the field." His concrete
example — expecting DET to win comfortably, and building that belief into Jahmyr Gibbs (clock-control
rushing volume) in two lineups and Amon-Ra St. Brown (the passing-side vehicle for the same DET
game-environment thesis) in the third — is explicitly **not** a trench/coverage-grade question
(the existing `MatchupContext` rows). It's a claim that **spread/expected-margin should directly
shift a specific player's expected share of his own team's carries or targets**, and that the size
of that shift should depend on how concentrated the player's role already is (a bell-cow vs. a
committee back).

Section 6 has no mechanism for this today. The "Scheme / game flow" `MatchupContext` row only
feeds team-level PROE/pace into `GameEnvironmentScore` — it doesn't touch any individual player's
projection. Separately, `StackProfile`'s own spec already has an acknowledged, currently-blocking
gap: "ranked by target share ... not raw season totals alone" has no target-share data behind it —
`src/nfl_dfs/correlation/stack_profile.py`'s `primary_stack_candidates`/`bring_back_candidates`
fields are explicitly `None`, blocked on exactly this. This ADR was scoped to check whether solving
Chris's usage-share question and closing `StackProfile`'s candidate-ranking gap are the same
underlying data problem. **They are** — see Decision 1 below.

## Research method

Live queries against `nfl_data_py.import_pbp_data()` and `import_schedules()` for 2023-2025 regular
season (`n=816` scheduled games with a recorded `spread_line`/`total_line`). For every team-week:

- **Carry share**: a player's `rusher_player_id` play count ÷ team's total rush plays that week.
- **Target share**: a player's `receiver_player_id` target count ÷ team's total pass plays that week.
- **"Lead RB" / "WR1"** identified by **full-season** cumulative volume (a backtest-only
  simplification — see the ingestion-gap note in Decision 4; live ingestion must identify this
  trailing-only, which will be noisier).
- **Expected margin**: derived from `spread_line` (this team's implied margin of victory, sign
  positive = favored) — the pre-game variable actually available at lineup-lock time.
- **Realized margin**: `home_score - away_score` (or the mirror) — the ex-post variable, used only
  to establish whether a real relationship exists at all before asking whether the noisier pre-game
  proxy can detect it.
- **Role concentration** tiers, cut from the empirical distribution of full-season "lead RB" share
  (league mean **0.504**, std **0.110**, n=96 team-seasons; percentiles: 10th=0.362, 25th=0.422,
  45th=0.485, 60th=0.551, 75th=0.586, 90th=0.626): **bell-cow** ≥0.60, **mid-tier** 0.45–0.60,
  **committee** <0.45. WR1 league mean target share: **0.236** (std 0.045, n=96).

## Finding 1 — the raw, pre-game-spread-vs-usage-share correlation is close to zero

Linear correlation between pre-game expected margin and that week's realized usage share, no
tiering:

| Population | r | n |
|---|---|---|
| All identified lead RBs, carry share vs. pre-game margin | **−0.008** | 1,486 |
| All identified WR1s, target share vs. pre-game margin | **−0.026** | 1,540 |

Both are indistinguishable from zero. **Taken at face value, this does not support Chris's
hypothesis at all.** The rest of the research explains why the true picture is more nuanced than
"no effect," but the honest headline is: a naive linear read of the data does not show what the
hypothesis predicted.

## Finding 2 — role concentration does gate *whether a linear signal is detectable*, but the real mechanism is non-linear (blowout risk, not "favorite gets more")

Splitting by role-concentration tier surfaces a weak linear signal for bell-cows that isn't there
for committee backs (r = **0.072** bell-cow vs. **−0.036** committee, pre-game margin, n=315/461) —
consistent with Chris's "role clarity should matter" framing, on the surface. But a linear
correlation is the wrong shape to test here. Fitting a quadratic (`carry_share ~ margin +
margin²`) shows why: for bell-cow RBs against **realized** margin, R² = 0.038 (vertex at
margin ≈ −3.9, i.e., carry share peaks near a close game and falls off toward *either* extreme,
not monotonically toward "more favored = more volume"). Bucketed means confirm a hump, not a ramp:

| `\|realized margin\|` band | Bell-cow mean carry share | n |
|---|---|---|
| < 7 (close) | 0.681 | 157 |
| 7–14 (moderate) | 0.692 | 63 |
| > 14 (blowout) | **0.630** | 95 |

A Welch's t-test, close (<7) vs. blowout (>14), bell-cow tier, realized margin: **t = 2.96, diff =
5.25 percentage points (≈7.7% relative), p < 0.01** — statistically real. **But this is a
magnitude-only (blowout-risk) effect, not a directional (favorite-gets-more) effect** — it fires
symmetrically whether the team wins big or loses big, and there is no reliable uplift in the
moderately-favored band over the close/pick'em band. This directly matches the shape already
adopted elsewhere in this project: `DSTProjection`'s `own_team_script_multiplier` (ADR-0009) is
already spread-magnitude-only, either direction, for exactly this "garbage-time bench rotation vs.
running clock" reasoning — this finding is new empirical support for a mechanism the PRD already
uses in a different construct, not a new kind of claim.

## Finding 3 — the surprising result: role concentration does NOT clearly gate the *size* of the blowout effect

This is the honest, non-obvious finding, and it complicates Chris's original framing (that role
clarity should determine how much game script matters). Repeating the same close-vs-blowout test
across all three concentration tiers, realized margin:

| Tier | Close mean | Blowout mean | Absolute diff | Relative diff | t | n (close/blowout) |
|---|---|---|---|---|---|---|
| Bell-cow (≥0.60) | 0.681 | 0.630 | 5.25pp | 7.7% | 2.96 | 135 / 95 |
| Mid-tier (0.45–0.60) | 0.587 | 0.526 | 6.05pp | 10.3% | 4.22 | 293 / 223 |
| Committee (<0.45) | 0.466 | 0.406 | 6.01pp | 12.9% | 3.09 | 193 / 146 |

All three tiers show a statistically significant (p < 0.01), similarly-sized **absolute**
displacement (~5–6 percentage points), and if anything the **relative** displacement is largest for
committee backs, not smallest. This suggests the mechanism (a coach pulling/rotating the
lead-volume player once a game is decided) applies regardless of how concentrated that player's
normal role is — it isn't that a bell-cow "holds onto" his role better in a blowout than a
committee piece does. Role concentration remains necessary for a different reason (identifying
*who* the volume leader is at all, so the discount below has a correct target), but it is **not**
used here to scale the discount's size — a deliberate, evidence-driven departure from Chris's
original framing, stated explicitly rather than silently dropped.

## Finding 4 — the pre-game spread signal is real but much weaker and rarer than the realized-outcome signal

The realized-margin effect above is real, but it is not the variable available at lineup lock —
pre-game spread is. Repeating the same close/blowout test using pre-game `|spread|` instead of
realized `|margin|`, bell-cow tier: **t = 0.83, not significant, n = 5 in the >14 band** — badly
underpowered, because **pre-game spreads that large are rare**: across 816 games (2023–2025),
median `|spread|` = 3.5, mean = 5.08, and only **1.6%** of games carry a pre-game `|spread| > 14`
(8.3% exceed 10). The true causal channel is realized game state, not the spread itself — spread is
a noisy, low-frequency proxy for whether an actual blowout materializes (NFL score margins vary
enormously around the closing line). This is the same class of attenuation ADR-0007 already
grappled with for weather (external research vs. this project's own distribution), except here the
attenuation is worse: weather forecasts *are* the realized pre-game condition, while a spread is
only a prediction of a realized outcome that hasn't happened yet.

## Finding 5 — WR1 target share shows no usable signal, and what little there is runs opposite to the naive "trailing team dumps to WR1" story

| Realized game state | Mean WR1 target share | n |
|---|---|---|
| Trailing (margin < −3) | 0.241 | 565 |
| Close (\|margin\| ≤ 3) | 0.253 | 391 |
| Leading (margin > 3) | 0.260 | 584 |

WR1 target share is *lowest* when trailing, not highest — the opposite of a "garbage-time volume
flows to the WR1" story. This is football-consistent, not noise: trailing-team garbage-time volume
tends to spray across more targets (checkdowns to backs/TEs, more clock-stopping short completions
to whoever's open) rather than concentrating on the true WR1, while a leading team's passing
(fewer overall attempts, higher-value, chain-moving/putting-the-game-away throws) skews slightly
more concentrated on its best receiver. This is exactly the mechanism `StackProfile`'s existing
spread dampener (ADR-0004) already encodes qualitatively ("the trailing team's passing volume in a
blowout skews unreliable — checkdowns over the vertical/intermediate routes a bring-back is usually
built on") — it's independent confirmation of an existing formula's rationale, not evidence for a
new one.

## Decision

### 1. `RoleShare` — a new, shared per-player signal (serves both this need and `StackProfile`'s existing gap)

A new per-player signal, computed from nflverse play-by-play using the same aggregation pattern
already established in `src/nfl_dfs/ingestion/nflverse.py` (pace/PROE): trailing, season-to-date
through the last completed week (never the current week, per the ADR-0003 population discipline),
blended toward a league-average positional-role prior using ADR-0011's shared shrinkage form —

```
role_share_blended = w(n) * role_share_current_to_date + (1 - w(n)) * role_share_league_prior
w(n) = n / (n + k),  n = weeks played through week W-1,  k = 6  (reuses pace/PROE's k as a
                                                                   first-pass starting point,
                                                                   per ADR-0011's own precedent
                                                                   for reusing an existing k
                                                                   rather than inventing one;
                                                                   flagged for backtesting)
```

- **RB role**: `role_share` = player's share of team rushing attempts. League prior = **0.504**
  (this ADR's live computation, 2023–2025 mean full-season lead-RB share).
- **WR role**: `role_share` = player's share of team targets. League prior = **0.236** (live
  computation, same window, mean full-season WR1 share).
- **Role tiers** (RB only — see Decision 2 for why WR tiers aren't gated to a proven effect):
  bell-cow ≥0.60, mid-tier 0.45–0.60, committee <0.45 — cut points chosen directly from this ADR's
  own empirical distribution (roughly the 75th/25th percentiles of full-season lead-RB share), not
  arbitrary round numbers.
- **Identification gate**: reuses the ADR-0006/ADR-0012 pattern (plurality-holder + minimum-volume
  floor over a trailing window) rather than "whoever has the single most carries this week" — see
  Decision 4 for the exact gate proposed to the Data Integration Engineer.

This directly closes `StackProfile`'s already-documented gap: `src/nfl_dfs/correlation/
stack_profile.py`'s `primary_stack_candidates`/`bring_back_candidates` fields are blocked today on
"target-share data [that] needs nflverse play-by-play aggregation ... that hasn't been built." That
data need and this ADR's data need are the same computation (trailing, shrinkage-blended, per-player
share-of-team-volume from nflverse pbp) — one ingestion module serves both. `RoleShare` becomes the
ranking key `StackProfile` already specifies ("ranked by target share ... not raw season totals
alone") instead of a raw season total.

### 2. `BlowoutVolumeDiscount` — the actual game-script adjustment, deliberately narrower than originally framed

Applies **only** to a team's identified lead RB (any `RoleShare` tier — bell-cow, mid, or
committee; Finding 3 showed the effect is not reliably gated by concentration tier, so it isn't
used to scale this specific discount). **No WR1 formula is specified — see "What this ADR does not
build," below.**

```
blowout_volume_discount(|pregame_spread|):
    |spread| <= 10          -> 1.00   # no reliable pregame-detectable differentiation in this range
    10 < |spread| <= 14     -> 0.97   # weak, narrow-sample support only (see Finding 4) -- conservative
    |spread| > 14           -> 0.93   # closest pregame band to the realized-outcome effect (Finding 3),
                                       # damped well below the observed ~8-13% relative decline

adjusted_carry_share = identified_lead_rb.role_share_blended * blowout_volume_discount(|pregame_spread|)
```

This multiplies the RB's expected *volume/opportunity share* — a different axis from `MatchupContext`'s
efficiency multipliers (yards-per-carry, explosive-run rate). The two combine multiplicatively
downstream (volume × efficiency = projection), not via the capped log-space method used for
same-axis multipliers elsewhere in Section 6 — there's no double-counting risk to guard against
here the way ADR-0005 guards against it for pass-protection/coverage, since volume and efficiency
are genuinely independent legs of the same multiplication, not two grades of the same thing.

**Damping rationale for 0.93/0.97 (why not the observed 8–13% relative decline):** the same class
of reasoning as ADR-0007's weather damping, but pushed further given Finding 4's added attenuation
layer. The observed effect is real against *realized* margin; pre-game spread only weakly predicts
whether that realized state actually materializes (t=0.83, not significant, at the exact >14
pre-game cut, n=5 — too small to anchor a full-strength number on directly). Roughly halving the
low end of the observed relative-decline range (7.7–12.9%) and rounding to a clean number yields
**7%** (0.93) at the extreme tier; **3%** (0.97) at the 10–14 tier is an even more conservative
interpolation given that band has essentially no direct empirical support of its own (the >14
pre-game cut was the one actually tested) — both are explicitly flagged as damped, low-confidence
starting values pending backtesting, not derived point estimates.

**Frequency note, stated plainly so this isn't mistaken for a weekly lever:** only ~1.6% of games
carry a pre-game `|spread| > 14` (2023–2025), ~8.3% exceed 10. On a typical 10–13-game DK main
slate, this triggers on roughly 0–2 games per week. This is a rare-but-real differentiator, not a
systematic weekly adjustment — sized and framed accordingly.

### 3. What this ADR does not build, and why

- **No directional "favorite RB gets bumped" formula.** Finding 2 shows the real mechanism is
  magnitude-only blowout risk, not a monotonic favorite-side bump — building the directional
  version Chris originally described would mean fitting a formula to a pattern the data doesn't
  actually show, which the Architect ground rules explicitly rule out ("never a narrative judgment
  ... with no numeric input").
- **No WR1 target-share tilt.** Finding 5 shows no usable signal, and what little exists runs
  opposite to the naive story. Chris's own DET example (St. Brown as "a different vehicle for the
  same DET-game-environment thesis") is still well-served by the existing `GameEnvironmentScore`
  (team environment) and `MatchupContext` coverage/pass-protection rows (St. Brown's own matchup
  quality) — those already price a good environment into his projection. This ADR just declines to
  add a *second*, spread-specific volume tilt on top for pass-catchers, because the data doesn't
  support one.
- **No role-concentration scaling of `BlowoutVolumeDiscount`'s magnitude.** Stated in Decision 2 —
  Finding 3 is the reason.

### 4. Recommendation on whether to proceed at all

Per the task's explicit invitation to say so plainly if the signal comes back weak: **it did, for
the pre-game-actionable version of Chris's original hypothesis.** The realized-outcome signal is
statistically real (p<0.01 across all three tiers) but the pre-game-predictable version of it is
small, rare-triggering, and only thinly validated at the exact band that matters. Recommendation:
**proceed with the hedged, damped version above (`RoleShare` + `BlowoutVolumeDiscount`), not the
full directional version originally envisioned.** Two reasons to still build the hedged version
rather than holding off entirely: (1) `RoleShare` has clear, independent value regardless of this
narrower finding — it closes `StackProfile`'s already-blocking candidate-ranking gap either way;
(2) the blowout-risk piece, even damped and rare-triggering, is a real, GPP-relevant differentiator
exactly in the kind of double-digit-spread game where the field is most likely to over- or
under-load the chalk starter — small edges in rare, high-leverage spots are consistent with this
project's GPP-not-cash framing (Section 2).

## Structural placement — `StackProfile`, not `MatchupContext`

Explicit call, per the Architect's decision rights: **this lives under `StackProfile`, as a new
`RoleShare` signal plus `BlowoutVolumeDiscount` mechanism, not as an extension of `MatchupContext`'s
"Scheme / game flow" row and not as a wholly new `MatchupContext` row.** Reasoning:

- **Not a unit-vs-unit matchup.** Every existing `MatchupContext` row is an opposing-unit grade
  differential (this team's O-line vs. that defense's D-line, etc.). This signal depends only on a
  team's *own* spread and its *own* players' role shares — there's no opposing unit in the formula
  at all. Forcing it into `MatchupContext`'s table would misdescribe what it measures.
- **"Scheme / game flow" is a different granularity already, not a placeholder for this.** That row
  today literally is "feeds directly into `GameEnvironmentScore`'s pace and PROE components" —
  team-level, not player-level. Extending it to also carry a player-level volume-share adjustment
  would collapse two genuinely different signals (team play-calling tendency vs. an individual
  player's opportunity share) under one row name, which is more confusing than clarifying.
- **It answers `StackProfile`'s own question.** Chris's framing — "a different vehicle for the same
  game-environment thesis" — is literally `StackProfile`'s job description: which player, within an
  already-scored game environment, is the right vehicle to roster. `RoleShare` extends that beyond
  pass-catcher stack candidates to a team's primary rusher (not historically a "stack" leg, but the
  same underlying question of vehicle selection within a good environment). This is a real, modest
  scope expansion of what `StackProfile` covers (from "stacking correlation" to "primary-vehicle
  selection more generally") — flagged explicitly for the Product Owner below, not decided
  unilaterally.
- **Precedent already exists for exactly this placement pattern.** `DSTProjection`'s
  `own_team_script_multiplier` (ADR-0009) and `StackProfile`'s own spread dampener (ADR-0004) both
  already live as dedicated formula blocks referencing a shared spread-band table, outside
  `MatchupContext`'s table structure, for the same reason: they aren't unit-grade differentials.
  This ADR follows that same precedent rather than inventing a third pattern.

**Pipeline integration point (Section 5):** applied at the same point `MatchupContext` multipliers
apply (step 4) — `RoleShare`/`BlowoutVolumeDiscount` shape the RB's expected *volume* input to the
blended projection, `MatchupContext`'s run-game row shapes *efficiency* on top of that volume. Both
land on the same player's final number by the end of step 4; which module computes which factor is
what's being resolved here, not when in the pipeline it happens.

## Scope flag for the Product Owner (not decided here)

`StackProfile`'s PRD definition today is scoped to stacking/correlation ("Defines the correlation
thesis for a given team/game"). This ADR extends its practical surface to also cover single-player
volume-share adjustment for a team's primary rusher — a real, if small, scope expansion beyond
"which pass-catchers correlate with the QB." Whether that's a natural extension of `StackProfile`'s
existing job or ought to be named/scoped as its own construct is a Product Owner call, flagged here
per the Architect's decision-rights boundary rather than resolved unilaterally.

## Flag for the Data Integration Engineer — the ingestion gap

`src/nfl_dfs/ingestion/nflverse.py` today computes only **team-week** pace/PROE aggregates
(`aggregate_team_week`, keyed on `posteam`, no player ID anywhere in the module). Nothing in the
ingestion layer aggregates play-by-play to the **player** level. This needs a new module (same
file, or a sibling — Data Integration Engineer's call), analogous in structure to the existing
pace/PROE code, that produces:

1. **Player-week carry/target counts and shares**, aggregated exactly as this ADR's research script
   did: `rusher_player_id`/`receiver_player_id` groupby against the same team-week denominators
   `aggregate_team_week` already computes (reuse, don't recompute, the team totals).
2. **Trailing-only role identification** — important methodological correction from this ADR's own
   research method: the research above identified "the lead RB"/"WR1" using **full-season**
   hindsight, a valid backtest simplification but **not valid for live ingestion**. Production
   identification must use only weeks `1..W-1` (same discipline as ADR-0003/ADR-0014), which will
   be noisier — especially early season — than what this ADR's numbers reflect. Reuse the
   ADR-0006/ADR-0012 identification-gate pattern: a proposed starting gate (draft, not backtested)
   is **RB: >40% trailing carry share AND ≥15 trailing season carries**; **WR: >18% trailing target
   share AND ≥20 trailing season targets**; fall back to "no identified lead RB/WR1 this week" (not
   a guessed name) when the gate fails, mirroring ADR-0012's returner-role fallback.
3. **`role_share_blended` per identified player**, via `shrinkage_weight`/`blend_toward_prior`
   (already implemented, reusable as-is from `ingestion/game_environment_stats.py`) against the two
   league-prior constants this ADR computed live (RB: 0.504, WR: 0.236) — these should be
   recomputed periodically (e.g., yearly) rather than hardcoded indefinitely, same posture as
   `nflverse.py`'s existing 2024/2025 prior-season baseline pattern.
4. **No new data source** — everything above is `import_pbp_data()`, the same function and season
   window (2023–2025 confirmed live, current-season current) already used for pace/PROE. Pre-game
   spread is already ingested via `odds_api.py`, no new dependency there either.

## Alternatives considered

- **Extend "Scheme / game flow" in place**, scoped down to player-level. Rejected — see Structural
  placement above; conflates two different granularities under one row.
- **A new, standalone `MatchupContext` row** ("Game script / usage share"), keeping the table's
  four-row structure intact. Considered seriously, since it would keep all "shapes the blended
  projection before lineup construction" logic in one table. Rejected because `MatchupContext`'s
  own definition is explicitly unit-vs-unit ("Player projections built purely from historical
  performance miss the thing that matters most in football: what happens at the point of attack
  this week") — this signal has no opposing unit in it at all, and forcing a table built for grade
  differentials to also carry a pure own-team-spread signal would make every future reader ask "wait,
  who's the opposing unit here?" for no benefit.
- **Scale `BlowoutVolumeDiscount` by role-concentration tier** (e.g., a smaller discount for
  bell-cows, a larger one for committee backs), matching Chris's original intuition. Rejected per
  Finding 3 — the data doesn't support differential scaling; a single discount for any identified
  lead RB is what the evidence actually shows, and inventing a tier-scaled version anyway would be
  exactly the "narrative judgment with no numeric input" the Architect ground rules prohibit.
- **Build the WR1 tilt anyway, at a heavily damped magnitude**, for symmetry with the RB side.
  Rejected — Finding 5 doesn't show a weak-but-real signal to damp, it shows a near-null-to-reversed
  one; damping a null still produces a formula with no real signal behind it, which fails the
  evidentiary bar as clearly as skipping the damping would.

## Consequences

- `StackProfile`'s `primary_stack_candidates`/`bring_back_candidates` fields (currently blocked,
  `src/nfl_dfs/correlation/stack_profile.py`) get their target-share input from the same
  `RoleShare` ingestion this ADR specifies — one build, two consumers.
- A new, real scope question for the Product Owner (see above) — not resolved here.
- `BlowoutVolumeDiscount`'s band edges (10, 14) and multipliers (0.97, 0.93) are explicitly flagged,
  low-confidence, damped starting values — same status as every other Section 6 threshold pending
  Model Analytics Expert + Fantasy Football Expert sign-off and eventual backtesting via
  `ProjectionAccuracyRecord` (ADR-0018), which already has the decomposition machinery
  (`cap_absorption`, per-leg point contribution) to evaluate this once real results exist.
- No formula is added for WR1 target share — an explicit "held off," not a silent gap, so a future
  pass doesn't need to re-run this same research to rediscover the null result.
