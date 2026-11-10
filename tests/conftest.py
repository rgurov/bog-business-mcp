from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from bog_business_mcp.config import DEFAULT_API_BASE, DEFAULT_TOKEN_URL, Settings, parse_accounts
from bog_business_mcp.normalize import TBILISI
from bog_business_mcp.service import BogService

FIXTURES = Path(__file__).parent / "fixtures"
API = DEFAULT_API_BASE
IBAN = "GE00BG0000000000000001"
IBAN2 = "GE00BG0000000000000002"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=TBILISI)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def fixture_response(name: str, status: int = 200) -> httpx.Response:
    """Serve the fixture bytes as-is, so decimals like 1500.50 keep their exact text."""
    return httpx.Response(
        status,
        content=(FIXTURES / name).read_bytes(),
        headers={"content-type": "application/json; charset=utf-8"},
    )


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def bog() -> respx.MockRouter:
    with respx.mock(assert_all_called=False) as router:
        router.post(DEFAULT_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "tok-1", "expires_in": 300, "token_type": "Bearer"}
            )
        )
        yield router


def make_settings(accounts: str = f"main={IBAN}:GEL") -> Settings:
    return Settings(client_id="cid", client_secret="secret", accounts=parse_accounts(accounts))


@pytest.fixture
def make_service(clock: FakeClock):
    created: list[BogService] = []

    def factory(accounts: str = f"main={IBAN}:GEL", now: datetime = NOW) -> BogService:
        service = BogService(
            make_settings(accounts),
            http=httpx.AsyncClient(),
            clock=clock,
            now=lambda: now,
        )
        created.append(service)
        return service

    return factory
