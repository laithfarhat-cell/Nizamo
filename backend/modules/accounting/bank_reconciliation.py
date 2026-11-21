"""Bank Statement CSV Import, Smart Open Invoice Auto-Matching & One-Click Reconciliation."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.chatter import log_chatter
from backend.core.decimal_utils import D, ZERO, quantize_currency
from backend.core.tenancy import ResPartner
from backend.modules.accounting.models import (
    AccountBankStatement,
    AccountBankStatementLine,
    AccountJournal,
    AccountMove,
)
from backend.modules.accounting.posting_engine import (
    AccountingError,
    JournalLineInput,
    create_journal_entry,
    post_move,
)
from backend.modules.accounting.reconciliation import register_payment


async def import_bank_statement_csv(
    session: AsyncSession,
    *,
    company_id: int,
    journal_id: int,
    name: str,
    csv_text: str,
    statement_date: date | None = None,
    balance_start: Decimal = ZERO,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountBankStatement:
    """Parse CSV (`date,ref,partner,amount`) into an AccountBankStatement with smart partner resolution."""
    journal = await session.get(AccountJournal, journal_id)
    if journal is None or journal.company_id != company_id:
        raise AccountingError("Bank/Cash journal not found in active company")
    if journal.journal_type not in ("BANK", "CASH"):
        raise AccountingError("Bank statements can only be imported onto BANK or CASH journals")

    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    parsed_rows = list(reader)
    if not parsed_rows:
        raise AccountingError("CSV statement contains no data rows. Expected columns: date,ref,partner,amount")

    partners = (
        await session.execute(select(ResPartner).where(ResPartner.company_id == company_id))
    ).scalars().all()

    stmt_date = statement_date or date.today()
    statement = AccountBankStatement(
        company_id=company_id,
        name=name,
        journal_id=journal_id,
        date=stmt_date,
        balance_start=quantize_currency(balance_start, 2),
        balance_end_real=quantize_currency(balance_start, 2),
        state="OPEN",
    )
    session.add(statement)
    await session.flush()

    running_bal = D(balance_start)
    for row in parsed_rows:
        # Normalize keys case-insensitively
        norm = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        raw_date = norm.get("date") or stmt_date.isoformat()
        line_date = date.fromisoformat(raw_date)
        ref = norm.get("ref") or norm.get("reference") or norm.get("description") or "Bank Transaction"
        partner_raw = norm.get("partner") or norm.get("counterparty") or ""
        amt_str = norm.get("amount") or "0"
        amt = quantize_currency(D(amt_str), 2)
        if amt == ZERO:
            continue

        matched_partner_id: int | None = None
        if partner_raw:
            plower = partner_raw.lower()
            for p in partners:
                if plower in p.name.lower() or p.name.lower() in plower:
                    matched_partner_id = p.id
                    break

        running_bal += amt
        session.add(
            AccountBankStatementLine(
                company_id=company_id,
                statement_id=statement.id,
                date=line_date,
                payment_ref=ref,
                partner_name_raw=partner_raw or None,
                partner_id=matched_partner_id,
                amount=amt,
                is_reconciled=False,
            )
        )

    statement.balance_end_real = quantize_currency(running_bal, 2)
    await session.flush()

    await log_chatter(
        session,
        company_id=company_id,
        res_model="account_bank_statement",
        res_id=statement.id,
        body=f"Imported bank statement '{statement.name}' ({len(parsed_rows)} lines, ending balance {statement.balance_end_real})",
        author_name=author_name,
        author_id=author_id,
    )
    return await get_statement_with_lines(session, statement.id)


async def get_statement_with_lines(session: AsyncSession, statement_id: int) -> AccountBankStatement:
    stmt = (
        select(AccountBankStatement)
        .options(selectinload(AccountBankStatement.lines))
        .where(AccountBankStatement.id == statement_id)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise AccountingError(f"Bank statement id={statement_id} not found")
    return row


async def serialize_statement_with_suggestions(
    session: AsyncSession, statement: AccountBankStatement
) -> dict[str, Any]:
    """Serialize statement lines along with auto-suggested open Customer Invoices / Vendor Bills."""
    journal = await session.get(AccountJournal, statement.journal_id)
    open_moves = (
        await session.execute(
            select(AccountMove)
            .where(
                AccountMove.company_id == statement.company_id,
                AccountMove.state == "POSTED",
                AccountMove.payment_state.in_(["NOT_PAID", "PARTIAL"]),
                AccountMove.move_type.in_(["out_invoice", "in_invoice"]),
            )
            .order_by(AccountMove.date.asc())
        )
    ).scalars().all()

    partners = (
        await session.execute(select(ResPartner).where(ResPartner.company_id == statement.company_id))
    ).scalars().all()
    pmap = {p.id: p.name for p in partners}

    lines_out = []
    for line in statement.lines:
        suggestions = []
        if not line.is_reconciled:
            abs_amt = abs(D(line.amount))
            expected_type = "out_invoice" if D(line.amount) > ZERO else "in_invoice"
            for m in open_moves:
                if m.move_type != expected_type:
                    continue
                score = 0
                if quantize_currency(m.amount_residual, 2) == abs_amt:
                    score += 60
                elif quantize_currency(m.amount_residual, 2) >= abs_amt:
                    score += 20
                if line.partner_id and m.partner_id == line.partner_id:
                    score += 30
                if m.name and m.name.lower() in line.payment_ref.lower():
                    score += 40
                if score >= 30:
                    suggestions.append(
                        {
                            "move_id": m.id,
                            "move_name": m.name,
                            "partner_id": m.partner_id,
                            "partner_name": pmap.get(m.partner_id or 0, ""),
                            "amount_residual": str(quantize_currency(m.amount_residual, 2)),
                            "confidence": min(100, score),
                        }
                    )
            suggestions.sort(key=lambda x: x["confidence"], reverse=True)

        lines_out.append(
            {
                "id": line.id,
                "date": line.date.isoformat(),
                "payment_ref": line.payment_ref,
                "partner_name": pmap.get(line.partner_id or 0, line.partner_name_raw or ""),
                "partner_id": line.partner_id,
                "amount": str(quantize_currency(line.amount, 2)),
                "is_reconciled": line.is_reconciled,
                "matched_move_id": line.matched_move_id,
                "payment_move_id": line.payment_move_id,
                "suggestions": suggestions[:3],
            }
        )

    return {
        "id": statement.id,
        "name": statement.name,
        "journal_id": statement.journal_id,
        "journal_name": journal.name if journal else "",
        "date": statement.date.isoformat(),
        "balance_start": str(quantize_currency(statement.balance_start, 2)),
        "balance_end_real": str(quantize_currency(statement.balance_end_real, 2)),
        "state": statement.state,
        "reconciled_count": sum(1 for l in statement.lines if l.is_reconciled),
        "total_lines": len(statement.lines),
        "lines": lines_out,
    }


async def reconcile_bank_statement_line(
    session: AsyncSession,
    *,
    line_id: int,
    company_id: int,
    invoice_move_id: int | None = None,
    counterpart_account_id: int | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> dict[str, Any]:
    """One-click bank reconciliation: either settle an open invoice OR post directly to an offset GL account."""
    line = await session.get(AccountBankStatementLine, line_id)
    if line is None or line.company_id != company_id:
        raise AccountingError(f"Bank statement line id={line_id} not found")
    if line.is_reconciled:
        raise AccountingError("Bank statement line is already reconciled")

    statement = await get_statement_with_lines(session, line.statement_id)
    journal = await session.get(AccountJournal, statement.journal_id)
    if journal is None or journal.default_account_id is None:
        raise AccountingError("Bank journal is missing default_account_id")

    amt = D(line.amount)
    abs_amt = abs(amt)

    if invoice_move_id is not None:
        inv = await session.get(AccountMove, invoice_move_id)
        if inv is None or inv.company_id != company_id:
            raise AccountingError(f"Invoice id={invoice_move_id} not found")
        if inv.partner_id is None:
            raise AccountingError("Target invoice has no partner")
        is_inbound = amt > ZERO
        _payment, pay_move, _partial = await register_payment(
            session,
            company_id=company_id,
            payment_type="INBOUND" if is_inbound else "OUTBOUND",
            partner_type="CUSTOMER" if is_inbound else "SUPPLIER",
            partner_id=inv.partner_id,
            journal_id=statement.journal_id,
            amount=abs_amt,
            payment_date=line.date,
            currency_id=inv.currency_id,
            ref=f"{statement.name} / {line.payment_ref}",
            invoice_move_id=inv.id,
            author_name=author_name,
            author_id=author_id,
        )
        line.matched_move_id = inv.id
        line.payment_move_id = pay_move.id
        line.partner_id = inv.partner_id
        line.is_reconciled = True
    elif counterpart_account_id is not None:
        # Direct bank fee / interest / miscellaneous bank posting
        if amt > ZERO:
            j_lines = [
                JournalLineInput(account_id=journal.default_account_id, name=line.payment_ref, debit=abs_amt, credit=ZERO),
                JournalLineInput(account_id=counterpart_account_id, name=line.payment_ref, debit=ZERO, credit=abs_amt),
            ]
        else:
            j_lines = [
                JournalLineInput(account_id=counterpart_account_id, name=line.payment_ref, debit=abs_amt, credit=ZERO),
                JournalLineInput(account_id=journal.default_account_id, name=line.payment_ref, debit=ZERO, credit=abs_amt),
            ]
        bank_move = await create_journal_entry(
            session,
            company_id=company_id,
            journal_id=statement.journal_id,
            move_date=line.date,
            ref=f"{statement.name} / {line.payment_ref}",
            partner_id=line.partner_id,
            lines=j_lines,
            author_name=author_name,
            author_id=author_id,
        )
        bank_move = await post_move(session, bank_move.id, author_name=author_name, author_id=author_id)
        line.payment_move_id = bank_move.id
        line.is_reconciled = True
    else:
        raise AccountingError("Provide either invoice_move_id or counterpart_account_id to reconcile")

    await session.flush()

    if all(l.is_reconciled for l in statement.lines):
        statement.state = "POSTED"
    await session.flush()

    return {
        "line_id": line.id,
        "is_reconciled": line.is_reconciled,
        "matched_move_id": line.matched_move_id,
        "payment_move_id": line.payment_move_id,
        "statement_state": statement.state,
    }
