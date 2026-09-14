import pandas as pd
import pytest

from nfl_dfs.ingestion.usage_share import (
    PRIOR_LEAGUE_AVERAGE,
    PRIOR_UNCONTESTED,
    ROLE_RB,
    ROLE_WR,
    UNCONTESTED_RB_PRIOR,
    RoleShareResult,
    adjusted_lead_rb_carry_share,
    aggregate_passer_week,
    aggregate_player_trailing_red_zone,
    aggregate_player_week,
    aggregate_player_week_red_zone,
    aggregate_team_week_volume,
    aggregate_team_week_volume_red_zone,
    blowout_volume_discount,
    compute_role_shares_for_week,
)


def _pbp_row(**kwargs) -> dict:
    row = {
        "season": 2026,
        "week": 1,
        "season_type": "REG",
        "posteam": "GB",
        "play_type": "run",
        "rusher_player_id": None,
        "rusher_player_name": None,
        "receiver_player_id": None,
        "receiver_player_name": None,
        "passer_player_id": None,
        "passer_player_name": None,
        "play_id": 1,
    }
    row.update(kwargs)
    return row


def _rush(week: int, team: str, player_id: str, name: str, n: int, play_id_start: int) -> list[dict]:
    return [
        _pbp_row(
            week=week,
            posteam=team,
            play_type="run",
            rusher_player_id=player_id,
            rusher_player_name=name,
            play_id=play_id_start + i,
        )
        for i in range(n)
    ]


def _target(week: int, team: str, player_id: str, name: str, n: int, play_id_start: int) -> list[dict]:
    return [
        _pbp_row(
            week=week,
            posteam=team,
            play_type="pass",
            receiver_player_id=player_id,
            receiver_player_name=name,
            play_id=play_id_start + i,
        )
        for i in range(n)
    ]


def _pass_attempts(week: int, team: str, player_id: str, name: str, n: int, play_id_start: int) -> list[dict]:
    """A QB's dropback pass attempts (no receiver credited here -- see aggregate_passer_week's
    docstring) -- used to establish a rusher_player_id as a real passer for the ADR-0020 Decision 1c
    QB-exclusion check. Kept separate from `_rush` (a mobile QB's scrambles are separate plays with
    rusher_player_id set, not passer_player_id)."""
    return [
        _pbp_row(
            week=week,
            posteam=team,
            play_type="pass",
            passer_player_id=player_id,
            passer_player_name=name,
            play_id=play_id_start + i,
        )
        for i in range(n)
    ]


# --------------------------------------------------------------------------------------------
# aggregate_team_week_volume / aggregate_player_week -- basic aggregation correctness
# --------------------------------------------------------------------------------------------


def test_aggregate_team_week_volume_counts_rushes_and_targets_separately():
    rows = (
        _rush(1, "GB", "RB1", "Back One", 10, 1)
        + _target(1, "GB", "WR1", "Wide One", 6, 100)
        + [_pbp_row(week=1, posteam="GB", play_type="pass", receiver_player_id=None, play_id=200)]  # sack/throwaway
        + [_pbp_row(week=1, posteam="GB", play_type="punt", play_id=201)]  # excluded entirely
    )
    out = aggregate_team_week_volume(pd.DataFrame(rows))
    gb = out[out["team"] == "GB"].iloc[0]
    assert gb["team_rush_attempts"] == 10
    assert gb["team_targets"] == 6


def test_aggregate_team_week_volume_normalizes_la_to_lar():
    rows = _rush(1, "LA", "RB1", "Back One", 5, 1)
    out = aggregate_team_week_volume(pd.DataFrame(rows))
    assert "LA" not in out["team"].values
    assert out[out["team"] == "LAR"].iloc[0]["team_rush_attempts"] == 5


def test_aggregate_player_week_computes_share_against_team_denominator():
    rows = (
        _rush(1, "GB", "RB1", "Back One", 8, 1)
        + _rush(1, "GB", "RB2", "Back Two", 2, 100)
        + _target(1, "GB", "WR1", "Wide One", 5, 200)
    )
    out = aggregate_player_week(pd.DataFrame(rows))
    rb1 = out[(out["player_id"] == "RB1") & (out["role"] == ROLE_RB)].iloc[0]
    assert rb1["volume"] == 8
    assert rb1["share"] == pytest.approx(0.8)  # 8 / (8 + 2)
    wr1 = out[(out["player_id"] == "WR1") & (out["role"] == ROLE_WR)].iloc[0]
    assert wr1["volume"] == 5
    assert wr1["share"] == pytest.approx(1.0)  # only target-getter that week


# --------------------------------------------------------------------------------------------
# Trailing-only identification + shrinkage -- the core behavior
# --------------------------------------------------------------------------------------------


def _team_week_frames_for(rows: list[dict]) -> tuple[pd.DataFrame, int]:
    df = pd.DataFrame(rows)
    return aggregate_player_week(df), df


def _result_for(rows: list[dict], team: str, role: str, target_week: int) -> RoleShareResult:
    df = pd.DataFrame(rows)
    player_week = aggregate_player_week(df)
    team_week_volume = aggregate_team_week_volume(df)
    passer_week = aggregate_passer_week(df)
    results = compute_role_shares_for_week(
        player_week,
        team_week_volume,
        passer_week,
        season=2026,
        target_week=target_week,
        all_teams=frozenset({team}),
        k=6.0,
    )
    return next(r for r in results if r.team == team and r.role == role)


def test_clear_bellcow_identified_confidently():
    # 4 trailing weeks, one RB with a dominant 15-of-20 share every week -> trailing_share=0.75,
    # trailing_volume=60 -- comfortably clears both RB gate legs (>0.40 share, >=15 volume). 4
    # weeks (not 3) so the shrinkage weight (w=4/10=0.4) is enough to pull the *blended* value
    # (0.4*0.75 + 0.6*0.504 = 0.602) into the bell-cow tier (>=0.60) -- role_tier is graded on the
    # blended value, not the raw trailing share (see module docstring).
    rows = []
    for wk, start in zip((1, 2, 3, 4), (1, 1000, 2000, 3000)):
        rows += _rush(wk, "DET", "RB_LEAD", "Lead Back", 15, start)
        rows += _rush(wk, "DET", "RB_BACKUP", "Backup Back", 5, start + 500)
    result = _result_for(rows, "DET", ROLE_RB, target_week=5)

    assert result.gate_passed is True
    assert result.identified is not None
    assert result.identified.player_id == "RB_LEAD"
    assert result.identified.trailing_share == pytest.approx(0.75)
    assert result.identified.trailing_volume == 60
    assert result.identified.role_tier == "bell_cow"
    # Candidates are sorted descending by role_share_blended -- the leader is first.
    assert result.candidates[0].player_id == "RB_LEAD"
    assert len(result.candidates) == 2


def test_genuine_committee_fails_the_gate():
    # 3 trailing weeks, three backs splitting 7/7/6 of 20 team carries each week -> top trailing
    # share = 21/60 = 0.35, below the 0.40 RB gate -- no single back should be trusted as "the"
    # lead RB, even though the top back clears the volume floor easily.
    rows = []
    for wk, start in zip((1, 2, 3), (1, 1000, 2000)):
        rows += _rush(wk, "COMM", "RB_A", "Back A", 7, start)
        rows += _rush(wk, "COMM", "RB_B", "Back B", 7, start + 100)
        rows += _rush(wk, "COMM", "RB_C", "Back C", 6, start + 200)
    result = _result_for(rows, "COMM", ROLE_RB, target_week=4)

    assert result.gate_passed is False
    assert result.identified is None
    assert "gate failed" in result.gate_reason
    # Candidates are still surfaced (for StackProfile-style ranking use) even though nobody is
    # confidently "identified".
    assert len(result.candidates) == 3
    assert result.candidates[0].player_id == "RB_A"  # ties broken by groupby order; still ranked


def test_early_season_small_sample_shrinks_toward_league_prior():
    # A single trailing week with an extreme, noisy share (15 of 21 carries) should be pulled
    # substantially toward the 0.504 RB league prior by the n=1 shrinkage weight (w = 1/7).
    # RB_OTHER carries 6 (> UNCONTESTED_RB_SECOND_CANDIDATE_MAX_CARRIES=5) so this stays a genuinely
    # generic-prior case, not the ADR-0020 uncontested case (that's its own test below).
    rows = _rush(1, "EARLY", "RB_HOT", "Hot Start", 15, 1) + _rush(1, "EARLY", "RB_OTHER", "Other", 6, 100)
    result = _result_for(rows, "EARLY", ROLE_RB, target_week=2)

    top = result.candidates[0]
    assert top.player_id == "RB_HOT"
    assert top.weeks_played == 1
    assert top.shrinkage_weight == pytest.approx(1 / 7)
    raw_share = 15 / 21
    expected_blended = (1 / 7) * raw_share + (6 / 7) * 0.504
    assert top.role_share_blended == pytest.approx(expected_blended)
    # Blended value sits strictly between the raw share and the prior, much closer to the prior.
    assert 0.504 < top.role_share_blended < raw_share
    assert abs(top.role_share_blended - 0.504) < abs(top.role_share_blended - raw_share)
    assert top.prior_used == PRIOR_LEAGUE_AVERAGE


def test_no_trailing_data_produces_no_candidates_and_fails_gate():
    rows = _rush(1, "BYE", "RB1", "Back One", 10, 1)
    # target_week=1 means trailing window is weeks < 1 -- empty, even though week-1 data exists.
    result = _result_for(rows, "BYE", ROLE_RB, target_week=1)
    assert result.candidates == []
    assert result.identified is None
    assert result.gate_passed is False
    assert "no trailing" in result.gate_reason


@pytest.mark.parametrize(
    "carries,filler_a,filler_b,expect_pass",
    [
        (8, 6, 6, False),  # RB1 share exactly 8/20=0.40 -- gate requires STRICTLY greater than 0.40
        (9, 6, 5, True),  # RB1 share 9/20=0.45 -- clears both legs
    ],
)
def test_rb_share_boundary_is_strict_greater_than(carries, filler_a, filler_b, expect_pass):
    # Two trailing weeks (team total 20/week: RB1 + two other backs splitting the remainder, so
    # RB1 stays the plurality leader at every split -- a two-player split would hand the *other*
    # back a 60% share whenever RB1 sits at the 40% boundary, which would test the wrong player).
    # trailing_volume = 2 * carries (16 or 18) -- both clear the >=15 volume floor either way.
    rows = []
    for wk, start in zip((1, 2), (1, 1000)):
        rows += _rush(wk, "BND", "RB1", "Boundary Back", carries, start)
        rows += _rush(wk, "BND", "RB2", "Filler A", filler_a, start + 500)
        rows += _rush(wk, "BND", "RB3", "Filler B", filler_b, start + 700)
    result = _result_for(rows, "BND", ROLE_RB, target_week=3)
    assert result.identified is None or result.identified.player_id == "RB1"
    assert result.gate_passed is expect_pass


def test_rb_volume_floor_boundary():
    # Exactly 15 trailing carries (the floor, inclusive) at a share comfortably above 0.40 should pass.
    rows = _rush(1, "VOL", "RB1", "Volume Back", 15, 1) + _rush(1, "VOL", "RB2", "Filler", 5, 100)
    result = _result_for(rows, "VOL", ROLE_RB, target_week=2)
    assert result.identified is not None
    assert result.identified.trailing_volume == 15

    rows_short = _rush(1, "VOL2", "RB1", "Volume Back", 14, 1) + _rush(1, "VOL2", "RB2", "Filler", 1, 100)
    result_short = _result_for(rows_short, "VOL2", ROLE_RB, target_week=2)
    # 14/15 = 0.933 share clears the share gate easily, but 14 < 15 volume floor -> fails.
    assert result_short.identified is None
    assert result_short.gate_passed is False


def test_wr1_gate_uses_its_own_thresholds():
    # WR gate: >18% share AND >=20 trailing targets. WR1 leads a 5-receiver target distribution
    # with a 25/100 = 0.25 trailing share -- this clears the WR gate (>0.18) but would FAIL the
    # RB gate's >0.40 threshold, specifically exercising that the WR-role gate uses its own
    # (looser) constants rather than reusing the RB ones.
    rows = []
    for wk, start in zip((1, 2), (1, 1000)):
        base = start
        rows += _target(wk, "WRT", "WR1", "Target Hog", 13 if wk == 1 else 12, base)
        rows += _target(wk, "WRT", "WR2", "Other A", 10, base + 200)
        rows += _target(wk, "WRT", "WR3", "Other B", 10, base + 400)
        rows += _target(wk, "WRT", "WR4", "Other C", 10, base + 600)
        rows += _target(wk, "WRT", "WR5", "Other D", 7 if wk == 1 else 8, base + 800)
    result = _result_for(rows, "WRT", ROLE_WR, target_week=3)
    assert result.identified is not None
    assert result.identified.player_id == "WR1"
    assert result.identified.trailing_share == pytest.approx(0.25)
    assert result.gate_passed is True
    assert result.identified.role_tier is None  # WR never gets a role tier
    # ADR-0020 scope discipline -- WR is untouched: always the generic prior, never "uncontested".
    assert result.identified.prior_used == PRIOR_LEAGUE_AVERAGE


# --------------------------------------------------------------------------------------------
# ADR-0020 Decision 2 -- ConcurrentActivityWindow (RB role only)
# --------------------------------------------------------------------------------------------


def test_concurrent_activity_window_excludes_blanked_established_candidates_weeks():
    # Reproduces the real 2025 LAC Hampton/Vidal pattern this ADR fixes: RB_LEAD (Hampton-like) is
    # the clear starter weeks 1-5, then goes to zero touches weeks 6-9 (a complete sever, e.g.
    # injury/IR -- not just a quiet game). RB_BACKUP (Vidal-like) is a token backup weeks 1-4
    # (zero carries -- not yet established), gets a handful of carries in week 5, then takes over as
    # the every-down back weeks 6-9. The naive (pre-ADR-0020) trailing average over all of weeks 1-9
    # would manufacture a false ~50/50 "committee" out of two sequential, non-overlapping stretches;
    # the fix should exclude weeks 6-9 entirely (RB_LEAD established via weeks 1-5, then blanked)
    # and compute the share only from the weeks both backs were genuinely concurrent (1-5).
    rows = []
    for wk, start in zip((1, 2, 3, 4), (1, 100, 200, 300)):
        rows += _rush(wk, "LAC2", "RB_LEAD", "Lead Back", 15, start)
    rows += _rush(5, "LAC2", "RB_LEAD", "Lead Back", 15, 400)
    rows += _rush(5, "LAC2", "RB_BACKUP", "Backup Back", 5, 450)
    for wk, start in zip((6, 7, 8, 9), (500, 600, 700, 800)):
        rows += _rush(wk, "LAC2", "RB_BACKUP", "Backup Back", 20, start)

    result = _result_for(rows, "LAC2", ROLE_RB, target_week=10)

    # weeks_played (RB role) reflects only the post-filter, concurrent weeks -- 5, not the raw 9.
    lead = next(c for c in result.candidates if c.player_id == "RB_LEAD")
    backup = next(c for c in result.candidates if c.player_id == "RB_BACKUP")
    assert lead.weeks_played == 5
    assert backup.weeks_played == 5
    assert lead.trailing_volume == 75  # 15 * 5 -- weeks 6-9's 0 carries are excluded, not averaged in
    assert backup.trailing_volume == 5  # only week 5's carries -- weeks 6-9 excluded too
    assert lead.trailing_team_volume == 80  # 75 + 5, weeks 1-5 only
    assert lead.trailing_share == pytest.approx(75 / 80)  # 0.9375 -- a dominant-starter picture...
    assert backup.trailing_share == pytest.approx(5 / 80)  # ...0.0625, not a false ~50/50 tie
    assert result.gate_passed is True
    assert result.identified is not None
    assert result.identified.player_id == "RB_LEAD"
    assert any("ConcurrentActivityWindow" in note for note in result.notes)


def test_concurrent_activity_window_does_not_apply_to_wr_role():
    # Scope discipline (ADR-0020): the same blanked-then-active pattern on the WR side must NOT be
    # filtered -- WR is explicitly out of scope this round.
    rows = []
    for wk in (1, 2, 3, 4, 5):
        rows += _target(wk, "WRSCOPE", "WR_LEAD", "Lead Wideout", 15, wk * 100)
    for wk in (6, 7, 8, 9):
        rows += _target(wk, "WRSCOPE", "WR_BACKUP", "Backup Wideout", 20, wk * 100 + 50)
    result = _result_for(rows, "WRSCOPE", ROLE_WR, target_week=10)
    wr_lead = next(c for c in result.candidates if c.player_id == "WR_LEAD")
    # No exclusion applied: weeks_played is the raw 9, and WR_LEAD's trailing_volume still includes
    # every week he actually played (75), not distorted by the RB-only filter.
    assert wr_lead.weeks_played == 9
    assert wr_lead.trailing_volume == 75
    assert result.notes == []


# --------------------------------------------------------------------------------------------
# ADR-0020 Decision 1 -- ConfirmedUncontestedPrior (RB role only)
# --------------------------------------------------------------------------------------------


def test_uncontested_rb_blends_toward_uncontested_prior_not_generic():
    # RB1 clears the identification gate comfortably; RB2 (the next-highest-volume non-QB
    # candidate) has exactly 5 trailing carries -- the validated "<=5" uncontested cut -- so RB1
    # should blend toward UNCONTESTED_RB_PRIOR (0.56), not the generic LEAGUE_PRIOR_SHARE (0.504).
    rows = _rush(1, "UNCON", "RB1", "Bell Cow", 18, 1) + _rush(1, "UNCON", "RB2", "Token Backup", 5, 100)
    result = _result_for(rows, "UNCON", ROLE_RB, target_week=2)

    top = result.candidates[0]
    assert top.player_id == "RB1"
    assert result.gate_passed is True
    assert top.prior_used == PRIOR_UNCONTESTED
    raw_share = 18 / 23
    weight = 1 / 7
    expected_blended = weight * raw_share + (1 - weight) * UNCONTESTED_RB_PRIOR
    assert top.role_share_blended == pytest.approx(expected_blended)
    assert any("uncontested_signal fired" in note for note in result.notes)


def test_uncontested_rb_fires_when_there_is_no_second_candidate_at_all():
    # A single RB with real volume and nobody else recorded any trailing carries -- "no second
    # candidate at all" also fires the uncontested signal per ADR-0020 Decision 1b.
    rows = _rush(1, "SOLO", "RB1", "Only Back", 18, 1)
    result = _result_for(rows, "SOLO", ROLE_RB, target_week=2)
    top = result.candidates[0]
    assert result.gate_passed is True
    assert top.prior_used == PRIOR_UNCONTESTED


def test_uncontested_rb_does_not_fire_when_second_candidate_clears_the_carry_floor():
    # RB2 has 6 trailing carries -- one above the <=5 uncontested cut -- so this should stay the
    # generic-prior case (regression coverage for the boundary itself).
    rows = _rush(1, "CONTEST", "RB1", "Leader", 18, 1) + _rush(1, "CONTEST", "RB2", "Real Competitor", 6, 100)
    result = _result_for(rows, "CONTEST", ROLE_RB, target_week=2)
    top = result.candidates[0]
    assert result.gate_passed is True
    assert top.prior_used == PRIOR_LEAGUE_AVERAGE


def test_mobile_qb_scrambles_are_excluded_from_the_second_candidate_check():
    # A mobile QB scrambles for real volume (7 rush attempts -- more than the genuine backup's 3)
    # and is a clearly established passer (10 trailing pass attempts, >= the 5-attempt exclusion
    # floor). Naively ranking by rush volume alone would make the QB "RB2" with 7 > 5 trailing
    # carries and wrongly suppress the uncontested signal; excluding him via the passer check
    # correctly finds the real backup (3 carries, <= 5) as RB2 instead, and the signal should fire.
    rows = (
        _rush(1, "QBEX", "RB1", "Lead Back", 18, 1)
        + _rush(1, "QBEX", "QB1", "Mobile QB", 7, 100)
        + _pass_attempts(1, "QBEX", "QB1", "Mobile QB", 10, 200)
        + _rush(1, "QBEX", "RB2", "Real Backup", 3, 300)
    )
    result = _result_for(rows, "QBEX", ROLE_RB, target_week=2)
    top = result.candidates[0]
    assert top.player_id == "RB1"
    assert result.gate_passed is True
    assert top.prior_used == PRIOR_UNCONTESTED


def test_genuinely_contested_committee_still_uses_generic_prior_when_gate_passes():
    # Regression coverage: a real, healthy two-back committee (RB2 comfortably above the <=5
    # uncontested cut every week) should behave exactly as before ADR-0020 -- generic league-average
    # prior, no ConcurrentActivityWindow exclusions (nobody is ever blanked).
    rows = []
    for wk, start in zip((1, 2, 3), (1, 1000, 2000)):
        rows += _rush(wk, "REALCOMM", "RB1", "Committee Lead", 20, start)
        rows += _rush(wk, "REALCOMM", "RB2", "Committee Two", 10, start + 500)
    result = _result_for(rows, "REALCOMM", ROLE_RB, target_week=4)

    top = result.candidates[0]
    assert top.player_id == "RB1"
    assert result.gate_passed is True
    assert result.notes == []  # no ConcurrentActivityWindow exclusions -- nobody was ever blanked
    assert top.prior_used == PRIOR_LEAGUE_AVERAGE
    raw_share = 60 / 90
    weight = 3 / 9
    expected_blended = weight * raw_share + (1 - weight) * 0.504
    assert top.role_share_blended == pytest.approx(expected_blended)


def test_compute_role_shares_for_week_returns_both_roles_for_every_requested_team():
    rows = _rush(1, "TWO", "RB1", "Back", 10, 1) + _target(1, "TWO", "WR1", "Wide", 5, 100)
    df = pd.DataFrame(rows)
    player_week = aggregate_player_week(df)
    team_week_volume = aggregate_team_week_volume(df)
    passer_week = aggregate_passer_week(df)
    results = compute_role_shares_for_week(
        player_week, team_week_volume, passer_week, season=2026, target_week=2, all_teams=frozenset({"TWO", "ZZZ"})
    )
    teams_roles = {(r.team, r.role) for r in results}
    assert teams_roles == {("TWO", ROLE_RB), ("TWO", ROLE_WR), ("ZZZ", ROLE_RB), ("ZZZ", ROLE_WR)}
    # A team with literally no rows at all still gets a (candidates=[], gate failed) result, not a
    # KeyError or a missing row.
    zzz_rb = next(r for r in results if r.team == "ZZZ" and r.role == ROLE_RB)
    assert zzz_rb.candidates == []
    assert zzz_rb.gate_passed is False


# --------------------------------------------------------------------------------------------
# BlowoutVolumeDiscount
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "abs_spread,expected",
    [
        (0.0, 1.00),
        (10.0, 1.00),  # inclusive lower band
        (10.01, 0.97),
        (14.0, 0.97),  # inclusive
        (14.01, 0.93),
        (28.0, 0.93),
    ],
)
def test_blowout_volume_discount_bands(abs_spread, expected):
    assert blowout_volume_discount(abs_spread) == pytest.approx(expected)


def test_blowout_volume_discount_rejects_negative_input():
    with pytest.raises(ValueError):
        blowout_volume_discount(-3.0)


def test_adjusted_lead_rb_carry_share_applies_discount_when_identified():
    rows = []
    for wk, start in zip((1, 2, 3), (1, 1000, 2000)):
        rows += _rush(wk, "DISC", "RB_LEAD", "Lead", 15, start)
        rows += _rush(wk, "DISC", "RB_BACKUP", "Backup", 5, start + 500)
    result = _result_for(rows, "DISC", ROLE_RB, target_week=4)
    assert result.gate_passed is True

    adjusted = adjusted_lead_rb_carry_share(result, pregame_spread=-17.5)
    assert adjusted == pytest.approx(result.identified.role_share_blended * 0.93)

    adjusted_small_spread = adjusted_lead_rb_carry_share(result, pregame_spread=3.0)
    assert adjusted_small_spread == pytest.approx(result.identified.role_share_blended * 1.00)


def test_adjusted_lead_rb_carry_share_returns_none_when_gate_fails():
    rows = []
    for wk, start in zip((1, 2, 3), (1, 1000, 2000)):
        rows += _rush(wk, "COMM2", "RB_A", "A", 7, start)
        rows += _rush(wk, "COMM2", "RB_B", "B", 7, start + 100)
        rows += _rush(wk, "COMM2", "RB_C", "C", 6, start + 200)
    result = _result_for(rows, "COMM2", ROLE_RB, target_week=4)
    assert result.gate_passed is False
    assert adjusted_lead_rb_carry_share(result, pregame_spread=20.0) is None


def test_adjusted_lead_rb_carry_share_rejects_wr_role():
    rows = _target(1, "WRX", "WR1", "Wide", 25, 1) + _target(1, "WRX", "WR2", "Other", 25, 100)
    result = _result_for(rows, "WRX", ROLE_WR, target_week=2)
    with pytest.raises(ValueError):
        adjusted_lead_rb_carry_share(result, pregame_spread=15.0)


# --------------------------------------------------------------------------------------------
# ADR-0022 Round A -- red zone usage (yardline_100 <= 20-filtered siblings)
# --------------------------------------------------------------------------------------------


def _rush_at(week: int, team: str, player_id: str, name: str, n: int, play_id_start: int, yardline_100: int) -> list[dict]:
    return [
        _pbp_row(
            week=week,
            posteam=team,
            play_type="run",
            rusher_player_id=player_id,
            rusher_player_name=name,
            play_id=play_id_start + i,
            yardline_100=yardline_100,
        )
        for i in range(n)
    ]


def _target_at(week: int, team: str, player_id: str, name: str, n: int, play_id_start: int, yardline_100: int) -> list[dict]:
    return [
        _pbp_row(
            week=week,
            posteam=team,
            play_type="pass",
            receiver_player_id=player_id,
            receiver_player_name=name,
            play_id=play_id_start + i,
            yardline_100=yardline_100,
        )
        for i in range(n)
    ]


def test_aggregate_team_week_volume_red_zone_only_counts_yardline_100_le_20():
    rows = (
        _rush_at(1, "GB", "RB1", "Back One", 3, 1, yardline_100=15)  # red zone
        + _rush_at(1, "GB", "RB1", "Back One", 5, 100, yardline_100=45)  # not red zone
        + _target_at(1, "GB", "WR1", "Wide One", 2, 200, yardline_100=20)  # boundary -- included (<=20)
        + _target_at(1, "GB", "WR1", "Wide One", 1, 300, yardline_100=21)  # just outside -- excluded
    )
    out = aggregate_team_week_volume_red_zone(pd.DataFrame(rows))
    gb = out[out["team"] == "GB"].iloc[0]
    assert gb["team_rush_attempts"] == 3
    assert gb["team_targets"] == 2


def test_aggregate_player_week_red_zone_computes_share_against_team_rz_denominator():
    rows = (
        _rush_at(1, "GB", "RB1", "Back One", 3, 1, yardline_100=10)
        + _rush_at(1, "GB", "RB2", "Back Two", 1, 100, yardline_100=5)
        + _rush_at(1, "GB", "RB1", "Back One", 10, 200, yardline_100=50)  # not red zone -- excluded
    )
    out = aggregate_player_week_red_zone(pd.DataFrame(rows))
    rb1 = out[(out["player_id"] == "RB1") & (out["role"] == ROLE_RB)].iloc[0]
    # Share is against the team's RED-ZONE volume (3+1=4), not whole-game volume (3+1+10=14).
    assert rb1["volume"] == 3
    assert rb1["share"] == pytest.approx(0.75)


def test_aggregate_player_trailing_red_zone_sums_across_completed_weeks_only():
    rows = (
        _rush_at(1, "DET", "RB_LEAD", "Lead Back", 4, 1, yardline_100=10)
        + _rush_at(1, "DET", "RB_OTHER", "Other Back", 1, 100, yardline_100=5)
        + _rush_at(2, "DET", "RB_LEAD", "Lead Back", 3, 200, yardline_100=8)
        + _rush_at(2, "DET", "RB_OTHER", "Other Back", 2, 300, yardline_100=12)
        + _rush_at(3, "DET", "RB_LEAD", "Lead Back", 99, 400, yardline_100=8)  # target week itself -- excluded
    )
    out = aggregate_player_trailing_red_zone(pd.DataFrame(rows), target_week=3)
    lead = out[out["player_id"] == "RB_LEAD"].iloc[0]
    other = out[out["player_id"] == "RB_OTHER"].iloc[0]

    assert lead["rz_trailing_volume"] == 7  # weeks 1-2 only: 4 + 3
    assert other["rz_trailing_volume"] == 3  # 1 + 2
    assert lead["rz_trailing_team_volume"] == 10  # team RZ carries weeks 1-2: (4+1) + (3+2)
    assert lead["rz_trailing_share"] == pytest.approx(0.7)
    assert other["rz_trailing_share"] == pytest.approx(0.3)


def test_aggregate_player_trailing_red_zone_empty_when_no_trailing_data():
    rows = _rush_at(1, "BYE", "RB1", "Back One", 5, 1, yardline_100=10)
    out = aggregate_player_trailing_red_zone(pd.DataFrame(rows), target_week=1)
    assert out.empty


def test_aggregate_player_trailing_red_zone_distinguishes_rb_and_wr_role_denominators():
    rows = (
        _rush_at(1, "MIX", "RB1", "Runner", 6, 1, yardline_100=10)
        + _target_at(1, "MIX", "WR1", "Catcher", 4, 100, yardline_100=10)
    )
    out = aggregate_player_trailing_red_zone(pd.DataFrame(rows), target_week=2)
    rb = out[out["role"] == ROLE_RB].iloc[0]
    wr = out[out["role"] == ROLE_WR].iloc[0]
    # RB's denominator is team RZ rush attempts (6), WR's is team RZ targets (4) -- each role gets
    # its own volume column, not a shared/combined denominator.
    assert rb["rz_trailing_team_volume"] == 6
    assert wr["rz_trailing_team_volume"] == 4
    assert rb["rz_trailing_share"] == pytest.approx(1.0)
    assert wr["rz_trailing_share"] == pytest.approx(1.0)
