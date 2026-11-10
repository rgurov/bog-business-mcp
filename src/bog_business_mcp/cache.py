"""In-memory statement cache and local rate limiter.

BoG allows 1 statement request per minute per account+currency. Past days are
immutable, so a statement fetched once is reused for the process lifetime, and
any period fully inside an already fetched one is served from memory.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from .errors import RateLimitedError

Pair = tuple[str, str]  # (iban, currency)

STATEMENT_INTERVAL = 60.0
MAX_ENTRIES_PER_PAIR = 16


@dataclass
class _Entry:
    start: date
    end: date
    records: list[dict[str, Any]]


class StatementCache:
    def __init__(self) -> None:
        self._entries: dict[Pair, list[_Entry]] = {}

    def get(self, pair: Pair, start: date, end: date) -> list[dict[str, Any]] | None:
        """Records of a cached period covering ``[start, end]`` (may include extra days)."""
        for entry in self._entries.get(pair, []):
            if entry.start <= start and entry.end >= end:
                return entry.records
        return None

    def put(self, pair: Pair, start: date, end: date, records: list[dict[str, Any]]) -> None:
        entries = [
            e for e in self._entries.get(pair, []) if not (start <= e.start and e.end <= end)
        ]
        entries.insert(0, _Entry(start, end, records))
        self._entries[pair] = entries[:MAX_ENTRIES_PER_PAIR]


class StatementLimiter:
    """Refuses a statement call locally instead of burning a request on a sure 429."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._next_allowed: dict[Pair, float] = {}

    def check(self, pair: Pair) -> None:
        wait = self._next_allowed.get(pair, 0.0) - self._clock()
        if wait > 0:
            raise RateLimitedError(
                f"Statement for {pair[0]}/{pair[1]} was requested less than a minute ago "
                "(BoG allows 1 request/minute per account+currency)",
                int(wait + 0.999),
            )

    def mark(self, pair: Pair) -> None:
        self._next_allowed[pair] = self._clock() + STATEMENT_INTERVAL

    def penalize(self, pair: Pair, retry_after: int) -> None:
        self._next_allowed[pair] = max(
            self._next_allowed.get(pair, 0.0), self._clock() + retry_after
        )
