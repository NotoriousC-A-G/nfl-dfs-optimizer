"""Assemble one `EvidencePacket` per game from signals the pipeline already computes.

Inputs are the slate snapshot's own shapes (`player_pool` = `PlayerDetailRecord`s as dicts,
`stack_profiles` as dicts) so this runs identically against a live in-memory run and against a saved
snapshot, plus the team-game proxy-metric table from `metrics.team_game_metrics`. Pure: no network,
no files. Nothing is invented -- every absent signal stays `None` and is named in `data_gaps`.
"""

from __future__ import annotations

from typing import Any, Iterable

import pandas as pd
from dataclasses import replace

from nfl_dfs.build.evidence.anchors import favorite_win_probability
from nfl_dfs.build.evidence.contracts import (
    SCHEMA_VERSION, AvailabilityItem, EvidencePacket, Lines, MetricValue, PlayerEvidence, TeamEvidence, with_sha,
)
from nfl_dfs.build.evidence.opportunity import GAP_NO_SNAPS, UNAVAILABLE_STATUSES, OpportunityTable, TeamOpportunity, redistribute
from nfl_dfs.build.evidence.metrics import DEFENSE_METRICS, OFFENSE_METRICS, season_to_date

MAX_PLAYERS_PER_TEAM = 10
GAP_UNIT_GRADES = "matchup unit grades are season-to-date and availability-unaware (a starter being out is not reflected in them)"
GAP_NO_INTERACTIONS = "unit interactions are not measured for 2026 (no participation data: no box counts, pressure flags or time to throw)"


def _get(d: Any, *path: str) -> Any:
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _num(v: Any, *keys: str) -> float | None:
    """A float, whether the field is a bare number or a dict carrying it under one of `keys`."""
    if isinstance(v, dict):
        for k in keys:
            if v.get(k) is not None:
                return float(v[k])
        return None
    return float(v) if v is not None else None


def _note(circ: Any) -> str | None:
    if not isinstance(circ, dict):
        return None
    for k in ("note", "summary", "assessment", "rationale"):
        if circ.get(k):
            return str(circ[k])[:400]
    return None


def _player_evidence(rec: dict, decision_by_key: dict[tuple[str, str], Any]) -> PlayerEvidence:
    ident = rec["identity"]
    own = rec.get("ownership") or {}
    inj = rec.get("injury")
    decision = decision_by_key.get((ident["display_name"], rec["team"]))
    return PlayerEvidence(
        canonical_id=ident["canonical_id"],
        name=ident["display_name"],
        team=rec["team"],
        position=rec["position"],
        salary=rec.get("salary"),
        projection=rec.get("projection"),
        ownership_pct=own.get("projected_ownership"),
        ownership_vs_baseline=own.get("ownership_vs_baseline"),
        is_chalk=bool(own.get("is_chalk", False)),
        is_leverage=bool(own.get("is_leverage", False)),
        ceiling_multiplier=_num(rec.get("ceiling_multiplier"), "multiplier", "value"),
        red_zone_discount=_num(rec.get("red_zone_role_security_discount"), "discount", "value"),
        carry_share_trailing=_get(rec, "usage", "red_zone", "carry_share_trailing"),
        target_share_trailing=_get(rec, "usage", "red_zone", "target_share_trailing"),
        injury_status=(inj.get("status") if isinstance(inj, dict) else None),
        status_after_q_pass=(decision.decision if decision is not None else None),
        circumstance_note=_note(rec.get("circumstance_assessment")),
        qb_designed_run_rate=_get(rec, "qb_rushing_profile", "designed_run_rate"),
    )


def _unavailable_status(rec: dict, decision_by_key: dict[tuple[str, str], Any]) -> str | None:
    """Why this pool player will not play (or None): a Q/override decision of excluded/barred, or a listed status that counts as out
    unless a decision cleared him. Questionable counts as out (ADR-0045)."""
    d = decision_by_key.get((rec["identity"]["display_name"], rec["team"]))
    if d is not None:
        return d.decision if d.decision in ("excluded", "barred", "out") else None
    inj = rec.get("injury")
    status = inj.get("status") if isinstance(inj, dict) else None
    return status if status in UNAVAILABLE_STATUSES else None


def _with_opportunity(pe: PlayerEvidence, shares: dict, team_opp: TeamOpportunity | None) -> PlayerEvidence:
    s = shares.get(pe.canonical_id)
    e = team_opp.expected.get(pe.canonical_id) if team_opp else None
    if s is None and e is None:
        return pe
    return replace(
        pe,
        carry_share_l4=s.carry_share if s else None, target_share_l4=s.target_share if s else None,
        touch_share_l4=s.touch_share if s else None, touch_share_min_l4=s.touch_share_min if s else None,
        carry_share_expected=e.carry_share if e else None, target_share_expected=e.target_share if e else None,
        opportunity_note=e.note if e else None,
    )


def _team_metric_values(
    tg: pd.DataFrame | None, teams: Iterable[str], season: int, through_week: int
) -> dict[str, dict[str, tuple[float | None, int | None]]]:
    if tg is None or tg.empty:
        return {}
    return {t: season_to_date(tg, t, season, through_week) for t in teams}


def _percentile(all_values: list[float], value: float | None) -> float | None:
    if value is None or not all_values:
        return None
    return sum(1 for v in all_values if v <= value) / len(all_values)


def build_evidence_packets(
    player_pool: list[dict],
    stack_profiles: list[dict],
    team_game_table: pd.DataFrame | None,
    *,
    season: int,
    week: int,
    availability: Iterable[Any] = (),
    weather_by_home_team: dict[str, dict[str, Any]] | None = None,
    as_of: str = "",
    opportunity: OpportunityTable | None = None,
) -> dict[str, EvidencePacket]:
    """`{game_id: EvidencePacket}`. `availability` are `AvailabilityDecision`s from the Q/override pass;
    `team_game_table` may be `None`/empty (the packet then records the missing metrics as a data gap)."""
    through_week = week - 1
    all_teams = sorted({sp["home_team"] for sp in stack_profiles} | {sp["away_team"] for sp in stack_profiles})
    league_metrics = _team_metric_values(team_game_table, all_teams, season, through_week)
    league_by_metric: dict[str, list[float]] = {}
    for per_team in league_metrics.values():
        for metric, (value, _n) in per_team.items():
            if value is not None:
                league_by_metric.setdefault(metric, []).append(value)

    availability = list(availability)
    decision_by_key = {(d.name, d.team): d for d in availability}
    by_team: dict[str, list[dict]] = {}
    for rec in player_pool:
        by_team.setdefault(rec["team"], []).append(rec)

    packets: dict[str, EvidencePacket] = {}
    for sp in stack_profiles:
        home, away = sp["home_team"], sp["away_team"]
        game_id = f"{away}@{home}"
        home_spread = float(sp["spread"])
        favorite = away if home_spread > 0 else home
        gaps = [GAP_UNIT_GRADES, GAP_NO_INTERACTIONS]

        def implied(team: str) -> float | None:
            return next((r.get("implied_total") for r in by_team.get(team, []) if r.get("implied_total") is not None), None)

        ih, ia = implied(home), implied(away)
        total = (ih + ia) if ih is not None and ia is not None else None
        if total is None:
            gaps.append("implied totals are missing for this game")
        lines = Lines(home_spread, total, ih, ia, favorite, abs(home_spread), favorite_win_probability(abs(home_spread)))

        teams: dict[str, TeamEvidence] = {}
        for team, opp in ((home, away), (away, home)):
            recs = by_team.get(team, [])
            ge = next((_get(r, "game_environment", "composite_score") for r in recs if _get(r, "game_environment", "composite_score") is not None), None)
            stk = next((r["stack_context"] for r in recs if isinstance(r.get("stack_context"), dict)), {})
            lean = stk.get("game_script_lean") or {}
            metrics: dict[str, MetricValue] = {}
            def_metrics: dict[str, MetricValue] = {}
            for metric, (value, n) in league_metrics.get(team, {}).items():
                mv = MetricValue(value, n, _percentile(league_by_metric.get(metric, []), value))
                if metric in DEFENSE_METRICS:
                    def_metrics[metric] = mv
                elif metric in OFFENSE_METRICS:
                    metrics[metric] = mv
            if not metrics:
                gaps.append(f"{team}: no season-to-date proxy metrics (through week {through_week})")
            teams[team] = TeamEvidence(
                team=team, opponent=opp, game_environment_score=ge,
                single_team_viability=stk.get("single_team_viability"),
                game_script_stance=lean.get("stance"), game_script_intensity=lean.get("intensity"),
                implied_total=implied(team), metrics=metrics, def_metrics=def_metrics,
            )

        opp_by_team: dict[str, TeamOpportunity] = {}
        if opportunity is None:
            gaps.append("opportunity shares (carries/targets, who inherits a missing player's work) were not computed for this packet")
        else:
            gaps.append(GAP_NO_SNAPS)
            for team in (home, away):
                if team not in opportunity:
                    gaps.append(f"{team}: no play-by-play shares (no games before week {week})")
                    continue
                recs = by_team.get(team, [])
                out_players = {
                    r["identity"]["canonical_id"]: (r["identity"]["display_name"], _unavailable_status(r, decision_by_key))
                    for r in recs if _unavailable_status(r, decision_by_key)
                }
                listed = {r["identity"]["canonical_id"]: r["position"] for r in recs if r.get("salary") is not None}
                opp_by_team[team] = redistribute(team, opportunity[team], out_players, listed)
                gaps.extend(f"{team}: {u}" for u in opp_by_team[team].unassigned)

        players: list[PlayerEvidence] = []
        for team in (home, away):
            usable = [r for r in by_team.get(team, []) if r.get("salary") is not None and r.get("projection") is not None]
            usable.sort(key=lambda r: -r["projection"])
            skill = [r for r in usable if r["position"] != "DST"][: MAX_PLAYERS_PER_TEAM - 1]
            dst = [r for r in usable if r["position"] == "DST"][:1]
            players += [_with_opportunity(_player_evidence(r, decision_by_key), (opportunity or {}).get(team, {}), opp_by_team.get(team)) for r in skill + dst]
        owned = sum(1 for p in players if p.ownership_pct is not None)
        if owned < len(players):
            gaps.append(f"ownership is populated for {owned} of {len(players)} listed players")

        items: list[AvailabilityItem] = [
            AvailabilityItem(d.name, d.team, d.decision, d.basis, d.source, as_of)
            for d in availability if d.team in (home, away)
        ]
        for team in (home, away):
            for r in by_team.get(team, []):
                inj = r.get("injury")
                if isinstance(inj, dict) and inj.get("status"):
                    items.append(AvailabilityItem(
                        r["identity"]["display_name"], team, "listed",
                        f"RotoGrinders {inj['status']} ({inj.get('body_part', '?')}), impact {inj.get('impact_rating', '?')}",
                        "RotoGrinders", as_of,
                    ))
        weather = (weather_by_home_team or {}).get(home)
        if weather is None:
            gaps.append("no weather reading for this game")

        slate_window = next((r.get("slate_window") for r in by_team.get(home, []) if r.get("slate_window")), None)
        packets[game_id] = with_sha(EvidencePacket(
            SCHEMA_VERSION, game_id, season, week, home, away, slate_window, lines, teams, tuple(players),
            tuple(items), weather, tuple(gaps), vacated=tuple(v for t in (home, away) for v in opp_by_team.get(t, TeamOpportunity()).vacated),
        ))
    return packets
