import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { apiRequest, formatMoney, setActiveCompanyId } from "./api/client";
import type { AccountMove, AppKey, Company, Locale, UserInfo } from "./types/erp";
import { t } from "./i18n/translations";
import { AppSwitcher } from "./components/AppSwitcher";
import { AccountingDashboard } from "./components/AccountingDashboard";
import { ListView } from "./components/ListView";
import { InvoiceEditor } from "./components/InvoiceEditor";
import { FinancialReports } from "./components/FinancialReports";
import { OrdersWorkspace } from "./components/OrdersWorkspace";
import { InventoryWorkspace } from "./components/InventoryWorkspace";
import { ConfigurationView, ContactsView } from "./components/ContactsAndConfig";
import { LoginModal } from "./components/LoginModal";
import { PeriodClosingAndBank } from "./components/PeriodClosingAndBank";

type AccountingSubView = "dashboard" | "moves" | "invoices" | "bills" | "reports" | "bank_closing";

export const App: React.FC = () => {
  const queryClient = useQueryClient();
  const [activeApp, setActiveApp] = useState<AppKey>("accounting");
  const [acctView, setAcctView] = useState<AccountingSubView>("dashboard");
  const [locale, setLocale] = useState<Locale>("en");
  const [companyId, setCompanyId] = useState<number | null>(null);
  const [loginOpen, setLoginOpen] = useState(false);

  // Selected/Creating AccountMove state
  const [editingMoveId, setEditingMoveId] = useState<number | null | undefined>(undefined);
  const [newMoveType, setNewMoveType] = useState<AccountMove["move_type"]>("out_invoice");
  const [journalFilterId, setJournalFilterId] = useState<number | null>(null);

  // Sync HTML dir & lang attributes for authentic LTR / RTL rendering
  useEffect(() => {
    document.documentElement.dir = locale === "ar" ? "rtl" : "ltr";
    document.documentElement.lang = locale;
  }, [locale]);

  const { data: user = null } = useQuery<UserInfo>({
    queryKey: ["me"],
    queryFn: () => apiRequest<UserInfo>("/core/auth/me"),
  });

  const { data: companies = [] } = useQuery<Company[]>({
    queryKey: ["companies"],
    queryFn: () => apiRequest<Company[]>("/core/companies"),
  });

  useEffect(() => {
    if (companyId === null && companies.length > 0) {
      const initial = user?.active_company_id ?? companies[0].id;
      setCompanyId(initial);
      setActiveCompanyId(initial);
    }
  }, [companies, user, companyId]);

  const handleChangeCompany = (nextId: number) => {
    setCompanyId(nextId);
    setActiveCompanyId(nextId);
    setEditingMoveId(undefined);
    setJournalFilterId(null);
    queryClient.invalidateQueries();
  };

  // Moves query for Accounting & Invoicing apps
  const movesQueryPath = useMemo(() => {
    const params = new URLSearchParams();
    if (activeApp === "invoicing" || acctView === "invoices") {
      params.set("move_type", "out_invoice,out_refund");
    } else if (acctView === "bills") {
      params.set("move_type", "in_invoice,in_refund");
    } else if (journalFilterId) {
      params.set("journal_id", String(journalFilterId));
    }
    const qs = params.toString();
    return `/accounting/moves${qs ? `?${qs}` : ""}`;
  }, [activeApp, acctView, journalFilterId]);

  const { data: moves = [], isLoading: movesLoading } = useQuery<AccountMove[]>({
    queryKey: ["moves", movesQueryPath, companyId],
    queryFn: () => apiRequest<AccountMove[]>(movesQueryPath),
    enabled: activeApp === "accounting" || activeApp === "invoicing",
  });

  const bulkPostMutation = useMutation({
    mutationFn: async (rows: AccountMove[]) => {
      for (const r of rows) {
        if (r.state === "DRAFT") {
          await apiRequest(`/accounting/moves/${r.id}/post`, { method: "POST" });
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["moves"] });
      queryClient.invalidateQueries({ queryKey: ["accounting-dashboard"] });
    },
  });

  const bulkCancelMutation = useMutation({
    mutationFn: async (rows: AccountMove[]) => {
      for (const r of rows) {
        if (r.state === "DRAFT") {
          await apiRequest(`/accounting/moves/${r.id}/cancel`, { method: "POST" });
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["moves"] });
      queryClient.invalidateQueries({ queryKey: ["accounting-dashboard"] });
    },
  });

  const moveColumns = useMemo<ColumnDef<AccountMove>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Number",
        cell: (info) => <span className="font-bold text-odoo-purple">{String(info.getValue())}</span>,
      },
      { accessorKey: "date", header: "Date" },
      { accessorKey: "partner_name", header: "Partner", cell: (info) => String(info.getValue() || "—") },
      {
        accessorKey: "journal_code",
        header: "Journal",
        cell: (info) => (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] font-bold text-slate-600">
            {String(info.getValue())}
          </span>
        ),
      },
      {
        accessorKey: "move_type",
        header: "Type",
        cell: (info) => <span className="text-slate-600">{String(info.getValue())}</span>,
      },
      {
        accessorKey: "amount_total",
        header: "Total",
        cell: (info) => (
          <span className="font-semibold tabular-nums">
            {formatMoney(String(info.getValue()), info.row.original.currency_symbol)}
          </span>
        ),
      },
      {
        accessorKey: "amount_residual",
        header: "Amount Due",
        cell: (info) => (
          <span className="tabular-nums text-slate-600">
            {formatMoney(String(info.getValue()), info.row.original.currency_symbol)}
          </span>
        ),
      },
      {
        accessorKey: "payment_state",
        header: "Payment",
        cell: (info) => {
          const st = String(info.getValue());
          if (info.row.original.move_type === "entry") return <span className="text-slate-400">—</span>;
          const cls =
            st === "PAID"
              ? "bg-emerald-100 text-emerald-800"
              : st === "PARTIAL"
              ? "bg-amber-100 text-amber-800"
              : "bg-slate-100 text-slate-600";
          return <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${cls}`}>{st}</span>;
        },
      },
      {
        accessorKey: "state",
        header: "Status",
        cell: (info) => {
          const st = String(info.getValue());
          const cls =
            st === "POSTED"
              ? "bg-purple-100 text-odoo-purple"
              : st === "CANCELLED"
              ? "bg-rose-100 text-rose-700"
              : "bg-slate-100 text-slate-700";
          return <span className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold ${cls}`}>{st}</span>;
        },
      },
    ],
    []
  );

  const openNewMove = (mtype: AccountMove["move_type"]) => {
    setNewMoveType(mtype);
    setEditingMoveId(null);
  };

  const openExistingMove = (id: number) => {
    setActiveApp("accounting");
    setEditingMoveId(id);
  };

  const activeCompany = companies.find((c) => c.id === companyId) ?? companies[0];

  return (
    <div className="min-h-screen bg-slate-50">
      <AppSwitcher
        activeApp={activeApp}
        onSelectApp={(app) => {
          setActiveApp(app);
          setEditingMoveId(undefined);
          setJournalFilterId(null);
          if (app === "accounting") setAcctView("dashboard");
        }}
        companies={companies}
        activeCompanyId={companyId}
        onChangeCompany={handleChangeCompany}
        locale={locale}
        onToggleLocale={() => setLocale((l) => (l === "en" ? "ar" : "en"))}
        user={user}
        onOpenLogin={() => setLoginOpen(true)}
      />

      {loginOpen && (
        <LoginModal
          locale={locale}
          currentUser={user}
          onClose={() => setLoginOpen(false)}
          onSuccess={() => {
            setCompanyId(null);
            queryClient.invalidateQueries();
          }}
        />
      )}

      {/* Accounting Secondary Sub-Navigation Bar */}
      {activeApp === "accounting" && (
        <div className="border-b border-slate-200 bg-white">
          <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-1 px-4 py-2 text-xs">
            {(
              [
                { id: "dashboard", label: t(locale, "dashboard") },
                { id: "invoices", label: t(locale, "customerInvoices") },
                { id: "bills", label: t(locale, "vendorBills") },
                { id: "moves", label: t(locale, "journalEntries") },
                { id: "bank_closing", label: t(locale, "bankAndClosing") },
                { id: "reports", label: t(locale, "reports") },
              ] as { id: AccountingSubView; label: string }[]
            ).map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => {
                  setAcctView(item.id);
                  setEditingMoveId(undefined);
                  setJournalFilterId(null);
                }}
                className={`rounded-lg px-3 py-1.5 font-semibold transition ${
                  acctView === item.id && editingMoveId === undefined
                    ? "bg-purple-50 text-odoo-purple"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Main Content Shell */}
      <main className="mx-auto max-w-[1600px] px-4 py-6">
        {/* ACCOUNTING & INVOICING APPS */}
        {(activeApp === "accounting" || activeApp === "invoicing") && (
          <>
            {editingMoveId !== undefined ? (
              <InvoiceEditor
                moveId={editingMoveId}
                defaultMoveType={newMoveType}
                locale={locale}
                onBack={() => setEditingMoveId(undefined)}
                onSelectMove={(id) => setEditingMoveId(id)}
              />
            ) : activeApp === "accounting" && acctView === "dashboard" ? (
              <AccountingDashboard
                locale={locale}
                onQuickCreate={(mtype) => openNewMove(mtype)}
                onOpenJournalMoves={(jid) => {
                  setJournalFilterId(jid);
                  setAcctView("moves");
                }}
                onOpenReports={() => setAcctView("reports")}
              />
            ) : activeApp === "accounting" && acctView === "bank_closing" ? (
              <PeriodClosingAndBank
                locale={locale}
                currencySymbol={activeCompany?.currency_symbol || "$"}
              />
            ) : activeApp === "accounting" && acctView === "reports" ? (
              <FinancialReports locale={locale} onOpenMove={(id) => openExistingMove(id)} />
            ) : (
              <ListView
                title={
                  activeApp === "invoicing" || acctView === "invoices"
                    ? t(locale, "customerInvoices")
                    : acctView === "bills"
                    ? t(locale, "vendorBills")
                    : t(locale, "journalEntries")
                }
                subtitle="Unified Double-Entry Ledger (`account_move` & `account_move_line`)"
                data={moves}
                columns={moveColumns}
                locale={locale}
                isLoading={movesLoading}
                searchFields={["name", "partner_name", "ref", "journal_code", "invoice_origin"]}
                sumFields={["amount_total", "amount_residual"]}
                filterChips={[
                  { id: "draft", label: t(locale, "draft"), predicate: (r) => r.state === "DRAFT" },
                  { id: "posted", label: t(locale, "posted"), predicate: (r) => r.state === "POSTED" },
                  { id: "unpaid", label: t(locale, "notPaid"), predicate: (r) => r.payment_state === "NOT_PAID" && r.move_type !== "entry" },
                  { id: "partial", label: t(locale, "partial"), predicate: (r) => r.payment_state === "PARTIAL" },
                  { id: "paid", label: t(locale, "paid"), predicate: (r) => r.payment_state === "PAID" },
                ]}
                groupByOptions={[
                  { label: "Status", field: "state" },
                  { label: "Journal", field: "journal_name" },
                  { label: "Partner", field: "partner_name" },
                  { label: "Payment Status", field: "payment_state" },
                ]}
                bulkActions={[
                  {
                    id: "post",
                    label: t(locale, "post"),
                    variant: "primary",
                    onClick: (rows) => bulkPostMutation.mutate(rows),
                  },
                  {
                    id: "cancel",
                    label: t(locale, "cancel"),
                    variant: "danger",
                    onClick: (rows) => bulkCancelMutation.mutate(rows),
                  },
                ]}
                onRowClick={(r) => setEditingMoveId(r.id)}
                onCreateNew={() =>
                  openNewMove(
                    acctView === "bills"
                      ? "in_invoice"
                      : acctView === "moves"
                      ? "entry"
                      : "out_invoice"
                  )
                }
              />
            )}
          </>
        )}

        {/* SALES APP */}
        {activeApp === "sales" && (
          <OrdersWorkspace
            mode="sales"
            locale={locale}
            onJumpToInvoice={(id) => openExistingMove(id)}
            onJumpToInventory={() => setActiveApp("inventory")}
          />
        )}

        {/* PURCHASE APP */}
        {activeApp === "purchase" && (
          <OrdersWorkspace
            mode="purchase"
            locale={locale}
            onJumpToInvoice={(id) => openExistingMove(id)}
            onJumpToInventory={() => setActiveApp("inventory")}
          />
        )}

        {/* INVENTORY APP */}
        {activeApp === "inventory" && <InventoryWorkspace locale={locale} />}

        {/* CONTACTS APP */}
        {activeApp === "contacts" && <ContactsView locale={locale} />}

        {/* CONFIGURATION APP */}
        {activeApp === "configuration" && (
          <ConfigurationView locale={locale} activeCompany={activeCompany} />
        )}
      </main>
    </div>
  );
};

export default App;
