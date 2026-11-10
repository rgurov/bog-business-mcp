"""OAuth2 client-credentials token provider for BoG (Keycloak at account.bog.ge)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import httpx

from .errors import AuthError

log = logging.getLogger(__name__)

# Refresh this many seconds before expiry (capped for very short-lived tokens).
REFRESH_MARGIN = 30.0


class TokenProvider:
    def __init__(
        self,
        http: httpx.AsyncClient,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock
        self._token: str | None = None
        self._refresh_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._token = None
        self._refresh_at = 0.0

    async def get_token(self) -> str:
        if self._token and self._clock() < self._refresh_at:
            return self._token
        async with self._lock:
            if self._token and self._clock() < self._refresh_at:
                return self._token
            await self._fetch()
            assert self._token is not None
            return self._token

    async def _fetch(self) -> None:
        try:
            response = await self._http.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                auth=(self._client_id, self._client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise AuthError(f"Cannot reach BoG identity server: {exc}") from exc

        if response.status_code != 200:
            detail = ""
            try:
                body = response.json()
                detail = body.get("error_description") or body.get("error") or ""
            except ValueError:
                pass
            raise AuthError(
                f"Token request failed with HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
                + ". Check BOG_CLIENT_ID / BOG_CLIENT_SECRET; on some accounts your "
                "business banker must enable API access first."
            )

        try:
            body = response.json()
            token = body["access_token"]
        except (ValueError, KeyError) as exc:
            raise AuthError("Token response has no access_token") from exc

        expires_in = float(body.get("expires_in") or 300)
        margin = min(REFRESH_MARGIN, expires_in / 5)
        self._token = token
        self._refresh_at = self._clock() + expires_in - margin
        log.debug("Obtained BoG access token, expires_in=%ss", expires_in)
