"""Nizamo ERP v2.0 Enterprise Integration, RBAC, E-Invoicing, Bank Recon, Closing & FX Test Suite."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.core.database as db_mod
from backend.core.config import settings
from backend.core.database import Base, get_db
from backend.core.decimal_utils import D
from backend.core.registry import registry
from backend.core.sequences import IrSequence, next_sequence_by_code
from backend.core.tenancy import ResCompany, ResCurrency, ResCurrencyRate, ResPartner
from backend.main import create_app
from backend.modules.accounting.invoice_pdf import decode_zatca_tlv_base64
from backend.modules.accounting.models import AccountAccount, AccountJournal, AccountMove
from backend.modules.accounting.posting_engine import InvoiceLineInput, create_invoice_move, post_move
from scripts.seed_erp import seed_erp


@pytest_asyncio.fixture
async def v2_env(tmp_path):
    """Spin up an isolated seeded ERP instance with real FastAPI ASGI transport."""
    registry.load_all()
    db_file = tmp_path / "nizamo_v2_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    orig_factory = db_mod.AsyncSessionLocal
    orig_engine = db_mod.engine

    await seed_erp(database_url=db_url)

    engine, factory = db_mod.configure_database(db_url)
    app = create_app()

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, factory

    db_mod.AsyncSessionLocal = orig_factory
    db_mod.engine = orig_engine
    await engine.dispose()


async def _login(client: AsyncClient, login: str, password: str) -> dict:
    r = await client.post("/api/v1/core/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_jwt_access_refresh_and_strict_production_auth(v2_env):
    client, _ = v2_env
    auth = await _login(client, "admin", "admin123")
    assert "access_token" in auth and "refresh_token" in auth

    # Refresh token yields a valid new access token
    ref = await client.post(
        "/api/v1/core/auth/refresh",
        json={"refresh_token": auth["refresh_token"]},
    )
    assert ref.status_code == 200, ref.text
    new_access = ref.json()["access_token"]

    me = await client.get(
        "/api/v1/core/auth/me",
        headers={"Authorization": f"Bearer {new_access}"},
    )
    assert me.status_code == 200
    assert me.json()["login"] == "admin"

    # Using refresh token directly as Bearer token must be rejected
    bad = await client.get(
        "/api/v1/core/auth/me",
        headers={"Authorization": f"Bearer {auth['refresh_token']}"},
    )
    assert bad.status_code == 401

    # With ALLOW_DEMO_FALLBACK=False, unauthenticated calls MUST fail with 401
    prev = settings.ALLOW_DEMO_FALLBACK
    settings.ALLOW_DEMO_FALLBACK = False
    try:
        unauth = await client.get("/api/v1/core/auth/me")
        assert unauth.status_code == 401
    finally:
        settings.ALLOW_DEMO_FALLBACK = prev


@pytest.mark.asyncio
async def test_rbac_and_company_isolation_enforced(v2_env):
    client, factory = v2_env
    sales_auth = await _login(client, "sales", "sales123")
    headers = {"Authorization": f"Bearer {sales_auth['access_token']}"}

    # Sales user cannot create CoA accounts (Accountant/Admin only)
    res = await client.post(
        "/api/v1/accounting/accounts",
        headers=headers,
        json={"code": "599999", "name": "Unauthorized Account", "account_type": "expense"},
    )
    assert res.status_code == 403

    # Sales user only sees their allowed company (Company 1) and gets 403 on Company 2
    comps = (await client.get("/api/v1/core/companies", headers=headers)).json()
    assert len(comps) == 1
    assert comps[0]["code"] == "NIZAMO"

    async with factory() as s:
        c2 = (await s.execute(select(ResCompany).where(ResCompany.code == "NIZAMO-MENA"))).scalar_one()

    cross = await client.get(
        "/api/v1/accounting/moves",
        headers={**headers, "X-Company-Id": str(c2.id)},
    )
    assert cross.status_code == 403


@pytest.mark.asyncio
async def test_draft_move_editing_and_posted_immutability_plus_zatca_pdf(v2_env):
    client, factory = v2_env
    admin = await _login(client, "admin", "admin123")
    headers = {"Authorization": f"Bearer {admin['access_token']}"}

    async with factory() as s:
        comp = (await s.execute(select(ResCompany).where(ResCompany.code == "NIZAMO"))).scalar_one()
        sale_j = (
            await s.execute(
                select(AccountJournal).where(
                    AccountJournal.company_id == comp.id, AccountJournal.journal_type == "SALE"
                )
            )
        ).scalar_one()
        rev_acc = (
            await s.execute(
                select(AccountAccount).where(
                    AccountAccount.company_id == comp.id, AccountAccount.code == "400000"
                )
            )
        ).scalar_one()
        partner = (
            await s.execute(
                select(ResPartner).where(ResPartner.company_id == comp.id, ResPartner.is_customer.is_(True))
            )
        ).scalars().first()

    # 1. Create Draft Invoice via API
    create_res = await client.post(
        "/api/v1/accounting/moves",
        headers=headers,
        json={
            "move_type": "out_invoice",
            "journal_id": sale_j.id,
            "partner_id": partner.id,
            "date": "2026-11-10",
            "ref": "DRAFT-INIT",
            "invoice_lines": [
                {
                    "account_id": rev_acc.id,
                    "name": "Enterprise ERP License",
                    "quantity": "1",
                    "price_unit": "1000.00",
                }
            ],
        },
    )
    assert create_res.status_code == 201, create_res.text
    move_id = create_res.json()["id"]

    # 2. Edit Draft Invoice via PUT
    upd = await client.put(
        f"/api/v1/accounting/moves/{move_id}",
        headers=headers,
        json={
            "ref": "DRAFT-UPDATED",
            "invoice_lines": [
                {
                    "account_id": rev_acc.id,
                    "name": "Enterprise ERP License (Upgraded)",
                    "quantity": "2",
                    "price_unit": "1000.00",
                }
            ],
        },
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["ref"] == "DRAFT-UPDATED"
    assert D(upd.json()["amount_total"]) == D("2000.00")

    # Verify Chatter logged the field diff
    chatter = (
        await client.get(f"/api/v1/core/chatter/account_move/{move_id}", headers=headers)
    ).json()
    assert any("DRAFT-UPDATED" in str(m) for m in chatter)

    # 3. Post Invoice -> Editing must now be rejected (422 PostedImmutabilityError)!
    posted = await client.post(f"/api/v1/accounting/moves/{move_id}/post", headers=headers)
    assert posted.status_code == 200
    blocked = await client.put(
        f"/api/v1/accounting/moves/{move_id}",
        headers=headers,
        json={"ref": "ILLEGAL-EDIT"},
    )
    assert blocked.status_code == 422

    # 4. Download Official A4 Invoice PDF with ZATCA Phase-1 Base64 TLV QR Code
    pdf_res = await client.get(f"/api/v1/accounting/moves/{move_id}/pdf", headers=headers)
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert pdf_res.content[:5] == b"%PDF-"
    qr_b64 = pdf_res.headers["x-zatca-qr-base64"]
    tlv = decode_zatca_tlv_base64(qr_b64)
    assert tlv[1] == comp.name
    assert D(tlv[4]) == D("2000.00")


@pytest.mark.asyncio
async def test_bank_statement_csv_import_automatch_and_reconcile(v2_env):
    client, factory = v2_env
    admin = await _login(client, "admin", "admin123")
    headers = {"Authorization": f"Bearer {admin['access_token']}"}

    async with factory() as s:
        comp = (await s.execute(select(ResCompany).where(ResCompany.code == "NIZAMO"))).scalar_one()
        bank_j = (
            await s.execute(
                select(AccountJournal).where(
                    AccountJournal.company_id == comp.id, AccountJournal.journal_type == "BANK"
                )
            )
        ).scalar_one()
        sale_j = (
            await s.execute(
                select(AccountJournal).where(
                    AccountJournal.company_id == comp.id, AccountJournal.journal_type == "SALE"
                )
            )
        ).scalar_one()
        rev_acc = (
            await s.execute(
                select(AccountAccount).where(
                    AccountAccount.company_id == comp.id, AccountAccount.code == "400000"
                )
            )
        ).scalar_one()
        exp_acc = (
            await s.execute(
                select(AccountAccount).where(
                    AccountAccount.company_id == comp.id, AccountAccount.code == "520000"
                )
            )
        ).scalar_one()
        partner = (
            await s.execute(
                select(ResPartner).where(ResPartner.company_id == comp.id, ResPartner.is_customer.is_(True))
            )
        ).scalars().first()
        inv = await create_invoice_move(
            s,
            company_id=comp.id,
            move_type="out_invoice",
            journal_id=sale_j.id,
            partner_id=partner.id,
            invoice_date=date(2026, 11, 15),
            invoice_date_due=date(2026, 12, 15),
            invoice_lines=[InvoiceLineInput(account_id=rev_acc.id, name="Consulting", quantity=D("1"), price_unit=D("777.00"))],
        )
        await post_move(s, inv.id)
        await s.commit()
        inv_id = inv.id
        inv_name = inv.name

    csv_feed = f"""date,ref,partner,amount
2026-11-16,WIRE {inv_name},{partner.name},777.00
2026-11-17,MONTHLY-BANK-FEE,First Bank,-25.00
"""
    imp = await client.post(
        "/api/v1/accounting/bank-statements/import-csv",
        headers=headers,
        json={"journal_id": bank_j.id, "name": "BNK/2026/11", "csv_text": csv_feed},
    )
    assert imp.status_code == 201, imp.text
    stmt = imp.json()
    assert stmt["total_lines"] == 2
    inflow_line = stmt["lines"][0]
    fee_line = stmt["lines"][1]
    assert any(s["move_id"] == inv_id and s["confidence"] >= 80 for s in inflow_line["suggestions"])

    # One-click reconcile inflow line to open invoice
    rec1 = await client.post(
        f"/api/v1/accounting/bank-statements/lines/{inflow_line['id']}/reconcile",
        headers=headers,
        json={"invoice_move_id": inv_id},
    )
    assert rec1.status_code == 200, rec1.text

    # Reconcile bank fee line directly to operating expense account
    rec2 = await client.post(
        f"/api/v1/accounting/bank-statements/lines/{fee_line['id']}/reconcile",
        headers=headers,
        json={"counterpart_account_id": exp_acc.id},
    )
    assert rec2.status_code == 200, rec2.text
    assert rec2.json()["statement_state"] == "POSTED"

    # Verify invoice is now PAID with 0 residual
    inv_after = (await client.get(f"/api/v1/accounting/moves/{inv_id}", headers=headers)).json()
    assert inv_after["payment_state"] == "PAID"
    assert D(inv_after["amount_residual"]) == D("0")


@pytest.mark.asyncio
async def test_unrealized_fx_revaluation_and_fiscal_year_closing_wizard(v2_env):
    client, factory = v2_env
    admin = await _login(client, "admin", "admin123")
    headers = {"Authorization": f"Bearer {admin['access_token']}"}

    async with factory() as s:
        comp = (await s.execute(select(ResCompany).where(ResCompany.code == "NIZAMO"))).scalar_one()
        eur = (await s.execute(select(ResCurrency).where(ResCurrency.code == "EUR"))).scalar_one()
        sale_j = (
            await s.execute(
                select(AccountJournal).where(
                    AccountJournal.company_id == comp.id, AccountJournal.journal_type == "SALE"
                )
            )
        ).scalar_one()
        rev_acc = (
            await s.execute(
                select(AccountAccount).where(
                    AccountAccount.company_id == comp.id, AccountAccount.code == "400000"
                )
            )
        ).scalar_one()
        partner = (
            await s.execute(
                select(ResPartner).where(ResPartner.company_id == comp.id, ResPartner.is_customer.is_(True))
            )
        ).scalars().first()
        # Create an open EUR invoice at existing Nov rate, then revalue at higher Dec 31 closing rate
        fx_inv = await create_invoice_move(
            s,
            company_id=comp.id,
            move_type="out_invoice",
            journal_id=sale_j.id,
            partner_id=partner.id,
            currency_id=eur.id,
            invoice_date=date(2026, 11, 20),
            invoice_date_due=date(2026, 12, 31),
            invoice_lines=[
                InvoiceLineInput(account_id=rev_acc.id, name="EUR Export", quantity=D("1"), price_unit=D("1000.00"))
            ],
        )
        await post_move(s, fx_inv.id)
        # Add stronger EUR closing rate on 2026-12-31
        s.add(
            ResCurrencyRate(
                company_id=comp.id,
                currency_id=eur.id,
                effective_date=date(2026, 12, 31),
                rate=D("1.250000"),
            )
        )
        await s.commit()

    # 1. Preview & Post Unrealized FX Revaluation (IAS 21) with auto-reversal
    fx_prev = (
        await client.get(
            "/api/v1/accounting/fx-revaluation/preview?revaluation_date=2026-12-31",
            headers=headers,
        )
    ).json()
    assert len(fx_prev["exposures"]) >= 1
    assert D(fx_prev["net_unrealized_fx"]) > D("0")

    fx_exec = await client.post(
        "/api/v1/accounting/fx-revaluation",
        headers=headers,
        json={"revaluation_date": "2026-12-31", "auto_reverse_next_day": True},
    )
    assert fx_exec.status_code == 201, fx_exec.text
    assert fx_exec.json()["reversal_move_id"] is not None

    # 2. Preview & Execute Year-End Fiscal Closing Wizard
    cl_prev = (
        await client.get(
            "/api/v1/accounting/fiscal-closing/preview?date_from=2026-01-01&date_to=2026-12-31",
            headers=headers,
        )
    ).json()
    assert len(cl_prev["accounts"]) >= 1

    cl_exec = await client.post(
        "/api/v1/accounting/fiscal-closing",
        headers=headers,
        json={"date_from": "2026-01-01", "date_to": "2026-12-31", "lock_fiscal_year": True},
    )
    assert cl_exec.status_code == 201, cl_exec.text
    assert cl_exec.json()["fiscalyear_lock_date"] == "2026-12-31"

    # After closing, P&L accounts for 2026 must all be zeroed!
    cl_after = (
        await client.get(
            "/api/v1/accounting/fiscal-closing/preview?date_from=2026-01-01&date_to=2026-12-31",
            headers=headers,
        )
    ).json()
    assert cl_after["accounts"] == []
    assert D(cl_after["net_profit"]) == D("0")

    # Posting a backdated move in 2026 must now be blocked by FiscalLockDateError (422)
    async with factory() as s:
        comp = (await s.execute(select(ResCompany).where(ResCompany.code == "NIZAMO"))).scalar_one()
        sale_j = (
            await s.execute(
                select(AccountJournal).where(
                    AccountJournal.company_id == comp.id, AccountJournal.journal_type == "SALE"
                )
            )
        ).scalar_one()
        rev_acc = (
            await s.execute(
                select(AccountAccount).where(
                    AccountAccount.company_id == comp.id, AccountAccount.code == "400000"
                )
            )
        ).scalar_one()
        partner = (
            await s.execute(
                select(ResPartner).where(ResPartner.company_id == comp.id, ResPartner.is_customer.is_(True))
            )
        ).scalars().first()

    backdated = await client.post(
        "/api/v1/accounting/moves",
        headers=headers,
        json={
            "move_type": "out_invoice",
            "journal_id": sale_j.id,
            "partner_id": partner.id,
            "date": "2026-10-01",
            "invoice_lines": [
                {"account_id": rev_acc.id, "name": "Late Entry", "quantity": "1", "price_unit": "50.00"}
            ],
        },
    )
    assert backdated.status_code == 201
    post_late = await client.post(
        f"/api/v1/accounting/moves/{backdated.json()['id']}/post", headers=headers
    )
    assert post_late.status_code == 422


@pytest.mark.asyncio
async def test_concurrent_atomic_sequences_zero_gaps_and_no_duplicates(v2_env):
    _, factory = v2_env

    async with factory() as s:
        comp = (await s.execute(select(ResCompany).where(ResCompany.code == "NIZAMO"))).scalar_one()
        seq = IrSequence(
            company_id=comp.id,
            code="concurrent.test.seq",
            name="Concurrent Test Sequence",
            prefix="CONC/2026/",
            padding=5,
            number_next=1,
            number_increment=1,
        )
        s.add(seq)
        await s.commit()
        comp_id = comp.id

    async def grab_one() -> str:
        async with factory() as session:
            val = await next_sequence_by_code(
                session, company_id=comp_id, code="concurrent.test.seq"
            )
            await session.commit()
            return val

    results = await asyncio.gather(*[grab_one() for _ in range(10)])
    assert len(set(results)) == 10
    assert sorted(results) == [f"CONC/2026/{i:05d}" for i in range(1, 11)]
