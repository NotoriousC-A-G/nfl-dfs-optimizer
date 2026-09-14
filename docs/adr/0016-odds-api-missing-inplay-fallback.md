# ADR-0016: Odds API missing-team fallback — cached pre-kickoff lines for in-play games

**Status:** Accepted (draft spec), pending Model Analytics Expert confirmation; direction for Data Integration Engineer's next Odds API ingestion pass
**Date:** 2026-09-13
**Owner:** Architect
**Related:** ADR-0003 (`docs/adr/0003-game-environment-score-scale-and-baselines.md`, "all 32 teams" population statement — not rewritten, addendum added), PRD Section 6 (`GameEnvironmentScore` implied-total component), Data Integration Engineer's live Odds API ingestion test

## Context

The Data Integration Engineer's live Odds API test returned only 28 of 32 teams. Root cause, confirmed by the Data Integration Engineer's own testing, not a data-quality bug to route around: the Odds API drops markets for games already in progress at pull time entirely — it doesn't return a stale/suspended pre-game line, it returns nothing for that game at all. Any pull that happens to catch even one game already underway will always be short exactly the teams in that game, structurally, not intermittently.

This breaks ADR-0003's stated population for `GameEnvironmentScore`'s implied-total z-score — "all 32 teams," implicitly assumed live-at-pull-time — whenever a pull runs after any game that week has kicked off. Since a full slate spans Thursday night through Monday night, *any* pull timed to catch the latest lines for Sunday's late games will, by construction, be running after the early-window games have already started.

## Decision

**Cache every pre-kickoff line pulled during the week; fall back to the most recent cached value for any team missing from a live pull.**

```
for each team, each week:
    if team appears in the current live pull: use that value, and cache it as this team's latest pre-kickoff line
    elif a cached pre-kickoff line exists for this team from an earlier pull this week: use the cached value
    else: mark this team's implied-total input as unavailable (do not substitute a league-average or any other imputed value)
```

This works because the ingestion layer already needs to pull periodically through the week to catch line movement (a Tuesday opening line and a Sunday-morning line for the same game are both real, useful signal, not just the final one) — a team playing in an early window still had a perfectly real pre-game line available from every pull earlier that week, right up until its own game kicked off. The fallback isn't inventing data; it's using the most recent real pre-game number for a team whose game has simply started before this particular pull ran.

**ADR-0003's population statement is updated, not silently reinterpreted:** "all 32 teams" now explicitly means **"all 32 teams' most recently available pre-kickoff line — live if the game hasn't started at pull time, else the most recent cached pre-kickoff value from earlier in the week."** This is a real, stated change to what "the population" means operationally, not a reinterpretation left implicit — exactly the kind of thing ADR-0003 itself was written to make precise rather than ambiguous.

**Freshness requirement for the Data Integration Engineer's caching mechanism:** ingestion needs to run and cache **at least once per day during the week**, not just once right before lock — a single once-a-week pull would mean "most recent cached value" could be up to six days stale for a team whose line moved meaningfully since. A minimum cadence of once daily, plus one additional pull as close to lock as practical (to catch late-week line movement for the not-yet-started games), keeps the cached fallback close in spirit to a live value without requiring continuous polling.

**Third tier — flagged unavailable, never imputed.** If a team has no cached pre-kickoff value at all for the current week (a pipeline outage prevented every earlier pull, or ingestion is being run for the first time mid-week with no history yet), that team's implied-total z-score input is explicitly marked unavailable — surfaced the same way `GameEnvironmentScore`'s existing injury/role uncertainty flag surfaces missing information, not silently defaulted to a league-average value. Spreads and totals are too team-and-matchup-specific to reasonably impute; an unavailable flag is more honest than a plausible-looking but fabricated number.

**Incidental clarification, not a new decision:** ADR-0003's "all 32 teams" always implicitly meant all teams *with a game that week* — a team on a bye has no line to pull, live or cached, and isn't part of that week's population at all. Worth stating plainly here since this ADR is already revisiting the population wording, even though it isn't the gap the Data Integration Engineer's test actually found.

## Alternatives considered

- **Treat a missing team as an outright gap and exclude it from that week's z-score population** (i.e., z-score against 31 teams that week instead of imputing anything). Rejected as the default — the missing team almost always has a perfectly good cached pre-kickoff value available, so falling back to real cached data is strictly better than shrinking the population when the information already exists; this stays as the fallback-of-last-resort (tier three above) only when no cached value exists at all.
- **Impute a missing team's implied total from the league average or from its own season-to-date scoring rate.** Rejected — a fabricated number dressed up as real data is worse than an explicit "unavailable" flag; the whole point of the injury/role uncertainty flag elsewhere in `GameEnvironmentScore` is to surface exactly this kind of gap rather than paper over it.
- **Require ingestion to run continuously (e.g., hourly) rather than daily-plus-pre-lock.** Rejected as unnecessary for v1 — daily-plus-pre-lock keeps cached staleness bounded to about a day, which is tight enough given lines typically move gradually across a week rather than needing hourly resolution; more frequent polling is a reasonable v1.1 refinement if backtesting shows staleness is a real problem, not a v1 requirement.

## Consequences

- The Data Integration Engineer's next Odds API ingestion pass needs a persistent per-team-week cache of the most recent pre-kickoff pull (spread and total), updated on at least a daily cadence through the week, plus the three-tier lookup logic above at `GameEnvironmentScore` computation time.
- ADR-0003 is not rewritten in place — it gets a short addendum pointing to this ADR's updated population statement, same pattern as its existing ADR-0011 shrinkage addendum.
- This is a real operational requirement (periodic ingestion runs, not a single pre-lock pull), which should be reflected in whatever scheduling/orchestration the weekly cycle uses — flagged here as a Data Integration Engineer scope note, not designed further in this ADR.
- Flagged for a lightweight Model Analytics Expert look (not blocking): whether staleness of a cached line (up to ~24 hours old under the daily-cadence minimum) meaningfully degrades the z-score's validity relative to a true live line — a backtesting question once live data exists, not resolved here.
