"""Stage 9: exposure report (PRD Section 5 step 9; Section 8: "An exposure report: how often each
player, and each stack, appears across the set").

**Genuinely general, not hardcoded to "always exactly once" (per the task brief):** `optimizer/
lineup.py`'s no-good-cut diversity mechanism means every generated set's `core_stack`s are
distinct today (PRD Section 7's "no more than one lineup ... should share an identical core
stack"), so in practice every stack below currently reports `count == 1`. Nothing here assumes
that, though -- both counters are plain tallies over however many lineups are actually passed in,
so a future caller that relaxes the no-good-cut rule (or feeds this module a hand-picked/edited
lineup set with a real repeat) gets a correct, non-trivial exposure count with no code change
here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from nfl_dfs.optimizer.lineup import Lineup


@dataclass(frozen=True)
class PlayerExposure:
    """How often one player appears across the generated lineup set."""

    canonical_id: str
    display_name: str
    position: str
    team: str
    count: int
    lineup_count: int
    exposure_pct: float  # count / lineup_count, as a fraction in [0, 1] -- 0.0 if lineup_count == 0


@dataclass(frozen=True)
class StackExposure:
    """How often one exact core stack (`Lineup.core_stack` -- the QB + same-team WR/TE combo the
    optimizer's no-good cut keys off of, PRD Section 7) appears across the generated lineup set.
    """

    core_stack: frozenset[str]  # canonical_ids, matching Lineup.core_stack exactly
    core_stack_team: str
    display: str  # human-readable "QB + WR/TE names" rendering, for the text report
    count: int
    lineup_count: int
    exposure_pct: float


@dataclass(frozen=True)
class ExposureReport:
    """PRD Section 8's exposure report: structured data plus a human-readable rendering."""

    lineup_count: int
    players: list[PlayerExposure]  # sorted descending by count, then display_name
    stacks: list[StackExposure]  # sorted descending by count, then core_stack_team

    def render_text(self) -> str:
        """A simple, human-readable table -- the "clear rendering" half of the task brief. Not
        aligned/padded fancily; this is meant to be read in a terminal or pasted into a plain-text
        note, not a polished report artifact.
        """
        lines = [f"Exposure report -- {self.lineup_count} lineup(s)", "", "Players:"]
        if not self.players:
            lines.append("  (none)")
        for pe in self.players:
            lines.append(
                f"  {pe.display_name:<24} {pe.position:<4} {pe.team:<4} "
                f"{pe.count}/{pe.lineup_count} ({pe.exposure_pct:.0%})"
            )
        lines.append("")
        lines.append("Stacks:")
        if not self.stacks:
            lines.append("  (none)")
        for se in self.stacks:
            lines.append(
                f"  {se.display:<40} [{se.core_stack_team}] "
                f"{se.count}/{se.lineup_count} ({se.exposure_pct:.0%})"
            )
        return "\n".join(lines)


def _stack_display(lineup: Lineup) -> str:
    """`"QB + WR/TE names"`, in QB-first order, for a stack's human-readable rendering. Reads
    display names off `lineup.players` (already carries them) -- no separate lookup needed.
    """
    by_id = {p.canonical_id: p.display_name for p in lineup.players}
    qb_name = lineup.slots["QB"].display_name
    catcher_names = sorted(
        by_id[cid] for cid in lineup.core_stack if cid in by_id and cid != lineup.slots["QB"].canonical_id
    )
    return " + ".join([qb_name, *catcher_names])


def build_exposure_report(lineups: list[Lineup]) -> ExposureReport:
    """Tally player and stack appearances across `lineups`. An empty `lineups` list produces a
    valid, empty report (`lineup_count=0`, both lists `[]`) rather than raising or dividing by
    zero -- `exposure_pct` is `0.0` for every row in that degenerate case.
    """
    lineup_count = len(lineups)

    player_counts: Counter[str] = Counter()
    player_meta: dict[str, tuple[str, str, str]] = {}
    for lineup in lineups:
        for p in lineup.players:
            player_counts[p.canonical_id] += 1
            player_meta[p.canonical_id] = (p.display_name, p.position, p.team)

    players = [
        PlayerExposure(
            canonical_id=cid,
            display_name=player_meta[cid][0],
            position=player_meta[cid][1],
            team=player_meta[cid][2],
            count=count,
            lineup_count=lineup_count,
            exposure_pct=(count / lineup_count) if lineup_count else 0.0,
        )
        for cid, count in player_counts.items()
    ]
    players.sort(key=lambda pe: (-pe.count, pe.display_name))

    stack_counts: Counter[frozenset[str]] = Counter()
    stack_meta: dict[frozenset[str], tuple[str, str]] = {}
    for lineup in lineups:
        stack_counts[lineup.core_stack] += 1
        stack_meta[lineup.core_stack] = (lineup.core_stack_team, _stack_display(lineup))

    stacks = [
        StackExposure(
            core_stack=stack,
            core_stack_team=stack_meta[stack][0],
            display=stack_meta[stack][1],
            count=count,
            lineup_count=lineup_count,
            exposure_pct=(count / lineup_count) if lineup_count else 0.0,
        )
        for stack, count in stack_counts.items()
    ]
    stacks.sort(key=lambda se: (-se.count, se.core_stack_team))

    return ExposureReport(lineup_count=lineup_count, players=players, stacks=stacks)
