"""Thin async client for the BoG Business Online API (read-only endpoints)."""

from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from .auth import TokenProvider
from .errors import BogApiError, RateLimitedError

log = logging.getLogger(__name__)

STATEMENT_PAGE_SIZE = 1000
MAX_STATEMENT_PAGES = 100  # 100k records; protects against a runaway paging loop


def lower_keys(value: Any) -> Any:
    """Recursively lowercase dict keys.

    BoG mixes PascalCase (live responses, v1 docs) and camelCase (v2 docs), so
    every response is normalised to lowercase keys before use.
    """
    if isinstance(value, dict):
        return {str(k).lower(): lower_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [lower_keys(v) for v in value]
    return value


def _retry_after(response: httpx.Response, default: int = 60) -> int:
    raw = response.headers.get("Retry-After", "")
    try:
        return int(float(raw))
    except ValueError:
        return default


class BogClient:
    def __init__(self, http: httpx.AsyncClient, tokens: TokenProvider, api_base: str) -> None:
        self._http = http
        self._tokens = tokens
        self._base = api_base.rstrip("/")

    async def _get(self, path: str) -> Any:
        """GET ``path`` and return the parsed JSON (Decimal floats, original keys)."""
        url = f"{self._base}/{path.lstrip('/')}"
        for attempt in (1, 2):
            token = await self._tokens.get_token()
            try:
                response = await self._http.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Accept-Language": "en",
                    },
                )
            except httpx.HTTPError as exc:
                raise BogApiError(0, f"Network error: {exc}", path=path) from exc

            correlation_id = response.headers.get("x-correlationid")
            if response.status_code == 401 and attempt == 1:
                log.info("Got 401 from BoG, refreshing token and retrying")
                self._tokens.invalidate()
                continue
            if response.status_code == 429:
                raise RateLimitedError(f"BoG rate limit hit on {path}", _retry_after(response))
            if response.status_code >= 400:
                raise BogApiError(
                    response.status_code,
                    _error_message(response),
                    path=path,
                    correlation_id=correlation_id,
                )
            log.debug(
                "GET %s -> %s (x-correlationid=%s)", path, response.status_code, correlation_id
            )
            if not response.content:
                return None
            try:
                return json.loads(response.text, parse_float=Decimal)
            except ValueError as exc:
                raise BogApiError(
                    response.status_code,
                    "Response is not valid JSON",
                    path=path,
                    correlation_id=correlation_id,
                ) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    # --- endpoints -------------------------------------------------------------------

    async def get_balance(self, iban: str, currency: str) -> dict[str, Any]:
        """``GET accounts/{iban}/{ccy}`` -> ``{availablebalance, currentbalance, ...}``."""
        data = await self._get(f"accounts/{iban}/{currency}")
        if not isinstance(data, dict):
            raise BogApiError(200, "Unexpected balance response shape", path="accounts")
        return lower_keys(data)

    async def get_statement(
        self, iban: str, currency: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        """Full statement for ``[start, end]`` (all pages), raw records with original keys."""
        first = await self._get(
            f"v2/statement/{iban}/{currency}/{start.isoformat()}/{end.isoformat()}"
        )
        first_l = lower_keys(first) if isinstance(first, dict) else {}
        records: list[dict[str, Any]] = list(_records_of(first))
        statement_id = first_l.get("id")
        total = first_l.get("totalcount")
        last_page_len = len(records)

        page = 2
        while statement_id is not None and page <= MAX_STATEMENT_PAGES + 1:
            need_more = (
                len(records) < int(total)
                if total is not None
                else last_page_len >= STATEMENT_PAGE_SIZE
            )
            if not need_more:
                break
            data = await self._get(f"v2/statement/{iban}/{currency}/{statement_id}/{page}")
            chunk = list(_records_of(data))
            if not chunk:
                break
            records.extend(chunk)
            last_page_len = len(chunk)
            page += 1
        return records

    async def get_today_activities(self, iban: str, currency: str) -> list[dict[str, Any]]:
        """``GET v2/documents/todayactivities/{iban}/{ccy}`` -> raw records."""
        data = await self._get(f"v2/documents/todayactivities/{iban}/{currency}")
        return list(_records_of(data))


def _records_of(data: Any) -> list[dict[str, Any]]:
    """Statement/paging/today responses come either as ``{Records: [...]}`` or a bare list."""
    if data is None:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() == "records" and isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:300] if text else response.reason_phrase
    if isinstance(body, dict):
        lowered = lower_keys(body)
        for key in ("message", "error_description", "error", "title", "detail"):
            if lowered.get(key):
                return str(lowered[key])[:300]
    return json.dumps(body, ensure_ascii=False)[:300]
