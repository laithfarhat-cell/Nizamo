"""Unified Double-Entry Accounting Models (Odoo-standard account_move / account_move_line architecture)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, CompanyScopedMixin, TimestampMixin
from backend.core.decimal_utils import FixedDecimal, ZERO


# Account Type Constants (IFRS / GAAP + Odoo alignment)
ACCOUNT_TYPE_ASSET_RECEIVABLE = "asset_receivable"
ACCOUNT_TYPE_ASSET_CASH = "asset_cash"
ACCOUNT_TYPE_ASSET_CURRENT = "asset_current"
ACCOUNT_TYPE_ASSET_NON_CURRENT = "asset_non_current"
ACCOUNT_TYPE_LIABILITY_PAYABLE = "liability_payable"
ACCOUNT_TYPE_LIABILITY_CURRENT = "liability_current"
ACCOUNT_TYPE_LIABILITY_NON_CURRENT = "liability_non_current"
ACCOUNT_TYPE_EQUITY = "equity"
ACCOUNT_TYPE_INCOME = "income"
ACCOUNT_TYPE_INCOME_OTHER = "income_other"
ACCOUNT_TYPE_EXPENSE_COGS = "expense_direct_cost"
ACCOUNT_TYPE_EXPENSE = "expense"

ASSET_TYPES = {
    ACCOUNT_TYPE_ASSET_RECEIVABLE,
    ACCOUNT_TYPE_ASSET_CASH,
    ACCOUNT_TYPE_ASSET_CURRENT,
    ACCOUNT_TYPE_ASSET_NON_CURRENT,
}
LIABILITY_TYPES = {
    ACCOUNT_TYPE_LIABILITY_PAYABLE,
    ACCOUNT_TYPE_LIABILITY_CURRENT,
    ACCOUNT_TYPE_LIABILITY_NON_CURRENT,
}
EQUITY_TYPES = {ACCOUNT_TYPE_EQUITY}
INCOME_TYPES = {ACCOUNT_TYPE_INCOME, ACCOUNT_TYPE_INCOME_OTHER}
EXPENSE_TYPES = {ACCOUNT_TYPE_EXPENSE_COGS, ACCOUNT_TYPE_EXPENSE}


class AccountAccount(Base, TimestampMixin, CompanyScopedMixin):
    """Chart of Accounts entry with IFRS/GAAP classification and reconciliation flag."""

    __tablename__ = "account_account"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_account_company_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    name_ar: Mapped[str | None] = mapped_column(String(160), nullable=True)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reconcilable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    currency_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("res_currency.id", ondelete="SET NULL"), nullable=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="SET NULL"), nullable=True
    )
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AccountJournal(Base, TimestampMixin, CompanyScopedMixin):
    """Accounting Journal (SALE, PURCHASE, BANK, CASH, GENERAL) tied to a dedicated sequence."""

    __tablename__ = "account_journal"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_journal_company_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    name_ar: Mapped[str | None] = mapped_column(String(128), nullable=True)
    journal_type: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True
    )  # SALE, PURCHASE, BANK, CASH, GENERAL
    default_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="SET NULL"), nullable=True
    )
    sequence_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ir_sequence.id", ondelete="SET NULL"), nullable=True
    )
    currency_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("res_currency.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AccountTax(Base, TimestampMixin, CompanyScopedMixin):
    """Configurable tax rule supporting percentage, fixed, price-included, and cascading/compound taxes."""

    __tablename__ = "account_tax"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    name_ar: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tax_type: Mapped[str] = mapped_column(String(16), default="SALE", nullable=False)  # SALE, PURCHASE, BOTH
    amount_type: Mapped[str] = mapped_column(String(16), default="PERCENT", nullable=False)  # PERCENT, FIXED
    amount: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    price_include: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    include_base_amount: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    tax_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="RESTRICT"), nullable=False
    )
    refund_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AccountAnalyticAccount(Base, TimestampMixin, CompanyScopedMixin):
    """Analytic cost center / project dimension independent of the General Ledger."""

    __tablename__ = "account_analytic_account"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_analytic_company_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    partner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("res_partner.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AccountMove(Base, TimestampMixin, CompanyScopedMixin):
    """Unified Double-Entry Document Header: Journal Entries, Invoices, Vendor Bills, Credit Notes, Payments."""

    __tablename__ = "account_move"
    __table_args__ = (
        Index("ix_move_company_state_date_type", "company_id", "state", "date", "move_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), default="DRAFT", nullable=False, index=True)
    ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    move_type: Mapped[str] = mapped_column(
        String(24), default="entry", nullable=False, index=True
    )  # entry, out_invoice, in_invoice, out_refund, in_refund
    state: Mapped[str] = mapped_column(
        String(16), default="DRAFT", nullable=False, index=True
    )  # DRAFT, POSTED, CANCELLED

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    invoice_date_due: Mapped[date | None] = mapped_column(Date, nullable=True)

    journal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_journal.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    partner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("res_partner.id", ondelete="SET NULL"), nullable=True, index=True
    )
    currency_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_currency.id", ondelete="RESTRICT"), nullable=False
    )
    exchange_rate: Mapped[Decimal] = mapped_column(FixedDecimal, default=Decimal("1.000000"), nullable=False)

    amount_untaxed: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    amount_tax: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    amount_total: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    amount_residual: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)

    payment_state: Mapped[str] = mapped_column(
        String(16), default="NOT_PAID", nullable=False
    )  # NOT_PAID, PARTIAL, IN_PAYMENT, PAID

    reversed_entry_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_move.id", ondelete="SET NULL"), nullable=True
    )
    invoice_origin: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list[AccountMoveLine]] = relationship(
        "AccountMoveLine",
        back_populates="move",
        cascade="all, delete-orphan",
        order_by="AccountMoveLine.id",
    )


class AccountMoveLine(Base, TimestampMixin, CompanyScopedMixin):
    """Double-Entry Ledger Line storing both company-currency debit/credit/balance and foreign amount_currency."""

    __tablename__ = "account_move_line"
    __table_args__ = (
        CheckConstraint("debit >= 0 AND credit >= 0", name="ck_aml_non_negative_dc"),
        Index("ix_aml_company_account_date", "company_id", "account_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    move_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_move.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    partner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("res_partner.id", ondelete="SET NULL"), nullable=True, index=True
    )
    analytic_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_analytic_account.id", ondelete="SET NULL"), nullable=True, index=True
    )
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    quantity: Mapped[Decimal] = mapped_column(FixedDecimal, default=Decimal("1.000000"), nullable=False)
    price_unit: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    discount: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)

    # Base Company Currency (DECIMAL(19, 6))
    debit: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    credit: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    balance: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)

    # Transaction Currency (DECIMAL(19, 6))
    currency_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_currency.id", ondelete="RESTRICT"), nullable=False
    )
    amount_currency: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)

    # Reconciliation Tracking
    amount_residual: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    amount_residual_currency: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    date_maturity: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    # Tax & Display Metadata
    tax_ids_json: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tax_line_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_tax.id", ondelete="SET NULL"), nullable=True
    )
    is_tax_line: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_type: Mapped[str] = mapped_column(
        String(24), default="product", nullable=False
    )  # product, tax, payment_term, entry

    move: Mapped[AccountMove] = relationship("AccountMove", back_populates="lines")


class AccountPayment(Base, TimestampMixin, CompanyScopedMixin):
    """Inbound/Outbound Payment Receipt linked to its generated accounting move."""

    __tablename__ = "account_payment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), default="DRAFT", nullable=False, index=True)
    payment_type: Mapped[str] = mapped_column(String(16), nullable=False)  # INBOUND, OUTBOUND
    partner_type: Mapped[str] = mapped_column(String(16), nullable=False)  # CUSTOMER, SUPPLIER
    partner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_partner.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    journal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_journal.id", ondelete="RESTRICT"), nullable=False
    )
    destination_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(FixedDecimal, nullable=False)
    currency_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_currency.id", ondelete="RESTRICT"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)  # DRAFT, POSTED, CANCELLED
    move_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_move.id", ondelete="SET NULL"), nullable=True
    )


class AccountPartialReconcile(Base, TimestampMixin, CompanyScopedMixin):
    """Partial or full reconciliation link between a debit and credit reconcilable move line."""

    __tablename__ = "account_partial_reconcile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    debit_move_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_move_line.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    credit_move_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_move_line.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Matched amount in base company currency (always positive)
    amount: Mapped[Decimal] = mapped_column(FixedDecimal, nullable=False)
    debit_amount_currency: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    credit_amount_currency: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    debit_currency_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_currency.id", ondelete="RESTRICT"), nullable=False
    )
    credit_currency_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_currency.id", ondelete="RESTRICT"), nullable=False
    )
    exchange_move_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_move.id", ondelete="SET NULL"), nullable=True
    )


class AccountBankStatement(Base, TimestampMixin, CompanyScopedMixin):
    """Imported Bank Statement batch for bank feed matching & reconciliation."""

    __tablename__ = "account_bank_statement"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    journal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_journal.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    balance_start: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    balance_end_real: Mapped[Decimal] = mapped_column(FixedDecimal, default=ZERO, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)  # OPEN, POSTED

    lines: Mapped[list[AccountBankStatementLine]] = relationship(
        "AccountBankStatementLine",
        back_populates="statement",
        cascade="all, delete-orphan",
        order_by="AccountBankStatementLine.id",
    )


class AccountBankStatementLine(Base, TimestampMixin, CompanyScopedMixin):
    """Individual bank feed transaction line matched & reconciled against open AR/AP invoices or GL accounts."""

    __tablename__ = "account_bank_statement_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    statement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_bank_statement.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payment_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    partner_name_raw: Mapped[str | None] = mapped_column(String(160), nullable=True)
    partner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("res_partner.id", ondelete="SET NULL"), nullable=True
    )
    # Positive = inbound cash deposit; Negative = outbound bank disbursement
    amount: Mapped[Decimal] = mapped_column(FixedDecimal, nullable=False)
    is_reconciled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    matched_move_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_move.id", ondelete="SET NULL"), nullable=True
    )
    payment_move_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account_move.id", ondelete="SET NULL"), nullable=True
    )

    statement: Mapped[AccountBankStatement] = relationship("AccountBankStatement", back_populates="lines")


class AccountFiscalClosing(Base, TimestampMixin, CompanyScopedMixin):
    """Audit record of executed Fiscal Year-End Closings transferring Net Profit to Retained Earnings."""

    __tablename__ = "account_fiscal_closing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    net_profit: Mapped[Decimal] = mapped_column(FixedDecimal, nullable=False)
    retained_earnings_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_account.id", ondelete="RESTRICT"), nullable=False
    )
    closing_move_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account_move.id", ondelete="RESTRICT"), nullable=False
    )

