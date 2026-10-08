"""The expert's prompt: reads every game thesis plus the field layer and builds each pool agent's pool.

`PROMPT_VERSION` is part of the cache key. The rules encode what the real-slate rehearsals and the reviews
showed: pools must be NARROW and DIFFERENT (broad pools converge every agent on the same highest-value
players), every assignment carries a reason, and the hard sliders are not the model's to set.
"""

from __future__ import annotations

from nfl_dfs.build.agents import POOL_AGENTS, PoolAgentSpec
from nfl_dfs.build.evidence.contracts import EvidencePacket
from nfl_dfs.build.pool.contracts import PlayerRef
from nfl_dfs.build.thesis.contracts import GameThesis

PROMPT_VERSION = "expert-v7"


def branch_refs(theses: dict[str, GameThesis]) -> dict[str, float]:
    """`"GAME:branch_id" -> probability` for every branch of every validated thesis."""
    return {f"{gid}:{b.branch_id}": b.prob for gid, t in theses.items() for b in t.branches}


def render_theses(theses: dict[str, GameThesis]) -> str:
    out = []
    for gid in sorted(theses):
        t = theses[gid]
        out.append(f"### {gid} -- {t.headline}")
        for q in t.questions:
            out.append(f"- {q.question_id} ({q.phase}): {q.text}  [{q.metric} {q.direction} {q.threshold} on {q.source_field}]")
        for b in t.branches:
            who = "; ".join(f"{o.canonical_id} x{o.mean_mult}/{o.q90_mult}" for o in b.player_outcomes[:8])
            out.append(f"- BRANCH {gid}:{b.branch_id}  p={b.prob:.2f}  {b.description}  chain: {' -> '.join(b.chain)}  outcomes: {who or 'none named'}")
        for bt in t.battles:
            lean = f" -> leans {gid}:{bt.leans_branch}" if bt.leans_branch else ""
            out.append(f"- BATTLE ({bt.conviction} conviction){lean}: {bt.title} -- CALL: {bt.call} -- CONSEQUENCE: {bt.consequence}")
        out.append(f"- counter-branch: {gid}:{t.counter_branch_id}; would change my mind: {' | '.join(t.would_change_mind)}")
        for c in t.claims[:6]:
            out.append(f"- claim: {c.text}")
        for s in t.pair_signs:
            if s.sign == "negative":
                out.append(f"- AVOID PAIR ({s.strength}): {s.player_a} + {s.player_b}: {s.reason}")
        out.append("")
    return "\n".join(out)


def render_field_layer(packets: dict[str, EvidencePacket], universe: list[PlayerRef]) -> str:
    out = ["| game | favorite | spread | total | env score (away/home) | slate window |", "|---|---|---|---|---|---|"]
    for gid in sorted(packets):
        p = packets[gid]
        out.append(f"| {gid} | {p.lines.favorite} | {p.lines.abs_spread:.1f} | {p.lines.total} | {p.teams[p.away].game_environment_score} / {p.teams[p.home].game_environment_score} | {p.slate_window} |")
    own = sorted(((pl.ownership_pct, pl) for p in packets.values() for pl in p.players if pl.ownership_pct is not None), key=lambda x: -x[0])[:30]
    out += ["", "Highest projected ownership (the chalk): " + "; ".join(f"{pl.name} {pl.team} {pl.position} {o:.1f}%" for o, pl in own)]
    lev = [pl for p in packets.values() for pl in p.players if pl.is_leverage]
    out.append("Flagged leverage plays: " + ("; ".join(f"{pl.name} {pl.team} {pl.position} ({pl.ownership_pct}% vs baseline {pl.ownership_vs_baseline})" for pl in lev) or "none"))
    return "\n".join(out)


def _v(x) -> str:
    if x is None or x == "":
        return "n/a"
    return f"{x:.3f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def render_universe(packets: dict[str, EvidencePacket]) -> str:
    """One labelled line per player (no table: a model can misalign empty/None cells)."""
    out = []
    for gid in sorted(packets):
        for p in packets[gid].players:
            out.append(
                f"- {p.canonical_id} | {p.name} | {p.team} {p.position} | game {gid} | salary {_v(p.salary)} | proj {_v(p.projection)} | "
                f"own% {_v(p.ownership_pct)} | chalk {'yes' if p.is_chalk else 'no'} | leverage {'yes' if p.is_leverage else 'no'} | "
                f"status {_v(p.status_after_q_pass or p.injury_status)} | last-4 touch share {_v(p.touch_share_l4)} | "
                f"expected carry share {_v(p.carry_share_expected)} / target share {_v(p.target_share_expected)}"
                + (f" | BENEFICIARY: {p.opportunity_note}" if p.opportunity_note else "")
            )
    return "\n".join(out)


def _brief(a: PoolAgentSpec) -> str:
    hard = []
    if a.rules.forbid_pass_catcher_bring_back:
        hard.append("NO WR/TE bring-backs (hard, enforced in code)")
    if a.rules.forbid_rb_bring_back:
        hard.append("NO RB bring-backs (hard, enforced in code)")
    return f"- **{a.agent_id}** ({a.display_name}): {a.brief}" + (f"  HARD: {'; '.join(hard)}." if hard else "")


_RULES = """\
You are the lineup-construction EXPERT. You have the game theses (each game's pivotal questions, branches, player outcomes and the analysts'
BATTLES with calls and convictions), the field layer (ownership, leverage) and the slate's position economics. For each agent below you design
how it builds lineups. A lineup is NOT one game's script: it is one set of beliefs across the slate, built in three steps --
  1. its BETS: one to three groups of players whose outcomes move together because the same thing drives them (a QB with his receivers in one
     game; an RB with his own defense in another; the opposing offenses of a shootout; two receivers of a pass-heavy offense). Bets need not
     share a game, and games are independent: one going a way says nothing about another.
  2. its VIEWS: for each game the agent has an opinion on, the ONE branch ("GAME:branch_id") it believes in. Where a view says a game will beat
     field expectations the agent grabs pieces of it; where it says a game or a role will fall short, the agent avoids it. A game with no view
     is priced at the full probability-weighted mix.
  3. the FILL: everything else, by value, at the agent's spend plan.
Each agent has 1-3 VARIATIONS (a variation = its bets + its views); one lineup is built per variation, in the order you give them. The engine
DERIVES each variation's tiers from its bets and views, so use default_tier "derived" and treat your group_tiers and overrides as ADJUSTMENTS
with reasons: promote a player the arithmetic misses (a battle call, a role the numbers do not show), demote one it over-credits, exclude one you
refuse. Tiers are GRADED CONFIDENCE, not a fence:
  core     -- on the agent's bets (required in the lineup), or a teammate on a bet's team whom the view lifts.
  eligible -- good fits: players a view lifts or leaves neutral, beneficiaries, and every defense (a defense is priced on its merits, never a fill).
  reach    -- allowed at a value haircut: a fill, or a deliberate SHOT (for example a player in a game the market expects to be quiet that still has
              a real chance of a shootout). A lineup may take one but should not lean on him. Name a reach player with a reason when you mean him as a shot.
  exclude  -- barred. Only with a stated reason: an avoid pair, a branch you are betting against, a chalk player you refuse.
SPEND PLAN: look at the position economics below and decide where each agent pays up and where it takes value. If RB has many cheap
players with real roles, "value" at RB frees salary to "pay" at WR and TE; a thin RB pool says the opposite. Defenses are the cheapest slot and
$200-500 more can matter a lot -- the solver weighs that against the rest of the lineup, so do not mark DST "value" without a reason.

RULES (machine-checked; a violation sends your answer back for one retry):
1. Give each agent EITHER a design OR {"agent_id": ..., "unavailable": true, "reason": "..."}. Unavailable is legitimate when the slate has no
   fit for that agent's angle (the brief says when) -- do NOT stretch an agent over a slate that does not suit it.
2. Use default_tier "derived" (recommended), or "reach" if you want to tier the whole slate yourself. Explicit core, if you give it, is at most 16
   players (4-16 when you do not use "derived"). Explicit core + eligible is best kept well under about 45% of the players listed -- a guide,
   not a limit. Use group_tiers (team / position / game_id selectors) for broad strokes and overrides (player_id) for named players.
3. BUILD ON THE ANALYSTS' CALLS. An agent's variations rest on calls you believe in, ranked by conviction; say in the build thesis reason
   which battles they rest on. Prefer high-conviction calls, and let the agents rest on different ones. At most TWO agents may place a pass-volume
   or shootout bet in the same game. At most 3 variations per agent, at most 3 bets per variation, at most ONE view per game in a variation.
   Views, avoids and hedges are refs "GAME:branch_id" copied from the BRANCH lines below.
4. Every core and every exclude assignment (group tier or override) carries a one-line reason citing a thesis branch or the field layer. A
   reach assignment you mean as a shot carries a reason too. Every bet carries a mechanism (pass_volume | shootout | lead_protect | pressure |
   other) and a note saying why those players move together.
5. A pass_volume bet must be a QB plus at least one of his WR/TE; other bets need two or more players. Never put an unavailable player in a bet.
   Injury BENEFICIARIES (marked BENEFICIARY below) are a core input to every design where they fit -- unless the field already owns them; with
   default_tier "derived" the engine already tiers them eligible or core.
6. Hard sliders (bring-back rules) are enforced in code from each agent's spec; you cannot and need not set them.
7. Do not build from projection alone: tail value is a calibrated scaling of the projection. The edge is in the calls, roles, game states and ownership.
8. min_core (1-8) is how many core players a lineup must use (bets are required in the lineup in any case).
9. portfolio_notes: two or three sentences on how the agents differ from each other.

OUTPUT: one JSON object, no markdown fences:
{
 "agents": [
  {"agent_id": "shootout_stack",
   "build_thesis": {"avoids": [], "hedges": [], "reason": "..."},
   "variations": [
     {"views": ["GAME:b0", "GAME2:b1"],
      "bets": [{"players": ["<qb id>", "<wr id>", "<te id>"], "mechanism": "pass_volume", "note": "..."},
               {"players": ["<rb id>", "<dst id>"], "mechanism": "lead_protect", "note": "..."}],
      "note": "..."}
   ],
   "spend_plan": {"RB": "value", "WR": "pay", "TE": "pay"},
   "default_tier": "derived",
   "group_tiers": [{"tier": "core", "reason": "...", "team": "XXX", "position": "QB", "game_id": null}],
   "overrides": [{"player_id": "<canonical_id>", "tier": "core", "reason": "..."}],
   "min_core": 3},
  {"agent_id": "short_field", "unavailable": true, "reason": "..."}
 ],
 "portfolio_notes": "..."
}
"""


def render_position_economics(packets: dict[str, EvidencePacket]) -> str:
    """Per position, how deep the slate is in value (projection per $1K) -- the evidence for each agent's spend plan."""
    rows = {}
    for pk in packets.values():
        for p in pk.players:
            if p.salary and p.projection and not (p.status_after_q_pass or p.injury_status):
                rows.setdefault(p.position, []).append((p.projection / (p.salary / 1000.0), p))
    out = []
    for pos in ("QB", "RB", "WR", "TE", "DST"):
        items = sorted(rows.get(pos, []), key=lambda x: -x[0])
        if not items:
            continue
        vals = sorted(v for v, _ in items)
        median = vals[len(vals) // 2]
        deep = sum(1 for v, p in items if v >= 3.0 and p.salary <= 5500)
        best = "; ".join(f"{p.name} ${p.salary:,} {p.projection:.1f}" for _, p in items[:5])
        out.append(f"- {pos}: {len(items)} playable; median {median:.2f} pts/$1K; {deep} at 3.0+ pts/$1K under $5,500; best value: {best}")
    return "\n".join(out)


def expert_prompt(
    theses: dict[str, GameThesis], packets: dict[str, EvidencePacket], agents: tuple[PoolAgentSpec, ...] = POOL_AGENTS,
    *, retry_errors: list[str] | None = None,
) -> str:
    refs = branch_refs(theses)
    parts = [
        f"[prompt {PROMPT_VERSION}]", _RULES,
        "AGENTS TO BUILD POOLS FOR:", *[_brief(a) for a in agents], "",
        f"GAME THESES ({len(theses)} of {len(packets)} games analyzed):", render_theses(theses),
        f"VALID BRANCH REFS ({len(refs)}): " + " ; ".join(sorted(refs)), "",
        "FIELD LAYER:", render_field_layer(packets, []), "",
        "POSITION ECONOMICS (healthy players only; value = projection per $1K of salary):", render_position_economics(packets), "",
        "PLAYERS (the only ids you may name):", render_universe(packets),
    ]
    if retry_errors:
        parts += ["", "YOUR PREVIOUS ANSWER WAS REJECTED. Fix every problem below and return the corrected full JSON:", *[f"- {e}" for e in retry_errors]]
    return "\n".join(parts)
