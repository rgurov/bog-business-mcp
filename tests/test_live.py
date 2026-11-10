"""Smoke test against the real API. Run with: BOG_LIVE=1 uv run pytest -m live -s"""

import os

import pytest

from bog_business_mcp.config import load_settings
from bog_business_mcp.service import BogService

pytestmark = [
    pytest.mark.live,
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("BOG_LIVE") != "1", reason="set BOG_LIVE=1 to run"),
]


async def test_live_smoke():
    svc = BogService(load_settings())
    try:
        accounts = await svc.list_accounts()
        print(accounts.model_dump_json(indent=2))
        assert accounts.accounts
        txs = await svc.get_transactions(limit=5)
        print(txs.model_dump_json(indent=2))
    finally:
        await svc.aclose()
