import pytest
from conftest import API, IBAN, fixture_response
from mcp import Client

from bog_business_mcp import server

pytestmark = pytest.mark.anyio


@pytest.fixture
def mcp_service(make_service):
    svc = make_service()
    server.set_service(svc)
    yield svc
    server.set_service(None)


async def test_tools_are_read_only():
    async with Client(server.mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"list_accounts", "get_transactions"}
    for tool in tools.values():
        assert tool.annotations.read_only_hint is True


async def test_get_transactions_via_mcp(bog, mcp_service):
    bog.get(f"{API}/v2/documents/todayactivities/{IBAN}/GEL").mock(
        return_value=fixture_response("today_activities.json")
    )
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_transactions", {"date_from": "2026-11-10", "include_raw": True}
        )
    assert not result.is_error
    data = result.structured_content
    assert data["returned"] == 1
    tx = data["transactions"][0]
    assert tx["amount"] == "200.00"
    assert tx["raw"]["DocNo"] == "2001"


async def test_errors_are_tool_errors(mcp_service):
    async with Client(server.mcp) as client:
        result = await client.call_tool("get_transactions", {"account": "missing"})
    assert result.is_error
    assert "Unknown account" in result.content[0].text
