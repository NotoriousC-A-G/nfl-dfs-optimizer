# Lineup-objective redesign — design brief for agent review

**Status:** DRAFT for review, 2026-10-07. Owner: Chris. Branch: `lineup-objective-redesign`.
**Target:** Week 5 generation happens **Friday 2026-10-09** (after Friday practice reports). Sunday kickoff 2026-10-11.
**Glossary:** `CONTEXT.md` at repo root (Edge, Tail value, Evidence packet, Game analyst, Game thesis, Interaction, Scenario, Angle, Expert, Agent, Slider, Pool, Tier, Build thesis, Questionable clearance). Use those terms.

## 1. Why we are redesigning (evidence, Weeks 3–4)

- Lineups the optimizer generates are poor. Week 4: all six agents scored 77–114 DK points against projections of 143–166. Chris's own hand-built L2 scored 131.64 (cash lines were ~132–138, winners 198–245). 22 entries, 0 cashed.
- Projection accuracy, stated honestly (Model Analytics review, 2026-10-07): wks 3-4 show r = 0.46 between projection and actual (R² ~ 0.21) across 307 players. That is **normal** for single-game DFS (trailing-mean vs actual on 2021-2024 history: r ~ 0.39 RB/WR, 0.38 QB, 0.28 TE). The QB/TE r ~ 0.14 comes from ~60 players each over 2 weeks (SE ~ +/-0.13) and is indistinguishable from 0.3. The top-projected decile was over-projected by ~3.6 pts (~2.8 SE, one decile chosen after looking, 2 weeks) — consistent with ordinary regression to the mean. **This is not evidence that projections are bad.** The case for the redesign rests on structure (correlated upside, edges the field misses), not on this statistic. Shrinkage of projections is a separate, cheaper fix.
- Today's objective (`src/nfl_dfs/optimizer/lineup.py::_solve_single_lineup`): maximize sum of `blended_projection` + a 1e-4 game-environment tie-break + a per-agent `objective_delta_by_id` (`agents/scoring.py`, capped at 0.5 × the pool's projection IQR per axis). The six agents (`agents/registry.py`) are projection-max lineups with small nudges, which is why they came out near-identical.
- Edge signals barely move anything: `ceiling_multiplier` populated for 98/629 players, max ≈ 1.04; ownership populated for 320/629.
- Winning scores come from one or two games blowing up (e.g. Lamb 44.3, Kyren Williams 36.7, Collins 33.8 in wk 4). Each of our lineups had exactly one 27+ player. These are anecdotes from the same weeks that shaped this design, so in-sample; the six agents are near-duplicates (effective n ~ 1-2 per week), and a ~50-pt lineup miss is ~2 sd, notable but not a verdict.
- Matchup layer gap (verified): `src/nfl_dfs/matchup/*` uses PFF season-to-date grades of individual linemen/defenders, snap-weighted over whoever has played; **nothing there knows who is inactive this week** (Chris's example: Lane Johnson out for PHI → LAR got heavy pressure).
- Weeks 3–4 operator projections had been logged against a stale snapshot (fixed, PRs #45/#52/#53). Don't trust any pre-10-07 projection numbers in `agent_results.csv` for wk 3 without checking.

## 2. Decisions already made (Chris)

1. **Questionable players are assumed OUT** unless Friday practice evidence clears them (Full, or Limited with a positive report). Game-time decisions are an avoid. No play-rate weighting. Implemented: ADR-0045, PR #54 (not yet merged). Hand-kept clearance file now; a scraper of the official NFL injury report later.
2. **Injury beneficiaries are a core input to EVERY lineup**, not a specialized lineup/agent.
3. **Edges, not projections, should drive selection.** Objective moves from "max mean projection" toward "lineup that can finish in the top 1%".
4. **Per-game integrated analysis, not per-unit specialists.** Units interact (pressure stalls the pass game → defense stacks the box → run dies; run game opens play-action). So one **game analyst per game** reads an **evidence packet** and writes a **game thesis**; units are never scored in isolation.
5. **Numbers are evidence; synthesis is reasoning.** Text-heavy inputs (injury/beat reports, articles) and cross-unit judgment go to an LLM; numeric aggregation stays in code, reproducible and testable. A deterministic baseline sits under every LLM judgment (e.g. a missing starter's unit contribution = replacement's grade shrunk toward replacement level, ADR-0011 shrinkage), and the LLM may override with a written reason. Consistent with the standing rule: surface real data to the reasoning step and let it reason transparently; don't silently encode interpretive calls in deterministic code.
6. **Step 0 = a script view per game; scenarios are the branches of each game's pivotal questions** (revised 2026-10-07: not four fixed score-shape buckets), 2–4 per game, probabilities anchored to the lines and checked so the mix reproduces the line. An **angle** = a bet on one scenario of one game. Lineups back, hedge or avoid angles on purpose.
7. **Each agent gets its own pool** (tiers: core / eligible / exclude, each with a one-line reason, plus a build thesis), built by the **expert** from the game theses to match the agent's sliders. The ILP then maximizes tail value *inside* the pool and still enforces salary/positions/Q rule. Hard sliders (e.g. `bring_back_allowed=False`) must be enforced in code, not by the expert. **Correction (QA review, verified):** today `bring_back_allowed` is only a *scoring* gate in `agents/scoring.py` (it decides whether a bring-back candidate gets an objective boost); `optimizer/lineup.py` has no bring-back constraint, so a bring-back can still be selected on projection alone. The constructor docstring's "hard on/off gates" is misleading. A real ILP constraint (and a post-solve assertion) has to be built.
8. **No forced correlation (revised 2026-10-07, Chris):** correlation is used where it pays (the one required QB + pass-catcher stack, an optional bring-back) and contradictory pairings are avoided (the analyst states the sign), but a lineup is otherwise a set of individual edges that may sit in different games, including a game expected to be low-scoring. No per-game coherence constraint.
9. **Tail value + a scenario-tree simulator (the "combo"):** a slate "world" = one scenario per game; sample worlds, score each player's outcome conditional on the scenario, estimate each candidate lineup's tail probability. Tail-weighted ILP generates candidates; the simulation ranks/selects. The sampler may slip past Friday; scenario views + angle-based pools should not.
10. **Portfolio allocation (leaning; confirm in review):** Chris plays **3 lineups** (L1–L3) into ~12 contests. Lean: top-edge angle in two lineups (constructed differently), second angle in the third, one shared avoid list across all three. Alternative: a different angle per lineup.
11. Output of the expert (pools, theses, scenarios, reasons) is **saved with the slate snapshot** so the post-mortem can grade each scenario/thesis/pool pick after the slate.
12. Forward A/B for Week 5: run the old six agents (control) alongside the new pool-built agents; grade both after Sunday. No fair backtest of the LLM expert on wks 3–4 exists inside this effort (results already seen) — validation is forward-only.

## 3. Proposed pipeline (strawman — review this)

```
Evidence packet builder (code, per game)
  lines → scenario priors; availability-adjusted unit strengths (OL/DL/CB/etc. incl. replacement grades);
  usage/role shares; ceiling/upside; QB rushing; weather; measured interactions
  (pass rate | pressure, rush efficiency | stacked box, play-action | run success, pace)
        │
Game analysts (LLM, one per game, parallel) → game thesis
  3–4 scenarios + probabilities, interaction chain per scenario, work redistribution,
  per-player conditional outcome (mean + ceiling) by scenario, counter-scenario, what would change my mind
        │
Field layer (code, slate-wide): ownership, leverage, pricing / salary lag, dup risk
        │
Expert (LLM): reads all theses + field layer → angle selection & allocation across the 3 lineups,
  per-agent pools (tiers + reasons + build thesis)
        │
Pool validator (code): per-position/salary feasibility, hard-slider checks, Q rule; widen-on-infeasible fallback
        │
ILP (existing solver) maximizing tail value inside each pool → candidate lineups
        │
[Later] scenario-tree simulation ranks candidates → final 3
        │
Snapshot + post-mortem grading (scenario hit, thesis claims, pool picks vs actual)
```

## 4. Builder agents (the sliders) — open

Existing six (`agents/registry.py`): Chalk Anchor, Game Script Architect, Matchup Purist, Arbitrageur, Explosion/Shootout, Volatility Engine. With pools, differences become structural. Strawman roster to review: keep **Chalk Anchor** as the control; evolve Explosion/Shootout → **Shootout Stack**, Arbitrageur → **Chalk Pivot**, Volatility Engine → **Ceiling Chaser**; add **Contrarian Game** (game-level leverage); fold Game Script Architect into Shootout Stack and matchup into every agent's brief. Injury beneficiary is NOT an agent. Chris has not yet reviewed this roster against the new design — reviewers should challenge it.

## 5. Existing assets to reuse

- Solver and hooks: `optimizer/lineup.py` (`generate_lineups`, `objective_delta_by_id`, core-stack no-good cuts, DST correlation, dup-risk-aware selection ADR-0035/0037, `EXCLUDED_INJURY_STATUSES`).
- Agents: `agents/constructor.py`, `registry.py`, `scoring.py`, `signal_bundle.py`, `orchestrate.py`.
- Signals: `game_environment/*` (GES, ADR-0003/0004/0017), `correlation/stack_profile.py`, `matchup/*` (ADR-0001/0005/0006/0014), `composition/player_detail.py`, ceiling (ADR-0028/0040), usage/role share (ADR-0019/0020), receiving/QB-rushing profiles (ADR-0029/0030), red-zone discount (ADR-0036), DST (ADR-0008/0009), ownership/leverage (ADR-0025/0026), dup-risk (ADR-0032–0037), props ingestion (ADR-0042; **no DK-id → canonical-id join yet**).
- LLM reasoning already shipped: `analysis/circumstance/` (injury / matchup-extreme / depth-chart detectors on one engine with a token cache) — a precedent for LLM-with-cache.
- Persistence/grading: `storage/slate_snapshot_store.py` (snapshot now sorted by embedded timestamp), `tracking/postmortem/*` (stack-thesis review, process grade, ceiling patterns, per-player context), ResultsDB (historical contest results/ownership, ADR-0023/0024).
- Data gaps: live availability for OL/DL/CB (nflverse injuries lag; RotoGrinders Situation Room is fantasy positions and `PART` = body part, not practice); pbp loaded with `include_participation=False` (box counts need it); PFF backup grades are thin-sample; Friday practice participation has no live source.

## 6. Constraints and ground rules

- Python env `.venv/bin/python`; tests `.venv/bin/pytest -q` (currently 1120 passing on the PR #54 branch). Live scripts are not part of pytest.
- **`main` is production; all work on branches via PR** (CLAUDE.md). Live scripts run only from `main`.
- Disclosed-draft convention: new magnitudes are labelled "draft, not backtested" unless validated (ADR-0019/0020).
- Time: Friday generation. Anything that cannot be validated or safely fall back by then must be staged behind the old path.
- No fabricated precision: scenario probabilities are uncalibrated reasoned inputs; they need anchors and post-slate grading.

## 7. Questions for reviewers

Each reviewer: answer the questions in your own prompt, challenge anything in §2–§4 you think is wrong or missing, and be specific (file paths, formulas, numbers, cuts). Say plainly what you would NOT build by Friday.
