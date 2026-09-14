import json
from pathlib import Path

import pytest

from nfl_dfs.ingestion.draftkings import (
    CONTESTS_URL,
    DRAFTABLES_URL,
    SlateSelectionError,
    classic_draft_group_ids,
    fetch_classic_draft_group_id,
    parse_draftables,
    select_classic_slate,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_classic_draft_group_ids_filters_by_game_type():
    contests_payload = json.loads((FIXTURES / "draftkings_contests.json").read_text())
    ids = classic_draft_group_ids(contests_payload)
    assert ids == [153070, 153071, 153109]


def test_parse_draftables_shapes_source_players():
    payload = json.loads((FIXTURES / "draftkings_draftables.json").read_text())
    players = parse_draftables(payload)

    # Fixture has 15 raw draftable rows but only 12 unique playerDkIds (Jefferson, Achane, and
    # Bowers each carry a duplicate row for a second roster-slot eligibility -- see
    # parse_draftables' docstring on why this must be deduped, not passed through raw).
    assert len(players) == 12
    positions = {p.position for p in players}
    assert positions == {"QB", "RB", "WR", "TE", "DST"}

    jefferson = next(p for p in players if p.name == "Justin Jefferson")
    assert jefferson.native_id == "485454"
    assert jefferson.team == "MIN"
    assert jefferson.position == "WR"
    assert jefferson.jersey_number is None


def test_parse_draftables_dedupes_duplicate_roster_slot_rows_by_player_id():
    payload = json.loads((FIXTURES / "draftkings_draftables.json").read_text())
    players = parse_draftables(payload)
    native_ids = [p.native_id for p in players]
    assert len(native_ids) == len(set(native_ids))
    assert native_ids.count("485454") == 1


def test_parse_draftables_flags_unexpected_position_via_warning():
    payload = {"draftables": [{"playerDkId": 1, "displayName": "Mystery Player", "teamAbbreviation": "ARI", "position": "FLEX"}]}
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        players = parse_draftables(payload)

    assert len(players) == 1
    assert any("FLEX" in str(w.message) for w in caught)


# --- Slate-selection fix tests -----------------------------------------------------------------
# Urgent correctness fix (Data Integration Engineer): `fetch_classic_draft_group_id` used to pick
# whichever live Classic draftGroupId had the most total draftables rows, with no awareness of
# which games/days it covered. These fixtures represent several distinct candidate slates a real
# `getcontests` pull can return at once (including a deliberately incoherent one) so the corrected,
# slate-aware selection logic in `select_classic_slate` is verifiable without live network access.


def _contest(dg: int, name: str, game_type: str = "Classic") -> dict:
    return {"dg": dg, "gameType": game_type, "n": name}


def _row(player_id: int, name: str, team: str, position: str, comp_id: int, comp_name: str, start_time: str) -> dict:
    return {
        "playerDkId": player_id,
        "displayName": name,
        "teamAbbreviation": team,
        "position": position,
        "competition": {"competitionId": comp_id, "name": comp_name, "startTime": start_time},
    }


def _draftables_payload(rows: list[dict]) -> dict:
    return {"draftables": rows}


def _main_slate_draftables() -> dict:
    # 3 early Sunday games, all kicking off the same America/New_York calendar date -- a coherent
    # single-slate player pool the way a real Sunday main slate should look.
    return _draftables_payload(
        [
            _row(10, "Josh Allen", "BUF", "QB", 2001, "BUF @ MIA", "2026-09-13T17:00:00.0000000Z"),
            _row(11, "Tua Tagovailoa", "MIA", "QB", 2001, "BUF @ MIA", "2026-09-13T17:00:00.0000000Z"),
            _row(12, "Justin Jefferson", "MIN", "WR", 2002, "GB @ MIN", "2026-09-13T17:00:00.0000000Z"),
            _row(13, "Jordan Love", "GB", "QB", 2002, "GB @ MIN", "2026-09-13T17:00:00.0000000Z"),
            _row(14, "Justin Herbert", "LAC", "QB", 2003, "LAC @ LV", "2026-09-13T20:05:00.0000000Z"),
            _row(15, "Maxx Crosby", "LV", "DST", 2003, "LAC @ LV", "2026-09-13T20:05:00.0000000Z"),
        ]
    )


def _primetime_draftables() -> dict:
    # Live-reproduced shape from this week's actual `getcontests` pull (2026 wk1, Sunday evening):
    # a real, coherent-as-a-DK-product "Primetime" slate combining Sunday Night Football and
    # Monday Night Football -- two games on two different calendar days.
    return _draftables_payload(
        [
            _row(1, "Dak Prescott", "DAL", "QB", 1001, "DAL @ NYG", "2026-09-14T00:20:00.0000000Z"),
            _row(2, "Patrick Mahomes", "KC", "QB", 1002, "DEN @ KC", "2026-09-15T00:15:00.0000000Z"),
        ]
    )


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    """Routes `CONTESTS_URL` to a fixed contests payload and each `DRAFTABLES_URL` (by
    draftGroupId) to a per-dg draftables payload -- mirrors the real call sequence
    (`select_classic_slate` makes one `getcontests` call, then one `draftables` call per live
    Classic candidate) with no live network access.
    """

    def __init__(self, contests_payload: dict, draftables_by_dg: dict[int, dict]):
        self._contests_payload = contests_payload
        self._draftables_by_dg = draftables_by_dg

    def get(self, url: str, timeout: float | None = None) -> _FakeResponse:
        if url == CONTESTS_URL:
            return _FakeResponse(self._contests_payload)
        for dg, payload in self._draftables_by_dg.items():
            if url == DRAFTABLES_URL.format(draft_group_id=dg):
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected URL requested in test: {url}")


def test_select_classic_slate_reproduces_this_weeks_live_finding_no_main_slate_live():
    # Mirrors the actual live pull done for this fix (2026 wk1, Sunday evening, after the real
    # Sunday-afternoon main slate had already locked and dropped out of `getcontests` entirely):
    # only a combined Primetime (SNF+MNF) slate and an unreleased Mon-Thu slate are live. Neither
    # is the main slate -- this must raise loudly, not silently pick Primetime the way the old
    # "most players" heuristic did.
    contests_payload = {
        "Contests": [
            _contest(153071, "NFL $30K Wildcat [$10K to 1st] (Primetime)"),
            _contest(153109, "NFL $30K Flea Flicker [$10K to 1st] (Mon-Thu)"),
        ]
    }
    draftables_by_dg = {153071: _primetime_draftables(), 153109: _draftables_payload([])}
    session = _FakeSession(contests_payload, draftables_by_dg)

    with pytest.raises(SlateSelectionError, match="no live Classic slate looks like the Sunday main slate"):
        select_classic_slate(session=session)


def test_select_classic_slate_require_label_targets_primetime_explicitly():
    # Same live-reproduced fixtures as above -- Chris (or a script) can still deliberately target
    # the Primetime slate rather than being stuck with a hard "Main or nothing" tool.
    contests_payload = {
        "Contests": [
            _contest(153071, "NFL $30K Wildcat [$10K to 1st] (Primetime)"),
            _contest(153109, "NFL $30K Flea Flicker [$10K to 1st] (Mon-Thu)"),
        ]
    }
    draftables_by_dg = {153071: _primetime_draftables(), 153109: _draftables_payload([])}
    session = _FakeSession(contests_payload, draftables_by_dg)

    slate = select_classic_slate(session=session, require_label="Primetime")
    assert slate.draft_group_id == 153071
    assert slate.teams == {"DAL", "NYG", "KC", "DEN"}
    # Confirms this candidate really is incoherent as a "main slate" -- SNF Sunday + MNF Monday.
    assert len(slate.eastern_dates) == 2


def test_select_classic_slate_picks_the_coherent_unlabeled_candidate_over_excluded_non_main_labels():
    contests_payload = {
        "Contests": [
            # The main slate: no trailing parenthetical at all -- see module docstring on why this
            # is the plausible-but-not-live-confirmed pattern the label check is built around.
            _contest(200001, "NFL $1M Play-Action [Top Prize $100K]"),
            _contest(153071, "NFL $30K Wildcat [$10K to 1st] (Primetime)"),
            _contest(153109, "NFL $30K Flea Flicker [$10K to 1st] (Mon-Thu)"),
        ]
    }
    draftables_by_dg = {
        200001: _main_slate_draftables(),
        153071: _primetime_draftables(),
        153109: _draftables_payload([]),
    }
    session = _FakeSession(contests_payload, draftables_by_dg)

    slate = select_classic_slate(session=session)
    assert slate.draft_group_id == 200001
    assert slate.slate_label == ""
    assert len(slate.eastern_dates) == 1
    assert slate.teams == {"BUF", "MIA", "MIN", "GB", "LAC", "LV"}


def test_select_classic_slate_excludes_afternoon_only_label_confirmed_from_phase0_fixture():
    contests_payload = {
        "Contests": [
            _contest(200001, "NFL $1M Play-Action [Top Prize $100K]"),
            _contest(153070, "NFL $500K Afternoon Only Rush [$100K to 1st] (Afternoon Only)"),
        ]
    }
    draftables_by_dg = {
        200001: _main_slate_draftables(),
        153070: _draftables_payload(
            [_row(50, "Terry McLaurin", "WAS", "WR", 5001, "WAS @ PHI", "2026-09-13T20:25:00.0000000Z")]
        ),
    }
    session = _FakeSession(contests_payload, draftables_by_dg)

    slate = select_classic_slate(session=session)
    assert slate.draft_group_id == 200001


def test_select_classic_slate_excludes_incoherent_multiday_candidate_even_without_a_denylist_label():
    # A deliberately incoherent candidate: its label doesn't match any known non-main marker (so it
    # passes the label check), and it has strictly more players than the real main slate -- but its
    # games span two different calendar days, so it must be excluded by the day-coherence check
    # rather than silently accepted because "most players" or "no suspicious label" alone would
    # otherwise have picked it.
    incoherent_rows = [
        _row(20, "Player A", "SEA", "WR", 3001, "SEA @ PHI", "2026-09-12T00:15:00.0000000Z"),
        _row(21, "Player B", "PHI", "WR", 3001, "SEA @ PHI", "2026-09-12T00:15:00.0000000Z"),
    ] + [
        _row(30 + i, f"Bench Player {i}", "PHI", "WR", 3002, "BUF @ MIA", "2026-09-13T17:00:00.0000000Z")
        for i in range(20)
    ]
    contests_payload = {
        "Contests": [
            _contest(200001, "NFL $1M Play-Action [Top Prize $100K]"),
            _contest(200002, "NFL $500K Oddity [Top Prize $50K]"),
        ]
    }
    draftables_by_dg = {200001: _main_slate_draftables(), 200002: _draftables_payload(incoherent_rows)}
    session = _FakeSession(contests_payload, draftables_by_dg)

    assert len(incoherent_rows) > len(_main_slate_draftables()["draftables"])

    slate = select_classic_slate(session=session)
    assert slate.draft_group_id == 200001


def test_select_classic_slate_raises_when_multiple_coherent_main_candidates_are_live():
    contests_payload = {
        "Contests": [
            _contest(200001, "NFL $1M Play-Action [Top Prize $100K]"),
            _contest(200003, "NFL $2M Sunday Ticket [Top Prize $200K]"),
        ]
    }
    draftables_by_dg = {
        200001: _main_slate_draftables(),
        200003: _draftables_payload(
            [
                _row(40, "Deebo Samuel", "ARI", "WR", 4001, "ARI @ SF", "2026-09-13T20:05:00.0000000Z"),
                _row(41, "Brock Purdy", "SF", "QB", 4001, "ARI @ SF", "2026-09-13T20:05:00.0000000Z"),
            ]
        ),
    }
    session = _FakeSession(contests_payload, draftables_by_dg)

    with pytest.raises(SlateSelectionError, match="ambiguous"):
        select_classic_slate(session=session)


def test_select_classic_slate_raises_when_every_candidate_is_empty():
    contests_payload = {"Contests": [_contest(153109, "NFL $30K Flea Flicker [$10K to 1st] (Mon-Thu)")]}
    draftables_by_dg = {153109: _draftables_payload([])}
    session = _FakeSession(contests_payload, draftables_by_dg)

    with pytest.raises(SlateSelectionError, match="no live Classic draftGroupId returned any players"):
        select_classic_slate(session=session)


def test_fetch_classic_draft_group_id_still_returns_an_int_for_backward_compatibility():
    contests_payload = {"Contests": [_contest(200001, "NFL $1M Play-Action [Top Prize $100K]")]}
    draftables_by_dg = {200001: _main_slate_draftables()}
    session = _FakeSession(contests_payload, draftables_by_dg)

    dg = fetch_classic_draft_group_id(session=session)
    assert dg == 200001
    assert isinstance(dg, int)
