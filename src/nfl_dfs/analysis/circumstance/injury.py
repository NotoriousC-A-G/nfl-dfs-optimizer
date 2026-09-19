"""Injury-driven role-share circumstance detection -- the `CircumstanceSource` implementation for
injuries. See `circumstance/engine.py`'s module docstring for the shared detect -> synthesize
architecture this plugs into (extracted from this module, formerly `analysis/injury_circumstance.py`,
2026-09-19).

Chris's original framing (2026-09-19), in his own words:
1. The injury forces a change.
2. Our content-consumption layer should help us build a point of view on how the team will handle
   the situation.
3. Projections tell us what we think is going to happen -- we should be able to see the reason for
   the projection to be what it is.

**Distinct from ADR-0020's `ConcurrentActivityWindow`** (RB role only, `ingestion/usage_share.py`):
that mechanism only corrects RETROACTIVE dilution -- excluding a PAST trailing week where a
teammate was blanked from the denominator, so an already-healthy back's trailing share isn't
artificially deflated by weeks the committee mate was actually out. Nothing in `usage_share.py` has
any awareness of a teammate's CURRENT-week injury status: `RoleShareResult` is built purely from
completed pbp through `week - 1`. `detect_injury_circumstance_change` is the first place that
circumstance gets detected at all -- a genuinely different problem, not a duplicate of ADR-0020's
fix. It's a pure function, no LLM, no network -- see `engine.synthesize_circumstance` for the
reasoning half.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.ingestion.usage_share import PlayerRoleShare, RoleShareResult
from nfl_dfs.optimizer.lineup import EXCLUDED_INJURY_STATUSES

CIRCUMSTANCE_KIND = "injury"

# Real judgment call, flagged as a draft starting value per this project's ADR-0019/ADR-0020
# convention (not backtested against history): a departed teammate only counts as a real
# "circumstance change" when they held real trailing volume -- not a bit-part committee back whose
# departure barely moves anything. Deliberately lower than usage_share.py's own MIDTIER_THRESHOLD
# (0.45, the bar for CROWNING someone a lead back) -- a departure worth SURFACING (this module's
# job) is a lower bar than a departure worth naming a new lead back.
DEPARTED_SHARE_FLOOR = 0.20

_ANOMALY_INSTRUCTION = (
    "Where a listed candidate's own position looks out of place for this role (e.g. a QB "
    "showing up in an RB-role list), do NOT apply a blanket 'wrong position means discount it' "
    "rule -- that's still not real judgment, just a different rule. Use what you actually know "
    "about THIS specific player's real playing style: a known mobile/dual-threat quarterback "
    "(the Lamar Jackson/Malik Willis type) can carry genuine, repeatable rushing volume that "
    "IS meaningful for this role, while a pocket passer with minimal real rushing history "
    "showing the same numbers almost certainly reflects an unrepresentative small sample, not a "
    "real role. Say explicitly which case applies to this named player and why. Separately, "
    "where a candidate's blended share diverges sharply from their raw trailing share (a small "
    "sample being pulled hard toward the league-wide prior), explain that divergence using ONLY "
    "the real numbers given above (raw share, tracked weeks, shrinkage weight) -- do not guess "
    "at a specific play-by-play mechanism (e.g. claiming it reflects kneel-downs) that isn't "
    "actually given to you."
)


def _candidate_line(candidate: PlayerRoleShare, position_by_player_id: dict[str, str] | None) -> str:
    """One candidate's real inputs, position included when known -- the model reasons about
    whether a candidate's numbers deserve full weight (e.g. a backup QB's real position showing up
    in an RB-role RoleShareResult, `usage_share.py`'s own disclosed non-position-filtered
    computation) itself, from real data, rather than that judgment being made silently in code
    before it ever sees the candidate. See `detect_injury_circumstance_change`'s docstring for why
    `remaining` is deliberately NOT filtered by position the way `departed` detection is.

    **Shows the RAW trailing share alongside the shrinkage-BLENDED one, not just the blended
    number alone (confirmed live 2026-09-19: showing only `role_share_blended` led the model to
    invent an unverifiable specific mechanism -- "kneel-downs" -- to explain a small-sample QB's
    44% blended share, when the real explanation was visible in data it simply wasn't shown: 2 real
    carries over 1 tracked week (raw share 6.5%), shrinkage-blended 86% of the way toward the
    league-wide RB prior. Giving the model the real raw share, sample size, and shrinkage weight
    lets it explain the actual divergence instead of guessing a plausible-sounding one.**
    """
    position = (position_by_player_id or {}).get(candidate.player_id)
    position_note = f", position {position}" if position else ""
    tier_note = f", role tier {candidate.role_tier}" if candidate.role_tier else ""
    return (
        f"- {candidate.player_name or candidate.player_id}: {candidate.role_share_blended:.0%} blended "
        f"role share (raw trailing: {candidate.trailing_volume}/{candidate.trailing_team_volume} = "
        f"{candidate.trailing_share:.0%} over {candidate.weeks_played} tracked week(s), shrinkage weight "
        f"{candidate.shrinkage_weight:.2f} toward the league-wide prior){tier_note}{position_note}"
    )


@dataclass(frozen=True)
class CircumstanceChange:
    """One detected real, current circumstance: a teammate with real trailing role share at this
    `(team, role)` is ruled OUT/IR for the week being projected, and at least one teammate at the
    same role remains eligible. This is the deterministic "point to evaluate" -- see
    `detect_injury_circumstance_change`. Implements `engine.CircumstanceSource` (structural typing,
    no inheritance needed) via the `circumstance_*` methods below.
    """

    season: int
    week: int
    team: str
    role: str
    departed: PlayerRoleShare  # the OUT/IR teammate, with their real trailing role share
    departed_status: str  # DraftKings' own real roster-status code (e.g. "OUT", "IR")
    remaining: list[PlayerRoleShare]  # every OTHER real trailing-volume candidate at this
    # (team, role), in role_share.candidates's existing role_share_blended order -- not just the
    # single highest, since more than one teammate's outlook can plausibly be affected
    position_by_player_id: dict[str, str] | None = None  # carried through for circumstance_prompt_
    # block()'s own use (real position context shown per candidate) -- see _candidate_line

    def circumstance_kind(self) -> str:
        return CIRCUMSTANCE_KIND

    def circumstance_team(self) -> str:
        return self.team

    def circumstance_season(self) -> int:
        return self.season

    def circumstance_week(self) -> int:
        return self.week

    def circumstance_subjects(self) -> list[str]:
        return [c.player_id for c in self.remaining]

    def circumstance_facts(self) -> dict:
        return {
            "role": self.role,
            "departed_player_id": self.departed.player_id,
            "departed_status": self.departed_status,
            "departed_role_share_blended": round(self.departed.role_share_blended, 4),
            "remaining": [
                {"player_id": c.player_id, "role_share_blended": round(c.role_share_blended, 4)}
                for c in self.remaining
            ],
        }

    def circumstance_prompt_block(self) -> str:
        departed_line = f"{_candidate_line(self.departed, self.position_by_player_id)} ({self.departed_status})"
        remaining_lines = "\n".join(_candidate_line(c, self.position_by_player_id) for c in self.remaining)
        return f"{departed_line}\n\nRemaining {self.role}-role teammates with real trailing volume:\n{remaining_lines}"

    def circumstance_instructions(self) -> str:
        return _ANOMALY_INSTRUCTION


def detect_injury_circumstance_change(
    role_share: RoleShareResult,
    dk_injury_status_by_player_id: dict[str, str | None],
    *,
    departed_share_floor: float = DEPARTED_SHARE_FLOOR,
    position_by_player_id: dict[str, str] | None = None,
) -> CircumstanceChange | None:
    """Finds the most consequential real, current-week injury circumstance at this `(team, role)`,
    or `None` when there isn't one.

    `role_share.candidates` is already sorted descending by `role_share_blended`
    (`usage_share.py`'s own contract) -- the first candidate whose DraftKings status is in
    `EXCLUDED_INJURY_STATUSES` ("IR"/"OUT") AND whose trailing share clears
    `departed_share_floor` is the departure this returns. Only the single most consequential
    departure is detected per call -- a second, smaller same-week departure at the same role is a
    genuinely rarer case this function doesn't try to layer reasoning on top of.

    Returns `None` (never fabricates a circumstance) when: no candidate clears both the injury-
    status and share-floor checks, or every remaining candidate is ALSO OUT/IR (nobody's outlook is
    left to reason about). `dk_injury_status_by_player_id` is keyed by the same id space
    `PlayerRoleShare.player_id` uses (nflverse `gsis_id`) -- a caller sourcing this from
    `PlayerProjection.dk_injury_status` (`projection/blend.py`) should key it by `canonical_id`,
    which equals `gsis_id` for these roles in the common case (ADR-0013).

    `position_by_player_id`, when supplied (same gsis_id-space key -- e.g. built from the
    reconciled `PlayerIdentity` pool's own real `position` field), is used ONLY to decide whether a
    candidate can be the DEPARTED player -- a real position mismatch (a confirmed `"QB"`) means
    this isn't a genuine RB/WR-role circumstance at all, just that backup QB's own usage pattern
    (`usage_share.py`'s RB/WR role computation is plurality-of-volume, not position-filtered, that
    module's own disclosed "Judgment call 2" -- a backup QB's kneel/scramble carries can genuinely
    clear an RB-role `RoleShareResult`'s candidate list). This is a mechanical correctness check on
    whether to fire a circumstance AT ALL, not an interpretive judgment. It's also carried onto the
    returned `CircumstanceChange` so `circumstance_prompt_block()` can show it per-candidate.

    **Deliberately NOT applied to `remaining`** (confirmed live 2026-09-19, then reconsidered same
    day): an earlier version of this function also filtered `remaining` by position, silently
    dropping an anomalous candidate (Carson Wentz, MIN's real backup QB) before the reasoning step
    ever saw him. That's exactly the wrong instinct this project has been pushing back against --
    deciding "this candidate doesn't count" in code is still a rule standing in for reasoning, just
    a quieter one. `remaining` stays unfiltered; the synthesis step is instead given real position
    data for every remaining candidate so the model can reason about an anomaly like this itself
    (and say so in its own POV), rather than have it invisibly disappear upstream.
    """
    departed_candidate: PlayerRoleShare | None = None
    for candidate in role_share.candidates:
        status = dk_injury_status_by_player_id.get(candidate.player_id)
        is_real_role_position = (
            position_by_player_id is None or position_by_player_id.get(candidate.player_id) != "QB"
        )
        if (
            status in EXCLUDED_INJURY_STATUSES
            and candidate.role_share_blended >= departed_share_floor
            and is_real_role_position
        ):
            departed_candidate = candidate
            break
    if departed_candidate is None:
        return None

    remaining = [
        c
        for c in role_share.candidates
        if c.player_id != departed_candidate.player_id
        and dk_injury_status_by_player_id.get(c.player_id) not in EXCLUDED_INJURY_STATUSES
    ]
    if not remaining:
        return None

    return CircumstanceChange(
        season=role_share.season,
        week=role_share.week,
        team=role_share.team,
        role=role_share.role,
        departed=departed_candidate,
        departed_status=dk_injury_status_by_player_id[departed_candidate.player_id],
        remaining=remaining,
        position_by_player_id=position_by_player_id,
    )
