"""Procure-to-Pay Workflows: RFQ -> Confirmed PO -> Incoming Shipment -> Vendor Bill with 3-Way Matching."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.chatter import log_chatter
from backend.core.decimal_utils import D, ONE, ZERO, quantize_currency, quantize_db
from backend.core.sequences import next_sequence_by_code
from backend.core.tenancy import ResCompany, ResCurrency
from backend.modules.accounting.models import (
    ACCOUNT_TYPE_EXPENSE,
    AccountAccount,
    AccountJournal,
    AccountMove,
    AccountTax,
)
from backend.modules.accounting.posting_engine import InvoiceLineInput, create_invoice_move
from backend.modules.accounting.tax_engine import compute_line_taxes
from backend.modules.inventory.models import ProductCategory, ProductTemplate, StockLocation, StockPicking
from backend.modules.inventory.valuation_engine import create_picking
from backend.modules.purchase.models import PurchaseOrder, PurchaseOrderLine


class PurchaseError(Exception):
    """Domain error for Purchase / 3-Way Matching violations."""


class ThreeWayMatchError(PurchaseError):
    """Raised when Vendor Bill quantities violate 3-Way Matching (Ordered vs Received vs Billed)."""


async def get_purchase_order(session: AsyncSession, order_id: int) -> PurchaseOrder:
    stmt = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.lines))
        .where(PurchaseOrder.id == order_id)
    )
    order = (await session.execute(stmt)).scalar_one_or_none()
    if order is None:
        raise PurchaseError(f"PurchaseOrder id={order_id} not found")
    return order


def compute_three_way_match(order: PurchaseOrder) -> dict[str, Any]:
    """Evaluate 3-Way Matching across all PO lines (`Ordered Qty` vs `Received Qty` vs `Billed Qty`)."""
    line_statuses = []
    all_matched = True
    any_over_billed = False
    any_waiting_receipt = False

    for line in order.lines:
        ordered = D(line.quantity)
        received = D(line.qty_received)
        billed = D(line.qty_billed)

        if billed > received:
            st = "OVER_BILLED"
            any_over_billed = True
            all_matched = False
        elif received < ordered:
            st = "WAIT_RECEIPT"
            any_waiting_receipt = True
            all_matched = False
        elif billed < received:
            st = "READY_TO_BILL"
            all_matched = False
        else:
            st = "MATCHED"

        line_statuses.append(
            {
                "line_id": line.id,
                "name": line.name,
                "ordered_qty": str(quantize_currency(ordered)),
                "received_qty": str(quantize_currency(received)),
                "billed_qty": str(quantize_currency(billed)),
                "billable_qty": str(quantize_currency(max(ZERO, received - billed))),
                "status": st,
            }
        )

    if any_over_billed:
        overall = "EXCEPTION_OVER_BILLED"
    elif all_matched and order.lines:
        overall = "MATCHED"
    elif any_waiting_receipt:
        overall = "WAITING_RECEIPT"
    else:
        overall = "READY_TO_BILL"

    return {"order_id": order.id, "order_name": order.name, "status": overall, "lines": line_statuses}


async def create_purchase_order(
    session: AsyncSession,
    *,
    company_id: int,
    partner_id: int,
    date_order: date,
    lines_data: list[dict],
    currency_id: int | None = None,
    note: str | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> PurchaseOrder:
    if not lines_data:
        raise PurchaseError("Purchase order must have at least one line")
    company = await session.get(ResCompany, company_id)
    if company is None:
        raise PurchaseError(f"Company id={company_id} not found")

    curr_id = currency_id or company.currency_id
    curr = await session.get(ResCurrency, curr_id)
    prec = curr.decimal_places if curr else 2

    all_tax_ids = {tid for ld in lines_data for tid in ld.get("tax_ids", [])}
    taxes_map: dict[int, AccountTax] = {}
    if all_tax_ids:
        t_rows = (
            await session.execute(select(AccountTax).where(AccountTax.id.in_(all_tax_ids)))
        ).scalars().all()
        taxes_map = {t.id: t for t in t_rows}

    order = PurchaseOrder(
        company_id=company_id,
        name="DRAFT",
        partner_id=partner_id,
        currency_id=curr_id,
        date_order=date_order,
        state="DRAFT",
        note=note,
    )
    session.add(order)
    await session.flush()

    tot_untaxed = ZERO
    tot_tax = ZERO

    for ld in lines_data:
        qty = quantize_db(D(ld.get("quantity", ONE)))
        price = quantize_currency(D(ld.get("price_unit", ZERO)), prec)
        tids = [int(t) for t in ld.get("tax_ids", [])]
        tax_res = compute_line_taxes(
            price_unit=price,
            quantity=qty,
            taxes=[taxes_map[t] for t in tids if t in taxes_map],
            currency_precision=prec,
        )
        tot_untaxed += tax_res.total_excluded
        tot_tax += tax_res.total_tax

        session.add(
            PurchaseOrderLine(
                company_id=company_id,
                order_id=order.id,
                product_id=ld.get("product_id"),
                name=str(ld["name"]),
                quantity=qty,
                price_unit=price,
                tax_ids_json=json.dumps(tids) if tids else None,
                analytic_account_id=ld.get("analytic_account_id"),
                price_subtotal=tax_res.total_excluded,
                price_tax=tax_res.total_tax,
                price_total=tax_res.total_included,
            )
        )

    order.amount_untaxed = quantize_currency(tot_untaxed, prec)
    order.amount_tax = quantize_currency(tot_tax, prec)
    order.amount_total = quantize_currency(tot_untaxed + tot_tax, prec)
    await session.flush()

    await log_chatter(
        session,
        company_id=company_id,
        res_model="purchase_order",
        res_id=order.id,
        body=f"RFQ created in DRAFT state for total {order.amount_total}",
        author_name=author_name,
        author_id=author_id,
    )
    return await get_purchase_order(session, order.id)


async def confirm_purchase_order(
    session: AsyncSession,
    order_id: int,
    *,
    author_name: str = "System",
    author_id: int | None = None,
) -> tuple[PurchaseOrder, StockPicking | None]:
    """Confirm RFQ -> Purchase Order, assign PO/YYYY/NNNNN, and auto-create Incoming Shipment."""
    order = await get_purchase_order(session, order_id)
    if order.state != "DRAFT":
        raise PurchaseError(f"Only DRAFT RFQs can be confirmed (current state: {order.state})")

    old_name = order.name
    old_state = order.state
    order.name = await next_sequence_by_code(
        session,
        company_id=order.company_id,
        code="purchase_order",
        ref_date=order.date_order,
        default_prefix="PO/%(year)s/",
    )
    order.state = "CONFIRMED"
    await session.flush()

    storable_moves = []
    for line in order.lines:
        if line.product_id:
            prod = await session.get(ProductTemplate, line.product_id)
            if prod and prod.product_type == "STORABLE":
                storable_moves.append(
                    {
                        "product_id": prod.id,
                        "description": line.name,
                        "quantity": line.quantity,
                        "price_unit": line.price_unit,
                    }
                )
            else:
                # Non-storable services are considered received upon confirmation
                line.qty_received = line.quantity

    picking: StockPicking | None = None
    if storable_moves:
        vendor_loc = (
            await session.execute(
                select(StockLocation)
                .where(
                    StockLocation.company_id == order.company_id,
                    StockLocation.usage == "SUPPLIER",
                )
                .order_by(StockLocation.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        wh_loc = (
            await session.execute(
                select(StockLocation)
                .where(
                    StockLocation.company_id == order.company_id,
                    StockLocation.usage == "INTERNAL",
                )
                .order_by(StockLocation.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if vendor_loc and wh_loc:
            picking = await create_picking(
                session,
                company_id=order.company_id,
                picking_type="INCOMING",
                location_id=vendor_loc.id,
                location_dest_id=wh_loc.id,
                scheduled_date=order.date_order,
                partner_id=order.partner_id,
                origin=order.name,
                moves_data=storable_moves,
                author_name=author_name,
                author_id=author_id,
            )

    await log_chatter(
        session,
        company_id=order.company_id,
        res_model="purchase_order",
        res_id=order.id,
        body=f"Purchase Order {order.name} confirmed"
        + (f"; auto-created Incoming Receipt {picking.name}" if picking else ""),
        message_type="state_change",
        author_name=author_name,
        author_id=author_id,
        diffs=[
            ("state", "Status", old_state, "CONFIRMED"),
            ("name", "PO Number", old_name, order.name),
        ],
    )
    return await get_purchase_order(session, order.id), picking


async def create_bill_from_purchase_order(
    session: AsyncSession,
    order_id: int,
    *,
    bill_date: date | None = None,
    enforce_three_way_match: bool = True,
    requested_quantities: dict[int, Decimal] | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountMove:
    """Create a Vendor Bill (`in_invoice`) with strict 3-Way Matching (`Ordered` vs `Received` vs `Billed`)."""
    order = await get_purchase_order(session, order_id)
    if order.state not in ("CONFIRMED", "DONE"):
        raise PurchaseError("Purchase Order must be CONFIRMED before creating a Vendor Bill")

    purch_journal = (
        await session.execute(
            select(AccountJournal)
            .where(
                AccountJournal.company_id == order.company_id,
                AccountJournal.journal_type == "PURCHASE",
            )
            .order_by(AccountJournal.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if purch_journal is None:
        raise PurchaseError("No PURCHASE journal configured for company")

    default_expense = (
        await session.execute(
            select(AccountAccount)
            .where(
                AccountAccount.company_id == order.company_id,
                AccountAccount.account_type == ACCOUNT_TYPE_EXPENSE,
            )
            .order_by(AccountAccount.code.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if default_expense is None:
        raise PurchaseError("No Expense account configured for company")

    bill_lines: list[InvoiceLineInput] = []
    for line in order.lines:
        ordered = D(line.quantity)
        received = D(line.qty_received)
        already_billed = D(line.qty_billed)

        if requested_quantities and line.id in requested_quantities:
            qty_to_bill = quantize_db(D(requested_quantities[line.id]))
        else:
            qty_to_bill = quantize_db(received - already_billed)

        if enforce_three_way_match:
            if already_billed + qty_to_bill > received:
                raise ThreeWayMatchError(
                    f"3-Way Matching Violation on '{line.name}': "
                    f"Ordered={ordered}, Received={received}, Already Billed={already_billed}, "
                    f"Attempted Bill={qty_to_bill}. Receive goods before billing."
                )
            if already_billed + qty_to_bill > ordered:
                raise ThreeWayMatchError(
                    f"3-Way Matching Violation on '{line.name}': Cannot bill more than ordered ({ordered})."
                )

        if qty_to_bill <= ZERO:
            continue

        # For storable products with perpetual valuation, Vendor Bill debits Stock Interim Received
        # (clearing the credit posted when WH/IN receipt was validated!)
        acct_id = default_expense.id
        if line.product_id:
            prod = await session.get(ProductTemplate, line.product_id)
            if prod:
                cat = await session.get(ProductCategory, prod.category_id)
                if cat:
                    if prod.product_type == "STORABLE" and cat.valuation == "REAL_TIME" and cat.stock_input_account_id:
                        acct_id = cat.stock_input_account_id
                    elif cat.expense_account_id:
                        acct_id = cat.expense_account_id

        tax_ids = json.loads(line.tax_ids_json) if line.tax_ids_json else []
        bill_lines.append(
            InvoiceLineInput(
                account_id=acct_id,
                name=line.name,
                quantity=qty_to_bill,
                price_unit=line.price_unit,
                tax_ids=tax_ids,
                analytic_account_id=line.analytic_account_id,
                product_id=line.product_id,
            )
        )
        line.qty_billed = quantize_db(already_billed + qty_to_bill)

    if not bill_lines:
        raise ThreeWayMatchError(
            f"3-Way Matching: No received unbilled quantities available on {order.name}. "
            "Validate the incoming warehouse receipt first."
        )

    b_dt = bill_date or order.date_order
    move = await create_invoice_move(
        session,
        company_id=order.company_id,
        move_type="in_invoice",
        journal_id=purch_journal.id,
        partner_id=order.partner_id,
        invoice_date=b_dt,
        invoice_date_due=b_dt + timedelta(days=30),
        currency_id=order.currency_id,
        ref=order.name,
        invoice_origin=order.name,
        invoice_lines=bill_lines,
        author_name=author_name,
        author_id=author_id,
    )

    if all(D(l.qty_billed) >= D(l.quantity) for l in order.lines):
        order.state = "DONE"
    await session.flush()

    await log_chatter(
        session,
        company_id=order.company_id,
        res_model="purchase_order",
        res_id=order.id,
        body=f"Vendor Bill draft (id={move.id}) generated after 3-Way Match verification",
        author_name=author_name,
        author_id=author_id,
    )
    return move
