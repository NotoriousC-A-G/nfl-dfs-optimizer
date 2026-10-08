"""Opportunity evidence: where a team's carries and targets go, and where a missing player's share GOES.

Chris, 2026-10-07: for skill players what matters is opportunity (snap/route share, carries, targets), not who is a
"starter"; and an injury is a redistribution question -- where do his touches go -- not "who replaces him".

Two things are computed from play-by-play, per team, over the team's last `WINDOW` games:

1. **Trailing shares** per player: share of the team's carries, of its targets, and of its combined touches (carries +
   targets), pooled over the window, plus the *lowest single-game* touch share in the window -- the stability measure
   behind a floor role (an opportunity that held every week, not one big week).
2. **Redistribution** of the shares held by players who will not play (unavailable) to the players who will: a
   deterministic baseline, pro rata to each candidate's own trailing share with a small prior so a player who had no
   role yet can still inherit one. Carries go to running backs; targets go to every non-QB. Whatever share goes to
   players outside the listed pool is kept as "other" so listed players are not credited with all of it.

Disclosed draft, not backtested: the pro-rata rule, the 0.02 prior and the minimum-share thresholds. It is the
baseline an analyst may override with a written reason; it does not look at who did absorb work the last time the
same player was out (sample sizes that small are not worth a number).
Not included: snap share and routes run (the snap-count join needs separate plumbing) -- named in `data_gaps`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from nfl_dfs.normalization.team_aliases import normalize_team

WINDOW = 4  # the team's most recent games
MIN_VACATED_CARRY_SHARE = 0.05
MIN_VACATED_TARGET_SHARE = 0.04
PRIOR = 0.02  # weight a listed candidate gets on top of his own share (so a no-role player can inherit one)
UNAVAILABLE_STATUSES = {"O", "OUT", "IR", "D", "Q", "BARRED"}  # RotoGrinders + DK vocabulary; "Q" counts as out unless cleared

GAP_NO_SNAPS = "snap share and routes run are not in the packet (carries and targets from play-by-play only)"


@dataclass(frozen=True)
class PlayerShares:
    player_id: str  # nflverse gsis id (= the canonical_id)
    name: str
    carry_share: float
    target_share: float
    touch_share: float  # (carries + targets) / team (carries + targets), pooled over the window
    touch_share_min: float  # lowest single-game touch share in the window (0.0 if he missed a game the team played)
    games: int  # games in the window in which he had a carry or target


@dataclass(frozen=True)
class VacatedRole:
    canonical_id: str
    name: str
    team: str
    carry_share: float
    target_share: float
    status: str


@dataclass(frozen=True)
class ExpectedShare:
    carry_share: float  # trailing share + his portion of the vacated carries
    target_share: float
    carry_gain: float
    target_gain: float
    note: str


OpportunityTable = dict[str, dict[str, PlayerShares]]  # team -> player_id -> shares


def trailing_shares(pbp: pd.DataFrame, *, season: int, through_week: int, window: int = WINDOW) -> OpportunityTable:
    """Per team, each player's shares over the team's last `window` regular-season games up to `through_week`."""
    d = pbp[(pbp["season"] == season) & (pbp["week"] <= through_week)]
    if "season_type" in d.columns:
        d = d[d["season_type"] == "REG"]
    out: OpportunityTable = {}
    for raw_team, tdf in d.groupby("posteam"):
        weeks = sorted(tdf["week"].unique())[-window:]
        if not weeks:
            continue
        t = tdf[tdf["week"].isin(weeks)]
        rush = t[(t["rush_attempt"] == 1) & (t.get("qb_kneel", 0) != 1) & (t.get("qb_scramble", 0) != 1) & t["rusher_player_id"].notna()]
        targ = t[(t["pass_attempt"] == 1) & t["receiver_player_id"].notna()]
        carries = rush.groupby(["week", "rusher_player_id"]).size()
        targets = targ.groupby(["week", "receiver_player_id"]).size()
        names: dict[str, str] = {}
        for df, idc, nc in ((rush, "rusher_player_id", "rusher_player_name"), (targ, "receiver_player_id", "receiver_player_name")):
            for pid, nm in zip(df[idc], df[nc]):
                names.setdefault(pid, nm)
        team_c, team_t = len(rush), len(targ)
        team_touch_by_week = {w: int((rush["week"] == w).sum() + (targ["week"] == w).sum()) for w in weeks}
        players = set(carries.index.get_level_values(1)) | set(targets.index.get_level_values(1))
        shares: dict[str, PlayerShares] = {}
        for pid in players:
            c_by_w = {w: int(carries.get((w, pid), 0)) for w in weeks}
            t_by_w = {w: int(targets.get((w, pid), 0)) for w in weeks}
            c, tg = sum(c_by_w.values()), sum(t_by_w.values())
            weekly = [(c_by_w[w] + t_by_w[w]) / team_touch_by_week[w] for w in weeks if team_touch_by_week[w]]
            shares[pid] = PlayerShares(
                pid, names.get(pid, pid),
                c / team_c if team_c else 0.0, tg / team_t if team_t else 0.0,
                (c + tg) / (team_c + team_t) if (team_c + team_t) else 0.0,
                min(weekly) if weekly else 0.0,
                sum(1 for w in weeks if c_by_w[w] + t_by_w[w] > 0),
            )
        out[normalize_team("nflverse_schedule", raw_team) or raw_team] = shares
    return out


@dataclass(frozen=True)
class TeamOpportunity:
    vacated: tuple[VacatedRole, ...] = ()
    expected: dict[str, ExpectedShare] = field(default_factory=dict)  # player_id -> expected shares
    unassigned: tuple[str, ...] = ()  # roles whose share had nowhere to go (no eligible candidate)


def redistribute(
    team: str,
    shares: dict[str, PlayerShares],
    unavailable: dict[str, tuple[str, str]],  # player_id -> (name, status)
    listed: dict[str, str],  # player_id -> position, for the available, rosterable (salaried) players of the team
) -> TeamOpportunity:
    """Move the carry/target share of `unavailable` players to the available `listed` players (plus an 'other' remainder)."""
    vacated: list[VacatedRole] = []
    for pid, (name, status) in unavailable.items():
        s = shares.get(pid)
        c, t = (s.carry_share, s.target_share) if s else (0.0, 0.0)
        if c >= MIN_VACATED_CARRY_SHARE or t >= MIN_VACATED_TARGET_SHARE:
            vacated.append(VacatedRole(pid, name, team, c, t, status))
    if not vacated:
        return TeamOpportunity()

    out_ids = set(unavailable)
    v_carry = sum(v.carry_share for v in vacated if v.carry_share >= MIN_VACATED_CARRY_SHARE)
    v_target = sum(v.target_share for v in vacated if v.target_share >= MIN_VACATED_TARGET_SHARE)
    positions = {pid: pos for pid, pos in listed.items() if pid not in out_ids}

    def weights(kind: str, eligible_positions: tuple[str, ...]) -> tuple[dict[str, float], float]:
        w = {pid: (getattr(shares.get(pid), kind, 0.0) or 0.0) + PRIOR for pid, pos in positions.items() if pos in eligible_positions}
        # "other": everyone else on the team who touched the ball and is not listed, not out and not a QB (practice squad,
        # unsalaried depth) -- they absorb their pro rata portion too, so the listed players are not credited with all of it
        other = sum(
            getattr(s, kind) for pid, s in shares.items()
            if pid not in out_ids and pid not in positions
        )
        return w, max(other, 0.0)

    carry_w, carry_other = weights("carry_share", ("RB",))
    target_w, target_other = weights("target_share", ("WR", "TE", "RB"))
    unassigned: list[str] = []
    gains_c = {pid: 0.0 for pid in positions}
    gains_t = {pid: 0.0 for pid in positions}
    for amount, w, other, gains, label in ((v_carry, carry_w, carry_other, gains_c, "carries"), (v_target, target_w, target_other, gains_t, "targets")):
        if amount <= 0:
            continue
        total = sum(w.values()) + other
        if not w or total <= 0:
            unassigned.append(f"{label}: {amount:.1%} has no eligible listed candidate")
            continue
        for pid, wt in w.items():
            gains[pid] += amount * wt / total

    names = ", ".join(f"{v.name} ({v.status}, {v.carry_share:.0%} carries / {v.target_share:.0%} targets)" for v in vacated)
    expected: dict[str, ExpectedShare] = {}
    for pid in positions:
        gc, gt = gains_c.get(pid, 0.0), gains_t.get(pid, 0.0)
        base = shares.get(pid)
        if gc + gt <= 0:
            continue
        parts = []
        if gc > 0:
            parts.append(f"+{gc * 100:.1f} pts of the team's carries")
        if gt > 0:
            parts.append(f"+{gt * 100:.1f} pts of its targets")
        expected[pid] = ExpectedShare(
            (base.carry_share if base else 0.0) + gc, (base.target_share if base else 0.0) + gt, gc, gt,
            " and ".join(parts) + f" from {names} (baseline: pro rata to current share; draft)",
        )
    return TeamOpportunity(tuple(vacated), expected, tuple(unassigned))


def team_volume(pbp: pd.DataFrame, *, season: int, through_week: int, window: int = WINDOW) -> dict[str, tuple[float, float]]:
    """`{team: (carries per game, targets per game)}` over the team's last `window` regular-season games -- the volume a share is a share OF."""
    d = pbp[(pbp["season"] == season) & (pbp["week"] <= through_week)]
    if "season_type" in d.columns:
        d = d[d["season_type"] == "REG"]
    out: dict[str, tuple[float, float]] = {}
    for raw_team, tdf in d.groupby("posteam"):
        weeks = sorted(tdf["week"].unique())[-window:]
        if not weeks:
            continue
        t = tdf[tdf["week"].isin(weeks)]
        carries = int(((t["rush_attempt"] == 1) & (t.get("qb_kneel", 0) != 1) & (t.get("qb_scramble", 0) != 1) & t["rusher_player_id"].notna()).sum())
        targets = int(((t["pass_attempt"] == 1) & t["receiver_player_id"].notna()).sum())
        out[normalize_team("nflverse_schedule", raw_team) or raw_team] = (carries / len(weeks), targets / len(weeks))
    return out
