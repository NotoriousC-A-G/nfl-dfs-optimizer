"""Cross-source injury-report comparison logic (ADR-0031/ADR-0038) -- factored out of
`scripts/injury_staleness_check.py`'s original bridging/classification logic so both that live
one-shot script and the new retrospective snapshot-archive comparison
(`scripts/injury_snapshot_retrospective_check.py`, ADR-0038) share one real implementation instead
of two independently-drifting copies.

Bridges RotoGrinders' Situation Room injury data (native-id-keyed, `identity.sources
["rotogrinders"].native_id`) against the NFL's own official weekly injury report (gsis_id-keyed,
`identity.nflverse_gsis_id`) through an already-reconciled `PlayerIdentity` pool -- the same join
both consumers need, done once here rather than duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.ingestion.official_injury_report import OfficialInjuryReportEntry
from nfl_dfs.ingestion.rotogrinders_injuries import InjuryReportEntry
from nfl_dfs.normalization.identity import MatchMethod, PlayerIdentity

# A rough, disclosed mapping from the NFL's official three-tier report_status vocabulary to
# RotoGrinders' single-letter STATUS codes -- used only to classify agreement/disagreement, not
# treated as an exact or backtested equivalence (neither source publishes a crosswalk between the
# two vocabularies). Carried unchanged from the original script (ADR-0031).
OFFICIAL_TO_RG_STATUS = {"Out": "O", "Doubtful": "D", "Questionable": "Q"}


@dataclass(frozen=True)
class InjurySourceComparison:
    """One (RotoGrinders snapshot, official report) comparison's categorized results --
    identities are kept alongside each entry so a caller can print/report on them without a
    second join."""

    only_rg: list[tuple[PlayerIdentity, InjuryReportEntry]]
    only_official: list[tuple[PlayerIdentity, OfficialInjuryReportEntry]]
    agree: list[tuple[PlayerIdentity, InjuryReportEntry, OfficialInjuryReportEntry]]
    disagree: list[tuple[PlayerIdentity, InjuryReportEntry, OfficialInjuryReportEntry]]

    @property
    def both(self) -> list[tuple[PlayerIdentity, InjuryReportEntry, OfficialInjuryReportEntry]]:
        """Every player present on both sources with a real official game-status -- `agree +
        disagree`, the only population a match rate can be computed over."""
        return self.agree + self.disagree

    @property
    def match_rate(self) -> float | None:
        """`None` (never a fabricated 0.0 or 1.0) when nobody was comparable this run -- the
        real, disclosed Finding-1 case ADR-0031 hit on its first live run."""
        total = len(self.both)
        return (len(self.agree) / total) if total > 0 else None


def compare_injury_sources(
    identities: list[PlayerIdentity],
    rg_entries: list[InjuryReportEntry],
    official_entries: list[OfficialInjuryReportEntry],
) -> InjurySourceComparison:
    """Bridges via `identity.sources["rotogrinders"].native_id` (RotoGrinders' own join key) and
    `identity.nflverse_gsis_id` (the official report's join key) -- only identities resolving BOTH
    can be classified; the same "can't compare what isn't bridgeable" scope the original script
    already disclosed. `official_entries` should already be scoped to the ONE week being compared
    against -- this function doesn't assume or enforce a single-week list, that's the caller's job
    (both current callers do it: the live script takes `latest_week_entries`'s result, the
    retrospective script filters archived-snapshot-matching weeks explicitly).
    """
    by_rg_id = {e.rotogrinders_player_id: e for e in rg_entries}
    by_gsis_id = {e.gsis_id: e for e in official_entries}

    only_rg: list[tuple[PlayerIdentity, InjuryReportEntry]] = []
    only_official: list[tuple[PlayerIdentity, OfficialInjuryReportEntry]] = []
    agree: list[tuple[PlayerIdentity, InjuryReportEntry, OfficialInjuryReportEntry]] = []
    disagree: list[tuple[PlayerIdentity, InjuryReportEntry, OfficialInjuryReportEntry]] = []

    for identity in identities:
        rg_match = identity.sources.get("rotogrinders")
        if rg_match is None or rg_match.method == MatchMethod.UNRESOLVED or rg_match.native_id is None:
            continue
        if identity.nflverse_gsis_id is None:
            continue

        rg_entry = by_rg_id.get(rg_match.native_id)
        official_entry = by_gsis_id.get(identity.nflverse_gsis_id)
        on_rg = rg_entry is not None
        on_official = official_entry is not None and official_entry.report_status is not None

        if on_rg and not on_official:
            only_rg.append((identity, rg_entry))
        elif on_official and not on_rg:
            only_official.append((identity, official_entry))
        elif on_rg and on_official:
            expected_rg_status = OFFICIAL_TO_RG_STATUS.get(official_entry.report_status)
            if expected_rg_status is not None and rg_entry.status == expected_rg_status:
                agree.append((identity, rg_entry, official_entry))
            else:
                disagree.append((identity, rg_entry, official_entry))

    return InjurySourceComparison(only_rg=only_rg, only_official=only_official, agree=agree, disagree=disagree)
