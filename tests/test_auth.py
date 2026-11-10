import base64

import httpx
import pytest
import respx

from bog_business_mcp.auth import TokenProvider
from bog_business_mcp.config import DEFAULT_TOKEN_URL
from bog_business_mcp.errors import AuthError

pytestmark = pytest.mark.anyio


async def test_token_cached_and_refreshed(clock):
    with respx.mock() as router:
        route = router.post(DEFAULT_TOKEN_URL).mock(
            side_effect=[
                httpx.Response(200, json={"access_token": "t1", "expires_in": 300}),
                httpx.Response(200, json={"access_token": "t2", "expires_in": 300}),
            ]
        )
        async with httpx.AsyncClient() as http:
            tp = TokenProvider(http, DEFAULT_TOKEN_URL, "cid", "sec", clock=clock)
            assert await tp.get_token() == "t1"
            assert await tp.get_token() == "t1"
            assert route.call_count == 1

            request = route.calls[0].request
            assert b"grant_type=client_credentials" in request.content
            expected = "Basic " + base64.b64encode(b"cid:sec").decode()
            assert request.headers["Authorization"] == expected

            clock.advance(275)  # refreshes 30s before expiry
            assert await tp.get_token() == "t2"
            assert route.call_count == 2


async def test_token_error_has_hint(clock):
    with respx.mock() as router:
        router.post(DEFAULT_TOKEN_URL).mock(
            return_value=httpx.Response(
                401, json={"error": "invalid_client", "error_description": "Invalid credentials"}
            )
        )
        async with httpx.AsyncClient() as http:
            tp = TokenProvider(http, DEFAULT_TOKEN_URL, "cid", "sec", clock=clock)
            with pytest.raises(AuthError, match="Invalid credentials.*business banker"):
                await tp.get_token()
