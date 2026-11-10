"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import ConfigError

DEFAULT_API_BASE = "https://api.businessonline.ge/api"
DEFAULT_TOKEN_URL = "https://account.bog.ge/auth/realms/bog/protocol/openid-connect/token"
DEFAULT_PROBE_CURRENCIES = ("GEL", "USD", "EUR", "GBP")

# Georgian IBAN: GE + 2 check digits + 2-letter bank code + 16 digits = 22 chars.
_IBAN_RE = re.compile(r"^GE\d{2}[A-Z]{2}\d{16}$")
_CCY_RE = re.compile(r"^[A-Z]{3}$")
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


@dataclass(frozen=True)
class AccountSpec:
    """One configured account: an IBAN, optional alias and optional fixed currencies."""

    iban: str
    alias: str | None = None
    currencies: tuple[str, ...] | None = None  # None -> probe

    @property
    def label(self) -> str:
        return self.alias or self.iban


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    accounts: tuple[AccountSpec, ...]
    probe_currencies: tuple[str, ...] = DEFAULT_PROBE_CURRENCIES
    api_base: str = DEFAULT_API_BASE
    token_url: str = DEFAULT_TOKEN_URL
    timeout: float = 30.0
    enable_write: bool = False


def normalize_iban(raw: str) -> tuple[str, str | None]:
    """Return ``(iban, currency_suffix)``.

    Accepts IBANs with spaces, lowercase and the BoG-style currency suffix
    (``GE00BG0000000000000000GEL``), which some BoG screens show.
    """
    value = re.sub(r"\s+", "", raw).upper()
    suffix: str | None = None
    if len(value) == 25 and _CCY_RE.match(value[-3:]):
        value, suffix = value[:-3], value[-3:]
    if not _IBAN_RE.match(value):
        raise ConfigError(f"Invalid Georgian IBAN: {raw!r}")
    return value, suffix


def _parse_currencies(raw: str, where: str) -> tuple[str, ...]:
    items = [c.strip().upper() for c in raw.split(",") if c.strip()]
    for ccy in items:
        if not _CCY_RE.match(ccy):
            raise ConfigError(f"Invalid currency code {ccy!r} in {where}")
    if not items:
        raise ConfigError(f"Empty currency list in {where}")
    return tuple(dict.fromkeys(items))


def parse_accounts(raw: str) -> tuple[AccountSpec, ...]:
    """Parse ``BOG_ACCOUNTS``.

    Entries are separated by ``;`` or newlines. Each entry is
    ``[alias=]IBAN[:CCY,CCY...]``, e.g.
    ``main=GE00BG0000000000000000:GEL,USD; GE11BG0000000000000001``.
    """
    specs: list[AccountSpec] = []
    seen: set[str] = set()
    for entry in re.split(r"[;\n]", raw):
        entry = entry.strip()
        if not entry:
            continue
        alias: str | None = None
        if "=" in entry:
            alias_raw, entry = (part.strip() for part in entry.split("=", 1))
            if not _ALIAS_RE.match(alias_raw):
                raise ConfigError(
                    f"Invalid alias {alias_raw!r}: use letters, digits, '_', '-', '.'"
                )
            alias = alias_raw
        currencies: tuple[str, ...] | None = None
        if ":" in entry:
            entry, ccy_part = (part.strip() for part in entry.split(":", 1))
            currencies = _parse_currencies(ccy_part, f"BOG_ACCOUNTS entry {entry!r}")
        iban, suffix = normalize_iban(entry)
        if suffix and currencies is None:
            currencies = (suffix,)
        elif suffix and currencies and suffix not in currencies:
            currencies = (suffix, *currencies)
        if iban in seen:
            raise ConfigError(f"Duplicate IBAN in BOG_ACCOUNTS: {iban}")
        seen.add(iban)
        specs.append(AccountSpec(iban=iban, alias=alias, currencies=currencies))

    aliases = [s.alias.lower() for s in specs if s.alias]
    if len(aliases) != len(set(aliases)):
        raise ConfigError("Duplicate alias in BOG_ACCOUNTS")
    return tuple(specs)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    client_id = (env.get("BOG_CLIENT_ID") or "").strip()
    client_secret = (env.get("BOG_CLIENT_SECRET") or "").strip()
    missing = [
        name
        for name, value in (("BOG_CLIENT_ID", client_id), ("BOG_CLIENT_SECRET", client_secret))
        if not value
    ]
    if missing:
        raise ConfigError(
            f"Missing {', '.join(missing)}. Create an API client in Business Online "
            "(https://bonline.bog.ge/admin/api, Client Credentials) and pass the values "
            "via the MCP server env."
        )

    accounts = parse_accounts(env.get("BOG_ACCOUNTS") or "")
    if not accounts:
        raise ConfigError(
            "BOG_ACCOUNTS is empty. BoG API has no 'list accounts' endpoint, so list your "
            "IBANs, e.g. BOG_ACCOUNTS='main=GE00BG0000000000000000:GEL,USD'."
        )

    probe_raw = env.get("BOG_PROBE_CURRENCIES")
    probe = (
        _parse_currencies(probe_raw, "BOG_PROBE_CURRENCIES")
        if probe_raw and probe_raw.strip()
        else DEFAULT_PROBE_CURRENCIES
    )

    timeout_raw = (env.get("BOG_TIMEOUT") or "").strip()
    try:
        timeout = float(timeout_raw) if timeout_raw else 30.0
    except ValueError as exc:
        raise ConfigError(f"BOG_TIMEOUT must be a number of seconds, got {timeout_raw!r}") from exc

    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        accounts=accounts,
        probe_currencies=probe,
        api_base=(env.get("BOG_API_BASE") or DEFAULT_API_BASE).rstrip("/"),
        token_url=env.get("BOG_TOKEN_URL") or DEFAULT_TOKEN_URL,
        timeout=timeout,
        enable_write=_truthy(env.get("BOG_ENABLE_WRITE")),
    )
