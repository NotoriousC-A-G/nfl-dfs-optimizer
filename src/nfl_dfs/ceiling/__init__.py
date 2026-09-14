"""Ceiling signal data layer (PRD Section 6's newest construct, ADR-0028) -- not yet a live
multiplier, see `signals.py`'s module docstring for why."""

from nfl_dfs.ceiling.signals import (
    ADOT_MIN_TARGETS,
    BOOM_THRESHOLD,
    CEILING_SHRINKAGE_K,
    COMPONENT_A_SCALE,
    MIN_TRAILING_WEEKS,
    CeilingSignal,
    adot_ceiling_signals,
    component_a_multiplier,
    red_zone_ceiling_signals,
    role_share_ceiling_signals,
    trailing_red_zone_share_by_week,
)

__all__ = [
    "ADOT_MIN_TARGETS",
    "BOOM_THRESHOLD",
    "CEILING_SHRINKAGE_K",
    "COMPONENT_A_SCALE",
    "MIN_TRAILING_WEEKS",
    "CeilingSignal",
    "adot_ceiling_signals",
    "component_a_multiplier",
    "red_zone_ceiling_signals",
    "role_share_ceiling_signals",
    "trailing_red_zone_share_by_week",
]
