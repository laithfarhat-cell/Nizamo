import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Coins, Cpu, Lock, Percent, Plus, Users } from "lucide-react";
import { apiRequest } from "../api/client";
import type {
  Account,
  Company,
  Currency,
  InstalledModule,
  Locale,
  Partner,
  TaxRule,
} from "../types/erp";
import { t } from "../i18n/translations";

export const ContactsView: React.FC<{ locale: Locale }> = ({ locale }) => {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [nameAr, setNameAr] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [vat, setVat] = useState("");
  const [isCustomer, setIsCustomer] = useState(true);
  const [isSupplier, setIsSupplier] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const { data: partners = [] } = useQuery<Partner[]>({
    queryKey: ["partners"],
    queryFn: () => apiRequest<Partner[]>("/core/partners"),
  });

  const createPartner = useMutation({
    mutationFn: () =>
      apiRequest("/core/partners", {
        method: "POST",
        body: JSON.stringify({
          name,
          name_ar: nameAr || null,
          email: email || null,
          phone: phone || null,
          vat: vat || null,
          is_customer: isCustomer,
          is_supplier: isSupplier,
        }),
      }),
    onSuccess: () => {
      setName("");
      setNameAr("");
      setEmail("");
      setPhone("");
      setVat("");
      setShowForm(false);
      setErr(null);
      qc.invalidateQueries({ queryKey: ["partners"] });
    },
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div>
          <h1 className="text-base font-bold text-slate-900 flex items-center gap-2">
            <Users className="h-4 w-4 text-odoo-purple" />
            {t(locale, "contacts")}
          </h1>
          <p className="text-xs text-slate-500">Company-scoped Customers & Vendors (`res_partner`)</p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-odoo-purple px-3 py-1.5 text-xs font-semibold text-white hover:bg-odoo-purple/90"
        >
          <Plus className="h-3.5 w-3.5" />
          {locale === "ar" ? "جهة اتصال جديدة" : "New Contact"}
        </button>
      </div>

      {showForm && (
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm space-y-3">
          <h3 className="text-sm font-bold text-slate-800">
            {locale === "ar" ? "إضافة عميل أو مورد جديد" : "Create Customer / Vendor"}
          </h3>
          {err && <div className="text-xs text-rose-700 bg-rose-50 p-2 rounded">{err}</div>}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            <input
              placeholder="Name (EN)*"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="border border-slate-300 rounded px-2.5 py-1.5"
            />
            <input
              placeholder="Name (AR)"
              value={nameAr}
              onChange={(e) => setNameAr(e.target.value)}
              className="border border-slate-300 rounded px-2.5 py-1.5"
            />
            <input
              placeholder="VAT / Tax ID"
              value={vat}
              onChange={(e) => setVat(e.target.value)}
              className="border border-slate-300 rounded px-2.5 py-1.5"
            />
            <input
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="border border-slate-300 rounded px-2.5 py-1.5"
            />
            <input
              placeholder="Phone"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              className="border border-slate-300 rounded px-2.5 py-1.5"
            />
            <div className="flex items-center gap-4">
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={isCustomer}
                  onChange={(e) => setIsCustomer(e.target.checked)}
                />
                Customer
              </label>
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={isSupplier}
                  onChange={(e) => setIsSupplier(e.target.checked)}
                />
                Vendor
              </label>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              disabled={!name || createPartner.isPending}
              onClick={() => createPartner.mutate()}
              className="rounded-lg bg-odoo-purple px-4 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
            >
              {createPartner.isPending ? "Saving..." : "Save Contact"}
            </button>
            <button
              onClick={() => setShowForm(false)}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {partners.map((p) => (
          <div key={p.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="text-sm font-bold text-slate-900">
                  {locale === "ar" && p.name_ar ? p.name_ar : p.name}
                </h3>
                <div className="text-[11px] font-mono text-slate-400">{p.ref || `ID-${p.id}`}</div>
              </div>
              <div className="flex gap-1">
                {p.is_customer && (
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800">
                    Customer
                  </span>
                )}
                {p.is_supplier && (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-800">
                    Vendor
                  </span>
                )}
              </div>
            </div>
            <div className="mt-3 space-y-1 text-xs text-slate-600">
              {p.email && <div>Email: {p.email}</div>}
              {p.phone && <div>Phone: {p.phone}</div>}
              {p.vat && <div>Tax ID / VAT: {p.vat}</div>}
              {p.address && <div className="text-slate-400">{p.address}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export const ConfigurationView: React.FC<{ locale: Locale; activeCompany?: Company }> = ({
  locale,
  activeCompany,
}) => {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"coa" | "currencies" | "taxes" | "locks" | "modules">("coa");
  const [banner, setBanner] = useState<string | null>(null);

  // CoA creator state
  const [accCode, setAccCode] = useState("");
  const [accName, setAccName] = useState("");
  const [accNameAr, setAccNameAr] = useState("");
  const [accType, setAccType] = useState("expense");
  const [accReconcilable, setAccReconcilable] = useState(false);

  // FX rate update state
  const [fxCurrencyId, setFxCurrencyId] = useState<number>(0);
  const [fxDate, setFxDate] = useState(new Date().toISOString().slice(0, 10));
  const [fxRate, setFxRate] = useState("1.100000");

  // Lock dates state
  const [fiscalLock, setFiscalLock] = useState(activeCompany?.fiscalyear_lock_date || "");
  const [taxLock, setTaxLock] = useState(activeCompany?.tax_lock_date || "");

  const { data: accounts = [] } = useQuery<Account[]>({
    queryKey: ["accounts"],
    queryFn: () => apiRequest<Account[]>("/accounting/accounts"),
  });
  const { data: currencies = [] } = useQuery<Currency[]>({
    queryKey: ["currencies"],
    queryFn: () => apiRequest<Currency[]>("/core/currencies"),
  });
  const { data: taxes = [] } = useQuery<TaxRule[]>({
    queryKey: ["taxes"],
    queryFn: () => apiRequest<TaxRule[]>("/accounting/taxes"),
  });
  const { data: modules = [] } = useQuery<InstalledModule[]>({
    queryKey: ["modules"],
    queryFn: () => apiRequest<InstalledModule[]>("/core/modules"),
  });

  const createAccount = useMutation({
    mutationFn: () =>
      apiRequest("/accounting/accounts", {
        method: "POST",
        body: JSON.stringify({
          code: accCode,
          name: accName,
          name_ar: accNameAr || null,
          account_type: accType,
          reconcilable: accReconcilable,
        }),
      }),
    onSuccess: () => {
      setAccCode("");
      setAccName("");
      setAccNameAr("");
      setBanner("Account added to Chart of Accounts.");
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (e: Error) => setBanner(`Error: ${e.message}`),
  });

  const addFxRate = useMutation({
    mutationFn: () =>
      apiRequest("/core/currency-rates", {
        method: "POST",
        body: JSON.stringify({
          currency_id: fxCurrencyId || currencies[0]?.id,
          effective_date: fxDate,
          rate: fxRate,
        }),
      }),
    onSuccess: () => {
      setBanner("Historical FX rate recorded.");
      qc.invalidateQueries({ queryKey: ["currencies"] });
    },
    onError: (e: Error) => setBanner(`Error: ${e.message}`),
  });

  const saveLockDates = useMutation({
    mutationFn: () =>
      apiRequest(`/core/companies/${activeCompany?.id}/lock-dates`, {
        method: "PATCH",
        body: JSON.stringify({
          fiscalyear_lock_date: fiscalLock || null,
          tax_lock_date: taxLock || null,
        }),
      }),
    onSuccess: () => {
      setBanner("Fiscal & Tax Lock Dates saved.");
      qc.invalidateQueries({ queryKey: ["companies"] });
    },
    onError: (e: Error) => setBanner(`Error: ${e.message}`),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div>
          <h1 className="text-base font-bold text-slate-900">{t(locale, "configuration")}</h1>
          <p className="text-xs text-slate-500">
            Chart of Accounts, Historical Multi-Currency Rates, Fiscal Lock Dates & Module Registry
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {[
            { id: "coa", label: "Chart of Accounts", icon: BookOpen },
            { id: "currencies", label: "Currencies & FX", icon: Coins },
            { id: "taxes", label: "Tax Rules", icon: Percent },
            { id: "locks", label: t(locale, "lockDates"), icon: Lock },
            { id: "modules", label: "Module Registry", icon: Cpu },
          ].map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id as any)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                tab === id
                  ? "bg-odoo-purple text-white"
                  : "bg-slate-100 text-slate-700 hover:bg-slate-200"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {banner && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-xs font-medium text-slate-800">
          {banner}
        </div>
      )}

      {tab === "coa" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="text-xs font-bold uppercase text-slate-600 mb-2">Add Account to Chart of Accounts</h3>
            <div className="grid grid-cols-1 md:grid-cols-6 gap-2 text-xs">
              <input
                placeholder="Code (e.g. 540000)"
                value={accCode}
                onChange={(e) => setAccCode(e.target.value)}
                className="border border-slate-300 rounded px-2 py-1.5 font-mono"
              />
              <input
                placeholder="Account Name (EN)"
                value={accName}
                onChange={(e) => setAccName(e.target.value)}
                className="border border-slate-300 rounded px-2 py-1.5"
              />
              <input
                placeholder="Arabic Name"
                value={accNameAr}
                onChange={(e) => setAccNameAr(e.target.value)}
                className="border border-slate-300 rounded px-2 py-1.5"
              />
              <select
                value={accType}
                onChange={(e) => setAccType(e.target.value)}
                className="border border-slate-300 rounded px-2 py-1.5"
              >
                <option value="asset_current">asset_current</option>
                <option value="asset_receivable">asset_receivable</option>
                <option value="asset_cash">asset_cash</option>
                <option value="liability_payable">liability_payable</option>
                <option value="liability_current">liability_current</option>
                <option value="equity">equity</option>
                <option value="income">income</option>
                <option value="expense">expense</option>
                <option value="expense_direct_cost">expense_direct_cost</option>
              </select>
              <label className="flex items-center gap-1.5 px-2">
                <input
                  type="checkbox"
                  checked={accReconcilable}
                  onChange={(e) => setAccReconcilable(e.target.checked)}
                />
                Reconcilable
              </label>
              <button
                disabled={!accCode || !accName || createAccount.isPending}
                onClick={() => createAccount.mutate()}
                className="rounded-lg bg-odoo-purple px-3 py-1.5 font-semibold text-white disabled:opacity-50"
              >
                + Add Account
              </button>
            </div>
          </div>

          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full text-start text-xs">
              <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
                <tr>
                  <th className="px-3 py-2.5">Code</th>
                  <th className="px-3 py-2.5">Account Name</th>
                  <th className="px-3 py-2.5">Arabic Name</th>
                  <th className="px-3 py-2.5">IFRS / GAAP Type</th>
                  <th className="px-3 py-2.5">Reconcilable</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {accounts.map((a) => (
                  <tr key={a.id} className="hover:bg-slate-50">
                    <td className="px-3 py-2 font-mono font-bold text-odoo-purple">{a.code}</td>
                    <td className="px-3 py-2 font-medium text-slate-900">{a.name}</td>
                    <td className="px-3 py-2 text-slate-600">{a.name_ar || "—"}</td>
                    <td className="px-3 py-2">
                      <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-700">
                        {a.account_type}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      {a.reconcilable ? (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800">
                          YES
                        </span>
                      ) : (
                        <span className="text-slate-400">No</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "currencies" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="text-xs font-bold uppercase text-slate-600 mb-2">
              Record Historical FX Rate (1 Foreign = X Base)
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-2 text-xs">
              <select
                value={fxCurrencyId || currencies[0]?.id || ""}
                onChange={(e) => setFxCurrencyId(Number(e.target.value))}
                className="border border-slate-300 rounded px-2 py-1.5"
              >
                {currencies.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.code} — {c.name}
                  </option>
                ))}
              </select>
              <input
                type="date"
                value={fxDate}
                onChange={(e) => setFxDate(e.target.value)}
                className="border border-slate-300 rounded px-2 py-1.5"
              />
              <input
                value={fxRate}
                onChange={(e) => setFxRate(e.target.value)}
                placeholder="Rate (DECIMAL)"
                className="border border-slate-300 rounded px-2 py-1.5 font-mono"
              />
              <button
                onClick={() => addFxRate.mutate()}
                disabled={addFxRate.isPending}
                className="rounded-lg bg-odoo-purple px-3 py-1.5 font-semibold text-white"
              >
                Save FX Rate
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {currencies.map((c) => (
              <div key={c.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex items-center justify-between">
                  <div>
                    <span className="font-mono text-base font-bold text-odoo-purple">{c.code}</span>
                    <span className="ms-2 text-xs text-slate-600">{c.name}</span>
                  </div>
                  <span className="rounded-lg bg-slate-100 px-2.5 py-1 text-xs font-bold tabular-nums">
                    1 {c.code} = {c.latest_rate} Base
                  </span>
                </div>
                <div className="mt-3 text-xs text-slate-500">
                  Precision: {c.decimal_places} decimals · Symbol: {c.symbol}
                </div>
                {c.rates.length > 0 && (
                  <div className="mt-3 divide-y divide-slate-100 rounded-lg border border-slate-100 text-xs tabular-nums">
                    {c.rates.map((r) => (
                      <div key={r.id} className="flex justify-between px-3 py-1.5">
                        <span className="text-slate-500">Effective {r.effective_date}</span>
                        <span className="font-semibold text-slate-800">{r.rate}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "locks" && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm max-w-xl space-y-4">
          <div>
            <h3 className="text-sm font-bold text-slate-900">
              {t(locale, "lockDates")} — {activeCompany?.name}
            </h3>
            <p className="text-xs text-slate-500 mt-1">
              Posting or cancelling any journal entry on or before a Lock Date is strictly blocked by
              the Accounting Engine (HTTP 422).
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Fiscal Year Lock Date</label>
              <input
                type="date"
                value={fiscalLock}
                onChange={(e) => setFiscalLock(e.target.value)}
                className="w-full border border-slate-300 rounded px-3 py-1.5"
              />
            </div>
            <div>
              <label className="block font-semibold text-slate-700 mb-1">Tax Period Lock Date</label>
              <input
                type="date"
                value={taxLock}
                onChange={(e) => setTaxLock(e.target.value)}
                className="w-full border border-slate-300 rounded px-3 py-1.5"
              />
            </div>
          </div>
          <button
            disabled={!activeCompany || saveLockDates.isPending}
            onClick={() => saveLockDates.mutate()}
            className="rounded-lg bg-odoo-purple px-4 py-2 text-xs font-semibold text-white"
          >
            {saveLockDates.isPending ? "Saving..." : "Update Lock Dates"}
          </button>
        </div>
      )}

      {tab === "taxes" && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-start text-xs">
            <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2.5">Tax Name</th>
                <th className="px-3 py-2.5">Scope</th>
                <th className="px-3 py-2.5">Method</th>
                <th className="px-3 py-2.5 text-end">Rate / Amount</th>
                <th className="px-3 py-2.5">Price Included</th>
                <th className="px-3 py-2.5">Compound</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {taxes.map((tx) => (
                <tr key={tx.id}>
                  <td className="px-3 py-2.5 font-semibold text-slate-900">{tx.name}</td>
                  <td className="px-3 py-2.5">{tx.tax_type}</td>
                  <td className="px-3 py-2.5 font-mono">{tx.amount_type}</td>
                  <td className="px-3 py-2.5 text-end font-mono font-bold">
                    {tx.amount_type === "PERCENT" ? `${tx.amount}%` : tx.amount}
                  </td>
                  <td className="px-3 py-2.5">{tx.price_include ? "Included" : "Excluded"}</td>
                  <td className="px-3 py-2.5">{tx.include_base_amount ? "Yes (Cascading)" : "No"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "modules" && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {modules.map((m) => (
            <div key={m.technical_name} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between">
                <span className="font-mono text-sm font-bold text-odoo-purple">{m.technical_name}</span>
                <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-[10px] font-bold text-emerald-800">
                  v{m.version} · INSTALLED
                </span>
              </div>
              <div className="mt-1 text-xs font-semibold text-slate-800">{m.name}</div>
              <p className="mt-1 text-xs text-slate-500">{m.summary}</p>
              <div className="mt-2 text-[11px] text-slate-400">
                Depends: {m.depends.length > 0 ? m.depends.join(", ") : "core"}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
