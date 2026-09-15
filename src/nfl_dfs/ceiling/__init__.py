"""Ceiling signal data layer (PRD Section 6's newest construct, ADR-0028) -- not yet a live
multiplier, see `signals.py`'s module docstring for why."""

from nfl_dfs.ceiling.signals import (
    ADOT_MIN_TARGETS,
    BOOM_THRESHOLD,
    CEILING_SHRINKAGE_K,
    COMPONENT_A_SCALE,
    EXPLOSIVE_RUSH_YARDS_THRESHOLD,
    MIN_TRAILING_WEEKS,
    QB_DESIGNED_RUN_MIN_TRAILING_VOLUME,
    QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME,
    WR_RED_ZONE_ROLE_SECURITY_DISCOUNT_BANDS,
    CeilingSignal,
    adot_ceiling_signals,
    component_a_multiplier,
    qb_explosive_rush_rate_signals,
    qb_rushing_ceiling_signals,
    red_zone_ceiling_signals,
    role_share_ceiling_signals,
    trailing_red_zone_share_by_week,
    wr_red_zone_role_security_discount,
)

__all__ = [
    "ADOT_MIN_TARGETS",
    "BOOM_THRESHOLD",
    "CEILING_SHRINKAGE_K",
    "COMPONENT_A_SCALE",
    "EXPLOSIVE_RUSH_YARDS_THRESHOLD",
    "MIN_TRAILING_WEEKS",
    "QB_DESIGNED_RUN_MIN_TRAILING_VOLUME",
    "QB_EXPLOSIVE_RUSH_MIN_TRAILING_VOLUME",
    "WR_RED_ZONE_ROLE_SECURITY_DISCOUNT_BANDS",
    "CeilingSignal",
    "adot_ceiling_signals",
    "component_a_multiplier",
    "qb_explosive_rush_rate_signals",
    "qb_rushing_ceiling_signals",
    "red_zone_ceiling_signals",
    "role_share_ceiling_signals",
    "trailing_red_zone_share_by_week",
    "wr_red_zone_role_security_discount",
]
