"""Zero floating-point financial arithmetic utilities and SQLAlchemy DECIMAL(19, 6) type."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import Numeric
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

ZERO = Decimal("0.000000")
ONE = Decimal("1.000000")
HUNDRED = Decimal("100.000000")
DB_QUANT = Decimal("0.000001")
MONEY_QUANT_2 = Decimal("0.01")


def D(value: Decimal | int | str | None) -> Decimal:
    """Strictly coerce a value into Decimal without accepting binary float precision drift."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("Boolean is not a valid financial Decimal value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return ZERO
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid Decimal string: {value!r}") from exc
    # Guard against raw float in domain math — convert via repr string only if deserialized externally
    if isinstance(value, float):
        return Decimal(str(value))
    raise TypeError(f"Unsupported type for financial Decimal: {type(value).__name__}")


def quantize_db(value: Decimal | int | str | None) -> Decimal:
    """Quantize to database storage scale DECIMAL(19, 6) using ROUND_HALF_UP."""
    return D(value).quantize(DB_QUANT, rounding=ROUND_HALF_UP)


def quantize_currency(value: Decimal | int | str | None, decimal_places: int = 2) -> Decimal:
    """Quantize monetary value to currency precision (e.g., 2 decimals USD/EUR, 3 decimals LYD) with ROUND_HALF_UP."""
    places = max(0, min(6, int(decimal_places)))
    exp = Decimal("1") if places == 0 else Decimal(f"0.{'0' * (places - 1)}1")
    return D(value).quantize(exp, rounding=ROUND_HALF_UP)


def is_zero(value: Decimal | int | str | None, decimal_places: int = 2) -> bool:
    """Return True if value rounds to zero at currency precision."""
    return quantize_currency(value, decimal_places) == Decimal("0")


class FixedDecimal(TypeDecorator[Decimal]):
    """SQLAlchemy type mapping strictly to DECIMAL(19, 6) / NUMERIC(19, 6) in PostgreSQL.

    Ensures all values bound to and read from the database are exact Python `Decimal`
    instances quantized with `ROUND_HALF_UP` to 6 decimal places.
    """

    impl = Numeric(19, 6, asdecimal=True)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        return dialect.type_descriptor(Numeric(19, 6, asdecimal=True))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return quantize_db(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value.quantize(DB_QUANT, rounding=ROUND_HALF_UP)
        return quantize_db(str(value))
