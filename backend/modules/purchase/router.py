"""Purchase Procure-to-Pay REST API Router."""

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
from backend.modules.purchase.models import PurchaseOrder
from backend.modules.purchase.workflows import (
    PurchaseError,
    ThreeWayMatchError,
    compute_three_way_match,
    confirm_purchase_order,
    create_bill_from_purchase_order,
    create_purchase_order,
    get_purchase_order,
)

router = APIRouter(prefix="/purchase", tags=["Purchase"])


class PurchaseOrderLineCreate(BaseModel):
    product_id: int | None = None
    name: str
    quantity: Decimal = ONE
    price_unit: Decimal = ZERO
    tax_ids: list[int] = Field(default_factory=list)
    analytic_account_id: int | None = None


class PurchaseOrderCreate(BaseModel):
    partner_id: int
    date_order: date
    currency_id: int | None = None
    note: str | None = None
    lines: list[PurchaseOrderLineCreate]


class CreateBillRequest(BaseModel):
    bill_date: date | None = None
    enforce_three_way_match: bool = True


async def _serialize_po(session: AsyncSession, po: PurchaseOrder) -> dict[str, Any]:
    partner = await session.get(ResPartner, po.partner_id)
    curr = await session.get(ResCurrency, po.currency_id)

    receipt_cnt = (
        await session.execute(
            select(func.count(StockPicking.id)).where(
                StockPicking.company_id == po.company_id,
                StockPicking.origin == po.name,
                StockPicking.picking_type == "INCOMING",
            )
        )
    ).scalar_one() if po.name != "DRAFT" else 0

    bill_cnt = (
        await session.execute(
            select(func.count(AccountMove.id)).where(
                AccountMove.company_id == po.company_id,
                AccountMove.invoice_origin == po.name,
                AccountMove.move_type.in_(["in_invoice", "in_refund"]),
            )
        )
    ).scalar_one() if po.name != "DRAFT" else 0

    match_info = compute_three_way_match(po)

    return {
        "id": po.id,
        "name": po.name,
        "partner_id": po.partner_id,
        "partner_name": partner.name if partner else "",
        "currency_id": po.currency_id,
        "currency_code": curr.code if curr else "USD",
        "currency_symbol": curr.symbol if curr else "$",
        "date_order": po.date_order.isoformat(),
        "state": po.state,
        "three_way_status": match_info["status"],
        "amount_untaxed": str(quantize_currency(po.amount_untaxed)),
        "amount_tax": str(quantize_currency(po.amount_tax)),
        "amount_total": str(quantize_currency(po.amount_total)),
        "note": po.note,
        "receipt_count": int(receipt_cnt),
        "bill_count": int(bill_cnt),
        "lines": [
            {
                "id": l.id,
                "product_id": l.product_id,
                "name": l.name,
                "quantity": str(quantize_currency(l.quantity)),
                "qty_received": str(quantize_currency(l.qty_received)),
                "qty_billed": str(quantize_currency(l.qty_billed)),
                "price_unit": str(quantize_currency(l.price_unit)),
                "tax_ids": json.loads(l.tax_ids_json) if l.tax_ids_json else [],
                "analytic_account_id": l.analytic_account_id,
                "price_subtotal": str(quantize_currency(l.price_subtotal)),
                "price_tax": str(quantize_currency(l.price_tax)),
                "price_total": str(quantize_currency(l.price_total)),
            }
            for l in po.lines
        ],
    }


@router.get("/orders")
async def list_purchase_orders(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.lines))
        .where(PurchaseOrder.company_id == ctx.company_id)
        .order_by(PurchaseOrder.id.desc())
    )
    orders = (await session.execute(stmt)).scalars().all()
    return [await _serialize_po(session, o) for o in orders]


@router.get("/orders/{order_id}")
async def get_purchase_order_endpoint(
    order_id: int,
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        po = await get_purchase_order(session, order_id)
    except PurchaseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if po.company_id != ctx.company_id:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return await _serialize_po(session, po)


@router.post("/orders", status_code=201)
async def create_purchase_order_endpoint(
    payload: PurchaseOrderCreate,
    ctx: RequestContext = Depends(require_permission("purchase_order", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        po = await create_purchase_order(
            session,
            company_id=ctx.company_id,
            partner_id=payload.partner_id,
            date_order=payload.date_order,
            currency_id=payload.currency_id,
            note=payload.note,
            lines_data=[l.model_dump() for l in payload.lines],
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
        return await _serialize_po(session, po)
    except PurchaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{order_id}/confirm")
async def confirm_purchase_order_endpoint(
    order_id: int,
    ctx: RequestContext = Depends(require_permission("purchase_order", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        po, picking = await confirm_purchase_order(
            session, order_id, author_name=ctx.user.full_name, author_id=ctx.user.id
        )
        data = await _serialize_po(session, po)
        data["created_picking_id"] = picking.id if picking else None
        return data
    except PurchaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/orders/{order_id}/three-way-match")
async def get_three_way_match(
    order_id: int,
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        po = await get_purchase_order(session, order_id)
    except PurchaseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return compute_three_way_match(po)


@router.post("/orders/{order_id}/create-bill", status_code=201)
async def create_bill_endpoint(
    order_id: int,
    payload: CreateBillRequest = CreateBillRequest(),
    ctx: RequestContext = Depends(require_permission("purchase_order", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        move = await create_bill_from_purchase_order(
            session,
            order_id,
            bill_date=payload.bill_date,
            enforce_three_way_match=payload.enforce_three_way_match,
            author_name=ctx.user.full_name,
            author_id=ctx.user.id,
        )
        return {"bill_id": move.id, "move_type": move.move_type, "state": move.state}
    except ThreeWayMatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PurchaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
