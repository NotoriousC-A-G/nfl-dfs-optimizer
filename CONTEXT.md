# NFL DFS Optimizer

Builds DraftKings Classic NFL lineups for GPP contests, then grades them against what actually happened. The aim is lineups that can finish in the top 1%, not lineups with the highest expected score.

## Language

**Edge**:
A real, reasoned advantage over how the field rosters a slate (a game environment the field underrates, a leverage spot, a role change from an injury). Not a number from a projection source.
_Avoid_: Value, signal (signal is the raw input; an edge is a conclusion drawn from it)

**Tail value**:
How much a player is worth to a lineup that has to finish near the top, driven by his upside outcome rather than his average outcome.
_Avoid_: Ceiling (one input to tail value), upside score

### The build pipeline

**Specialist**:
An analyst that assesses one element of the slate (for example game environment, matchups, usage, ownership leverage, injuries, weather, props) and reports what it found.
_Avoid_: Sub-agent, signal agent

**Expert**:
The reasoning step that reads every specialist's findings and decides how lineups should be built, stating a reason for each decision.
_Avoid_: Judge, orchestrator

**Agent**:
One lineup-building persona, defined by its sliders, that receives its own pool from the expert and produces lineups from it.
_Avoid_: Strategy, bot (the contest entries Chris plays by hand are *lineups* labelled L1/L2/L3, not agents)

**Slider**:
One axis of an agent's character, from -1 to +1 (ceiling, ownership stance, matchup, game script, stack), where 0 is neutral.
_Avoid_: Lever, weight

**Pool**:
The set of players one agent may draw from for a slate, arranged in tiers and built by the expert to fit that agent's sliders.
_Avoid_: Universe, candidate list

**Tier**:
A pool member's standing for that agent: **core** (the expert wants it used), **eligible** (allowed), or **exclude** (barred). Each assignment carries a one-line reason.
_Avoid_: Bucket, rank

**Thesis**:
An agent's stated bet for the slate: the game and stack it is built around and why.
_Avoid_: Angle, stack idea

### Availability

**Questionable clearance**:
Evidence that a Questionable player practiced Friday (full, or limited with a positive report), which lets him be rostered. Without one, a Questionable player is treated as out.
_Avoid_: Probable override, status override
