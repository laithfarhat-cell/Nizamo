"""Sales Order-to-Cash REST API Router."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.database import get_db
from backend.core.decimal_utils import ONE, ZERO, quantize_currency
from backend.core.security import RequestContext, get_current_context, require_permission
from backend.core.tenancy import ResCurrency, ResPartner
from backend.modules.accounting.models import AccountMove
from backend.modules.inventory.models import StockPicking
from backend.modules.sales.models import SaleOrder
from backend.modules.sales.workflows import (
    SalesError,
    confirm_sale_order,
    create_invoice_from_sale_order,
    create_sale_order,
    get_sale_order,
)

router = APIRouter(prefix="/sales", tags=["Sales"])


class SaleOrderLineCreate(BaseModel):
    product_id: int | None = None
    name: str
    quantity: Decimal = ONE
    price_unit: Decimal = ZERO
    discount: Decimal = ZERO
    tax_ids: list[int] = Field(default_factory=list)
    analytic_account_id: int | None = None


class SaleOrderCreate(BaseModel):
    partner_id: int
    date_order: date
    currency_id: int | None = None
    invoice_policy: str = "ORDERED"
    note: str | None = None
    lines: list[SaleOrderLineCreate]


async def _serialize_sale_order(session: AsyncSession, so: SaleOrder) -> dict[str, Any]:
    partner = await session.get(ResPartner, so.partner_id)
    curr = await session.get(ResCurrency, so.currency_id)

    delivery_cnt = (
        await session.execute(
            select(func.count(StockPicking.id)).where(
                StockPicking.company_id == so.company_id,
                StockPicking.origin == so.name,
                StockPicking.picking_type == "OUTGOING",
            )
        )
    ).scalar_one() if so.name != "DRAFT" else 0

    inv_cnt = (
        await session.execute(
            select(func.count(AccountMove.id)).where(
                AccountMove.company_id == so.company_id,
                AccountMove.invoice_origin == so.name,
                AccountMove.move_type.in_(["out_invoice", "out_refund"]),
            )
        )
    ).scalar_one() if so.name != "DRAFT" else 0

    return {
        "id": so.id,
        "name": so.name,
        "partner_id": so.partner_id,
        "partner_name": partner.name if partner else "",
        "currency_id": so.currency_id,
        "currency_code": curr.code if curr else "USD",
        "currency_symbol": curr.symbol if curr else "$",
        "date_order": so.date_order.isoformat(),
        "state": so.state,
        "invoice_policy": so.invoice_policy,
        "amount_untaxed": str(quantize_currency(so.amount_untaxed)),
        "amount_tax": str(quantize_currency(so.amount_tax)),
        "amount_total": str(quantize_currency(so.amount_total)),
        "note": so.note,
        "delivery_count": int(delivery_cnt),
        "invoice_count": int(inv_cnt),
        "lines": [
            {
                "id": l.id,
                "product_id": l.product_id,
                "name": l.name,
                "quantity": str(quantize_currency(l.quantity)),
                "qty_delivered": str(quantize_currency(l.qty_delivered)),
                "qty_invoiced": str(quantize_currency(l.qty_invoiced)),
                "price_unit": str(quantize_currency(l.price_unit)),
                "discount": str(l.discount),
                "tax_ids": json.loads(l.tax_ids_json) if l.tax_ids_json else [],
                "analytic_account_id": l.analytic_account_id,
                "price_subtotal": str(quantize_currency(l.price_subtotal)),
                "price_tax": str(quantize_currency(l.price_tax)),
                "price_total": str(quantize_currency(l.price_total)),
            }
            for l in so.lines
        ],
    }


@router.get("/orders")
async def list_sale_orders(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = (
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines))
        .where(SaleOrder.company_id == ctx.company_id)
        .order_by(SaleOrder.id.desc())
    )
    orders = (await session.execute(stmt)).scalars().all()
    return [await _serialize_sale_order(session, o) for o in orders]


@router.get("/orders/{order_id}")
async def get_sale_order_endpoint(
    order_id: int,
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        so = await get_sale_order(session, order_id)
    except SalesError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if so.company_id != ctx.company_id:
        raise HTTPException(status_code=404, detail="Order not found")
    return await _serialize_sale_order(session, so)


@router.post("/orders", status_code=201)
async def create_sale_order_endpoint(
    payload: SaleOrderCreate,
    ctx: RequestContext = Depends(require_permission("sale_order", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        so = await create_sale_order(
            session,
            company_id=ctx.company_id,
            partner_id=payload.partner_id,
            date_order=payload.date_order,
            currency_id=payload.currency_id,
            invoice_policy=payload.invoice_policy,
            note=payload.note,
            lines_data=[l.model_dump() for l in payload.lines],
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
        return await _serialize_sale_order(session, so)
    except SalesError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{order_id}/confirm")
async def confirm_sale_order_endpoint(
    order_id: int,
    ctx: RequestContext = Depends(require_permission("sale_order", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        so, picking = await confirm_sale_order(
            session, order_id, author_name=ctx.user.full_name, author_id=ctx.user.id
        )
        data = await _serialize_sale_order(session, so)
        data["created_picking_id"] = picking.id if picking else None
        return data
    except SalesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/orders/{order_id}/create-invoice", status_code=201)
async def create_invoice_endpoint(
    order_id: int,
    ctx: RequestContext = Depends(require_permission("sale_order", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        move = await create_invoice_from_sale_order(
            session, order_id, author_name=ctx.user.full_name, author_id=ctx.user.id
        )
        return {"invoice_id": move.id, "move_type": move.move_type, "state": move.state}
    except SalesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
