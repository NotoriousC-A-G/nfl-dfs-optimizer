# Lineup-objective redesign — detailed build plan

**Status:** DRAFT for Chris's sign-off, 2026-10-07. Synthesizes the 8-agent review of `lineup-objective-redesign-brief.md`
(architect, football expert, model-analytics, product owner, QA, data-integration, UI/UX, performance-analytics).
Glossary: `CONTEXT.md`. Branch: `lineup-objective-redesign`.
**Hard dates:** Wed 10-07 (today) → Fri 10-09 generation after practice reports → Sun 10-11 kickoff / late swap.

## 0. What we are building, in one paragraph

A flag-gated, fail-open "pool pipeline" that runs *beside* the existing six-agent path. Code builds a per-game **evidence packet**;
a **game analyst** (LLM) turns it into a **game thesis** with 3–4 **scenarios** whose probabilities are anchored to the lines;
an **expert** (LLM) picks **angles** and builds a **pool** (tiers + reasons + build thesis) for each of 3 builder agents; a code
**validator** checks feasibility; the existing ILP maximizes **tail value** inside each pool; everything is saved before kickoff so
the post-mortem can grade it. Old path always runs first and is the control. Friday output is a **forward experiment**, not a validated improvement.

## 1. Corrections to the evidence base (do not repeat the old claims)

- r = 0.46 projection↔actual is **normal** (trailing-mean r ≈ 0.28–0.39 on 2021–24). QB/TE r ≈ 0.14 is noise (SE ±0.13). The 3.6-pt top-decile gap is ~2.8 SE, in-sample, 2 weeks — regression to the mean, not proof of a broken model.
- The six agents are near-duplicates → effective n ≈ 1–2/week. A ~50-pt lineup miss ≈ 2 sd. "0 of 22 cashed" is weak evidence.
- What *is* solid: `bring_back_allowed` is **not** a hard constraint (scoring boost only); `ceiling_multiplier` is inert (98/629 populated, max ≈ 1.04); matchup grades ignore who is inactive; snapshot selection was buggy (fixed, #45); wk 3/4 operator projections were stale (fixed, #52/#53).
- Data corrections: nflverse injuries **already hold week-5 rows** for all positions (ADR-0031's "lags" premise is out of date) and nfl.com/injuries is plain scrapable HTML; **2026 participation data does not exist** (box counts/pressure/time-to-throw only from history); **no play-action column exists** anywhere in nflverse.

## 2. Resolved design decisions (reviewer conflicts settled)

| # | Decision | Basis |
|---|---|---|
| D1 | **Scenarios:** 3 by default per game — **A Shootout**, **B Favorite controls**, **C Grind**. A 4th, **D One offense broken (by side)**, is added only when the packet shows a large availability gap on one side. Priors for A–C are computed in code from spread/total (margin ~ N(spread, 13.5), total sd ≈ 10 — verify on history); D's prior is carved out of B/C and bounded. C gets no GPP angle. | Football + Model-Analytics + PO |
| D2 | LLM may shift any scenario ≤ ±0.5 log-odds from its anchor (then renormalize), each shift with a cited reason; probabilities in 5-pt buckets, labelled "reasoned, uncalibrated"; both anchor and adjusted values logged. | Model-Analytics, Football, UI/UX |
| D3 | **Player outcomes by scenario:** coarse multipliers (0.8/0.9/1.0/1.15/1.3) on the shrunk baseline for mean and q90, clamped (draft 0.7–1.4 mean, 0.7–1.6 q90); ≤ 10 players/team named; never raw points. | Model-Analytics, Architect |
| D4 | **Tail value** = shrunk position×salary-tier q90 of weekly DK points: `TV = w·q90_player + (1−w)·q90_cell`, `w = n/(n+6)`, history 2021–24 as prior; then scenario multipliers. Mean level stays the dominant driver. Sanity gates: rank-corr(TV, projection) > 0.8; pool mean TV ≈ empirical q90; print top-20 TV vs top-20 projection. All magnitudes "draft, not backtested". Props excluded from TV until the id join lands. | Model-Analytics |
| D5 | **Pool → solver:** pool filter applied *after* `_eligible_pool` (injury/Q rules can never be resurrected); `exclude` tier dropped; **core = constraint** `Σcore ≥ 4` (not a bonus); real **bring-back constraint** + post-solve assertion; per-game coherence `y_g` (≥3 players from a backed game) **behind a second flag**. | Architect, QA |
| D6 | **Validator + widen order** (each step logged): (1) drop cross-agent no-good cut → (2) relax `min_core` → (3) promote eligible to whole game/team → (4) that agent falls back to old-path lineup, labelled. Injury exclusions and hard sliders are never widened. Feasibility: QB≥3 (3 teams), RB≥6, WR≥9, TE≥3, DST≥3, ≥2 stackable QB teams, cheapest roster < $50k, max-proj roster ≥ 95% of cap, dry solve yields ≥ 3 distinct core stacks. | QA, Architect |
| D7 | **Builder agents for Week 5 (3):** **Shootout Stack** (backs A), **Contrarian Game** (backs D / game-level leverage), **Chalk Pivot** (same game as chalk, different players). **Chalk Anchor** stays in the *old* path as the control (provably inert → regression test). Ceiling Chaser, Matchup Purist, Game Script Architect, Volatility Engine: not rebuilt now. Agents are defined by the scenario they back, not by attitude. **Needs Chris.** | Football + PO + Architect |
| D8 | **Portfolio (Chris approved the lean):** top angle in **two** lineups that differ in the supporting cast (one chalk-leaning, one leverage), second angle in the third; lineups span chalk→leverage; share only a **short hard-avoid** (uncleared Q, game-time decisions, scenario-C games) — soft avoids are per-lineup. | Football, PO, UI/UX |
| D9 | **Dup-risk bucket rule (PRD §7):** keep as a *post-filter* on each agent's own oversampled candidates, not the old hard-coded per-slot targets. **Needs Chris.** | PO vs Architect → compromise |
| D10 | **LLM runs** as session subagents with a file contract (`prompt.md`/`packet.json` in → validated JSON out); API backend later. Outputs carry player **IDs not names**; claims are `{text, cite_keys[]}` resolved against the saved packet; unresolved cites render UNVERIFIED. Cache key = sha256(packet_sha + prompt_version + model + clearance_file_sha); never silently re-ask. | Architect, QA, UI/UX |
| D11 | **Q rule** (ADR-0045, PR #54) is a prerequisite and must be merged first. | PO, Architect |
| D12 | **Scenario-tree simulator: out of Friday.** Pull in after ≥ 2 graded slates show scenario probabilities beat the line anchor, and with a PRD amendment. A deterministic coherence proxy covers Friday. | PO, Model-Analytics, Architect |

## 3. Build plan (branches/PRs; every new path flag-off by default, fail-open)

| Step | When | Work | PR |
|---|---|---|---|
| 0 | **Wed now** | Merge #54 (Q rule). Start **injury snapshot capture**: nflverse `import_injuries` (all positions, week 5) + nfl.com/injuries HTML parser, run Wed/Thu/Fri (ADR-0038 pattern). This is both the Q-clearance source and the unit-availability source; it is the highest value-per-effort item and has no blockers. Widen depth-chart ingestion to OL/DL/DB (one-line `positions` change). | #54, then PR-A |
| 1 | Wed–Thu | **Contracts + validator**: `build/{evidence,thesis,expert,pool}/contracts.py` (frozen dataclasses, `schema_version`), validators (prob sums, ID resolution, cite resolution, tier contradictions), widen fallback, hard-slider check. Real **bring-back solver constraint** + assertion. Tests first (QA's fixtures: hallucinated ID, probs sum 1.3, duplicate tier, Q in core). | PR-B |
| 2 | Wed–Thu | **Evidence packet v0** from existing signals: lines/implied totals (scenario priors), weather, GES/StackProfile, usage/role share, upside, receiving/QB-rush profile, availability list (Q rule applied + out/inactive starters), matchup grades labelled "season-to-date, availability-unaware", ownership where populated (say 320/629), explicit `data_gaps`. **Team-level unit adjustment** from PFF pass-block/pass-rush grades with a "starters out" fallback; per-player replacement needs the PFF↔depth-chart name+team join (collision-flagged, never guessed) — Thursday stretch goal. | PR-C |
| 3 | Thu | **Pool-constrained solve** (`optimizer/pool_solve.py`): optional `base_value_by_id` + `extra_constraints` on `_solve_single_lineup` (defaults keep old behaviour byte-identical; Chalk Anchor identity test guards it). TV base (D4). Seeded no-good cuts L1→L3. | PR-D |
| 4 | Thu | **Runners + prompts**: game-analyst and expert prompts (`PROMPT_VERSION`), file-contract runner, cache. Analysts on the top ~6–8 games by GES/stack viability only (rest get a code default, stay eligible, not backed); one expert call, 3 pools. Guardrails in prompt/schema: cite packet fields, "what this adds beyond the line", counter-scenario with probability, status basis for every availability claim, scenario-conditional sign for each player, ownership check on every beneficiary claim. | PR-E |
| 5 | Thu pm | **Wire-in**: one guarded block in `live_integration_check_dashboard.py` after old-path generation and before `save_slate_snapshot`, flag `NEW_BUILD`; snapshot `redesign` envelope (git sha, prompt version, model, clearance sha, packet refs, theses, expert output, pools, pool lineups, control ref). **Dry-run rehearsal on the saved Week 4 snapshot — plumbing only, no performance claims.** | PR-F |
| 6 | **Thu noon + Thu eve: go/no-go** | If steps 1–4 aren't green by Thursday evening, **Friday runs the old path + Q rule only** (plus whatever availability data is in). The new path never blocks Friday. | — |
| 7 | **Fri** | Final injury capture → Chris fills clearances (Full, or Limited + positive-report note) → run old path (saved first) → new path → validator → artifacts hash committed **before kickoff** → slate-brief page. Chris picks L1–L3 from the new output (control not in the played three unless Chris decides). | — |
| 8 | Sun | Re-pull statuses ~90 min before first lock, check each rostered player, late-swap from the pre-ranked same-pool list; log every manual change against the stored lineup. | — |

**Acceptance (checkable):** old path unchanged when flag off and auto-fallback on any exception (logged); no uncleared Q in any pool or lineup; every pool validator-green (or that agent falls back, logged); `bring_back_allowed=False` verified post-solve; final = exactly 3 valid lineups with a stack each and distinct core stacks; every pool member has tier+reason; scenario sets sum to 1 and carry anchor+adjusted; every thesis names a counter-scenario; artifacts round-trip through the snapshot; runtime < ~20 min warm with per-call timeouts; pytest green, no live network in tests; magnitudes labelled draft.

**Friday output pages (UI/UX):** slate map (scenario strip, angle chips, Q flag) → game thesis cards (collapsed headline; expanded scenarios/chain/redistribution/counter-scenario) → angles-portfolio matrix (BACK/HEDGE/AVOID per lineup, shared hard-avoid, overlap grid, worst-case line) → per-agent lineup cards (core + Q-excludes only; rest a count) → Q/availability panel with Sunday checklist and a "copy lineup as text in DK order" button. Cite chips with hover source/as-of. Defer: players×scenarios grids, full pool tables, touch click-reveal, DK CSV (verify DK's template first).

## 4. Grading and the A/B (performance-analytics + model-analytics)

- **Pre-register before kickoff:** primary metric = rank percentile of the best played lineup per week (+ total score). Diagnostics only: scenario Brier/log-loss **vs the line-implied anchor** (reliability tables not before ~100 game-scenarios ≈ 7 weeks), thesis-claim hits (graded only in the scenario that occurred, on opportunity numbers not fantasy points), core-tier hit rate vs base rate, exclude regret, Q decisions, angle outcomes (right scenario/wrong players vs wrong scenario).
- Scenario-hit rules fixed in advance (e.g. Shootout: total ≥ 1.15×line or both ≥ 24; Favorite pulls away: margin ≥ 14 and favorite wins; Grind: total ≤ 0.8×line and bottom-tercile pace; D: team pressure+sack rate ≥ 75th pct and pass EPA ≤ −0.15; else "none" — a high "none" rate means the set is incomplete).
- **Control:** old path (Chalk Anchor + at most one more old agent). Always-on baselines saved at generation time: chalk, projection-max, random feasible. Write-once artifacts; hash committed pre-kickoff; log divergence between generated and entered lineups.
- **Decision rules:** no winner and no drops before ~8 weeks or a pre-registered ≥ 2 SE loss; keep a component only if paired difference has the right sign in ≥ 70% of weeks and the pooled CI excludes 0; revise-not-drop when scenario layer and pool layer disagree. No ROI claims (one $1M-to-1st contest dominates variance). Week 5 is n = 1: it proves nothing statistically — say so in the output.
- Post-mortem extensions: scenario-grading table, angle outcomes, A/B block first; thesis-claim grading and pool regret list once structured claims exist. Replace the biased stack "hit = actual > projected" with the angle-conditional grade (keep as a secondary column). Add contest-type buckets to the season record.

## 5. Risks (ranked) and mitigations

1. Schedule: whole pipeline by Friday is unlikely → thin slice + Thursday go/no-go + old-path fallback.
2. `y_g` coherence constraints make solves infeasible/slow → second flag, off for Friday unless the dry run is clean.
3. LLM schema/ID/tier errors → contract layer, one retry with error text, reject item, widen/fallback.
4. Narrative over base rates / double-counting the line / TDs treated as skill / public injury news already in ownership → required guardrails in §3 step 4.
5. Cache staleness after Friday practice reports → clearance-file hash in the cache key.
6. Editing the ~900-line live script → one guarded fail-open block only.
7. Name+team join collisions for linemen → flag, never guess; fall back to team-level.
8. Contamination: no valid backtest of the LLM stages on wks 3–4 → forward-only.

## 6. Decisions needed from Chris

1. **PRD waiver/amendment:** PRD §1 (linear v1) and §12–13 (no multi-agent runtime / Monte Carlo) conflict with per-game LLM analysts + expert (simulator is excluded outright). Approve a documented amendment, with the simulator deferred?
2. **Builder roster for Week 5:** Shootout Stack, Contrarian Game, Chalk Pivot as the 3 pool agents; Chalk Anchor stays old-path control. OK?
3. **Control in the played three?** Recommended no (you hand-pick from the new output; control graded as comparison only).
4. **Dup-risk rule:** keep PRD §7 buckets as a post-filter on each agent's candidates?
5. **Fallback:** if the new path fails any acceptance check, play old-path output (with the Q rule)?
6. **Clearances:** you fill `data/overrides/q_clearances.csv` Friday before generation (via `scripts/log_q_clearance.py`)?
7. **Metric:** adopt the pre-registered primary metric in §4?
8. **Start now:** merge #54 and begin step 0 (injury capture + depth-chart widening) today?
