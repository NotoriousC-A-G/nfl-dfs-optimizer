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

PROMPT_VERSION = "expert-v4"


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
You are the lineup-construction EXPERT. You have the game theses (each game's pivotal questions, scenarios and player outcomes) and the
field layer (ownership, leverage). For each pool agent below, build a POOL: every player on the slate arranged by how much confidence the
agent's stand gives him, plus a build thesis saying which angles it backs. The tiers are GRADED CONFIDENCE, not a fence:
  core     -- the players the stand is built on; you want them used (4-16 players).
  eligible -- good fits for the stand, used freely.
  reach    -- allowed at a value haircut: a salary or position fill, or a deliberate SHOT (for example a player in a game the market expects to
              be quiet that still has a real chance of a shootout). A lineup may take one, but should not lean on him and he should not be in
              every lineup. Name a reach player with a reason when you mean him as a shot.
  exclude  -- barred. Only with a stated reason: an avoid pair, a branch you are betting against, a chalk player you refuse.

RULES (machine-checked; a violation sends your answer back for one retry):
1. Give each agent EITHER a pool OR {"agent_id": ..., "unavailable": true, "reason": "..."}. Unavailable is legitimate when the slate has no
   fit for that agent's angle (the brief says when) -- do NOT stretch an agent over a slate that does not suit it.
2. THE STAND MUST BE NARROW. Use default_tier "reach" for the open remainder of the slate, then rank the players the stand is about: core 4-16
   players; core + eligible must be at most 45% of the players listed (reach and exclude do not count toward that). Use group_tiers (team /
   position / game_id selectors) for broad strokes and overrides (player_id) for named players. Later entries win over earlier ones.
3. POOLS MUST DIFFER. At most TWO agents may back the same game; the agents' core tiers should not be near-duplicates; each agent backs
   different branches. backs/avoids/hedges are refs of the form "GAME:branch_id" copied from the BRANCH lines below.
4. Every core and every exclude assignment (group tier or override) carries a one-line reason citing a thesis branch or the field layer. A reach
   assignment you mean as a shot carries a reason too.
5. Never put a player with an injury status in core; the engine strips them and rejects an unavailable core player. Injury BENEFICIARIES
   (every player marked BENEFICIARY below, and any the theses name as inheriting a vacated role) are a core input to EVERY pool where they fit
   -- unless the field already owns them. Tier each one core or eligible in the pools where he fits and give a reason; do NOT leave a
   beneficiary in the open remainder (reach), which is only for shots and fills.
6. Hard sliders (bring-back rules) are enforced in code from each agent's spec; you cannot and need not set them.
7. Do not build pools out of projection alone: "tail value" is just a calibrated scaling of the projection. The edge is in the theses, role,
   game state and ownership. A game with no thesis is backed by nobody but its players stay eligible if they fit.
8. min_core (1-8) is how many core players a lineup must use. stack_anchor lists the canonical_ids the agent's stack is built around.
9. portfolio_notes: two or three sentences on how the agents differ from each other.

OUTPUT: one JSON object, no markdown fences:
{
 "agents": [
  {"agent_id": "shootout_stack",
   "build_thesis": {"backs": ["GAME:b0"], "avoids": [], "hedges": [], "stack_anchor": ["<canonical_id>"], "reason": "..."},
   "default_tier": "reach",
   "group_tiers": [{"tier": "core", "reason": "...", "team": "XXX", "position": "QB", "game_id": null}],
   "overrides": [{"player_id": "<canonical_id>", "tier": "core", "reason": "..."}],
   "min_core": 4},
  {"agent_id": "short_field", "unavailable": true, "reason": "..."}
 ],
 "portfolio_notes": "..."
}
"""


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
        "PLAYERS (the only ids you may name):", render_universe(packets),
    ]
    if retry_errors:
        parts += ["", "YOUR PREVIOUS ANSWER WAS REJECTED. Fix every problem below and return the corrected full JSON:", *[f"- {e}" for e in retry_errors]]
    return "\n".join(parts)
