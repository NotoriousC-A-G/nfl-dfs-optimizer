# NFL DFS Optimizer — Product Requirements Document

**Owner:** Chris
**Target contests:** DraftKings NFL GPP tournaments (Classic slate)
**Build approach:** Claude Code executes, directed by a defined team of subagents (Section 10) plus Chris on architecture and design — same working pattern as the MLB DFS optimizer, formalized into named roles
**Status:** Draft for Phase 0 scoping. `GameEnvironmentScore`, `StackProfile`, and `MatchupContext` (Section 6) have draft formulas below, but each is provisional until Phase 0 (Section 11) confirms what data is actually available from each source. Treat the formulas as blockers to finalize, not details to resolve mid-build.

---

## 1. Purpose

Build a lineup construction tool for DraftKings NFL Classic GPP contests. The tool blends external projection and grading sources with an in-house game-environment and correlation model, then generates a small slate of lineups built around explicit stacking theses rather than raw point-per-dollar optimization.

The technical pipeline (Section 5) is a single linear system in v1, not the eight-agent runtime the MLB optimizer uses — that stays a later-phase option, not a v1 requirement. Separately, the *work* of building, validating, and running that pipeline is owned by a defined team of Claude Code subagents (Section 10). Those are two different things: one is the shape of the software, the other is who's responsible for what while it gets built and operated.

## 2. Scope & Contest Focus

- **Format:** DraftKings Classic NFL slate, GPP only. Cash games are explicitly out of scope for v1 — no floor-optimized or high-ownership "safe" mode.
- **Weekly contest mix:** Chris builds 3 lineups per slate and enters them across a spread of contests — a single-entry contest, a handful of 3-max, 5-max, and 20-max entry contests, and all three lineups into the Millionaire Maker (150-max entries per user, massive overall field). The same 3-lineup set has to hold up across that whole range, from small-field contests where the optimal build looks close to the chalk play, to a field the size of the Millionaire Maker where leverage and differentiation matter far more. Lineup construction (Section 7) and output (Section 8) are built around this 3-lineup set, not a larger N-lineup batch.
- **Slate types:** Main slate (Sunday 1pm window) is primary. Showdown/Captain Mode slates are out of scope for v1; the roster and correlation logic differ enough to warrant a separate build later.
- **Cadence:** Weekly, in-season. The tool needs to run a full cycle — data pull through lineup output — inside a single afternoon before lock.

## 3. Roster & Contest Mechanics

DK Classic NFL roster: QB, RB, RB, WR, WR, WR, TE, FLEX (RB/WR/TE), DST. $50,000 salary cap, nine roster spots.

Relevant mechanics the model needs to respect:
- No hard rule against RB or WR facing the opposing DST in the same lineup, but the correlation model should price that matchup as negative.
- DST scoring is volatile and swings on defensive/special teams touchdowns and sacks — treat it as a separate projection problem from offensive skill positions, not a scaled-down version of the same model.
- Late swap is available on DK; the tool should flag players in early games separately from those in the late/Sunday-night window so Chris can hold flexible spots where it makes sense.

## 4. Data Sources

| Source | Access method | What it provides |
|---|---|---|
| **PFF** | Official Developer API (developer.pff.com), CLI or direct REST calls against their OpenAPI spec, gated by Chris's PFF Pro subscription | Player grades (pass block, run block, coverage, QB under pressure), Premium Stats Pro (target share, aDOT, YAC, pressure rate), PFF's own fantasy/DFS projections |
| **RotoGrinders** | No public API — the old endpoint now returns 403. Reverse-engineer authenticated endpoints the way the MLB build did for RotoGrinders, using the Claude in Chrome extension to capture the actual network calls behind LineupHQ, the ownership grids, and NFL WeatherEdge | Ownership projections, expert rankings, weather-adjusted projections |
| **Footballguys** | No public API. Subscriber projection pages are served from authenticated URLs under `subscribers.footballguys.com/myfbg/` — the pattern is documented by other DFS hobbyist tooling. Same reverse-engineering approach as RotoGrinders | Consensus and individual-projector fantasy projections, injury/role notes |
| **DraftKings public API** | Public JSON endpoints used broadly across the DFS tooling community | Salaries, slate structure, contest payout structures, post-lock actual ownership for backtesting |
| **nflverse / nfl-data-py** | Free, open-source | Historical play-by-play, target share, red zone share, snap counts, air yards — the foundation layer for an in-house projection model rather than pure pass-through of vendor numbers |
| **Odds API** (e.g. The Odds API or similar) | Paid API, low cost | Live Vegas lines, game totals, spreads — used to derive implied team totals |
| **Weather API** | Free tier sufficient | Wind and precipitation for outdoor stadiums — matters most in the back half of the season |

**Note on reverse-engineered sources:** RotoGrinders and Footballguys both require an authenticated session. Claude cannot log in and hold that session on its own — Chris will need to either capture the session cookie through Claude in Chrome and hand it to Claude Code, or the tool needs a lightweight local auth step Chris runs himself before each weekly cycle. This gets tested in Phase 0 (Section 11), not assumed away.

**Note on PFF field-level availability:** the OpenAPI spec is public to browse before subscribing, but the exact granularity of some metrics referenced in Section 6 (e.g., pass-block efficiency by individual lineman vs. team-level only, coverage grade broken out by alignment) hasn't been confirmed against a live authenticated pull. Phase 0 confirms this before the formulas in Section 6 are finalized.

## 5. System Architecture (v1)

A single linear pipeline, in this order:

1. **Ingestion** — pull salaries/slate info from DraftKings, projections and grades from PFF, RotoGrinders, and Footballguys, historical stats from nflverse, lines from the odds API, and weather where relevant.
2. **Normalization** — reconcile player IDs across sources (DK's naming doesn't match PFF's or Footballguys' cleanly), resolve name collisions, flag players missing from any source.
3. **Projection blending** — combine vendor projections with the in-house model built on nflverse data into a single blended projection per player, with a defensible weighting rationale rather than an arbitrary average.
4. **Matchup adjustment** — apply `MatchupContext` (Section 6) to the blended projection, so trench and coverage matchups shape the number rather than sitting next to it as a footnote.
5. **Game environment scoring** — compute `GameEnvironmentScore` per game (Section 6).
6. **Correlation/stack scoring** — compute `StackProfile` per team/game combination (Section 6).
7. **Ownership/leverage layer** — use RotoGrinders ownership projections to flag high-owned chalk and identify leverage spots (strong player, low projected ownership).
8. **Lineup construction** — an ILP-based optimizer, constrained by salary cap, roster rules, and the stack theses defined in Section 7, producing a small set of distinct lineups rather than N near-duplicates.
9. **Output** — lineup list plus an exposure report showing how often each player appears across the generated set.

Simulation (Monte Carlo outcome modeling, the way the MLB build uses it) is deferred to v2. For v1, `GameEnvironmentScore` and `StackProfile` carry the correlation logic instead of full game simulation.

## 6. Core Analytical Components

The formulas below are a draft starting point, written to be concrete enough to build against. Each is subject to revision once Phase 0 confirms actual data granularity, and each needs Model Analytics Expert and Fantasy Football Expert sign-off (Section 10) before it moves from draft to implemented.

### GameEnvironmentScore
A per-game score, 0–100, computed as a weighted composite:

- **Implied team total (40%)** — derived from spread and game total, normalized against the season's implied-total distribution (z-score, not a raw point value, so it stays comparable across weeks with different scoring environments).
- **Pace (20%)** — offense's situation-neutral plays-per-game, z-scored against league average for the season to date.
- **Pass rate over expectation, PROE (20%)** — league-standard PROE metric, z-scored.
- **Weather impact (10%, outdoor games only; redistributed to the other three for indoor/dome games)** — a penalty function on wind speed and precipitation; wind above roughly 15 mph and any measurable precipitation both reduce the score, with wind the larger driver for passing volume specifically.
- **Injury/role uncertainty (10%, flag not a score)** — this one doesn't blend into the composite numerically; it's a confidence flag attached to the score (e.g., "moderate," "high uncertainty") when a starter's status is unresolved close to lock.

Output: a 0–100 score per team per game, plus the uncertainty flag. Weights above are a starting proposal for Architect and Model Analytics Expert to validate against a season or more of backtested data, not a final answer.

### StackProfile
Defines the correlation thesis for a given team/game:
- Primary stack candidates (QB + top 1–2 pass-catchers), ranked by target share and `MatchupContext` favorability, not raw season totals alone.
- Bring-back candidates (opposing pass-catcher in a game-stack build), selected the same way from the opposing roster.
- Game-stack viability score = a direct function of `GameEnvironmentScore` for both teams in the game (the higher the combined score, the more a full game-stack build is favored over a single-team stack).
- An explicit `pivot_to` field — the affirmative thesis for the stack, not just an absence of red flags. This mirrors the field added to the MLB `SlateStrategy` dataclass: a contrarian build needs a stated reason, not just constraint-based avoidance.

### MatchupContext
Player projections built purely from historical performance miss the thing that matters most in football: what happens at the point of attack this week. `MatchupContext` is a unit-vs-unit adjustment layer, computed before the projection blend, not after — so it shapes the projection rather than just tagging it with a note. Every adjustment must trace to a specific stat or grade the pipeline pulls and computes against — never a narrative judgment applied at build time with no numeric input behind it. If the data doesn't support an adjustment, no adjustment is made.

| Matchup | Primary data inputs | Draft formula | What it adjusts |
|---|---|---|---|
| **Run game** | PFF offensive-line run-block grade vs. PFF opposing front's run-defense grade; nflverse yards-before-contact and stuffed-run-rate as a cross-check | z-score differential between the two grades, mapped to a capped multiplier (proposed range 0.85x–1.15x) applied to the RB's blended carry efficiency | RB efficiency (yards per carry, explosive-run rate) |
| **Pass protection vs. pass rush** | PFF pass-block efficiency (by lineman, aggregated to team) vs. PFF team pass-rush win rate; nflverse pressure rate, sack rate, and time-to-throw as a cross-check | same z-score-differential-to-multiplier approach, applied to QB sack/pressure-adjusted efficiency, which then flows downstream into every pass-catcher's blended target value | QB sack/pressure rate, completion rate, and therefore volume and efficiency for every pass-catcher on the field |
| **Coverage** | PFF coverage grade split by scheme (man rate vs. zone rate) and by alignment (slot vs. perimeter); PFF yards-per-route-run and targets allowed, matched to the specific receiver's own alignment split | multiplier applied per receiver based on the grade gap specific to *their* alignment, not the defense's grade as a whole | Target share efficiency and catch rate for the specific pass-catchers likely to see that coverage |
| **Scheme / game flow** | nflverse pass rate over expectation (PROE), plays-per-game pace, personnel-grouping rates by game script | feeds directly into `GameEnvironmentScore`'s pace and PROE components rather than a separate multiplier | Expected play volume split between run and pass |

Open formula questions for Phase 0 / Architect + Model Analytics Expert to resolve: whether the multiplier caps (0.85x–1.15x above) are the right range, whether pass-protection and coverage adjustments should stack multiplicatively or get capped in combination, and what the fallback behavior is when PFF's alignment-level splits aren't available at the granularity assumed above.

## 7. Lineup Construction Rules

- Every lineup must include at least one QB + pass-catcher stack from the same team.
- Game stacks (QB + pass-catcher + opposing skill player) are allowed and should be favored in the highest-`GameEnvironmentScore` games.
- No more than one lineup in the generated set should share an identical core stack — the set needs to cover distinct theses, not variations on one favorite game.
- Because the same 3 lineups run in everything from a single-entry contest up to the Millionaire Maker, none of the 3 should be a pure small-field chalk build and none should be a pure max-leverage punt. The set as a whole should span that range — for example, one build closer to the strongest projected core, one or two built around a stated leverage thesis — so it performs reasonably whichever field size it lands in.
- RB/DST pairings that face each other are avoided by default but not hard-blocked; treat it as a scoring penalty, not a constraint.
- Salary usage floor: flag any lineup leaving more than a defined threshold of cap on the table unless it's an explicit punt build.
- Strategy count per slate is bounded — floor of 2, ceiling of 5 distinct builds, same bound used on the MLB side.

## 8. Outputs

- A generated set of 3 lineups by default, sized for Chris's actual weekly contest mix (single-entry, 3-max, 5-max, 20-max, and the Millionaire Maker), not a larger batch.
- An exposure report: how often each player, and each stack, appears across the set.
- A short rationale per lineup, tying it back to the `StackProfile` thesis that drove it.
- CSV export in DraftKings' bulk-upload format.

## 9. Non-Functional Requirements

- Built via Claude Code, same working pattern as the MLB optimizer, now formalized into the subagent team in Section 10.
- Runs on Chris's existing Intel Mac setup, using Claude Desktop with the Claude in Chrome extension for any session/auth bridging the reverse-engineered sources need.
- Weekly cycle must complete well ahead of Sunday lock, including time for Chris to review and adjust before submitting lineups.
- Cross-environment data bridging (Claude Code environment to the main assistant) follows the same pattern already established on the MLB side: CSV/JSON export, copy-paste, or screenshot when deeper analysis needs to move between environments.

## 10. Agent Team & Responsibilities

Rather than one undifferentiated Claude Code session doing everything, the build and ongoing operation of this tool is owned by a defined team of Claude Code subagents — each a separate, isolated context with its own system prompt, tool access, and lane of responsibility (Claude Code's native subagent mechanism, configured via `/agents`). This keeps formula review separate from implementation, and keeps implementation separate from whether a stack thesis actually holds up in an NFL game.

| Agent | Owns | Runs during |
|---|---|---|
| **Product Owner** | Scope and priorities — what's in or out of a given sprint. Resolves ambiguity by checking it against this PRD rather than guessing, and flags scope creep before it gets built. | Every sprint |
| **Architect** | System design and the formula-level specs in Section 6. Treats `GameEnvironmentScore`, `StackProfile`, and `MatchupContext` as a living spec, updated as Phase 0 confirms real data availability. | Phase 0 onward |
| **Data Integration Engineer** | Connection testing for every source in Section 4, the reverse-engineered RotoGrinders/Footballguys endpoints, player ID reconciliation, and the auth handoff. Reports back what's actually available — Section 6's inputs stay provisional until this agent confirms them. | Phase 0, then every weekly ingestion run |
| **Model Analytics Expert** | Statistical rigor of the *model itself* — the same role Dr. Marcus Webb plays on the MLB build. Checks every formula in Section 6 for arbitrary thresholds versus data-driven calibration, reviews the blended-projection weighting, and signs off before a formula moves from draft to implemented. This is a build-time, math-facing role — it evaluates whether a formula is defensible, not whether the tool is winning. | Formula review during build; revisits a formula when Performance Analytics flags drift |
| **Fantasy Football Expert** | Domain sanity-check — whether a `StackProfile` thesis or a `MatchupContext` adjustment actually holds up against how NFL games play out. Catches the gap between a formula that's mathematically defensible and one that's football-literate. | Design review during build; a pre-lock sanity pass on generated lineups each week |
| **Performance Analytics** | How the *tool* is actually performing — a distinct role from Model Analytics Expert. Tracks backtested and live results: hit rate on `MatchupContext` adjustments against what actually happened, which `StackProfile` theses cashed versus missed, ROI split out by contest type given the shared 3-lineup constraint (Section 2), and whether a specific formula's accuracy is drifting over the season. Doesn't touch the formulas directly — it surfaces what's working and what isn't, and routes findings to the Model Analytics Expert (is the math off) or the Fantasy Football Expert (is the football read off) to actually revise. | Can't run meaningfully until Phase 2–3 produce real output; ongoing weekly and season-long once live |
| **QA** | Testing — data validation (missing fields, stale pulls, ID mismatches), edge cases (bye weeks, short weeks, weather-driven late swaps), and backtesting against Chris's DK contest history before the model is trusted live. | Every sprint; pre-output validation each week |
| **UI/UX** | Output design — the exposure report, lineup rationale text, CSV export, and the Phase 3 review dashboard, plus surfacing Performance Analytics' findings somewhere Chris can actually see them week to week. Owns making the output something Chris can read and act on in a narrow pre-lock window. | Phase 2–3 |

Decision rights: the Architect owns the technical spec and the Product Owner owns scope. A formula in Section 6 needs sign-off from both the Model Analytics Expert and the Fantasy Football Expert before it moves from draft to implemented — one checks the math, the other checks the football. Once live, Performance Analytics is the one watching whether that sign-off is holding up against real results, and it's the trigger for revisiting a formula, not a standing veto over it. Where any two of these disagree, Chris makes the call rather than one agent overriding another.

## 11. Open Questions / Blockers

1. `GameEnvironmentScore`, `StackProfile`, and `MatchupContext` all have draft formulas now (Section 6), but none are finalized until Phase 0 confirms real data availability and the Model Analytics Expert / Fantasy Football Expert sign off.
2. Session/auth handling for RotoGrinders and Footballguys — needs a concrete answer (Chrome-captured cookie vs. a manual weekly auth step), tested in Phase 0.
3. Player ID reconciliation across DK, PFF, RotoGrinders, and Footballguys — no shared player-ID standard exists across these sources, so this needs its own mapping layer, owned by the Data Integration Engineer.
4. Historical backtesting data — how many seasons of DK contest history does Chris want to validate the model against, matching the MLB build's approach of analyzing full contest history before trusting the tool live.

## 12. Phased Roadmap

- **Phase 0 — Discovery:** Test connections to every source in Section 4 — auth, rate limits, and the actual fields returned. Confirm which specific PFF metrics referenced in Section 6 exist at the assumed granularity (pass-block efficiency by lineman, coverage grade by alignment, etc.). Capture and validate the RotoGrinders and Footballguys reverse-engineered endpoints via Claude in Chrome. Output is a data-availability report that Section 6's formulas get finalized against, owned by the Data Integration Engineer and reviewed by the Architect.
- **Phase 1 — Foundation:** Data ingestion for all sources, player ID reconciliation, and `GameEnvironmentScore` / `StackProfile` / `MatchupContext` finalized and implemented against Phase 0's findings.
- **Phase 2 — Optimizer wiring:** ILP lineup construction against the rules in Section 7, ownership/leverage layer, exposure reporting.
- **Phase 3 — Output & review tooling:** CSV export, lineup rationale generation, a lightweight review dashboard that includes Performance Analytics' hit-rate and ROI tracking once results start coming in.
- **Phase 4 (later, out of v1 scope):** Monte Carlo simulation layer, a multi-agent *runtime* pipeline (distinct from the subagent build/ops team in Section 10), Showdown/Captain Mode support.

## 13. Out of Scope (v1)

- Cash game / high-floor optimization
- Showdown/Captain Mode slates
- Full Monte Carlo simulation
- A multi-agent runtime pipeline (the build/ops team in Section 10 is separate from this)
- Automated bet/entry submission of any kind
