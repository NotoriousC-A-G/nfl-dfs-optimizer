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

**Evidence packet**:
Everything the numbers can say about one game: the lines, availability-adjusted unit strengths, usage shares, and measured interactions between units. It is evidence for a thesis, not a conclusion.
_Avoid_: Signal bundle, data package

**Game analyst**:
The reasoning step that reads one game's evidence packet and writes its game thesis.
_Avoid_: Specialist, sub-agent

**Game thesis**:
One game's integrated story of how it is likely to play out: the scenarios and their rough probabilities, how the units affect each other within them, where work is redistributed, and which players win in which scenario. Every claim cites the evidence packet and names its strongest counter-scenario.
_Avoid_: Game script (script is only the first link of the chain), game preview

**Interaction**:
A feedback loop between units that makes them impossible to assess in isolation, such as pressure stalling the pass game, which lets the defense stack the box against the run.
_Avoid_: Correlation, synergy

**Expert**:
The reasoning step that reads every game thesis plus the field's ownership and pricing, and decides how lineups should be built, stating a reason for each decision.
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
