from datetime import date
from decimal import Decimal

import httpx
import pytest
from conftest import API, IBAN, IBAN2, fixture_response

from bog_business_mcp.errors import BogError
from bog_business_mcp.service import InvalidRequest

pytestmark = pytest.mark.anyio

STMT = f"{API}/v2/statement/{IBAN}/GEL/2026-11-04/2026-11-09"
PAGE2 = f"{API}/v2/statement/{IBAN}/GEL/987654/2"
TODAY = f"{API}/v2/documents/todayactivities/{IBAN}/GEL"


def mock_default(bog):
    stmt = bog.get(STMT).mock(return_value=fixture_response("statement_page1.json"))
    page2 = bog.get(PAGE2).mock(return_value=fixture_response("statement_page2.json"))
    today = bog.get(TODAY).mock(return_value=fixture_response("today_activities.json"))
    return stmt, page2, today


async def test_transactions_merge_statement_and_today(bog, make_service):
    stmt, page2, today = mock_default(bog)
    svc = make_service()

    res = await svc.get_transactions()

    assert (res.period.date_from, res.period.date_to) == (date(2026, 11, 4), date(2026, 11, 10))
    assert [t.id for t in res.transactions] == [
        "today:9990001",
        "statement:7770001",
        "statement:7770002",
        "statement:7770003",
    ]
    assert (res.total, res.returned, res.truncated, res.warnings) == (4, 4, False, [])
    assert stmt.call_count == page2.call_count == today.call_count == 1
    assert stmt.calls[0].request.headers["Authorization"] == "Bearer tok-1"

    today_tx, incoming, outgoing, fee = res.transactions
    assert today_tx.direction == "out"
    assert today_tx.amount == Decimal("200.00")
    assert today_tx.counterparty.name == "Ivan Ivanov"
    assert today_tx.description_en == "Transfer"
    assert today_tx.source == "today"

    assert incoming.direction == "in"
    assert incoming.amount == Decimal("1500.50")
    assert incoming.counterparty.name == "შპს კლიენტი"
    assert incoming.purpose == "ინვოისი #42 გადახდა"
    assert incoming.type == "PMD"
    assert incoming.alias == "main"

    assert outgoing.direction == "out"
    assert outgoing.amount == Decimal("35.20")
    assert outgoing.counterparty.name == "Hetzner Online GmbH"

    # camelCase page with tz offset + 7-digit fraction
    assert fee.date == date(2026, 11, 4)
    assert fee.counterparty.bank_name == "Bank of Georgia"


async def test_amounts_serialize_as_decimal_strings(bog, make_service):
    mock_default(bog)
    res = await make_service().get_transactions()
    dumped = res.model_dump(mode="json")
    assert dumped["transactions"][1]["amount"] == "1500.50"
    assert dumped["transactions"][1]["raw"] is None


async def test_filters_and_limit(bog, make_service):
    mock_default(bog)
    svc = make_service()

    res = await svc.get_transactions(direction="out", limit=2)
    assert [t.id for t in res.transactions] == ["today:9990001", "statement:7770002"]
    assert (res.total, res.returned, res.truncated) == (3, 2, True)

    res = await svc.get_transactions(query="hetzner")
    assert [t.id for t in res.transactions] == ["statement:7770002"]

    res = await svc.get_transactions(query="ინვოისი")
    assert [t.id for t in res.transactions] == ["statement:7770001"]

    res = await svc.get_transactions(min_amount=Decimal("100"), max_amount=Decimal("1000"))
    assert [t.id for t in res.transactions] == ["today:9990001"]


async def test_statement_cache_serves_subperiod(bog, make_service):
    stmt, _, _ = mock_default(bog)
    svc = make_service()
    await svc.get_transactions()
    res = await svc.get_transactions(date_from=date(2026, 11, 8), date_to=date(2026, 11, 9))
    assert stmt.call_count == 1
    assert [t.id for t in res.transactions] == ["statement:7770001", "statement:7770002"]


async def test_local_rate_limit_returns_warning_with_today_data(bog, make_service, clock):
    mock_default(bog)
    other = f"{API}/v2/statement/{IBAN}/GEL/2026-11-01/2026-11-09"
    bog.get(other).mock(return_value=httpx.Response(200, json={"Id": 1, "Records": []}))
    svc = make_service()
    await svc.get_transactions()

    res = await svc.get_transactions(date_from=date(2026, 11, 1))
    assert [t.id for t in res.transactions] == ["today:9990001"]
    assert len(res.warnings) == 1
    assert res.warnings[0].retry_after == 60

    clock.advance(61)
    res = await svc.get_transactions(date_from=date(2026, 11, 1))
    assert res.warnings == []


async def test_server_429_only_pair_raises(bog, make_service):
    bog.get(f"{API}/v2/statement/{IBAN}/GEL/2026-11-01/2026-11-05").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "42"})
    )
    svc = make_service()
    with pytest.raises(BogError, match="retry after 42s"):
        await svc.get_transactions(date_from=date(2026, 11, 1), date_to=date(2026, 11, 5))


async def test_token_refreshed_on_401(bog, make_service):
    bog.get(TODAY).mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json=[])],
    )
    svc = make_service()
    res = await svc.get_transactions(date_from=date(2026, 11, 10))
    assert res.transactions == []
    assert bog.routes[0].call_count == 2  # token fetched twice


async def test_currency_probe_and_list_accounts(bog, make_service):
    bog.get(f"{API}/accounts/{IBAN2}/GEL").mock(
        return_value=httpx.Response(200, json={"AvailableBalance": 10.5, "CurrentBalance": 12.0})
    )
    bog.get(f"{API}/accounts/{IBAN2}/USD").mock(
        return_value=httpx.Response(200, json={"availableBalance": 3, "currentBalance": 3})
    )
    eur = bog.get(f"{API}/accounts/{IBAN2}/EUR").mock(
        return_value=httpx.Response(400, json={"Message": "Account not found"})
    )
    bog.get(f"{API}/accounts/{IBAN2}/GBP").mock(return_value=httpx.Response(404))
    svc = make_service(accounts=IBAN2)

    res = await svc.list_accounts()
    assert [(a.currency, a.available_balance, a.current_balance) for a in res.accounts] == [
        ("GEL", Decimal("10.5"), Decimal("12.0")),
        ("USD", Decimal(3), Decimal(3)),
    ]
    assert res.warnings == []

    # resolution is cached: second call doesn't probe EUR again
    await svc.list_accounts()
    assert eur.call_count == 1


async def test_unknown_account_and_bad_period(make_service):
    svc = make_service()
    with pytest.raises(InvalidRequest, match="Configured accounts: main"):
        await svc.get_transactions(account="nope")
    with pytest.raises(InvalidRequest, match="after"):
        await svc.get_transactions(date_from=date(2026, 11, 9), date_to=date(2026, 11, 1))
    with pytest.raises(InvalidRequest, match="366"):
        await svc.get_transactions(date_from=date(2025, 1, 1))
