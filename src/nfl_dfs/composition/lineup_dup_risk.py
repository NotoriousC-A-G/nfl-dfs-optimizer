"""Live dup-risk read for one of this project's own generated `Lineup`s (ADR-0034), joining a
lineup's real players against this week's live projected ownership (`ownership/leverage.py`,
ADR-0026) and classifying the lineup's own average against the real historical dup-rate lookup
table `analysis/dup_risk_calibration.py` built from settled-contest field data (ADR-0032/0033).

**A real, disclosed assumption, not a proven equivalence:** the historical lookup table's `avg_own`
axis is REAL, POST-CONTEST ACTUAL field ownership; this module classifies a lineup using this
week's LIVE PROJECTED ownership instead (no other source exists for a not-yet-played slate). This
is the same projected-vs-actual comparison `ownership/leverage.py` already makes for its own
chalk/leverage flags (ADR-0026) -- not a new leap of faith this module invents, but still worth
stating plainly: this read describes "if the field roughly plays out the way it's currently
projected to," not a guaranteed forecast.

**`assess_lineup_dup_risk` itself still only ASSESSES an already-generated lineup after the fact**
-- the dashboard's own descriptive read (ADR-0034), unchanged by the below. Folding a dup-risk
consideration into generation itself was, at the time this module was first built, named as its
own separate design decision deserving its own review -- that review happened (ADR-0035) and was
built (ADR-0037, `optimizer.lineup.generate_dup_risk_aware_lineups`). `build_projected_ownership_
by_canonical_id` below is the one piece of real, reusable join logic both that generation-time
consumer and this module's own per-lineup assessment need (the RotoGrinders-`native_id` join),
factored out here rather than duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.analysis.dup_risk_calibration import DupRiskLookupTable, classify_avg_ownership
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity
from nfl_dfs.optimizer.lineup import Lineup
from nfl_dfs.ownership.leverage import LeverageAssessment

MIN_PLAYERS_COVERED = 5  # below this many of the lineup's 9 spots resolving to a real projected-
# ownership number, the average is too thin a sample of the roster to trust -- gate to None/reason,
# never a silently-computed average over 1-2 players standing in for the whole lineup.


@dataclass(frozen=True)
class LineupDupRiskAssessment:
    """One generated lineup's real dup-risk read. `avg_projected_ownership`/`players_covered` are
    `None`/`0` (with `reason` set) when fewer than `MIN_PLAYERS_COVERED` of the lineup's 9 players
    resolved to a live projected-ownership number -- never a fabricated average over too few real
    inputs. `bucket`/`dup_rate`/`mean_lineup_ct` are the real historical read from `classify_avg_
    ownership` once a trustworthy average exists."""

    avg_projected_ownership: float | None
    players_covered: int
    bucket: int | None
    dup_rate: float | None
    mean_lineup_ct: float | None
    reason: str | None


def assess_lineup_dup_risk(
    lineup: Lineup,
    identities: list[PlayerIdentity],
    leverage_by_native_id: dict[str, LeverageAssessment] | None,
    table: DupRiskLookupTable,
    *,
    min_players_covered: int = MIN_PLAYERS_COVERED,
) -> LineupDupRiskAssessment:
    """Joined via `identity.sources["rotogrinders"].native_id` -- the same join
    `composition/player_detail.py`'s `_ownership_leverage` already uses, not a new join scheme."""
    by_canonical_id = {identity.canonical_id: identity for identity in identities}
    leverage_by_native_id = leverage_by_native_id or {}

    projected_ownerships: list[float] = []
    for player in lineup.players:
        identity = by_canonical_id.get(player.canonical_id)
        if identity is None:
            continue
        rg_match = identity.sources.get("rotogrinders")
        if rg_match is None or rg_match.method in (MatchMethod.UNRESOLVED, MatchMethod.AMBIGUOUS) or rg_match.native_id is None:
            continue
        assessment = leverage_by_native_id.get(rg_match.native_id)
        if assessment is None:
            continue
        projected_ownerships.append(assessment.projected_ownership)

    players_covered = len(projected_ownerships)
    if players_covered < min_players_covered:
        return LineupDupRiskAssessment(
            avg_projected_ownership=None,
            players_covered=players_covered,
            bucket=None,
            dup_rate=None,
            mean_lineup_ct=None,
            reason=(
                f"only {players_covered}/{len(lineup.players)} of this lineup's players resolved to a "
                f"live projected-ownership number -- below the {min_players_covered}-player floor this "
                "read requires to trust the lineup's average, not a fabricated estimate over too few "
                "real inputs."
            ),
        )

    avg_projected_ownership = sum(projected_ownerships) / players_covered
    bucket, dup_rate, mean_lineup_ct = classify_avg_ownership(avg_projected_ownership, table)
    return LineupDupRiskAssessment(
        avg_projected_ownership=avg_projected_ownership,
        players_covered=players_covered,
        bucket=bucket,
        dup_rate=dup_rate,
        mean_lineup_ct=mean_lineup_ct,
        reason=None,
    )


def build_projected_ownership_by_canonical_id(
    identities: list[PlayerIdentity], leverage_by_native_id: dict[str, LeverageAssessment]
) -> dict[str, float]:
    """The full reconciled identity pool's live projected ownership, keyed by `canonical_id` --
    joined via `identity.sources["rotogrinders"].native_id`, the same join `assess_lineup_dup_risk`
    performs per-lineup above, factored out here so a caller that needs to classify MANY candidate
    lineups (`optimizer.lineup.generate_dup_risk_aware_lineups`, ADR-0035/0037) doesn't re-derive
    this join once per candidate. A `canonical_id` with no resolved RotoGrinders match, or no live
    leverage assessment this week, is simply absent from the returned dict -- never a fabricated
    value (the same "gate nulls/omits, never fabricates" convention `assess_lineup_dup_risk` above
    already follows for its own per-lineup average).
    """
    result: dict[str, float] = {}
    for identity in identities:
        rg_match = identity.sources.get("rotogrinders")
        if rg_match is None or rg_match.method in (MatchMethod.UNRESOLVED, MatchMethod.AMBIGUOUS) or rg_match.native_id is None:
            continue
        assessment = leverage_by_native_id.get(rg_match.native_id)
        if assessment is None:
            continue
        result[identity.canonical_id] = assessment.projected_ownership
    return result
