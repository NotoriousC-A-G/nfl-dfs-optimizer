# Lineup-objective redesign — detailed build plan

**Status:** DRAFT for Chris's sign-off, 2026-10-07. Synthesizes the 8-agent review of `lineup-objective-redesign-brief.md`
(architect, football expert, model-analytics, product owner, QA, data-integration, UI/UX, performance-analytics).
Glossary: `CONTEXT.md`. Branch: `lineup-objective-redesign`.
**Hard dates:** Wed 10-07 (today) → Fri 10-09 generation after practice reports → Sun 10-11 kickoff / late swap.

## Revision 1 — Chris's answers (2026-10-07), supersedes anything below that conflicts

1. **PRD amendment created** (ADR-0046): we are moving beyond v1; multi-agent runtime pipeline in scope, simulator still deferred.
2. **Six builder agents stay** (not three). Roster confirmed by Chris (D7).
3. **No separate "control" exclusion.** Chris may play any agent's lineup — including the old Chalk Anchor baseline — as L1/L2/L3 when it looks good; the log records which agent each played lineup came from (provenance), so grading works either way.
4. **Dup-risk buckets (PRD §7) stay as a post-filter** on each agent's own candidates.
5. **No silent fallback — fail loudly and diagnose (Chris):** if a stage can't produce a lineup, the run says so, names the stage and cause, and we fix it. Substituting a projection-max lineup "checks a box" and hides the problem, so it is not done. (Replaces the earlier fail-open/fallback design from the PO/architect/QA reviews.)
6. **Q handling is automatic, Chris overrides, and there is a Sunday-morning check.** Chris does not fill a clearance list before generation. The system decides each Q player from the official practice report (Full clears; Limited/DNP/no evidence stays out) and prints its basis; Chris overrides either way (`clear`/`bar`) when he has more information or disagrees (PR #55). A **Sunday-morning availability check** re-pulls statuses ~90 min before kickoff, flags any rostered player whose status changed, and offers a swap menu (next PR).
7. **Primary metric adopted** (best played lineup's rank percentile per week; see §4).
8. **Go:** #54 merged; step 0 started (PR #55).
9. **Scenario design revised (from Chris's pushback that four game types are too limited):** scenarios are the branches of each game's *pivotal questions* (D1 below), not four score-shape buckets.
10. **No forced correlation (Chris's pushback on the architect's per-game coherence constraint):** the per-game `y_g` constraint is dropped entirely. Correlation is used where it pays (the one required QB+pass-catcher stack, an optional bring-back), contradictory pairings are avoided (analyst states the sign), everything else is independent individual edge. A grind-game player can be a good play; a game's label does not decide its players' value.

## 0. What we are building, in one paragraph

A flag-gated pool pipeline that fails loudly (never silently substituting a lineup). Code builds a per-game **evidence packet**;
a **game analyst** (LLM) turns it into a **game thesis**: the game's pivotal questions and 2–4 **scenarios** (their combinations)
whose probabilities are anchored to the lines; an **expert** (LLM) picks **angles** and builds a **pool** (tiers + reasons + build
thesis) for each of the **six builder agents**; a code **validator** checks feasibility; the existing ILP maximizes **tail value**
inside each pool; everything is saved before kickoff so the post-mortem can grade it. Chalk Anchor (agent 1) is the baseline.
Friday output is a **forward experiment**, not a validated improvement.

## 1. Corrections to the evidence base (do not repeat the old claims)

- r = 0.46 projection↔actual is **normal** (trailing-mean r ≈ 0.28–0.39 on 2021–24). QB/TE r ≈ 0.14 is noise (SE ±0.13). The 3.6-pt top-decile gap is ~2.8 SE, in-sample, 2 weeks — regression to the mean, not proof of a broken model.
- The six agents are near-duplicates → effective n ≈ 1–2/week. A ~50-pt lineup miss ≈ 2 sd. "0 of 22 cashed" is weak evidence.
- What *is* solid: `bring_back_allowed` is **not** a hard constraint (scoring boost only); `ceiling_multiplier` is inert (98/629 populated, max ≈ 1.04); matchup grades ignore who is inactive; snapshot selection was buggy (fixed, #45); wk 3/4 operator projections were stale (fixed, #52/#53).
- Data corrections: nflverse injuries **already hold week-5 rows** for all positions (ADR-0031's "lags" premise is out of date) and nfl.com/injuries is plain scrapable HTML; **2026 participation data does not exist** (box counts/pressure/time-to-throw only from history); **no play-action column exists** anywhere in nflverse.

## 2. Resolved design decisions (reviewer conflicts settled)

| # | Decision | Basis |
|---|---|---|
| D1 | **Scenarios = branches of pivotal questions (revised, Rev 1 #9).** The analyst reads the packet and names the 1–2 questions that decide the game (e.g. "does PHI protect Hurts without Johnson?", "can LAR run while leading?"); scenarios are the combinations of answers (2–4 branches, exhaustive by construction), with stated dependencies between questions (the interaction loop). Each question declares a measurable proxy and threshold *before kickoff*. Code checks the probability-weighted mix still reproduces the game's line (margin/total) within tolerance. The old A–D taxonomy (shootout / favorite controls / grind / one offense broken) is kept only as a **grading backstop** (did the game finish like one of those?) and as a coverage checklist, not as the menu. A grind gets no blanket "no angle": its individual-edge players (lead back, TE, DST) and the fade of crowded stacks are valid angles. | Chris, Football, Model-Analytics |
| D2 | LLM may shift any scenario ≤ ±0.5 log-odds from its anchor (then renormalize), each shift with a cited reason; probabilities in 5-pt buckets, labelled "reasoned, uncalibrated"; both anchor and adjusted values logged. | Model-Analytics, Football, UI/UX |
| D3 | **Player outcomes by scenario:** coarse multipliers (0.8/0.9/1.0/1.15/1.3) on the shrunk baseline for mean and q90, clamped (draft 0.7–1.4 mean, 0.7–1.6 q90); ≤ 10 players/team named; never raw points. | Model-Analytics, Architect |
| D4 | **Tail value** = shrunk position×salary-tier q90 of weekly DK points: `TV = w·q90_player + (1−w)·q90_cell`, `w = n/(n+6)`, history 2021–24 as prior; then scenario multipliers. Mean level stays the dominant driver. Sanity gates: rank-corr(TV, projection) > 0.8; pool mean TV ≈ empirical q90; print top-20 TV vs top-20 projection. All magnitudes "draft, not backtested". Props excluded from TV until the id join lands. | Model-Analytics |
| D5 | **Pool → solver:** pool filter applied *after* `_eligible_pool` (injury/Q rules can never be resurrected); `exclude` tier dropped; **core = constraint** `Σcore ≥ 4` (not a bonus); real **bring-back constraint** + post-solve assertion; **no per-game coherence constraint** (Rev 1 #10): stacks are optional/soft beyond the PRD §7 required one. | Architect, QA |
| D6 | **Validator + widening, then fail loudly (Rev 1 #5).** Widening is principled and logged: (1) drop the cross-agent no-good cut → (2) relax `min_core` → (3) promote eligible players only at the *failing* position/game/team (never wholesale — a fully widened pool is just projection-max). If the agent still can't produce a valid lineup, **that agent fails with a diagnostic report** (which check, which constraint, pool counts, salary reach, the stripped players) and nothing is substituted. Injury exclusions and hard sliders are never widened. Feasibility checks: QB≥3 (3 teams), RB≥6, WR≥9, TE≥3, DST≥3, ≥2 stackable QB teams, cheapest roster < $50k, max-proj roster ≥ 95% of cap, dry solve yields ≥ 3 distinct core stacks. | QA, Architect, Chris |
| D7 | **Six builder agents (Chris).** Defined by the angle/mechanism they back, each with its own pool: **1 Chalk Anchor** (old-path projection-max baseline, kept as is); **2 Shootout Stack** (backs the high-total / both-offenses-function branch); **3 Contrarian Game** (game-level leverage: an under-owned game with a live environment); **4 Chalk Pivot** (the chalk game, different players); **5 Short Field** (backs "one offense broken": the other side's DST, RB, pass catchers); **6 Volume Anchor** (locked-in individual volume — lead back, TE, DST — including in low-total games, with only the required stack). **Confirmed by Chris.** Retired in this mapping: Game Script Architect (→ Shootout Stack), Matchup Purist (matchup is in every agent's brief), Arbitrageur (→ Chalk Pivot), Volatility Engine and Explosion/Shootout (ceiling is an input to tail value, not a persona). | Chris + Football + Architect |
| D8 | **Portfolio (Chris approved the lean):** top angle in **two** lineups that differ in the supporting cast (one chalk-leaning, one leverage), second angle in the third; Chris picks his three from any of the six agents' lineups and provenance is logged; lineups span chalk→leverage; share only a **short hard-avoid** (uncleared Q, game-time decisions, scenario-C games) — soft avoids are per-lineup. | Football, PO, UI/UX |
| D9 | **Dup-risk bucket rule (PRD §7):** kept as a *post-filter* on each agent's own oversampled candidates, not the old hard-coded per-slot targets. **Approved by Chris.** | PO vs Architect → compromise |
| D10 | **LLM runs** as session subagents with a file contract (`prompt.md`/`packet.json` in → validated JSON out); API backend later. Outputs carry player **IDs not names**; claims are `{text, cite_keys[]}` resolved against the saved packet; unresolved cites render UNVERIFIED. Cache key = sha256(packet_sha + prompt_version + model + clearance_file_sha); never silently re-ask. | Architect, QA, UI/UX |
| D11 | **Q rule** (ADR-0045; merged #54, revised in PR #55): Q = out unless the **official practice report** shows Full participation; Chris overrides either way; every decision printed with its basis. | Chris |
| D12 | **Scenario-tree simulator: out of Friday.** Pull in after ≥ 2 graded slates show scenario probabilities beat the line anchor, plus Chris's sign-off (ADR-0046 keeps it deferred). A simple deterministic proxy covers Friday. | PO, Model-Analytics, Architect |

## 3. Build plan (branches/PRs; new path flag-gated until validated, then primary; fails loudly)

| Step | When | Work | PR |
|---|---|---|---|
| 0 | **Wed now** | #54 merged; PR #55 adds the automatic Q decision + overrides and the **official injury capture** (run it Wed/Thu/Fri/Sat/Sun morning). Start **injury snapshot capture**: nflverse `import_injuries` (all positions, week 5) + nfl.com/injuries HTML parser, run Wed/Thu/Fri (ADR-0038 pattern). This is both the Q-clearance source and the unit-availability source; it is the highest value-per-effort item and has no blockers. Widen depth-chart ingestion to OL/DL/DB (one-line `positions` change). | #54, then PR-A |
| 1 | Wed–Thu | **Contracts + validator**: `build/{evidence,thesis,expert,pool}/contracts.py` (frozen dataclasses, `schema_version`), validators (prob sums, ID resolution, cite resolution, tier contradictions), widen fallback, hard-slider check. Real **bring-back solver constraint** + assertion. Tests first (QA's fixtures: hallucinated ID, probs sum 1.3, duplicate tier, Q in core). | PR-B |
| 2 | Wed–Thu | **Evidence packet v0** from existing signals: lines/implied totals (scenario priors), weather, GES/StackProfile, usage/role share, upside, receiving/QB-rush profile, availability list (Q rule applied + out/inactive starters), matchup grades labelled "season-to-date, availability-unaware", ownership where populated (say 320/629), explicit `data_gaps`. **Team-level unit adjustment** from PFF pass-block/pass-rush grades with a "starters out" fallback; per-player replacement needs the PFF↔depth-chart name+team join (collision-flagged, never guessed) — Thursday stretch goal. | PR-C |
| 3 | Thu | **Pool-constrained solve** (`optimizer/pool_solve.py`): optional `base_value_by_id` + `extra_constraints` on `_solve_single_lineup` (defaults keep old behaviour byte-identical; Chalk Anchor identity test guards it). TV base (D4). Seeded no-good cuts L1→L3. | PR-D |
| 4 | Thu | **Runners + prompts**: game-analyst and expert prompts (`PROMPT_VERSION`), file-contract runner, cache. Analysts on the top ~6–8 games by GES/stack viability only (rest get a code default, stay eligible, not backed); one expert call, 3 pools. Guardrails in prompt/schema: cite packet fields, "what this adds beyond the line", counter-scenario with probability, status basis for every availability claim, scenario-conditional sign for each player, ownership check on every beneficiary claim. | PR-E |
| 5 | Thu pm | **Wire-in**: one block in `live_integration_check_dashboard.py` before `save_slate_snapshot`, flag `NEW_BUILD`; failures raise with diagnostics (partial artifacts are saved for diagnosis); snapshot `redesign` envelope (git sha, prompt version, model, clearance sha, packet refs, theses, expert output, pools, pool lineups, control ref). **Dry-run rehearsal on the saved Week 4 snapshot — plumbing only, no performance claims.** | PR-F |
| 6 | **Thu noon + Thu eve: go/no-go** | If steps 1–4 aren't green by Thursday evening, we say so: **no generated lineups Friday until the issue is diagnosed and resolved** — no old-path substitute. Chris can still hand-build, using whatever packets/theses exist. | — |
| 7 | **Fri** | Final injury capture → run old path (saved first) → new path (Q decisions automatic; review the printed basis and override anything you disagree with, then re-run)  → validator → artifacts hash committed **before kickoff** → slate-brief page. Chris picks L1–L3 from the new output (control not in the played three unless Chris decides). | — |
| 8 | **Sun morning (required check)** | `scripts/sunday_availability_check.py` (next PR): re-pull official report + RotoGrinders ~90 min before the first lock, flag any rostered player whose status changed, show a same-pool swap menu per slot; log every manual change against the stored lineup. | PR-G |

**Acceptance (checkable):** Chalk Anchor (agent 1) unchanged and a regression test for the solver refactor; **any stage failure halts that agent/run with a diagnostic report and is never silently substituted**; no uncleared Q in any pool or lineup (Q decisions come from the official practice report + Chris's overrides); every pool validator-green (or that agent falls back, logged); `bring_back_allowed=False` verified post-solve; final = exactly 3 valid lineups with a stack each and distinct core stacks; every pool member has tier+reason; scenario sets sum to 1 and carry anchor+adjusted; every thesis names a counter-scenario; artifacts round-trip through the snapshot; runtime < ~20 min warm with per-call timeouts; pytest green, no live network in tests; magnitudes labelled draft.

**Friday output pages (UI/UX):** slate map (scenario strip, angle chips, Q flag) → game thesis cards (collapsed headline; expanded scenarios/chain/redistribution/counter-scenario) → angles-portfolio matrix (BACK/HEDGE/AVOID per lineup, shared hard-avoid, overlap grid, worst-case line) → per-agent lineup cards (core + Q-excludes only; rest a count) → Q/availability panel with Sunday checklist and a "copy lineup as text in DK order" button. Cite chips with hover source/as-of. Defer: players×scenarios grids, full pool tables, touch click-reveal, DK CSV (verify DK's template first).

## 4. Grading and the A/B (performance-analytics + model-analytics)

- **Pre-register before kickoff:** primary metric = rank percentile of the best played lineup per week (+ total score). Diagnostics only: scenario Brier/log-loss **vs the line-implied anchor** (reliability tables not before ~100 game-scenarios ≈ 7 weeks), thesis-claim hits (graded only in the scenario that occurred, on opportunity numbers not fantasy points), core-tier hit rate vs base rate, exclude regret, Q decisions, angle outcomes (right scenario/wrong players vs wrong scenario).
- Scenario-hit rules fixed in advance (e.g. Shootout: total ≥ 1.15×line or both ≥ 24; Favorite pulls away: margin ≥ 14 and favorite wins; Grind: total ≤ 0.8×line and bottom-tercile pace; D: team pressure+sack rate ≥ 75th pct and pass EPA ≤ −0.15; else "none" — a high "none" rate means the set is incomplete).
- **Control:** old path (Chalk Anchor + at most one more old agent). Always-on baselines saved at generation time: chalk, projection-max, random feasible. Write-once artifacts; hash committed pre-kickoff; log divergence between generated and entered lineups.
- **Decision rules:** no winner and no drops before ~8 weeks or a pre-registered ≥ 2 SE loss; keep a component only if paired difference has the right sign in ≥ 70% of weeks and the pooled CI excludes 0; revise-not-drop when scenario layer and pool layer disagree. No ROI claims (one $1M-to-1st contest dominates variance). Week 5 is n = 1: it proves nothing statistically — say so in the output.
- Post-mortem extensions: scenario-grading table, angle outcomes, A/B block first; thesis-claim grading and pool regret list once structured claims exist. Replace the biased stack "hit = actual > projected" with the angle-conditional grade (keep as a secondary column). Add contest-type buckets to the season record.

## 5. Risks (ranked) and mitigations

1. Schedule: whole pipeline by Friday is unlikely → thin slice + Thursday go/no-go; if it isn't ready we say so and diagnose — no old-path substitute.
2. (Removed: the per-game coherence constraint was dropped, Rev 1 #10.)
3. LLM schema/ID/tier errors → contract layer, one retry with error text, reject item, widen at the failing spot, then fail with a diagnostic.
4. Narrative over base rates / double-counting the line / TDs treated as skill / public injury news already in ownership → required guardrails in §3 step 4.
5. Cache staleness after Friday practice reports → clearance-file hash in the cache key.
6. Editing the ~900-line live script → one clearly delimited block only; failures raise with diagnostics.
7. Name+team join collisions for linemen → flag, never guess; fall back to team-level.
8. Contamination: no valid backtest of the LLM stages on wks 3–4 → forward-only.

## 6. Decisions

**Answered (Chris, 2026-10-07):** 1 PRD amendment — yes (ADR-0046). 2 Six agents — yes. 3 Any agent lineup can be played as L1–L3 — yes (provenance logged). 4 Dup-risk post-filter — yes. 6 Q handling — automatic with overrides + Sunday-morning check. 7 Metric — adopted. 8 Start now — yes.

**Still open**
- **(5) Fallback: answered (Chris) — none.** Say we can't generate a lineup, diagnose, and resolve; never substitute projection-max.
- **Agents 2–6 roster:** confirmed.
- **Rev 1 #9/#10 (pivotal-question scenarios; no forced correlation):** to be sent back to the football and model-analytics experts for challenge once Chris is happy with the direction.
