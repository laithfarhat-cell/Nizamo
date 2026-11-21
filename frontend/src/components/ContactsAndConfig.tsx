import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Coins, Cpu, Percent, Users } from "lucide-react";
import { apiRequest } from "../api/client";
import type { Account, Currency, InstalledModule, Locale, Partner, TaxRule } from "../types/erp";
import { t } from "../i18n/translations";

export const ContactsView: React.FC<{ locale: Locale }> = ({ locale }) => {
  const { data: partners = [] } = useQuery<Partner[]>({
    queryKey: ["partners"],
    queryFn: () => apiRequest<Partner[]>("/core/partners"),
  });

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <h1 className="text-base font-bold text-slate-900 flex items-center gap-2">
          <Users className="h-4 w-4 text-odoo-purple" />
          {t(locale, "contacts")}
        </h1>
        <p className="text-xs text-slate-500">Company-scoped Customers & Vendors (`res_partner`)</p>
      </div>
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

export const ConfigurationView: React.FC<{ locale: Locale }> = ({ locale }) => {
  const [tab, setTab] = useState<"coa" | "currencies" | "taxes" | "modules">("coa");

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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div>
          <h1 className="text-base font-bold text-slate-900">{t(locale, "configuration")}</h1>
          <p className="text-xs text-slate-500">
            Chart of Accounts, Historical Multi-Currency Rates, Tax Rules & Modular ERP Kernel Registry
          </p>
        </div>
        <div className="flex gap-1.5">
          {[
            { id: "coa", label: "Chart of Accounts", icon: BookOpen },
            { id: "currencies", label: "Currencies & FX", icon: Coins },
            { id: "taxes", label: "Tax Rules", icon: Percent },
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

      {tab === "coa" && (
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
      )}

      {tab === "currencies" && (
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
                  <td className="px-3 py-2.5 text-end font-bold tabular-nums">
                    {tx.amount}
                    {tx.amount_type === "PERCENT" ? "%" : ""}
                  </td>
                  <td className="px-3 py-2.5">{tx.price_include ? "Yes" : "No"}</td>
                  <td className="px-3 py-2.5">{tx.include_base_amount ? "Yes" : "No"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "modules" && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {modules.map((m) => (
            <div key={m.technical_name} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-start justify-between">
                <div>
                  <span className="rounded bg-purple-100 px-2 py-0.5 font-mono text-[10px] font-bold text-odoo-purple">
                    Load Order #{m.load_rank} · {m.technical_name}
                  </span>
                  <h3 className="mt-1.5 text-sm font-bold text-slate-900">{m.name}</h3>
                </div>
                <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-[10px] font-bold text-emerald-800">
                  v{m.version} · Installed
                </span>
              </div>
              <p className="mt-2 text-xs text-slate-600">{m.summary}</p>
              <div className="mt-3 text-[11px] text-slate-400">
                Depends on:{" "}
                <span className="font-mono text-slate-700">
                  {m.depends.length > 0 ? m.depends.join(", ") : "core_kernel"}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
