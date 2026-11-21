# Nizamo ERP — Modular Enterprise Resource Planning & Double-Entry Accounting Platform

**Nizamo** is a production-grade, Odoo-inspired **Enterprise Resource Planning (ERP) & IFRS/GAAP Double-Entry Accounting Platform** engineered with **FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL 16 + Alembic + Pydantic v2** on the backend and **React + TypeScript + Vite + TailwindCSS + TanStack Table + TanStack Query** on the frontend.

---

## Key Capabilities

### 1. Modular ERP Kernel (`backend/core/`)
- **Plugin Registry (`registry.py`)**: Discovers `__manifest__.py` metadata across `backend/modules/*`, computes topological load order via Kahn's algorithm, and mounts models, routers, security rules, and seed hooks.
- **Multi-Company & Multi-Currency (`tenancy.py`)**: Strict `company_id` tenant isolation (`X-Company-Id` header) and historical `res_currency_rate` conversions (`USD`, `EUR`, `LYD`, `SAR`) using zero-float `DECIMAL(19, 6)` + `ROUND_HALF_UP`.
- **Atomic Gapless Sequences (`sequences.py`)**: `SELECT ... FOR UPDATE` row-locked numbering (`INV/2026/00001`, `BILL/2026/00001`, `SO/2026/00001`, `PO/2026/00001`, `WH/OUT/2026/00001`).
- **RBAC & Immutable Audit Chatter (`security.py`, `chatter.py`)**: Model-level CRUD permissions (`Accountant`, `Billing Officer`, `Sales User`, `Inventory Manager`, `System Admin`), company record rules, and automatic field-level diff logging (`Old Value -> New Value`).

### 2. Double-Entry Accounting Engine (`backend/modules/accounting/`)
- **Unified Document Model**: General Journal Entries, Customer Invoices, Vendor Bills, Credit Notes, and Payments unified under `account_move` & `account_move_line`.
- **Strict Ledger Invariants + PostgreSQL Immutability Trigger (`0002_production_hardening_and_v2`)**: Rejects unbalanced entries ($\sum \text{debit} \ne \sum \text{credit}$), enforces a native PostgreSQL `BEFORE UPDATE OR DELETE` trigger blocking mutation/deletion of `POSTED` `account_move_line` financial columns, supports safe draft editing (`PUT /accounting/moves/{id}`) with Chatter field diffs, and enforces `fiscalyear_lock_date` & `tax_lock_date`.
- **Official Invoice PDF + ZATCA Phase-1 QR Code (`invoice_pdf.py`)**: Generates A4 tax invoices/bills/credit notes embedding a ZATCA-compliant Base64 TLV QR Code (Tags 1–5: Seller Name, VAT Number, Timestamp, Invoice Total, VAT Total) via `GET /accounting/moves/{id}/pdf`.
- **Year-End Fiscal Closing Wizard (`closing_engine.py`)**: Previews and posts a balanced closing journal entry zeroing all Income & Expense accounts into Retained Earnings (`310000`) and automatically locks `fiscalyear_lock_date`.
- **Unrealized FX Revaluation — IAS 21 (`closing_engine.py`)**: Computes period-end unrealized FX gains/losses on open foreign-currency receivables/payables and posts revaluation entries with optional next-day auto-reversal.
- **Bank Statement CSV Import & Smart Reconciliation (`bank_reconciliation.py`)**: Imports CSV bank feeds, auto-matches lines against open customer invoices & vendor bills with confidence scoring, and settles them in one click.
- **Tax Engine (`tax_engine.py`)**: Supports percentage taxes (e.g., 15% VAT), fixed per-unit taxes, price-included pricing, and cascading/compound taxes (`include_base_amount`).
- **Payments, Partial Reconciliation & Realized FX Gain/Loss (`reconciliation.py`)**: Tracks `amount_residual` & `payment_state` (`NOT_PAID`, `PARTIAL`, `PAID`) and auto-posts balanced Realized FX Gain/Loss entries when settlement exchange rates differ from invoice dates.
- **Real-Time Financial Reporting (`reports.py`)**: Trial Balance (ميزان المراجعة), Profit & Loss (قائمة الدخل), Balance Sheet (الميزانية العمومية — verifying $\text{Assets} == \text{Liabilities} + \text{Equity}$), General Ledger (دفتر الأستاذ العام) with drill-down, and Aged Receivable/Payable (`Current`, `1–30`, `31–60`, `61–90`, `90+`) with **CSV, XLSX, and PDF** export.

### 3. Inventory, Order-to-Cash & Procure-to-Pay
- **Double-Entry Stock Ledger (`backend/modules/inventory/`)**: Conserves stock units across physical & virtual locations; supports **STANDARD**, **AVCO** (dynamic weighted average), and **FIFO** (chronological cost layers) with automated perpetual GL entries.
- **Sales Cycle (`backend/modules/sales/`)**: `Quotation` → `Confirmed SO` → auto `Delivery Order` → `Customer Invoice` (`ORDERED` or `DELIVERED` policy) → `Payment & Reconciliation`.
- **Purchase Cycle (`backend/modules/purchase/`)**: `RFQ` → `Confirmed PO` → auto `Incoming Shipment` → `Vendor Bill` with **3-Way Matching** (`Ordered Qty` vs `Received Qty` vs `Billed Qty`) → `Payment & Reconciliation`.

### 4. Odoo-Inspired Bilingual Web UI & Production DevOps (`frontend/src/`, `.github/workflows/ci.yml`)
- **App Switcher, JWT Login / RBAC Role Switcher & Multi-Company Shell**: `Accounting` (incl. `Bank & Closing` CPA workspace), `Invoicing`, `Sales`, `Purchase`, `Inventory`, `Contacts`, `Configuration` (CoA, FX Rates, Lock Dates, Modules), plus live **English (LTR) / Arabic (RTL)** toggle.
- **Production Packaging & CI**: Multi-stage Nginx frontend container (`frontend/Dockerfile`), JWT Access + Refresh token rotation with `ALLOW_DEMO_FALLBACK=false` strict production mode, and GitHub Actions CI running Alembic migrations, PostgreSQL 16 trigger verification, `pytest` (15 tests), and Vite production builds.

---

## Quick Start (Docker Compose — PostgreSQL 16 + FastAPI + React)

```bash
docker compose up --build
```
- **Web UI**: `http://localhost:5173`
- **FastAPI OpenAPI Docs**: `http://localhost:8000/docs`
- **Demo Accounts** (seeded idempotently by `scripts/seed_erp.py`):
  - `admin` / `admin123` (`System Admin`, `Accountant`)
  - `accountant` / `account123` (`Accountant`)
  - `sales` / `sales123` (`Sales User`)

---

## Local Development (Without Docker)

### Backend
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# Point to PostgreSQL (or SQLite for quick local runs)
export DATABASE_URL="sqlite+aiosqlite:///./nizamo_dev.db"
python scripts/seed_erp.py
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

---

## Running the Automated Test Suite

```bash
pytest -v tests/test_accounting_engine.py
```

The test suite rigorously verifies:
1. Topological module dependency resolution (`__manifest__.py`)
2. Unbalanced journal entry rejection (`UnbalancedMoveError`)
3. Posted entry immutability & automated reversal workflow (`PostedImmutabilityError`)
4. Fiscal & tax lock date enforcement (`FiscalLockDateError`)
5. Pure Decimal tax computation (percent, fixed, price-included, compound)
6. Partial & full payment reconciliation (`NOT_PAID` → `PARTIAL` → `PAID`)
7. Foreign currency Realized FX Gain & Loss auto-posting
8. FIFO & AVCO perpetual inventory valuation & unit conservation
9. End-to-end P2P (with 3-Way Matching), O2C, Balance Sheet equilibrium ($\text{Assets} == \text{Liabilities} + \text{Equity}$), and CSV/XLSX/PDF exports

---

## Repository Layout

```text
├── docker-compose.yml
├── ARCHITECTURE.md
├── README.md
├── pytest.ini
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── alembic.ini & alembic/
│   ├── main.py
│   ├── core/              # registry.py, tenancy.py, sequences.py, security.py, chatter.py, database.py
│   └── modules/
│       ├── accounting/    # models.py, posting_engine.py, reconciliation.py, tax_engine.py, reports.py, router.py
│       ├── inventory/     # models.py, valuation_engine.py, router.py
│       ├── sales/         # models.py, workflows.py, router.py
│       └── purchase/      # models.py, workflows.py, router.py
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│       ├── components/    # AppSwitcher.tsx, FormView.tsx, ListView.tsx, InvoiceEditor.tsx,
│       │                  # FinancialReports.tsx, ChatterPanel.tsx, AccountingDashboard.tsx,
│       │                  # OrdersWorkspace.tsx, InventoryWorkspace.tsx, ContactsAndConfig.tsx
│       ├── api/client.ts
│       ├── i18n/translations.ts
│       └── types/erp.ts
├── scripts/
│   └── seed_erp.py
└── tests/
    └── test_accounting_engine.py
```
