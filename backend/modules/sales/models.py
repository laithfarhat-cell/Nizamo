"""Sales Order & Quotation Models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, CompanyScopedMixin, TimestampMixin
from backend.core.decimal_utils import FixedDecimal, ONE, ZERO


class SaleOrder(Base, TimestampMixin, CompanyScopedMixin):
    """Sales Quotation / Confirmed Sales Order driving Delivery Orders and Customer Invoices."""

    __tablename__ = "sale_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), default="DRAFT", nullable=False, index=True)
    partner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_partner.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    currency_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_currency.id", ondelete="RESTRICT"), nullable=False
    )
    date_order: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(16), default="DRAFT", nullable=False, index=True
    )  # DRAFT, CONFIRMED, DONE, CANCELLED
    invoice_policy: Mapped[str] = mapped_column(
        String(16), default="ORDERED", nullable=False
    )  # ORDERED, DELIVERED
    amount_untaxed: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    amount_tax: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    amount_total: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list[SaleOrderLine]] = relationship(
        "SaleOrderLine", back_populates="order", cascade="all, delete-orphan", order_by="SaleOrderLine.id"
    )


class SaleOrderLine(Base, TimestampMixin, CompanyScopedMixin):
    """Sales Order line tracking ordered, delivered, and invoiced quantities."""

    __tablename__ = "sale_order_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sale_order.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("product_template.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(FixedDecimal, default=ONE, nullable=False)
    qty_delivered: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    qty_invoiced: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    price_unit: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    discount: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    tax_ids_json: Mapped[str | None] = mapped_column(String(256), nullable=True)
    analytic_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_analytic_account.id", ondelete="SET NULL"), nullable=True
    )
    price_subtotal: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    price_tax: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    price_total: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)

    order: Mapped[SaleOrder] = relationship("SaleOrder", back_populates="lines")
