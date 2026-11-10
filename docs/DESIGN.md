# Design: bog-business-mcp v0.1

## Goal

Give an LLM read-only access to a company's Bank of Georgia accounts: balances and
recent transactions. Local use only (stdio), credentials never leave the machine.

## Decisions

| Topic | Decision |
|---|---|
| Stack | Python 3.12+, official `mcp` SDK v2 (`MCPServer`), httpx, pydantic |
| Transport | stdio only |
| Distribution | `uvx --from git+https://github.com/rgurov/bog-business-mcp` (PyPI later) |
| Secrets | env vars in the MCP client config |
| Scope v0.1 | balances + transactions. No rates, deposits, document status, summary |
| Writes | none. `BOG_ENABLE_WRITE` is reserved: future write tools must be opt-in, off by default |
| Account discovery | IBANs from `BOG_ACCOUNTS`, currencies explicit or auto-probed via balance |
| Transactions tool | one unified `get_transactions` (statement + today's activities) |
| Output | normalised compact schema, optional `raw` |
| Rate limits | in-memory cache + local limiter + partial results with `warnings` |
| Language | texts as-is; `description_en` when BoG provides it; `Accept-Language: en` |
| Tests | pytest + respx on anonymised fixtures; live smoke test behind `BOG_LIVE=1`; GitHub Actions CI |

## BoG API facts used

Source: https://api.bog.ge/docs/en/bonline/introduction and the official Postman
collection (v1.0.5).

- Base URL `https://api.businessonline.ge/api`. Token:
  `POST https://account.bog.ge/auth/realms/bog/protocol/openid-connect/token`,
  `grant_type=client_credentials` plus HTTP Basic `client_id:client_secret`.
- No accounts list. Everything is keyed by `(IBAN without currency suffix, currency)`.
- `GET accounts/{iban}/{ccy}` → `AvailableBalance`, `CurrentBalance`.
- `GET v2/statement/{iban}/{ccy}/{start}/{end}` → `{Id, Count, TotalCount, Records}`,
  max 1000 records. Next pages: `GET v2/statement/{iban}/{ccy}/{Id}/{page}` from page 2,
  strictly sequential. The page response may be a bare array.
- `GET v2/documents/todayactivities/{iban}/{ccy}` → array of today's entries.
- Rate limits: 5 req/s per client by default; statement 1/min per account+currency;
  today's activities v2 10,000/day per account+currency. `429` + `Retry-After`.
- JSON key casing is inconsistent (PascalCase live, camelCase in v2 docs). Keys are
  lowercased before use. Floats are parsed as `Decimal`.
- Dates come with `+04:00` or without an offset. Offset-less values are Tbilisi local time.

## Modules

```
config.py     env parsing, IBAN validation
auth.py       token provider: cached, refreshed 30s before expires_in, invalidated on 401
client.py     httpx wrapper: auth header, 401 retry, 429 → RateLimitedError, paging
cache.py      StatementCache (covering-period lookup), StatementLimiter (1/min/pair)
normalize.py  raw records → Transaction; dates, decimals, direction, counterparty
service.py    account selection, currency probing, list_accounts, get_transactions
server.py     MCP tools (read_only_hint=True), errors → ToolError
```

## get_transactions algorithm

1. Period: `date_to` defaults to today (Tbilisi), clipped to today. `date_from` defaults
   to `date_to - 6 days`. Max 366 days.
2. Pairs: selected accounts × their currencies (filtered by `currency`).
3. For every pair, concurrently (semaphore 4):
   - past part `[date_from, min(date_to, yesterday)]` → statement (cache → limiter → API);
   - if `date_to == today` → today's activities.
   A failure in one part becomes a warning. The other part's data is still returned.
4. Normalise, dedupe by `(iban, currency, id)`, filter by period, direction, query,
   amount; sort newest first; apply `limit` (`total`, `truncated`).
5. If nothing succeeded anywhere → tool error with all messages.

Direction: `out` if debit > 0, `in` if credit > 0, otherwise the sign of the entry
amount. `amount` is always positive. Counterparty: the sender for incoming, the
beneficiary for outgoing.

## Verified on the live API (2026-11-10)

- Token: client_credentials works with no scope, `expires_in` = 1800s.
- `accounts/{iban}/{ccy}` for a currency the account does not hold → `404`
  (`application/problem+json`). A held currency with zero balance → `200` with zeros.
- v2 statement and today's activities use camelCase keys. The statement is
  `{id, count, totalCount, records}`. Amounts come as JSON ints or decimals.
- `documentValueDate` is usually null. `entryDate` has no offset (`2026-10-13T00:00:00`).
- Card payments (`TRN`, also `GCE` today): sender = own account, beneficiary empty.
  The merchant, MCC and original amount are only in `entryComment` / `docComment`:
  `გადახდა - თანხა: HKD129.50; ობიექტი: <merchant>, <country>; MCC:5814; თარიღი: ...`.
  Parsed into `merchant`, `mcc`, `original_amount`, `original_currency`.
- `x-correlationid` is not returned on success responses.

## Open questions

- Whether statement paging requests count against the 1/min limit.
- Maximum statement period accepted by the bank.
- Whether `Accept-Language: en` changes any texts.

## Future

- Tools: `get_statement_summary`, exchange rates, deposits, document status.
- Write tools (payments) behind `BOG_ENABLE_WRITE=1`, with `destructive_hint` and
  explicit confirmation.
- Optional on-disk cache of closed days.
