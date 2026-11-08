"""Opportunity evidence: where a team's carries and targets go, and where a missing player's share GOES.

Chris, 2026-10-07: for skill players what matters is opportunity (snap/route share, carries, targets), not who is a
"starter"; and an injury is a redistribution question -- where do his touches go -- not "who replaces him".

Two things are computed from play-by-play, per team, over the team's last `WINDOW` games:

1. **Trailing shares** per player: share of the team's carries, of its targets, and of its combined touches (carries +
   targets), pooled over the games HE PLAYED in the window (his volume over the team's volume in those same games --
   Chris, 2026-11-08: a share that divides by games he missed understates a player back from injury, e.g. Rice at 6.3%
   when he took 21% of the targets in the game he played), plus the *lowest single-game* touch share over those games --
   the stability measure behind a floor role (an opportunity that held every week, not one big week). `games_played` and
   `window_games` ride along so the reader sees how thin the sample is. "Played" = a snap-count entry (`played_weeks`); with
   none supplied the old behaviour applies (the whole window), and a player absent from the snap data counts only the
   weeks he had a touch.
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

from dataclasses import dataclass, field, replace

import pandas as pd

from nfl_dfs.normalization.team_aliases import normalize_team

WINDOW = 4  # the team's most recent games
PRIOR_GAMES = 4  # games played before an absence that define a returning player's role
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
    games_played: int = 0  # games in the window he was on the field (snap count), or the whole window when no snap data was supplied
    window_games: int = 0  # games the team played in the window
    # A player back from an absence (on the field in none of the window's games, but with earlier games this season):
    # his role over his last PRIOR_GAMES games played, before he left. carry_share/target_share stay 0 -- he had none in the
    # window, and his teammates' window shares are what he left them. `redistribute` hands this role back when he is available.
    prior_carry_share: float = 0.0
    prior_target_share: float = 0.0
    prior_games: int = 0
    prior_last_week: int | None = None


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


def trailing_shares(
    pbp: pd.DataFrame, *, season: int, through_week: int, window: int = WINDOW,
    played_weeks: dict[str, set[int]] | None = None,
) -> OpportunityTable:
    """Per team, each player's shares over the games he played within the team's last `window` regular-season games up to
    `through_week`. `played_weeks` maps a player id (gsis) to the weeks he was on the field; without it every player is
    measured over the whole window (the earlier behaviour, which dilutes a player who missed games)."""
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
        team_c_by_w = {w: int((rush["week"] == w).sum()) for w in weeks}
        team_t_by_w = {w: int((targ["week"] == w).sum()) for w in weeks}
        team_touch_by_week = {w: team_c_by_w[w] + team_t_by_w[w] for w in weeks}
        players = set(carries.index.get_level_values(1)) | set(targets.index.get_level_values(1))
        shares: dict[str, PlayerShares] = {}
        for pid in players:
            c_by_w = {w: int(carries.get((w, pid), 0)) for w in weeks}
            t_by_w = {w: int(targets.get((w, pid), 0)) for w in weeks}
            if played_weeks is None:
                mine = list(weeks)
            elif pid in played_weeks:
                mine = [w for w in weeks if w in played_weeks[pid]]
            else:  # not in the snap data: only the weeks he demonstrably took part in
                mine = [w for w in weeks if c_by_w[w] + t_by_w[w] > 0]
            team_c = sum(team_c_by_w[w] for w in mine)
            team_t = sum(team_t_by_w[w] for w in mine)
            c, tg = sum(c_by_w[w] for w in mine), sum(t_by_w[w] for w in mine)
            weekly = [(c_by_w[w] + t_by_w[w]) / team_touch_by_week[w] for w in mine if team_touch_by_week[w]]
            shares[pid] = PlayerShares(
                pid, names.get(pid, pid),
                c / team_c if team_c else 0.0, tg / team_t if team_t else 0.0,
                (c + tg) / (team_c + team_t) if (team_c + team_t) else 0.0,
                min(weekly) if weekly else 0.0,
                sum(1 for w in weeks if c_by_w[w] + t_by_w[w] > 0),
                len(mine), len(weeks),
            )
        if played_weeks is not None:
            _add_returning(shares, tdf, weeks, played_weeks)
        out[normalize_team("nflverse_schedule", raw_team) or raw_team] = shares
    return out


def _add_returning(shares: dict[str, PlayerShares], tdf: pd.DataFrame, window_weeks: list, played_weeks: dict[str, set[int]]) -> None:
    """Players with earlier games this season for this team but none in the window: their pre-absence role (see PlayerShares)."""
    rush_all = tdf[(tdf["rush_attempt"] == 1) & (tdf.get("qb_kneel", 0) != 1) & (tdf.get("qb_scramble", 0) != 1) & tdf["rusher_player_id"].notna()]
    targ_all = tdf[(tdf["pass_attempt"] == 1) & tdf["receiver_player_id"].notna()]
    names: dict[str, str] = {}
    for df, idc, nc in ((rush_all, "rusher_player_id", "rusher_player_name"), (targ_all, "receiver_player_id", "receiver_player_name")):
        for pid, nm in zip(df[idc], df[nc]):
            names.setdefault(pid, nm)
    window = set(window_weeks)
    for pid in set(rush_all["rusher_player_id"]) | set(targ_all["receiver_player_id"]):
        weeks_played = played_weeks.get(pid)
        if not weeks_played or (weeks_played & window) or pid in shares:
            continue
        earlier = sorted(w for w in weeks_played if w < min(window))[-PRIOR_GAMES:]
        if not earlier:
            continue
        team_c = int(rush_all["week"].isin(earlier).sum())
        team_t = int(targ_all["week"].isin(earlier).sum())
        c = int(((rush_all["rusher_player_id"] == pid) & rush_all["week"].isin(earlier)).sum())
        tg = int(((targ_all["receiver_player_id"] == pid) & targ_all["week"].isin(earlier)).sum())
        shares[pid] = PlayerShares(
            pid, names.get(pid, pid), 0.0, 0.0, 0.0, 0.0, 0, 0, len(window_weeks),
            prior_carry_share=c / team_c if team_c else 0.0, prior_target_share=tg / team_t if team_t else 0.0,
            prior_games=len(earlier), prior_last_week=earlier[-1],
        )


def fetch_played_weeks(player_pool: list[dict], *, season: int, through_week: int) -> dict[str, set[int]] | None:
    """`{gsis id: weeks he was on the field}` from the nflverse snap counts, joined through each pool identity's pfr id.
    Returns None (with a warning -- the shares then fall back to the whole window) if the snap counts cannot be fetched."""
    import warnings

    try:
        import nfl_data_py as nfl

        snaps = nfl.import_snap_counts([season])
    except Exception as exc:  # network, 404 for an in-progress season, missing dependency
        warnings.warn(f"snap counts unavailable ({exc!r}); opportunity shares fall back to the whole window", stacklevel=2)
        return None
    snaps = snaps[(snaps["game_type"] == "REG") & (snaps["week"] <= through_week) & (snaps["offense_snaps"] > 0)]
    weeks_by_pfr = snaps.groupby("pfr_player_id")["week"].apply(lambda w: {int(x) for x in w}).to_dict()
    out: dict[str, set[int]] = {}
    for p in player_pool:
        ident = p.get("identity") or {}
        gsis, pfr = ident.get("nflverse_gsis_id"), ident.get("nflverse_pfr_id")
        if gsis and pfr in weeks_by_pfr:
            out[gsis] = weeks_by_pfr[pfr]
    return out


def fetch_depth_chart(season: int) -> list | None:
    """The latest nflverse depth chart (skill positions), or None with a warning if it cannot be fetched (the packets then say so)."""
    import warnings

    try:
        from nfl_dfs.ingestion.nflverse_depth_charts import fetch_current_depth_chart

        return fetch_current_depth_chart(season)
    except Exception as exc:  # network / schema
        warnings.warn(f"depth chart unavailable ({exc!r}); depth rank is blank in the packets", stacklevel=2)
        return None


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
    returners = {
        pid: s for pid, s in shares.items()
        if s.prior_games and pid in listed and pid not in unavailable and (s.prior_carry_share >= MIN_VACATED_CARRY_SHARE or s.prior_target_share >= MIN_VACATED_TARGET_SHARE)
    }
    orig_shares = shares
    if returners:
        shares = _hand_back_roles(shares, returners, unavailable)
    vacated: list[VacatedRole] = []
    for pid, (name, status) in unavailable.items():
        s = shares.get(pid)
        c, t = (s.carry_share, s.target_share) if s else (0.0, 0.0)
        if c >= MIN_VACATED_CARRY_SHARE or t >= MIN_VACATED_TARGET_SHARE:
            vacated.append(VacatedRole(pid, name, team, c, t, status))
    if not vacated:
        return TeamOpportunity(expected=_returner_expected(orig_shares, shares, returners, listed, unavailable)) if returners else TeamOpportunity()

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
    if returners:
        for pid, e in _returner_expected(orig_shares, shares, returners, listed, unavailable).items():
            prev = expected.get(pid)
            expected[pid] = e if prev is None or pid in returners else ExpectedShare(
                prev.carry_share, prev.target_share, prev.carry_gain + e.carry_gain, prev.target_gain + e.target_gain, prev.note + "; " + e.note)
    return TeamOpportunity(tuple(vacated), expected, tuple(unassigned))


def _hand_back_roles(shares: dict[str, PlayerShares], returners: dict[str, PlayerShares], unavailable: dict) -> dict[str, PlayerShares]:
    """Teammates' window shares were earned while the returner was away; with him back they shrink so the roles sum to 1 again.
    Returners get their pre-absence role as their share; everyone else (not out) is scaled by what the returners reclaim."""
    r_c = min(sum(r.prior_carry_share for r in returners.values()), 0.95)
    r_t = min(sum(r.prior_target_share for r in returners.values()), 0.95)
    out: dict[str, PlayerShares] = {}
    for pid, s in shares.items():
        if pid in returners:
            out[pid] = replace(s, carry_share=s.prior_carry_share, target_share=s.prior_target_share)
        elif pid in unavailable:
            out[pid] = s
        else:
            out[pid] = replace(s, carry_share=s.carry_share * (1 - r_c), target_share=s.target_share * (1 - r_t))
    return out


def _returner_expected(
    orig: dict[str, PlayerShares], scaled: dict[str, PlayerShares], returners: dict[str, PlayerShares], listed: dict[str, str], unavailable: dict,
) -> dict[str, ExpectedShare]:
    out: dict[str, ExpectedShare] = {}
    back = ", ".join(sorted(r.name for r in returners.values()))  # sorted: set iteration order varies per process and would change the packet hash
    for pid, r in returners.items():
        out[pid] = ExpectedShare(
            r.prior_carry_share, r.prior_target_share, r.prior_carry_share, r.prior_target_share,
            f"back after missing games: his role over his last {r.prior_games} games played (through week {r.prior_last_week}) was "
            f"{r.prior_carry_share:.0%} of carries / {r.prior_target_share:.0%} of targets, and teammates' recent shares are scaled down to make room "
            f"(baseline; he may be eased back in -- override with a reason)",
        )
    for pid in listed:
        if pid in returners or pid in unavailable or pid not in orig:
            continue
        dc, dt = scaled[pid].carry_share - orig[pid].carry_share, scaled[pid].target_share - orig[pid].target_share
        if abs(dc) < 0.005 and abs(dt) < 0.005:
            continue
        out[pid] = ExpectedShare(
            scaled[pid].carry_share, scaled[pid].target_share, dc, dt,
            f"{dc * 100:+.1f} pts of carries and {dt * 100:+.1f} pts of targets because {back} is back (his recent shares were earned while {back} was away)",
        )
    return out


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
