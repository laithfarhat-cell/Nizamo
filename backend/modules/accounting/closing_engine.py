"""Year-End Fiscal Closing Wizard & Unrealized Foreign Exchange Revaluation Engine (IFRS / IAS 21)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.chatter import log_chatter
from backend.core.decimal_utils import D, ZERO, quantize_currency
from backend.core.tenancy import ResCompany, ResCurrency, get_effective_rate
from backend.modules.accounting.models import (
    ACCOUNT_TYPE_EQUITY,
    AccountAccount,
    AccountFiscalClosing,
    AccountJournal,
    AccountMove,
    AccountMoveLine,
    EXPENSE_TYPES,
    INCOME_TYPES,
)
from backend.modules.accounting.posting_engine import (
    AccountingError,
    JournalLineInput,
    create_journal_entry,
    post_move,
    reverse_move,
)
from backend.modules.accounting.reconciliation import _find_fx_accounts_and_journal


async def _get_general_journal(session: AsyncSession, company_id: int) -> AccountJournal:
    journal = (
        await session.execute(
            select(AccountJournal)
            .where(
                AccountJournal.company_id == company_id,
                AccountJournal.journal_type == "GENERAL",
            )
            .order_by(AccountJournal.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if journal is None:
        raise AccountingError("No GENERAL journal configured for company")
    return journal


async def _get_retained_earnings_account(
    session: AsyncSession, company_id: int, account_id: int | None = None
) -> AccountAccount:
    if account_id is not None:
        acct = await session.get(AccountAccount, account_id)
        if acct is None or acct.company_id != company_id:
            raise AccountingError("Selected Retained Earnings account not found in company")
        return acct

    acct = (
        await session.execute(
            select(AccountAccount).where(
                AccountAccount.company_id == company_id,
                AccountAccount.code == "310000",
            )
        )
    ).scalar_one_or_none()
    if acct is None:
        acct = (
            await session.execute(
                select(AccountAccount)
                .where(
                    AccountAccount.company_id == company_id,
                    AccountAccount.account_type == ACCOUNT_TYPE_EQUITY,
                )
                .order_by(AccountAccount.code.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if acct is None:
        raise AccountingError("No Equity / Retained Earnings account found for company")
    return acct


async def preview_fiscal_year_closing(
    session: AsyncSession,
    *,
    company_id: int,
    date_from: date,
    date_to: date,
) -> dict[str, Any]:
    """Preview P&L account balances to be zeroed into Retained Earnings at fiscal year-end."""
    stmt = (
        select(
            AccountAccount.id,
            AccountAccount.code,
            AccountAccount.name,
            AccountAccount.account_type,
            func.coalesce(func.sum(AccountMoveLine.debit), 0).label("debit"),
            func.coalesce(func.sum(AccountMoveLine.credit), 0).label("credit"),
        )
        .join(AccountMoveLine, AccountMoveLine.account_id == AccountAccount.id)
        .join(AccountMove, AccountMove.id == AccountMoveLine.move_id)
        .where(
            AccountMoveLine.company_id == company_id,
            AccountMove.company_id == company_id,
            AccountMove.state == "POSTED",
            AccountMoveLine.date >= date_from,
            AccountMoveLine.date <= date_to,
            AccountAccount.account_type.in_(list(INCOME_TYPES | EXPENSE_TYPES)),
        )
        .group_by(
            AccountAccount.id,
            AccountAccount.code,
            AccountAccount.name,
            AccountAccount.account_type,
        )
        .order_by(AccountAccount.code.asc())
    )
    rows = (await session.execute(stmt)).all()

    accounts_to_close: list[dict[str, Any]] = []
    net_profit = ZERO  # Credit - Debit across all P&L accounts
    for r in rows:
        net_dr_minus_cr = quantize_currency(D(r.debit) - D(r.credit), 2)
        if net_dr_minus_cr == ZERO:
            continue
        # To zero an account with net debit > 0, we CREDIT it; with net credit > 0, we DEBIT it
        close_dr = abs(net_dr_minus_cr) if net_dr_minus_cr < ZERO else ZERO
        close_cr = net_dr_minus_cr if net_dr_minus_cr > ZERO else ZERO
        net_profit += close_dr - close_cr
        accounts_to_close.append(
            {
                "account_id": r.id,
                "code": r.code,
                "name": r.name,
                "account_type": r.account_type,
                "closing_debit": str(close_dr),
                "closing_credit": str(close_cr),
            }
        )

    re_acct = await _get_retained_earnings_account(session, company_id)
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "accounts_count": len(accounts_to_close),
        "accounts": accounts_to_close,
        "net_profit": str(quantize_currency(net_profit, 2)),
        "retained_earnings_account_id": re_acct.id,
        "retained_earnings_account_label": f"{re_acct.code} {re_acct.name}",
    }


async def execute_fiscal_year_closing(
    session: AsyncSession,
    *,
    company_id: int,
    date_from: date,
    date_to: date,
    retained_earnings_account_id: int | None = None,
    lock_fiscal_year: bool = True,
    author_name: str = "System",
    author_id: int | None = None,
) -> dict[str, Any]:
    """Close fiscal period: zero all Income/Expense accounts into Retained Earnings & lock fiscal year."""
    preview = await preview_fiscal_year_closing(
        session, company_id=company_id, date_from=date_from, date_to=date_to
    )
    if preview["accounts_count"] == 0:
        raise AccountingError(
            f"No non-zero P&L account balances found between {date_from} and {date_to} to close."
        )

    re_acct = await _get_retained_earnings_account(
        session, company_id, retained_earnings_account_id
    )
    journal = await _get_general_journal(session, company_id)

    lines: list[JournalLineInput] = []
    for item in preview["accounts"]:
        lines.append(
            JournalLineInput(
                account_id=item["account_id"],
                name=f"Year-End Closing ({date_from}..{date_to}): {item['code']} {item['name']}",
                debit=D(item["closing_debit"]),
                credit=D(item["closing_credit"]),
            )
        )

    net_profit = D(preview["net_profit"])
    if net_profit > ZERO:
        lines.append(
            JournalLineInput(
                account_id=re_acct.id,
                name=f"Net Profit Transferred to Retained Earnings ({date_to.year})",
                debit=ZERO,
                credit=net_profit,
            )
        )
    elif net_profit < ZERO:
        lines.append(
            JournalLineInput(
                account_id=re_acct.id,
                name=f"Net Loss Transferred to Retained Earnings ({date_to.year})",
                debit=abs(net_profit),
                credit=ZERO,
            )
        )

    closing_move = await create_journal_entry(
        session,
        company_id=company_id,
        journal_id=journal.id,
        move_date=date_to,
        ref=f"FY {date_to.year} Year-End Closing",
        narration=f"Automated IFRS Year-End Closing transferring Net Profit {net_profit} to {re_acct.code} {re_acct.name}",
        lines=lines,
        author_name=author_name,
        author_id=author_id,
    )
    closing_move = await post_move(
        session, closing_move.id, author_name=author_name, author_id=author_id
    )

    closing_record = AccountFiscalClosing(
        company_id=company_id,
        name=f"FY {date_to.year} Closing ({date_from} → {date_to})",
        date_from=date_from,
        date_to=date_to,
        net_profit=net_profit,
        retained_earnings_account_id=re_acct.id,
        closing_move_id=closing_move.id,
    )
    session.add(closing_record)

    company = await session.get(ResCompany, company_id)
    if lock_fiscal_year and company is not None:
        old_lock = company.fiscalyear_lock_date
        company.fiscalyear_lock_date = date_to
        await log_chatter(
            session,
            company_id=company_id,
            res_model="res_company",
            res_id=company.id,
            body=f"Fiscal year closed through {date_to} via {closing_move.name} (Net Profit: {net_profit})",
            message_type="state_change",
            author_name=author_name,
            author_id=author_id,
            diffs=[("fiscalyear_lock_date", "Fiscal Year Lock Date", old_lock, date_to)],
        )

    await session.flush()
    return {
        "closing_id": closing_record.id,
        "name": closing_record.name,
        "closing_move_id": closing_move.id,
        "closing_move_name": closing_move.name,
        "net_profit": str(quantize_currency(net_profit, 2)),
        "retained_earnings_account": f"{re_acct.code} {re_acct.name}",
        "fiscalyear_lock_date": date_to.isoformat() if lock_fiscal_year else None,
    }


async def preview_fx_revaluation(
    session: AsyncSession,
    *,
    company_id: int,
    revaluation_date: date,
) -> dict[str, Any]:
    """Compute Unrealized Foreign Exchange Gain/Loss on open foreign-currency AR/AP balances (IAS 21)."""
    company = await session.get(ResCompany, company_id)
    if company is None:
        raise AccountingError(f"Company {company_id} not found")
    base_curr = await session.get(ResCurrency, company.currency_id)
    base_prec = base_curr.decimal_places if base_curr else 2

    stmt = (
        select(AccountMoveLine, AccountAccount.code, AccountAccount.name, ResCurrency.code.label("curr_code"))
        .join(AccountMove, AccountMove.id == AccountMoveLine.move_id)
        .join(AccountAccount, AccountAccount.id == AccountMoveLine.account_id)
        .join(ResCurrency, ResCurrency.id == AccountMoveLine.currency_id)
        .where(
            AccountMoveLine.company_id == company_id,
            AccountMove.state == "POSTED",
            AccountMoveLine.date <= revaluation_date,
            AccountMoveLine.reconciled.is_(False),
            AccountAccount.reconcilable.is_(True),
            AccountMoveLine.currency_id != company.currency_id,
            AccountMoveLine.amount_residual_currency != ZERO,
        )
        .order_by(AccountAccount.code.asc(), AccountMoveLine.id.asc())
    )
    rows = (await session.execute(stmt)).all()

    exposures: list[dict[str, Any]] = []
    total_adjustment = ZERO

    for r in rows:
        line = r.AccountMoveLine
        closing_rate = await get_effective_rate(
            session,
            company_id=company_id,
            currency_id=line.currency_id,
            as_of_date=revaluation_date,
        )
        booked_base = quantize_currency(D(line.amount_residual), base_prec)
        revalued_base = quantize_currency(D(line.amount_residual_currency) * closing_rate, base_prec)
        diff = quantize_currency(revalued_base - booked_base, base_prec)
        if diff == ZERO:
            continue

        total_adjustment += diff
        exposures.append(
            {
                "line_id": line.id,
                "account_id": line.account_id,
                "account_code": r.code,
                "account_name": r.name,
                "partner_id": line.partner_id,
                "currency_id": line.currency_id,
                "currency_code": r.curr_code,
                "open_foreign_amount": str(quantize_currency(line.amount_residual_currency, 2)),
                "booked_base_amount": str(booked_base),
                "closing_rate": str(closing_rate),
                "revalued_base_amount": str(revalued_base),
                "unrealized_gain_loss": str(diff),  # >0 = Unrealized Gain; <0 = Unrealized Loss
            }
        )

    return {
        "revaluation_date": revaluation_date.isoformat(),
        "items_count": len(exposures),
        "exposures": exposures,
        "net_unrealized_fx": str(quantize_currency(total_adjustment, base_prec)),
    }


async def execute_fx_revaluation(
    session: AsyncSession,
    *,
    company_id: int,
    revaluation_date: date,
    auto_reverse_next_day: bool = True,
    author_name: str = "System",
    author_id: int | None = None,
) -> dict[str, Any]:
    """Post balanced Unrealized FX Revaluation entry (and optional next-period auto-reversal)."""
    preview = await preview_fx_revaluation(
        session, company_id=company_id, revaluation_date=revaluation_date
    )
    if preview["items_count"] == 0:
        raise AccountingError(
            f"No unrealized FX revaluation differences found as of {revaluation_date}."
        )

    gain_acct, loss_acct, fx_journal = await _find_fx_accounts_and_journal(session, company_id)
    company = await session.get(ResCompany, company_id)
    assert company is not None

    lines: list[JournalLineInput] = []
    for exp in preview["exposures"]:
        diff = D(exp["unrealized_gain_loss"])
        abs_diff = abs(diff)
        if diff > ZERO:
            # Increase AR (or decrease AP liability) -> Debit Control Account, Credit Unrealized FX Gain
            lines.append(
                JournalLineInput(
                    account_id=exp["account_id"],
                    partner_id=exp["partner_id"],
                    name=f"Unrealized FX Revaluation ({exp['currency_code']} @ {exp['closing_rate']})",
                    debit=abs_diff,
                    credit=ZERO,
                    amount_currency=ZERO,
                    currency_id=exp["currency_id"],
                )
            )
            lines.append(
                JournalLineInput(
                    account_id=gain_acct.id,
                    partner_id=exp["partner_id"],
                    name=f"Unrealized Foreign Exchange Gain ({exp['currency_code']})",
                    debit=ZERO,
                    credit=abs_diff,
                    amount_currency=-abs_diff,
                    currency_id=company.currency_id,
                )
            )
        else:
            # Decrease AR (or increase AP liability) -> Debit Unrealized FX Loss, Credit Control Account
            lines.append(
                JournalLineInput(
                    account_id=loss_acct.id,
                    partner_id=exp["partner_id"],
                    name=f"Unrealized Foreign Exchange Loss ({exp['currency_code']})",
                    debit=abs_diff,
                    credit=ZERO,
                    amount_currency=abs_diff,
                    currency_id=company.currency_id,
                )
            )
            lines.append(
                JournalLineInput(
                    account_id=exp["account_id"],
                    partner_id=exp["partner_id"],
                    name=f"Unrealized FX Revaluation ({exp['currency_code']} @ {exp['closing_rate']})",
                    debit=ZERO,
                    credit=abs_diff,
                    amount_currency=ZERO,
                    currency_id=exp["currency_id"],
                )
            )

    reval_move = await create_journal_entry(
        session,
        company_id=company_id,
        journal_id=fx_journal.id,
        move_date=revaluation_date,
        ref=f"Unrealized FX Revaluation ({revaluation_date.isoformat()})",
        lines=lines,
        author_name=author_name,
        author_id=author_id,
    )
    reval_move = await post_move(
        session, reval_move.id, author_name=author_name, author_id=author_id
    )

    reversal_move_id: int | None = None
    reversal_move_name: str | None = None
    if auto_reverse_next_day:
        rev_date = revaluation_date + timedelta(days=1)
        rev = await reverse_move(
            session,
            reval_move.id,
            reversal_date=rev_date,
            reason="Auto-reversal of period-end Unrealized FX Revaluation",
            auto_post=True,
            auto_reconcile=False,
            author_name=author_name,
            author_id=author_id,
        )
        reversal_move_id = rev.id
        reversal_move_name = rev.name

    return {
        "revaluation_move_id": reval_move.id,
        "revaluation_move_name": reval_move.name,
        "reversal_move_id": reversal_move_id,
        "reversal_move_name": reversal_move_name,
        "net_unrealized_fx": preview["net_unrealized_fx"],
        "items_revalued": preview["items_count"],
    }
