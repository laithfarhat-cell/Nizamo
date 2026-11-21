"""Core Kernel API Router: Auth, Multi-Company, Currencies/Rates, Partners, Sequences, Registry, Chatter."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.chatter import get_document_chatter, log_chatter
from backend.core.database import get_db
from backend.core.decimal_utils import D, quantize_db
from backend.core.registry import registry
from backend.core.security import (
    ROLE_ADMIN,
    RequestContext,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    get_current_context,
    require_permission,
    verify_password,
    ResUser,
)
from backend.core.sequences import IrSequence
from backend.core.tenancy import ResCompany, ResCurrency, ResCurrencyRate, ResPartner

router = APIRouter(prefix="/core", tags=["Core Kernel"])


# --- Schemas ---

class LoginRequest(BaseModel):
    login: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class PartnerCreate(BaseModel):
    name: str
    name_ar: str | None = None
    ref: str | None = None
    email: str | None = None
    phone: str | None = None
    vat: str | None = None
    address: str | None = None
    is_customer: bool = True
    is_supplier: bool = False


class CurrencyRateCreate(BaseModel):
    currency_id: int
    effective_date: date
    rate: Decimal = Field(..., gt=Decimal("0"))


class CompanyLockDatesUpdate(BaseModel):
    fiscalyear_lock_date: date | None = None
    tax_lock_date: date | None = None


class ChatterCommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


# --- Auth ---

@router.post("/auth/login")
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    stmt = select(ResUser).where(ResUser.login == payload.login, ResUser.active.is_(True))
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    access_token = create_access_token(user.id, user.login)
    refresh_token = create_refresh_token(user.id, user.login)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "login": user.login,
            "full_name": user.full_name,
            "email": user.email,
            "roles": sorted(user.role_names),
            "default_company_id": user.default_company_id,
            "allowed_company_ids": sorted(user.allowed_company_ids),
        },
    }


@router.post("/auth/refresh")
async def refresh_token_endpoint(
    payload: RefreshRequest, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    decoded = decode_access_token(payload.refresh_token, expected_type="refresh")
    user = await session.get(ResUser, int(decoded["sub"]))
    if user is None or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or missing")
    return {
        "access_token": create_access_token(user.id, user.login),
        "refresh_token": create_refresh_token(user.id, user.login),
        "token_type": "bearer",
    }


@router.get("/auth/me")
async def get_me(ctx: RequestContext = Depends(get_current_context)) -> dict[str, Any]:
    return {
        "id": ctx.user.id,
        "login": ctx.user.login,
        "full_name": ctx.user.full_name,
        "email": ctx.user.email,
        "roles": sorted(ctx.user.role_names),
        "active_company_id": ctx.company_id,
        "allowed_company_ids": sorted(ctx.user.allowed_company_ids),
    }


# --- Modules Registry ---

@router.get("/modules")
async def list_modules(ctx: RequestContext = Depends(get_current_context)) -> list[dict[str, Any]]:
    return registry.describe_modules()


# --- Companies ---

@router.get("/companies")
async def list_companies(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(ResCompany).where(ResCompany.active.is_(True))
    if ROLE_ADMIN not in ctx.user.role_names:
        stmt = stmt.where(ResCompany.id.in_(ctx.user.allowed_company_ids))
    stmt = stmt.order_by(ResCompany.id.asc())
    companies = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "name_ar": c.name_ar,
            "tax_id": c.tax_id,
            "currency_id": c.currency_id,
            "currency_code": c.currency.code if c.currency else "USD",
            "currency_symbol": c.currency.symbol if c.currency else "$",
            "fiscalyear_lock_date": c.fiscalyear_lock_date.isoformat() if c.fiscalyear_lock_date else None,
            "tax_lock_date": c.tax_lock_date.isoformat() if c.tax_lock_date else None,
        }
        for c in companies
    ]


@router.patch("/companies/{company_id}/lock-dates")
async def update_lock_dates(
    company_id: int,
    payload: CompanyLockDatesUpdate,
    ctx: RequestContext = Depends(require_permission("res_company", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    company = await session.get(ResCompany, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    old_fy = company.fiscalyear_lock_date
    old_tax = company.tax_lock_date
    company.fiscalyear_lock_date = payload.fiscalyear_lock_date
    company.tax_lock_date = payload.tax_lock_date
    await session.flush()
    await log_chatter(
        session,
        company_id=company.id,
        res_model="res_company",
        res_id=company.id,
        body="Updated fiscal lock dates",
        author_name=ctx.user.full_name,
        author_id=ctx.user.id,
        diffs=[
            ("fiscalyear_lock_date", "Fiscal Year Lock Date", old_fy, company.fiscalyear_lock_date),
            ("tax_lock_date", "Tax Lock Date", old_tax, company.tax_lock_date),
        ],
    )
    return {
        "id": company.id,
        "fiscalyear_lock_date": company.fiscalyear_lock_date.isoformat() if company.fiscalyear_lock_date else None,
        "tax_lock_date": company.tax_lock_date.isoformat() if company.tax_lock_date else None,
    }


# --- Currencies & Exchange Rates ---

@router.get("/currencies")
async def list_currencies(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    currencies = (
        await session.execute(select(ResCurrency).where(ResCurrency.active.is_(True)).order_by(ResCurrency.code))
    ).scalars().all()
    rates = (
        await session.execute(
            select(ResCurrencyRate)
            .where(ResCurrencyRate.company_id == ctx.company_id)
            .order_by(ResCurrencyRate.effective_date.desc())
        )
    ).scalars().all()

    rates_by_curr: dict[int, list[dict[str, Any]]] = {}
    for r in rates:
        rates_by_curr.setdefault(r.currency_id, []).append(
            {
                "id": r.id,
                "effective_date": r.effective_date.isoformat(),
                "rate": str(r.rate),
            }
        )

    return [
        {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "symbol": c.symbol,
            "decimal_places": c.decimal_places,
            "latest_rate": rates_by_curr[c.id][0]["rate"] if c.id in rates_by_curr else "1.000000",
            "rates": rates_by_curr.get(c.id, []),
        }
        for c in currencies
    ]


@router.post("/currency-rates", status_code=201)
async def upsert_currency_rate(
    payload: CurrencyRateCreate,
    ctx: RequestContext = Depends(require_permission("res_currency", "write")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(ResCurrencyRate).where(
        ResCurrencyRate.company_id == ctx.company_id,
        ResCurrencyRate.currency_id == payload.currency_id,
        ResCurrencyRate.effective_date == payload.effective_date,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        existing.rate = quantize_db(D(payload.rate))
        rate_row = existing
    else:
        rate_row = ResCurrencyRate(
            company_id=ctx.company_id,
            currency_id=payload.currency_id,
            effective_date=payload.effective_date,
            rate=quantize_db(D(payload.rate)),
        )
        session.add(rate_row)
    await session.flush()
    return {
        "id": rate_row.id,
        "currency_id": rate_row.currency_id,
        "effective_date": rate_row.effective_date.isoformat(),
        "rate": str(rate_row.rate),
    }


# --- Partners / Contacts ---

@router.get("/partners")
async def list_partners(
    role: str | None = Query(default=None, description="customer | supplier"),
    search: str | None = Query(default=None),
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(ResPartner).where(
        ResPartner.company_id == ctx.company_id,
        ResPartner.active.is_(True),
    )
    if role == "customer":
        stmt = stmt.where(ResPartner.is_customer.is_(True))
    elif role == "supplier":
        stmt = stmt.where(ResPartner.is_supplier.is_(True))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(ResPartner.name.ilike(like) | ResPartner.ref.ilike(like))
    stmt = stmt.order_by(ResPartner.name.asc())
    partners = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "name_ar": p.name_ar,
            "ref": p.ref,
            "email": p.email,
            "phone": p.phone,
            "vat": p.vat,
            "address": p.address,
            "is_customer": p.is_customer,
            "is_supplier": p.is_supplier,
        }
        for p in partners
    ]


@router.post("/partners", status_code=201)
async def create_partner(
    payload: PartnerCreate,
    ctx: RequestContext = Depends(require_permission("res_partner", "create")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    partner = ResPartner(company_id=ctx.company_id, **payload.model_dump())
    session.add(partner)
    await session.flush()
    await log_chatter(
        session,
        company_id=ctx.company_id,
        res_model="res_partner",
        res_id=partner.id,
        body=f"Partner '{partner.name}' created",
        author_name=ctx.user.full_name,
        author_id=ctx.user.id,
    )
    return {
        "id": partner.id,
        "name": partner.name,
        "name_ar": partner.name_ar,
        "ref": partner.ref,
        "email": partner.email,
        "phone": partner.phone,
        "vat": partner.vat,
        "address": partner.address,
        "is_customer": partner.is_customer,
        "is_supplier": partner.is_supplier,
    }


# --- Sequences ---

@router.get("/sequences")
async def list_sequences(
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = (
        select(IrSequence)
        .where(IrSequence.company_id == ctx.company_id)
        .order_by(IrSequence.code.asc())
    )
    seqs = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": s.id,
            "code": s.code,
            "name": s.name,
            "prefix": s.prefix,
            "suffix": s.suffix,
            "padding": s.padding,
            "number_next": s.number_next,
            "preview_next": s.format_number(s.number_next),
        }
        for s in seqs
    ]


# --- Audit Chatter ---

@router.get("/chatter/{res_model}/{res_id}")
async def list_chatter(
    res_model: str,
    res_id: int,
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return await get_document_chatter(
        session, company_id=ctx.company_id, res_model=res_model, res_id=res_id
    )


@router.post("/chatter/{res_model}/{res_id}", status_code=201)
async def post_chatter_comment(
    res_model: str,
    res_id: int,
    payload: ChatterCommentCreate,
    ctx: RequestContext = Depends(get_current_context),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    msg = await log_chatter(
        session,
        company_id=ctx.company_id,
        res_model=res_model,
        res_id=res_id,
        body=payload.body,
        message_type="comment",
        author_name=ctx.user.full_name,
        author_id=ctx.user.id,
    )
    return {
        "id": msg.id,
        "res_model": msg.res_model,
        "res_id": msg.res_id,
        "message_type": msg.message_type,
        "body": msg.body,
        "author_name": msg.author_name,
        "created_at": msg.created_at.isoformat(),
        "tracking_values": [],
    }
