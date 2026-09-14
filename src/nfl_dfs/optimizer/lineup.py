"""Stage 8: ILP-based lineup construction (PRD Section 5 step 8; Section 7's rules).

**Walking-skeleton scope, per the Product Owner's approved pivot -- a working v0, not the full
Section 7 sophistication.** This module consumes `projection.blend.build_projection_pool`'s
output directly (`PlayerProjection`) and produces a small set of distinct DraftKings Classic
lineups via `pulp` (an ILP solver -- CBC, bundled with `pulp` and confirmed available in this
environment via `pulp.listSolvers(onlyAvailable=True)` before this module was written).

## What's implemented (hard constraints -- PRD Section 3 + Section 7's first rule)

- Exactly 9 players, salary sum <= $50,000 (PRD Section 3).
- Roster composition via aggregate position-count constraints, not per-slot assignment
  variables -- verified against DK's actual roster (QB, RB, RB, WR, WR, WR, TE, FLEX(RB/WR/TE),
  DST): `num_QB == 1`, `num_DST == 1`, `num_RB in {2,3}`, `num_WR in {3,4}`, `num_TE in {1,2}`,
  and `num_RB + num_WR + num_TE == 7`. This correctly encodes the single FLEX slot: the base
  roster always needs 2 RB + 3 WR + 1 TE = 6 flex-eligible players, and exactly one more of
  *some* flex-eligible position fills FLEX, for 7 total -- the count bounds allow exactly the
  three ways that 7th player can be an RB, WR, or TE, and forbid every other combination (e.g.
  4 RB is impossible since MAX_RB=3; 2 WR is impossible since MIN_WR=3). Once the roster is
  chosen, `_assign_slots` recovers which specific player occupies FLEX for display purposes --
  no slot-assignment variable was needed in the ILP itself.
- **QB + same-team pass-catcher stack (PRD Section 7's first rule):** for every team `t`,
  `sum(x_p for p in WR/TE of team t) >= sum(x_p for p in QB of team t)`. Verified reasoning:
  exactly one QB is selected roster-wide (the `num_QB == 1` constraint above), so for the one
  team `t*` that QB belongs to, this reduces to "at least 1 WR/TE from team t* is also
  selected" -- exactly PRD Section 7's rule. For every other team (RHS is 0 players selected `x`
  QB, `<=` sum over that team's QB variables, which is 0 since no QB from that team is
  rostered), the constraint is `sum(...) >= 0`, always trivially satisfied, so it never
  constrains a team that isn't the QB's own team. This is a standard clean linear formulation
  for binary variables -- no big-M term needed, unlike a naive "if QB then pass-catcher"
  implication would require.

## Diversity across the 3-lineup set (PRD Section 7: "no more than one lineup ... should share an
identical core stack")

Solved sequentially, maximizing total blended projection each time. After each solve, the
"core stack" (the selected QB's canonical_id plus every selected WR/TE on the QB's own team) is
extracted, and a no-good cut is added before the next solve:
`sum(x_p for p in core_stack) <= len(core_stack) - 1`. With binary variables this forces *at
least one* member of that exact core-stack set to change in every subsequent lineup -- it does
NOT forbid any individual player from reappearing (a non-stack RB or the DST can repeat freely
across all 3 lineups); it only forbids re-selecting the identical QB+pass-catcher combination
together. This is the standard no-good-cut technique for enumerating diverse ILP solutions.

## Explicitly OUT OF SCOPE this round (named here, not silently skipped -- per the task brief)

- **RB/DST-facing-each-other soft penalty (PRD Section 7):** needs a correlation/matchup score
  keyed off which RB's team plays which DST -- that requires schedule/opponent data joined into
  the objective, and more importantly the scaling term depends on `MatchupContext`-adjacent
  concepts (implied-total tertiles, spread strength bands) that this walking skeleton's upstream
  stages don't populate yet in a form this module can consume. Known v1 gap.
- **Salary-usage-floor flagging:** not automated. Every `Lineup` exposes `total_salary` so this
  can be eyeballed manually; no threshold/flag logic is implemented.
- **Strategy-count bounds (2-5 distinct builds) and `StackProfile`'s thesis/pivot_to labeling:**
  `StackProfile` candidate selection is itself still blocked upstream (needs target-share data
  this pipeline doesn't compute yet). For this round, "3 lineups with distinct core stacks" (the
  no-good-cut mechanism above) is the whole diversity mechanism -- there is no formal strategy
  classification, thesis label, or count-bounds check here.
- **Full game-stack favoring by `GameEnvironmentScore`:** optional, off by default. If a caller
  passes `game_environment_scores` (team -> 0-100 composite, from
  `game_environment.score.GameEnvironmentScore.composite_score`), a tiny epsilon nudge is added
  to the objective (see `_GAME_ENVIRONMENT_NUDGE_WEIGHT` below) that only breaks ties/near-ties
  in favor of higher-scoring game environments -- it is deliberately far too small to ever
  override a real blended-projection difference between two rosters. This is a soft nudge, not
  the "favor game stacks" rule from PRD Section 7 (which is specified in terms of
  `StackProfile.game_stack_viability`, not implemented here).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pulp

from nfl_dfs.projection.blend import PlayerProjection

SALARY_CAP = 50_000
ROSTER_SIZE = 9

# PRD Section 3 roster: QB, RB, RB, WR, WR, WR, TE, FLEX(RB/WR/TE), DST.
MIN_RB, MAX_RB = 2, 3
MIN_WR, MAX_WR = 3, 4
MIN_TE, MAX_TE = 1, 2
FLEX_GROUP_TOTAL = 7  # RB + WR + TE combined: 2+3+1 base flex-eligible slots + 1 FLEX slot.
FLEX_ELIGIBLE_POSITIONS = ("RB", "WR", "TE")
ROSTER_POSITIONS = ("QB", "RB", "WR", "TE", "DST")

# Deliberately tiny -- see module docstring's "Full game-stack favoring" note. Blended
# projections are on the order of 5-30 DK points per player; this weight can only ever matter
# when two candidate rosters are within a small fraction of a point of each other.
_GAME_ENVIRONMENT_NUDGE_WEIGHT = 1e-4


class LineupGenerationError(RuntimeError):
    """Raised when the optimizer cannot produce even one feasible lineup from the given pool --
    e.g. too few eligible players at some position, or every legal roster exceeds the salary
    cap. This is the module's "fails gracefully" path for a genuinely infeasible pool: no
    exception escapes from `pulp` itself, and no invalid/partial lineup is ever returned instead.
    """


@dataclass(frozen=True)
class Lineup:
    """One complete, valid DK Classic lineup: 9 players, roster-legal, under the salary cap.

    `slots` maps DK's own roster-slot labels ('QB', 'RB1', 'RB2', 'WR1', 'WR2', 'WR3', 'TE',
    'FLEX', 'DST') to the `PlayerProjection` filling that slot -- recovered post-solve by
    `_assign_slots` (see that function's docstring for why no slot-assignment ILP variable was
    needed). `players` is the same 9 players as an unordered tuple, for callers that don't need
    slot labels.
    """

    slots: dict[str, PlayerProjection]
    players: tuple[PlayerProjection, ...]
    total_salary: int
    total_projected_points: float
    # The QB + same-team WR/TE selected -- the exact set the no-good cut keys off of. Exposed so
    # callers/tests can confirm 3 generated lineups are actually distinct by this definition.
    core_stack: frozenset[str]
    core_stack_team: str
    notes: list[str] = field(default_factory=list)


def _eligible_pool(pool: list[PlayerProjection]) -> dict[str, PlayerProjection]:
    """Filter to players with a usable objective value and a known salary, deduped by
    `canonical_id`. A `blended_projection is None` player (zero vendor coverage) has no usable
    objective value -- per the task brief, excluded from the optimizer's candidate pool entirely,
    same treatment as a player DK never priced (`salary is None`, which can only happen if the
    DraftKings identity match itself failed -- see `PlayerIdentity`/ADR-0013). Positions outside
    DK's five-position roster vocabulary are also dropped defensively; none should reach this
    module in practice since `projection.blend` only ever sees DK-eligible identities.
    """
    result: dict[str, PlayerProjection] = {}
    for p in pool:
        if p.blended_projection is None or p.salary is None:
            continue
        if p.position not in ROSTER_POSITIONS:
            continue
        if p.canonical_id in result:
            continue  # defensive de-dupe; canonical_id should already be unique upstream
        result[p.canonical_id] = p
    return result


def _assign_slots(selected: list[PlayerProjection]) -> dict[str, PlayerProjection]:
    """Recover DK's own roster-slot labels for a chosen, roster-legal set of 9 players.

    No slot-assignment ILP variable was needed to select *which* players fill FLEX -- the
    aggregate RB/WR/TE counts fully determine it: whichever position has more than its base
    count (2 RB / 3 WR / 1 TE) is the one occupying FLEX, and the position-count constraints
    guarantee exactly one position has exactly one extra player. This function just does that
    bookkeeping for a legal roster; it does not re-check legality (the ILP constraints already
    guarantee it holds for anything passed in here).
    """
    qbs = [p for p in selected if p.position == "QB"]
    dsts = [p for p in selected if p.position == "DST"]
    rbs = [p for p in selected if p.position == "RB"]
    wrs = [p for p in selected if p.position == "WR"]
    tes = [p for p in selected if p.position == "TE"]

    slots: dict[str, PlayerProjection] = {"QB": qbs[0], "DST": dsts[0]}
    base_rb, base_wr, base_te = rbs[:2], wrs[:3], tes[:1]
    slots["RB1"], slots["RB2"] = base_rb[0], base_rb[1]
    slots["WR1"], slots["WR2"], slots["WR3"] = base_wr[0], base_wr[1], base_wr[2]
    slots["TE"] = base_te[0]

    if len(rbs) > 2:
        slots["FLEX"] = rbs[2]
    elif len(wrs) > 3:
        slots["FLEX"] = wrs[3]
    else:
        slots["FLEX"] = tes[1]

    return slots


def _extract_core_stack(selected: list[PlayerProjection]) -> tuple[frozenset[str], str]:
    """The QB's canonical_id plus every selected WR/TE on the QB's own team -- the set the
    no-good cut forbids re-selecting together on a later lineup. Exactly one QB always exists in
    a legal roster (the `num_QB == 1` constraint), so `qb` here is unambiguous.
    """
    qb = next(p for p in selected if p.position == "QB")
    pass_catchers_same_team = {
        p.canonical_id
        for p in selected
        if p.position in ("WR", "TE") and p.team == qb.team
    }
    return frozenset({qb.canonical_id} | pass_catchers_same_team), qb.team


def _solve_single_lineup(
    pool_by_id: dict[str, PlayerProjection],
    previous_core_stacks: list[frozenset[str]],
    game_environment_scores: dict[str, float] | None,
) -> Lineup | None:
    """One ILP solve: maximize total blended projection subject to PRD Section 3's roster/salary
    rules, Section 7's QB+pass-catcher stack rule, and a no-good cut per already-generated
    lineup's core stack. Returns `None` (never raises) when this specific solve is infeasible --
    `generate_lineups` decides what that means (first-solve infeasibility vs. cuts exhausted).
    """
    players = list(pool_by_id.values())
    ids = [p.canonical_id for p in players]

    prob = pulp.LpProblem("nfl_dfs_lineup", pulp.LpMaximize)
    x = {pid: pulp.LpVariable(f"x_{i}", cat="Binary") for i, pid in enumerate(ids)}

    def objective_term(p: PlayerProjection) -> float:
        term = p.blended_projection
        if game_environment_scores is not None:
            term += _GAME_ENVIRONMENT_NUDGE_WEIGHT * game_environment_scores.get(p.team, 0.0)
        return term

    prob += pulp.lpSum(objective_term(p) * x[p.canonical_id] for p in players)

    # Roster size and salary cap (PRD Section 3).
    prob += pulp.lpSum(x[pid] for pid in ids) == ROSTER_SIZE
    prob += pulp.lpSum(p.salary * x[p.canonical_id] for p in players) <= SALARY_CAP

    # Position-count constraints (verified against Section 3 -- see module docstring).
    by_position: dict[str, list[PlayerProjection]] = defaultdict(list)
    for p in players:
        by_position[p.position].append(p)

    def count(position: str):
        return pulp.lpSum(x[p.canonical_id] for p in by_position.get(position, []))

    prob += count("QB") == 1
    prob += count("DST") == 1
    prob += count("RB") >= MIN_RB
    prob += count("RB") <= MAX_RB
    prob += count("WR") >= MIN_WR
    prob += count("WR") <= MAX_WR
    prob += count("TE") >= MIN_TE
    prob += count("TE") <= MAX_TE
    prob += count("RB") + count("WR") + count("TE") == FLEX_GROUP_TOTAL

    # QB + same-team pass-catcher stack (PRD Section 7's first rule -- see module docstring for
    # the linearization reasoning).
    by_team: dict[str, list[PlayerProjection]] = defaultdict(list)
    for p in players:
        by_team[p.team].append(p)
    for team, team_players in by_team.items():
        qb_sum = pulp.lpSum(x[p.canonical_id] for p in team_players if p.position == "QB")
        catcher_sum = pulp.lpSum(
            x[p.canonical_id] for p in team_players if p.position in ("WR", "TE")
        )
        prob += catcher_sum >= qb_sum

    # No-good cuts -- one per already-generated lineup's core stack (see module docstring).
    for i, core_stack in enumerate(previous_core_stacks):
        members_in_pool = [pid for pid in core_stack if pid in x]
        if not members_in_pool:
            continue  # this core stack can't possibly recur if none of its players are even
            # in this pool -- an always-true constraint isn't worth adding to the model.
        prob += pulp.lpSum(x[pid] for pid in members_in_pool) <= len(core_stack) - 1, (
            f"no_good_cut_{i}"
        )

    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    if pulp.LpStatus[prob.status] != "Optimal":
        return None

    selected = [p for p in players if round(x[p.canonical_id].value()) == 1]
    core_stack, core_stack_team = _extract_core_stack(selected)
    total_salary = sum(p.salary for p in selected)
    total_points = sum(p.blended_projection for p in selected)

    return Lineup(
        slots=_assign_slots(selected),
        players=tuple(selected),
        total_salary=total_salary,
        total_projected_points=total_points,
        core_stack=core_stack,
        core_stack_team=core_stack_team,
    )


def generate_lineups(
    pool: list[PlayerProjection],
    n: int = 3,
    game_environment_scores: dict[str, float] | None = None,
) -> list[Lineup]:
    """Generate up to `n` distinct-core-stack lineups from a `build_projection_pool` output.

    Raises `LineupGenerationError` if not even one legal lineup can be built from `pool` (a pool
    too small, too expensive, or missing a mandatory position entirely -- the "infeasible" case
    called out in the task brief). If the *first* lineup succeeds but a later one can't be found
    because every remaining distinct-core-stack combination is infeasible (exhausted diversity,
    not a broken pool), generation stops early and returns the lineups already found rather than
    raising -- this is a normal outcome for a small player pool, not an error condition, and is
    recorded in that returned list's last entry's `notes` would be misleading since it's a
    property of the whole run, not one lineup, so it's surfaced via a `RuntimeWarning` instead so
    a caller monitoring stderr/warnings can still notice without every normal 3-for-3 run growing
    a spurious return-shape branch.
    """
    import warnings

    pool_by_id = _eligible_pool(pool)

    lineups: list[Lineup] = []
    previous_core_stacks: list[frozenset[str]] = []

    for i in range(n):
        lineup = _solve_single_lineup(pool_by_id, previous_core_stacks, game_environment_scores)
        if lineup is None:
            if i == 0:
                raise LineupGenerationError(
                    "No feasible lineup exists for this player pool -- check that enough "
                    "eligible players (blended_projection and salary both present) exist at "
                    "every position, and that a legal <= $50,000 roster is possible at all."
                )
            warnings.warn(
                f"Only {len(lineups)} of {n} requested lineups could be generated: every "
                "remaining distinct-core-stack combination was infeasible for this pool "
                "(diversity exhausted, not a broken pool).",
                RuntimeWarning,
                stacklevel=2,
            )
            break
        lineups.append(lineup)
        previous_core_stacks.append(lineup.core_stack)

    return lineups
