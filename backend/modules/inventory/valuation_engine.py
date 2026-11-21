"""Double-Entry Stock Movement Engine with STANDARD / AVCO / FIFO Perpetual Valuation & GL Posting."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.chatter import log_chatter
from backend.core.database import apply_for_update, atomic_lock
from backend.core.decimal_utils import D, ZERO, quantize_currency, quantize_db
from backend.core.sequences import next_sequence_by_code
from backend.modules.accounting.models import AccountJournal, AccountMove
from backend.modules.accounting.posting_engine import (
    JournalLineInput,
    create_journal_entry,
    post_move,
)
from backend.modules.inventory.models import (
    ProductCategory,
    ProductTemplate,
    StockLocation,
    StockMove,
    StockPicking,
    StockQuant,
    StockValuationLayer,
)


class InventoryError(Exception):
    """Domain error raised on invalid stock transfer, insufficient stock, or missing valuation config."""


async def _get_or_create_quant_locked(
    session: AsyncSession,
    *,
    company_id: int,
    product_id: int,
    location_id: int,
) -> StockQuant:
    stmt = apply_for_update(
        select(StockQuant).where(
            StockQuant.company_id == company_id,
            StockQuant.product_id == product_id,
            StockQuant.location_id == location_id,
        ),
        session,
    )
    quant = (await session.execute(stmt)).scalar_one_or_none()
    if quant is None:
        quant = StockQuant(
            company_id=company_id,
            product_id=product_id,
            location_id=location_id,
            quantity=ZERO,
            reserved_quantity=ZERO,
        )
        session.add(quant)
        await session.flush()
    return quant


async def get_internal_on_hand(
    session: AsyncSession, *, company_id: int, product_id: int
) -> Decimal:
    """Total quantity on hand across all INTERNAL warehouse locations for a product."""
    stmt = (
        select(func.coalesce(func.sum(StockQuant.quantity), 0))
        .join(StockLocation, StockLocation.id == StockQuant.location_id)
        .where(
            StockQuant.company_id == company_id,
            StockQuant.product_id == product_id,
            StockLocation.usage == "INTERNAL",
        )
    )
    return quantize_db(D((await session.execute(stmt)).scalar_one()))


async def create_picking(
    session: AsyncSession,
    *,
    company_id: int,
    picking_type: str,  # INCOMING, OUTGOING, INTERNAL
    location_id: int,
    location_dest_id: int,
    scheduled_date: date,
    moves_data: list[dict],
    partner_id: int | None = None,
    origin: str | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> StockPicking:
    """Create a warehouse transfer (Receipt / Delivery / Internal) with gapless sequence."""
    seq_map = {
        "INCOMING": ("stock_in", "WH/IN/%(year)s/"),
        "OUTGOING": ("stock_out", "WH/OUT/%(year)s/"),
        "INTERNAL": ("stock_int", "WH/INT/%(year)s/"),
    }
    seq_code, seq_prefix = seq_map.get(picking_type, ("stock_int", "WH/INT/%(year)s/"))
    name = await next_sequence_by_code(
        session, company_id=company_id, code=seq_code, ref_date=scheduled_date, default_prefix=seq_prefix
    )

    picking = StockPicking(
        company_id=company_id,
        name=name,
        picking_type=picking_type,
        origin=origin,
        partner_id=partner_id,
        location_id=location_id,
        location_dest_id=location_dest_id,
        scheduled_date=scheduled_date,
        state="CONFIRMED",
    )
    session.add(picking)
    await session.flush()

    for md in moves_data:
        qty = quantize_db(D(md["quantity"]))
        if qty <= ZERO:
            continue
        session.add(
            StockMove(
                company_id=company_id,
                picking_id=picking.id,
                product_id=int(md["product_id"]),
                description=str(md.get("description", "")),
                product_uom_qty=qty,
                quantity_done=ZERO,
                price_unit=quantize_db(D(md.get("price_unit", ZERO))),
                location_id=location_id,
                location_dest_id=location_dest_id,
                date=scheduled_date,
                state="CONFIRMED",
            )
        )
    await session.flush()

    await log_chatter(
        session,
        company_id=company_id,
        res_model="stock_picking",
        res_id=picking.id,
        body=f"Transfer {picking.name} ({picking_type}) created from origin {origin or 'Manual'}",
        author_name=author_name,
        author_id=author_id,
    )
    return await get_picking_with_moves(session, picking.id)


async def get_picking_with_moves(session: AsyncSession, picking_id: int) -> StockPicking:
    stmt = (
        select(StockPicking)
        .options(selectinload(StockPicking.moves))
        .where(StockPicking.id == picking_id)
    )
    picking = (await session.execute(stmt)).scalar_one_or_none()
    if picking is None:
        raise InventoryError(f"StockPicking id={picking_id} not found")
    return picking


async def reserve_picking(
    session: AsyncSession,
    picking_id: int,
    *,
    author_name: str = "System",
    author_id: int | None = None,
) -> StockPicking:
    """Atomically reserve available stock at source location using SELECT ... FOR UPDATE."""
    async with atomic_lock(session):
        picking = await get_picking_with_moves(session, picking_id)
        if picking.state in ("DONE", "CANCELLED"):
            raise InventoryError(f"Picking {picking.name} is already {picking.state}")

        src_loc = await session.get(StockLocation, picking.location_id)
        if src_loc and src_loc.usage == "INTERNAL":
            for move in picking.moves:
                quant = await _get_or_create_quant_locked(
                    session,
                    company_id=picking.company_id,
                    product_id=move.product_id,
                    location_id=move.location_id,
                )
                available = D(quant.quantity) - D(quant.reserved_quantity)
                if available < D(move.product_uom_qty):
                    prod = await session.get(ProductTemplate, move.product_id)
                    raise InventoryError(
                        f"Insufficient stock to reserve '{prod.name if prod else move.product_id}': "
                        f"available={available}, requested={move.product_uom_qty}"
                    )
                quant.reserved_quantity = quantize_db(D(quant.reserved_quantity) + D(move.product_uom_qty))
                move.state = "ASSIGNED"

        old_state = picking.state
        picking.state = "ASSIGNED"
        await session.flush()
        await log_chatter(
            session,
            company_id=picking.company_id,
            res_model="stock_picking",
            res_id=picking.id,
            body=f"Stock reserved for transfer {picking.name}",
            message_type="state_change",
            author_name=author_name,
            author_id=author_id,
            diffs=[("state", "Status", old_state, "ASSIGNED")],
        )
        return picking


async def _post_valuation_journal_entry(
    session: AsyncSession,
    *,
    company_id: int,
    move_date: date,
    debit_account_id: int,
    credit_account_id: int,
    amount: Decimal,
    description: str,
    ref: str,
    partner_id: int | None = None,
    product_id: int | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> AccountMove | None:
    amt = quantize_currency(amount, 2)
    if amt <= ZERO:
        return None

    # Locate Stock/General journal
    journal = (
        await session.execute(
            select(AccountJournal)
            .where(
                AccountJournal.company_id == company_id,
                AccountJournal.code.in_(["STJ", "MISC", "GEN"]),
            )
            .order_by(AccountJournal.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if journal is None:
        journal = (
            await session.execute(
                select(AccountJournal)
                .where(
                    AccountJournal.company_id == company_id,
                    AccountJournal.journal_type == "GENERAL",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    if journal is None:
        raise InventoryError("No GENERAL/Inventory journal configured for automated stock accounting")

    acc_move = await create_journal_entry(
        session,
        company_id=company_id,
        journal_id=journal.id,
        move_date=move_date,
        ref=ref,
        partner_id=partner_id,
        lines=[
            JournalLineInput(
                account_id=debit_account_id,
                partner_id=partner_id,
                product_id=product_id,
                name=description,
                debit=amt,
                credit=ZERO,
            ),
            JournalLineInput(
                account_id=credit_account_id,
                partner_id=partner_id,
                product_id=product_id,
                name=description,
                debit=ZERO,
                credit=amt,
            ),
        ],
        author_name=author_name,
        author_id=author_id,
    )
    return await post_move(session, acc_move.id, author_name=author_name, author_id=author_id)


async def validate_picking(
    session: AsyncSession,
    picking_id: int,
    *,
    validation_date: date | None = None,
    author_name: str = "System",
    author_id: int | None = None,
) -> tuple[StockPicking, list[AccountMove]]:
    """Validate a StockPicking atomically:
    1. Executes double-entry StockQuant transfers (source -= qty, dest += qty -> strict unit conservation).
    2. Applies STANDARD, AVCO, or FIFO costing rules and creates StockValuationLayer entries.
    3. Posts balanced perpetual accounting AccountMove entries (Stock Valuation / Interim / COGS).
    """
    async with atomic_lock(session):
        stmt = apply_for_update(
            select(StockPicking)
            .options(selectinload(StockPicking.moves))
            .where(StockPicking.id == picking_id),
            session,
        )
        picking = (await session.execute(stmt)).scalar_one_or_none()
        if picking is None:
            raise InventoryError(f"StockPicking id={picking_id} not found")
        if picking.state == "DONE":
            raise InventoryError(f"Transfer {picking.name} is already validated (DONE)")
        if picking.state == "CANCELLED":
            raise InventoryError(f"Cannot validate a CANCELLED transfer ({picking.name})")

        val_date = validation_date or picking.scheduled_date
        created_account_moves: list[AccountMove] = []

        for sm in picking.moves:
            qty = quantize_db(D(sm.quantity_done) if D(sm.quantity_done) > ZERO else D(sm.product_uom_qty))
            if qty <= ZERO:
                continue

            product = await session.get(ProductTemplate, sm.product_id)
            if product is None:
                raise InventoryError(f"Product id={sm.product_id} not found")
            category = await session.get(ProductCategory, product.category_id)
            src_loc = await session.get(StockLocation, sm.location_id)
            dest_loc = await session.get(StockLocation, sm.location_dest_id)
            if src_loc is None or dest_loc is None:
                raise InventoryError("Source and destination stock locations must exist")

            # Compute on-hand BEFORE applying this stock movement (needed for AVCO)
            prev_on_hand = await get_internal_on_hand(
                session, company_id=picking.company_id, product_id=product.id
            )

            # Enforce non-negative internal stock on outgoing deliveries
            is_incoming = src_loc.usage != "INTERNAL" and dest_loc.usage == "INTERNAL"
            is_outgoing = src_loc.usage == "INTERNAL" and dest_loc.usage != "INTERNAL"

            if is_outgoing and prev_on_hand < qty:
                raise InventoryError(
                    f"Insufficient inventory for '{product.name}': on_hand={prev_on_hand}, requested={qty}"
                )

            # Double-entry StockQuant update (strict unit conservation: -qty at source, +qty at dest)
            src_quant = await _get_or_create_quant_locked(
                session,
                company_id=picking.company_id,
                product_id=product.id,
                location_id=src_loc.id,
            )
            dest_quant = await _get_or_create_quant_locked(
                session,
                company_id=picking.company_id,
                product_id=product.id,
                location_id=dest_loc.id,
            )
            src_quant.quantity = quantize_db(D(src_quant.quantity) - qty)
            if picking.state == "ASSIGNED" and src_loc.usage == "INTERNAL":
                src_quant.reserved_quantity = max(
                    ZERO, quantize_db(D(src_quant.reserved_quantity) - qty)
                )
            dest_quant.quantity = quantize_db(D(dest_quant.quantity) + qty)

            sm.quantity_done = qty
            sm.state = "DONE"
            sm.date = val_date

            # Perpetual Inventory Valuation for STORABLE products
            if product.product_type == "STORABLE" and category is not None:
                costing = category.costing_method

                if is_incoming:
                    # Determine incoming unit cost
                    if costing == "STANDARD":
                        unit_cost = D(product.standard_price)
                    else:
                        unit_cost = D(sm.price_unit) if D(sm.price_unit) > ZERO else D(product.standard_price)

                    layer_value = quantize_currency(qty * unit_cost, 2)

                    # AVCO: dynamically recompute weighted average cost on receipt
                    if costing == "AVCO":
                        prev_val = max(ZERO, prev_on_hand) * D(product.standard_price)
                        new_total_qty = max(ZERO, prev_on_hand) + qty
                        if new_total_qty > ZERO:
                            new_avg = ((prev_val + qty * unit_cost) / new_total_qty).quantize(
                                Decimal("0.000001"), rounding=ROUND_HALF_UP
                            )
                            product.standard_price = new_avg
                    elif costing == "FIFO":
                        # Update reference standard_price to latest receipt cost
                        if prev_on_hand <= ZERO:
                            product.standard_price = unit_cost

                    acc_move = None
                    if (
                        category.valuation == "REAL_TIME"
                        and category.stock_valuation_account_id
                        and category.stock_input_account_id
                    ):
                        acc_move = await _post_valuation_journal_entry(
                            session,
                            company_id=picking.company_id,
                            move_date=val_date,
                            debit_account_id=category.stock_valuation_account_id,
                            credit_account_id=category.stock_input_account_id,
                            amount=layer_value,
                            description=f"Stock Receipt: {product.name} ({qty} {product.uom}) - {picking.name}",
                            ref=f"{picking.name} / {product.default_code}",
                            partner_id=picking.partner_id,
                            product_id=product.id,
                            author_name=author_name,
                            author_id=author_id,
                        )
                        if acc_move:
                            created_account_moves.append(acc_move)

                    session.add(
                        StockValuationLayer(
                            company_id=picking.company_id,
                            product_id=product.id,
                            stock_move_id=sm.id,
                            quantity=qty,
                            remaining_qty=qty,
                            unit_cost=unit_cost,
                            value=layer_value,
                            remaining_value=layer_value,
                            account_move_id=acc_move.id if acc_move else None,
                            description=f"Receipt {picking.name} ({costing})",
                        )
                    )

                elif is_outgoing:
                    # Determine outgoing COGS value via FIFO, AVCO, or STANDARD
                    if costing == "FIFO":
                        layers_stmt = apply_for_update(
                            select(StockValuationLayer)
                            .where(
                                StockValuationLayer.company_id == picking.company_id,
                                StockValuationLayer.product_id == product.id,
                                StockValuationLayer.remaining_qty > ZERO,
                            )
                            .order_by(StockValuationLayer.id.asc()),
                            session,
                        )
                        open_layers = (await session.execute(layers_stmt)).scalars().all()
                        qty_to_consume = qty
                        consumed_value = ZERO
                        for layer in open_layers:
                            if qty_to_consume <= ZERO:
                                break
                            take = min(D(layer.remaining_qty), qty_to_consume)
                            if take == D(layer.remaining_qty):
                                take_val = D(layer.remaining_value)
                            else:
                                take_val = quantize_currency(take * D(layer.unit_cost), 2)
                            layer.remaining_qty = quantize_db(D(layer.remaining_qty) - take)
                            layer.remaining_value = quantize_currency(D(layer.remaining_value) - take_val, 2)
                            consumed_value += take_val
                            qty_to_consume = quantize_db(qty_to_consume - take)

                        if qty_to_consume > ZERO:
                            consumed_value += quantize_currency(qty_to_consume * D(product.standard_price), 2)

                        out_value = quantize_currency(consumed_value, 2)
                        effective_unit_cost = (out_value / qty).quantize(
                            Decimal("0.000001"), rounding=ROUND_HALF_UP
                        )
                    else:
                        # AVCO and STANDARD both value outgoing units at current `product.standard_price`
                        effective_unit_cost = D(product.standard_price)
                        out_value = quantize_currency(qty * effective_unit_cost, 2)

                    debit_acct = category.expense_account_id or category.stock_output_account_id
                    acc_move = None
                    if (
                        category.valuation == "REAL_TIME"
                        and category.stock_valuation_account_id
                        and debit_acct
                    ):
                        acc_move = await _post_valuation_journal_entry(
                            session,
                            company_id=picking.company_id,
                            move_date=val_date,
                            debit_account_id=debit_acct,
                            credit_account_id=category.stock_valuation_account_id,
                            amount=out_value,
                            description=f"COGS / Delivery: {product.name} ({qty} {product.uom}) - {picking.name}",
                            ref=f"{picking.name} / {product.default_code}",
                            partner_id=picking.partner_id,
                            product_id=product.id,
                            author_name=author_name,
                            author_id=author_id,
                        )
                        if acc_move:
                            created_account_moves.append(acc_move)

                    session.add(
                        StockValuationLayer(
                            company_id=picking.company_id,
                            product_id=product.id,
                            stock_move_id=sm.id,
                            quantity=-qty,
                            remaining_qty=ZERO,
                            unit_cost=effective_unit_cost,
                            value=-out_value,
                            remaining_value=ZERO,
                            account_move_id=acc_move.id if acc_move else None,
                            description=f"Delivery {picking.name} ({costing})",
                        )
                    )

        old_state = picking.state
        picking.state = "DONE"
        picking.date_done = val_date
        await session.flush()

        # Sync delivered/received quantities back to Sales Orders / Purchase Orders if origin matches
        await _sync_order_quantities_from_picking(session, picking)

        await log_chatter(
            session,
            company_id=picking.company_id,
            res_model="stock_picking",
            res_id=picking.id,
            body=f"Transfer {picking.name} validated (DONE). Posted {len(created_account_moves)} valuation journal entries.",
            message_type="state_change",
            author_name=author_name,
            author_id=author_id,
            diffs=[("state", "Status", old_state, "DONE")],
        )
        return picking, created_account_moves


async def _sync_order_quantities_from_picking(session: AsyncSession, picking: StockPicking) -> None:
    """Update SaleOrderLine.qty_delivered or PurchaseOrderLine.qty_received when transfer completes."""
    if not picking.origin:
        return

    if picking.picking_type == "OUTGOING":
        from backend.modules.sales.models import SaleOrder, SaleOrderLine

        so = (
            await session.execute(
                select(SaleOrder)
                .options(selectinload(SaleOrder.lines))
                .where(
                    SaleOrder.company_id == picking.company_id,
                    SaleOrder.name == picking.origin,
                )
            )
        ).scalar_one_or_none()
        if so:
            done_by_prod: dict[int, Decimal] = {}
            for sm in picking.moves:
                done_by_prod[sm.product_id] = done_by_prod.get(sm.product_id, ZERO) + D(sm.quantity_done)
            for line in so.lines:
                if line.product_id and line.product_id in done_by_prod:
                    line.qty_delivered = quantize_db(D(line.qty_delivered) + done_by_prod[line.product_id])

    elif picking.picking_type == "INCOMING":
        from backend.modules.purchase.models import PurchaseOrder, PurchaseOrderLine

        po = (
            await session.execute(
                select(PurchaseOrder)
                .options(selectinload(PurchaseOrder.lines))
                .where(
                    PurchaseOrder.company_id == picking.company_id,
                    PurchaseOrder.name == picking.origin,
                )
            )
        ).scalar_one_or_none()
        if po:
            done_by_prod = {}
            for sm in picking.moves:
                done_by_prod[sm.product_id] = done_by_prod.get(sm.product_id, ZERO) + D(sm.quantity_done)
            for line in po.lines:
                if line.product_id and line.product_id in done_by_prod:
                    line.qty_received = quantize_db(D(line.qty_received) + done_by_prod[line.product_id])
    await session.flush()
