"""Payment Registration, Partial/Full Reconciliation, and Realized FX Gain/Loss Engine."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.chatter import log_chatter
from backend.core.database import apply_for_update, atomic_lock
from backend.core.decimal_utils import D, ZERO, is_zero, quantize_currency
from backend.core.sequences import next_sequence_by_code
from backend.core.tenancy import ResCompany, ResCurrency, convert_to_company_currency
from backend.modules.accounting.models import (
    ACCOUNT_TYPE_ASSET_RECEIVABLE,
    ACCOUNT_TYPE_EXPENSE,
    ACCOUNT_TYPE_INCOME_OTHER,
    ACCOUNT_TYPE_LIABILITY_PAYABLE,
    AccountAccount,
    AccountJournal,
    AccountMove,
    AccountMoveLine,
    AccountPartialReconcile,
    AccountPayment,
)
from backend.modules.accounting.posting_engine import (
    AccountingError,
    JournalLineInput,
    create_journal_entry,
    get_move_with_lines,
    post_move,
)


async def _find_fx_accounts_and_journal(
    session: AsyncSession, company_id: int
) -> tuple[AccountAccount, AccountAccount, AccountJournal]:
    """Locate FX Gain account (490000), FX Loss account (590000), and General/FX journal."""
    gain_acct = (
        await session.execute(
            select(AccountAccount).where(
                AccountAccount.company_id == company_id,
                AccountAccount.code == "490000",
            )
        )
    ).scalar_one_or_none()
    if gain_acct is None:
        gain_acct = (
            await session.execute(
                select(AccountAccount)
                .where(
                    AccountAccount.company_id == company_id,
                    AccountAccount.account_type == ACCOUNT_TYPE_INCOME_OTHER,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    if gain_acct is None:
        gain_acct = AccountAccount(
            company_id=company_id,
            code="490000",
            name="Foreign Exchange Gain",
            name_ar="أرباح فروق العملة",
            account_type=ACCOUNT_TYPE_INCOME_OTHER,
            reconcilable=False,
        )
        session.add(gain_acct)
        await session.flush()

    loss_acct = (
        await session.execute(
            select(AccountAccount).where(
                AccountAccount.company_id == company_id,
                AccountAccount.code == "590000",
            )
        )
    ).scalar_one_or_none()
    if loss_acct is None:
        loss_acct = (
            await session.execute(
                select(AccountAccount)
                .where(
                    AccountAccount.company_id == company_id,
                    AccountAccount.account_type == ACCOUNT_TYPE_EXPENSE,
                    AccountAccount.name.ilike("%exchange%"),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    if loss_acct is None:
        loss_acct = AccountAccount(
            company_id=company_id,
            code="590000",
            name="Foreign Exchange Loss",
            name_ar="خسائر فروق العملة",
            account_type=ACCOUNT_TYPE_EXPENSE,
            reconcilable=False,
        )
        session.add(loss_acct)
        await session.flush()

    fx_journal = (
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
    if fx_journal is None:
        raise AccountingError(f"No GENERAL journal found for company_id={company_id} to post FX Gain/Loss")

    return gain_acct, loss_acct, fx_journal


async def recompute_move_payment_state(session: AsyncSession, move_id: int) -> AccountMove:
    """Dynamically compute `amount_residual` and `payment_state` (NOT_PAID, PARTIAL, IN_PAYMENT, PAID)."""
    move = await get_move_with_lines(session, move_id)
    if move.move_type not in ("out_invoice", "in_invoice", "out_refund", "in_refund"):
        return move
    if move.state != "POSTED":
        return move

    acct_ids = {l.account_id for l in move.lines}
    accts = (
        await session.execute(select(AccountAccount).where(AccountAccount.id.in_(acct_ids)))
    ).scalars().all()
    acct_map = {a.id: a for a in accts}

    company = await session.get(ResCompany, move.company_id)
    doc_curr = await session.get(ResCurrency, move.currency_id)
    doc_prec = doc_curr.decimal_places if doc_curr else 2

    reconcilable_lines = [
        l
        for l in move.lines
        if acct_map.get(l.account_id)
        and acct_map[l.account_id].account_type
        in (ACCOUNT_TYPE_ASSET_RECEIVABLE, ACCOUNT_TYPE_LIABILITY_PAYABLE)
    ]
    if not reconcilable_lines:
        return move

    # Compute residual in invoice's transaction currency
    if company and move.currency_id == company.currency_id:
        residual_abs = sum((abs(D(l.amount_residual)) for l in reconcilable_lines), ZERO)
    else:
        residual_abs = sum((abs(D(l.amount_residual_currency)) for l in reconcilable_lines), ZERO)

    residual_abs = quantize_currency(residual_abs, doc_prec)
    move.amount_residual = residual_abs

    old_pstate = move.payment_state
    all_reconciled = all(l.reconciled for l in reconcilable_lines)
    if all_reconciled or is_zero(residual_abs, doc_prec):
        move.amount_residual = ZERO
        move.payment_state = "PAID"
    elif residual_abs < move.amount_total:
        move.payment_state = "PARTIAL"
    else:
        move.payment_state = "NOT_PAID"

    await session.flush()

    if old_pstate != move.payment_state:
        await log_chatter(
            session,
            company_id=move.company_id,
            res_model="account_move",
            res_id=move.id,
            body=f"Payment status updated: {old_pstate} -> {move.payment_state} (Residual: {move.amount_residual})",
            message_type="state_change",
            diffs=[("payment_state", "Payment Status", old_pstate, move.payment_state)],
        )
    return move


async def reconcile_move_lines(
    session: AsyncSession,
    *,
    debit_line_id: int,
    credit_line_id: int,
    amount_currency: Decimal | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountPartialReconcile:
    """Reconcile a debit line against a credit line (fully or partially) with SELECT ... FOR UPDATE.

    Automatically posts Realized Foreign Exchange Gain/Loss journal entries when the historical
    exchange rate on the payment date differs from the invoice date.
    """
    async with atomic_lock(session):
        # Lock lines in deterministic ID order to prevent deadlocks
        ids_sorted = sorted([debit_line_id, credit_line_id])
        stmt = apply_for_update(
            select(AccountMoveLine)
            .where(AccountMoveLine.id.in_(ids_sorted))
            .order_by(AccountMoveLine.id.asc()),
            session,
        )
        rows = (await session.execute(stmt)).scalars().all()
        line_map = {r.id: r for r in rows}

        dline = line_map.get(debit_line_id)
        cline = line_map.get(credit_line_id)
        if dline is None or cline is None:
            raise AccountingError("Both debit and credit move lines must exist for reconciliation")
        if dline.company_id != cline.company_id:
            raise AccountingError("Cannot reconcile lines belonging to different companies")
        if dline.account_id != cline.account_id:
            raise AccountingError("Reconciled lines must belong to the same account")

        acct = await session.get(AccountAccount, dline.account_id)
        if acct is None or not acct.reconcilable:
            raise AccountingError(f"Account '{acct.code if acct else dline.account_id}' is not reconcilable")

        if dline.amount_residual <= ZERO:
            raise AccountingError(f"Debit line id={dline.id} has no remaining positive residual")
        if cline.amount_residual >= ZERO:
            raise AccountingError(f"Credit line id={cline.id} has no remaining negative residual")

        company = await session.get(ResCompany, dline.company_id)
        assert company is not None
        base_curr = await session.get(ResCurrency, company.currency_id)
        base_prec = base_curr.decimal_places if base_curr else 2

        same_foreign_currency = (
            dline.currency_id == cline.currency_id
            and dline.currency_id != company.currency_id
        )

        fx_move_id: int | None = None

        if same_foreign_currency:
            fc = await session.get(ResCurrency, dline.currency_id)
            fc_prec = fc.decimal_places if fc else 2

            d_rem_fc = abs(D(dline.amount_residual_currency))
            c_rem_fc = abs(D(cline.amount_residual_currency))
            max_fc = min(d_rem_fc, c_rem_fc)
            matched_fc = max_fc if amount_currency is None else min(D(amount_currency), max_fc)
            matched_fc = quantize_currency(matched_fc, fc_prec)
            if matched_fc <= ZERO:
                raise AccountingError("Reconciliation amount must be greater than zero")

            # Historical base value per unit of foreign currency on each line
            d_orig_fc = abs(D(dline.amount_currency))
            c_orig_fc = abs(D(cline.amount_currency))
            d_base_portion = (
                quantize_currency(D(dline.amount_residual) * (matched_fc / d_rem_fc), base_prec)
                if d_rem_fc > ZERO
                else ZERO
            )
            c_base_portion = (
                quantize_currency(abs(D(cline.amount_residual)) * (matched_fc / c_rem_fc), base_prec)
                if c_rem_fc > ZERO
                else ZERO
            )

            matched_base = min(d_base_portion, c_base_portion)
            fx_diff = quantize_currency(c_base_portion - d_base_portion, base_prec)

            # Update residuals for the primary partial reconciliation
            dline.amount_residual = quantize_currency(D(dline.amount_residual) - matched_base, base_prec)
            dline.amount_residual_currency = quantize_currency(
                D(dline.amount_residual_currency) - matched_fc, fc_prec
            )
            cline.amount_residual = quantize_currency(D(cline.amount_residual) + matched_base, base_prec)
            cline.amount_residual_currency = quantize_currency(
                D(cline.amount_residual_currency) + matched_fc, fc_prec
            )

            partial = AccountPartialReconcile(
                company_id=dline.company_id,
                debit_move_id=dline.id,
                credit_move_id=cline.id,
                amount=matched_base,
                debit_amount_currency=matched_fc,
                credit_amount_currency=matched_fc,
                debit_currency_id=dline.currency_id,
                credit_currency_id=cline.currency_id,
            )
            session.add(partial)
            await session.flush()

            # Realized Foreign Exchange Gain / Loss Posting
            if fx_diff != ZERO:
                gain_acct, loss_acct, fx_journal = await _find_fx_accounts_and_journal(
                    session, dline.company_id
                )
                fx_date = max(dline.date, cline.date)
                abs_diff = abs(fx_diff)

                if fx_diff > ZERO:
                    # Credit line had higher base value than Debit line -> Realized FX Gain
                    # Debit Control Account (absorbs excess base credit on cline), Credit FX Gain
                    fx_move = await create_journal_entry(
                        session,
                        company_id=dline.company_id,
                        journal_id=fx_journal.id,
                        move_date=fx_date,
                        ref=f"Realized FX Gain ({fc.code if fc else ''})",
                        partner_id=dline.partner_id or cline.partner_id,
                        currency_id=company.currency_id,
                        lines=[
                            JournalLineInput(
                                account_id=dline.account_id,
                                partner_id=dline.partner_id or cline.partner_id,
                                name=f"FX Gain adjustment on reconciliation",
                                debit=abs_diff,
                                credit=ZERO,
                                amount_currency=ZERO,
                                currency_id=dline.currency_id,
                            ),
                            JournalLineInput(
                                account_id=gain_acct.id,
                                partner_id=dline.partner_id or cline.partner_id,
                                name=f"Realized Foreign Exchange Gain",
                                debit=ZERO,
                                credit=abs_diff,
                                amount_currency=-abs_diff,
                                currency_id=company.currency_id,
                            ),
                        ],
                        author_name=author_name,
                        author_id=author_id,
                    )
                    fx_move = await post_move(
                        session, fx_move.id, author_name=author_name, author_id=author_id
                    )
                    fx_control_line = next(l for l in fx_move.lines if l.account_id == dline.account_id)
                    # Reconcile FX Debit line against excess base credit residual on cline
                    fx_control_line.amount_residual = ZERO
                    fx_control_line.reconciled = True
                    cline.amount_residual = quantize_currency(D(cline.amount_residual) + abs_diff, base_prec)
                    session.add(
                        AccountPartialReconcile(
                            company_id=dline.company_id,
                            debit_move_id=fx_control_line.id,
                            credit_move_id=cline.id,
                            amount=abs_diff,
                            debit_amount_currency=ZERO,
                            credit_amount_currency=ZERO,
                            debit_currency_id=dline.currency_id,
                            credit_currency_id=cline.currency_id,
                            exchange_move_id=fx_move.id,
                        )
                    )
                else:
                    # Debit line had higher base value than Credit line -> Realized FX Loss
                    # Debit FX Loss, Credit Control Account (absorbs excess base debit on dline)
                    fx_move = await create_journal_entry(
                        session,
                        company_id=dline.company_id,
                        journal_id=fx_journal.id,
                        move_date=fx_date,
                        ref=f"Realized FX Loss ({fc.code if fc else ''})",
                        partner_id=dline.partner_id or cline.partner_id,
                        currency_id=company.currency_id,
                        lines=[
                            JournalLineInput(
                                account_id=loss_acct.id,
                                partner_id=dline.partner_id or cline.partner_id,
                                name=f"Realized Foreign Exchange Loss",
                                debit=abs_diff,
                                credit=ZERO,
                                amount_currency=abs_diff,
                                currency_id=company.currency_id,
                            ),
                            JournalLineInput(
                                account_id=dline.account_id,
                                partner_id=dline.partner_id or cline.partner_id,
                                name=f"FX Loss adjustment on reconciliation",
                                debit=ZERO,
                                credit=abs_diff,
                                amount_currency=ZERO,
                                currency_id=dline.currency_id,
                            ),
                        ],
                        author_name=author_name,
                        author_id=author_id,
                    )
                    fx_move = await post_move(
                        session, fx_move.id, author_name=author_name, author_id=author_id
                    )
                    fx_control_line = next(l for l in fx_move.lines if l.account_id == dline.account_id)
                    # Reconcile FX Credit line against excess base debit residual on dline
                    fx_control_line.amount_residual = ZERO
                    fx_control_line.reconciled = True
                    dline.amount_residual = quantize_currency(D(dline.amount_residual) - abs_diff, base_prec)
                    session.add(
                        AccountPartialReconcile(
                            company_id=dline.company_id,
                            debit_move_id=dline.id,
                            credit_move_id=fx_control_line.id,
                            amount=abs_diff,
                            debit_amount_currency=ZERO,
                            credit_amount_currency=ZERO,
                            debit_currency_id=dline.currency_id,
                            credit_currency_id=cline.currency_id,
                            exchange_move_id=fx_move.id,
                        )
                    )

                fx_move_id = fx_move.id
                partial.exchange_move_id = fx_move_id

            dline.reconciled = is_zero(dline.amount_residual, base_prec) and is_zero(
                dline.amount_residual_currency, fc_prec
            )
            cline.reconciled = is_zero(cline.amount_residual, base_prec) and is_zero(
                cline.amount_residual_currency, fc_prec
            )
            if dline.reconciled:
                dline.amount_residual = ZERO
                dline.amount_residual_currency = ZERO
            if cline.reconciled:
                cline.amount_residual = ZERO
                cline.amount_residual_currency = ZERO
        else:
            # Base currency reconciliation
            max_base = min(D(dline.amount_residual), abs(D(cline.amount_residual)))
            matched_base = max_base if amount_currency is None else min(D(amount_currency), max_base)
            matched_base = quantize_currency(matched_base, base_prec)
            if matched_base <= ZERO:
                raise AccountingError("Reconciliation amount must be greater than zero")

            dline.amount_residual = quantize_currency(D(dline.amount_residual) - matched_base, base_prec)
            cline.amount_residual = quantize_currency(D(cline.amount_residual) + matched_base, base_prec)
            if dline.currency_id == company.currency_id:
                dline.amount_residual_currency = dline.amount_residual
            if cline.currency_id == company.currency_id:
                cline.amount_residual_currency = cline.amount_residual

            dline.reconciled = is_zero(dline.amount_residual, base_prec)
            cline.reconciled = is_zero(cline.amount_residual, base_prec)
            if dline.reconciled:
                dline.amount_residual = ZERO
                dline.amount_residual_currency = ZERO
            if cline.reconciled:
                cline.amount_residual = ZERO
                cline.amount_residual_currency = ZERO

            partial = AccountPartialReconcile(
                company_id=dline.company_id,
                debit_move_id=dline.id,
                credit_move_id=cline.id,
                amount=matched_base,
                debit_amount_currency=matched_base,
                credit_amount_currency=matched_base,
                debit_currency_id=dline.currency_id,
                credit_currency_id=cline.currency_id,
            )
            session.add(partial)

        await session.flush()

        # Recompute parent move payment states
        await recompute_move_payment_state(session, dline.move_id)
        if cline.move_id != dline.move_id:
            await recompute_move_payment_state(session, cline.move_id)

        return partial


async def register_payment(
    session: AsyncSession,
    *,
    company_id: int,
    payment_type: str,  # INBOUND | OUTBOUND
    partner_type: str,  # CUSTOMER | SUPPLIER
    partner_id: int,
    journal_id: int,
    amount: Decimal,
    payment_date: date,
    currency_id: int | None = None,
    ref: str | None = None,
    invoice_move_id: int | None = None,
    destination_account_id: int | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> tuple[AccountPayment, AccountMove, AccountPartialReconcile | None]:
    """Create and post a payment, generating its Bank/Cash AccountMove and reconciling open invoice lines."""
    company = await session.get(ResCompany, company_id)
    if company is None:
        raise AccountingError(f"Company {company_id} not found")
    journal = await session.get(AccountJournal, journal_id)
    if journal is None or journal.default_account_id is None:
        raise AccountingError("Payment journal must exist and define a default Bank/Cash account")

    amt = D(amount)
    if amt <= ZERO:
        raise AccountingError("Payment amount must be strictly positive")

    pay_curr_id = currency_id or company.currency_id
    pay_curr = await session.get(ResCurrency, pay_curr_id)
    pay_prec = pay_curr.decimal_places if pay_curr else 2
    amt = quantize_currency(amt, pay_prec)

    target_move: AccountMove | None = None
    target_line: AccountMoveLine | None = None

    if invoice_move_id is not None:
        target_move = await get_move_with_lines(session, invoice_move_id)
        if target_move.state != "POSTED":
            raise AccountingError("Cannot register payment against a non-POSTED invoice")
        # Locate open AR/AP line on the invoice
        acct_ids = {l.account_id for l in target_move.lines}
        accts = (
            await session.execute(select(AccountAccount).where(AccountAccount.id.in_(acct_ids)))
        ).scalars().all()
        acct_map = {a.id: a for a in accts}
        for l in target_move.lines:
            a = acct_map.get(l.account_id)
            if a and a.reconcilable and not l.reconciled and l.amount_residual != ZERO:
                target_line = l
                destination_account_id = l.account_id
                break
        if target_line is None:
            raise AccountingError(f"Invoice '{target_move.name}' is already fully reconciled")

    if destination_account_id is None:
        target_type = (
            ACCOUNT_TYPE_ASSET_RECEIVABLE if partner_type == "CUSTOMER" else ACCOUNT_TYPE_LIABILITY_PAYABLE
        )
        dest_acct = (
            await session.execute(
                select(AccountAccount)
                .where(
                    AccountAccount.company_id == company_id,
                    AccountAccount.account_type == target_type,
                )
                .order_by(AccountAccount.code.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if dest_acct is None:
            raise AccountingError(f"No {target_type} account found for company_id={company_id}")
        destination_account_id = dest_acct.id

    base_amt, _ = await convert_to_company_currency(
        session,
        amount=amt,
        currency_id=pay_curr_id,
        company_id=company_id,
        as_of_date=payment_date,
    )

    seq_code = "payment_in" if payment_type == "INBOUND" else "payment_out"
    seq_prefix = "PAY/IN/%(year)s/" if payment_type == "INBOUND" else "PAY/OUT/%(year)s/"
    pay_name = await next_sequence_by_code(
        session, company_id=company_id, code=seq_code, ref_date=payment_date, default_prefix=seq_prefix
    )

    # INBOUND (Customer pays us): Debit Bank/Cash, Credit AR
    # OUTBOUND (We pay Vendor): Debit AP, Credit Bank/Cash
    if payment_type == "INBOUND":
        lines = [
            JournalLineInput(
                account_id=journal.default_account_id,
                partner_id=partner_id,
                name=f"Payment {pay_name} ({ref or ''})".strip(),
                debit=base_amt,
                credit=ZERO,
                amount_currency=amt,
                currency_id=pay_curr_id,
            ),
            JournalLineInput(
                account_id=destination_account_id,
                partner_id=partner_id,
                name=f"Customer Payment {pay_name}",
                debit=ZERO,
                credit=base_amt,
                amount_currency=-amt,
                currency_id=pay_curr_id,
            ),
        ]
    else:
        lines = [
            JournalLineInput(
                account_id=destination_account_id,
                partner_id=partner_id,
                name=f"Vendor Payment {pay_name}",
                debit=base_amt,
                credit=ZERO,
                amount_currency=amt,
                currency_id=pay_curr_id,
            ),
            JournalLineInput(
                account_id=journal.default_account_id,
                partner_id=partner_id,
                name=f"Payment {pay_name} ({ref or ''})".strip(),
                debit=ZERO,
                credit=base_amt,
                amount_currency=-amt,
                currency_id=pay_curr_id,
            ),
        ]

    pay_move = await create_journal_entry(
        session,
        company_id=company_id,
        journal_id=journal_id,
        move_date=payment_date,
        lines=lines,
        ref=ref or pay_name,
        partner_id=partner_id,
        currency_id=pay_curr_id,
        invoice_origin=target_move.name if target_move else None,
        author_name=author_name,
        author_id=author_id,
    )
    pay_move = await post_move(session, pay_move.id, author_name=author_name, author_id=author_id)

    payment = AccountPayment(
        company_id=company_id,
        name=pay_name,
        payment_type=payment_type,
        partner_type=partner_type,
        partner_id=partner_id,
        journal_id=journal_id,
        destination_account_id=destination_account_id,
        amount=amt,
        currency_id=pay_curr_id,
        date=payment_date,
        ref=ref,
        state="POSTED",
        move_id=pay_move.id,
    )
    session.add(payment)
    await session.flush()

    partial: AccountPartialReconcile | None = None
    if target_line is not None:
        pay_counterpart = next(
            l for l in pay_move.lines if l.account_id == destination_account_id
        )
        if target_line.amount_residual > ZERO and pay_counterpart.amount_residual < ZERO:
            partial = await reconcile_move_lines(
                session,
                debit_line_id=target_line.id,
                credit_line_id=pay_counterpart.id,
                author_name=author_name,
                author_id=author_id,
            )
        elif pay_counterpart.amount_residual > ZERO and target_line.amount_residual < ZERO:
            partial = await reconcile_move_lines(
                session,
                debit_line_id=pay_counterpart.id,
                credit_line_id=target_line.id,
                author_name=author_name,
                author_id=author_id,
            )

    await log_chatter(
        session,
        company_id=company_id,
        res_model="account_payment",
        res_id=payment.id,
        body=f"Payment {payment.name} posted for {amt}",
        message_type="state_change",
        author_name=author_name,
        author_id=author_id,
    )
    return payment, pay_move, partial
