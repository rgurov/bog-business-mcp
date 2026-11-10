"""Business logic behind the MCP tools: account resolution, balances, transactions."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

import httpx

from .auth import TokenProvider
from .cache import StatementCache, StatementLimiter
from .client import BogClient
from .config import AccountSpec, Settings
from .errors import AuthError, BogApiError, BogError, RateLimitedError
from .models import (
    AccountBalance,
    AccountsResult,
    PairWarning,
    Period,
    Transaction,
    TransactionsResult,
)
from .normalize import (
    TBILISI,
    from_statement,
    from_today,
    matches_query,
    to_decimal,
    today_tbilisi,
)

log = logging.getLogger(__name__)

DEFAULT_DAYS = 7
MAX_DAYS = 366
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
CONCURRENCY = 4  # BoG default limit is 5 req/s per client


class InvalidRequest(BogError):
    """Bad tool arguments (unknown account, wrong dates, ...)."""


def _is_not_held(exc: BogApiError) -> bool:
    """A 4xx on a balance probe means the account has no such currency."""
    return 400 <= exc.status_code < 500 and exc.status_code not in (401, 403, 429)


def _warning(spec: AccountSpec, currency: str | None, exc: Exception) -> PairWarning:
    return PairWarning(
        account=spec.label,
        currency=currency,
        message=str(exc),
        retry_after=exc.retry_after if isinstance(exc, RateLimitedError) else None,
    )


def _join_warnings(warnings: list[PairWarning]) -> str:
    return "; ".join(
        f"{w.account}{' ' + w.currency if w.currency else ''}: {w.message}" for w in warnings
    )


class BogService:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(TBILISI),
    ) -> None:
        self.settings = settings
        self._http = http or httpx.AsyncClient(timeout=settings.timeout)
        self._owns_http = http is None
        self.tokens = TokenProvider(
            self._http, settings.token_url, settings.client_id, settings.client_secret, clock=clock
        )
        self.client = BogClient(self._http, self.tokens, settings.api_base)
        self._now = now
        self._cache = StatementCache()
        self._limiter = StatementLimiter(clock)
        self._resolved: dict[str, tuple[str, ...]] = {}
        self._sem = asyncio.Semaphore(CONCURRENCY)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # --- account selection --------------------------------------------------------

    def select_accounts(self, account: str | None) -> list[AccountSpec]:
        specs = list(self.settings.accounts)
        if not account:
            return specs
        key = re.sub(r"\s+", "", account).upper()
        for spec in specs:
            if (spec.alias and spec.alias.upper() == key) or spec.iban == key:
                return [spec]
            if len(key) == 25 and spec.iban == key[:22]:  # IBAN with currency suffix
                return [spec]
        known = ", ".join(s.label for s in specs)
        raise InvalidRequest(f"Unknown account {account!r}. Configured accounts: {known}")

    async def _call(self, coro_factory: Callable[[], Any]) -> Any:
        async with self._sem:
            return await coro_factory()

    async def _probe(
        self, spec: AccountSpec
    ) -> tuple[list[str], dict[str, dict[str, Any]], list[PairWarning]]:
        """Find which currencies an IBAN holds by requesting a balance for each candidate."""
        currencies = list(self.settings.probe_currencies)
        results = await asyncio.gather(
            *(self._call(lambda c=c: self.client.get_balance(spec.iban, c)) for c in currencies),
            return_exceptions=True,
        )
        held: list[str] = []
        balances: dict[str, dict[str, Any]] = {}
        warnings: list[PairWarning] = []
        for ccy, result in zip(currencies, results, strict=True):
            if isinstance(result, AuthError) or (
                isinstance(result, BaseException) and not isinstance(result, Exception)
            ):
                raise result
            if isinstance(result, BogApiError) and _is_not_held(result):
                continue
            if isinstance(result, Exception):
                warnings.append(_warning(spec, ccy, result))
                continue
            held.append(ccy)
            balances[ccy] = result
        if not warnings:
            self._resolved[spec.iban] = tuple(held)
            if not held:
                warnings.append(
                    PairWarning(
                        account=spec.label,
                        message=(
                            "No currency found for this IBAN among "
                            f"{', '.join(currencies)}. Set currencies explicitly in "
                            "BOG_ACCOUNTS (IBAN:GEL,USD) or extend BOG_PROBE_CURRENCIES."
                        ),
                    )
                )
        return held, balances, warnings

    async def _currencies(self, spec: AccountSpec) -> tuple[list[str], list[PairWarning]]:
        if spec.currencies is not None:
            return list(spec.currencies), []
        if spec.iban in self._resolved:
            return list(self._resolved[spec.iban]), []
        held, _, warnings = await self._probe(spec)
        return held, warnings

    # --- list_accounts ------------------------------------------------------------

    async def list_accounts(self, account: str | None = None) -> AccountsResult:
        specs = self.select_accounts(account)
        per_spec = await asyncio.gather(*(self._account_balances(s) for s in specs))
        accounts: list[AccountBalance] = []
        warnings: list[PairWarning] = []
        for balances, warns in per_spec:
            accounts.extend(balances)
            warnings.extend(warns)
        if not accounts and warnings:
            raise BogError(_join_warnings(warnings))
        return AccountsResult(accounts=accounts, warnings=warnings)

    async def _account_balances(
        self, spec: AccountSpec
    ) -> tuple[list[AccountBalance], list[PairWarning]]:
        warnings: list[PairWarning] = []
        if spec.currencies is None and spec.iban not in self._resolved:
            currencies, balances, warnings = await self._probe(spec)
        else:
            currencies, _ = await self._currencies(spec)
            balances = {}

        missing = [c for c in currencies if c not in balances]
        results = await asyncio.gather(
            *(self._call(lambda c=c: self.client.get_balance(spec.iban, c)) for c in missing),
            return_exceptions=True,
        )
        for ccy, result in zip(missing, results, strict=True):
            if isinstance(result, AuthError) or (
                isinstance(result, BaseException) and not isinstance(result, Exception)
            ):
                raise result
            if isinstance(result, Exception):
                warnings.append(_warning(spec, ccy, result))
            else:
                balances[ccy] = result

        out = [
            AccountBalance(
                iban=spec.iban,
                alias=spec.alias,
                currency=ccy,
                available_balance=to_decimal(balances[ccy].get("availablebalance")),
                current_balance=to_decimal(balances[ccy].get("currentbalance")),
            )
            for ccy in currencies
            if ccy in balances
        ]
        return out, warnings

    # --- get_transactions ---------------------------------------------------------

    async def _statement(
        self, iban: str, currency: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        pair = (iban, currency)
        cached = self._cache.get(pair, start, end)
        if cached is not None:
            log.debug("Statement cache hit %s %s..%s", pair, start, end)
            return cached
        self._limiter.check(pair)
        self._limiter.mark(pair)
        try:
            records = await self._call(
                lambda: self.client.get_statement(iban, currency, start, end)
            )
        except RateLimitedError as exc:
            self._limiter.penalize(pair, exc.retry_after)
            raise
        self._cache.put(pair, start, end, records)
        return records

    async def _pair_transactions(
        self,
        spec: AccountSpec,
        currency: str,
        date_from: date,
        date_to: date,
        today: date,
        include_raw: bool,
    ) -> tuple[list[Transaction], list[PairWarning], bool]:
        """Returns ``(transactions, warnings, any_part_succeeded)``."""
        txs: list[Transaction] = []
        warnings: list[PairWarning] = []
        ok = False

        statement_end = min(date_to, today - timedelta(days=1))
        if date_from <= statement_end:
            try:
                records = await self._statement(spec.iban, currency, date_from, statement_end)
                txs.extend(
                    from_statement(r, spec, currency, include_raw=include_raw) for r in records
                )
                ok = True
            except AuthError:
                raise
            except BogError as exc:
                warnings.append(_warning(spec, currency, exc))

        if date_to >= today:
            try:
                records = await self._call(
                    lambda: self.client.get_today_activities(spec.iban, currency)
                )
                txs.extend(
                    from_today(r, spec, currency, today, include_raw=include_raw) for r in records
                )
                ok = True
            except AuthError:
                raise
            except BogError as exc:
                warnings.append(_warning(spec, currency, exc))
        return txs, warnings, ok

    def _period(self, date_from: date | None, date_to: date | None, today: date) -> Period:
        if date_to is None or date_to > today:
            date_to = today
        if date_from is None:
            date_from = date_to - timedelta(days=DEFAULT_DAYS - 1)
        if date_from > date_to:
            raise InvalidRequest(f"date_from {date_from} is after date_to {date_to}")
        if (date_to - date_from).days + 1 > MAX_DAYS:
            raise InvalidRequest(f"Period is longer than {MAX_DAYS} days; split the request")
        return Period(date_from=date_from, date_to=date_to)

    async def get_transactions(
        self,
        *,
        account: str | None = None,
        currency: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        direction: Literal["in", "out"] | None = None,
        query: str | None = None,
        min_amount: Decimal | None = None,
        max_amount: Decimal | None = None,
        limit: int = DEFAULT_LIMIT,
        include_raw: bool = False,
    ) -> TransactionsResult:
        if not 1 <= limit <= MAX_LIMIT:
            raise InvalidRequest(f"limit must be between 1 and {MAX_LIMIT}")
        today = today_tbilisi(self._now())
        period = self._period(date_from, date_to, today)
        specs = self.select_accounts(account)
        ccy_filter = currency.strip().upper() if currency else None

        warnings: list[PairWarning] = []
        pairs: list[tuple[AccountSpec, str]] = []
        for spec, (currencies, warns) in zip(
            specs, await asyncio.gather(*(self._currencies(s) for s in specs)), strict=True
        ):
            warnings.extend(warns)
            pairs.extend((spec, c) for c in currencies if not ccy_filter or c == ccy_filter)
        if not pairs and not warnings:
            raise InvalidRequest(
                f"No configured account holds {ccy_filter}" if ccy_filter else "No accounts"
            )

        results = await asyncio.gather(
            *(
                self._pair_transactions(
                    spec, ccy, period.date_from, period.date_to, today, include_raw
                )
                for spec, ccy in pairs
            )
        )

        seen: set[tuple[str, str, str]] = set()
        txs: list[Transaction] = []
        any_ok = False
        for pair_txs, warns, ok in results:
            warnings.extend(warns)
            any_ok = any_ok or ok
            for tx in pair_txs:
                key = (tx.iban, tx.currency, tx.id)
                if key in seen:
                    continue
                seen.add(key)
                txs.append(tx)

        # Nothing succeeded anywhere -> a real error for the model, not an empty list.
        if warnings and not any_ok:
            raise BogError(_join_warnings(warnings))

        def keep(tx: Transaction) -> bool:
            if not period.date_from <= tx.date <= period.date_to:
                return False
            if direction and tx.direction != direction:
                return False
            if min_amount is not None and tx.amount < min_amount:
                return False
            if max_amount is not None and tx.amount > max_amount:
                return False
            return not (query and not matches_query(tx, query))

        matched = [tx for tx in txs if keep(tx)]
        matched.sort(key=_sort_key, reverse=True)
        page = matched[:limit]
        return TransactionsResult(
            transactions=page,
            total=len(matched),
            returned=len(page),
            truncated=len(matched) > len(page),
            period=period,
            warnings=warnings,
        )


def _sort_key(tx: Transaction) -> tuple[date, int, int]:
    _, _, bank_id = tx.id.partition(":")
    try:
        num = int(bank_id)
    except ValueError:
        num = 0
    return (tx.date, 1 if tx.source == "today" else 0, num)
