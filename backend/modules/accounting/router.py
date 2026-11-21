"""Double-Entry Accounting REST API Router."""

from __future__ import annotations

import json
from datetime import date, date as DateType
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.database import get_db
from backend.core.decimal_utils import D, ONE, ZERO, quantize_currency
from backend.core.security import RequestContext, get_current_context, require_permission
from backend.core.tenancy import ResCurrency, ResPartner
from backend.modules.accounting.bank_reconciliation import (
    import_bank_statement_csv,
    reconcile_bank_statement_line,
    serialize_statement_with_suggestions,
)
from backend.modules.accounting.closing_engine import (
    execute_fiscal_year_closing,
    execute_fx_revaluation,
    preview_fiscal_year_closing,
    preview_fx_revaluation,
)
from backend.modules.accounting.invoice_pdf import generate_invoice_pdf
from backend.modules.accounting.models import (
    AccountAccount,
    AccountAnalyticAccount,
    AccountBankStatement,
    AccountFiscalClosing,
    AccountJournal,
    AccountMove,
    AccountMoveLine,
    AccountPayment,
    AccountTax,
)
from backend.modules.accounting.posting_engine import (
    AccountingError,
    FiscalLockDateError,
    InvoiceLineInput,
    JournalLineInput,
    PostedImmutabilityError,
    UnbalancedMoveError,
    cancel_move,
    create_invoice_move,
    create_journal_entry,
    get_move_with_lines,
    post_move,
    reverse_move,
    update_draft_move,
)
from backend.modules.accounting.reconciliation import reconcile_move_lines, register_payment
from backend.modules.accounting.reports import (
    export_report_csv,
    export_report_pdf,
    export_report_xlsx,
    generate_aged_partner_balance,
    generate_balance_sheet,
    generate_general_ledger,
    generate_profit_and_loss,
    generate_trial_balance,
)

router = APIRouter(prefix="/accounting", tags=["Accounting"])


# --- Pydantic Schemas ---

class AccountCreate(BaseModel):
    code: str
    name: str
    name_ar: str | None = None
    account_type: str
    reconcilable: bool = False
    currency_id: int | None = None
    parent_id: int | None = None


class TaxCreate(BaseModel):
    name: str
    name_ar: str | None = None
    tax_type: str = "SALE"
    amount_type: str = "PERCENT"
    amount: Decimal
    price_include: bool = False
    include_base_amount: bool = False
    sequence: int = 10
    tax_account_id: int
    refund_account_id: int | None = None


class AnalyticCreate(BaseModel):
    code: str
    name: str
    partner_id: int | None = None


class JournalLineSchema(BaseModel):
    account_id: int
    name: str = ""
    partner_id: int | None = None
    analytic_account_id: int | None = None
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    amount_currency: Decimal | None = None
    currency_id: int | None = None
    date_maturity: date | None = None


class InvoiceLineSchema(BaseModel):
    account_id: int
    name: str
    quantity: Decimal = ONE
    price_unit: Decimal = ZERO
    discount: Decimal = ZERO
    tax_ids: list[int] = Field(default_factory=list)
    analytic_account_id: int | None = None
    product_id: int | None = None


class MoveCreateRequest(BaseModel):
    move_type: str = "out_invoice"  # entry, out_invoice, in_invoice, out_refund, in_refund
    journal_id: int
    partner_id: int | None = None
    date: date
    invoice_date_due: date | None = None
    currency_id: int | None = None
    ref: str | None = None
    invoice_origin: str | None = None
    narration: str | None = None
    journal_lines: list[JournalLineSchema] = Field(default_factory=list)
    invoice_lines: list[InvoiceLineSchema] = Field(default_factory=list)


class ReverseMoveRequest(BaseModel):
    reversal_date: date | None = None
    reason: str = "Manual Reversal"
    auto_post: bool = True
    auto_reconcile: bool = True


class RegisterPaymentRequest(BaseModel):
    payment_type: str = "INBOUND"  # INBOUND | OUTBOUND
    partner_type: str = "CUSTOMER"  # CUSTOMER | SUPPLIER
    partner_id: int
    journal_id: int
    amount: Decimal = Field(..., gt=Decimal("0"))
    date: date
    currency_id: int | None = None
    ref: str | None = None
    invoice_move_id: int | None = None


class ReconcileRequest(BaseModel):
    debit_line_id: int
    credit_line_id: int
    amount_currency: Decimal | None = None


def _handle_accounting_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (UnbalancedMoveError, FiscalLockDateError, PostedImmutabilityError)):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, AccountingError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


async def _serialize_move(session: AsyncSession, move: AccountMove) -> dict[str, Any]:
    partner = await session.get(ResPartner, move.partner_id) if move.partner_id else None
    journal = await session.get(AccountJournal, move.journal_id)
    currency = await session.get(ResCurrency, move.currency_id)
    acct_ids = {l.account_id for l in move.lines}
    accts = (
        await session.execute(select(AccountAccount).where(AccountAccount.id.in_(acct_ids)))
    ).scalars().all() if acct_ids else []
    acct_map = {a.id: a for a in accts}

    return {
        "id": move.id,
        "name": move.name,
        "ref": move.ref,
        "move_type": move.move_type,
        "state": move.state,
        "date": move.date.isoformat(),
        "invoice_date": move.invoice_date.isoformat() if move.invoice_date else None,
        "invoice_date_due": move.invoice_date_due.isoformat() if move.invoice_date_due else None,
        "journal_id": move.journal_id,
        "journal_name": journal.name if journal else "",
        "journal_code": journal.code if journal else "",
        "partner_id": move.partner_id,
        "partner_name": partner.name if partner else "",
        "currency_id": move.currency_id,
        "currency_code": currency.code if currency else "USD",
        "currency_symbol": currency.symbol if currency else "$",
        "exchange_rate": str(move.exchange_rate),
        "amount_untaxed": str(quantize_currency(move.amount_untaxed)),
        "amount_tax": str(quantize_currency(move.amount_tax)),
        "amount_total": str(quantize_currency(move.amount_total)),
        "amount_residual": str(quantize_currency(move.amount_residual)),
        "payment_state": move.payment_state,
        "reversed_entry_id": move.reversed_entry_id,
        "invoice_origin": move.invoice_origin,
        "narration": move.narration,
        "lines": [
            {
                "id": l.id,
                "account_id": l.account_id,
                "account_code": acct_map[l.account_id].code if l.account_id in acct_map else "",
                "account_name": acct_map[l.account_id].name if l.account_id in acct_map else "",
                "partner_id": l.partner_id,
                "analytic_account_id": l.analytic_account_id,
                "product_id": l.product_id,
                "name": l.name,
                "quantity": str(l.quantity),
                "price_unit": str(quantize_currency(l.price_unit)),
                "discount": str(l.discount),
                "debit": str(quantize_currency(l.debit)),
                "credit": str(quantize_currency(l.credit)),
                "balance": str(quantize_currency(l.balance)),
                "currency_id": l.currency_id,
                "amount_currency": str(quantize_currency(l.amount_currency)),
                "amount_residual": str(quantize_currency(l.amount_residual)),
                "reconciled": l.reconciled,
                "tax_ids": json.loads(l.tax_ids_json) if l.tax_ids_json else [],
                "is_tax_line": l.is_tax_line,
                "display_type": l.display_type,
            }
            for l in move.lines
        ],
    }


# --- Dashboard Journal Cards ---

@router.get("/dashboard")
async def accounting_dashboard(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    journals = (
        await session.execute(
            select(AccountJournal)
            .where(AccountJournal.company_id == ctx.company_id, AccountJournal.active.is_(True))
            .order_by(AccountJournal.id.asc())
        )
    ).scalars().all()

    cards: list[dict[str, Any]] = []
    for j in journals:
        # Open / Draft moves count & open balance
        draft_cnt = (
            await session.execute(
                select(func.count(AccountMove.id)).where(
                    AccountMove.company_id == ctx.company_id,
                    AccountMove.journal_id == j.id,
                    AccountMove.state == "DRAFT",
                )
            )
        ).scalar_one()

        unpaid_stmt = select(
            func.count(AccountMove.id),
            func.coalesce(func.sum(AccountMove.amount_residual), 0),
        ).where(
            AccountMove.company_id == ctx.company_id,
            AccountMove.journal_id == j.id,
            AccountMove.state == "POSTED",
            AccountMove.payment_state.in_(["NOT_PAID", "PARTIAL"]),
            AccountMove.move_type.in_(["out_invoice", "in_invoice"]),
        )
        unpaid_cnt, unpaid_sum = (await session.execute(unpaid_stmt)).one()

        # For Bank/Cash journals compute current GL account balance
        gl_balance = ZERO
        if j.default_account_id and j.journal_type in ("BANK", "CASH"):
            bal_res = (
                await session.execute(
                    select(func.coalesce(func.sum(AccountMoveLine.debit - AccountMoveLine.credit), 0))
                    .join(AccountMove, AccountMove.id == AccountMoveLine.move_id)
                    .where(
                        AccountMoveLine.company_id == ctx.company_id,
                        AccountMoveLine.account_id == j.default_account_id,
                        AccountMove.state == "POSTED",
                    )
                )
            ).scalar_one()
            gl_balance = quantize_currency(D(bal_res))
        else:
            gl_balance = quantize_currency(D(unpaid_sum))

        cards.append(
            {
                "journal_id": j.id,
                "code": j.code,
                "name": j.name,
                "name_ar": j.name_ar or j.name,
                "journal_type": j.journal_type,
                "draft_count": int(draft_cnt),
                "unreconciled_count": int(unpaid_cnt),
                "open_balance": str(gl_balance),
            }
        )

    return {"journals": cards}


# --- Chart of Accounts ---

@router.get("/accounts")
async def list_accounts(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    accts = (
        await session.execute(
            select(AccountAccount)
            .where(AccountAccount.company_id == ctx.company_id)
            .order_by(AccountAccount.code.asc())
        )
    ).scalars().all()
    return [
        {
            "id": a.id,
            "code": a.code,
            "name": a.name,
            "name_ar": a.name_ar,
            "account_type": a.account_type,
            "reconcilable": a.reconcilable,
            "currency_id": a.currency_id,
            "parent_id": a.parent_id,
        }
        for a in accts
    ]


@router.post("/accounts", status_code=201)
async def create_account(
    payload: AccountCreate,
    ctx: RequestContext = Depends(require_permission("account_account", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    acct = AccountAccount(company_id=ctx.company_id, **payload.model_dump())
    session.add(acct)
    await session.flush()
    return {"id": acct.id, "code": acct.code, "name": acct.name, "account_type": acct.account_type}


# --- Journals, Taxes, Analytic Accounts ---

@router.get("/journals")
async def list_journals(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(AccountJournal)
            .where(AccountJournal.company_id == ctx.company_id, AccountJournal.active.is_(True))
            .order_by(AccountJournal.id.asc())
        )
    ).scalars().all()
    return [
        {
            "id": j.id,
            "code": j.code,
            "name": j.name,
            "name_ar": j.name_ar,
            "journal_type": j.journal_type,
            "default_account_id": j.default_account_id,
            "currency_id": j.currency_id,
        }
        for j in rows
    ]


@router.get("/taxes")
async def list_taxes(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(AccountTax)
            .where(AccountTax.company_id == ctx.company_id, AccountTax.active.is_(True))
            .order_by(AccountTax.sequence.asc(), AccountTax.id.asc())
        )
    ).scalars().all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "name_ar": t.name_ar,
            "tax_type": t.tax_type,
            "amount_type": t.amount_type,
            "amount": str(t.amount),
            "price_include": t.price_include,
            "include_base_amount": t.include_base_amount,
            "sequence": t.sequence,
            "tax_account_id": t.tax_account_id,
        }
        for t in rows
    ]


@router.post("/taxes", status_code=201)
async def create_tax(
    payload: TaxCreate,
    ctx: RequestContext = Depends(require_permission("account_account", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tax = AccountTax(company_id=ctx.company_id, **payload.model_dump())
    session.add(tax)
    await session.flush()
    return {"id": tax.id, "name": tax.name, "amount": str(tax.amount)}


@router.get("/analytics")
async def list_analytics(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(AccountAnalyticAccount)
            .where(AccountAnalyticAccount.company_id == ctx.company_id, AccountAnalyticAccount.active.is_(True))
            .order_by(AccountAnalyticAccount.code.asc())
        )
    ).scalars().all()
    return [{"id": a.id, "code": a.code, "name": a.name, "partner_id": a.partner_id} for a in rows]


@router.post("/analytics", status_code=201)
async def create_analytic(
    payload: AnalyticCreate,
    ctx: RequestContext = Depends(require_permission("account_account", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = AccountAnalyticAccount(company_id=ctx.company_id, **payload.model_dump())
    session.add(row)
    await session.flush()
    return {"id": row.id, "code": row.code, "name": row.name}


# --- Journal Entries & Invoices (`account_move`) ---

@router.get("/moves")
async def list_moves(
    move_type: str | None = Query(default=None),
    state: str | None = Query(default=None),
    journal_id: int | None = Query(default=None),
    partner_id: int | None = Query(default=None),
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = (
        select(AccountMove)
        .options(selectinload(AccountMove.lines))
        .where(AccountMove.company_id == ctx.company_id)
    )
    if move_type:
        types = [t.strip() for t in move_type.split(",") if t.strip()]
        stmt = stmt.where(AccountMove.move_type.in_(types))
    if state:
        stmt = stmt.where(AccountMove.state == state)
    if journal_id:
        stmt = stmt.where(AccountMove.journal_id == journal_id)
    if partner_id:
        stmt = stmt.where(AccountMove.partner_id == partner_id)

    stmt = stmt.order_by(AccountMove.date.desc(), AccountMove.id.desc())
    moves = (await session.execute(stmt)).scalars().all()
    return [await _serialize_move(session, m) for m in moves]


@router.get("/moves/{move_id}")
async def get_move(
    move_id: int,
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        move = await get_move_with_lines(session, move_id)
    except AccountingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if move.company_id != ctx.company_id:
        raise HTTPException(status_code=404, detail="Move not found in active company")
    return await _serialize_move(session, move)


@router.post("/moves", status_code=201)
async def create_move_endpoint(
    payload: MoveCreateRequest,
    ctx: RequestContext = Depends(require_permission("account_move", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        if payload.move_type == "entry":
            move = await create_journal_entry(
                session,
                company_id=ctx.company_id,
                journal_id=payload.journal_id,
                move_date=payload.date,
                ref=payload.ref,
                partner_id=payload.partner_id,
                currency_id=payload.currency_id,
                narration=payload.narration,
                invoice_origin=payload.invoice_origin,
                lines=[
                    JournalLineInput(
                        account_id=l.account_id,
                        name=l.name,
                        partner_id=l.partner_id,
                        analytic_account_id=l.analytic_account_id,
                        debit=l.debit,
                        credit=l.credit,
                        amount_currency=l.amount_currency,
                        currency_id=l.currency_id,
                        date_maturity=l.date_maturity,
                    )
                    for l in payload.journal_lines
                ],
                author_name=ctx.user.full_name,
                author_id=ctx.user.id,
            )
        else:
            if payload.partner_id is None:
                raise AccountingError("partner_id is required for invoices and vendor bills")
            move = await create_invoice_move(
                session,
                company_id=ctx.company_id,
                move_type=payload.move_type,
                journal_id=payload.journal_id,
                partner_id=payload.partner_id,
                invoice_date=payload.date,
                invoice_date_due=payload.invoice_date_due,
                currency_id=payload.currency_id,
                ref=payload.ref,
                invoice_origin=payload.invoice_origin,
                narration=payload.narration,
                invoice_lines=[
                    InvoiceLineInput(
                        account_id=l.account_id,
                        name=l.name,
                        quantity=l.quantity,
                        price_unit=l.price_unit,
                        discount=l.discount,
                        tax_ids=l.tax_ids,
                        analytic_account_id=l.analytic_account_id,
                        product_id=l.product_id,
                    )
                    for l in payload.invoice_lines
                ],
                author_name=ctx.user.full_name,
                author_id=ctx.user.id,
            )
        return await _serialize_move(session, move)
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


class MoveUpdateRequest(BaseModel):
    date: DateType | None = None
    invoice_date_due: DateType | None = None
    partner_id: int | None = None
    currency_id: int | None = None
    ref: str | None = None
    narration: str | None = None
    journal_lines: list[JournalLineSchema] | None = None
    invoice_lines: list[InvoiceLineSchema] | None = None


class FiscalClosingRequest(BaseModel):
    date_from: date
    date_to: date
    retained_earnings_account_id: int | None = None
    lock_fiscal_year: bool = True


class FxRevaluationRequest(BaseModel):
    revaluation_date: date
    auto_reverse_next_day: bool = True


class BankStatementImportRequest(BaseModel):
    journal_id: int
    name: str
    statement_date: date | None = None
    balance_start: Decimal = ZERO
    csv_text: str


class ReconcileStatementLineRequest(BaseModel):
    invoice_move_id: int | None = None
    counterpart_account_id: int | None = None


@router.put("/moves/{move_id}")
async def update_move_endpoint(
    move_id: int,
    payload: MoveUpdateRequest,
    ctx: RequestContext = Depends(require_permission("account_move", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        existing = await get_move_with_lines(session, move_id)
        if existing.company_id != ctx.company_id:
            raise HTTPException(status_code=404, detail="Move not found in active company")
        move = await update_draft_move(
            session,
            move_id,
            move_date=payload.date,
            invoice_date_due=payload.invoice_date_due,
            partner_id=payload.partner_id,
            currency_id=payload.currency_id,
            ref=payload.ref,
            narration=payload.narration,
            journal_lines=(
                [
                    JournalLineInput(
                        account_id=l.account_id,
                        name=l.name,
                        partner_id=l.partner_id,
                        analytic_account_id=l.analytic_account_id,
                        debit=l.debit,
                        credit=l.credit,
                        amount_currency=l.amount_currency,
                        currency_id=l.currency_id,
                        date_maturity=l.date_maturity,
                    )
                    for l in payload.journal_lines
                ]
                if payload.journal_lines is not None
                else None
            ),
            invoice_lines=(
                [
                    InvoiceLineInput(
                        account_id=l.account_id,
                        name=l.name,
                        quantity=l.quantity,
                        price_unit=l.price_unit,
                        discount=l.discount,
                        tax_ids=l.tax_ids,
                        analytic_account_id=l.analytic_account_id,
                        product_id=l.product_id,
                    )
                    for l in payload.invoice_lines
                ]
                if payload.invoice_lines is not None
                else None
            ),
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
        return await _serialize_move(session, move)
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


@router.get("/moves/{move_id}/pdf")
async def download_move_pdf(
    move_id: int,
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> Response:
    try:
        move = await get_move_with_lines(session, move_id)
    except AccountingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if move.company_id != ctx.company_id:
        raise HTTPException(status_code=404, detail="Move not found in active company")
    pdf_bytes, qr_b64 = await generate_invoice_pdf(session, move)
    safe_name = move.name.replace("/", "_").replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.pdf"',
            "X-ZATCA-QR-Base64": qr_b64,
        },
    )


@router.post("/moves/{move_id}/post")
async def post_move_endpoint(
    move_id: int,
    ctx: RequestContext = Depends(require_permission("account_move", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        move = await post_move(session, move_id, author_name=ctx.user.full_name, author_id=ctx.user.id)
        return await _serialize_move(session, move)
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


@router.post("/moves/{move_id}/cancel")
async def cancel_move_endpoint(
    move_id: int,
    ctx: RequestContext = Depends(require_permission("account_move", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        move = await cancel_move(session, move_id, author_name=ctx.user.full_name, author_id=ctx.user.id)
        return await _serialize_move(session, move)
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


@router.post("/moves/{move_id}/reverse")
async def reverse_move_endpoint(
    move_id: int,
    payload: ReverseMoveRequest,
    ctx: RequestContext = Depends(require_permission("account_move", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        rev = await reverse_move(
            session,
            move_id,
            reversal_date=payload.reversal_date,
            reason=payload.reason,
            auto_post=payload.auto_post,
            auto_reconcile=payload.auto_reconcile,
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
        return await _serialize_move(session, rev)
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


# --- Payments & Reconciliation ---

@router.get("/payments")
async def list_payments(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(AccountPayment)
            .where(AccountPayment.company_id == ctx.company_id)
            .order_by(AccountPayment.date.desc(), AccountPayment.id.desc())
        )
    ).scalars().all()
    out = []
    for p in rows:
        partner = await session.get(ResPartner, p.partner_id)
        journal = await session.get(AccountJournal, p.journal_id)
        curr = await session.get(ResCurrency, p.currency_id)
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "payment_type": p.payment_type,
                "partner_type": p.partner_type,
                "partner_id": p.partner_id,
                "partner_name": partner.name if partner else "",
                "journal_id": p.journal_id,
                "journal_name": journal.name if journal else "",
                "amount": str(quantize_currency(p.amount)),
                "currency_id": p.currency_id,
                "currency_code": curr.code if curr else "USD",
                "date": p.date.isoformat(),
                "ref": p.ref,
                "state": p.state,
                "move_id": p.move_id,
            }
        )
    return out


@router.post("/payments", status_code=201)
async def create_payment_endpoint(
    payload: RegisterPaymentRequest,
    ctx: RequestContext = Depends(require_permission("account_payment", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        payment, pay_move, partial = await register_payment(
            session,
            company_id=ctx.company_id,
            payment_type=payload.payment_type,
            partner_type=payload.partner_type,
            partner_id=payload.partner_id,
            journal_id=payload.journal_id,
            amount=payload.amount,
            payment_date=payload.date,
            currency_id=payload.currency_id,
            ref=payload.ref,
            invoice_move_id=payload.invoice_move_id,
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
        return {
            "payment_id": payment.id,
            "payment_name": payment.name,
            "move_id": pay_move.id,
            "move_name": pay_move.name,
            "amount": str(quantize_currency(payment.amount)),
            "reconciled": partial is not None,
            "exchange_move_id": partial.exchange_move_id if partial else None,
        }
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


@router.post("/reconcile")
async def reconcile_endpoint(
    payload: ReconcileRequest,
    ctx: RequestContext = Depends(require_permission("account_move", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        partial = await reconcile_move_lines(
            session,
            debit_line_id=payload.debit_line_id,
            credit_line_id=payload.credit_line_id,
            amount_currency=payload.amount_currency,
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
        return {
            "partial_id": partial.id,
            "amount": str(quantize_currency(partial.amount)),
            "exchange_move_id": partial.exchange_move_id,
        }
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


# --- Financial Reports & Exports (JSON / CSV / XLSX / PDF) ---

def _export_response(report_data: dict[str, Any], export: str | None) -> Any:
    if export == "csv":
        data = export_report_csv(report_data)
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_data["report"]}.csv"'},
        )
    if export == "xlsx":
        data = export_report_xlsx(report_data)
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{report_data["report"]}.xlsx"'},
        )
    if export == "pdf":
        data = export_report_pdf(report_data)
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{report_data["report"]}.pdf"'},
        )
    return report_data


@router.get("/reports/trial-balance")
async def report_trial_balance(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    target_move: str = Query(default="posted"),
    analytic_account_id: int | None = Query(default=None),
    export: str | None = Query(default=None, description="csv | xlsx | pdf"),
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
):
    data = await generate_trial_balance(
        session,
        company_id=ctx.company_id,
        date_from=date_from,
        date_to=date_to,
        target_move=target_move,
        analytic_account_id=analytic_account_id,
    )
    return _export_response(data, export)


@router.get("/reports/profit-and-loss")
async def report_profit_and_loss(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    target_move: str = Query(default="posted"),
    analytic_account_id: int | None = Query(default=None),
    export: str | None = Query(default=None, description="csv | xlsx | pdf"),
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
):
    data = await generate_profit_and_loss(
        session,
        company_id=ctx.company_id,
        date_from=date_from,
        date_to=date_to,
        target_move=target_move,
        analytic_account_id=analytic_account_id,
    )
    return _export_response(data, export)


@router.get("/reports/balance-sheet")
async def report_balance_sheet(
    date_to: date | None = Query(default=None),
    target_move: str = Query(default="posted"),
    analytic_account_id: int | None = Query(default=None),
    export: str | None = Query(default=None, description="csv | xlsx | pdf"),
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
):
    data = await generate_balance_sheet(
        session,
        company_id=ctx.company_id,
        date_to=date_to,
        target_move=target_move,
        analytic_account_id=analytic_account_id,
    )
    return _export_response(data, export)


@router.get("/reports/general-ledger")
async def report_general_ledger(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    target_move: str = Query(default="posted"),
    account_id: int | None = Query(default=None),
    analytic_account_id: int | None = Query(default=None),
    export: str | None = Query(default=None, description="csv | xlsx | pdf"),
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
):
    data = await generate_general_ledger(
        session,
        company_id=ctx.company_id,
        date_from=date_from,
        date_to=date_to,
        target_move=target_move,
        account_id=account_id,
        analytic_account_id=analytic_account_id,
    )
    return _export_response(data, export)


@router.get("/reports/aged-partner")
async def report_aged_partner(
    report_type: str = Query(default="receivable", description="receivable | payable"),
    as_of_date: date | None = Query(default=None),
    target_move: str = Query(default="posted"),
    export: str | None = Query(default=None, description="csv | xlsx | pdf"),
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
):
    data = await generate_aged_partner_balance(
        session,
        company_id=ctx.company_id,
        report_type=report_type,
        as_of_date=as_of_date,
        target_move=target_move,
    )
    return _export_response(data, export)


# --- Year-End Fiscal Closing Wizard ---

@router.get("/fiscal-closing/preview")
async def fiscal_closing_preview(
    date_from: date = Query(...),
    date_to: date = Query(...),
    ctx: RequestContext = Depends(require_permission("account_move", "read")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await preview_fiscal_year_closing(
            session, company_id=ctx.company_id, date_from=date_from, date_to=date_to
        )
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


@router.post("/fiscal-closing", status_code=201)
async def fiscal_closing_execute(
    payload: FiscalClosingRequest,
    ctx: RequestContext = Depends(require_permission("account_move", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await execute_fiscal_year_closing(
            session,
            company_id=ctx.company_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
            retained_earnings_account_id=payload.retained_earnings_account_id,
            lock_fiscal_year=payload.lock_fiscal_year,
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


@router.get("/fiscal-closing/history")
async def fiscal_closing_history(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(AccountFiscalClosing)
            .where(AccountFiscalClosing.company_id == ctx.company_id)
            .order_by(AccountFiscalClosing.id.desc())
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "date_from": r.date_from.isoformat(),
            "date_to": r.date_to.isoformat(),
            "net_profit": str(quantize_currency(r.net_profit)),
            "closing_move_id": r.closing_move_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# --- Unrealized Foreign Exchange Revaluation (IAS 21) ---

@router.get("/fx-revaluation/preview")
async def fx_revaluation_preview(
    revaluation_date: date = Query(...),
    ctx: RequestContext = Depends(require_permission("account_move", "read")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await preview_fx_revaluation(
            session, company_id=ctx.company_id, revaluation_date=revaluation_date
        )
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


@router.post("/fx-revaluation", status_code=201)
async def fx_revaluation_execute(
    payload: FxRevaluationRequest,
    ctx: RequestContext = Depends(require_permission("account_move", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await execute_fx_revaluation(
            session,
            company_id=ctx.company_id,
            revaluation_date=payload.revaluation_date,
            auto_reverse_next_day=payload.auto_reverse_next_day,
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


# --- Bank Statement Import & Reconciliation ---

@router.get("/bank-statements")
async def list_bank_statements(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmts = (
        await session.execute(
            select(AccountBankStatement)
            .options(selectinload(AccountBankStatement.lines))
            .where(AccountBankStatement.company_id == ctx.company_id)
            .order_by(AccountBankStatement.id.desc())
        )
    ).scalars().all()
    return [await serialize_statement_with_suggestions(session, s) for s in stmts]


@router.post("/bank-statements/import-csv", status_code=201)
async def import_bank_statement_endpoint(
    payload: BankStatementImportRequest,
    ctx: RequestContext = Depends(require_permission("account_payment", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        stmt = await import_bank_statement_csv(
            session,
            company_id=ctx.company_id,
            journal_id=payload.journal_id,
            name=payload.name,
            csv_text=payload.csv_text,
            statement_date=payload.statement_date,
            balance_start=payload.balance_start,
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
        return await serialize_statement_with_suggestions(session, stmt)
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc


@router.post("/bank-statements/lines/{line_id}/reconcile")
async def reconcile_bank_line_endpoint(
    line_id: int,
    payload: ReconcileStatementLineRequest,
    ctx: RequestContext = Depends(require_permission("account_payment", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await reconcile_bank_statement_line(
            session,
            line_id=line_id,
            company_id=ctx.company_id,
            invoice_move_id=payload.invoice_move_id,
            counterpart_account_id=payload.counterpart_account_id,
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
    except AccountingError as exc:
        raise _handle_accounting_error(exc) from exc

