"""Depth-chart vs. trailing-usage divergence detection -- the `CircumstanceSource` implementation
for a real disagreement between nflverse's current depth chart (`ingestion/nflverse_depth_charts.py`,
live-verified 2026-09-19 to genuinely reflect in-season roster news -- confirmed real case: Jordan
Mason correctly demoted to depth-chart rank 4 the same week he was ruled IR) and `RoleShareResult`'s
own trailing-usage leader (`ingestion/usage_share.py`).

Third detector type (Chris: "I don't think reasoning should be limited to injuries"), and --
unlike line/spread movement (explicitly deferred, Phase 2) -- deliberately snapshot-free: this
doesn't need a new longitudinal archive of its own, because nflverse already keeps that history on
its own end and `fetch_current_depth_chart` only ever reads the single freshest real snapshot. This
is a pure current-week comparison, same posture as `injury.py`'s own detection.

**Why this is a genuinely different signal than the injury detector, not a duplicate**: the injury
detector reasons from DraftKings' own roster status crossed with trailing role share -- it has no
independent read on what the TEAM itself currently believes its own depth chart is. This detector
compares two real, independent sources against each other (a team's own stated plan vs. what has
actually happened on the field), and can surface a real story the injury detector can't: a depth
chart that's already moved ahead of trailing usage (a new addition who hasn't gotten touches yet),
or one that's lagging behind a usage shift that's already happened.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.ingestion.nflverse_depth_charts import DepthChartEntry
from nfl_dfs.ingestion.usage_share import PlayerRoleShare, RoleShareResult

CIRCUMSTANCE_KIND = "depth_chart_divergence"


@dataclass(frozen=True)
class DepthChartDivergence:
    """One detected real, current disagreement between the depth chart's #1 and the real trailing-
    usage leader at one `(team, role)`. This is the deterministic "point to evaluate" -- see
    `detect_depth_chart_divergence`. Implements `engine.CircumstanceSource` (structural typing) via
    the `circumstance_*` methods below.
    """

    season: int
    week: int
    team: str
    role: str  # usage_share.py's ROLE_RB/ROLE_WR -- confirmed live to equal the depth chart's own
    # "RB"/"WR" pos_abb label exactly, so no mapping table is needed between the two sources
    depth_chart_leader: DepthChartEntry  # the real, current depth-chart #1 at this (team, role)
    usage_leader: PlayerRoleShare  # role_share.candidates[0] -- the real trailing-usage leader
    snapshot_at: str  # depth_chart_leader.snapshot_at, carried up here for convenience
    usage_leader_position: str | None = None  # real position of usage_leader, when known (e.g.
    # from the reconciled PlayerIdentity pool) -- confirmed live 2026-09-19: usage_share.py's
    # "role" is plurality-of-volume, not position-filtered (same disclosed gap injury.py's own
    # position_by_player_id already handles), so a TE with real receiving volume can legitimately
    # be the "usage leader" for a WR-role RoleShareResult. Shown to the model so it can judge
    # whether that's a genuine divergence or this same well-known non-position-filtered artifact,
    # rather than silently filtering it out (the exact "reasoning, not rules" lesson from injury.py).

    def circumstance_kind(self) -> str:
        return CIRCUMSTANCE_KIND

    def circumstance_team(self) -> str:
        return self.team

    def circumstance_season(self) -> int:
        return self.season

    def circumstance_week(self) -> int:
        return self.week

    def circumstance_subjects(self) -> list[str]:
        # Both players' outlook is worth reasoning about -- the depth-chart-anointed player AND
        # the trailing-usage leader, since the divergence itself is the real story for both.
        return [self.depth_chart_leader.player_id, self.usage_leader.player_id]

    def circumstance_facts(self) -> dict:
        return {
            "role": self.role,
            "depth_chart_leader_id": self.depth_chart_leader.player_id,
            "depth_chart_snapshot_at": self.snapshot_at,
            "usage_leader_id": self.usage_leader.player_id,
            "usage_leader_role_share_blended": round(self.usage_leader.role_share_blended, 4),
        }

    def circumstance_prompt_block(self) -> str:
        dc = self.depth_chart_leader
        ul = self.usage_leader
        tier_note = f", role tier {ul.role_tier}" if ul.role_tier else ""
        position_note = f", real position {self.usage_leader_position}" if self.usage_leader_position else ""
        return (
            f"- Real current depth chart (nflverse, as of {self.snapshot_at}) lists "
            f"{dc.player_name} as the #1 {self.role} for {self.team}.\n"
            f"- Real trailing usage (RoleShare, through last completed week) instead shows "
            f"{ul.player_name or ul.player_id} leading with {ul.role_share_blended:.0%} blended "
            f"role share{tier_note}{position_note}."
        )

    def circumstance_instructions(self) -> str:
        return (
            "This is a real disagreement between two independent real sources: the depth chart (a "
            "team's own stated plan, updated with real roster/injury news) and trailing usage "
            "(what has actually happened on the field through last week). A depth chart can lead "
            "usage (a new addition who hasn't gotten real touches yet) or lag it (a committee "
            "that hasn't been formally reordered even though usage has already shifted) -- judge "
            "which direction this particular case looks like from the real numbers given, and say "
            "so explicitly. Do not assume the depth chart is automatically right just because it's "
            "the more official-sounding source; a team's depth chart can also be a formality that "
            "doesn't reflect the real practical rotation. Separately: the usage leader's real "
            f"position may not match {self.role} exactly (this project's trailing-usage role is a "
            "plurality-of-VOLUME grouping, not position-filtered -- e.g. a TE with real receiving "
            "volume can lead a 'WR-role' group). If the usage leader's real position doesn't match "
            "this role, say so explicitly and judge whether that changes what this divergence "
            "actually means, rather than treating it as a like-for-like WR-vs-WR or RB-vs-RB story."
        )


def detect_depth_chart_divergence(
    depth_chart_entries: list[DepthChartEntry],
    role_share: RoleShareResult,
    *,
    position_by_player_id: dict[str, str] | None = None,
) -> DepthChartDivergence | None:
    """Finds a real disagreement between the depth chart's #1 at `(role_share.team, role_share.role)`
    and `role_share.candidates[0]` (the real trailing-usage leader), or `None` when there isn't one.

    Returns `None` (never fabricates a comparison) when: `role_share.candidates` is empty (no real
    trailing usage data yet to compare against -- early season, or a bye week so far), no
    `depth_chart_entries` entry matches `(team, position=role, depth_rank=1)`, or the two sources
    actually agree (same real player leading both).

    `position_by_player_id`, when supplied (same gsis_id-space key as `injury.py`'s own parameter
    of the same name -- e.g. built from the reconciled `PlayerIdentity` pool), is carried onto the
    returned `DepthChartDivergence` as `usage_leader_position` so the synthesis step can reason
    about a real position mismatch (a TE leading a "WR-role" trailing-usage group,
    `usage_share.py`'s own disclosed non-position-filtered computation) itself, rather than that
    judgment being made silently here.
    """
    if not role_share.candidates:
        return None
    usage_leader = role_share.candidates[0]

    depth_chart_leader = next(
        (
            entry
            for entry in depth_chart_entries
            if entry.team == role_share.team and entry.position == role_share.role and entry.depth_rank == 1
        ),
        None,
    )
    if depth_chart_leader is None:
        return None

    if depth_chart_leader.player_id == usage_leader.player_id:
        return None

    return DepthChartDivergence(
        season=role_share.season,
        week=role_share.week,
        team=role_share.team,
        role=role_share.role,
        usage_leader_position=(position_by_player_id or {}).get(usage_leader.player_id),
        depth_chart_leader=depth_chart_leader,
        usage_leader=usage_leader,
        snapshot_at=depth_chart_leader.snapshot_at,
    )
