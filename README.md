# bog-business-mcp

Read-only [MCP](https://modelcontextprotocol.io) server for the
[Bank of Georgia Business Online API](https://api.bog.ge/docs/en/bonline/introduction).
Lets Claude (or any MCP client) see your business accounts, balances and recent
transactions. It cannot move money: no write endpoints are implemented.

## Tools

| Tool | What it does |
|---|---|
| `list_accounts(account?)` | Configured accounts with available / current balance for every currency held |
| `get_transactions(...)` | Recent transactions across accounts, newest first |

`get_transactions` parameters: `account` (alias or IBAN), `currency`, `date_from`, `date_to`
(`YYYY-MM-DD`, default: last 7 days, max 366), `direction` (`in` / `out`), `query`
(substring over counterparty, purpose, description, document number), `min_amount`,
`max_amount`, `limit` (default 50, max 500), `include_raw`.

It combines the booked statement (`v2/statement`, past days, all pages) with today's
activities (`v2/documents/todayactivities`). "Today" is Asia/Tbilisi.

Each transaction:

```json
{
  "id": "statement:7770001",
  "date": "2026-11-09",
  "value_date": "2026-11-09",
  "iban": "GE00BG0000000000000001",
  "alias": "main",
  "currency": "GEL",
  "direction": "in",
  "amount": "1500.50",
  "amount_gel": "1500.50",
  "counterparty": {"name": "...", "inn": "...", "account": "GE...", "bank_code": "TBCBGE22", "bank_name": "TBC Bank"},
  "purpose": "Invoice #42",
  "description": "...",
  "description_en": null,
  "type": "PMD",
  "document_no": "1001",
  "source": "statement"
}
```

Amounts are decimal strings. Texts are returned as BoG sends them (often in Georgian).

## Getting API credentials

1. Log in to [Business Online](https://bonline.bog.ge) and open
   **Admin → API** (`https://bonline.bog.ge/admin/api`).
2. Register a new application with the **Client Credentials** flow and confirm with OTP.
3. Copy `client_id` and `client_secret`. The secret is shown only once.
4. If the token request fails with `invalid_client` / `invalid_credentials`, ask your
   business banker to enable API access for your company.

The API has **no "list accounts" endpoint**, so you have to list your IBANs yourself.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `BOG_CLIENT_ID` | yes | OAuth client id |
| `BOG_CLIENT_SECRET` | yes | OAuth client secret |
| `BOG_ACCOUNTS` | yes | `;`-separated `[alias=]IBAN[:CCY,CCY]` entries |
| `BOG_PROBE_CURRENCIES` | no | Currencies to auto-detect when an entry has none. Default `GEL,USD,EUR,GBP` |
| `BOG_TIMEOUT` | no | HTTP timeout, seconds. Default `30` |
| `BOG_API_BASE`, `BOG_TOKEN_URL` | no | Override endpoints |
| `BOG_ENABLE_WRITE` | no | Reserved for future write tools. Currently a no-op |

`BOG_ACCOUNTS` examples:

```
main=GE00BG0000000000000001:GEL,USD
main=GE00BG0000000000000001; savings=GE00BG0000000000000002:EUR
GE00BG0000000000000001GEL
```

When an entry has no currencies, the server requests the balance for each
`BOG_PROBE_CURRENCIES` value on first use and caches which ones exist.

## Install

Requires [uv](https://docs.astral.sh/uv/).

### Claude Code

```bash
claude mcp add bog-business \
  -e BOG_CLIENT_ID=... \
  -e BOG_CLIENT_SECRET=... \
  -e BOG_ACCOUNTS='main=GE00BG0000000000000001:GEL,USD' \
  -- uvx --from git+https://github.com/rgurov/bog-business-mcp bog-business-mcp
```

### Claude Desktop / other clients

```json
{
  "mcpServers": {
    "bog-business": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rgurov/bog-business-mcp", "bog-business-mcp"],
      "env": {
        "BOG_CLIENT_ID": "...",
        "BOG_CLIENT_SECRET": "...",
        "BOG_ACCOUNTS": "main=GE00BG0000000000000001:GEL,USD"
      }
    }
  }
}
```

## Rate limits

BoG allows **1 statement request per minute per account+currency**. The server:

- caches fetched statements in memory for the process lifetime (past days do not change),
  and serves any period inside an already fetched one from the cache;
- refuses a repeat statement call within a minute locally instead of sending a request
  that would get `429`;
- returns partial results plus a `warnings` entry with `retry_after` when one
  account/currency is limited. It raises an error only if nothing could be fetched.

Today's activities (10,000/day) and balances are not cached.

## Development

```bash
uv sync
uv run pytest          # mocked HTTP (respx), no network
uv run ruff check . && uv run ruff format --check .

# smoke test against the real API (reads env vars above)
BOG_LIVE=1 uv run pytest -m live -s
```

See [docs/DESIGN.md](docs/DESIGN.md) for the design and API notes.

## Disclaimer

Unofficial project, not affiliated with Bank of Georgia. Use at your own risk.

## License

MIT
