"""The **evidence packet**: everything the numbers can say about one game (see `CONTEXT.md`).

Built in code from signals the pipeline already computes (the slate snapshot's per-player records, the
stack profiles, play-by-play proxy metrics, the Q/override decisions). It is *evidence for a thesis, not
a conclusion*: no opinions, only values, their sample sizes, where they came from, and an explicit list
of what is missing. A game analyst may only cite what is in here -- `flatten` produces the set of
citeable keys, and the thesis validator rejects any claim or question that points elsewhere.

Every nullable field is `None` when the signal genuinely doesn't apply or wasn't computed (never a
fabricated value), and `data_gaps` says so in words. The packet is hashed (`packet_sha`) so a thesis, a
cache entry and the post-mortem all refer to exactly the evidence that was used.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from nfl_dfs.build.evidence.opportunity import VacatedRole

SCHEMA_VERSION = 2


@dataclass(frozen=True)
class MetricValue:
    value: float | None
    n: int | None  # plays/dropbacks/games behind the value
    league_percentile: float | None = None  # where this value sits among league team-games (0-1)


@dataclass(frozen=True)
class Lines:
    home_spread: float  # home team's spread; > 0 means the home team is the underdog
    total: float | None
    implied_home: float | None
    implied_away: float | None
    favorite: str
    abs_spread: float
    favorite_win_probability: float  # code-computed from the spread (normal margin model, draft)


@dataclass(frozen=True)
class TeamEvidence:
    team: str
    opponent: str
    game_environment_score: float | None
    single_team_viability: float | None
    game_script_stance: str | None  # "favorite" | "underdog" | ...
    game_script_intensity: float | None
    implied_total: float | None
    metrics: dict[str, MetricValue] = field(default_factory=dict)  # offense proxies, season to date
    def_metrics: dict[str, MetricValue] = field(default_factory=dict)  # pass rush generated (evidence only)


@dataclass(frozen=True)
class PlayerEvidence:
    canonical_id: str
    name: str
    team: str
    position: str
    salary: int | None
    projection: float | None
    ownership_pct: float | None
    ownership_vs_baseline: float | None
    is_chalk: bool
    is_leverage: bool
    ceiling_multiplier: float | None
    red_zone_discount: float | None
    carry_share_trailing: float | None
    target_share_trailing: float | None
    injury_status: str | None
    status_after_q_pass: str | None  # e.g. "Q_CLEARED", "BARRED", "OUT" from the Q/override pass
    circumstance_note: str | None
    qb_designed_run_rate: float | None = None
    # Opportunity (play-by-play, last 4 team games; `evidence/opportunity.py`). Carries/targets only -- no snap or route data.
    carry_share_l4: float | None = None
    target_share_l4: float | None = None
    touch_share_l4: float | None = None  # (carries + targets) share
    touch_share_min_l4: float | None = None  # lowest single-game touch share: the floor-role stability measure
    carry_share_expected: float | None = None  # trailing + his portion of what unavailable teammates leave behind
    target_share_expected: float | None = None
    opportunity_note: str | None = None


@dataclass(frozen=True)
class AvailabilityItem:
    name: str
    team: str
    decision: str  # "cleared" | "excluded" | "barred" | "out"
    basis: str
    source: str
    as_of: str


@dataclass(frozen=True)
class EvidencePacket:
    schema_version: int
    game_id: str  # "AWAY@HOME"
    season: int
    week: int
    home: str
    away: str
    slate_window: str | None
    lines: Lines
    teams: dict[str, TeamEvidence]
    players: tuple[PlayerEvidence, ...]  # at most 10 per team (top by projection, plus the DST)
    availability: tuple[AvailabilityItem, ...]
    weather: dict[str, Any] | None
    data_gaps: tuple[str, ...]
    interactions: tuple[dict[str, Any], ...] = ()  # measured unit interactions -- empty until measured
    vacated: tuple[VacatedRole, ...] = ()  # roles held by players who will not play, with the carry/target share they leave
    packet_sha: str = ""


def _body(packet: EvidencePacket) -> dict[str, Any]:
    d = asdict(packet)
    d.pop("packet_sha", None)
    return d


def with_sha(packet: EvidencePacket) -> EvidencePacket:
    """Return `packet` with `packet_sha` set to the sha256 of its canonical JSON (sorted keys)."""
    blob = json.dumps(_body(packet), sort_keys=True, default=str).encode()
    return replace(packet, packet_sha=hashlib.sha256(blob).hexdigest())


def flatten(packet: EvidencePacket) -> dict[str, Any]:
    """Dotted-path view of the packet: the set of keys a thesis may cite. Layout:

    - `lines.<field>`, `weather.<field>`, `meta.packet_sha`
    - `units.<TEAM>.<metric>` (value) and `units.<TEAM>.<metric>.n` / `.pct` (sample size, league percentile);
      defense-side metrics are `units.<TEAM>.def_sack_rate` etc.
    - `teams.<TEAM>.<field>` (environment score, implied total, script stance, ...)
    - `players.<canonical_id>.<field>`
    - `availability.<TEAM>.<index>.<field>`
    """
    out: dict[str, Any] = {"meta.packet_sha": packet.packet_sha}
    for k, v in asdict(packet.lines).items():
        out[f"lines.{k}"] = v
    for team, te in packet.teams.items():
        for k in ("opponent", "game_environment_score", "single_team_viability", "game_script_stance", "game_script_intensity", "implied_total"):
            out[f"teams.{team}.{k}"] = getattr(te, k)
        for group in (te.metrics, te.def_metrics):
            for metric, mv in group.items():
                out[f"units.{team}.{metric}"] = mv.value
                out[f"units.{team}.{metric}.n"] = mv.n
                out[f"units.{team}.{metric}.pct"] = mv.league_percentile
    for p in packet.players:
        for k, v in asdict(p).items():
            if k != "canonical_id":
                out[f"players.{p.canonical_id}.{k}"] = v
    by_team: dict[str, int] = {}
    for a in packet.availability:
        i = by_team.get(a.team, 0)
        by_team[a.team] = i + 1
        for k, v in asdict(a).items():
            out[f"availability.{a.team}.{i}.{k}"] = v
    by_vac: dict[str, int] = {}
    for v in packet.vacated:
        i = by_vac.get(v.team, 0)
        by_vac[v.team] = i + 1
        for k, val in asdict(v).items():
            out[f"vacated.{v.team}.{i}.{k}"] = val
    if packet.weather:
        for k, v in packet.weather.items():
            out[f"weather.{k}"] = v
    return out


def packet_keys(packet: EvidencePacket) -> set[str]:
    """Keys a claim/question may cite (only fields with a real, non-null value)."""
    return {k for k, v in flatten(packet).items() if v is not None}
