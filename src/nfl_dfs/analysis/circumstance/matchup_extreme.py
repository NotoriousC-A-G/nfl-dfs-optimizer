"""MatchupContext "extreme" circumstance detection -- the `CircumstanceSource` implementation for a
player whose real, current-week matchup multiplier (PFF-grade-based unit-vs-unit differentials,
`matchup/context.py`) is unusually favorable or unfavorable. See `circumstance/engine.py`'s module
docstring for the shared detect -> synthesize architecture this plugs into.

Second detector type added 2026-09-19 (Chris: "I don't think reasoning should be limited to
injuries"), and the first one requiring ZERO new data -- `MatchupContextResult.combined_multiplier`
is already computed for every reconciled player, every live run.

Deliberately detects only the RAW SIZE of the deviation from neutral (1.0x), not its DIRECTION or
"goodness" -- a big favorable OR unfavorable multiplier is equally worth reasoning about (a huge
mismatch either way is more likely to hold up or fall apart than a middling one), and the synthesis
step (not this detection layer) is where judgment about whether the driving row's own reason is
specific/mechanistic enough to trust belongs -- same "detect the fact, reason about the
interpretation elsewhere" split as `injury.py`'s own departed/remaining detection.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.matchup.context import MatchupContextResult, MatchupRowResult
from nfl_dfs.matchup.grading import MULTIPLIER_CAP, PASS_PROTECTION_COVERAGE_COMBINED_CAP

CIRCUMSTANCE_KIND = "matchup_extreme"

# Real judgment call, flagged as a draft starting value per this project's ADR-0019/ADR-0020
# convention (not backtested against history): how far from neutral (1.0x) a combined_multiplier
# must be, as a FRACTION of the applicable cap for this player's position, to count as a real
# "extreme" worth reasoning about. A WR/TE's combined multiplier can swing further from neutral
# than an RB/QB's single-row one (PASS_PROTECTION_COVERAGE_COMBINED_CAP=0.20 vs. MULTIPLIER_CAP=
# 0.15, matchup/grading.py), so using a fixed fraction of each position's own cap (rather than one
# flat number for every position) keeps the bar proportionate to what's actually reachable.
#
# Started at 0.75, tightened live 2026-09-19: confirmed against the real week 2 pool, 0.75 fired on
# 81 of ~670 players (~1 in 5 eligible skill players) -- far too loose for something meant to flag
# a genuine outlier, and costly at that volume (~5-8k tokens per real synthesis). 0.95 (within a
# hair of the real maximum) is a much stricter, but still explicitly unvalidated, starting guess --
# real backtesting against settled outcomes, not just a tighter number, is the real fix long-term.
EXTREME_MULTIPLIER_DISTANCE_FRACTION = 0.95

_CAP_BY_POSITION: dict[str, float] = {
    "RB": MULTIPLIER_CAP,
    "QB": MULTIPLIER_CAP,
    "WR": PASS_PROTECTION_COVERAGE_COMBINED_CAP,
    "TE": PASS_PROTECTION_COVERAGE_COMBINED_CAP,
}


def _extreme_threshold(position: str, extreme_fraction: float) -> float | None:
    """`None` for a position with no applicable cap (DST, or anything `matchup/context.py` doesn't
    build a row for) -- never guesses a threshold for a position this module has no real basis for."""
    cap = _CAP_BY_POSITION.get(position)
    if cap is None:
        return None
    return cap * extreme_fraction


def _driving_rows(matchup: MatchupContextResult) -> list[MatchupRowResult]:
    return [row for row in (matchup.run_game, matchup.pass_protection, matchup.coverage) if row is not None]


def _row_line(row: MatchupRowResult) -> str:
    multiplier_text = f"{row.multiplier:.3f}x" if row.multiplier is not None else "not computable"
    reason = row.reason or "(no reason given)"
    return f"- {row.label}: {multiplier_text} -- {reason}"


@dataclass(frozen=True)
class MatchupExtremeCircumstance:
    """One detected real, current-week matchup extreme: `matchup.combined_multiplier` deviates from
    neutral (1.0x) by at least `_extreme_threshold(position)` for this player. This is the
    deterministic "point to evaluate" -- see `detect_matchup_extreme_circumstance`. Implements
    `engine.CircumstanceSource` (structural typing) via the `circumstance_*` methods below.
    """

    season: int
    week: int
    team: str
    player_id: str  # canonical_player_id -- the same id space PlayerDetailRecord is keyed by
    player_name: str
    position: str
    matchup: MatchupContextResult

    def circumstance_kind(self) -> str:
        return CIRCUMSTANCE_KIND

    def circumstance_team(self) -> str:
        return self.team

    def circumstance_season(self) -> int:
        return self.season

    def circumstance_week(self) -> int:
        return self.week

    def circumstance_subjects(self) -> list[str]:
        return [self.player_id]

    def circumstance_facts(self) -> dict:
        # player_id is a real, load-bearing fact here -- NOT just descriptive metadata. run_game/
        # pass_protection multipliers are computed at TEAM level (matchup/context.py), so two
        # different teammates at the same position facing the same opponent can share an IDENTICAL
        # position/opponent/multiplier/rows combination. Without player_id in the cache key, their
        # circumstance_facts() would collide onto the same cache entry -- confirmed live 2026-09-19
        # (81 real matchup-extreme circumstances detected, only 18 distinct cache files written) --
        # and a cached POV that names ONE player by name would get served for a DIFFERENT one.
        return {
            "player_id": self.player_id,
            "position": self.position,
            "opponent": self.matchup.opponent,
            "combined_multiplier": round(self.matchup.combined_multiplier, 4),
            "rows": [
                {
                    "label": row.label,
                    "multiplier": round(row.multiplier, 4) if row.multiplier is not None else None,
                    "reason": row.reason,
                }
                for row in _driving_rows(self.matchup)
            ],
        }

    def circumstance_prompt_block(self) -> str:
        combined = self.matchup.combined_multiplier
        direction = "favorable" if combined > 1.0 else "unfavorable"
        rows = _driving_rows(self.matchup)
        rows_text = "\n".join(_row_line(row) for row in rows) if rows else "(no contributing row data available)"
        return (
            f"- {self.player_name} ({self.position}) vs {self.matchup.opponent}: "
            f"{combined:.3f}x combined matchup multiplier ({direction} relative to a neutral 1.000x), "
            f"driven by:\n{rows_text}"
        )

    def circumstance_instructions(self) -> str:
        return (
            "This multiplier comes from real PFF-grade-based z-score differentials between this "
            "player's own unit and the opposing unit (run-blocking vs run-defense, pass-blocking vs "
            "pass-rush, or receiving scheme vs coverage scheme -- see the row(s) above, and their "
            "own real 'reason' text). Judge whether the driving row's stated reason is specific and "
            "mechanistic (names a real grade differential) or thin/generic before treating the "
            "extreme as a meaningful edge -- a real, well-supported grade differential is a genuine "
            "signal, but a large number with a weak or missing reason deserves real skepticism. Do "
            "not invent a specific scheme detail, play type, or player name beyond what the row's "
            "own reason states."
        )


def detect_matchup_extreme_circumstance(
    matchup: MatchupContextResult,
    player_name: str,
    season: int,
    week: int,
    *,
    extreme_fraction: float = EXTREME_MULTIPLIER_DISTANCE_FRACTION,
) -> MatchupExtremeCircumstance | None:
    """Finds a real, current-week matchup extreme for one player, or `None` when there isn't one.

    `matchup.combined_multiplier`'s distance from neutral (1.0x) must clear
    `extreme_fraction * _extreme_threshold(matchup.position)`'s applicable cap
    (`MULTIPLIER_CAP`/`PASS_PROTECTION_COVERAGE_COMBINED_CAP`, `matchup/grading.py`) -- see this
    module's own docstring for why the threshold is a fraction of each position's own cap rather
    than one flat number.

    Returns `None` (never fabricates a circumstance) when: `matchup.position` has no applicable cap
    (e.g. DST), or the multiplier's distance from neutral doesn't clear the threshold. `season`/
    `week` are supplied by the caller -- `MatchupContextResult` itself carries no season/week field.
    """
    threshold = _extreme_threshold(matchup.position, extreme_fraction)
    if threshold is None:
        return None
    if abs(matchup.combined_multiplier - 1.0) < threshold:
        return None
    return MatchupExtremeCircumstance(
        season=season,
        week=week,
        team=matchup.team,
        player_id=matchup.canonical_player_id,
        player_name=player_name,
        position=matchup.position,
        matchup=matchup,
    )
