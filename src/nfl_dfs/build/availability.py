"""Read the Questionable/override decisions the live run saved with its slate snapshot (`redesign.availability`).

A snapshot from before the redesign has none. That is reported, never silently treated as "nobody is out": the packets then
say availability is unknown (the Q decisions are what turn a Questionable player into an out player in every pool).
"""

from __future__ import annotations

from typing import Any

from nfl_dfs.normalization.injury_lookup import AvailabilityDecision


def availability_from_snapshot(snapshot: dict[str, Any]) -> tuple[list[AvailabilityDecision], str | None]:
    """`(decisions, warning)`; `warning` is a sentence to print when the snapshot carries no decisions at all."""
    block = snapshot.get("redesign")
    if not isinstance(block, dict) or "availability" not in block:
        return [], "this snapshot has no saved Questionable/override decisions (it predates the redesign build): availability is UNKNOWN in the packets -- re-run the live script"
    return [AvailabilityDecision(**{k: d[k] for k in ("name", "team", "decision", "basis", "source")}) for d in block["availability"]], None
