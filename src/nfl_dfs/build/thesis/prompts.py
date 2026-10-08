"""The game analyst's prompt (`PROMPT_VERSION` is part of every cache key, so any edit here invalidates
cached theses by design). Built from the evidence packet plus code-computed reference tables, so the model
can pick thresholds with a known base rate and never has to do probability arithmetic.

The rules below are the guardrails the football and model-analytics reviews required; the validator
(`validate.py`) enforces each one deterministically, and a violation is sent back for one retry.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

from nfl_dfs.build.evidence.anchors import base_rate, lead_anchor
from nfl_dfs.build.evidence.contracts import EvidencePacket, packet_keys
from nfl_dfs.build.thesis.contracts import MULTIPLIER_BINS, PHASES, PROXY_METRICS

PROMPT_VERSION = "analyst-v4"

DISTRIBUTION_METRICS = (
    "sack_rate", "qb_hit_rate", "explosive_pass_rate", "pass_epa", "rush_epa", "pass_rate_over_expected",
    "pass_rate_over_expected_leading", "pace_seconds_per_play", "total_plays", "rush_attempts_leading", "combined_pass_attempts",
)
_PERCENTILES = (50, 60, 70, 75, 80, 90)
# A pressure/collapse question must clear the 60th league percentile AFTER rounding; offering the 60th itself
# bounced a model that followed the menu exactly (rehearsal, 2026-10-07). Start these higher, with margin.
_COLLAPSE_METRICS = ("sack_rate", "qb_hit_rate")
_COLLAPSE_PERCENTILES = (70, 75, 80, 90)


def _sig(x: float) -> float:
    return float(f"{x:.3g}")


def anchor_menu(packet: EvidencePacket, league_value_fn: Callable[[str], np.ndarray]) -> str:
    """Per metric: thresholds at league percentiles with the base rate of a single team-game landing on the
    YES side. The model picks a listed threshold, so its anchor is known; the parser recomputes it anyway."""
    lines = ["| metric | threshold | P(YES) if direction=gte | P(YES) if direction=lte |", "|---|---|---|---|"]
    for metric in DISTRIBUTION_METRICS:
        vals = league_value_fn(metric)
        if len(vals) == 0:
            continue
        seen = set()
        for pct in (_COLLAPSE_PERCENTILES if metric in _COLLAPSE_METRICS else _PERCENTILES):
            t = _sig(float(np.quantile(vals, pct / 100)))
            if t in seen:
                continue
            seen.add(t)
            gte, lte = base_rate(vals, t, "gte"), base_rate(vals, t, "lte")
            lines.append(f"| {metric} | {t} | {gte:.2f} | {lte:.2f} |")
    spreads = {packet.lines.favorite: packet.lines.abs_spread}
    other = packet.away if packet.lines.favorite == packet.home else packet.home
    spreads[other] = -packet.lines.abs_spread
    for team in (packet.away, packet.home):
        for metric in ("lead_at_q3_start", "lead_at_q4"):
            p = lead_anchor(metric, spreads[team])
            lines.append(f"| {metric} (source_field units.{team}.{metric}, threshold 0.5) | 0.5 | {p:.2f} (team led) | {1 - p:.2f} (team did not lead) |")
    return "\n".join(lines)


def _v(x) -> str:
    """Explicit text for a value: 'n/a' for missing, never an empty cell or a bare None a model can misalign."""
    if x is None or x == "":
        return "n/a"
    return f"{x:.3f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def render_packet(packet: EvidencePacket) -> str:
    ln = packet.lines
    out = [
        f"# {packet.away} @ {packet.home}  (season {packet.season}, week {packet.week}, slate window {packet.slate_window})",
        f"Lines: {ln.favorite} favored by {ln.abs_spread:.1f}; total {ln.total}; implied {packet.home} {ln.implied_home} / {packet.away} {ln.implied_away}; "
        f"favorite win probability {ln.favorite_win_probability:.2f} (code-computed).",
        "",
        "## Data gaps (what you do NOT have -- never reason as if you did)",
        *[f"- {g}" for g in packet.data_gaps],
        "",
    ]
    for team, te in packet.teams.items():
        out += [f"## {team} (vs {te.opponent}): env score {te.game_environment_score}, stack viability {te.single_team_viability}, script {te.game_script_stance} ({te.game_script_intensity}), implied {te.implied_total}"]
        for group in (te.metrics, te.def_metrics):
            for k, mv in group.items():
                out.append(f"- units.{team}.{k} = {mv.value if mv.value is None else round(mv.value, 4)}  (n={mv.n}, league percentile {mv.league_percentile if mv.league_percentile is None else round(mv.league_percentile, 2)})")
        out.append("")
    out += ["## Players listed (use these canonical_ids; these are the only players you may name). One line per player; \"n/a\" = no value."]
    for p in packet.players:
        out.append(
            f"- {p.canonical_id} | {p.name} | {p.team} {p.position} | salary {_v(p.salary)} | proj {_v(p.projection)} | own% {_v(p.ownership_pct)} "
            f"(vs baseline {_v(p.ownership_vs_baseline)}) | chalk {'yes' if p.is_chalk else 'no'} | leverage {'yes' if p.is_leverage else 'no'} | "
            f"ceiling mult {_v(p.ceiling_multiplier)} | red-zone carry share {_v(p.carry_share_trailing)} | red-zone target share {_v(p.target_share_trailing)} | "
            f"last-4 carry share {_v(p.carry_share_l4)} | last-4 target share {_v(p.target_share_l4)} | last-4 touch share {_v(p.touch_share_l4)} "
            f"(lowest single game {_v(p.touch_share_min_l4)}) | expected carry share {_v(p.carry_share_expected)} | expected target share {_v(p.target_share_expected)} | "
            f"opportunity note {_v(p.opportunity_note)} | "
            f"status {_v(p.status_after_q_pass or p.injury_status)} | note {_v(p.circumstance_note)}"
        )
    if packet.vacated:
        out += ["", "## Roles vacated by players who will not play (share of the team's last-4-game carries / targets they leave; code-computed baseline you may override with a reason)"]
        out += [f"- vacated.{v.team}: {v.name} ({v.status}) leaves {v.carry_share:.1%} of carries and {v.target_share:.1%} of targets" for v in packet.vacated]
    out += ["", "## Availability"]
    out += [f"- {a.name} ({a.team}): {a.decision} -- {a.basis} [{a.source}, as of {a.as_of or 'unknown'}]" for a in packet.availability] or ["- none listed"]
    out += ["", f"## Weather: {packet.weather or 'no reading'}"]
    return "\n".join(out)


_GLOSSARY = """\
METRIC GLOSSARY (all season-to-date, pooled; `n` in the packet is the sample behind each value):
- sack_rate, qb_hit_rate: sacks / QB hits that team's OWN offense takes per dropback (pass protection; higher = worse). The `def_` versions are the
  pass rush that team's DEFENSE generates (evidence only -- NOT usable as a question metric). Both have n = dropbacks.
- explosive_pass_rate: share of its pass attempts gaining 20+ yards. pass_epa / rush_epa: EPA per dropback / per rush. pass_rate_over_expected
  (and ..._leading, when ahead): points above the pass rate the situation predicts (positive = throws more than expected).
- pace_seconds_per_play: seconds between consecutive offensive plays (lower = faster).
- PER-GAME metrics: total_plays, rush_attempts_leading (rushes in the 2nd half while ahead by 4+), combined_pass_attempts -> value is a per-game
  average and n is GAMES. lead_at_q3_start / lead_at_q4 -> value is the share of its games the team led entering that quarter; as a QUESTION
  use threshold 0.5 and direction "gte" (YES = this team led entering that quarter in THIS game); the menu below lists the anchor.
- The sample_n >= 25 rule applies ONLY to metrics whose name contains "rate". Per-game and lead metrics are exempt (use sample_n = null).
"""

_RULES = f"""\
You are a football analyst writing the GAME THESIS for one NFL game, for DraftKings GPP lineup construction.
Reason about HOW THE UNITS INTERACT (pass protection vs pass rush -> the passing game -> game script -> volume; run game and
play-action; coverage and who absorbs targets; turnovers and the defense/special teams), not about each unit in isolation.
Use ONLY the evidence packet below. Numbers are evidence; the story is yours, and every claim must cite the packet.

RULES (each is machine-checked; a violation sends your answer back for one retry):
1. QUESTIONS: name the 1-2 pivotal questions that decide this game ("independent" questions). A third is allowed only as a CHILD of
   the first (set parent_id and parent_answer). Two independent questions must be about DIFFERENT phases: {sorted(PHASES)}.
2. Each question needs: a metric from {sorted(PROXY_METRICS)}; source_field = a citeable key like units.<TEAM>.<metric>; a threshold
   chosen from the ANCHOR MENU; direction "gte" or "lte" (YES when metric >= / <= threshold); sample_n (plays/dropbacks behind the
   metric, from the packet's n; rate metrics need >= 25); and adds_beyond_line = what this question tells us that the betting line
   does not already say. Do not ask "who wins" -- that is the line. A pressure/collapse threshold must be a HARD bar (>= the 60th
   league percentile), not one the team clears anyway.
3. MARGINALS: for each question give "adjusted", the probability it resolves YES, in 5% steps (0.05, 0.10, ...), within +/-0.25 log-odds
   of the anchor for your chosen threshold (the menu shows the anchor). If you move off the nearest 5% step to the anchor, give a
   reason that cites the packet. Prefer staying at the anchor unless you have specific evidence -- staying put is a legitimate answer.
4. DEPENDENCY: at most one. Only if one question's answer genuinely changes the other's odds; give the causal CHANNEL in words
   ("pressure -> quick throws -> lower aDOT -> lower WR ceiling") and lambda = +0.5 or -0.5 (the sign of the effect). Otherwise null.
5. BRANCHES: one branch for every combination of answers to the independent questions, plus exactly ONE residual branch
   ("residual": true, "neither question resolves as framed") with residual_prob >= 0.10. DO NOT write branch probabilities -- code
   derives them. Each branch has a description, a 2-4 link "chain" of the interaction, optional margin_shift / total_shift (points vs the
   line, favorite perspective; keep |shift| <= 8; the probability-weighted mix should land within 1 point of zero unless you declare a
   disagreement with the line via declared_margin_disagreement / declared_total_disagreement, each |d| <= 2).
6. PLAYER OUTCOMES per branch (<= 10 players per team, only listed canonical_ids): mean_mult and q90_mult from {list(MULTIPLIER_BINS)}
   (1.0 = no change vs his normal outlook). Justify with OPPORTUNITY (targets, carries, red-zone touches, snaps), never with
   fantasy points or touchdown luck. Opportunity, not depth-chart rank, decides who matters: use the last-4 shares, the lowest single-game
   touch share (a stable role) and, for a player whose teammate will not play, the expected shares and the vacated-roles list.
7. SIGNS: for any pair of players whose outcomes you expect to move TOGETHER or AGAINST each other in this game, state it in
   pair_signs (sign positive|negative|neutral, strength mild|strong, reason). A QB and the opposing defense, and a favorite's RB
   against its own pass catchers in a blowout, are the usual negatives. Do not force correlation; say where there is none.
8. CLAIMS: 3-8 claims, each with cite_keys copied EXACTLY from the citeable keys listed below. An availability claim (kind
   "availability") must also give status and as_of copied from the packet (write "unknown" if the packet gives no time). Never cite a key that is not listed; never state a number that
   is not in the packet. Account for the data gaps -- especially that matchup grades are availability-unaware.
9. COUNTER: counter_branch_id = the branch most against your headline. would_change_mind = 1-3 PRE-KICKOFF observables (an
   inactive, the weather, a line move).
10. Do not double-count the line, and do not treat a public injury as an edge if the beneficiary is already heavily owned.

OUTPUT: a single JSON object, no markdown fences, no commentary, exactly this shape:
{{
 "headline": "...",
 "questions": [{{"id":"q1","text":"...","phase":"...","metric":"...","source_field":"units.XXX.metric","threshold":0.0,"direction":"gte","sample_n":0,"adds_beyond_line":"...","parent_id":null,"parent_answer":null}}],
 "marginals": [{{"question_id":"q1","adjusted":0.25,"reason":"..."}}],
 "dependency": {{"from":"q1","to":"q2","channel":"...","lambda":0.5}} ,
 "residual_prob": 0.10,
 "branches": [{{"id":"b0","answers":{{"q1":true,"q2":false}},"description":"...","chain":["...","..."],"margin_shift":0.0,"total_shift":0.0,
               "player_outcomes":[{{"player_id":"<canonical_id>","mean_mult":1.0,"q90_mult":1.0,"reason":"..."}}]}},
              {{"id":"res","residual":true,"description":"...","chain":[]}}],
 "counter_branch_id": "b0",
 "claims": [{{"text":"...","cite_keys":["units.XXX.metric"],"kind":"general","status":null,"as_of":null}}],
 "would_change_mind": ["..."],
 "pair_signs": [{{"player_a":"<id>","player_b":"<id>","sign":"negative","strength":"mild","reason":"..."}}],
 "declared_margin_disagreement": 0.0,
 "declared_total_disagreement": 0.0
}}
(Use "dependency": null when there is none.)
"""


def analyst_prompt(packet: EvidencePacket, league_value_fn: Callable[[str], np.ndarray], *, retry_errors: list[str] | None = None) -> str:
    keys = sorted(packet_keys(packet))
    parts = [
        f"[prompt {PROMPT_VERSION}]", _RULES, _GLOSSARY,
        "ANCHOR MENU (pick thresholds from here; the P(YES) column is the anchor for that threshold and direction):",
        anchor_menu(packet, league_value_fn), "",
        "EVIDENCE PACKET:", render_packet(packet), "",
        f"CITEABLE KEYS ({len(keys)}): " + " ; ".join(keys),
    ]
    if retry_errors:
        parts += ["", "YOUR PREVIOUS ANSWER WAS REJECTED. Fix every problem below and return the corrected full JSON:", *[f"- {e}" for e in retry_errors]]
    return "\n".join(parts)
