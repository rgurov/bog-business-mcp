import pytest

from bog_business_mcp.config import AccountSpec, load_settings, normalize_iban, parse_accounts
from bog_business_mcp.errors import ConfigError

IBAN = "GE00BG0000000000000001"


def test_parse_accounts_formats():
    specs = parse_accounts(
        f"main={IBAN}:gel, usd; ge00 bg00 0000 0000 0000 02 \n reserve=GE00BG0000000000000003EUR"
    )
    assert specs == (
        AccountSpec(iban=IBAN, alias="main", currencies=("GEL", "USD")),
        AccountSpec(iban="GE00BG0000000000000002", alias=None, currencies=None),
        AccountSpec(iban="GE00BG0000000000000003", alias="reserve", currencies=("EUR",)),
    )


def test_iban_with_suffix():
    assert normalize_iban(f"{IBAN}USD") == (IBAN, "USD")
    assert normalize_iban(IBAN.lower()) == (IBAN, None)


@pytest.mark.parametrize(
    "raw",
    ["GE00BG000", "DE89370400440532013000", f"{IBAN}:GELX", f"bad alias={IBAN}", f"{IBAN};{IBAN}"],
)
def test_parse_accounts_invalid(raw):
    with pytest.raises(ConfigError):
        parse_accounts(raw)


def test_duplicate_alias():
    with pytest.raises(ConfigError):
        parse_accounts(f"a={IBAN}; A=GE00BG0000000000000002")


def test_load_settings_requires_credentials_and_accounts():
    with pytest.raises(ConfigError, match="BOG_CLIENT_ID"):
        load_settings({"BOG_ACCOUNTS": IBAN})
    with pytest.raises(ConfigError, match="BOG_ACCOUNTS"):
        load_settings({"BOG_CLIENT_ID": "a", "BOG_CLIENT_SECRET": "b"})


def test_load_settings_full():
    s = load_settings(
        {
            "BOG_CLIENT_ID": "a",
            "BOG_CLIENT_SECRET": "b",
            "BOG_ACCOUNTS": IBAN,
            "BOG_PROBE_CURRENCIES": "gel,chf",
            "BOG_API_BASE": "https://example.test/api/",
            "BOG_ENABLE_WRITE": "true",
        }
    )
    assert s.probe_currencies == ("GEL", "CHF")
    assert s.api_base == "https://example.test/api"
    assert s.enable_write is True
