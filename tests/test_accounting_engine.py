"""Comprehensive pytest suite verifying Double-Entry Accounting, FX, Tax, Inventory & ERP Invariants."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.core.tenancy  # noqa: F401
import backend.core.sequences  # noqa: F401
import backend.core.security  # noqa: F401
import backend.core.chatter  # noqa: F401
from backend.core.chatter import get_document_chatter
from backend.core.database import Base
from backend.core.decimal_utils import D, ZERO
from backend.core.registry import registry
from backend.core.tenancy import ResCompany, ResCurrency, ResCurrencyRate, ResPartner

registry.load_all()

from backend.modules.accounting.models import (  # noqa: E402
    ACCOUNT_TYPE_ASSET_CASH,
    ACCOUNT_TYPE_ASSET_CURRENT,
    ACCOUNT_TYPE_ASSET_RECEIVABLE,
    ACCOUNT_TYPE_EQUITY,
    ACCOUNT_TYPE_EXPENSE,
    ACCOUNT_TYPE_EXPENSE_COGS,
    ACCOUNT_TYPE_INCOME,
    ACCOUNT_TYPE_INCOME_OTHER,
    ACCOUNT_TYPE_LIABILITY_CURRENT,
    ACCOUNT_TYPE_LIABILITY_PAYABLE,
    AccountAccount,
    AccountJournal,
    AccountMove,
    AccountMoveLine,
    AccountTax,
)
from backend.modules.accounting.posting_engine import (  # noqa: E402
    FiscalLockDateError,
    InvoiceLineInput,
    JournalLineInput,
    PostedImmutabilityError,
    UnbalancedMoveError,
    cancel_move,
    create_invoice_move,
    create_journal_entry,
    delete_move_line_protected,
    get_move_with_lines,
    post_move,
    reverse_move,
    update_move_line_protected,
)
from backend.modules.accounting.reconciliation import register_payment  # noqa: E402
from backend.modules.accounting.reports import (  # noqa: E402
    export_report_csv,
    export_report_pdf,
    export_report_xlsx,
    generate_aged_partner_balance,
    generate_balance_sheet,
    generate_general_ledger,
    generate_profit_and_loss,
    generate_trial_balance,
)
from backend.modules.accounting.tax_engine import compute_line_taxes  # noqa: E402
from backend.modules.inventory.models import (  # noqa: E402
    ProductCategory,
    ProductTemplate,
    StockLocation,
    StockQuant,
    StockValuationLayer,
)
from backend.modules.inventory.valuation_engine import (  # noqa: E402
    create_picking,
    get_internal_on_hand,
    validate_picking,
)
from backend.modules.purchase.workflows import (  # noqa: E402
    ThreeWayMatchError,
    confirm_purchase_order,
    create_bill_from_purchase_order,
    create_purchase_order,
)
from backend.modules.sales.workflows import (  # noqa: E402
    confirm_sale_order,
    create_invoice_from_sale_order,
    create_sale_order,
)


@pytest_asyncio.fixture
async def erp_env():
    """Fresh isolated in-memory async database fixture seeded with standard IFRS/GAAP Chart of Accounts."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        usd = ResCurrency(code="USD", name="US Dollar", symbol="$", decimal_places=2)
        eur = ResCurrency(code="EUR", name="Euro", symbol="€", decimal_places=2)
        session.add_all([usd, eur])
        await session.flush()

        company = ResCompany(code="TESTCO", name="Test ERP Corp", currency_id=usd.id)
        session.add(company)
        await session.flush()

        # Exchange rates: EUR starts at 1.10 USD on Jan 1, rises to 1.20 on Jan 15, drops to 1.05 on Jan 25
        session.add_all(
            [
                ResCurrencyRate(company_id=company.id, currency_id=usd.id, effective_date=date(2026, 1, 1), rate=D("1.000000")),
                ResCurrencyRate(company_id=company.id, currency_id=eur.id, effective_date=date(2026, 1, 1), rate=D("1.100000")),
                ResCurrencyRate(company_id=company.id, currency_id=eur.id, effective_date=date(2026, 1, 15), rate=D("1.200000")),
                ResCurrencyRate(company_id=company.id, currency_id=eur.id, effective_date=date(2026, 1, 25), rate=D("1.050000")),
            ]
        )

        # Chart of Accounts
        accts: dict[str, AccountAccount] = {}
        for code, name, atype, rec in [
            ("102000", "Main Bank", ACCOUNT_TYPE_ASSET_CASH, False),
            ("110000", "Accounts Receivable", ACCOUNT_TYPE_ASSET_RECEIVABLE, True),
            ("120000", "Inventory Valuation", ACCOUNT_TYPE_ASSET_CURRENT, False),
            ("135000", "Input VAT", ACCOUNT_TYPE_ASSET_CURRENT, False),
            ("200000", "Accounts Payable", ACCOUNT_TYPE_LIABILITY_PAYABLE, True),
            ("215000", "Stock Interim Received", ACCOUNT_TYPE_LIABILITY_CURRENT, False),
            ("220000", "Output VAT", ACCOUNT_TYPE_LIABILITY_CURRENT, False),
            ("300000", "Share Capital", ACCOUNT_TYPE_EQUITY, False),
            ("400000", "Sales Revenue", ACCOUNT_TYPE_INCOME, False),
            ("490000", "Foreign Exchange Gain", ACCOUNT_TYPE_INCOME_OTHER, False),
            ("500000", "Cost of Goods Sold", ACCOUNT_TYPE_EXPENSE_COGS, False),
            ("510000", "Operating Expense", ACCOUNT_TYPE_EXPENSE, False),
            ("590000", "Foreign Exchange Loss", ACCOUNT_TYPE_EXPENSE, False),
        ]:
            a = AccountAccount(
                company_id=company.id, code=code, name=name, account_type=atype, reconcilable=rec
            )
            session.add(a)
            accts[code] = a
        await session.flush()

        # Journals
        journals: dict[str, AccountJournal] = {}
        for code, name, jtype, def_acct in [
            ("INV", "Sales Journal", "SALE", "400000"),
            ("BILL", "Purchase Journal", "PURCHASE", "500000"),
            ("BNK1", "Bank Journal", "BANK", "102000"),
            ("MISC", "General Journal", "GENERAL", None),
            ("STJ", "Stock Journal", "GENERAL", "120000"),
        ]:
            j = AccountJournal(
                company_id=company.id,
                code=code,
                name=name,
                journal_type=jtype,
                default_account_id=accts[def_acct].id if def_acct else None,
                currency_id=usd.id,
            )
            session.add(j)
            journals[code] = j
        await session.flush()

        # Taxes
        vat15 = AccountTax(
            company_id=company.id,
            name="15% VAT",
            tax_type="SALE",
            amount_type="PERCENT",
            amount=D("15"),
            price_include=False,
            include_base_amount=False,
            sequence=10,
            tax_account_id=accts["220000"].id,
        )
        vat15_in = AccountTax(
            company_id=company.id,
            name="15% Purchase VAT",
            tax_type="PURCHASE",
            amount_type="PERCENT",
            amount=D("15"),
            price_include=False,
            include_base_amount=False,
            sequence=10,
            tax_account_id=accts["135000"].id,
        )
        session.add_all([vat15, vat15_in])

        # Locations
        loc_stock = StockLocation(company_id=company.id, name="Stock", complete_name="WH/Stock", usage="INTERNAL")
        loc_cust = StockLocation(company_id=company.id, name="Customers", complete_name="Partner Locations/Customers", usage="CUSTOMER")
        loc_vend = StockLocation(company_id=company.id, name="Vendors", complete_name="Partner Locations/Vendors", usage="SUPPLIER")
        session.add_all([loc_stock, loc_cust, loc_vend])

        # Categories
        cat_fifo = ProductCategory(
            company_id=company.id,
            name="FIFO Goods",
            costing_method="FIFO",
            valuation="REAL_TIME",
            stock_valuation_account_id=accts["120000"].id,
            stock_input_account_id=accts["215000"].id,
            stock_output_account_id=accts["500000"].id,
            income_account_id=accts["400000"].id,
            expense_account_id=accts["500000"].id,
        )
        cat_avco = ProductCategory(
            company_id=company.id,
            name="AVCO Goods",
            costing_method="AVCO",
            valuation="REAL_TIME",
            stock_valuation_account_id=accts["120000"].id,
            stock_input_account_id=accts["215000"].id,
            stock_output_account_id=accts["500000"].id,
            income_account_id=accts["400000"].id,
            expense_account_id=accts["500000"].id,
        )
        session.add_all([cat_fifo, cat_avco])
        await session.flush()

        p_fifo = ProductTemplate(
            company_id=company.id,
            default_code="SKU-FIFO-01",
            name="Industrial Server (FIFO)",
            product_type="STORABLE",
            category_id=cat_fifo.id,
            list_price=D("500.00"),
            standard_price=D("100.00"),
        )
        p_avco = ProductTemplate(
            company_id=company.id,
            default_code="SKU-AVCO-01",
            name="Workstation (AVCO)",
            product_type="STORABLE",
            category_id=cat_avco.id,
            list_price=D("300.00"),
            standard_price=D("100.00"),
        )
        customer = ResPartner(company_id=company.id, name="Acme Customer", is_customer=True)
        vendor = ResPartner(company_id=company.id, name="Global Supplier", is_supplier=True)
        session.add_all([p_fifo, p_avco, customer, vendor])
        await session.flush()

        yield {
            "session": session,
            "company": company,
            "usd": usd,
            "eur": eur,
            "accts": accts,
            "journals": journals,
            "taxes": {"vat15": vat15, "vat15_in": vat15_in},
            "locations": {"stock": loc_stock, "customer": loc_cust, "vendor": loc_vend},
            "products": {"fifo": p_fifo, "avco": p_avco},
            "partners": {"customer": customer, "vendor": vendor},
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_module_registry_topological_order():
    """Verify Odoo-inspired __manifest__.py dependency resolution loads accounting -> inventory -> sales/purchase."""
    order = registry.resolve_dependency_order()
    assert order.index("accounting") < order.index("inventory")
    assert order.index("inventory") < order.index("sales")
    assert order.index("inventory") < order.index("purchase")


@pytest.mark.asyncio
async def test_reject_unbalanced_journal_entry(erp_env):
    """Invariant 1: Strictly reject posting any journal entry where Sum(debit) != Sum(credit)."""
    s = erp_env["session"]
    co = erp_env["company"]
    accts = erp_env["accts"]
    journals = erp_env["journals"]

    unbalanced = await create_journal_entry(
        s,
        company_id=co.id,
        journal_id=journals["MISC"].id,
        move_date=date(2026, 2, 1),
        ref="Bad Entry",
        lines=[
            JournalLineInput(account_id=accts["102000"].id, name="Cash In", debit=D("1000.00"), credit=ZERO),
            JournalLineInput(account_id=accts["300000"].id, name="Capital", debit=ZERO, credit=D("999.99")),
        ],
    )
    with pytest.raises(UnbalancedMoveError):
        await post_move(s, unbalanced.id)

    refreshed = await get_move_with_lines(s, unbalanced.id)
    assert refreshed.state == "DRAFT"


@pytest.mark.asyncio
async def test_posted_immutability_and_automated_reversal(erp_env):
    """Invariant 2: Prevent modifying/deleting/cancelling POSTED lines; verify Reversal Entry workflow."""
    s = erp_env["session"]
    co = erp_env["company"]
    accts = erp_env["accts"]
    journals = erp_env["journals"]

    move = await create_journal_entry(
        s,
        company_id=co.id,
        journal_id=journals["MISC"].id,
        move_date=date(2026, 2, 5),
        ref="Rent Entry",
        lines=[
            JournalLineInput(account_id=accts["510000"].id, name="Rent", debit=D("2500.00"), credit=ZERO),
            JournalLineInput(account_id=accts["102000"].id, name="Bank", debit=ZERO, credit=D("2500.00")),
        ],
    )
    posted = await post_move(s, move.id, author_name="Auditor")
    assert posted.state == "POSTED"
    assert posted.name.startswith("JE/2026/")

    # Immutability guards: editing, deleting, or cancelling a posted entry MUST fail
    with pytest.raises(PostedImmutabilityError):
        await update_move_line_protected(s, line_id=posted.lines[0].id, debit=D("3000.00"))
    with pytest.raises(PostedImmutabilityError):
        await delete_move_line_protected(s, line_id=posted.lines[0].id)
    with pytest.raises(PostedImmutabilityError):
        await cancel_move(s, posted.id)

    # Automated Reversal Entry must flip debits/credits and zero out net account impact
    rev = await reverse_move(s, posted.id, reversal_date=date(2026, 2, 6), reason="Booked in wrong period")
    assert rev.state == "POSTED"
    assert rev.reversed_entry_id == posted.id

    tb = await generate_trial_balance(s, company_id=co.id, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    for row in tb["rows"]:
        assert D(row["closing_balance"]) == ZERO

    # Verify Audit Chatter recorded state transitions
    chatter = await get_document_chatter(s, company_id=co.id, res_model="account_move", res_id=posted.id)
    assert any(m["message_type"] == "state_change" for m in chatter)


@pytest.mark.asyncio
async def test_fiscal_and_tax_lock_dates(erp_env):
    """Invariant 3: Block posting entries dated on or before fiscalyear_lock_date or tax_lock_date."""
    s = erp_env["session"]
    co = erp_env["company"]
    accts = erp_env["accts"]
    journals = erp_env["journals"]

    co.fiscalyear_lock_date = date(2026, 1, 31)
    await s.flush()

    locked_move = await create_journal_entry(
        s,
        company_id=co.id,
        journal_id=journals["MISC"].id,
        move_date=date(2026, 1, 31),
        lines=[
            JournalLineInput(account_id=accts["102000"].id, debit=D("500.00"), credit=ZERO),
            JournalLineInput(account_id=accts["300000"].id, debit=ZERO, credit=D("500.00")),
        ],
    )
    with pytest.raises(FiscalLockDateError):
        await post_move(s, locked_move.id)


@pytest.mark.asyncio
async def test_tax_engine_percent_fixed_included_and_compound(erp_env):
    """Verify pure Decimal tax calculations: percent, fixed, price-included, and cascading/compound."""
    accts = erp_env["accts"]
    co = erp_env["company"]

    # Compound tax: 10% Excise (include_base_amount=True) followed by 15% VAT on (base + excise)
    t_excise = AccountTax(
        id=1, company_id=co.id, name="10% Excise", amount_type="PERCENT", amount=D("10"),
        price_include=False, include_base_amount=True, sequence=10, tax_account_id=accts["220000"].id
    )
    t_vat = AccountTax(
        id=2, company_id=co.id, name="15% VAT", amount_type="PERCENT", amount=D("15"),
        price_include=False, include_base_amount=False, sequence=20, tax_account_id=accts["220000"].id
    )
    res_compound = compute_line_taxes(
        price_unit=D("100.00"), quantity=D("2"), taxes=[t_vat, t_excise], currency_precision=2
    )
    # Base = 200.00 -> Excise 10% = 20.00 -> New Base = 220.00 -> VAT 15% = 33.00 -> Total = 253.00
    assert res_compound.total_excluded == D("200.00")
    assert res_compound.total_tax == D("53.00")
    assert res_compound.total_included == D("253.00")

    # Tax-Included: 115.00 with 15% VAT included -> Base = 100.00, Tax = 15.00
    t_inc = AccountTax(
        id=3, company_id=co.id, name="15% VAT Inc", amount_type="PERCENT", amount=D("15"),
        price_include=True, include_base_amount=False, sequence=10, tax_account_id=accts["220000"].id
    )
    res_inc = compute_line_taxes(price_unit=D("115.00"), quantity=D("1"), taxes=[t_inc], currency_precision=2)
    assert res_inc.total_excluded == D("100.00")
    assert res_inc.total_tax == D("15.00")
    assert res_inc.total_included == D("115.00")

    # Fixed tax: $2.50/unit * 4 units = $10.00
    t_fixed = AccountTax(
        id=4, company_id=co.id, name="$2.50 Eco Fee", amount_type="FIXED", amount=D("2.50"),
        price_include=False, include_base_amount=False, sequence=10, tax_account_id=accts["220000"].id
    )
    res_fix = compute_line_taxes(price_unit=D("50.00"), quantity=D("4"), taxes=[t_fixed], currency_precision=2)
    assert res_fix.total_excluded == D("200.00")
    assert res_fix.total_tax == D("10.00")
    assert res_fix.total_included == D("210.00")


@pytest.mark.asyncio
async def test_partial_payment_reconciliation_and_residual_tracking(erp_env):
    """Verify NOT_PAID -> PARTIAL -> PAID state transitions and exact residual balance tracking."""
    s = erp_env["session"]
    co = erp_env["company"]
    accts = erp_env["accts"]
    journals = erp_env["journals"]
    customer = erp_env["partners"]["customer"]
    vat15 = erp_env["taxes"]["vat15"]

    inv = await create_invoice_move(
        s,
        company_id=co.id,
        move_type="out_invoice",
        journal_id=journals["INV"].id,
        partner_id=customer.id,
        invoice_date=date(2026, 3, 1),
        invoice_date_due=date(2026, 3, 31),
        invoice_lines=[
            InvoiceLineInput(
                account_id=accts["400000"].id,
                name="ERP License",
                quantity=D("1"),
                price_unit=D("1000.00"),
                tax_ids=[vat15.id],
            )
        ],
    )
    inv = await post_move(s, inv.id)
    assert inv.amount_untaxed == D("1000.00")
    assert inv.amount_tax == D("150.00")
    assert inv.amount_total == D("1150.00")
    assert inv.amount_residual == D("1150.00")
    assert inv.payment_state == "NOT_PAID"

    # Partial payment #1: $450.00 -> residual = $700.00, state = PARTIAL
    await register_payment(
        s,
        company_id=co.id,
        payment_type="INBOUND",
        partner_type="CUSTOMER",
        partner_id=customer.id,
        journal_id=journals["BNK1"].id,
        amount=D("450.00"),
        payment_date=date(2026, 3, 10),
        invoice_move_id=inv.id,
    )
    inv = await get_move_with_lines(s, inv.id)
    assert inv.amount_residual == D("700.00")
    assert inv.payment_state == "PARTIAL"

    # Final payment #2: $700.00 -> residual = $0.00, state = PAID
    await register_payment(
        s,
        company_id=co.id,
        payment_type="INBOUND",
        partner_type="CUSTOMER",
        partner_id=customer.id,
        journal_id=journals["BNK1"].id,
        amount=D("700.00"),
        payment_date=date(2026, 3, 20),
        invoice_move_id=inv.id,
    )
    inv = await get_move_with_lines(s, inv.id)
    assert inv.amount_residual == ZERO
    assert inv.payment_state == "PAID"


@pytest.mark.asyncio
async def test_foreign_currency_realized_fx_gain_and_loss(erp_env):
    """Verify automatic Realized FX Gain & FX Loss journal entries when payment rate != invoice rate."""
    s = erp_env["session"]
    co = erp_env["company"]
    eur = erp_env["eur"]
    accts = erp_env["accts"]
    journals = erp_env["journals"]
    customer = erp_env["partners"]["customer"]

    # Case A: Realized FX GAIN
    # Invoice on Jan 1: 1,000 EUR @ 1.10 = $1,100.00 USD
    # Payment on Jan 15: 1,000 EUR @ 1.20 = $1,200.00 USD -> FX Gain = $100.00 USD
    inv_gain = await create_invoice_move(
        s,
        company_id=co.id,
        move_type="out_invoice",
        journal_id=journals["INV"].id,
        partner_id=customer.id,
        invoice_date=date(2026, 1, 1),
        invoice_date_due=date(2026, 1, 31),
        currency_id=eur.id,
        invoice_lines=[
            InvoiceLineInput(account_id=accts["400000"].id, name="EUR Export A", quantity=D("1"), price_unit=D("1000.00"))
        ],
    )
    inv_gain = await post_move(s, inv_gain.id)
    _p1, _pm1, partial1 = await register_payment(
        s,
        company_id=co.id,
        payment_type="INBOUND",
        partner_type="CUSTOMER",
        partner_id=customer.id,
        journal_id=journals["BNK1"].id,
        amount=D("1000.00"),
        currency_id=eur.id,
        payment_date=date(2026, 1, 15),
        invoice_move_id=inv_gain.id,
    )
    assert partial1 is not None and partial1.exchange_move_id is not None
    inv_gain = await get_move_with_lines(s, inv_gain.id)
    assert inv_gain.payment_state == "PAID"
    assert inv_gain.amount_residual == ZERO

    fx_gain_move = await get_move_with_lines(s, partial1.exchange_move_id)
    gain_line = next(l for l in fx_gain_move.lines if l.account_id == accts["490000"].id)
    assert gain_line.credit == D("100.00")

    # Case B: Realized FX LOSS
    # Invoice on Jan 1: 1,000 EUR @ 1.10 = $1,100.00 USD
    # Payment on Jan 25: 1,000 EUR @ 1.05 = $1,050.00 USD -> FX Loss = $50.00 USD
    inv_loss = await create_invoice_move(
        s,
        company_id=co.id,
        move_type="out_invoice",
        journal_id=journals["INV"].id,
        partner_id=customer.id,
        invoice_date=date(2026, 1, 1),
        invoice_date_due=date(2026, 1, 31),
        currency_id=eur.id,
        invoice_lines=[
            InvoiceLineInput(account_id=accts["400000"].id, name="EUR Export B", quantity=D("1"), price_unit=D("1000.00"))
        ],
    )
    inv_loss = await post_move(s, inv_loss.id)
    _p2, _pm2, partial2 = await register_payment(
        s,
        company_id=co.id,
        payment_type="INBOUND",
        partner_type="CUSTOMER",
        partner_id=customer.id,
        journal_id=journals["BNK1"].id,
        amount=D("1000.00"),
        currency_id=eur.id,
        payment_date=date(2026, 1, 25),
        invoice_move_id=inv_loss.id,
    )
    assert partial2 is not None and partial2.exchange_move_id is not None
    inv_loss = await get_move_with_lines(s, inv_loss.id)
    assert inv_loss.payment_state == "PAID"

    fx_loss_move = await get_move_with_lines(s, partial2.exchange_move_id)
    loss_line = next(l for l in fx_loss_move.lines if l.account_id == accts["590000"].id)
    assert loss_line.debit == D("50.00")

    # Verify AR balance is exactly 0.00 and Balance Sheet balances!
    bs = await generate_balance_sheet(s, company_id=co.id, date_to=date(2026, 12, 31))
    assert bs["summary"]["is_balanced"] is True


@pytest.mark.asyncio
async def test_fifo_and_avco_inventory_valuation_and_perpetual_accounting(erp_env):
    """Verify FIFO chronological cost consumption, AVCO dynamic average recalculation, unit conservation & GL entries."""
    s = erp_env["session"]
    co = erp_env["company"]
    locs = erp_env["locations"]
    prods = erp_env["products"]
    accts = erp_env["accts"]

    # --- FIFO TEST ---
    # Batch 1: Receive 10 units @ $100 = $1,000
    r1 = await create_picking(
        s, company_id=co.id, picking_type="INCOMING",
        location_id=locs["vendor"].id, location_dest_id=locs["stock"].id,
        scheduled_date=date(2026, 4, 1),
        moves_data=[{"product_id": prods["fifo"].id, "quantity": D("10"), "price_unit": D("100.00")}],
    )
    _, moves_r1 = await validate_picking(s, r1.id)
    assert len(moves_r1) == 1  # Debit Stock Valuation 1000 / Credit Stock Interim 1000

    # Batch 2: Receive 10 units @ $150 = $1,500
    r2 = await create_picking(
        s, company_id=co.id, picking_type="INCOMING",
        location_id=locs["vendor"].id, location_dest_id=locs["stock"].id,
        scheduled_date=date(2026, 4, 5),
        moves_data=[{"product_id": prods["fifo"].id, "quantity": D("10"), "price_unit": D("150.00")}],
    )
    await validate_picking(s, r2.id)
    assert await get_internal_on_hand(s, company_id=co.id, product_id=prods["fifo"].id) == D("20")

    # Deliver 12 units -> FIFO MUST consume 10 @ $100 ($1,000) + 2 @ $150 ($300) = $1,300 COGS!
    d1 = await create_picking(
        s, company_id=co.id, picking_type="OUTGOING",
        location_id=locs["stock"].id, location_dest_id=locs["customer"].id,
        scheduled_date=date(2026, 4, 10),
        moves_data=[{"product_id": prods["fifo"].id, "quantity": D("12")}],
    )
    _, moves_d1 = await validate_picking(s, d1.id)
    assert len(moves_d1) == 1
    cogs_line = next(l for l in moves_d1[0].lines if l.account_id == accts["500000"].id)
    assert cogs_line.debit == D("1300.00")

    # --- AVCO TEST ---
    # Receive 10 units @ $100, then 10 units @ $200 -> AVCO becomes $150.00!
    a1 = await create_picking(
        s, company_id=co.id, picking_type="INCOMING",
        location_id=locs["vendor"].id, location_dest_id=locs["stock"].id,
        scheduled_date=date(2026, 4, 1),
        moves_data=[{"product_id": prods["avco"].id, "quantity": D("10"), "price_unit": D("100.00")}],
    )
    await validate_picking(s, a1.id)
    a2 = await create_picking(
        s, company_id=co.id, picking_type="INCOMING",
        location_id=locs["vendor"].id, location_dest_id=locs["stock"].id,
        scheduled_date=date(2026, 4, 2),
        moves_data=[{"product_id": prods["avco"].id, "quantity": D("10"), "price_unit": D("200.00")}],
    )
    await validate_picking(s, a2.id)

    avco_prod = await s.get(ProductTemplate, prods["avco"].id)
    assert avco_prod is not None and avco_prod.standard_price == D("150.000000")

    # Deliver 4 AVCO units -> COGS = 4 * $150 = $600.00
    d2 = await create_picking(
        s, company_id=co.id, picking_type="OUTGOING",
        location_id=locs["stock"].id, location_dest_id=locs["customer"].id,
        scheduled_date=date(2026, 4, 12),
        moves_data=[{"product_id": prods["avco"].id, "quantity": D("4")}],
    )
    _, moves_d2 = await validate_picking(s, d2.id)
    avco_cogs = next(l for l in moves_d2[0].lines if l.account_id == accts["500000"].id)
    assert avco_cogs.debit == D("600.00")

    # Double-Entry Stock Unit Conservation: sum of all StockQuant across all locations (physical + virtual) == 0!
    total_units = (
        await s.execute(select(func.sum(StockQuant.quantity)).where(StockQuant.company_id == co.id))
    ).scalar_one()
    assert D(total_units) == ZERO


@pytest.mark.asyncio
async def test_end_to_end_p2p_3way_match_o2c_and_balance_sheet_equilibrium(erp_env):
    """End-to-end ERP lifecycle verifying 3-Way Matching, O2C, Balance Sheet equilibrium & CSV/XLSX/PDF exports."""
    s = erp_env["session"]
    co = erp_env["company"]
    accts = erp_env["accts"]
    journals = erp_env["journals"]
    prods = erp_env["products"]
    partners = erp_env["partners"]
    taxes = erp_env["taxes"]

    # 1. Inject Opening Share Capital ($50,000)
    cap = await create_journal_entry(
        s,
        company_id=co.id,
        journal_id=journals["MISC"].id,
        move_date=date(2026, 5, 1),
        lines=[
            JournalLineInput(account_id=accts["102000"].id, name="Capital Deposit", debit=D("50000.00"), credit=ZERO),
            JournalLineInput(account_id=accts["300000"].id, name="Share Capital", debit=ZERO, credit=D("50000.00")),
        ],
    )
    await post_move(s, cap.id)

    # 2. Procure-to-Pay with 3-Way Matching Enforcement
    po = await create_purchase_order(
        s,
        company_id=co.id,
        partner_id=partners["vendor"].id,
        date_order=date(2026, 5, 2),
        lines_data=[
            {
                "product_id": prods["fifo"].id,
                "name": prods["fifo"].name,
                "quantity": D("5"),
                "price_unit": D("200.00"),
                "tax_ids": [taxes["vat15_in"].id],
            }
        ],
    )
    po, receipt = await confirm_purchase_order(s, po.id)
    assert po.name.startswith("PO/2026/")
    assert receipt is not None

    # 3-Way Match MUST block billing before goods are received!
    with pytest.raises(ThreeWayMatchError):
        await create_bill_from_purchase_order(s, po.id, enforce_three_way_match=True)

    # Receive goods -> Now 3-Way Match succeeds!
    await validate_picking(s, receipt.id, validation_date=date(2026, 5, 3))
    bill = await create_bill_from_purchase_order(s, po.id, bill_date=date(2026, 5, 3))
    bill = await post_move(s, bill.id)
    await register_payment(
        s,
        company_id=co.id,
        payment_type="OUTBOUND",
        partner_type="SUPPLIER",
        partner_id=partners["vendor"].id,
        journal_id=journals["BNK1"].id,
        amount=bill.amount_total,
        payment_date=date(2026, 5, 4),
        invoice_move_id=bill.id,
    )

    # 3. Order-to-Cash (SO -> Delivery -> Customer Invoice -> Partial Payment)
    so = await create_sale_order(
        s,
        company_id=co.id,
        partner_id=partners["customer"].id,
        date_order=date(2026, 5, 5),
        invoice_policy="DELIVERED",
        lines_data=[
            {
                "product_id": prods["fifo"].id,
                "name": prods["fifo"].name,
                "quantity": D("3"),
                "price_unit": D("500.00"),
                "tax_ids": [taxes["vat15"].id],
            }
        ],
    )
    so, delivery = await confirm_sale_order(s, so.id)
    assert so.name.startswith("SO/2026/")
    assert delivery is not None
    await validate_picking(s, delivery.id, validation_date=date(2026, 5, 6))

    inv = await create_invoice_from_sale_order(s, so.id, invoice_date=date(2026, 5, 6))
    inv = await post_move(s, inv.id)
    # Partial payment of $1,000 out of $1,725 total
    await register_payment(
        s,
        company_id=co.id,
        payment_type="INBOUND",
        partner_type="CUSTOMER",
        partner_id=partners["customer"].id,
        journal_id=journals["BNK1"].id,
        amount=D("1000.00"),
        payment_date=date(2026, 5, 7),
        invoice_move_id=inv.id,
    )

    # 4. Financial Statements Verification
    bs = await generate_balance_sheet(s, company_id=co.id, date_to=date(2026, 12, 31))
    assert bs["summary"]["is_balanced"] is True
    assert D(bs["summary"]["total_assets"]) == D(bs["summary"]["total_liabilities_and_equity"])
    assert D(bs["summary"]["difference"]) == ZERO

    tb = await generate_trial_balance(s, company_id=co.id, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    assert tb["totals"]["is_balanced"] is True

    pnl = await generate_profit_and_loss(s, company_id=co.id, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    # Revenue = 3 * 500 = 1500; COGS = 3 * 200 = 600 -> Gross & Net Profit = 900.00
    assert D(pnl["summary"]["total_operating_income"]) == D("1500.00")
    assert D(pnl["summary"]["total_cogs"]) == D("600.00")
    assert D(pnl["summary"]["gross_profit"]) == D("900.00")
    assert D(pnl["summary"]["net_profit"]) == D("900.00")

    gl = await generate_general_ledger(s, company_id=co.id, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    assert len(gl["accounts"]) > 0

    aged_ar = await generate_aged_partner_balance(
        s, company_id=co.id, report_type="receivable", as_of_date=date(2026, 7, 15)
    )
    # Invoice due June 5, as_of July 15 = 40 days overdue -> falls in 31-60 days bucket ($725.00 residual)
    assert D(aged_ar["totals"]["days_31_60"]) == D("725.00")
    assert D(aged_ar["totals"]["total"]) == D("725.00")

    # 5. Verify CSV, XLSX, and PDF export generators produce valid payloads
    csv_bytes = export_report_csv(bs)
    xlsx_bytes = export_report_xlsx(bs)
    pdf_bytes = export_report_pdf(bs)
    assert b"TOTAL ASSETS" in csv_bytes
    assert xlsx_bytes[:2] == b"PK"  # ZIP/XLSX magic header
    assert pdf_bytes[:4] == b"%PDF"  # PDF magic header
