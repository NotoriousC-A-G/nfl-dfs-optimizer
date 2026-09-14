import pytest

from nfl_dfs.ingestion.pff import (
    DEFENSE_COVERAGE_PATH,
    DEFENSE_SLOT_COVERAGE_PATH,
    RECEIVING_SUMMARY_PATH,
    PFFAPIError,
    PFFClient,
    PFFCredentialsError,
)
from nfl_dfs.matchup.coverage import DefenderAlignmentSnaps, ReceiverAlignmentShare


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._json_body


class FakeSession:
    """Stands in for requests.Session, returning queued responses per call."""

    def __init__(self, responses_by_path):
        # path -> list of FakeResponse, consumed in order (last one repeats)
        self._responses_by_path = responses_by_path
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        for path, responses in self._responses_by_path.items():
            if url.endswith(path):
                return responses.pop(0) if len(responses) > 1 else responses[0]
        raise AssertionError(f"Unexpected URL requested: {url}")


SLOT_COVERAGE_ROW = {
    "coverage_snaps": 20,
    "coverage_snaps_per_target": 4,
    "coverage_snaps_per_reception": 10,
    "player_id": 44485,
    "team": "PHI",
    "position": "CB",
    "targets": 5,
    "receptions": 2,
    "yards": 18,
    "yards_after_catch": 4,
    "touchdowns": 0,
    "interceptions": 0,
    "qb_rating_against": 60.0,
    "yards_per_coverage_snap": 0.9,
}

DEFENSE_COVERAGE_ROW = {
    "player_id": 44485,
    "team": "PHI",
    "snap_counts_coverage": 32,
    "targets": 8,
}

RECEIVING_SUMMARY_ROW = {
    "player_id": 12345,
    "team": "DAL",
    "position": "WR",
    "slot_snaps": 34,
    "slot_rate": 68.0,
    "wide_snaps": 16,
    "wide_rate": 32.0,
    "inline_snaps": 0,
    "inline_rate": 0.0,
    "routes": 50,
}


def make_client(responses_by_path, api_key="ak_test"):
    session = FakeSession(responses_by_path)
    return PFFClient(api_key=api_key, session=session), session


class TestPFFClientAuth:
    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("PFF_API_KEY", raising=False)
        with pytest.raises(PFFCredentialsError):
            PFFClient(session=FakeSession({}))

    def test_uses_env_var_when_no_explicit_key(self, monkeypatch):
        monkeypatch.setenv("PFF_API_KEY", "ak_from_env")
        client = PFFClient(session=FakeSession({}))
        assert client._api_key == "ak_from_env"


class TestPFFClientGet:
    def test_raises_pff_api_error_on_4xx(self):
        client, _ = make_client(
            {DEFENSE_SLOT_COVERAGE_PATH: [FakeResponse(403, {"request_id": "req_1"})]}
        )
        with pytest.raises(PFFAPIError) as exc_info:
            client.get(DEFENSE_SLOT_COVERAGE_PATH, {"league": "nfl"})
        assert exc_info.value.status_code == 403
        assert exc_info.value.request_id == "req_1"

    def test_retries_once_on_429_then_succeeds(self):
        client, session = make_client(
            {
                DEFENSE_SLOT_COVERAGE_PATH: [
                    FakeResponse(429, headers={"Retry-After": "0"}),
                    FakeResponse(200, {"slot_coverages": []}),
                ]
            }
        )
        result = client.get(DEFENSE_SLOT_COVERAGE_PATH, {"league": "nfl"})
        assert result == {"slot_coverages": []}
        assert len(session.calls) == 2

    def test_gives_up_after_max_retries(self):
        client, _ = make_client(
            {
                DEFENSE_SLOT_COVERAGE_PATH: [
                    FakeResponse(503, headers={"Retry-After": "0"}),
                ]
            }
        )
        with pytest.raises(PFFAPIError):
            client.get(DEFENSE_SLOT_COVERAGE_PATH, {"league": "nfl"})


class TestFetchDefenderAlignmentSnaps:
    def test_derives_perimeter_snaps_from_total_minus_slot(self):
        client, _ = make_client(
            {
                DEFENSE_SLOT_COVERAGE_PATH: [FakeResponse(200, {"slot_coverages": [SLOT_COVERAGE_ROW]})],
                DEFENSE_COVERAGE_PATH: [FakeResponse(200, {"coverage_summary": [DEFENSE_COVERAGE_ROW]})],
            }
        )

        result = client.fetch_defender_alignment_snaps("nfl", 2025, 1)

        assert result == [
            DefenderAlignmentSnaps(native_id="44485", team="PHI", slot_snaps=20, perimeter_snaps=12)
        ]

    def test_skips_defender_missing_from_total_coverage_report(self):
        client, _ = make_client(
            {
                DEFENSE_SLOT_COVERAGE_PATH: [FakeResponse(200, {"slot_coverages": [SLOT_COVERAGE_ROW]})],
                DEFENSE_COVERAGE_PATH: [FakeResponse(200, {"coverage_summary": []})],
            }
        )

        assert client.fetch_defender_alignment_snaps("nfl", 2025, 1) == []

    def test_clamps_negative_derived_perimeter_snaps_to_zero(self):
        slot_row = {**SLOT_COVERAGE_ROW, "coverage_snaps": 50}
        total_row = {**DEFENSE_COVERAGE_ROW, "snap_counts_coverage": 32}
        client, _ = make_client(
            {
                DEFENSE_SLOT_COVERAGE_PATH: [FakeResponse(200, {"slot_coverages": [slot_row]})],
                DEFENSE_COVERAGE_PATH: [FakeResponse(200, {"coverage_summary": [total_row]})],
            }
        )

        [defender] = client.fetch_defender_alignment_snaps("nfl", 2025, 1)
        assert defender.perimeter_snaps == 0

    def test_returns_empty_when_slot_coverage_column_restricted(self):
        client, _ = make_client(
            {
                DEFENSE_SLOT_COVERAGE_PATH: [
                    FakeResponse(
                        200,
                        {"slot_coverages": [SLOT_COVERAGE_ROW], "restricted": ["coverage_snaps"]},
                    )
                ],
                DEFENSE_COVERAGE_PATH: [FakeResponse(200, {"coverage_summary": [DEFENSE_COVERAGE_ROW]})],
            }
        )

        assert client.fetch_defender_alignment_snaps("nfl", 2025, 1) == []


class TestFetchReceiverAlignmentShare:
    def test_computes_shares_from_slot_and_wide_snaps(self):
        client, _ = make_client(
            {RECEIVING_SUMMARY_PATH: [FakeResponse(200, {"receiving_summary": [RECEIVING_SUMMARY_ROW]})]}
        )

        result = client.fetch_receiver_alignment_share("nfl", 2025, 1)

        assert result == [ReceiverAlignmentShare(player_id="12345", slot_share=0.68, perimeter_share=0.32)]

    def test_skips_receiver_with_zero_alignment_snaps(self):
        zero_row = {**RECEIVING_SUMMARY_ROW, "slot_snaps": 0, "wide_snaps": 0}
        client, _ = make_client(
            {RECEIVING_SUMMARY_PATH: [FakeResponse(200, {"receiving_summary": [zero_row]})]}
        )

        assert client.fetch_receiver_alignment_share("nfl", 2025, 1) == []

    def test_returns_empty_when_wide_snaps_restricted(self):
        client, _ = make_client(
            {
                RECEIVING_SUMMARY_PATH: [
                    FakeResponse(
                        200,
                        {"receiving_summary": [RECEIVING_SUMMARY_ROW], "restricted": ["wide_snaps"]},
                    )
                ]
            }
        )

        assert client.fetch_receiver_alignment_share("nfl", 2025, 1) == []
