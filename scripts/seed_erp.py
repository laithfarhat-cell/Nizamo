"""Idempotent Enterprise ERP Seed Bootstrapper for Nizamo ERP."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

# Ensure repo root is on sys.path when invoked directly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import backend.core.tenancy  # noqa: F401
import backend.core.sequences  # noqa: F401
import backend.core.security  # noqa: F401
import backend.core.chatter  # noqa: F401
from backend.core.config import settings
from backend.core.database import Base, configure_database
from backend.core.decimal_utils import D, ZERO
from backend.core.registry import registry
from backend.core.security import (
    ALL_ROLES,
    IrModelAccess,
    IrRule,
    ROLE_ACCOUNTANT,
    ROLE_ADMIN,
    ROLE_BILLING,
    ROLE_INVENTORY,
    ROLE_SALES,
    ResGroup,
    ResUser,
    hash_password,
)
from backend.core.sequences import IrSequence
from backend.core.tenancy import ResCompany, ResCurrency, ResCurrencyRate, ResPartner

# Load all plugin models in topological order
registry.load_all()

from backend.modules.accounting.models import (  # noqa: E402
    ACCOUNT_TYPE_ASSET_CASH,
    ACCOUNT_TYPE_ASSET_CURRENT,
    ACCOUNT_TYPE_ASSET_NON_CURRENT,
    ACCOUNT_TYPE_ASSET_RECEIVABLE,
    ACCOUNT_TYPE_EQUITY,
    ACCOUNT_TYPE_EXPENSE,
    ACCOUNT_TYPE_EXPENSE_COGS,
    ACCOUNT_TYPE_INCOME,
    ACCOUNT_TYPE_INCOME_OTHER,
    ACCOUNT_TYPE_LIABILITY_CURRENT,
    ACCOUNT_TYPE_LIABILITY_NON_CURRENT,
    ACCOUNT_TYPE_LIABILITY_PAYABLE,
    AccountAccount,
    AccountAnalyticAccount,
    AccountJournal,
    AccountTax,
)
from backend.modules.accounting.posting_engine import (  # noqa: E402
    InvoiceLineInput,
    JournalLineInput,
    create_invoice_move,
    create_journal_entry,
    post_move,
)
from backend.modules.accounting.reconciliation import register_payment  # noqa: E402
from backend.modules.inventory.models import (  # noqa: E402
    ProductCategory,
    ProductTemplate,
    StockLocation,
)
from backend.modules.inventory.valuation_engine import validate_picking  # noqa: E402
from backend.modules.purchase.workflows import (  # noqa: E402
    confirm_purchase_order,
    create_bill_from_purchase_order,
    create_purchase_order,
)
from backend.modules.sales.workflows import (  # noqa: E402
    confirm_sale_order,
    create_invoice_from_sale_order,
    create_sale_order,
)


async def seed_company_chart(
    session: AsyncSession,
    company: ResCompany,
    currencies: dict[str, ResCurrency],
) -> dict[str, object]:
    """Create standard IFRS/GAAP Chart of Accounts, Journals, Sequences, Taxes, Locations, Products."""
    coa_defs = [
        ("101000", "Cash on Hand", "الصندوق النقدي", ACCOUNT_TYPE_ASSET_CASH, False),
        ("102000", "Main Operating Bank", "الحساب البنكي الرئيسي", ACCOUNT_TYPE_ASSET_CASH, False),
        ("110000", "Accounts Receivable", "الذمم المدينة", ACCOUNT_TYPE_ASSET_RECEIVABLE, True),
        ("120000", "Inventory Valuation (Stock)", "تقييم المخزون", ACCOUNT_TYPE_ASSET_CURRENT, False),
        ("135000", "Input VAT Receivable", "ضريبة القيمة المضافة للمشتريات", ACCOUNT_TYPE_ASSET_CURRENT, False),
        ("150000", "Property & IT Equipment", "الممتلكات والمعدات التقنية", ACCOUNT_TYPE_ASSET_NON_CURRENT, False),
        ("200000", "Accounts Payable", "الذمم الدائنة", ACCOUNT_TYPE_LIABILITY_PAYABLE, True),
        ("215000", "Stock Interim (Received)", "حساب وسيط المخزون المستلم", ACCOUNT_TYPE_LIABILITY_CURRENT, False),
        ("220000", "Output VAT Payable", "ضريبة القيمة المضافة للمبيعات", ACCOUNT_TYPE_LIABILITY_CURRENT, False),
        ("250000", "Long-Term Bank Facility", "قروض بنكية طويلة الأجل", ACCOUNT_TYPE_LIABILITY_NON_CURRENT, False),
        ("300000", "Share Capital", "رأس المال المدفوع", ACCOUNT_TYPE_EQUITY, False),
        ("310000", "Retained Earnings", "الأرباح المحتجزة", ACCOUNT_TYPE_EQUITY, False),
        ("400000", "Product Sales Revenue", "إيرادات مبيعات البضائع", ACCOUNT_TYPE_INCOME, False),
        ("410000", "Consulting & ERP Services Revenue", "إيرادات الاستشارات والخدمات", ACCOUNT_TYPE_INCOME, False),
        ("490000", "Foreign Exchange Gain", "أرباح فروق العملة", ACCOUNT_TYPE_INCOME_OTHER, False),
        ("500000", "Cost of Goods Sold (COGS)", "تكلفة البضاعة المباعة", ACCOUNT_TYPE_EXPENSE_COGS, False),
        ("510000", "Office Rent & Facilities", "إيجار المكاتب والمرافق", ACCOUNT_TYPE_EXPENSE, False),
        ("520000", "Cloud Infrastructure & Software", "البنية السحابية والبرمجيات", ACCOUNT_TYPE_EXPENSE, False),
        ("530000", "Salaries & Benefits", "الرواتب والمزايا", ACCOUNT_TYPE_EXPENSE, False),
        ("590000", "Foreign Exchange Loss", "خسائر فروق العملة", ACCOUNT_TYPE_EXPENSE, False),
    ]

    accounts: dict[str, AccountAccount] = {}
    for code, name, name_ar, atype, rec in coa_defs:
        acct = AccountAccount(
            company_id=company.id,
            code=code,
            name=name,
            name_ar=name_ar,
            account_type=atype,
            reconcilable=rec,
        )
        session.add(acct)
        accounts[code] = acct
    await session.flush()

    # Sequences & Journals
    journal_defs = [
        ("INV", "Customer Invoices", "فواتير العملاء", "SALE", "400000", "INV/%(year)s/"),
        ("BILL", "Vendor Bills", "فواتير الموردين", "PURCHASE", "500000", "BILL/%(year)s/"),
        ("BNK1", "Operating Bank (USD)", "الحساب البنكي الرئيسي", "BANK", "102000", "BNK/%(year)s/"),
        ("CSH1", "Petty Cash", "الخزينة النقدية", "CASH", "101000", "CSH/%(year)s/"),
        ("MISC", "Miscellaneous Operations", "قيود يومية متنوعة", "GENERAL", None, "JE/%(year)s/"),
        ("STJ", "Inventory Valuation", "يومية تقييم المخزون", "GENERAL", "120000", "STJ/%(year)s/"),
    ]
    journals: dict[str, AccountJournal] = {}
    for code, name, name_ar, jtype, def_acct, prefix in journal_defs:
        seq = IrSequence(
            company_id=company.id,
            code=f"journal_{code.lower()}",
            name=f"{name} Sequence",
            prefix=prefix,
            suffix="",
            padding=5,
            number_next=1,
            number_increment=1,
        )
        session.add(seq)
        await session.flush()
        j = AccountJournal(
            company_id=company.id,
            code=code,
            name=name,
            name_ar=name_ar,
            journal_type=jtype,
            default_account_id=accounts[def_acct].id if def_acct else None,
            sequence_id=seq.id,
            currency_id=company.currency_id,
        )
        session.add(j)
        journals[code] = j
    await session.flush()

    # Taxes
    vat_sale = AccountTax(
        company_id=company.id,
        name="15% VAT (Sales)",
        name_ar="ضريبة القيمة المضافة 15% مبيعات",
        tax_type="SALE",
        amount_type="PERCENT",
        amount=D("15"),
        price_include=False,
        include_base_amount=False,
        sequence=10,
        tax_account_id=accounts["220000"].id,
    )
    vat_purchase = AccountTax(
        company_id=company.id,
        name="15% VAT (Purchase)",
        name_ar="ضريبة القيمة المضافة 15% مشتريات",
        tax_type="PURCHASE",
        amount_type="PERCENT",
        amount=D("15"),
        price_include=False,
        include_base_amount=False,
        sequence=10,
        tax_account_id=accounts["135000"].id,
    )
    vat_included = AccountTax(
        company_id=company.id,
        name="15% VAT (Included)",
        name_ar="ضريبة 15% شاملة السعر",
        tax_type="SALE",
        amount_type="PERCENT",
        amount=D("15"),
        price_include=True,
        include_base_amount=False,
        sequence=20,
        tax_account_id=accounts["220000"].id,
    )
    session.add_all([vat_sale, vat_purchase, vat_included])
    await session.flush()

    # Analytic Cost Centers
    cc_ops = AccountAnalyticAccount(company_id=company.id, code="CC-OPS", name="Operations & Logistics")
    cc_sales = AccountAnalyticAccount(company_id=company.id, code="CC-SALES", name="Commercial & Sales")
    cc_it = AccountAnalyticAccount(company_id=company.id, code="CC-IT", name="Enterprise IT & Cloud")
    session.add_all([cc_ops, cc_sales, cc_it])
    await session.flush()

    # Warehouse Locations
    loc_stock = StockLocation(
        company_id=company.id, name="Stock", complete_name="WH/Stock", usage="INTERNAL"
    )
    loc_cust = StockLocation(
        company_id=company.id, name="Customers", complete_name="Partner Locations/Customers", usage="CUSTOMER"
    )
    loc_vend = StockLocation(
        company_id=company.id, name="Vendors", complete_name="Partner Locations/Vendors", usage="SUPPLIER"
    )
    loc_loss = StockLocation(
        company_id=company.id,
        name="Inventory Loss",
        complete_name="Virtual Locations/Inventory Loss",
        usage="INVENTORY",
    )
    session.add_all([loc_stock, loc_cust, loc_vend, loc_loss])
    await session.flush()

    # Product Categories (AVCO, FIFO, STANDARD, Services)
    cat_avco = ProductCategory(
        company_id=company.id,
        name="Enterprise Hardware (AVCO)",
        costing_method="AVCO",
        valuation="REAL_TIME",
        stock_valuation_account_id=accounts["120000"].id,
        stock_input_account_id=accounts["215000"].id,
        stock_output_account_id=accounts["500000"].id,
        income_account_id=accounts["400000"].id,
        expense_account_id=accounts["500000"].id,
    )
    cat_fifo = ProductCategory(
        company_id=company.id,
        name="Datacenter Servers (FIFO)",
        costing_method="FIFO",
        valuation="REAL_TIME",
        stock_valuation_account_id=accounts["120000"].id,
        stock_input_account_id=accounts["215000"].id,
        stock_output_account_id=accounts["500000"].id,
        income_account_id=accounts["400000"].id,
        expense_account_id=accounts["500000"].id,
    )
    cat_svc = ProductCategory(
        company_id=company.id,
        name="ERP Implementation Services",
        costing_method="STANDARD",
        valuation="MANUAL",
        income_account_id=accounts["410000"].id,
        expense_account_id=accounts["530000"].id,
    )
    session.add_all([cat_avco, cat_fifo, cat_svc])
    await session.flush()

    # Products
    p_laptop = ProductTemplate(
        company_id=company.id,
        default_code="LAP-PRO-16",
        name="Workstation Pro 16\" Laptop",
        name_ar="حاسوب محمول احترافي 16 بوصة",
        product_type="STORABLE",
        category_id=cat_avco.id,
        list_price=D("2400.00"),
        standard_price=D("1500.00"),
        uom="Units",
    )
    p_server = ProductTemplate(
        company_id=company.id,
        default_code="SRV-RACK-4U",
        name="Enterprise 4U Rack Server",
        name_ar="خادم مؤسسي 4U",
        product_type="STORABLE",
        category_id=cat_fifo.id,
        list_price=D("6500.00"),
        standard_price=D("4000.00"),
        uom="Units",
    )
    p_switch = ProductTemplate(
        company_id=company.id,
        default_code="NET-SW-48P",
        name="Managed 48-Port 10G Switch",
        name_ar="مبدل شبكة 48 منفذ 10G",
        product_type="STORABLE",
        category_id=cat_avco.id,
        list_price=D("1250.00"),
        standard_price=D("750.00"),
        uom="Units",
    )
    p_service = ProductTemplate(
        company_id=company.id,
        default_code="SVC-ERP-IMP",
        name="ERP Architecture & Deployment Day",
        name_ar="يوم استشارات وتطبيق نظام ERP",
        product_type="SERVICE",
        category_id=cat_svc.id,
        list_price=D("1200.00"),
        standard_price=D("450.00"),
        uom="Days",
    )
    session.add_all([p_laptop, p_server, p_switch, p_service])
    await session.flush()

    # Exchange Rates for Company (Base = USD)
    today = date.today()
    rate_date_early = today - timedelta(days=60)
    rate_date_mid = today - timedelta(days=15)
    rate_date_now = today - timedelta(days=2)

    rates = [
        ResCurrencyRate(company_id=company.id, currency_id=currencies["USD"].id, effective_date=rate_date_early, rate=D("1.000000")),
        ResCurrencyRate(company_id=company.id, currency_id=currencies["EUR"].id, effective_date=rate_date_early, rate=D("1.100000")),
        ResCurrencyRate(company_id=company.id, currency_id=currencies["EUR"].id, effective_date=rate_date_now, rate=D("1.140000")),
        ResCurrencyRate(company_id=company.id, currency_id=currencies["LYD"].id, effective_date=rate_date_early, rate=D("0.206000")),
        ResCurrencyRate(company_id=company.id, currency_id=currencies["SAR"].id, effective_date=rate_date_early, rate=D("0.266667")),
    ]
    session.add_all(rates)
    await session.flush()

    # Partners (Customers & Vendors)
    c_med = ResPartner(
        company_id=company.id,
        name="Mediterranean Telecom Group",
        name_ar="مجموعة المتوسط للاتصالات",
        ref="CUST-001",
        email="finance@medtelecom.example",
        phone="+218-21-555-0101",
        vat="LY-VAT-883920",
        address="Tripoli Business Tower, Floor 12",
        is_customer=True,
        is_supplier=False,
    )
    c_euro = ResPartner(
        company_id=company.id,
        name="Nordic Systems GmbH",
        name_ar="نورديك سيستمز ألمانيا",
        ref="CUST-002",
        email="ap@nordicsystems.example",
        phone="+49-30-555-0192",
        vat="DE-298471022",
        address="Friedrichstrasse 90, Berlin",
        is_customer=True,
        is_supplier=False,
    )
    c_gulf = ResPartner(
        company_id=company.id,
        name="Al-Riyadh Horizon Logistics",
        name_ar="آفاق الرياض للوجستيات",
        ref="CUST-003",
        email="billing@horizonlogistics.example",
        phone="+966-11-555-0144",
        vat="SA-300491820100003",
        address="King Fahd Road, Riyadh",
        is_customer=True,
        is_supplier=False,
    )
    v_silicon = ResPartner(
        company_id=company.id,
        name="Apex Silicon Distributors",
        name_ar="أبيكس لتوزيع التقنية",
        ref="VEND-001",
        email="orders@apexsilicon.example",
        phone="+1-408-555-0188",
        vat="US-94-3328191",
        address="2400 Innovation Way, San Jose, CA",
        is_customer=False,
        is_supplier=True,
    )
    v_cloud = ResPartner(
        company_id=company.id,
        name="Atlas Cloud & Hardware Supply",
        name_ar="أطلس للسحابة والأجهزة",
        ref="VEND-002",
        email="ar@atlascloud.example",
        phone="+44-20-7946-0912",
        vat="GB-772910421",
        address="18 Canary Wharf, London",
        is_customer=False,
        is_supplier=True,
    )
    session.add_all([c_med, c_euro, c_gulf, v_silicon, v_cloud])
    await session.flush()

    return {
        "accounts": accounts,
        "journals": journals,
        "taxes": {"vat_sale": vat_sale, "vat_purchase": vat_purchase, "vat_included": vat_included},
        "analytics": {"ops": cc_ops, "sales": cc_sales, "it": cc_it},
        "products": {"laptop": p_laptop, "server": p_server, "switch": p_switch, "service": p_service},
        "partners": {"med": c_med, "euro": c_euro, "gulf": c_gulf, "silicon": v_silicon, "cloud": v_cloud},
    }


async def seed_erp(database_url: str | None = None) -> None:
    """Idempotently bootstrap Nizamo ERP database with master data and realistic end-to-end transactions."""
    url = database_url or os.environ.get("DATABASE_URL") or settings.DATABASE_URL
    eng, session_factory = configure_database(url)

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        existing = (
            await session.execute(select(ResCompany).where(ResCompany.code == "NIZAMO"))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"[seed_erp] Company '{existing.code}' already exists — skipping idempotent bootstrap.")
            await eng.dispose()
            return

        # 1. Currencies
        currencies = {
            "USD": ResCurrency(code="USD", name="US Dollar", symbol="$", decimal_places=2),
            "EUR": ResCurrency(code="EUR", name="Euro", symbol="€", decimal_places=2),
            "LYD": ResCurrency(code="LYD", name="Libyan Dinar", symbol="LYD", decimal_places=3),
            "SAR": ResCurrency(code="SAR", name="Saudi Riyal", symbol="SAR", decimal_places=2),
        }
        session.add_all(currencies.values())
        await session.flush()

        # 2. Companies (Multi-Company Demo)
        co_main = ResCompany(
            code="NIZAMO",
            name="Nizamo Global Trading LLC",
            name_ar="نيزامو للتجارة العالمية ذ.م.م",
            tax_id="TAX-NIZ-2026-001",
            email="erp@nizamo.io",
            phone="+218-21-555-9000",
            address="Al-Shat Road, Tripoli Financial District",
            currency_id=currencies["USD"].id,
        )
        co_sub = ResCompany(
            code="NIZAMO-MENA",
            name="Nizamo MENA Logistics",
            name_ar="نيزامو مينا للوجستيات",
            tax_id="TAX-NIZ-2026-002",
            email="mena@nizamo.io",
            phone="+966-11-555-8000",
            address="Riyadh Logistics Park, Bldg 4",
            currency_id=currencies["USD"].id,
        )
        session.add_all([co_main, co_sub])
        await session.flush()

        # 3. RBAC Groups, Model Permissions, Record Rules & Users
        groups: dict[str, ResGroup] = {}
        for role_name in ALL_ROLES:
            g = ResGroup(name=role_name, description=f"ERP Security Role: {role_name}")
            session.add(g)
            groups[role_name] = g
        await session.flush()

        acl_matrix = [
            (ROLE_ACCOUNTANT, "account_move", True, True, True, False),
            (ROLE_ACCOUNTANT, "account_payment", True, True, True, False),
            (ROLE_ACCOUNTANT, "account_account", True, True, True, False),
            (ROLE_ACCOUNTANT, "res_partner", True, True, True, False),
            (ROLE_ACCOUNTANT, "res_currency", True, True, True, False),
            (ROLE_ACCOUNTANT, "purchase_order", True, True, True, False),
            (ROLE_BILLING, "account_move", True, True, True, False),
            (ROLE_BILLING, "account_payment", True, True, True, False),
            (ROLE_BILLING, "sale_order", True, True, True, False),
            (ROLE_BILLING, "res_partner", True, True, True, False),
            (ROLE_SALES, "sale_order", True, True, True, False),
            (ROLE_SALES, "res_partner", True, True, True, False),
            (ROLE_INVENTORY, "stock_picking", True, True, True, False),
            (ROLE_INVENTORY, "product_template", True, True, True, False),
            (ROLE_INVENTORY, "purchase_order", True, True, True, False),
        ]
        for gname, model, r, w, c, u in acl_matrix:
            session.add(
                IrModelAccess(
                    group_id=groups[gname].id,
                    model_name=model,
                    perm_read=r,
                    perm_write=w,
                    perm_create=c,
                    perm_unlink=u,
                )
            )
        for model_name in ["account_move", "res_partner", "product_template", "stock_picking", "sale_order", "purchase_order"]:
            session.add(
                IrRule(
                    name=f"Multi-Company Isolation: {model_name}",
                    model_name=model_name,
                    domain_description="[('company_id', 'in', user.allowed_company_ids)]",
                )
            )

        admin_user = ResUser(
            login="admin",
            full_name="Laith Farhat (CPA / Admin)",
            email="admin@nizamo.io",
            password_hash=hash_password("admin123"),
            default_company_id=co_main.id,
        )
        admin_user.groups = [groups[ROLE_ADMIN], groups[ROLE_ACCOUNTANT]]
        admin_user.companies = [co_main, co_sub]

        accountant_user = ResUser(
            login="accountant",
            full_name="Nadia Mansour (Chief Accountant)",
            email="accountant@nizamo.io",
            password_hash=hash_password("account123"),
            default_company_id=co_main.id,
        )
        accountant_user.groups = [groups[ROLE_ACCOUNTANT]]
        accountant_user.companies = [co_main]

        sales_user = ResUser(
            login="sales",
            full_name="Omar Khalil (Sales Executive)",
            email="sales@nizamo.io",
            password_hash=hash_password("sales123"),
            default_company_id=co_main.id,
        )
        sales_user.groups = [groups[ROLE_SALES]]
        sales_user.companies = [co_main]

        session.add_all([admin_user, accountant_user, sales_user])
        await session.flush()

        # 4. Seed Chart of Accounts & Master Data for both companies
        main_ctx = await seed_company_chart(session, co_main, currencies)
        sub_ctx = await seed_company_chart(session, co_sub, currencies)

        # 5. Seed Complete End-to-End Business Transactions in Primary Company (NIZAMO)
        accts = main_ctx["accounts"]
        journals = main_ctx["journals"]
        taxes = main_ctx["taxes"]
        analytics = main_ctx["analytics"]
        prods = main_ctx["products"]
        partners = main_ctx["partners"]

        today = date.today()
        d_cap = today - timedelta(days=55)
        d_po1 = today - timedelta(days=50)
        d_po2 = today - timedelta(days=30)
        d_so1 = today - timedelta(days=20)
        d_overdue = today - timedelta(days=45)
        d_fx_inv = today - timedelta(days=25)
        d_fx_pay = today - timedelta(days=1)
        d_exp = today - timedelta(days=10)

        # 5A. Opening Share Capital Injection ($250,000)
        cap_move = await create_journal_entry(
            session,
            company_id=co_main.id,
            journal_id=journals["MISC"].id,
            move_date=d_cap,
            ref="Initial Share Capital Contribution",
            lines=[
                JournalLineInput(
                    account_id=accts["102000"].id,
                    name="Paid-in Capital Wire Deposit",
                    debit=D("250000.00"),
                    credit=ZERO,
                ),
                JournalLineInput(
                    account_id=accts["300000"].id,
                    name="Authorized & Issued Share Capital",
                    debit=ZERO,
                    credit=D("250000.00"),
                ),
            ],
            author_name=admin_user.full_name,
            author_id=admin_user.id,
        )
        await post_move(session, cap_move.id, author_name=admin_user.full_name, author_id=admin_user.id)

        # 5B. Procure-to-Pay Cycle #1 (PO -> Receipt -> AVCO/FIFO Layer -> 3-Way Matched Bill -> Full Payment)
        po1 = await create_purchase_order(
            session,
            company_id=co_main.id,
            partner_id=partners["silicon"].id,
            date_order=d_po1,
            lines_data=[
                {
                    "product_id": prods["laptop"].id,
                    "name": prods["laptop"].name,
                    "quantity": D("10"),
                    "price_unit": D("1400.00"),
                    "tax_ids": [taxes["vat_purchase"].id],
                    "analytic_account_id": analytics["ops"].id,
                },
                {
                    "product_id": prods["server"].id,
                    "name": prods["server"].name,
                    "quantity": D("4"),
                    "price_unit": D("3800.00"),
                    "tax_ids": [taxes["vat_purchase"].id],
                    "analytic_account_id": analytics["it"].id,
                },
            ],
            author_name=admin_user.full_name,
            author_id=admin_user.id,
        )
        po1, po1_picking = await confirm_purchase_order(
            session, po1.id, author_name=admin_user.full_name, author_id=admin_user.id
        )
        if po1_picking:
            await validate_picking(
                session, po1_picking.id, validation_date=d_po1, author_name=admin_user.full_name, author_id=admin_user.id
            )
        bill1 = await create_bill_from_purchase_order(
            session, po1.id, bill_date=d_po1, author_name=admin_user.full_name, author_id=admin_user.id
        )
        bill1 = await post_move(session, bill1.id, author_name=admin_user.full_name, author_id=admin_user.id)
        await register_payment(
            session,
            company_id=co_main.id,
            payment_type="OUTBOUND",
            partner_type="SUPPLIER",
            partner_id=partners["silicon"].id,
            journal_id=journals["BNK1"].id,
            amount=bill1.amount_total,
            payment_date=d_po1 + timedelta(days=3),
            ref=f"Wire Payment {bill1.name}",
            invoice_move_id=bill1.id,
            author_name=admin_user.full_name,
            author_id=admin_user.id,
        )

        # 5C. Procure-to-Pay Cycle #2 (Different cost -> demonstrates AVCO recalculation & FIFO 2nd layer + Partial Payment)
        po2 = await create_purchase_order(
            session,
            company_id=co_main.id,
            partner_id=partners["cloud"].id,
            date_order=d_po2,
            lines_data=[
                {
                    "product_id": prods["laptop"].id,
                    "name": prods["laptop"].name,
                    "quantity": D("10"),
                    "price_unit": D("1600.00"),  # AVCO becomes (10*1400 + 10*1600)/20 = 1500.00!
                    "tax_ids": [taxes["vat_purchase"].id],
                    "analytic_account_id": analytics["ops"].id,
                },
                {
                    "product_id": prods["server"].id,
                    "name": prods["server"].name,
                    "quantity": D("4"),
                    "price_unit": D("4200.00"),  # FIFO Layer #2 @ 4200.00
                    "tax_ids": [taxes["vat_purchase"].id],
                    "analytic_account_id": analytics["it"].id,
                },
            ],
            author_name=admin_user.full_name,
            author_id=admin_user.id,
        )
        po2, po2_picking = await confirm_purchase_order(
            session, po2.id, author_name=admin_user.full_name, author_id=admin_user.id
        )
        if po2_picking:
            await validate_picking(
                session, po2_picking.id, validation_date=d_po2, author_name=admin_user.full_name, author_id=admin_user.id
            )
        bill2 = await create_bill_from_purchase_order(
            session, po2.id, bill_date=d_po2, author_name=admin_user.full_name, author_id=admin_user.id
        )
        bill2 = await post_move(session, bill2.id, author_name=admin_user.full_name, author_id=admin_user.id)
        # Pay 50% of Bill 2 so AP & Aged Payable show an active partial balance
        await register_payment(
            session,
            company_id=co_main.id,
            payment_type="OUTBOUND",
            partner_type="SUPPLIER",
            partner_id=partners["cloud"].id,
            journal_id=journals["BNK1"].id,
            amount=D("15000.00"),
            payment_date=d_po2 + timedelta(days=5),
            ref=f"Partial Settlement {bill2.name}",
            invoice_move_id=bill2.id,
            author_name=admin_user.full_name,
            author_id=admin_user.id,
        )

        # 5D. Order-to-Cash Cycle #1 (SO -> Delivery consuming FIFO Layer #1 & #2 and AVCO -> Customer Invoice -> Partial Payment)
        so1 = await create_sale_order(
            session,
            company_id=co_main.id,
            partner_id=partners["med"].id,
            date_order=d_so1,
            invoice_policy="DELIVERED",
            lines_data=[
                {
                    "product_id": prods["laptop"].id,
                    "name": prods["laptop"].name,
                    "quantity": D("6"),
                    "price_unit": D("2400.00"),
                    "tax_ids": [taxes["vat_sale"].id],
                    "analytic_account_id": analytics["sales"].id,
                },
                {
                    "product_id": prods["server"].id,
                    "name": prods["server"].name,
                    "quantity": D("5"),  # Consumes 4 @ 3800 + 1 @ 4200 = $19,400 FIFO COGS!
                    "price_unit": D("6500.00"),
                    "tax_ids": [taxes["vat_sale"].id],
                    "analytic_account_id": analytics["sales"].id,
                },
            ],
            author_name=sales_user.full_name,
            author_id=sales_user.id,
        )
        so1, so1_picking = await confirm_sale_order(
            session, so1.id, author_name=sales_user.full_name, author_id=sales_user.id
        )
        if so1_picking:
            await validate_picking(
                session, so1_picking.id, validation_date=d_so1 + timedelta(days=1), author_name=admin_user.full_name, author_id=admin_user.id
            )
        inv1 = await create_invoice_from_sale_order(
            session, so1.id, invoice_date=d_so1 + timedelta(days=2), author_name=admin_user.full_name, author_id=admin_user.id
        )
        inv1 = await post_move(session, inv1.id, author_name=admin_user.full_name, author_id=admin_user.id)
        await register_payment(
            session,
            company_id=co_main.id,
            payment_type="INBOUND",
            partner_type="CUSTOMER",
            partner_id=partners["med"].id,
            journal_id=journals["BNK1"].id,
            amount=D("35000.00"),
            payment_date=d_so1 + timedelta(days=5),
            ref=f"Customer Wire {inv1.name}",
            invoice_move_id=inv1.id,
            author_name=admin_user.full_name,
            author_id=admin_user.id,
        )

        # 5E. Multi-Currency EUR Customer Invoice + EUR Payment at higher rate -> Realized FX Gain!
        # Invoice date: EUR = 1.10 USD -> 10,000 EUR = $11,000 USD
        # Payment date: EUR = 1.14 USD -> 10,000 EUR = $11,400 USD -> Realized FX Gain = +$400.00!
        fx_inv = await create_invoice_move(
            session,
            company_id=co_main.id,
            move_type="out_invoice",
            journal_id=journals["INV"].id,
            partner_id=partners["euro"].id,
            invoice_date=d_fx_inv,
            invoice_date_due=d_fx_inv + timedelta(days=30),
            currency_id=currencies["EUR"].id,
            ref="EUR Consulting Contract Q4",
            invoice_lines=[
                InvoiceLineInput(
                    account_id=accts["410000"].id,
                    name="Cross-Border ERP Architecture Consulting",
                    quantity=D("10"),
                    price_unit=D("1000.00"),
                    analytic_account_id=analytics["it"].id,
                )
            ],
            author_name=admin_user.full_name,
            author_id=admin_user.id,
        )
        fx_inv = await post_move(session, fx_inv.id, author_name=admin_user.full_name, author_id=admin_user.id)
        await register_payment(
            session,
            company_id=co_main.id,
            payment_type="INBOUND",
            partner_type="CUSTOMER",
            partner_id=partners["euro"].id,
            journal_id=journals["BNK1"].id,
            amount=D("10000.00"),
            currency_id=currencies["EUR"].id,
            payment_date=d_fx_pay,
            ref="SEPA EUR Settlement",
            invoice_move_id=fx_inv.id,
            author_name=admin_user.full_name,
            author_id=admin_user.id,
        )

        # 5F. Overdue Customer Invoice (45 days old, due 35 days ago -> Aged Receivable 31-60 Days bucket)
        overdue_inv = await create_invoice_move(
            session,
            company_id=co_main.id,
            move_type="out_invoice",
            journal_id=journals["INV"].id,
            partner_id=partners["gulf"].id,
            invoice_date=d_overdue,
            invoice_date_due=d_overdue + timedelta(days=10),
            ref="Riyadh ERP Phase 1 Milestone",
            invoice_lines=[
                InvoiceLineInput(
                    account_id=accts["410000"].id,
                    name="ERP Deployment & Data Migration Services",
                    quantity=D("8"),
                    price_unit=D("1200.00"),
                    tax_ids=[taxes["vat_sale"].id],
                    analytic_account_id=analytics["sales"].id,
                )
            ],
            author_name=admin_user.full_name,
            author_id=admin_user.id,
        )
        await post_move(session, overdue_inv.id, author_name=admin_user.full_name, author_id=admin_user.id)

        # 5G. Operating Expense Journal Entry (Rent & Cloud Infrastructure)
        exp_move = await create_journal_entry(
            session,
            company_id=co_main.id,
            journal_id=journals["MISC"].id,
            move_date=d_exp,
            ref="Monthly HQ Rent & Cloud Infrastructure",
            lines=[
                JournalLineInput(
                    account_id=accts["510000"].id,
                    analytic_account_id=analytics["ops"].id,
                    name="Tripoli HQ Office Lease",
                    debit=D("4500.00"),
                    credit=ZERO,
                ),
                JournalLineInput(
                    account_id=accts["520000"].id,
                    analytic_account_id=analytics["it"].id,
                    name="AWS & PostgreSQL Managed Cluster",
                    debit=D("2200.00"),
                    credit=ZERO,
                ),
                JournalLineInput(
                    account_id=accts["102000"].id,
                    name="Bank Auto-Debit Monthly Facilities",
                    debit=ZERO,
                    credit=D("6700.00"),
                ),
            ],
            author_name=accountant_user.full_name,
            author_id=accountant_user.id,
        )
        await post_move(session, exp_move.id, author_name=accountant_user.full_name, author_id=accountant_user.id)

        # 5H. Draft Sales Quotation ready for interactive confirmation in the UI
        await create_sale_order(
            session,
            company_id=co_main.id,
            partner_id=partners["gulf"].id,
            date_order=today,
            invoice_policy="ORDERED",
            note="Enterprise workstation refresh quotation",
            lines_data=[
                {
                    "product_id": prods["laptop"].id,
                    "name": prods["laptop"].name,
                    "quantity": D("4"),
                    "price_unit": D("2400.00"),
                    "tax_ids": [taxes["vat_sale"].id],
                    "analytic_account_id": analytics["sales"].id,
                },
                {
                    "product_id": prods["service"].id,
                    "name": prods["service"].name,
                    "quantity": D("3"),
                    "price_unit": D("1200.00"),
                    "tax_ids": [taxes["vat_sale"].id],
                    "analytic_account_id": analytics["it"].id,
                },
            ],
            author_name=sales_user.full_name,
            author_id=sales_user.id,
        )

        # 6. Seed Opening Capital in Secondary Company (NIZAMO-MENA) so Company Switcher shows live isolated books
        sub_accts = sub_ctx["accounts"]
        sub_journals = sub_ctx["journals"]
        sub_cap = await create_journal_entry(
            session,
            company_id=co_sub.id,
            journal_id=sub_journals["MISC"].id,
            move_date=d_cap,
            ref="MENA Subsidiary Capital Injection",
            lines=[
                JournalLineInput(
                    account_id=sub_accts["102000"].id,
                    name="Subsidiary Bank Capital Deposit",
                    debit=D("100000.00"),
                    credit=ZERO,
                ),
                JournalLineInput(
                    account_id=sub_accts["300000"].id,
                    name="Subsidiary Share Capital",
                    debit=ZERO,
                    credit=D("100000.00"),
                ),
            ],
            author_name=admin_user.full_name,
            author_id=admin_user.id,
        )
        await post_move(session, sub_cap.id, author_name=admin_user.full_name, author_id=admin_user.id)

        await session.commit()
        print("[seed_erp] Successfully seeded Nizamo ERP (2 companies, CoA, multi-currency rates, AVCO/FIFO inventory, P2P, O2C, FX Gain/Loss).")

    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(seed_erp())
