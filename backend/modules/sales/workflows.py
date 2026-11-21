"""Order-to-Cash Workflows: Quotation -> Confirmed SO -> Auto Delivery -> Customer Invoice."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.chatter import log_chatter
from backend.core.decimal_utils import D, ONE, ZERO, quantize_currency, quantize_db
from backend.core.sequences import next_sequence_by_code
from backend.core.tenancy import ResCompany, ResCurrency
from backend.modules.accounting.models import (
    ACCOUNT_TYPE_INCOME,
    AccountAccount,
    AccountJournal,
    AccountMove,
    AccountTax,
)
from backend.modules.accounting.posting_engine import InvoiceLineInput, create_invoice_move
from backend.modules.accounting.tax_engine import compute_line_taxes
from backend.modules.inventory.models import ProductCategory, ProductTemplate, StockLocation, StockPicking
from backend.modules.inventory.valuation_engine import create_picking
from backend.modules.sales.models import SaleOrder, SaleOrderLine


class SalesError(Exception):
    """Domain error for Sales Order-to-Cash workflow violations."""


async def get_sale_order(session: AsyncSession, order_id: int) -> SaleOrder:
    stmt = select(SaleOrder).options(selectinload(SaleOrder.lines)).where(SaleOrder.id == order_id)
    order = (await session.execute(stmt)).scalar_one_or_none()
    if order is None:
        raise SalesError(f"SaleOrder id={order_id} not found")
    return order


async def create_sale_order(
    session: AsyncSession,
    *,
    company_id: int,
    partner_id: int,
    date_order: date,
    lines_data: list[dict],
    currency_id: int | None = None,
    invoice_policy: str = "ORDERED",
    note: str | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> SaleOrder:
    if not lines_data:
        raise SalesError("Sales order must have at least one line")
    company = await session.get(ResCompany, company_id)
    if company is None:
        raise SalesError(f"Company id={company_id} not found")

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

    order = SaleOrder(
        company_id=company_id,
        name="DRAFT",
        partner_id=partner_id,
        currency_id=curr_id,
        date_order=date_order,
        state="DRAFT",
        invoice_policy=invoice_policy,
        note=note,
    )
    session.add(order)
    await session.flush()

    tot_untaxed = ZERO
    tot_tax = ZERO

    for ld in lines_data:
        qty = quantize_db(D(ld.get("quantity", ONE)))
        price = quantize_currency(D(ld.get("price_unit", ZERO)), prec)
        disc = D(ld.get("discount", ZERO))
        tids = [int(t) for t in ld.get("tax_ids", [])]
        tax_res = compute_line_taxes(
            price_unit=price,
            quantity=qty,
            discount=disc,
            taxes=[taxes_map[t] for t in tids if t in taxes_map],
            currency_precision=prec,
        )
        tot_untaxed += tax_res.total_excluded
        tot_tax += tax_res.total_tax

        session.add(
            SaleOrderLine(
                company_id=company_id,
                order_id=order.id,
                product_id=ld.get("product_id"),
                name=str(ld["name"]),
                quantity=qty,
                price_unit=price,
                discount=disc,
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
        res_model="sale_order",
        res_id=order.id,
        body=f"Quotation created in DRAFT state for total {order.amount_total}",
        author_name=author_name,
        author_id=author_id,
    )
    return await get_sale_order(session, order.id)


async def confirm_sale_order(
    session: AsyncSession,
    order_id: int,
    *,
    author_name: str = "System",
    author_id: int | None = None,
) -> tuple[SaleOrder, StockPicking | None]:
    """Confirm Quotation -> Sales Order, assign SO/YYYY/NNNNN, and auto-create Delivery Order."""
    order = await get_sale_order(session, order_id)
    if order.state != "DRAFT":
        raise SalesError(f"Only DRAFT quotations can be confirmed (current state: {order.state})")

    old_name = order.name
    old_state = order.state
    order.name = await next_sequence_by_code(
        session,
        company_id=order.company_id,
        code="sale_order",
        ref_date=order.date_order,
        default_prefix="SO/%(year)s/",
    )
    order.state = "CONFIRMED"
    await session.flush()

    # Build Delivery Order for storable products
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
                        "price_unit": prod.standard_price,
                    }
                )

    picking: StockPicking | None = None
    if storable_moves:
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
        cust_loc = (
            await session.execute(
                select(StockLocation)
                .where(
                    StockLocation.company_id == order.company_id,
                    StockLocation.usage == "CUSTOMER",
                )
                .order_by(StockLocation.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if wh_loc and cust_loc:
            picking = await create_picking(
                session,
                company_id=order.company_id,
                picking_type="OUTGOING",
                location_id=wh_loc.id,
                location_dest_id=cust_loc.id,
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
        res_model="sale_order",
        res_id=order.id,
        body=f"Sales Order {order.name} confirmed"
        + (f"; auto-created Delivery {picking.name}" if picking else ""),
        message_type="state_change",
        author_name=author_name,
        author_id=author_id,
        diffs=[
            ("state", "Status", old_state, "CONFIRMED"),
            ("name", "Order Number", old_name, order.name),
        ],
    )
    return await get_sale_order(session, order.id), picking


async def create_invoice_from_sale_order(
    session: AsyncSession,
    order_id: int,
    *,
    invoice_date: date | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountMove:
    """Create a Customer Invoice (`out_invoice`) based on ordered or delivered quantities."""
    order = await get_sale_order(session, order_id)
    if order.state not in ("CONFIRMED", "DONE"):
        raise SalesError("Sales Order must be CONFIRMED before invoicing")

    sale_journal = (
        await session.execute(
            select(AccountJournal)
            .where(
                AccountJournal.company_id == order.company_id,
                AccountJournal.journal_type == "SALE",
            )
            .order_by(AccountJournal.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if sale_journal is None:
        raise SalesError("No SALE journal configured for company")

    default_income = (
        await session.execute(
            select(AccountAccount)
            .where(
                AccountAccount.company_id == order.company_id,
                AccountAccount.account_type == ACCOUNT_TYPE_INCOME,
            )
            .order_by(AccountAccount.code.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if default_income is None:
        raise SalesError("No Income account configured for company")

    inv_lines: list[InvoiceLineInput] = []
    for line in order.lines:
        target_qty = D(line.qty_delivered) if order.invoice_policy == "DELIVERED" else D(line.quantity)
        qty_to_invoice = quantize_db(target_qty - D(line.qty_invoiced))
        if qty_to_invoice <= ZERO:
            continue

        acct_id = default_income.id
        if line.product_id:
            prod = await session.get(ProductTemplate, line.product_id)
            if prod:
                cat = await session.get(ProductCategory, prod.category_id)
                if cat and cat.income_account_id:
                    acct_id = cat.income_account_id

        tax_ids = json.loads(line.tax_ids_json) if line.tax_ids_json else []
        inv_lines.append(
            InvoiceLineInput(
                account_id=acct_id,
                name=line.name,
                quantity=qty_to_invoice,
                price_unit=line.price_unit,
                discount=line.discount,
                tax_ids=tax_ids,
                analytic_account_id=line.analytic_account_id,
                product_id=line.product_id,
            )
        )
        line.qty_invoiced = quantize_db(D(line.qty_invoiced) + qty_to_invoice)

    if not inv_lines:
        raise SalesError(
            f"Nothing left to invoice on {order.name} under policy '{order.invoice_policy}'"
        )

    inv_dt = invoice_date or order.date_order
    move = await create_invoice_move(
        session,
        company_id=order.company_id,
        move_type="out_invoice",
        journal_id=sale_journal.id,
        partner_id=order.partner_id,
        invoice_date=inv_dt,
        invoice_date_due=inv_dt + timedelta(days=30),
        currency_id=order.currency_id,
        ref=order.name,
        invoice_origin=order.name,
        invoice_lines=inv_lines,
        author_name=author_name,
        author_id=author_id,
    )

    if all(D(l.qty_invoiced) >= D(l.quantity) for l in order.lines):
        order.state = "DONE"
    await session.flush()

    await log_chatter(
        session,
        company_id=order.company_id,
        res_model="sale_order",
        res_id=order.id,
        body=f"Customer Invoice draft (id={move.id}) generated from {order.name}",
        author_name=author_name,
        author_id=author_id,
    )
    return move
