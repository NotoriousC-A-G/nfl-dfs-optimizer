"""Stage 9 orchestration (PRD Section 5 step 9; Section 8's full "Outputs" list) -- the single
call that represents "here's Chris's weekly deliverable."

PRD Section 8 lists four things:
  1. "A generated set of 3 lineups" -- already produced upstream by `optimizer.lineup.
     generate_lineups`; this module takes that output as a given rather than re-deriving it (this
     stage is pure consumption, per this round's scope).
  2. "An exposure report" -- `output.exposure.build_exposure_report`.
  3. "A short rationale per lineup" -- `output.rationale.build_lineup_rationales`.
  4. "CSV export in DraftKings' bulk-upload format" -- `output.csv_export.export_lineups_to_dk_csv`.

`build_weekly_output` below bundles 2-4 (plus echoing 1) into one `WeeklyOutput` result from one
call, over the full real pipeline's actual objects: a projection pool's identities (for the DK
CSV's player-ID join, see `output/csv_export.py`'s "interface gap" note), the generated lineups,
and whatever `StackProfile`s are available for this week's relevant games (see `output/
rationale.py`'s "join-key gap" note for why "available" sometimes means "doesn't cover every
lineup's team").
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.correlation.stack_profile import StackProfile
from nfl_dfs.normalization.identity import PlayerIdentity
from nfl_dfs.optimizer.lineup import Lineup
from nfl_dfs.output.csv_export import DkCsvExport, export_lineups_to_dk_csv
from nfl_dfs.output.exposure import ExposureReport, build_exposure_report
from nfl_dfs.output.rationale import LineupRationale, build_lineup_rationales


@dataclass(frozen=True)
class WeeklyOutput:
    """All of PRD Section 8's deliverables for one weekly run, bundled together."""

    lineups: list[Lineup]
    exposure_report: ExposureReport
    rationales: list[LineupRationale]
    dk_csv: DkCsvExport

    def render_text_summary(self) -> str:
        """A single, plain-text rendering of everything but the raw CSV -- convenient for a quick
        terminal look or a live-integration-check transcript. The CSV itself
        (`self.dk_csv.csv_text`) is left out here since it's meant to be saved as a real `.csv`
        file, not read inline.
        """
        lines = [self.exposure_report.render_text(), "", "Rationales:"]
        for rationale in self.rationales:
            lines.append(f"- {rationale.text}")
        return "\n".join(lines)


def build_weekly_output(
    lineups: list[Lineup],
    identities: list[PlayerIdentity],
    stack_profiles: list[StackProfile] | None = None,
    *,
    include_contest_columns: bool = False,
    lineup_labels: list[str] | None = None,
) -> WeeklyOutput:
    """Produce the full weekly deliverable from the real pipeline's already-computed objects.

    `identities` must be the same week's full `PlayerIdentity` list the normalization stage
    produced (needed for the DK CSV's player-ID join -- see `output/csv_export.py`). `
    stack_profiles`, if omitted, produces every rationale via the "no StackProfile available"
    fallback path (`output/rationale.py`) rather than raising -- a caller who hasn't computed any
    `StackProfile`s yet still gets a complete, honestly-labeled `WeeklyOutput`.

    `lineup_labels` (2026-09-20, Chris: "have you not named the agents? I want to track
    performance for each one") -- positionally matched to `lineups`
    (`e.g. [r.agent.display_name for r in agent_results]`), threaded into each
    `LineupRationale.agent_label` (see that field's own docstring). `None` (the default) preserves
    the plain "Lineup N" labeling every existing caller already gets.
    """
    stack_profiles = stack_profiles if stack_profiles is not None else []

    exposure_report = build_exposure_report(lineups)
    rationales = build_lineup_rationales(lineups, stack_profiles, agent_labels=lineup_labels)
    dk_csv = export_lineups_to_dk_csv(
        lineups, identities, include_contest_columns=include_contest_columns
    )

    return WeeklyOutput(
        lineups=lineups,
        exposure_report=exposure_report,
        rationales=rationales,
        dk_csv=dk_csv,
    )
