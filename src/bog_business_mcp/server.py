"""MCP server entry point (stdio)."""

from __future__ import annotations

import datetime as dt
import logging
import sys
from decimal import Decimal
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .config import load_settings
from .errors import BogError
from .models import AccountsResult, TransactionsResult
from .service import DEFAULT_DAYS, DEFAULT_LIMIT, MAX_DAYS, MAX_LIMIT, BogService

log = logging.getLogger("bog_business_mcp")

INSTRUCTIONS = """\
Read-only access to the user's Bank of Georgia business accounts (Business Online API).
- list_accounts: configured accounts with balances per currency.
- get_transactions: recent transactions across accounts (statement for past days +
  today's activities), newest first, with server-side filters.
Amounts are decimal strings in the account currency; amount_gel is the GEL equivalent.
Texts (purpose, description, counterparty names) are often in Georgian: translate when
answering if needed. The bank allows one statement request per minute per
account+currency; if a warning has retry_after, wait or narrow the request instead of
retrying immediately. Repeated queries inside an already fetched period are served from
cache.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

mcp = MCPServer("bog-business", instructions=INSTRUCTIONS)

_service: BogService | None = None


def get_service() -> BogService:
    global _service
    if _service is None:
        try:
            _service = BogService(load_settings())
        except BogError as exc:
            raise ToolError(str(exc)) from exc
    return _service


def set_service(service: BogService | None) -> None:
    """Inject a service (tests)."""
    global _service
    _service = service


@mcp.tool(title="List BoG accounts and balances", annotations=READ_ONLY)
async def list_accounts(
    account: Annotated[
        str | None,
        Field(description="Alias or IBAN to show only one account; omit for all"),
    ] = None,
) -> AccountsResult:
    """List configured Bank of Georgia business accounts with available and current
    balance for every currency held. Currencies are auto-detected unless fixed in config."""
    try:
        return await get_service().list_accounts(account)
    except BogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    title="Get BoG transactions",
    annotations=READ_ONLY,
    description=(
        "Recent transactions of Bank of Georgia business accounts, newest first. "
        "Combines the booked statement (past days) with today's activities. "
        f"Period is limited to {MAX_DAYS} days. Check `truncated` and `warnings` in the result."
    ),
)
async def get_transactions(
    account: Annotated[
        str | None, Field(description="Alias or IBAN; omit for all configured accounts")
    ] = None,
    currency: Annotated[
        str | None, Field(description="ISO currency (GEL, USD, ...); omit for all")
    ] = None,
    date_from: Annotated[
        dt.date | None,
        Field(description=f"Start date YYYY-MM-DD (default: {DEFAULT_DAYS} days back)"),
    ] = None,
    date_to: Annotated[
        dt.date | None, Field(description="End date YYYY-MM-DD inclusive (default: today)")
    ] = None,
    direction: Annotated[
        Literal["in", "out"] | None, Field(description="in = incoming, out = outgoing")
    ] = None,
    query: Annotated[
        str | None,
        Field(
            description="Case-insensitive substring over counterparty name/INN/IBAN, "
            "purpose, description, document number"
        ),
    ] = None,
    min_amount: Annotated[
        Decimal | None, Field(description="Minimum absolute amount, account currency")
    ] = None,
    max_amount: Annotated[
        Decimal | None, Field(description="Maximum absolute amount, account currency")
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=MAX_LIMIT, description="Max transactions to return")
    ] = DEFAULT_LIMIT,
    include_raw: Annotated[
        bool, Field(description="Attach the original BoG record to each transaction")
    ] = False,
) -> TransactionsResult:
    try:
        return await get_service().get_transactions(
            account=account,
            currency=currency,
            date_from=date_from,
            date_to=date_to,
            direction=direction,
            query=query,
            min_amount=min_amount,
            max_amount=max_amount,
            limit=limit,
            include_raw=include_raw,
        )
    except BogError as exc:
        raise ToolError(str(exc)) from exc


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = load_settings()
    except BogError as exc:
        # Start anyway so the client can show the error on the first tool call.
        log.error("Configuration error: %s", exc)
    else:
        if settings.enable_write:
            log.warning("BOG_ENABLE_WRITE is set, but write tools are not implemented yet")
        log.info("Configured accounts: %s", ", ".join(a.label for a in settings.accounts))
    mcp.run()


if __name__ == "__main__":
    main()
