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
_Avoid_: Game analyst, sub-agent

**Game thesis**:
One game's integrated story of how it is likely to play out: the scenarios and their rough probabilities, how the units affect each other within them, where work is redistributed, and which players win in which scenario. Every claim cites the evidence packet and names its strongest counter-scenario.
_Avoid_: Game script (script is only the first link of the chain), game preview

**Pivotal question**:
A question that decides how a game plays out (for example "does Philadelphia protect its QB without its right tackle?"). Each names a measurable proxy and threshold before kickoff.
_Avoid_: Key factor, storyline

**Scenario**:
One combination of answers to a game's pivotal questions, with a rough probability. A game's scenarios are mutually exclusive and cover every case. The probability-weighted mix must still reproduce the game's betting line.
_Avoid_: Script (the flow of a game within one scenario), game type, outcome

**Angle**:
A bet on one scenario of one game (or on an individual edge within it). A lineup can back an angle, hedge it, or avoid it, and a set of lineups spreads exposure across angles on purpose. A lineup's angles may sit in different games; they are not forced to correlate.
_Avoid_: Stack (a stack is one way to express an angle), narrative

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

**Build thesis**:
An agent's stated bet for the slate: which angles it backs, avoids or hedges, the stack it builds around, and why. Distinct from a game thesis, which describes a game, not a bet.
_Avoid_: Thesis (ambiguous with game thesis), angle, stack idea

### Availability

**Questionable decision**:
The system's call on a Questionable player, made from the official practice report: full participation clears him, anything else leaves him out. Printed with its basis.
_Avoid_: Clearance (it is a decision the system makes, not a list Chris keeps)

**Override**:
Chris's instruction, when he has additional information or disagrees, to clear or bar a player against the system's call.
_Avoid_: Clearance, manual exclusion

**Opportunity**:
A skill player's claim on his team's work: his share of its carries and targets (and, later, snaps and routes). It, not depth-chart rank, says whether a player is a safe or a volatile source of points.
_Avoid_: Starter, role (too vague), usage (already means the red-zone shares)

**Vacated role**:
The share of a team's carries and targets held by a player who will not play, to be taken up by his teammates. The question an injury poses is where it goes, not who replaces him.
_Avoid_: Replacement, injury beneficiary (a beneficiary is a teammate who absorbs a vacated role)

**Floor lean**:
An agent's tilt, from -1 to 1, between valuing a player's downside and his upside when the solver values him. Zero is neutral; positive prefers steady opportunity, negative prefers ceiling.
_Avoid_: Risk setting, variance weight
