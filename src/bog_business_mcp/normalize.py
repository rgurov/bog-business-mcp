"""Map raw BoG records to the normalised :class:`Transaction` model."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .client import lower_keys
from .config import AccountSpec
from .models import Counterparty, Transaction

# Georgia has no DST since 2005: Asia/Tbilisi == UTC+4 all year.
TBILISI = timezone(timedelta(hours=4), "Asia/Tbilisi")


def today_tbilisi(now: datetime | None = None) -> date:
    now = now or datetime.now(TBILISI)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TBILISI)
    return now.astimezone(TBILISI).date()


def parse_date(value: Any) -> date | None:
    """Parse BoG date strings; values without an offset are Tbilisi local time."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python <3.11-style fromisoformat chokes on 7-digit fractions (.4094325).
    if "." in text:
        head, _, tail = text.partition(".")
        frac = ""
        rest = ""
        for i, ch in enumerate(tail):
            if not ch.isdigit():
                rest = tail[i:]
                break
            frac += ch
        text = f"{head}.{frac[:6]}{rest}" if frac else f"{head}{rest}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(TBILISI).date()


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _id_str(value: Any) -> str | None:
    dec = to_decimal(value)
    if dec is None:
        return str(value) if value not in (None, "") else None
    if dec == dec.to_integral_value():
        return str(int(dec))
    return str(dec)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _counterparty(details: Any) -> Counterparty | None:
    if not isinstance(details, dict):
        return None
    cp = Counterparty(
        name=_clean(details.get("name")),
        inn=_clean(details.get("inn")),
        account=_clean(details.get("accountnumber")),
        bank_code=_clean(details.get("bankcode")),
        bank_name=_clean(details.get("bankname")),
    )
    return cp if any(cp.model_dump().values()) else None


def _direction_and_amount(
    debit: Decimal | None, credit: Decimal | None, signed: Decimal | None
) -> tuple[str, Decimal]:
    debit = abs(debit) if debit else Decimal(0)
    credit = abs(credit) if credit else Decimal(0)
    if debit > 0 and credit == 0:
        return "out", debit
    if credit > 0 and debit == 0:
        return "in", credit
    if signed is not None and signed != 0:
        return ("out" if signed < 0 else "in"), abs(signed)
    if debit or credit:  # both non-zero (shouldn't happen): net it
        net = credit - debit
        return ("out" if net < 0 else "in"), abs(net)
    return "in", Decimal(0)


def from_statement(
    record: dict[str, Any], spec: AccountSpec, currency: str, *, include_raw: bool = False
) -> Transaction:
    r = lower_keys(record)
    debit = to_decimal(r.get("entryamountdebit"))
    credit = to_decimal(r.get("entryamountcredit"))
    direction, amount = _direction_and_amount(debit, credit, to_decimal(r.get("entryamount")))

    if direction == "out":
        gel = to_decimal(r.get("entryamountdebitbase"))
        counterparty = _counterparty(r.get("beneficiarydetails"))
    else:
        gel = to_decimal(r.get("entryamountcreditbase"))
        counterparty = _counterparty(r.get("senderdetails"))
    if gel is None:
        base = to_decimal(r.get("entryamountbase"))
        gel = abs(base) if base is not None else None
    if gel is None and currency == "GEL":
        gel = amount

    bank_id = _id_str(r.get("entryid")) or _id_str(r.get("documentkey")) or "unknown"
    entry_date = (
        parse_date(r.get("entrydate"))
        or parse_date(r.get("documentvaluedate"))
        or parse_date(r.get("authdate"))
    )
    return Transaction(
        id=f"statement:{bank_id}",
        date=entry_date or date.min,
        value_date=parse_date(r.get("documentvaluedate")),
        iban=spec.iban,
        alias=spec.alias,
        currency=currency,
        direction=direction,  # type: ignore[arg-type]
        amount=amount,
        amount_gel=gel,
        counterparty=counterparty,
        purpose=_clean(r.get("documentnomination")),
        description=_clean(r.get("entrycomment")) or _clean(r.get("doccomment")),
        description_en=None,
        type=_clean(r.get("documentproductgroup")),
        document_no=_clean(r.get("entrydocumentnumber")),
        source="statement",
        raw=record if include_raw else None,
    )


def from_today(
    record: dict[str, Any],
    spec: AccountSpec,
    currency: str,
    today: date,
    *,
    include_raw: bool = False,
) -> Transaction:
    r = lower_keys(record)
    direction, amount = _direction_and_amount(
        to_decimal(r.get("debit")), to_decimal(r.get("credit")), to_decimal(r.get("amount"))
    )
    counterparty = _counterparty(r.get("beneficiary") if direction == "out" else r.get("sender"))
    gel = to_decimal(r.get("amountbase"))
    gel = abs(gel) if gel is not None else (amount if currency == "GEL" else None)

    bank_id = _id_str(r.get("id")) or _id_str(r.get("dockey")) or "unknown"
    return Transaction(
        id=f"today:{bank_id}",
        date=parse_date(r.get("postdate")) or today,
        value_date=parse_date(r.get("valuedate")),
        iban=spec.iban,
        alias=spec.alias,
        currency=currency,
        direction=direction,  # type: ignore[arg-type]
        amount=amount,
        amount_gel=gel,
        counterparty=counterparty,
        purpose=_clean(r.get("nomination")),
        description=_clean(r.get("entrycomment")),
        description_en=_clean(r.get("entrycommenten")),
        type=_clean(r.get("entrytype")),
        document_no=_clean(r.get("docno")),
        source="today",
        raw=record if include_raw else None,
    )


def matches_query(tx: Transaction, query: str) -> bool:
    needle = query.casefold()
    haystack = [tx.purpose, tx.description, tx.description_en, tx.document_no, tx.type]
    if tx.counterparty:
        haystack += [tx.counterparty.name, tx.counterparty.inn, tx.counterparty.account]
    return any(needle in h.casefold() for h in haystack if h)
