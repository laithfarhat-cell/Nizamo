"""Double-Entry Posting Engine enforcing strict balance, immutability, fiscal lock dates & gapless sequences."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.chatter import log_chatter
from backend.core.database import apply_for_update, atomic_lock
from backend.core.decimal_utils import D, ONE, ZERO, quantize_currency
from backend.core.sequences import next_sequence_by_code, next_sequence_by_id
from backend.core.tenancy import (
    ResCompany,
    ResCurrency,
    ResPartner,
    convert_to_company_currency,
    get_effective_rate,
)
from backend.modules.accounting.models import (
    ACCOUNT_TYPE_ASSET_RECEIVABLE,
    ACCOUNT_TYPE_LIABILITY_PAYABLE,
    AccountAccount,
    AccountJournal,
    AccountMove,
    AccountMoveLine,
    AccountTax,
)
from backend.modules.accounting.tax_engine import compute_line_taxes


class AccountingError(Exception):
    """Base domain exception for double-entry accounting rule violations."""


class UnbalancedMoveError(AccountingError):
    """Raised when Sum(debit) != Sum(credit) upon attempting to post a journal entry."""


class PostedImmutabilityError(AccountingError):
    """Raised when attempting to modify or delete a POSTED journal entry or its lines."""


class FiscalLockDateError(AccountingError):
    """Raised when attempting to post an entry dated on or before fiscalyear_lock_date / tax_lock_date."""


@dataclass
class JournalLineInput:
    account_id: int
    name: str = ""
    partner_id: int | None = None
    analytic_account_id: int | None = None
    product_id: int | None = None
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    amount_currency: Decimal | None = None
    currency_id: int | None = None
    date_maturity: date | None = None
    display_type: str = "entry"


@dataclass
class InvoiceLineInput:
    account_id: int
    name: str
    quantity: Decimal = ONE
    price_unit: Decimal = ZERO
    discount: Decimal = ZERO
    tax_ids: list[int] = field(default_factory=list)
    analytic_account_id: int | None = None
    product_id: int | None = None


async def _resolve_partner_control_account(
    session: AsyncSession,
    *,
    company_id: int,
    partner_id: int | None,
    is_receivable: bool,
) -> AccountAccount:
    if partner_id:
        partner = await session.get(ResPartner, partner_id)
        if partner:
            acct_id = partner.receivable_account_id if is_receivable else partner.payable_account_id
            if acct_id:
                acct = await session.get(AccountAccount, acct_id)
                if acct:
                    return acct
    target_type = ACCOUNT_TYPE_ASSET_RECEIVABLE if is_receivable else ACCOUNT_TYPE_LIABILITY_PAYABLE
    stmt = (
        select(AccountAccount)
        .where(
            AccountAccount.company_id == company_id,
            AccountAccount.account_type == target_type,
            AccountAccount.deprecated.is_(False),
        )
        .order_by(AccountAccount.code.asc())
        .limit(1)
    )
    acct = (await session.execute(stmt)).scalar_one_or_none()
    if acct is None:
        raise AccountingError(f"No {target_type} account configured for company_id={company_id}")
    return acct


async def create_journal_entry(
    session: AsyncSession,
    *,
    company_id: int,
    journal_id: int,
    move_date: date,
    lines: list[JournalLineInput],
    ref: str | None = None,
    partner_id: int | None = None,
    currency_id: int | None = None,
    narration: str | None = None,
    invoice_origin: str | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountMove:
    """Create a DRAFT general journal entry (`move_type='entry'`)."""
    company = await session.get(ResCompany, company_id)
    if company is None:
        raise AccountingError(f"Company {company_id} not found")
    base_currency = await session.get(ResCurrency, company.currency_id)
    base_prec = base_currency.decimal_places if base_currency else 2

    doc_currency_id = currency_id or company.currency_id
    doc_currency = await session.get(ResCurrency, doc_currency_id)
    doc_prec = doc_currency.decimal_places if doc_currency else 2
    rate = await get_effective_rate(
        session, company_id=company_id, currency_id=doc_currency_id, as_of_date=move_date
    )

    move = AccountMove(
        company_id=company_id,
        name="DRAFT",
        ref=ref,
        move_type="entry",
        state="DRAFT",
        date=move_date,
        journal_id=journal_id,
        partner_id=partner_id,
        currency_id=doc_currency_id,
        exchange_rate=rate,
        narration=narration,
        invoice_origin=invoice_origin,
        payment_state="NOT_PAID",
    )
    session.add(move)
    await session.flush()

    total_debit = ZERO
    for li in lines:
        line_curr_id = li.currency_id or doc_currency_id
        debit = quantize_currency(D(li.debit), base_prec)
        credit = quantize_currency(D(li.credit), base_prec)

        if debit < ZERO or credit < ZERO:
            raise AccountingError("Journal line debit and credit must be non-negative")
        if debit > ZERO and credit > ZERO:
            raise AccountingError("A single journal line cannot have both positive debit and positive credit")

        if li.amount_currency is not None and debit == ZERO and credit == ZERO:
            amt_curr = quantize_currency(D(li.amount_currency), doc_prec)
            base_amt, _ = await convert_to_company_currency(
                session,
                amount=abs(amt_curr),
                currency_id=line_curr_id,
                company_id=company_id,
                as_of_date=move_date,
            )
            if amt_curr >= ZERO:
                debit = base_amt
            else:
                credit = base_amt
        else:
            balance = debit - credit
            if li.amount_currency is not None:
                amt_curr = quantize_currency(D(li.amount_currency), doc_prec)
            elif line_curr_id == company.currency_id:
                amt_curr = balance
            else:
                # Derive transaction currency from base balance using effective rate
                l_rate = await get_effective_rate(
                    session, company_id=company_id, currency_id=line_curr_id, as_of_date=move_date
                )
                amt_curr = quantize_currency(balance / l_rate, doc_prec) if l_rate != ZERO else ZERO

        balance = debit - credit
        total_debit += debit
        move_line = AccountMoveLine(
            company_id=company_id,
            move_id=move.id,
            account_id=li.account_id,
            partner_id=li.partner_id if li.partner_id is not None else partner_id,
            analytic_account_id=li.analytic_account_id,
            product_id=li.product_id,
            name=li.name,
            debit=debit,
            credit=credit,
            balance=balance,
            currency_id=line_curr_id,
            amount_currency=amt_curr,
            amount_residual=ZERO,
            amount_residual_currency=ZERO,
            date=move_date,
            date_maturity=li.date_maturity or move_date,
            display_type=li.display_type,
        )
        session.add(move_line)

    move.amount_total = quantize_currency(total_debit, base_prec)
    move.amount_untaxed = move.amount_total
    await session.flush()

    await log_chatter(
        session,
        company_id=company_id,
        res_model="account_move",
        res_id=move.id,
        body="Journal entry created in DRAFT state",
        author_name=author_name,
        author_id=author_id,
    )
    return await get_move_with_lines(session, move.id)


async def create_invoice_move(
    session: AsyncSession,
    *,
    company_id: int,
    move_type: str,  # out_invoice, in_invoice, out_refund, in_refund
    journal_id: int,
    partner_id: int,
    invoice_date: date,
    invoice_date_due: date | None,
    invoice_lines: list[InvoiceLineInput],
    currency_id: int | None = None,
    ref: str | None = None,
    invoice_origin: str | None = None,
    narration: str | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountMove:
    """Create a DRAFT Customer Invoice, Vendor Bill, or Credit Note with auto-generated tax & AR/AP lines."""
    if move_type not in ("out_invoice", "in_invoice", "out_refund", "in_refund"):
        raise AccountingError(f"Unsupported invoice move_type: {move_type}")
    if not invoice_lines:
        raise AccountingError("Invoice must contain at least one line")

    company = await session.get(ResCompany, company_id)
    if company is None:
        raise AccountingError(f"Company {company_id} not found")
    base_currency = await session.get(ResCurrency, company.currency_id)
    base_prec = base_currency.decimal_places if base_currency else 2

    doc_currency_id = currency_id or company.currency_id
    doc_currency = await session.get(ResCurrency, doc_currency_id)
    doc_prec = doc_currency.decimal_places if doc_currency else 2
    rate = await get_effective_rate(
        session, company_id=company_id, currency_id=doc_currency_id, as_of_date=invoice_date
    )

    is_sale = move_type in ("out_invoice", "out_refund")
    is_refund = move_type in ("out_refund", "in_refund")
    # Normal direction for product/tax lines:
    # out_invoice & in_refund => product/tax are CREDITED, AR/AP is DEBITED
    # in_invoice & out_refund => product/tax are DEBITED, AR/AP is CREDITED
    product_is_credit = move_type in ("out_invoice", "in_refund")

    due_date = invoice_date_due or invoice_date
    control_account = await _resolve_partner_control_account(
        session, company_id=company_id, partner_id=partner_id, is_receivable=is_sale
    )

    # Load all referenced taxes
    all_tax_ids = {tid for li in invoice_lines for tid in li.tax_ids}
    taxes_by_id: dict[int, AccountTax] = {}
    if all_tax_ids:
        t_rows = (
            await session.execute(select(AccountTax).where(AccountTax.id.in_(all_tax_ids)))
        ).scalars().all()
        taxes_by_id = {t.id: t for t in t_rows}

    move = AccountMove(
        company_id=company_id,
        name="DRAFT",
        ref=ref,
        move_type=move_type,
        state="DRAFT",
        date=invoice_date,
        invoice_date=invoice_date,
        invoice_date_due=due_date,
        journal_id=journal_id,
        partner_id=partner_id,
        currency_id=doc_currency_id,
        exchange_rate=rate,
        invoice_origin=invoice_origin,
        narration=narration,
        payment_state="NOT_PAID",
    )
    session.add(move)
    await session.flush()

    total_untaxed_curr = ZERO
    total_tax_curr = ZERO
    total_base_opposite = ZERO

    # Aggregate tax lines grouped by (tax_id, account_id, name)
    grouped_taxes: dict[tuple[int, int, str], Decimal] = {}

    for li in invoice_lines:
        line_taxes = [taxes_by_id[tid] for tid in li.tax_ids if tid in taxes_by_id]
        tax_res = compute_line_taxes(
            price_unit=D(li.price_unit),
            quantity=D(li.quantity),
            discount=D(li.discount),
            taxes=line_taxes,
            currency_precision=doc_prec,
            is_refund=is_refund,
        )
        line_sub_curr = tax_res.total_excluded
        total_untaxed_curr += line_sub_curr

        base_amt, _ = await convert_to_company_currency(
            session,
            amount=line_sub_curr,
            currency_id=doc_currency_id,
            company_id=company_id,
            as_of_date=invoice_date,
        )
        total_base_opposite += base_amt

        if product_is_credit:
            debit, credit = ZERO, base_amt
            amt_curr = -line_sub_curr
        else:
            debit, credit = base_amt, ZERO
            amt_curr = line_sub_curr

        session.add(
            AccountMoveLine(
                company_id=company_id,
                move_id=move.id,
                account_id=li.account_id,
                partner_id=partner_id,
                analytic_account_id=li.analytic_account_id,
                product_id=li.product_id,
                name=li.name,
                quantity=D(li.quantity),
                price_unit=D(li.price_unit),
                discount=D(li.discount),
                debit=debit,
                credit=credit,
                balance=debit - credit,
                currency_id=doc_currency_id,
                amount_currency=amt_curr,
                date=invoice_date,
                date_maturity=due_date,
                tax_ids_json=json.dumps(li.tax_ids) if li.tax_ids else None,
                is_tax_line=False,
                display_type="product",
            )
        )

        for tl in tax_res.tax_lines:
            key = (tl.tax_id, tl.account_id, tl.name)
            grouped_taxes[key] = grouped_taxes.get(key, ZERO) + tl.amount

    # Create grouped Tax Move Lines
    for (tax_id, tax_account_id, tax_name), tax_amt_curr in grouped_taxes.items():
        tax_amt_curr = quantize_currency(tax_amt_curr, doc_prec)
        if tax_amt_curr == ZERO:
            continue
        total_tax_curr += tax_amt_curr
        tax_base_amt, _ = await convert_to_company_currency(
            session,
            amount=tax_amt_curr,
            currency_id=doc_currency_id,
            company_id=company_id,
            as_of_date=invoice_date,
        )
        total_base_opposite += tax_base_amt

        if product_is_credit:
            t_debit, t_credit = ZERO, tax_base_amt
            t_amt_curr = -tax_amt_curr
        else:
            t_debit, t_credit = tax_base_amt, ZERO
            t_amt_curr = tax_amt_curr

        session.add(
            AccountMoveLine(
                company_id=company_id,
                move_id=move.id,
                account_id=tax_account_id,
                partner_id=partner_id,
                name=f"Tax: {tax_name}",
                quantity=ONE,
                price_unit=tax_amt_curr,
                debit=t_debit,
                credit=t_credit,
                balance=t_debit - t_credit,
                currency_id=doc_currency_id,
                amount_currency=t_amt_curr,
                date=invoice_date,
                date_maturity=due_date,
                tax_line_id=tax_id,
                is_tax_line=True,
                display_type="tax",
            )
        )

    total_curr = quantize_currency(total_untaxed_curr + total_tax_curr, doc_prec)
    total_base = quantize_currency(total_base_opposite, base_prec)

    # Create Receivable / Payable counterpart line (strictly balancing company currency)
    if product_is_credit:
        cp_debit, cp_credit = total_base, ZERO
        cp_amt_curr = total_curr
    else:
        cp_debit, cp_credit = ZERO, total_base
        cp_amt_curr = -total_curr

    session.add(
        AccountMoveLine(
            company_id=company_id,
            move_id=move.id,
            account_id=control_account.id,
            partner_id=partner_id,
            name=f"{control_account.name} ({ref or move_type})",
            quantity=ONE,
            price_unit=total_curr,
            debit=cp_debit,
            credit=cp_credit,
            balance=cp_debit - cp_credit,
            currency_id=doc_currency_id,
            amount_currency=cp_amt_curr,
            date=invoice_date,
            date_maturity=due_date,
            is_tax_line=False,
            display_type="payment_term",
        )
    )

    move.amount_untaxed = quantize_currency(total_untaxed_curr, doc_prec)
    move.amount_tax = quantize_currency(total_tax_curr, doc_prec)
    move.amount_total = total_curr
    move.amount_residual = total_curr
    await session.flush()

    await log_chatter(
        session,
        company_id=company_id,
        res_model="account_move",
        res_id=move.id,
        body=f"Document ({move_type}) created in DRAFT state for total {total_curr}",
        author_name=author_name,
        author_id=author_id,
    )
    return await get_move_with_lines(session, move.id)


async def get_move_with_lines(session: AsyncSession, move_id: int) -> AccountMove:
    stmt = (
        select(AccountMove)
        .options(selectinload(AccountMove.lines))
        .where(AccountMove.id == move_id)
    )
    move = (await session.execute(stmt)).scalar_one_or_none()
    if move is None:
        raise AccountingError(f"AccountMove id={move_id} not found")
    return move


async def enforce_move_mutable(session: AsyncSession, move_id: int) -> AccountMove:
    """Enforce Posted Immutability: strictly reject edits/deletes once state == POSTED."""
    move = await get_move_with_lines(session, move_id)
    if move.state == "POSTED":
        raise PostedImmutabilityError(
            f"Immutable Ledger Violation: Entry '{move.name}' is POSTED and cannot be modified or deleted. "
            "Use an automated Reversal Entry or Credit Note to make corrections."
        )
    if move.state == "CANCELLED":
        raise AccountingError(f"Entry '{move.name}' is CANCELLED and cannot be edited")
    return move


async def update_move_line_protected(
    session: AsyncSession,
    *,
    line_id: int,
    debit: Decimal | None = None,
    credit: Decimal | None = None,
    name: str | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountMoveLine:
    """Attempt to modify an AccountMoveLine; blocked unconditionally if parent move is POSTED."""
    line = await session.get(AccountMoveLine, line_id)
    if line is None:
        raise AccountingError(f"AccountMoveLine id={line_id} not found")
    move = await enforce_move_mutable(session, line.move_id)

    diffs = []
    if name is not None and name != line.name:
        diffs.append(("name", "Line Label", line.name, name))
        line.name = name
    if debit is not None:
        new_d = D(debit)
        if new_d != line.debit:
            diffs.append(("debit", "Debit", line.debit, new_d))
            line.debit = new_d
    if credit is not None:
        new_c = D(credit)
        if new_c != line.credit:
            diffs.append(("credit", "Credit", line.credit, new_c))
            line.credit = new_c
    line.balance = line.debit - line.credit
    await session.flush()

    if diffs:
        await log_chatter(
            session,
            company_id=move.company_id,
            res_model="account_move",
            res_id=move.id,
            body=f"Updated draft line #{line.id}",
            author_name=author_name,
            author_id=author_id,
            diffs=diffs,
        )
    return line


async def delete_move_line_protected(session: AsyncSession, line_id: int) -> None:
    """Attempt to delete an AccountMoveLine; blocked unconditionally if parent move is POSTED."""
    line = await session.get(AccountMoveLine, line_id)
    if line is None:
        raise AccountingError(f"AccountMoveLine id={line_id} not found")
    await enforce_move_mutable(session, line.move_id)
    await session.delete(line)
    await session.flush()


async def post_move(
    session: AsyncSession,
    move_id: int,
    *,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountMove:
    """Atomically validate and post an AccountMove (DRAFT -> POSTED) using SELECT ... FOR UPDATE.

    Enforced Ledger Invariants:
      1. Fiscal Lock Dates (`fiscalyear_lock_date` & `tax_lock_date`)
      2. Strict Double-Entry Balance (`Sum(debit) == Sum(credit)` in company currency)
      3. Atomic gapless sequence generation per journal
      4. Initialization of reconcilable residuals
    """
    async with atomic_lock(session):
        stmt = apply_for_update(
            select(AccountMove)
            .options(selectinload(AccountMove.lines))
            .where(AccountMove.id == move_id),
            session,
        )
        move = (await session.execute(stmt)).scalar_one_or_none()
        if move is None:
            raise AccountingError(f"AccountMove id={move_id} not found")
        if move.state != "DRAFT":
            raise AccountingError(f"Only DRAFT entries can be posted (current state: {move.state})")
        if len(move.lines) < 2:
            raise UnbalancedMoveError("A journal entry must have at least 2 lines to be posted")

        company = await session.get(ResCompany, move.company_id)
        if company is None:
            raise AccountingError(f"Company id={move.company_id} not found")

        # Invariant 1: Fiscal & Tax Lock Dates
        if company.fiscalyear_lock_date and move.date <= company.fiscalyear_lock_date:
            raise FiscalLockDateError(
                f"Fiscal Lock Date Violation: Cannot post entry dated {move.date} on or before "
                f"fiscalyear_lock_date ({company.fiscalyear_lock_date})."
            )
        has_tax = any(line.is_tax_line or line.tax_line_id is not None for line in move.lines)
        if has_tax and company.tax_lock_date and move.date <= company.tax_lock_date:
            raise FiscalLockDateError(
                f"Tax Lock Date Violation: Cannot post tax-bearing entry dated {move.date} on or before "
                f"tax_lock_date ({company.tax_lock_date})."
            )

        # Invariant 2: Strict Double-Entry Balance Check (Zero Float!)
        total_debit = ZERO
        total_credit = ZERO
        for line in move.lines:
            d = D(line.debit)
            c = D(line.credit)
            if d < ZERO or c < ZERO:
                raise UnbalancedMoveError("Negative debit or credit is prohibited")
            line.balance = d - c
            total_debit += d
            total_credit += c

        if total_debit != total_credit:
            raise UnbalancedMoveError(
                f"Unbalanced Journal Entry Rejected: Sum(debit)={total_debit} != Sum(credit)={total_credit} "
                f"(diff={total_debit - total_credit})"
            )
        if total_debit == ZERO:
            raise UnbalancedMoveError("Cannot post a zero-value journal entry")

        # Fetch accounts to initialize reconcilable line residuals
        acct_ids = {l.account_id for l in move.lines}
        accts = (
            await session.execute(select(AccountAccount).where(AccountAccount.id.in_(acct_ids)))
        ).scalars().all()
        acct_map = {a.id: a for a in accts}

        for line in move.lines:
            acct = acct_map.get(line.account_id)
            if acct and acct.reconcilable:
                line.amount_residual = line.balance
                line.amount_residual_currency = line.amount_currency
                line.reconciled = line.balance == ZERO and line.amount_currency == ZERO
            else:
                line.amount_residual = ZERO
                line.amount_residual_currency = ZERO
                line.reconciled = False

        # Invariant 3: Atomic Gapless Sequence Numbering
        old_name = move.name
        if move.name == "DRAFT" or not move.name:
            journal = await session.get(AccountJournal, move.journal_id)
            if journal and journal.sequence_id:
                move.name = await next_sequence_by_id(session, journal.sequence_id, move.date)
            else:
                prefix_map = {
                    "out_invoice": "INV/%(year)s/",
                    "out_refund": "RINV/%(year)s/",
                    "in_invoice": "BILL/%(year)s/",
                    "in_refund": "RBILL/%(year)s/",
                    "entry": "JE/%(year)s/",
                }
                code = f"journal_{journal.code.lower()}" if journal else move.move_type
                move.name = await next_sequence_by_code(
                    session,
                    company_id=move.company_id,
                    code=code,
                    ref_date=move.date,
                    default_prefix=prefix_map.get(move.move_type, "JE/%(year)s/"),
                )

        old_state = move.state
        move.state = "POSTED"
        if move.move_type in ("out_invoice", "in_invoice", "out_refund", "in_refund"):
            move.payment_state = "NOT_PAID"
            move.amount_residual = move.amount_total
        await session.flush()

        await log_chatter(
            session,
            company_id=move.company_id,
            res_model="account_move",
            res_id=move.id,
            body=f"Entry posted as {move.name} (Total Debit = Credit = {total_debit})",
            message_type="state_change",
            author_name=author_name,
            author_id=author_id,
            diffs=[
                ("state", "Status", old_state, "POSTED"),
                ("name", "Document Number", old_name, move.name),
            ],
        )
        return move


async def cancel_move(
    session: AsyncSession,
    move_id: int,
    *,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountMove:
    """Cancel a DRAFT entry; strictly rejects cancellation of POSTED entries."""
    move = await enforce_move_mutable(session, move_id)
    old_state = move.state
    move.state = "CANCELLED"
    await session.flush()
    await log_chatter(
        session,
        company_id=move.company_id,
        res_model="account_move",
        res_id=move.id,
        body="Draft entry cancelled",
        message_type="state_change",
        author_name=author_name,
        author_id=author_id,
        diffs=[("state", "Status", old_state, "CANCELLED")],
    )
    return move


async def reverse_move(
    session: AsyncSession,
    move_id: int,
    *,
    reversal_date: date | None = None,
    reason: str = "Reversal",
    auto_post: bool = True,
    auto_reconcile: bool = True,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountMove:
    """Create an automated Reversal Entry or Credit Note for a POSTED move.

    Flips every line's debit <-> credit and negates `amount_currency`.
    """
    orig = await get_move_with_lines(session, move_id)
    if orig.state != "POSTED":
        raise AccountingError("Only POSTED entries can be reversed")

    reverse_type_map = {
        "out_invoice": "out_refund",
        "out_refund": "out_invoice",
        "in_invoice": "in_refund",
        "in_refund": "in_invoice",
        "entry": "entry",
    }
    rev_date = reversal_date or orig.date
    rev_move = AccountMove(
        company_id=orig.company_id,
        name="DRAFT",
        ref=f"Reversal of {orig.name}: {reason}",
        move_type=reverse_type_map.get(orig.move_type, "entry"),
        state="DRAFT",
        date=rev_date,
        invoice_date=rev_date if orig.invoice_date else None,
        invoice_date_due=rev_date if orig.invoice_date_due else None,
        journal_id=orig.journal_id,
        partner_id=orig.partner_id,
        currency_id=orig.currency_id,
        exchange_rate=orig.exchange_rate,
        amount_untaxed=orig.amount_untaxed,
        amount_tax=orig.amount_tax,
        amount_total=orig.amount_total,
        amount_residual=orig.amount_total,
        reversed_entry_id=orig.id,
        invoice_origin=orig.name,
        narration=f"Automated reversal of {orig.name} ({reason})",
    )
    session.add(rev_move)
    await session.flush()

    for line in orig.lines:
        new_debit = line.credit
        new_credit = line.debit
        session.add(
            AccountMoveLine(
                company_id=orig.company_id,
                move_id=rev_move.id,
                account_id=line.account_id,
                partner_id=line.partner_id,
                analytic_account_id=line.analytic_account_id,
                product_id=line.product_id,
                name=f"Reversal: {line.name}",
                quantity=line.quantity,
                price_unit=line.price_unit,
                discount=line.discount,
                debit=new_debit,
                credit=new_credit,
                balance=new_debit - new_credit,
                currency_id=line.currency_id,
                amount_currency=-line.amount_currency,
                date=rev_date,
                date_maturity=rev_date,
                tax_line_id=line.tax_line_id,
                is_tax_line=line.is_tax_line,
                display_type=line.display_type,
            )
        )
    await session.flush()

    await log_chatter(
        session,
        company_id=orig.company_id,
        res_model="account_move",
        res_id=orig.id,
        body=f"Reversal entry created (reason: {reason})",
        author_name=author_name,
        author_id=author_id,
    )

    if auto_post:
        rev_move = await post_move(
            session, rev_move.id, author_name=author_name, author_id=author_id
        )
        if auto_reconcile:
            from backend.modules.accounting.reconciliation import reconcile_move_lines

            orig_fresh = await get_move_with_lines(session, orig.id)
            rev_fresh = await get_move_with_lines(session, rev_move.id)
            for ol in orig_fresh.lines:
                if ol.reconciled or ol.amount_residual == ZERO:
                    continue
                for rl in rev_fresh.lines:
                    if rl.account_id == ol.account_id and not rl.reconciled and rl.amount_residual != ZERO:
                        if ol.amount_residual > ZERO and rl.amount_residual < ZERO:
                            await reconcile_move_lines(
                                session,
                                debit_line_id=ol.id,
                                credit_line_id=rl.id,
                                author_name=author_name,
                                author_id=author_id,
                            )
                        elif rl.amount_residual > ZERO and ol.amount_residual < ZERO:
                            await reconcile_move_lines(
                                session,
                                debit_line_id=rl.id,
                                credit_line_id=ol.id,
                                author_name=author_name,
                                author_id=author_id,
                            )
                        break

    return await get_move_with_lines(session, rev_move.id)


async def update_draft_move(
    session: AsyncSession,
    move_id: int,
    *,
    move_date: date | None = None,
    invoice_date_due: date | None = None,
    partner_id: int | None = None,
    currency_id: int | None = None,
    ref: str | None = None,
    narration: str | None = None,
    journal_lines: list[JournalLineInput] | None = None,
    invoice_lines: list[InvoiceLineInput] | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountMove:
    """Edit a DRAFT AccountMove header and/or rebuild its lines, logging field diffs to Audit Chatter.

    Unconditionally rejects editing POSTED or CANCELLED entries via `enforce_move_mutable()`.
    """
    move = await enforce_move_mutable(session, move_id)
    company = await session.get(ResCompany, move.company_id)
    if company is None:
        raise AccountingError(f"Company {move.company_id} not found")
    base_curr = await session.get(ResCurrency, company.currency_id)
    base_prec = base_curr.decimal_places if base_curr else 2

    diffs: list[tuple[str, str, object, object]] = []
    old_total = move.amount_total

    if move_date is not None and move_date != move.date:
        diffs.append(("date", "Accounting Date", move.date, move_date))
        move.date = move_date
        if move.invoice_date is not None:
            move.invoice_date = move_date
    if invoice_date_due is not None and invoice_date_due != move.invoice_date_due:
        diffs.append(("invoice_date_due", "Due Date", move.invoice_date_due, invoice_date_due))
        move.invoice_date_due = invoice_date_due
    if partner_id is not None and partner_id != move.partner_id:
        diffs.append(("partner_id", "Partner ID", move.partner_id, partner_id))
        move.partner_id = partner_id
    if currency_id is not None and currency_id != move.currency_id:
        diffs.append(("currency_id", "Currency ID", move.currency_id, currency_id))
        move.currency_id = currency_id
    if ref is not None and ref != move.ref:
        diffs.append(("ref", "Reference", move.ref, ref))
        move.ref = ref
    if narration is not None and narration != move.narration:
        move.narration = narration

    doc_curr = await session.get(ResCurrency, move.currency_id)
    doc_prec = doc_curr.decimal_places if doc_curr else 2
    move.exchange_rate = await get_effective_rate(
        session, company_id=move.company_id, currency_id=move.currency_id, as_of_date=move.date
    )

    if move.move_type == "entry" and journal_lines is not None:
        if len(journal_lines) < 2:
            raise AccountingError("Journal entry must have at least 2 lines")
        for existing in list(move.lines):
            await session.delete(existing)
        await session.flush()

        total_debit = ZERO
        for li in journal_lines:
            line_curr_id = li.currency_id or move.currency_id
            debit = quantize_currency(D(li.debit), base_prec)
            credit = quantize_currency(D(li.credit), base_prec)
            if debit < ZERO or credit < ZERO:
                raise AccountingError("Journal line debit and credit must be non-negative")
            if debit > ZERO and credit > ZERO:
                raise AccountingError("A single journal line cannot have both positive debit and credit")
            balance = debit - credit
            amt_curr = (
                quantize_currency(D(li.amount_currency), doc_prec)
                if li.amount_currency is not None
                else balance
            )
            total_debit += debit
            session.add(
                AccountMoveLine(
                    company_id=move.company_id,
                    move_id=move.id,
                    account_id=li.account_id,
                    partner_id=li.partner_id if li.partner_id is not None else move.partner_id,
                    analytic_account_id=li.analytic_account_id,
                    product_id=li.product_id,
                    name=li.name,
                    debit=debit,
                    credit=credit,
                    balance=balance,
                    currency_id=line_curr_id,
                    amount_currency=amt_curr,
                    date=move.date,
                    date_maturity=li.date_maturity or move.date,
                    display_type=li.display_type,
                )
            )
        move.amount_total = quantize_currency(total_debit, base_prec)
        move.amount_untaxed = move.amount_total

    elif move.move_type != "entry" and invoice_lines is not None:
        if not invoice_lines:
            raise AccountingError("Invoice must contain at least one line")
        for existing in list(move.lines):
            await session.delete(existing)
        await session.flush()

        is_sale = move.move_type in ("out_invoice", "out_refund")
        is_refund = move.move_type in ("out_refund", "in_refund")
        product_is_credit = move.move_type in ("out_invoice", "in_refund")
        due_dt = move.invoice_date_due or move.date

        control_account = await _resolve_partner_control_account(
            session, company_id=move.company_id, partner_id=move.partner_id, is_receivable=is_sale
        )
        all_tax_ids = {tid for li in invoice_lines for tid in li.tax_ids}
        taxes_by_id: dict[int, AccountTax] = {}
        if all_tax_ids:
            t_rows = (
                await session.execute(select(AccountTax).where(AccountTax.id.in_(all_tax_ids)))
            ).scalars().all()
            taxes_by_id = {t.id: t for t in t_rows}

        total_untaxed_curr = ZERO
        total_tax_curr = ZERO
        total_base_opposite = ZERO
        grouped_taxes: dict[tuple[int, int, str], Decimal] = {}

        for li in invoice_lines:
            line_taxes = [taxes_by_id[tid] for tid in li.tax_ids if tid in taxes_by_id]
            tax_res = compute_line_taxes(
                price_unit=D(li.price_unit),
                quantity=D(li.quantity),
                discount=D(li.discount),
                taxes=line_taxes,
                currency_precision=doc_prec,
                is_refund=is_refund,
            )
            line_sub_curr = tax_res.total_excluded
            total_untaxed_curr += line_sub_curr
            base_amt, _ = await convert_to_company_currency(
                session,
                amount=line_sub_curr,
                currency_id=move.currency_id,
                company_id=move.company_id,
                as_of_date=move.date,
            )
            total_base_opposite += base_amt
            debit, credit = (ZERO, base_amt) if product_is_credit else (base_amt, ZERO)
            amt_curr = -line_sub_curr if product_is_credit else line_sub_curr

            session.add(
                AccountMoveLine(
                    company_id=move.company_id,
                    move_id=move.id,
                    account_id=li.account_id,
                    partner_id=move.partner_id,
                    analytic_account_id=li.analytic_account_id,
                    product_id=li.product_id,
                    name=li.name,
                    quantity=D(li.quantity),
                    price_unit=D(li.price_unit),
                    discount=D(li.discount),
                    debit=debit,
                    credit=credit,
                    balance=debit - credit,
                    currency_id=move.currency_id,
                    amount_currency=amt_curr,
                    date=move.date,
                    date_maturity=due_dt,
                    tax_ids_json=json.dumps(li.tax_ids) if li.tax_ids else None,
                    is_tax_line=False,
                    display_type="product",
                )
            )
            for tl in tax_res.tax_lines:
                key = (tl.tax_id, tl.account_id, tl.name)
                grouped_taxes[key] = grouped_taxes.get(key, ZERO) + tl.amount

        for (tax_id, tax_account_id, tax_name), tax_amt_curr in grouped_taxes.items():
            tax_amt_curr = quantize_currency(tax_amt_curr, doc_prec)
            if tax_amt_curr == ZERO:
                continue
            total_tax_curr += tax_amt_curr
            tax_base_amt, _ = await convert_to_company_currency(
                session,
                amount=tax_amt_curr,
                currency_id=move.currency_id,
                company_id=move.company_id,
                as_of_date=move.date,
            )
            total_base_opposite += tax_base_amt
            t_debit, t_credit = (ZERO, tax_base_amt) if product_is_credit else (tax_base_amt, ZERO)
            t_amt_curr = -tax_amt_curr if product_is_credit else tax_amt_curr
            session.add(
                AccountMoveLine(
                    company_id=move.company_id,
                    move_id=move.id,
                    account_id=tax_account_id,
                    partner_id=move.partner_id,
                    name=f"Tax: {tax_name}",
                    quantity=ONE,
                    price_unit=tax_amt_curr,
                    debit=t_debit,
                    credit=t_credit,
                    balance=t_debit - t_credit,
                    currency_id=move.currency_id,
                    amount_currency=t_amt_curr,
                    date=move.date,
                    date_maturity=due_dt,
                    tax_line_id=tax_id,
                    is_tax_line=True,
                    display_type="tax",
                )
            )

        total_curr = quantize_currency(total_untaxed_curr + total_tax_curr, doc_prec)
        total_base = quantize_currency(total_base_opposite, base_prec)
        cp_debit, cp_credit = (total_base, ZERO) if product_is_credit else (ZERO, total_base)
        cp_amt_curr = total_curr if product_is_credit else -total_curr

        session.add(
            AccountMoveLine(
                company_id=move.company_id,
                move_id=move.id,
                account_id=control_account.id,
                partner_id=move.partner_id,
                name=f"{control_account.name} ({move.ref or move.move_type})",
                quantity=ONE,
                price_unit=total_curr,
                debit=cp_debit,
                credit=cp_credit,
                balance=cp_debit - cp_credit,
                currency_id=move.currency_id,
                amount_currency=cp_amt_curr,
                date=move.date,
                date_maturity=due_dt,
                is_tax_line=False,
                display_type="payment_term",
            )
        )
        move.amount_untaxed = quantize_currency(total_untaxed_curr, doc_prec)
        move.amount_tax = quantize_currency(total_tax_curr, doc_prec)
        move.amount_total = total_curr
        move.amount_residual = total_curr

    if move.amount_total != old_total:
        diffs.append(("amount_total", "Total Amount", old_total, move.amount_total))

    await session.flush()
    await log_chatter(
        session,
        company_id=move.company_id,
        res_model="account_move",
        res_id=move.id,
        body="Draft document updated",
        author_name=author_name,
        author_id=author_id,
        diffs=diffs,
    )
    return await get_move_with_lines(session, move.id)

