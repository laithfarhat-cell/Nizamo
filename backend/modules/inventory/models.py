"""Double-Entry Stock Ledger, Product Catalog, Quants, Pickings, and FIFO/AVCO Valuation Layers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, CompanyScopedMixin, TimestampMixin
from backend.core.decimal_utils import FixedDecimal, ZERO


class ProductCategory(Base, TimestampMixin, CompanyScopedMixin):
    """Product category configuring costing method (STANDARD, AVCO, FIFO) and perpetual valuation accounts."""

    __tablename__ = "product_category"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    costing_method: Mapped[str] = mapped_column(
        String(16), default="AVCO", nullable=False
    )  # STANDARD, AVCO, FIFO
    valuation: Mapped[str] = mapped_column(
        String(16), default="REAL_TIME", nullable=False
    )  # MANUAL, REAL_TIME

    stock_valuation_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="SET NULL"), nullable=True
    )
    stock_input_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="SET NULL"), nullable=True
    )
    stock_output_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="SET NULL"), nullable=True
    )
    income_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="SET NULL"), nullable=True
    )
    expense_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="SET NULL"), nullable=True
    )


class ProductTemplate(Base, TimestampMixin, CompanyScopedMixin):
    """Product master record with SKU, list price, and tracked standard/average cost."""

    __tablename__ = "product_template"
    __table_args__ = (UniqueConstraint("company_id", "default_code", name="uq_product_company_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    default_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    name_ar: Mapped[str | None] = mapped_column(String(160), nullable=True)
    product_type: Mapped[str] = mapped_column(
        String(16), default="STORABLE", nullable=False
    )  # STORABLE, CONSUMABLE, SERVICE
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_category.id", ondelete="RESTRICT"), nullable=False
    )
    list_price: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    standard_price: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    uom: Mapped[str] = mapped_column(String(32), default="Units", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    category: Mapped[ProductCategory] = relationship("ProductCategory", lazy="joined")


class StockLocation(Base, TimestampMixin, CompanyScopedMixin):
    """Physical (WH/Stock) or Virtual (Customers, Vendors, Inventory Loss) double-entry stock location."""

    __tablename__ = "stock_location"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    complete_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    usage: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True
    )  # INTERNAL, CUSTOMER, SUPPLIER, INVENTORY, TRANSIT
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stock_location.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StockPicking(Base, TimestampMixin, CompanyScopedMixin):
    """Warehouse Transfer Document (Incoming Shipment, Delivery Order, Internal Transfer)."""

    __tablename__ = "stock_picking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    picking_type: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True
    )  # INCOMING, OUTGOING, INTERNAL
    origin: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    partner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("res_partner.id", ondelete="SET NULL"), nullable=True
    )
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stock_location.id", ondelete="RESTRICT"), nullable=False
    )
    location_dest_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stock_location.id", ondelete="RESTRICT"), nullable=False
    )
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    date_done: Mapped[date | None] = mapped_column(Date, nullable=True)
    state: Mapped[str] = mapped_column(
        String(16), default="DRAFT", nullable=False, index=True
    )  # DRAFT, CONFIRMED, ASSIGNED, DONE, CANCELLED

    moves: Mapped[list[StockMove]] = relationship(
        "StockMove", back_populates="picking", cascade="all, delete-orphan", order_by="StockMove.id"
    )


class StockMove(Base, TimestampMixin, CompanyScopedMixin):
    """Individual stock movement between source and destination locations conserving total units."""

    __tablename__ = "stock_move"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    picking_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stock_picking.id", ondelete="CASCADE"), nullable=True, index=True
    )
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_template.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    product_uom_qty: Mapped[Decimal] = mapped_column(FixedDecimal, nullable=False)
    quantity_done: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    price_unit: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stock_location.id", ondelete="RESTRICT"), nullable=False
    )
    location_dest_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stock_location.id", ondelete="RESTRICT"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)

    picking: Mapped[StockPicking | None] = relationship("StockPicking", back_populates="moves")


class StockQuant(Base, TimestampMixin, CompanyScopedMixin):
    """Current inventory quantity & reservation at `(product_id, location_id)`."""

    __tablename__ = "stock_quant"
    __table_args__ = (
        UniqueConstraint("company_id", "product_id", "location_id", name="uq_quant_company_prod_loc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_template.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stock_location.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    reserved_quantity: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)


class StockValuationLayer(Base, TimestampMixin, CompanyScopedMixin):
    """Chronological perpetual valuation layer powering FIFO queues, AVCO auditing, and GL reconciliation."""

    __tablename__ = "stock_valuation_layer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_template.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    stock_move_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stock_move.id", ondelete="SET NULL"), nullable=True, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(FixedDecimal, nullable=False)
    remaining_qty: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(FixedDecimal, nullable=False)
    value: Mapped[Decimal] = mapped_column(FixedDecimal, nullable=False)
    remaining_value: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    account_move_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_move.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(256), default="", nullable=False)
