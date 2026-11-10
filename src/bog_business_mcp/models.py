"""Normalised output models returned by MCP tools."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class PairWarning(BaseModel):
    """A non-fatal problem with one account/currency pair."""

    account: str = Field(description="Account alias or IBAN")
    currency: str | None = None
    message: str
    retry_after: int | None = Field(
        default=None, description="Seconds to wait before retrying (rate limits)"
    )


class AccountBalance(BaseModel):
    iban: str
    alias: str | None = None
    currency: str
    available_balance: Decimal | None = Field(
        default=None, description="Funds available to spend (decimal string)"
    )
    current_balance: Decimal | None = Field(
        default=None, description="Booked balance (decimal string)"
    )


class AccountsResult(BaseModel):
    accounts: list[AccountBalance]
    warnings: list[PairWarning] = Field(default_factory=list)


class Counterparty(BaseModel):
    name: str | None = None
    inn: str | None = Field(default=None, description="Tax ID / personal number")
    account: str | None = Field(default=None, description="Counterparty IBAN")
    bank_code: str | None = None
    bank_name: str | None = None


class Transaction(BaseModel):
    id: str = Field(description="Stable id: '<source>:<bank id>'")
    date: dt.date = Field(description="Entry (booking) date, Asia/Tbilisi")
    value_date: dt.date | None = None
    iban: str
    alias: str | None = None
    currency: str
    direction: Literal["in", "out"]
    amount: Decimal = Field(description="Absolute amount in account currency (decimal string)")
    amount_gel: Decimal | None = Field(default=None, description="GEL equivalent")
    counterparty: Counterparty | None = Field(
        default=None, description="Sender for incoming, beneficiary for outgoing"
    )
    purpose: str | None = Field(default=None, description="Payment purpose (often Georgian)")
    description: str | None = Field(default=None, description="Bank entry comment")
    description_en: str | None = Field(
        default=None, description="English entry comment, when BoG provides it"
    )
    type: str | None = Field(
        default=None, description="BoG product group / entry type (e.g. PMD, PLC, COM)"
    )
    document_no: str | None = None
    source: Literal["statement", "today"] = Field(
        description="statement = booked past days, today = today's activities"
    )
    raw: dict[str, Any] | None = Field(default=None, description="Original BoG record")


class Period(BaseModel):
    date_from: dt.date
    date_to: dt.date


class TransactionsResult(BaseModel):
    transactions: list[Transaction]
    total: int = Field(description="Matching transactions before applying limit")
    returned: int
    truncated: bool = Field(description="True if more matches exist than returned")
    period: Period
    warnings: list[PairWarning] = Field(default_factory=list)
