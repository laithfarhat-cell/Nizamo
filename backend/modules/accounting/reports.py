"""Real-Time SQL Financial Reporting Engine with Drill-Down and CSV / XLSX / PDF Export."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.decimal_utils import D, ZERO, quantize_currency
from backend.core.tenancy import ResCompany, ResPartner
from backend.modules.accounting.models import (
    ACCOUNT_TYPE_ASSET_CASH,
    ACCOUNT_TYPE_ASSET_CURRENT,
    ACCOUNT_TYPE_ASSET_NON_CURRENT,
    ACCOUNT_TYPE_ASSET_RECEIVABLE,
    ACCOUNT_TYPE_EQUITY,
    ACCOUNT_TYPE_EXPENSE,
    ACCOUNT_TYPE_EXPENSE_COGS,
    ACCOUNT_TYPE_INCOME,
    ACCOUNT_TYPE_INCOME_OTHER,
    ACCOUNT_TYPE_LIABILITY_CURRENT,
    ACCOUNT_TYPE_LIABILITY_NON_CURRENT,
    ACCOUNT_TYPE_LIABILITY_PAYABLE,
    AccountAccount,
    AccountMove,
    AccountMoveLine,
    EXPENSE_TYPES,
    INCOME_TYPES,
)


def _state_filter(target_move: str):
    if target_move == "all":
        return AccountMove.state.in_(["DRAFT", "POSTED"])
    return AccountMove.state == "POSTED"


async def generate_trial_balance(
    session: AsyncSession,
    *,
    company_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    target_move: str = "posted",
    analytic_account_id: int | None = None,
) -> dict[str, Any]:
    """1. Trial Balance (ميزان المراجعة): Opening Balance, Period Debit, Period Credit, Closing Balance."""
    d_to = date_to or date.today()
    d_from = date_from or date(d_to.year, 1, 1)

    base_filters = [
        AccountMoveLine.company_id == company_id,
        AccountMove.company_id == company_id,
        _state_filter(target_move),
    ]
    if analytic_account_id is not None:
        base_filters.append(AccountMoveLine.analytic_account_id == analytic_account_id)

    # Opening balances (strictly before date_from)
    open_stmt = (
        select(
            AccountMoveLine.account_id,
            func.coalesce(func.sum(AccountMoveLine.debit), 0).label("open_debit"),
            func.coalesce(func.sum(AccountMoveLine.credit), 0).label("open_credit"),
        )
        .join(AccountMove, AccountMove.id == AccountMoveLine.move_id)
        .where(*base_filters, AccountMoveLine.date < d_from)
        .group_by(AccountMoveLine.account_id)
    )
    open_rows = {
        r.account_id: (D(r.open_debit), D(r.open_credit))
        for r in (await session.execute(open_stmt)).all()
    }

    # Period activity (date_from <= date <= date_to)
    period_stmt = (
        select(
            AccountMoveLine.account_id,
            func.coalesce(func.sum(AccountMoveLine.debit), 0).label("p_debit"),
            func.coalesce(func.sum(AccountMoveLine.credit), 0).label("p_credit"),
        )
        .join(AccountMove, AccountMove.id == AccountMoveLine.move_id)
        .where(*base_filters, AccountMoveLine.date >= d_from, AccountMoveLine.date <= d_to)
        .group_by(AccountMoveLine.account_id)
    )
    period_rows = {
        r.account_id: (D(r.p_debit), D(r.p_credit))
        for r in (await session.execute(period_stmt)).all()
    }

    accounts = (
        await session.execute(
            select(AccountAccount)
            .where(AccountAccount.company_id == company_id)
            .order_by(AccountAccount.code.asc())
        )
    ).scalars().all()

    rows: list[dict[str, Any]] = []
    tot_open_dr = tot_open_cr = tot_per_dr = tot_per_cr = tot_close_dr = tot_close_cr = ZERO

    for acct in accounts:
        o_dr, o_cr = open_rows.get(acct.id, (ZERO, ZERO))
        p_dr, p_cr = period_rows.get(acct.id, (ZERO, ZERO))
        open_net = quantize_currency(o_dr - o_cr)
        open_dr = open_net if open_net > ZERO else ZERO
        open_cr = -open_net if open_net < ZERO else ZERO

        p_dr = quantize_currency(p_dr)
        p_cr = quantize_currency(p_cr)

        close_net = quantize_currency(open_net + p_dr - p_cr)
        close_dr = close_net if close_net > ZERO else ZERO
        close_cr = -close_net if close_net < ZERO else ZERO

        if open_net == ZERO and p_dr == ZERO and p_cr == ZERO:
            continue

        tot_open_dr += open_dr
        tot_open_cr += open_cr
        tot_per_dr += p_dr
        tot_per_cr += p_cr
        tot_close_dr += close_dr
        tot_close_cr += close_cr

        rows.append(
            {
                "account_id": acct.id,
                "code": acct.code,
                "name": acct.name,
                "name_ar": acct.name_ar or acct.name,
                "account_type": acct.account_type,
                "opening_debit": str(open_dr),
                "opening_credit": str(open_cr),
                "opening_balance": str(open_net),
                "period_debit": str(p_dr),
                "period_credit": str(p_cr),
                "closing_debit": str(close_dr),
                "closing_credit": str(close_cr),
                "closing_balance": str(close_net),
            }
        )

    return {
        "report": "trial_balance",
        "title": "Trial Balance",
        "title_ar": "ميزان المراجعة",
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "target_move": target_move,
        "rows": rows,
        "totals": {
            "opening_debit": str(quantize_currency(tot_open_dr)),
            "opening_credit": str(quantize_currency(tot_open_cr)),
            "period_debit": str(quantize_currency(tot_per_dr)),
            "period_credit": str(quantize_currency(tot_per_cr)),
            "closing_debit": str(quantize_currency(tot_close_dr)),
            "closing_credit": str(quantize_currency(tot_close_cr)),
            "is_balanced": quantize_currency(tot_close_dr) == quantize_currency(tot_close_cr),
        },
    }


async def generate_profit_and_loss(
    session: AsyncSession,
    *,
    company_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    target_move: str = "posted",
    analytic_account_id: int | None = None,
) -> dict[str, Any]:
    """2. Profit & Loss / Income Statement (قائمة الدخل): Income, COGS, Gross Profit, Expenses, Net Profit."""
    d_to = date_to or date.today()
    d_from = date_from or date(d_to.year, 1, 1)

    filters = [
        AccountMoveLine.company_id == company_id,
        AccountMove.company_id == company_id,
        _state_filter(target_move),
        AccountMoveLine.date >= d_from,
        AccountMoveLine.date <= d_to,
        AccountAccount.account_type.in_(list(INCOME_TYPES | EXPENSE_TYPES)),
    ]
    if analytic_account_id is not None:
        filters.append(AccountMoveLine.analytic_account_id == analytic_account_id)

    stmt = (
        select(
            AccountAccount.id,
            AccountAccount.code,
            AccountAccount.name,
            AccountAccount.name_ar,
            AccountAccount.account_type,
            func.coalesce(func.sum(AccountMoveLine.debit), 0).label("debit"),
            func.coalesce(func.sum(AccountMoveLine.credit), 0).label("credit"),
        )
        .join(AccountMoveLine, AccountMoveLine.account_id == AccountAccount.id)
        .join(AccountMove, AccountMove.id == AccountMoveLine.move_id)
        .where(*filters)
        .group_by(
            AccountAccount.id,
            AccountAccount.code,
            AccountAccount.name,
            AccountAccount.name_ar,
            AccountAccount.account_type,
        )
        .order_by(AccountAccount.code.asc())
    )
    results = (await session.execute(stmt)).all()

    operating_income: list[dict[str, Any]] = []
    other_income: list[dict[str, Any]] = []
    cogs: list[dict[str, Any]] = []
    operating_expenses: list[dict[str, Any]] = []

    tot_op_income = tot_other_income = tot_cogs = tot_op_exp = ZERO

    for r in results:
        dr = quantize_currency(D(r.debit))
        cr = quantize_currency(D(r.credit))
        if r.account_type in INCOME_TYPES:
            amt = quantize_currency(cr - dr)
            item = {
                "account_id": r.id,
                "code": r.code,
                "name": r.name,
                "name_ar": r.name_ar or r.name,
                "amount": str(amt),
            }
            if r.account_type == ACCOUNT_TYPE_INCOME:
                operating_income.append(item)
                tot_op_income += amt
            else:
                other_income.append(item)
                tot_other_income += amt
        else:
            amt = quantize_currency(dr - cr)
            item = {
                "account_id": r.id,
                "code": r.code,
                "name": r.name,
                "name_ar": r.name_ar or r.name,
                "amount": str(amt),
            }
            if r.account_type == ACCOUNT_TYPE_EXPENSE_COGS:
                cogs.append(item)
                tot_cogs += amt
            else:
                operating_expenses.append(item)
                tot_op_exp += amt

    total_income = quantize_currency(tot_op_income + tot_other_income)
    gross_profit = quantize_currency(tot_op_income - tot_cogs)
    net_profit = quantize_currency(total_income - tot_cogs - tot_op_exp)

    return {
        "report": "profit_and_loss",
        "title": "Profit & Loss Statement",
        "title_ar": "قائمة الدخل",
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "target_move": target_move,
        "sections": {
            "operating_income": operating_income,
            "other_income": other_income,
            "cost_of_goods_sold": cogs,
            "operating_expenses": operating_expenses,
        },
        "summary": {
            "total_operating_income": str(quantize_currency(tot_op_income)),
            "total_other_income": str(quantize_currency(tot_other_income)),
            "total_income": str(total_income),
            "total_cogs": str(quantize_currency(tot_cogs)),
            "gross_profit": str(gross_profit),
            "total_operating_expenses": str(quantize_currency(tot_op_exp)),
            "net_profit": str(net_profit),
        },
    }


async def generate_balance_sheet(
    session: AsyncSession,
    *,
    company_id: int,
    date_to: date | None = None,
    target_move: str = "posted",
    analytic_account_id: int | None = None,
) -> dict[str, Any]:
    """3. Balance Sheet (الميزانية العمومية) proving Assets == Liabilities + Equity + Unallocated Earnings."""
    d_to = date_to or date.today()

    filters = [
        AccountMoveLine.company_id == company_id,
        AccountMove.company_id == company_id,
        _state_filter(target_move),
        AccountMoveLine.date <= d_to,
    ]
    if analytic_account_id is not None:
        filters.append(AccountMoveLine.analytic_account_id == analytic_account_id)

    stmt = (
        select(
            AccountAccount.id,
            AccountAccount.code,
            AccountAccount.name,
            AccountAccount.name_ar,
            AccountAccount.account_type,
            func.coalesce(func.sum(AccountMoveLine.debit), 0).label("debit"),
            func.coalesce(func.sum(AccountMoveLine.credit), 0).label("credit"),
        )
        .join(AccountMoveLine, AccountMoveLine.account_id == AccountAccount.id)
        .join(AccountMove, AccountMove.id == AccountMoveLine.move_id)
        .where(*filters)
        .group_by(
            AccountAccount.id,
            AccountAccount.code,
            AccountAccount.name,
            AccountAccount.name_ar,
            AccountAccount.account_type,
        )
        .order_by(AccountAccount.code.asc())
    )
    results = (await session.execute(stmt)).all()

    current_assets: list[dict[str, Any]] = []
    non_current_assets: list[dict[str, Any]] = []
    current_liabilities: list[dict[str, Any]] = []
    non_current_liabilities: list[dict[str, Any]] = []
    equity_lines: list[dict[str, Any]] = []

    tot_curr_assets = tot_noncurr_assets = ZERO
    tot_curr_liab = tot_noncurr_liab = ZERO
    tot_equity_booked = ZERO
    unallocated_earnings = ZERO

    for r in results:
        dr = quantize_currency(D(r.debit))
        cr = quantize_currency(D(r.credit))
        bal_dr = quantize_currency(dr - cr)
        bal_cr = quantize_currency(cr - dr)

        item_dr = {
            "account_id": r.id,
            "code": r.code,
            "name": r.name,
            "name_ar": r.name_ar or r.name,
            "balance": str(bal_dr),
        }
        item_cr = {
            "account_id": r.id,
            "code": r.code,
            "name": r.name,
            "name_ar": r.name_ar or r.name,
            "balance": str(bal_cr),
        }

        if r.account_type in (
            ACCOUNT_TYPE_ASSET_CASH,
            ACCOUNT_TYPE_ASSET_RECEIVABLE,
            ACCOUNT_TYPE_ASSET_CURRENT,
        ):
            if bal_dr != ZERO:
                current_assets.append(item_dr)
                tot_curr_assets += bal_dr
        elif r.account_type == ACCOUNT_TYPE_ASSET_NON_CURRENT:
            if bal_dr != ZERO:
                non_current_assets.append(item_dr)
                tot_noncurr_assets += bal_dr
        elif r.account_type in (ACCOUNT_TYPE_LIABILITY_PAYABLE, ACCOUNT_TYPE_LIABILITY_CURRENT):
            if bal_cr != ZERO:
                current_liabilities.append(item_cr)
                tot_curr_liab += bal_cr
        elif r.account_type == ACCOUNT_TYPE_LIABILITY_NON_CURRENT:
            if bal_cr != ZERO:
                non_current_liabilities.append(item_cr)
                tot_noncurr_liab += bal_cr
        elif r.account_type == ACCOUNT_TYPE_EQUITY:
            if bal_cr != ZERO:
                equity_lines.append(item_cr)
                tot_equity_booked += bal_cr
        elif r.account_type in INCOME_TYPES or r.account_type in EXPENSE_TYPES:
            # Cumulative P&L (Income credit - Expense debit) flows into Current Year Unallocated Earnings
            unallocated_earnings += bal_cr

    unallocated_earnings = quantize_currency(unallocated_earnings)
    total_assets = quantize_currency(tot_curr_assets + tot_noncurr_assets)
    total_liabilities = quantize_currency(tot_curr_liab + tot_noncurr_liab)
    total_equity = quantize_currency(tot_equity_booked + unallocated_earnings)
    total_liabilities_and_equity = quantize_currency(total_liabilities + total_equity)
    equilibrium_diff = quantize_currency(total_assets - total_liabilities_and_equity)

    return {
        "report": "balance_sheet",
        "title": "Balance Sheet",
        "title_ar": "الميزانية العمومية",
        "as_of_date": d_to.isoformat(),
        "target_move": target_move,
        "assets": {
            "current_assets": current_assets,
            "non_current_assets": non_current_assets,
            "total_current_assets": str(quantize_currency(tot_curr_assets)),
            "total_non_current_assets": str(quantize_currency(tot_noncurr_assets)),
            "total_assets": str(total_assets),
        },
        "liabilities": {
            "current_liabilities": current_liabilities,
            "non_current_liabilities": non_current_liabilities,
            "total_current_liabilities": str(quantize_currency(tot_curr_liab)),
            "total_non_current_liabilities": str(quantize_currency(tot_noncurr_liab)),
            "total_liabilities": str(total_liabilities),
        },
        "equity": {
            "accounts": equity_lines,
            "booked_equity": str(quantize_currency(tot_equity_booked)),
            "current_year_unallocated_earnings": str(unallocated_earnings),
            "total_equity": str(total_equity),
        },
        "summary": {
            "total_assets": str(total_assets),
            "total_liabilities_and_equity": str(total_liabilities_and_equity),
            "difference": str(equilibrium_diff),
            "is_balanced": equilibrium_diff == ZERO,
        },
    }


async def generate_general_ledger(
    session: AsyncSession,
    *,
    company_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    target_move: str = "posted",
    account_id: int | None = None,
    analytic_account_id: int | None = None,
) -> dict[str, Any]:
    """4. General Ledger (دفتر الأستاذ العام): Chronological running balance per account with drill-down."""
    d_to = date_to or date.today()
    d_from = date_from or date(d_to.year, 1, 1)

    acct_stmt = select(AccountAccount).where(AccountAccount.company_id == company_id)
    if account_id is not None:
        acct_stmt = acct_stmt.where(AccountAccount.id == account_id)
    accounts = (await session.execute(acct_stmt.order_by(AccountAccount.code.asc()))).scalars().all()

    base_filters = [
        AccountMoveLine.company_id == company_id,
        AccountMove.company_id == company_id,
        _state_filter(target_move),
    ]
    if analytic_account_id is not None:
        base_filters.append(AccountMoveLine.analytic_account_id == analytic_account_id)

    # Opening balance per account
    open_stmt = (
        select(
            AccountMoveLine.account_id,
            func.coalesce(func.sum(AccountMoveLine.debit - AccountMoveLine.credit), 0).label("opening"),
        )
        .join(AccountMove, AccountMove.id == AccountMoveLine.move_id)
        .where(*base_filters, AccountMoveLine.date < d_from)
        .group_by(AccountMoveLine.account_id)
    )
    opening_map = {r.account_id: quantize_currency(D(r.opening)) for r in (await session.execute(open_stmt)).all()}

    # Period lines with Move + Partner metadata
    line_filters = [
        *base_filters,
        AccountMoveLine.date >= d_from,
        AccountMoveLine.date <= d_to,
    ]
    if account_id is not None:
        line_filters.append(AccountMoveLine.account_id == account_id)

    lines_stmt = (
        select(
            AccountMoveLine.id,
            AccountMoveLine.account_id,
            AccountMoveLine.move_id,
            AccountMoveLine.date,
            AccountMoveLine.name,
            AccountMoveLine.debit,
            AccountMoveLine.credit,
            AccountMoveLine.amount_currency,
            AccountMove.name.label("move_name"),
            AccountMove.ref.label("move_ref"),
            AccountMove.state.label("move_state"),
            ResPartner.name.label("partner_name"),
        )
        .join(AccountMove, AccountMove.id == AccountMoveLine.move_id)
        .outerjoin(ResPartner, ResPartner.id == AccountMoveLine.partner_id)
        .where(*line_filters)
        .order_by(AccountMoveLine.account_id.asc(), AccountMoveLine.date.asc(), AccountMoveLine.id.asc())
    )
    all_lines = (await session.execute(lines_stmt)).all()
    lines_by_acct: dict[int, list[Any]] = {}
    for row in all_lines:
        lines_by_acct.setdefault(row.account_id, []).append(row)

    ledger_accounts: list[dict[str, Any]] = []
    for acct in accounts:
        opening = opening_map.get(acct.id, ZERO)
        acct_lines = lines_by_acct.get(acct.id, [])
        if opening == ZERO and not acct_lines:
            continue

        running = opening
        tot_dr = ZERO
        tot_cr = ZERO
        entries: list[dict[str, Any]] = []
        for l in acct_lines:
            dr = quantize_currency(D(l.debit))
            cr = quantize_currency(D(l.credit))
            tot_dr += dr
            tot_cr += cr
            running = quantize_currency(running + dr - cr)
            entries.append(
                {
                    "line_id": l.id,
                    "move_id": l.move_id,
                    "move_name": l.move_name,
                    "move_state": l.move_state,
                    "date": l.date.isoformat(),
                    "partner_name": l.partner_name or "",
                    "label": l.name,
                    "ref": l.move_ref or "",
                    "debit": str(dr),
                    "credit": str(cr),
                    "running_balance": str(running),
                }
            )

        ledger_accounts.append(
            {
                "account_id": acct.id,
                "code": acct.code,
                "name": acct.name,
                "name_ar": acct.name_ar or acct.name,
                "opening_balance": str(opening),
                "total_debit": str(quantize_currency(tot_dr)),
                "total_credit": str(quantize_currency(tot_cr)),
                "closing_balance": str(running),
                "entries": entries,
            }
        )

    return {
        "report": "general_ledger",
        "title": "General Ledger",
        "title_ar": "دفتر الأستاذ العام",
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "target_move": target_move,
        "accounts": ledger_accounts,
    }


async def generate_aged_partner_balance(
    session: AsyncSession,
    *,
    company_id: int,
    report_type: str = "receivable",  # receivable | payable
    as_of_date: date | None = None,
    target_move: str = "posted",
) -> dict[str, Any]:
    """5. Partner Aged Receivable & Aged Payable (أعمار الذمم): Current, 1-30, 31-60, 61-90, 90+ Days."""
    as_of = as_of_date or date.today()
    acct_type = (
        ACCOUNT_TYPE_ASSET_RECEIVABLE if report_type == "receivable" else ACCOUNT_TYPE_LIABILITY_PAYABLE
    )

    stmt = (
        select(
            AccountMoveLine.id,
            AccountMoveLine.move_id,
            AccountMoveLine.partner_id,
            AccountMoveLine.date,
            AccountMoveLine.date_maturity,
            AccountMoveLine.amount_residual,
            AccountMove.name.label("move_name"),
            ResPartner.name.label("partner_name"),
        )
        .join(AccountAccount, AccountAccount.id == AccountMoveLine.account_id)
        .join(AccountMove, AccountMove.id == AccountMoveLine.move_id)
        .outerjoin(ResPartner, ResPartner.id == AccountMoveLine.partner_id)
        .where(
            AccountMoveLine.company_id == company_id,
            AccountMove.company_id == company_id,
            _state_filter(target_move),
            AccountAccount.account_type == acct_type,
            AccountMoveLine.reconciled.is_(False),
            AccountMoveLine.date <= as_of,
            AccountMoveLine.amount_residual != ZERO,
        )
        .order_by(ResPartner.name.asc(), AccountMoveLine.date_maturity.asc())
    )
    rows = (await session.execute(stmt)).all()

    partner_buckets: dict[int, dict[str, Any]] = {}
    totals = {
        "current": ZERO,
        "days_1_30": ZERO,
        "days_31_60": ZERO,
        "days_61_90": ZERO,
        "days_90_plus": ZERO,
        "total": ZERO,
    }

    for r in rows:
        # Receivable normal sign is positive; Payable normal sign is negative (present positive owed amount)
        residual = D(r.amount_residual)
        amt = quantize_currency(residual if report_type == "receivable" else -residual)
        if amt == ZERO:
            continue

        due = r.date_maturity or r.date
        overdue_days = (as_of - due).days
        if overdue_days <= 0:
            bucket = "current"
        elif overdue_days <= 30:
            bucket = "days_1_30"
        elif overdue_days <= 60:
            bucket = "days_31_60"
        elif overdue_days <= 90:
            bucket = "days_61_90"
        else:
            bucket = "days_90_plus"

        pid = r.partner_id or 0
        if pid not in partner_buckets:
            partner_buckets[pid] = {
                "partner_id": pid,
                "partner_name": r.partner_name or "Unknown Partner",
                "current": ZERO,
                "days_1_30": ZERO,
                "days_31_60": ZERO,
                "days_61_90": ZERO,
                "days_90_plus": ZERO,
                "total": ZERO,
                "lines": [],
            }
        pb = partner_buckets[pid]
        pb[bucket] += amt
        pb["total"] += amt
        pb["lines"].append(
            {
                "move_id": r.move_id,
                "move_name": r.move_name,
                "date": r.date.isoformat(),
                "due_date": due.isoformat(),
                "overdue_days": max(0, overdue_days),
                "bucket": bucket,
                "amount": str(amt),
            }
        )
        totals[bucket] += amt
        totals["total"] += amt

    serialized = [
        {
            "partner_id": pb["partner_id"],
            "partner_name": pb["partner_name"],
            "current": str(quantize_currency(pb["current"])),
            "days_1_30": str(quantize_currency(pb["days_1_30"])),
            "days_31_60": str(quantize_currency(pb["days_31_60"])),
            "days_61_90": str(quantize_currency(pb["days_61_90"])),
            "days_90_plus": str(quantize_currency(pb["days_90_plus"])),
            "total": str(quantize_currency(pb["total"])),
            "lines": pb["lines"],
        }
        for pb in partner_buckets.values()
    ]

    return {
        "report": f"aged_{report_type}",
        "title": "Aged Receivable" if report_type == "receivable" else "Aged Payable",
        "title_ar": "أعمار الذمم المدينة" if report_type == "receivable" else "أعمار الذمم الدائنة",
        "as_of_date": as_of.isoformat(),
        "target_move": target_move,
        "partners": serialized,
        "totals": {k: str(quantize_currency(v)) for k, v in totals.items()},
    }


# --- Tabular Flattening & CSV / XLSX / PDF Exporters ---

def _flatten_report_to_table(report_data: dict[str, Any]) -> tuple[str, list[str], list[list[str]]]:
    rtype = report_data["report"]
    title = report_data.get("title", rtype)

    if rtype == "trial_balance":
        headers = [
            "Code",
            "Account",
            "Opening Debit",
            "Opening Credit",
            "Period Debit",
            "Period Credit",
            "Closing Debit",
            "Closing Credit",
        ]
        rows = [
            [
                r["code"],
                r["name"],
                r["opening_debit"],
                r["opening_credit"],
                r["period_debit"],
                r["period_credit"],
                r["closing_debit"],
                r["closing_credit"],
            ]
            for r in report_data["rows"]
        ]
        t = report_data["totals"]
        rows.append(
            [
                "",
                "TOTAL",
                t["opening_debit"],
                t["opening_credit"],
                t["period_debit"],
                t["period_credit"],
                t["closing_debit"],
                t["closing_credit"],
            ]
        )
        return title, headers, rows

    if rtype == "profit_and_loss":
        headers = ["Section", "Code", "Account", "Amount"]
        rows = []
        for sec_label, sec_key in [
            ("Operating Income", "operating_income"),
            ("Other Income", "other_income"),
            ("Cost of Goods Sold", "cost_of_goods_sold"),
            ("Operating Expenses", "operating_expenses"),
        ]:
            for item in report_data["sections"][sec_key]:
                rows.append([sec_label, item["code"], item["name"], item["amount"]])
        s = report_data["summary"]
        rows.append(["SUMMARY", "", "Gross Profit", s["gross_profit"]])
        rows.append(["SUMMARY", "", "Net Profit", s["net_profit"]])
        return title, headers, rows

    if rtype == "balance_sheet":
        headers = ["Category", "Code", "Account", "Balance"]
        rows = []
        for item in report_data["assets"]["current_assets"]:
            rows.append(["Current Assets", item["code"], item["name"], item["balance"]])
        for item in report_data["assets"]["non_current_assets"]:
            rows.append(["Non-Current Assets", item["code"], item["name"], item["balance"]])
        rows.append(["TOTAL ASSETS", "", "", report_data["assets"]["total_assets"]])
        for item in report_data["liabilities"]["current_liabilities"]:
            rows.append(["Current Liabilities", item["code"], item["name"], item["balance"]])
        for item in report_data["liabilities"]["non_current_liabilities"]:
            rows.append(["Non-Current Liabilities", item["code"], item["name"], item["balance"]])
        for item in report_data["equity"]["accounts"]:
            rows.append(["Equity", item["code"], item["name"], item["balance"]])
        rows.append(
            [
                "Equity",
                "",
                "Current Year Unallocated Earnings",
                report_data["equity"]["current_year_unallocated_earnings"],
            ]
        )
        rows.append(
            [
                "TOTAL LIABILITIES & EQUITY",
                "",
                "",
                report_data["summary"]["total_liabilities_and_equity"],
            ]
        )
        return title, headers, rows

    if rtype == "general_ledger":
        headers = ["Account", "Date", "Move", "Partner", "Label", "Debit", "Credit", "Balance"]
        rows = []
        for acct in report_data["accounts"]:
            acct_label = f"{acct['code']} {acct['name']}"
            rows.append([acct_label, "", "Opening", "", "", "", "", acct["opening_balance"]])
            for e in acct["entries"]:
                rows.append(
                    [
                        acct_label,
                        e["date"],
                        e["move_name"],
                        e["partner_name"],
                        e["label"],
                        e["debit"],
                        e["credit"],
                        e["running_balance"],
                    ]
                )
        return title, headers, rows

    # Aged Receivable / Aged Payable
    headers = ["Partner", "Current", "1-30 Days", "31-60 Days", "61-90 Days", "90+ Days", "Total"]
    rows = [
        [
            p["partner_name"],
            p["current"],
            p["days_1_30"],
            p["days_31_60"],
            p["days_61_90"],
            p["days_90_plus"],
            p["total"],
        ]
        for p in report_data["partners"]
    ]
    t = report_data["totals"]
    rows.append(
        [
            "TOTAL",
            t["current"],
            t["days_1_30"],
            t["days_31_60"],
            t["days_61_90"],
            t["days_90_plus"],
            t["total"],
        ]
    )
    return title, headers, rows


def export_report_csv(report_data: dict[str, Any]) -> bytes:
    """Export any financial report as UTF-8 CSV."""
    _title, headers, rows = _flatten_report_to_table(report_data)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def export_report_xlsx(report_data: dict[str, Any]) -> bytes:
    """Export any financial report as a styled Excel (.xlsx) workbook."""
    title, headers, rows = _flatten_report_to_table(report_data)
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]

    ws.append([f"Nizamo ERP — {title}"])
    ws["A1"].font = Font(size=14, bold=True, color="1E293B")
    ws.append([])
    ws.append(headers)

    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    for cell in ws[3]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for row in rows:
        ws.append(row)

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(42, max(12, max_len + 3))

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def export_report_pdf(report_data: dict[str, Any]) -> bytes:
    """Export any financial report as a clean A4 landscape PDF document."""
    title, headers, rows = _flatten_report_to_table(report_data)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=28, bottomMargin=24)
    styles = getSampleStyleSheet()
    elements = [
        Paragraph(f"<b>Nizamo ERP — {title}</b>", styles["Title"]),
        Spacer(1, 10),
    ]

    table_data = [headers] + rows
    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)
    return buf.getvalue()
