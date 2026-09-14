"""PFF Developer API client and coverage-alignment ingestion (ADR-0001, ADR-0022).

Wraps `GET` requests against `api.pff.com`'s `/v1/facet/*` report endpoints
(https://developer.pff.com/reference/), gated by a PFF Pro API key.
"""

from __future__ import annotations

import os
import time
from typing import Any, Mapping, Optional

import requests

from nfl_dfs.matchup.coverage import DefenderAlignmentSnaps, ReceiverAlignmentShare

PFF_API_BASE_URL = "https://api.pff.com"

DEFENSE_SLOT_COVERAGE_PATH = "/v1/facet/signature/defense/slot_coverage"
DEFENSE_COVERAGE_PATH = "/v1/facet/defense/coverage"
RECEIVING_SUMMARY_PATH = "/v1/facet/receiving/summary"
GAMES_PATH = "/v1/games"

# PFF's documented client retries these statuses (respecting Retry-After) up
# to twice; we mirror that rather than inventing a full backoff policy.
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 2
_DEFAULT_RETRY_AFTER_SECONDS = 1.0


class PFFCredentialsError(RuntimeError):
    """Raised when no PFF API key is available (arg or PFF_API_KEY env var)."""


class PFFAPIError(RuntimeError):
    """Raised for a non-2xx response from the PFF Developer API."""

    def __init__(self, status_code: int, request_id: Optional[str] = None, body: Any = None):
        self.status_code = status_code
        self.request_id = request_id
        self.body = body
        message = f"PFF API returned {status_code}"
        if request_id:
            message += f" (request_id={request_id})"
        super().__init__(message)


class PFFClient:
    """Thin authenticated client for the PFF Developer API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        session: Optional[requests.Session] = None,
        base_url: str = PFF_API_BASE_URL,
    ):
        self._api_key = api_key or os.environ.get("PFF_API_KEY")
        if not self._api_key:
            raise PFFCredentialsError(
                "No PFF API key provided. Pass api_key= or set the PFF_API_KEY "
                "environment variable to a key from www.pff.com/account/api-keys."
            )
        self._session = session or requests.Session()
        self._base_url = base_url

    def get(self, path: str, params: Mapping[str, Any]) -> dict:
        """Authenticated GET against `path`, returning the parsed JSON body.

        Retries a bounded number of times on PFF's documented transient
        statuses (429/500/502/503/504), respecting `Retry-After` when present.
        """
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        attempt = 0
        while True:
            response = self._session.get(url, params=dict(params), headers=headers, timeout=30)
            if response.status_code < 400:
                return response.json()

            if response.status_code in _RETRYABLE_STATUSES and attempt < _MAX_RETRIES:
                attempt += 1
                time.sleep(_retry_after_seconds(response))
                continue

            request_id = _extract_request_id(response)
            raise PFFAPIError(response.status_code, request_id=request_id, body=_safe_json(response))

    def fetch_defender_alignment_snaps(
        self, league: str, season: int, week: int
    ) -> list[DefenderAlignmentSnaps]:
        """Per-defender slot/perimeter coverage snaps for one league-season-week.

        Combines `/v1/facet/signature/defense/slot_coverage` (slot snaps) with
        `/v1/facet/defense/coverage` (total coverage snaps) to derive perimeter
        snaps as the difference — see ADR-0001.
        """
        params = {"league": league, "season": season, "week": week}

        slot_payload = self.get(DEFENSE_SLOT_COVERAGE_PATH, params)
        slot_rows = _report_rows(
            slot_payload, "slot_coverages", required_fields=("player_id", "team", "coverage_snaps")
        )

        total_payload = self.get(DEFENSE_COVERAGE_PATH, params)
        total_rows = _report_rows(
            total_payload, "coverage_summary", required_fields=("player_id", "snap_counts_coverage")
        )
        total_coverage_snaps_by_player = {
            row["player_id"]: row["snap_counts_coverage"]
            for row in total_rows
            if row.get("snap_counts_coverage") is not None
        }

        results = []
        for row in slot_rows:
            player_id = row.get("player_id")
            team = row.get("team")
            slot_snaps = row.get("coverage_snaps") or 0
            if player_id is None or team is None:
                continue

            total_snaps = total_coverage_snaps_by_player.get(player_id)
            if total_snaps is None:
                # Can't derive perimeter snaps without a matching total-coverage row.
                continue

            perimeter_snaps = max(total_snaps - slot_snaps, 0)
            results.append(
                DefenderAlignmentSnaps(
                    native_id=str(player_id),
                    team=str(team),
                    slot_snaps=int(slot_snaps),
                    perimeter_snaps=int(perimeter_snaps),
                )
            )
        return results

    def fetch_receiver_alignment_share(
        self, league: str, season: int, week: int
    ) -> list[ReceiverAlignmentShare]:
        """Per-receiver slot/perimeter snap share for one league-season-week.

        `GET /v1/facet/receiving/summary` — PFF's alignment vocabulary is
        `slot`/`wide`/`inline`; `wide_snaps` maps to `perimeter_share` here
        (`inline`, in-line tight end alignment, is unused) — see ADR-0001.
        """
        params = {"league": league, "season": season, "week": week}
        payload = self.get(RECEIVING_SUMMARY_PATH, params)
        rows = _report_rows(
            payload, "receiving_summary", required_fields=("player_id", "slot_snaps", "wide_snaps")
        )

        results = []
        for row in rows:
            player_id = row.get("player_id")
            slot_snaps = row.get("slot_snaps") or 0
            wide_snaps = row.get("wide_snaps") or 0
            total = slot_snaps + wide_snaps
            if player_id is None or total <= 0:
                continue

            results.append(
                ReceiverAlignmentShare(
                    player_id=str(player_id),
                    slot_share=slot_snaps / total,
                    perimeter_share=wide_snaps / total,
                )
            )
        return results


def _report_rows(payload: dict, envelope_key: str, required_fields: tuple) -> list[dict]:
    """Return a report's rows, or `[]` if any required field was entitlement-restricted."""
    restricted = set(payload.get("restricted", ()))
    if restricted.intersection(required_fields):
        return []
    return payload.get(envelope_key) or []


def _retry_after_seconds(response: requests.Response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return _DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(float(retry_after), 0.0)
    except ValueError:
        return _DEFAULT_RETRY_AFTER_SECONDS


def _extract_request_id(response: requests.Response) -> Optional[str]:
    body = _safe_json(response)
    if isinstance(body, dict):
        return body.get("request_id") or (body.get("details") or {}).get("request_id")
    return None


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None
