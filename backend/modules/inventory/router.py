"""Inventory & Perpetual Valuation REST API Router."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.database import get_db
from backend.core.decimal_utils import D, ZERO, quantize_currency
from backend.core.security import RequestContext, get_current_context, require_permission
from backend.core.tenancy import ResPartner
from backend.modules.inventory.models import (
    ProductCategory,
    ProductTemplate,
    StockLocation,
    StockPicking,
    StockQuant,
    StockValuationLayer,
)
from backend.modules.inventory.valuation_engine import (
    InventoryError,
    create_picking,
    get_internal_on_hand,
    get_picking_with_moves,
    reserve_picking,
    validate_picking,
)

router = APIRouter(prefix="/inventory", tags=["Inventory"])


class ProductCreate(BaseModel):
    default_code: str
    name: str
    name_ar: str | None = None
    product_type: str = "STORABLE"
    category_id: int
    list_price: Decimal = ZERO
    standard_price: Decimal = ZERO
    uom: str = "Units"


class PickingMoveInput(BaseModel):
    product_id: int
    description: str = ""
    quantity: Decimal = Field(..., gt=Decimal("0"))
    price_unit: Decimal = ZERO


class PickingCreate(BaseModel):
    picking_type: str = "INCOMING"
    location_id: int
    location_dest_id: int
    scheduled_date: date
    partner_id: int | None = None
    origin: str | None = None
    moves: list[PickingMoveInput]


@router.get("/categories")
async def list_categories(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(ProductCategory)
            .where(ProductCategory.company_id == ctx.company_id)
            .order_by(ProductCategory.id.asc())
        )
    ).scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "costing_method": c.costing_method,
            "valuation": c.valuation,
            "stock_valuation_account_id": c.stock_valuation_account_id,
            "stock_input_account_id": c.stock_input_account_id,
            "stock_output_account_id": c.stock_output_account_id,
            "income_account_id": c.income_account_id,
            "expense_account_id": c.expense_account_id,
        }
        for c in rows
    ]


@router.get("/products")
async def list_products(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    prods = (
        await session.execute(
            select(ProductTemplate)
            .where(ProductTemplate.company_id == ctx.company_id, ProductTemplate.active.is_(True))
            .order_by(ProductTemplate.default_code.asc())
        )
    ).scalars().all()
    out = []
    for p in prods:
        qty_on_hand = await get_internal_on_hand(session, company_id=ctx.company_id, product_id=p.id)
        out.append(
            {
                "id": p.id,
                "default_code": p.default_code,
                "name": p.name,
                "name_ar": p.name_ar,
                "product_type": p.product_type,
                "category_id": p.category_id,
                "category_name": p.category.name if p.category else "",
                "costing_method": p.category.costing_method if p.category else "AVCO",
                "income_account_id": p.category.income_account_id if p.category else None,
                "expense_account_id": p.category.expense_account_id if p.category else None,
                "stock_input_account_id": p.category.stock_input_account_id if p.category else None,
                "list_price": str(quantize_currency(p.list_price)),
                "standard_price": str(quantize_currency(p.standard_price)),
                "qty_on_hand": str(quantize_currency(qty_on_hand)),
                "uom": p.uom,
            }
        )
    return out


@router.post("/products", status_code=201)
async def create_product(
    payload: ProductCreate,
    ctx: RequestContext = Depends(require_permission("product_template", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    prod = ProductTemplate(company_id=ctx.company_id, **payload.model_dump())
    session.add(prod)
    await session.flush()
    return {
        "id": prod.id,
        "default_code": prod.default_code,
        "name": prod.name,
        "list_price": str(quantize_currency(prod.list_price)),
        "standard_price": str(quantize_currency(prod.standard_price)),
    }


@router.get("/locations")
async def list_locations(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(StockLocation)
            .where(StockLocation.company_id == ctx.company_id, StockLocation.active.is_(True))
            .order_by(StockLocation.id.asc())
        )
    ).scalars().all()
    return [
        {
            "id": l.id,
            "name": l.name,
            "complete_name": l.complete_name,
            "usage": l.usage,
        }
        for l in rows
    ]


@router.get("/quants")
async def list_quants(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = (
        select(
            StockQuant,
            ProductTemplate.default_code,
            ProductTemplate.name.label("product_name"),
            ProductTemplate.standard_price,
            StockLocation.complete_name,
            StockLocation.usage,
        )
        .join(ProductTemplate, ProductTemplate.id == StockQuant.product_id)
        .join(StockLocation, StockLocation.id == StockQuant.location_id)
        .where(StockQuant.company_id == ctx.company_id)
        .order_by(StockLocation.usage.asc(), ProductTemplate.default_code.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": r.StockQuant.id,
            "product_id": r.StockQuant.product_id,
            "sku": r.default_code,
            "product_name": r.product_name,
            "location_id": r.StockQuant.location_id,
            "location_name": r.complete_name,
            "location_usage": r.usage,
            "quantity": str(quantize_currency(r.StockQuant.quantity)),
            "reserved_quantity": str(quantize_currency(r.StockQuant.reserved_quantity)),
            "unit_cost": str(quantize_currency(r.standard_price)),
            "total_value": str(quantize_currency(D(r.StockQuant.quantity) * D(r.standard_price))),
        }
        for r in rows
    ]


async def _serialize_picking(session: AsyncSession, p: StockPicking) -> dict[str, Any]:
    src = await session.get(StockLocation, p.location_id)
    dst = await session.get(StockLocation, p.location_dest_id)
    partner = await session.get(ResPartner, p.partner_id) if p.partner_id else None
    prod_ids = {m.product_id for m in p.moves}
    prods = (
        await session.execute(select(ProductTemplate).where(ProductTemplate.id.in_(prod_ids)))
    ).scalars().all() if prod_ids else []
    pmap = {pr.id: pr for pr in prods}

    return {
        "id": p.id,
        "name": p.name,
        "picking_type": p.picking_type,
        "origin": p.origin,
        "partner_id": p.partner_id,
        "partner_name": partner.name if partner else "",
        "location_id": p.location_id,
        "location_name": src.complete_name if src else "",
        "location_dest_id": p.location_dest_id,
        "location_dest_name": dst.complete_name if dst else "",
        "scheduled_date": p.scheduled_date.isoformat(),
        "date_done": p.date_done.isoformat() if p.date_done else None,
        "state": p.state,
        "moves": [
            {
                "id": m.id,
                "product_id": m.product_id,
                "product_sku": pmap[m.product_id].default_code if m.product_id in pmap else "",
                "product_name": pmap[m.product_id].name if m.product_id in pmap else "",
                "description": m.description,
                "product_uom_qty": str(quantize_currency(m.product_uom_qty)),
                "quantity_done": str(quantize_currency(m.quantity_done)),
                "price_unit": str(quantize_currency(m.price_unit)),
                "state": m.state,
            }
            for m in p.moves
        ],
    }


@router.get("/pickings")
async def list_pickings(
    picking_type: str | None = Query(default=None),
    origin: str | None = Query(default=None),
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = (
        select(StockPicking)
        .options(selectinload(StockPicking.moves))
        .where(StockPicking.company_id == ctx.company_id)
    )
    if picking_type:
        stmt = stmt.where(StockPicking.picking_type == picking_type)
    if origin:
        stmt = stmt.where(StockPicking.origin == origin)
    stmt = stmt.order_by(StockPicking.id.desc())
    pickings = (await session.execute(stmt)).scalars().all()
    return [await _serialize_picking(session, p) for p in pickings]


@router.post("/pickings", status_code=201)
async def create_picking_endpoint(
    payload: PickingCreate,
    ctx: RequestContext = Depends(require_permission("stock_picking", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    picking = await create_picking(
        session,
        company_id=ctx.company_id,
        picking_type=payload.picking_type,
        location_id=payload.location_id,
        location_dest_id=payload.location_dest_id,
        scheduled_date=payload.scheduled_date,
        partner_id=payload.partner_id,
        origin=payload.origin,
        moves_data=[m.model_dump() for m in payload.moves],
        author_name=ctx.user.full_name,
        author_id=ctx.user.id,
    )
    return await _serialize_picking(session, picking)


@router.post("/pickings/{picking_id}/reserve")
async def reserve_picking_endpoint(
    picking_id: int,
    ctx: RequestContext = Depends(require_permission("stock_picking", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        picking = await reserve_picking(
            session, picking_id, author_name=ctx.user.full_name, author_id=ctx.user.id
        )
        return await _serialize_picking(session, picking)
    except InventoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/pickings/{picking_id}/validate")
async def validate_picking_endpoint(
    picking_id: int,
    ctx: RequestContext = Depends(require_permission("stock_picking", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        picking, acc_moves = await validate_picking(
            session, picking_id, author_name=ctx.user.full_name, author_id=ctx.user.id
        )
        data = await _serialize_picking(session, picking)
        data["account_move_ids"] = [m.id for m in acc_moves]
        return data
    except InventoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/valuation-layers")
async def list_valuation_layers(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = (
        select(StockValuationLayer, ProductTemplate.default_code, ProductTemplate.name.label("product_name"))
        .join(ProductTemplate, ProductTemplate.id == StockValuationLayer.product_id)
        .where(StockValuationLayer.company_id == ctx.company_id)
        .order_by(StockValuationLayer.id.desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": r.StockValuationLayer.id,
            "product_id": r.StockValuationLayer.product_id,
            "sku": r.default_code,
            "product_name": r.product_name,
            "quantity": str(quantize_currency(r.StockValuationLayer.quantity)),
            "remaining_qty": str(quantize_currency(r.StockValuationLayer.remaining_qty)),
            "unit_cost": str(quantize_currency(r.StockValuationLayer.unit_cost)),
            "value": str(quantize_currency(r.StockValuationLayer.value)),
            "remaining_value": str(quantize_currency(r.StockValuationLayer.remaining_value)),
            "account_move_id": r.StockValuationLayer.account_move_id,
            "description": r.StockValuationLayer.description,
            "created_at": r.StockValuationLayer.created_at.isoformat(),
        }
        for r in rows
    ]
