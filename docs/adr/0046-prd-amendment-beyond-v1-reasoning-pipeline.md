# ADR-0046: PRD amendment — moving beyond v1: a reasoning-based multi-agent runtime pipeline

**Status:** Accepted (scope approved by Chris, 2026-10-07; implementation staged per `docs/design/lineup-objective-redesign-plan.md`)
**Date:** 2026-10-07
**Owner:** Chris
**Amends:** PRD §1 (v1 is a "single linear system"), §5 (linear pipeline; simulation deferred), §12 (Phase 4), §13 (Out of Scope)
**Related:** ADR-0035/0037 (dup-risk-aware generation), ADR-0040 (agent levers), ADR-0041 (postmortem), ADR-0045 (Q players),
`docs/design/lineup-objective-redesign-brief.md`, `CONTEXT.md`

## Context

PRD §1 and §5 define v1 as a single linear pipeline, and §12 Phase 4 / §13 list a multi-agent *runtime* pipeline and
Monte Carlo simulation as out of scope. The existing NflAgentConstructor (six parameter-vector agents over one ILP) and
the LLM circumstance layer (`analysis/circumstance/`) already stretched that line, and weeks 2–4 showed the six agents
are near-duplicates that lose to Chris's own hand-built lineups (best generated vs best hand-picked: wk3 134.8 vs 156.2,
wk4 114.0 vs 131.6; the five lever agents trailed plain projection-max in both). The product owner's review of the
redesign flagged that the proposed per-game analysts + expert are, as written, exactly what §13 excludes, and asked for an
explicit amendment instead of a silent departure. Chris's decision: "We're moving beyond v1."

## Decision

1. **A reasoning-based multi-agent runtime pipeline is in scope** (pulled forward from Phase 4): per-game *game analysts*
   (LLM) produce *game theses* with scenarios from code-built *evidence packets*; an *expert* (LLM) builds per-agent
   *pools*; code validates pools; the ILP builds lineups inside the pools; everything is persisted for grading. Terms:
   `CONTEXT.md`.
2. **Six builder agents remain** (Chris, 2026-10-07), now differentiated structurally by pool, not by small objective nudges.
3. **Numbers stay in code; judgment is reasoned and transparent.** Numeric aggregation is deterministic and testable;
   LLM steps see real evidence, state reasons, cite packet fields, and every output is saved. A deterministic baseline sits
   under each LLM judgment and an LLM override carries a written reason.
4. **Monte Carlo / scenario-tree simulation stays deferred.** Pull-in criterion (revised 2026-10-07 after model-analytics review):
   a pre-registered minimum of ~100 graded pivotal questions with a favorable paired Brier difference against their
   line/reference anchors (two slates give only ~22 questions and cannot be informative), plus Chris's sign-off. A
   lightweight Monte Carlo *over branch vectors* for rescoring candidate lineups is in scope; the full scenario-tree
   simulator is not. A deterministic proxy covers v2's first cut.
5. **Unchanged:** GPP-only, DK Classic main slate; no Showdown; no automated entry submission; PRD §7 construction rules
   (QB + pass-catcher stack in each lineup, no two final lineups with the same core stack, dup-risk buckets — the buckets
   now applied as a post-filter on each agent's own candidates); Chris plays up to three lineups (L1–L3) that he chooses
   from any agent's output, with provenance logged.
6. **Validation is forward-only and pre-registered** (no valid backtest of the LLM stages exists): one primary metric, saved
   write-once artifacts, no winner declared before ~8 slates (performance-analytics review).
7. Model Analytics and Fantasy Football Expert sign-off remain required before any *formula* leaves "draft, not
   backtested"; the new tail-value formula ships as draft.

## Consequences

- PRD §1, §5, §12, §13 carry amendment notes pointing here; the v1 text is otherwise left intact as history.
- The pipeline is new code behind a flag until validated; `main` stays the only place live runs happen (CLAUDE.md).
- **No silent fallback (Chris, 2026-10-07):** if a stage cannot produce a lineup the run reports which stage and why, and the issue is diagnosed and fixed — a projection-max substitute would hide the failure. Partial artifacts are saved to support diagnosis.
