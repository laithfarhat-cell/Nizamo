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
- **Strict Ledger Invariants**: Rejects unbalanced entries ($\sum \text{debit} \ne \sum \text{credit}$), blocks edits/deletes on `POSTED` entries (requiring automated Reversal Entries / Credit Notes), and enforces `fiscalyear_lock_date` & `tax_lock_date`.
- **Tax Engine (`tax_engine.py`)**: Supports percentage taxes (e.g., 15% VAT), fixed per-unit taxes, price-included pricing, and cascading/compound taxes (`include_base_amount`).
- **Payments, Partial Reconciliation & Realized FX Gain/Loss (`reconciliation.py`)**: Tracks `amount_residual` & `payment_state` (`NOT_PAID`, `PARTIAL`, `PAID`) and auto-posts balanced Realized FX Gain/Loss entries when settlement exchange rates differ from invoice dates.
- **Real-Time Financial Reporting (`reports.py`)**: Trial Balance (ميزان المراجعة), Profit & Loss (قائمة الدخل), Balance Sheet (الميزانية العمومية — verifying $\text{Assets} == \text{Liabilities} + \text{Equity}$), General Ledger (دفتر الأستاذ العام) with drill-down, and Aged Receivable/Payable (`Current`, `1–30`, `31–60`, `61–90`, `90+`) with **CSV, XLSX, and PDF** export.

### 3. Inventory, Order-to-Cash & Procure-to-Pay
- **Double-Entry Stock Ledger (`backend/modules/inventory/`)**: Conserves stock units across physical & virtual locations; supports **STANDARD**, **AVCO** (dynamic weighted average), and **FIFO** (chronological cost layers) with automated perpetual GL entries.
- **Sales Cycle (`backend/modules/sales/`)**: `Quotation` → `Confirmed SO` → auto `Delivery Order` → `Customer Invoice` (`ORDERED` or `DELIVERED` policy) → `Payment & Reconciliation`.
- **Purchase Cycle (`backend/modules/purchase/`)**: `RFQ` → `Confirmed PO` → auto `Incoming Shipment` → `Vendor Bill` with **3-Way Matching** (`Ordered Qty` vs `Received Qty` vs `Billed Qty`) → `Payment & Reconciliation`.

### 4. Odoo-Inspired Bilingual Web UI (`frontend/src/`)
- **App Switcher & Multi-Company Shell**: `Accounting`, `Invoicing`, `Sales`, `Purchase`, `Inventory`, `Contacts`, `Configuration`, plus live **English (LTR) / Arabic (RTL)** toggle.
- **Unified Multi-View UX**: High-density `TanStack Table` List View (search, filter chips, `Group By`, column sum footers, bulk actions), Form View with Statusbar Pipeline, live Smart Buttons, inline Invoice/Journal Editor, Accounting Journal Cards Dashboard, and Audit Chatter timeline.

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
