"""Multi-Company Tenancy, Multi-Currency Engine, and Master Partner Models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, CompanyScopedMixin, TimestampMixin
from backend.core.decimal_utils import D, FixedDecimal, ONE, quantize_currency


class ResCurrency(Base, TimestampMixin):
    """ISO 4217 currency master record with rounding precision."""

    __tablename__ = "res_currency"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    decimal_places: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    rates: Mapped[list[ResCurrencyRate]] = relationship(
        "ResCurrencyRate", back_populates="currency", cascade="all, delete-orphan"
    )


class ResCurrencyRate(Base, TimestampMixin):
    """Historical exchange rate effective on or before a document date.

    Convention (Odoo-compatible):
      `rate` represents units of THIS currency per 1 unit of the company's base currency,
      OR equivalently `company_amount = foreign_amount / rate` (when base currency rate = 1.0).
      We also store `unit_to_base_rate` = 1 / rate explicitly? To keep accounting crystal clear
      and avoid inversion confusion:
      `rate` = how many units of company base currency equal 1 unit of `currency_id`?
      Wait: let's define `rate_to_base` clearly:
      Example (Base = USD):
        - USD: rate = 1.000000  (1 USD = 1.00 USD)
        - EUR: rate = 1.100000  (1 EUR = 1.10 USD) -> 100 EUR = 110.00 USD
        - LYD: rate = 0.206000  (1 LYD = 0.206 USD) -> 1000 LYD = 206.00 USD
        - SAR: rate = 0.266667  (1 SAR = 0.266667 USD)
      Formula: base_amount = foreign_amount * rate.
    """

    __tablename__ = "res_currency_rate"
    __table_args__ = (
        UniqueConstraint("company_id", "currency_id", "effective_date", name="uq_currency_rate_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_company.id", ondelete="CASCADE"), nullable=False, index=True
    )
    currency_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_currency.id", ondelete="CASCADE"), nullable=False, index=True
    )
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # 1 unit of `currency_id` = `rate` units of company base currency (DECIMAL(19, 6))
    rate: Mapped[Decimal] = mapped_column(FixedDecimal, nullable=False)

    currency: Mapped[ResCurrency] = relationship("ResCurrency", back_populates="rates")


class ResCompany(Base, TimestampMixin):
    """Multi-company legal entity with base currency and fiscal/tax lock dates."""

    __tablename__ = "res_company"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    name_ar: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(String(256), nullable=True)

    currency_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_currency.id", ondelete="RESTRICT"), nullable=False
    )
    fiscalyear_lock_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tax_lock_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    currency: Mapped[ResCurrency] = relationship("ResCurrency", lazy="joined")


class ResPartner(Base, TimestampMixin, CompanyScopedMixin):
    """Unified Contact / Customer / Vendor master record scoped by company_id."""

    __tablename__ = "res_partner"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    name_ar: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_customer: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    receivable_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payable_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


async def get_effective_rate(
    session: AsyncSession,
    *,
    company_id: int,
    currency_id: int,
    as_of_date: date,
) -> Decimal:
    """Return effective exchange rate (units of company base currency per 1 unit of currency_id).

    Looks up the latest `ResCurrencyRate` with `effective_date <= as_of_date`.
    Returns `Decimal('1.000000')` when `currency_id` is the company's own base currency.
    """
    company = await session.get(ResCompany, company_id)
    if company is None:
        raise ValueError(f"Company {company_id} not found")
    if currency_id == company.currency_id:
        return ONE

    stmt = (
        select(ResCurrencyRate.rate)
        .where(
            ResCurrencyRate.company_id == company_id,
            ResCurrencyRate.currency_id == currency_id,
            ResCurrencyRate.effective_date <= as_of_date,
        )
        .order_by(ResCurrencyRate.effective_date.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    rate = result.scalar_one_or_none()
    if rate is None:
        # Fallback to earliest available rate if dated before initial table entry
        fallback_stmt = (
            select(ResCurrencyRate.rate)
            .where(
                ResCurrencyRate.company_id == company_id,
                ResCurrencyRate.currency_id == currency_id,
            )
            .order_by(ResCurrencyRate.effective_date.asc())
            .limit(1)
        )
        rate = (await session.execute(fallback_stmt)).scalar_one_or_none()

    if rate is None:
        raise ValueError(
            f"No exchange rate configured for currency_id={currency_id} "
            f"in company_id={company_id} on or before {as_of_date}"
        )
    return D(rate)


async def convert_to_company_currency(
    session: AsyncSession,
    *,
    amount: Decimal,
    currency_id: int,
    company_id: int,
    as_of_date: date,
) -> tuple[Decimal, Decimal]:
    """Convert `amount` in `currency_id` into company base currency using historical rate.

    Returns `(converted_base_amount, effective_rate)` quantized strictly with ROUND_HALF_UP.
    """
    company = await session.get(ResCompany, company_id)
    if company is None:
        raise ValueError(f"Company {company_id} not found")
    base_currency = await session.get(ResCurrency, company.currency_id)
    precision = base_currency.decimal_places if base_currency else 2

    rate = await get_effective_rate(
        session, company_id=company_id, currency_id=currency_id, as_of_date=as_of_date
    )
    converted = (D(amount) * rate).quantize(
        Decimal(f"0.{'0' * (precision - 1)}1") if precision > 0 else Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    return quantize_currency(converted, precision), rate
