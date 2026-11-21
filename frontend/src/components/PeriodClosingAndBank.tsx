import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Globe2,
  Landmark,
  Lock,
  RefreshCw,
  Upload,
} from "lucide-react";
import { apiRequest, formatMoney } from "../api/client";
import type { Account, Journal, Locale } from "../types/erp";

interface PeriodClosingAndBankProps {
  locale: Locale;
  currencySymbol: string;
}

interface BankStatementSuggestion {
  move_id: number;
  move_name: string;
  partner_name: string | null;
  amount_residual: string;
  confidence: number;
}

interface BankStatementLineDto {
  id: number;
  date: string;
  payment_ref: string;
  partner_name: string | null;
  amount: string;
  is_reconciled: boolean;
  payment_id: number | null;
  move_id: number | null;
  suggestions: BankStatementSuggestion[];
}

interface BankStatementDto {
  id: number;
  name: string;
  journal_id: number;
  date: string;
  balance_start: string;
  balance_end_real: string;
  state: string;
  reconciled_count: number;
  total_lines: number;
  lines: BankStatementLineDto[];
}

interface FiscalClosingPreview {
  date_from: string;
  date_to: string;
  accounts_count: number;
  net_profit: string;
  retained_earnings_account_id: number;
  retained_earnings_account_label: string;
  accounts: Array<{
    account_id: number;
    code: string;
    name: string;
    account_type: string;
    closing_debit: string;
    closing_credit: string;
  }>;
}

interface FxRevalPreview {
  revaluation_date: string;
  items_count: number;
  net_unrealized_fx: string;
  exposures: Array<{
    line_id: number;
    account_code: string;
    account_name: string;
    currency_code: string;
    open_foreign_amount: string;
    booked_base_amount: string;
    closing_rate: string;
    revalued_base_amount: string;
    unrealized_gain_loss: string;
  }>;
}

const SAMPLE_BANK_CSV = `date,ref,partner,amount
2026-11-20,WIRE-PAYMENT-ACME,Acme Retail Group,2500.00
2026-11-21,BANK-SERVICE-FEE,First National Bank,-35.00`;

export const PeriodClosingAndBank: React.FC<PeriodClosingAndBankProps> = ({
  locale,
  currencySymbol,
}) => {
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<"bank" | "closing" | "fx">("bank");
  const [banner, setBanner] = useState<{ type: "ok" | "err"; msg: string } | null>(null);
  const [busy, setBusy] = useState(false);

  // Bank import state
  const [stmtName, setStmtName] = useState(`BNK/STMT/${new Date().toISOString().slice(0, 7)}`);
  const [csvText, setCsvText] = useState(SAMPLE_BANK_CSV);
  const [selectedJournalId, setSelectedJournalId] = useState<number>(0);
  const [offsetAccountByLine, setOffsetAccountByLine] = useState<Record<number, number>>({});

  // Fiscal closing state
  const [closeFrom, setCloseFrom] = useState("2026-01-01");
  const [closeTo, setCloseTo] = useState("2026-12-31");
  const [lockYear, setLockYear] = useState(true);

  // FX Revaluation state
  const [revalDate, setRevalDate] = useState(new Date().toISOString().slice(0, 10));
  const [autoReverse, setAutoReverse] = useState(true);

  const { data: journals = [] } = useQuery<Journal[]>({
    queryKey: ["journals"],
    queryFn: () => apiRequest<Journal[]>("/accounting/journals"),
  });
  const { data: accounts = [] } = useQuery<Account[]>({
    queryKey: ["accounts"],
    queryFn: () => apiRequest<Account[]>("/accounting/accounts"),
  });
  const { data: statements = [] } = useQuery<BankStatementDto[]>({
    queryKey: ["bank-statements"],
    queryFn: () => apiRequest<BankStatementDto[]>("/accounting/bank-statements"),
  });
  const { data: closingPreview, refetch: refetchClosing } = useQuery<FiscalClosingPreview>({
    queryKey: ["closing-preview", closeFrom, closeTo],
    queryFn: () =>
      apiRequest<FiscalClosingPreview>(
        `/accounting/fiscal-closing/preview?date_from=${closeFrom}&date_to=${closeTo}`
      ),
    enabled: activeTab === "closing",
  });
  const { data: closingHistory = [] } = useQuery<
    Array<{ id: number; name: string; date_from: string; date_to: string; net_profit: string }>
  >({
    queryKey: ["closing-history"],
    queryFn: () => apiRequest("/accounting/fiscal-closing/history"),
    enabled: activeTab === "closing",
  });
  const { data: fxPreview, refetch: refetchFx } = useQuery<FxRevalPreview>({
    queryKey: ["fx-preview", revalDate],
    queryFn: () =>
      apiRequest<FxRevalPreview>(
        `/accounting/fx-revaluation/preview?revaluation_date=${revalDate}`
      ),
    enabled: activeTab === "fx",
  });

  const bankJournals = journals.filter((j) => j.journal_type === "BANK" || j.journal_type === "CASH");
  const activeBankJournalId = selectedJournalId || bankJournals[0]?.id || 0;
  const expenseAccounts = accounts.filter(
    (a) => a.account_type.startsWith("expense") || a.account_type.startsWith("income")
  );

  const handleImportCsv = async () => {
    if (!activeBankJournalId) {
      setBanner({ type: "err", msg: "No Bank journal found." });
      return;
    }
    setBusy(true);
    setBanner(null);
    try {
      await apiRequest("/accounting/bank-statements/import-csv", {
        method: "POST",
        body: JSON.stringify({
          journal_id: activeBankJournalId,
          name: stmtName,
          csv_text: csvText,
        }),
      });
      await qc.invalidateQueries({ queryKey: ["bank-statements"] });
      setBanner({ type: "ok", msg: "Bank statement imported & auto-matched against open invoices." });
    } catch (e: unknown) {
      setBanner({ type: "err", msg: e instanceof Error ? e.message : "Import failed" });
    } finally {
      setBusy(false);
    }
  };

  const handleReconcileLine = async (
    lineId: number,
    invoiceMoveId?: number,
    counterpartAccountId?: number
  ) => {
    setBusy(true);
    setBanner(null);
    try {
      await apiRequest(`/accounting/bank-statements/lines/${lineId}/reconcile`, {
        method: "POST",
        body: JSON.stringify({
          invoice_move_id: invoiceMoveId ?? null,
          counterpart_account_id: counterpartAccountId ?? null,
        }),
      });
      await qc.invalidateQueries({ queryKey: ["bank-statements"] });
      await qc.invalidateQueries({ queryKey: ["moves"] });
      await qc.invalidateQueries({ queryKey: ["dashboard"] });
      setBanner({ type: "ok", msg: "Bank line reconciled & posted to General Ledger." });
    } catch (e: unknown) {
      setBanner({ type: "err", msg: e instanceof Error ? e.message : "Reconciliation failed" });
    } finally {
      setBusy(false);
    }
  };

  const handleExecuteClosing = async () => {
    setBusy(true);
    setBanner(null);
    try {
      const res = await apiRequest<{ closing_move_name: string; net_profit: string }>(
        "/accounting/fiscal-closing",
        {
          method: "POST",
          body: JSON.stringify({
            date_from: closeFrom,
            date_to: closeTo,
            lock_fiscal_year: lockYear,
          }),
        }
      );
      await qc.invalidateQueries({ queryKey: ["closing-preview"] });
      await qc.invalidateQueries({ queryKey: ["closing-history"] });
      await qc.invalidateQueries({ queryKey: ["moves"] });
      await qc.invalidateQueries({ queryKey: ["companies"] });
      setBanner({
        type: "ok",
        msg: `Fiscal year closed! Posted ${res.closing_move_name} (Net Profit ${formatMoney(
          res.net_profit,
          currencySymbol
        )}).`,
      });
    } catch (e: unknown) {
      setBanner({ type: "err", msg: e instanceof Error ? e.message : "Closing failed" });
    } finally {
      setBusy(false);
    }
  };

  const handleExecuteFx = async () => {
    setBusy(true);
    setBanner(null);
    try {
      const res = await apiRequest<{
        revaluation_move_name: string;
        reversal_move_name: string | null;
        net_unrealized_fx: string;
      }>("/accounting/fx-revaluation", {
        method: "POST",
        body: JSON.stringify({
          revaluation_date: revalDate,
          auto_reverse_next_day: autoReverse,
        }),
      });
      await qc.invalidateQueries({ queryKey: ["fx-preview"] });
      await qc.invalidateQueries({ queryKey: ["moves"] });
      setBanner({
        type: "ok",
        msg: `FX Revaluation posted (${res.revaluation_move_name})${
          res.reversal_move_name ? ` with next-day reversal (${res.reversal_move_name})` : ""
        }.`,
      });
    } catch (e: unknown) {
      setBanner({ type: "err", msg: e instanceof Error ? e.message : "FX Revaluation failed" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 bg-white border border-slate-200 rounded-lg p-1 shadow-sm">
          <button
            onClick={() => setActiveTab("bank")}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium ${
              activeTab === "bank" ? "bg-odoo-purple text-white" : "text-slate-700 hover:bg-slate-100"
            }`}
          >
            <Landmark className="w-3.5 h-3.5" />
            {locale === "ar" ? "التسوية البنكية (CSV)" : "Bank Reconciliation"}
          </button>
          <button
            onClick={() => setActiveTab("closing")}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium ${
              activeTab === "closing" ? "bg-odoo-purple text-white" : "text-slate-700 hover:bg-slate-100"
            }`}
          >
            <Lock className="w-3.5 h-3.5" />
            {locale === "ar" ? "إقفال السنة المالية" : "Year-End Closing"}
          </button>
          <button
            onClick={() => setActiveTab("fx")}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium ${
              activeTab === "fx" ? "bg-odoo-purple text-white" : "text-slate-700 hover:bg-slate-100"
            }`}
          >
            <Globe2 className="w-3.5 h-3.5" />
            {locale === "ar" ? "إعادة تقييم العملات (IAS 21)" : "Unrealized FX (IAS 21)"}
          </button>
        </div>
      </div>

      {banner && (
        <div
          className={`px-4 py-2.5 rounded-lg text-sm border ${
            banner.type === "ok"
              ? "bg-emerald-50 border-emerald-200 text-emerald-800"
              : "bg-rose-50 border-rose-200 text-rose-700"
          }`}
        >
          {banner.msg}
        </div>
      )}

      {activeTab === "bank" && (
        <div className="space-y-5">
          <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-3">
            <h3 className="font-bold text-slate-900 text-sm flex items-center gap-2">
              <Upload className="w-4 h-4 text-odoo-purple" />
              {locale === "ar" ? "استيراد كشف حساب بنكي (CSV)" : "Import Bank Statement CSV Feed"}
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Bank Journal</label>
                <select
                  value={activeBankJournalId}
                  onChange={(e) => setSelectedJournalId(Number(e.target.value))}
                  className="w-full border border-slate-300 rounded px-3 py-1.5 text-sm"
                >
                  {bankJournals.map((j) => (
                    <option key={j.id} value={j.id}>
                      {j.code} — {j.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Statement Reference</label>
                <input
                  value={stmtName}
                  onChange={(e) => setStmtName(e.target.value)}
                  className="w-full border border-slate-300 rounded px-3 py-1.5 text-sm"
                />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                CSV Lines (columns: date, ref, partner, amount)
              </label>
              <textarea
                rows={4}
                value={csvText}
                onChange={(e) => setCsvText(e.target.value)}
                className="w-full border border-slate-300 rounded-lg p-2.5 font-mono text-xs"
              />
            </div>
            <button
              onClick={handleImportCsv}
              disabled={busy}
              className="px-4 py-2 rounded-md bg-odoo-purple hover:opacity-95 text-white text-xs font-medium disabled:opacity-50"
            >
              {busy ? "Importing..." : "Import & Auto-Match Statement"}
            </button>
          </div>

          {statements.map((st) => (
            <div key={st.id} className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
              <div className="px-5 py-3 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
                <div>
                  <span className="font-bold text-slate-900 text-sm">{st.name}</span>
                  <span className="ml-3 text-xs text-slate-500">{st.date}</span>
                </div>
                <div className="flex items-center gap-3 text-xs">
                  <span className="font-mono text-slate-700">
                    End Balance: {formatMoney(st.balance_end_real, currencySymbol)}
                  </span>
                  <span
                    className={`px-2 py-0.5 rounded-full font-semibold ${
                      st.state === "POSTED"
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-amber-100 text-amber-800"
                    }`}
                  >
                    {st.reconciled_count}/{st.total_lines} Reconciled
                  </span>
                </div>
              </div>
              <div className="divide-y divide-slate-100">
                {st.lines.map((ln) => (
                  <div key={ln.id} className="px-5 py-3 flex flex-col lg:flex-row lg:items-center justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-sm font-medium text-slate-900">
                        <span className="font-mono text-xs text-slate-500">{ln.date}</span>
                        <span>{ln.payment_ref}</span>
                        {ln.partner_name && (
                          <span className="text-xs text-slate-500">({ln.partner_name})</span>
                        )}
                      </div>
                      <div
                        className={`font-mono text-sm font-bold ${
                          Number(ln.amount) >= 0 ? "text-emerald-700" : "text-rose-700"
                        }`}
                      >
                        {formatMoney(ln.amount, currencySymbol)}
                      </div>
                    </div>

                    {ln.is_reconciled ? (
                      <div className="flex items-center gap-1.5 text-emerald-700 text-xs font-semibold">
                        <CheckCircle2 className="w-4 h-4" />
                        Reconciled (Move #{ln.move_id})
                      </div>
                    ) : (
                      <div className="flex flex-wrap items-center gap-2">
                        {ln.suggestions.map((sug) => (
                          <button
                            key={sug.move_id}
                            disabled={busy}
                            onClick={() => handleReconcileLine(ln.id, sug.move_id)}
                            className="px-2.5 py-1 rounded-md bg-emerald-50 border border-emerald-300 hover:bg-emerald-100 text-emerald-800 text-xs font-medium"
                          >
                            Match {sug.move_name} ({formatMoney(sug.amount_residual, currencySymbol)}) ·{" "}
                            {sug.confidence}%
                          </button>
                        ))}
                        <div className="flex items-center gap-1.5">
                          <select
                            value={offsetAccountByLine[ln.id] || expenseAccounts[0]?.id || ""}
                            onChange={(e) =>
                              setOffsetAccountByLine((prev) => ({
                                ...prev,
                                [ln.id]: Number(e.target.value),
                              }))
                            }
                            className="border border-slate-300 rounded px-2 py-1 text-xs"
                          >
                            {expenseAccounts.map((a) => (
                              <option key={a.id} value={a.id}>
                                {a.code} {a.name}
                              </option>
                            ))}
                          </select>
                          <button
                            disabled={busy}
                            onClick={() =>
                              handleReconcileLine(
                                ln.id,
                                undefined,
                                offsetAccountByLine[ln.id] || expenseAccounts[0]?.id
                              )
                            }
                            className="px-2.5 py-1 rounded-md bg-slate-800 hover:bg-slate-900 text-white text-xs font-medium"
                          >
                            Book Direct
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {activeTab === "closing" && (
        <div className="space-y-5">
          <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Fiscal Year Start</label>
                <input
                  type="date"
                  value={closeFrom}
                  onChange={(e) => setCloseFrom(e.target.value)}
                  className="border border-slate-300 rounded px-3 py-1.5 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Fiscal Year End</label>
                <input
                  type="date"
                  value={closeTo}
                  onChange={(e) => setCloseTo(e.target.value)}
                  className="border border-slate-300 rounded px-3 py-1.5 text-sm"
                />
              </div>
              <label className="flex items-center gap-2 text-xs font-medium text-slate-700 pb-2">
                <input
                  type="checkbox"
                  checked={lockYear}
                  onChange={(e) => setLockYear(e.target.checked)}
                />
                Lock Fiscal Year Date ({closeTo}) after closing
              </label>
              <button
                onClick={() => refetchClosing()}
                className="px-3 py-1.5 border border-slate-300 rounded text-xs font-medium hover:bg-slate-50 flex items-center gap-1"
              >
                <RefreshCw className="w-3.5 h-3.5" /> Preview
              </button>
              <button
                onClick={handleExecuteClosing}
                disabled={busy || !closingPreview || closingPreview.accounts.length === 0}
                className="px-4 py-1.5 rounded bg-odoo-purple hover:opacity-95 text-white text-xs font-medium disabled:opacity-50"
              >
                Post Closing Entry to Retained Earnings
              </button>
            </div>

            {closingPreview && (
              <div className="space-y-3">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="p-3 rounded-lg bg-slate-50 border border-slate-200">
                    <div className="text-xs text-slate-600">P&amp;L Accounts to Zero</div>
                    <div className="text-lg font-bold font-mono text-slate-900">
                      {closingPreview.accounts_count}
                    </div>
                  </div>
                  <div className="p-3 rounded-lg bg-purple-50 border border-purple-200">
                    <div className="text-xs text-odoo-purple">
                      Net Profit → {closingPreview.retained_earnings_account_label}
                    </div>
                    <div className="text-lg font-bold font-mono text-purple-900">
                      {formatMoney(closingPreview.net_profit, currencySymbol)}
                    </div>
                  </div>
                </div>

                <table className="w-full text-xs border border-slate-200 rounded overflow-hidden">
                  <thead className="bg-slate-50 text-slate-600">
                    <tr>
                      <th className="px-3 py-2 text-left">Account</th>
                      <th className="px-3 py-2 text-left">Type</th>
                      <th className="px-3 py-2 text-right">Closing Debit</th>
                      <th className="px-3 py-2 text-right">Closing Credit</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {closingPreview.accounts.map((a) => (
                      <tr key={a.account_id}>
                        <td className="px-3 py-2 font-medium">
                          {a.code} — {a.name}
                        </td>
                        <td className="px-3 py-2 text-slate-500">{a.account_type}</td>
                        <td className="px-3 py-2 text-right font-mono">
                          {formatMoney(a.closing_debit, currencySymbol)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {formatMoney(a.closing_credit, currencySymbol)}
                        </td>
                      </tr>
                    ))}
                    {closingPreview.accounts.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-3 py-6 text-center text-slate-400">
                          All P&amp;L accounts are zero for this period.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {closingHistory.length > 0 && (
            <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <h4 className="font-bold text-xs uppercase text-slate-500 mb-2">Previous Fiscal Closings</h4>
              <div className="space-y-1.5 text-xs">
                {closingHistory.map((h) => (
                  <div key={h.id} className="flex items-center justify-between py-1 border-b border-slate-100">
                    <span className="font-semibold">{h.name}</span>
                    <span className="text-slate-500">
                      {h.date_from} → {h.date_to}
                    </span>
                    <span className="font-mono font-bold">
                      {formatMoney(h.net_profit, currencySymbol)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === "fx" && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Revaluation Date</label>
              <input
                type="date"
                value={revalDate}
                onChange={(e) => setRevalDate(e.target.value)}
                className="border border-slate-300 rounded px-3 py-1.5 text-sm"
              />
            </div>
            <label className="flex items-center gap-2 text-xs font-medium text-slate-700 pb-2">
              <input
                type="checkbox"
                checked={autoReverse}
                onChange={(e) => setAutoReverse(e.target.checked)}
              />
              Auto-reverse next day (IAS 21 standard practice)
            </label>
            <button
              onClick={() => refetchFx()}
              className="px-3 py-1.5 border border-slate-300 rounded text-xs font-medium hover:bg-slate-50 flex items-center gap-1"
            >
              <RefreshCw className="w-3.5 h-3.5" /> Compute Preview
            </button>
            <button
              onClick={handleExecuteFx}
              disabled={busy || !fxPreview || fxPreview.exposures.length === 0}
              className="px-4 py-1.5 rounded bg-odoo-purple hover:opacity-95 text-white text-xs font-medium disabled:opacity-50"
            >
              Post Unrealized FX Entry
            </button>
          </div>

          {fxPreview && (
            <div className="space-y-3">
              <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 flex items-center justify-between">
                <span className="text-xs font-medium text-slate-600">
                  Total Unrealized FX Gain / (Loss) as of {fxPreview.revaluation_date}
                </span>
                <span
                  className={`font-mono text-base font-bold ${
                    Number(fxPreview.net_unrealized_fx) >= 0
                      ? "text-emerald-700"
                      : "text-rose-700"
                  }`}
                >
                  {formatMoney(fxPreview.net_unrealized_fx, currencySymbol)}
                </span>
              </div>
              <table className="w-full text-xs border border-slate-200 rounded overflow-hidden">
                <thead className="bg-slate-50 text-slate-600">
                  <tr>
                    <th className="px-3 py-2 text-left">Account / Line</th>
                    <th className="px-3 py-2 text-right">Foreign Open</th>
                    <th className="px-3 py-2 text-right">Closing Rate</th>
                    <th className="px-3 py-2 text-right">Book Base</th>
                    <th className="px-3 py-2 text-right">Revalued Base</th>
                    <th className="px-3 py-2 text-right">Unrealized Diff</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {fxPreview.exposures.map((it) => (
                    <tr key={it.line_id}>
                      <td className="px-3 py-2">
                        <div className="font-semibold">
                          {it.account_code} {it.account_name}
                        </div>
                        <div className="text-slate-500">Line #{it.line_id}</div>
                      </td>
                      <td className="px-3 py-2 text-right font-mono">
                        {it.open_foreign_amount} {it.currency_code}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">{it.closing_rate}</td>
                      <td className="px-3 py-2 text-right font-mono">
                        {formatMoney(it.booked_base_amount, currencySymbol)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">
                        {formatMoney(it.revalued_base_amount, currencySymbol)}
                      </td>
                      <td
                        className={`px-3 py-2 text-right font-mono font-bold ${
                          Number(it.unrealized_gain_loss) >= 0 ? "text-emerald-700" : "text-rose-700"
                        }`}
                      >
                        {formatMoney(it.unrealized_gain_loss, currencySymbol)}
                      </td>
                    </tr>
                  ))}
                  {fxPreview.exposures.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-3 py-6 text-center text-slate-400">
                        No unrealized foreign currency gain/loss adjustments needed for this date.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
