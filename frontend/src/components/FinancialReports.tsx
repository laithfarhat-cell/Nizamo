import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  FileSpreadsheet,
  FileText,
  Scale,
} from "lucide-react";
import { apiRequest, downloadReportExport, formatMoney } from "../api/client";
import type { AnalyticAccount, Locale } from "../types/erp";
import { t } from "../i18n/translations";

type ReportTab =
  | "trial_balance"
  | "profit_and_loss"
  | "balance_sheet"
  | "general_ledger"
  | "aged_receivable"
  | "aged_payable";

interface FinancialReportsProps {
  locale: Locale;
  onOpenMove?: (moveId: number) => void;
}

export const FinancialReports: React.FC<FinancialReportsProps> = ({ locale, onOpenMove }) => {
  const today = new Date().toISOString().slice(0, 10);
  const yearStart = `${new Date().getFullYear()}-01-01`;

  const [tab, setTab] = useState<ReportTab>("balance_sheet");
  const [dateFrom, setDateFrom] = useState(yearStart);
  const [dateTo, setDateTo] = useState(today);
  const [targetMove, setTargetMove] = useState<"posted" | "all">("posted");
  const [analyticId, setAnalyticId] = useState<string>("");
  const [expandedAccts, setExpandedAccts] = useState<Set<number>>(new Set());

  const { data: analytics = [] } = useQuery<AnalyticAccount[]>({
    queryKey: ["analytics"],
    queryFn: () => apiRequest<AnalyticAccount[]>("/accounting/analytics"),
  });

  const buildReportPath = (exportFmt?: "csv" | "xlsx" | "pdf") => {
    const params = new URLSearchParams();
    params.set("target_move", targetMove);
    if (analyticId) params.set("analytic_account_id", analyticId);
    if (exportFmt) params.set("export", exportFmt);

    if (tab === "trial_balance") {
      params.set("date_from", dateFrom);
      params.set("date_to", dateTo);
      return `/accounting/reports/trial-balance?${params.toString()}`;
    }
    if (tab === "profit_and_loss") {
      params.set("date_from", dateFrom);
      params.set("date_to", dateTo);
      return `/accounting/reports/profit-and-loss?${params.toString()}`;
    }
    if (tab === "balance_sheet") {
      params.set("date_to", dateTo);
      return `/accounting/reports/balance-sheet?${params.toString()}`;
    }
    if (tab === "general_ledger") {
      params.set("date_from", dateFrom);
      params.set("date_to", dateTo);
      return `/accounting/reports/general-ledger?${params.toString()}`;
    }
    params.set("report_type", tab === "aged_receivable" ? "receivable" : "payable");
    params.set("as_of_date", dateTo);
    return `/accounting/reports/aged-partner?${params.toString()}`;
  };

  const { data: report, isLoading } = useQuery<any>({
    queryKey: ["financial-report", tab, dateFrom, dateTo, targetMove, analyticId],
    queryFn: () => apiRequest<any>(buildReportPath()),
  });

  const toggleExpand = (id: number) => {
    setExpandedAccts((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const tabs: { id: ReportTab; label: string }[] = [
    { id: "balance_sheet", label: t(locale, "balanceSheet") },
    { id: "profit_and_loss", label: t(locale, "profitAndLoss") },
    { id: "trial_balance", label: t(locale, "trialBalance") },
    { id: "general_ledger", label: t(locale, "generalLedger") },
    { id: "aged_receivable", label: t(locale, "agedReceivable") },
    { id: "aged_payable", label: t(locale, "agedPayable") },
  ];

  return (
    <div className="space-y-4">
      {/* Report Tabs + Filters + Export Bar */}
      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-1.5">
            {tabs.map((tb) => (
              <button
                key={tb.id}
                type="button"
                onClick={() => setTab(tb.id)}
                className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                  tab === tb.id
                    ? "bg-odoo-purple text-white shadow-sm"
                    : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                }`}
              >
                {tb.label}
              </button>
            ))}
          </div>

          {/* Export Buttons: CSV / XLSX / PDF */}
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => downloadReportExport(buildReportPath("csv"), `${tab}.csv`)}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              <Download className="h-3.5 w-3.5" /> CSV
            </button>
            <button
              type="button"
              onClick={() => downloadReportExport(buildReportPath("xlsx"), `${tab}.xlsx`)}
              className="inline-flex items-center gap-1 rounded-lg border border-emerald-300 bg-emerald-50 px-2.5 py-1.5 text-xs font-semibold text-emerald-800 hover:bg-emerald-100"
            >
              <FileSpreadsheet className="h-3.5 w-3.5" /> XLSX
            </button>
            <button
              type="button"
              onClick={() => downloadReportExport(buildReportPath("pdf"), `${tab}.pdf`)}
              className="inline-flex items-center gap-1 rounded-lg border border-rose-300 bg-rose-50 px-2.5 py-1.5 text-xs font-semibold text-rose-800 hover:bg-rose-100"
            >
              <FileText className="h-3.5 w-3.5" /> PDF
            </button>
          </div>
        </div>

        {/* Date, Target Move & Analytic Filters */}
        <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-3 text-xs">
          {tab !== "balance_sheet" && tab !== "aged_receivable" && tab !== "aged_payable" && (
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-slate-500">From:</span>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="rounded-lg border border-slate-300 px-2.5 py-1"
              />
            </div>
          )}
          <div className="flex items-center gap-1.5">
            <span className="font-semibold text-slate-500">
              {tab === "balance_sheet" || tab.startsWith("aged_") ? "As of:" : "To:"}
            </span>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="rounded-lg border border-slate-300 px-2.5 py-1"
            />
          </div>

          <select
            value={targetMove}
            onChange={(e) => setTargetMove(e.target.value as "posted" | "all")}
            className="rounded-lg border border-slate-300 px-2.5 py-1 font-medium"
          >
            <option value="posted">{t(locale, "postedOnly")}</option>
            <option value="all">{t(locale, "allEntries")}</option>
          </select>

          {!tab.startsWith("aged_") && (
            <select
              value={analyticId}
              onChange={(e) => setAnalyticId(e.target.value)}
              className="rounded-lg border border-slate-300 px-2.5 py-1"
            >
              <option value="">All Analytic Cost Centers</option>
              {analytics.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.code} — {a.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* Report Body */}
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        {isLoading || !report ? (
          <div className="py-12 text-center text-sm text-slate-400">Computing real-time SQL ledger report...</div>
        ) : (
          <>
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-4">
              <div>
                <h2 className="text-lg font-bold text-slate-900">
                  {locale === "ar" ? report.title_ar : report.title}
                </h2>
                <p className="text-xs text-slate-500">
                  {report.date_from ? `${report.date_from} → ${report.date_to}` : `As of ${report.as_of_date}`} ·{" "}
                  {targetMove === "posted" ? "Posted Ledger" : "Including Drafts"}
                </p>
              </div>

              {tab === "balance_sheet" && (
                <div
                  className={`inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-xs font-bold ${
                    report.summary?.is_balanced
                      ? "bg-emerald-100 text-emerald-800"
                      : "bg-rose-100 text-rose-800"
                  }`}
                >
                  <Scale className="h-4 w-4" />
                  {report.summary?.is_balanced ? t(locale, "balanced") : t(locale, "unbalanced")}
                </div>
              )}

              {tab === "trial_balance" && (
                <div
                  className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold ${
                    report.totals?.is_balanced
                      ? "bg-emerald-100 text-emerald-800"
                      : "bg-rose-100 text-rose-800"
                  }`}
                >
                  <CheckCircle2 className="h-4 w-4" />
                  {report.totals?.is_balanced ? "Debit == Credit Verified" : "Out of Balance"}
                </div>
              )}
            </div>

            {/* 1. BALANCE SHEET */}
            {tab === "balance_sheet" && (
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 text-xs">
                {/* Assets */}
                <div className="space-y-4">
                  <h3 className="text-sm font-bold uppercase tracking-wide text-odoo-purple">Assets</h3>
                  <div className="rounded-lg border border-slate-200 overflow-hidden">
                    <div className="bg-slate-50 px-3 py-2 font-semibold text-slate-600">Current Assets</div>
                    <div className="divide-y divide-slate-100">
                      {report.assets.current_assets.map((r: any) => (
                        <div key={r.account_id} className="flex justify-between px-3 py-2">
                          <span>
                            <span className="font-mono text-slate-400 me-2">{r.code}</span>
                            {locale === "ar" ? r.name_ar : r.name}
                          </span>
                          <span className="font-semibold tabular-nums">{formatMoney(r.balance)}</span>
                        </div>
                      ))}
                      {report.assets.non_current_assets.map((r: any) => (
                        <div key={r.account_id} className="flex justify-between px-3 py-2">
                          <span>
                            <span className="font-mono text-slate-400 me-2">{r.code}</span>
                            {locale === "ar" ? r.name_ar : r.name}
                          </span>
                          <span className="font-semibold tabular-nums">{formatMoney(r.balance)}</span>
                        </div>
                      ))}
                    </div>
                    <div className="flex justify-between bg-purple-50 px-3 py-2.5 text-sm font-bold text-odoo-purple tabular-nums">
                      <span>Total Assets</span>
                      <span>{formatMoney(report.assets.total_assets)}</span>
                    </div>
                  </div>
                </div>

                {/* Liabilities & Equity */}
                <div className="space-y-4">
                  <h3 className="text-sm font-bold uppercase tracking-wide text-odoo-teal">
                    Liabilities & Equity
                  </h3>
                  <div className="rounded-lg border border-slate-200 overflow-hidden">
                    <div className="bg-slate-50 px-3 py-2 font-semibold text-slate-600">Liabilities</div>
                    <div className="divide-y divide-slate-100">
                      {[...report.liabilities.current_liabilities, ...report.liabilities.non_current_liabilities].map(
                        (r: any) => (
                          <div key={r.account_id} className="flex justify-between px-3 py-2">
                            <span>
                              <span className="font-mono text-slate-400 me-2">{r.code}</span>
                              {locale === "ar" ? r.name_ar : r.name}
                            </span>
                            <span className="font-semibold tabular-nums">{formatMoney(r.balance)}</span>
                          </div>
                        )
                      )}
                      <div className="flex justify-between bg-slate-50/70 px-3 py-2 font-bold tabular-nums">
                        <span>Total Liabilities</span>
                        <span>{formatMoney(report.liabilities.total_liabilities)}</span>
                      </div>
                    </div>

                    <div className="bg-slate-50 px-3 py-2 font-semibold text-slate-600 border-t border-slate-200">
                      Equity
                    </div>
                    <div className="divide-y divide-slate-100">
                      {report.equity.accounts.map((r: any) => (
                        <div key={r.account_id} className="flex justify-between px-3 py-2">
                          <span>
                            <span className="font-mono text-slate-400 me-2">{r.code}</span>
                            {locale === "ar" ? r.name_ar : r.name}
                          </span>
                          <span className="font-semibold tabular-nums">{formatMoney(r.balance)}</span>
                        </div>
                      ))}
                      <div className="flex justify-between px-3 py-2 bg-emerald-50/50">
                        <span className="font-medium text-emerald-900">
                          Current Year Unallocated Earnings (Net Profit)
                        </span>
                        <span className="font-bold text-emerald-800 tabular-nums">
                          {formatMoney(report.equity.current_year_unallocated_earnings)}
                        </span>
                      </div>
                    </div>

                    <div className="flex justify-between bg-teal-50 px-3 py-2.5 text-sm font-bold text-odoo-teal tabular-nums">
                      <span>Total Liabilities & Equity</span>
                      <span>{formatMoney(report.summary.total_liabilities_and_equity)}</span>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* 2. PROFIT & LOSS */}
            {tab === "profit_and_loss" && (
              <div className="max-w-3xl space-y-4 text-xs">
                {[
                  { title: "Operating Income", items: report.sections.operating_income },
                  { title: "Cost of Goods Sold (COGS)", items: report.sections.cost_of_goods_sold },
                  { title: "Other Income (incl. Realized FX Gain)", items: report.sections.other_income },
                  { title: "Operating Expenses", items: report.sections.operating_expenses },
                ].map((sec) => (
                  <div key={sec.title} className="rounded-lg border border-slate-200 overflow-hidden">
                    <div className="bg-slate-50 px-3 py-2 font-bold text-slate-700">{sec.title}</div>
                    <div className="divide-y divide-slate-100">
                      {sec.items.length === 0 ? (
                        <div className="px-3 py-2 text-slate-400">No entries in period</div>
                      ) : (
                        sec.items.map((item: any) => (
                          <div key={item.account_id} className="flex justify-between px-3 py-2">
                            <span>
                              <span className="font-mono text-slate-400 me-2">{item.code}</span>
                              {locale === "ar" ? item.name_ar : item.name}
                            </span>
                            <span className="font-semibold tabular-nums">{formatMoney(item.amount)}</span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                ))}

                <div className="rounded-xl border border-purple-200 bg-purple-50/70 p-4 space-y-2 tabular-nums">
                  <div className="flex justify-between text-sm font-semibold text-slate-800">
                    <span>Gross Profit (Operating Income − COGS)</span>
                    <span>{formatMoney(report.summary.gross_profit)}</span>
                  </div>
                  <div className="flex justify-between border-t border-purple-200 pt-2 text-base font-bold text-odoo-purple">
                    <span>Net Profit / (Loss)</span>
                    <span>{formatMoney(report.summary.net_profit)}</span>
                  </div>
                </div>
              </div>
            )}

            {/* 3. TRIAL BALANCE */}
            {tab === "trial_balance" && (
              <div className="overflow-x-auto">
                <table className="w-full text-start text-xs tabular-nums">
                  <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
                    <tr>
                      <th className="px-3 py-2">Code</th>
                      <th className="px-3 py-2">Account</th>
                      <th className="px-3 py-2 text-end">Open Dr</th>
                      <th className="px-3 py-2 text-end">Open Cr</th>
                      <th className="px-3 py-2 text-end">Period Dr</th>
                      <th className="px-3 py-2 text-end">Period Cr</th>
                      <th className="px-3 py-2 text-end">Close Dr</th>
                      <th className="px-3 py-2 text-end">Close Cr</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {report.rows.map((r: any) => (
                      <tr key={r.account_id} className="hover:bg-slate-50">
                        <td className="px-3 py-2 font-mono text-slate-500">{r.code}</td>
                        <td className="px-3 py-2 font-medium text-slate-800">
                          {locale === "ar" ? r.name_ar : r.name}
                        </td>
                        <td className="px-3 py-2 text-end">{formatMoney(r.opening_debit)}</td>
                        <td className="px-3 py-2 text-end">{formatMoney(r.opening_credit)}</td>
                        <td className="px-3 py-2 text-end">{formatMoney(r.period_debit)}</td>
                        <td className="px-3 py-2 text-end">{formatMoney(r.period_credit)}</td>
                        <td className="px-3 py-2 text-end font-semibold">{formatMoney(r.closing_debit)}</td>
                        <td className="px-3 py-2 text-end font-semibold">{formatMoney(r.closing_credit)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot className="border-t-2 border-slate-300 bg-slate-50 font-bold">
                    <tr>
                      <td colSpan={2} className="px-3 py-2.5">TOTAL</td>
                      <td className="px-3 py-2.5 text-end">{formatMoney(report.totals.opening_debit)}</td>
                      <td className="px-3 py-2.5 text-end">{formatMoney(report.totals.opening_credit)}</td>
                      <td className="px-3 py-2.5 text-end">{formatMoney(report.totals.period_debit)}</td>
                      <td className="px-3 py-2.5 text-end">{formatMoney(report.totals.period_credit)}</td>
                      <td className="px-3 py-2.5 text-end text-odoo-purple">{formatMoney(report.totals.closing_debit)}</td>
                      <td className="px-3 py-2.5 text-end text-odoo-purple">{formatMoney(report.totals.closing_credit)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}

            {/* 4. GENERAL LEDGER (with drill-down) */}
            {tab === "general_ledger" && (
              <div className="space-y-2 text-xs">
                {report.accounts.map((acct: any) => {
                  const isOpen = expandedAccts.has(acct.account_id);
                  return (
                    <div key={acct.account_id} className="rounded-lg border border-slate-200 overflow-hidden">
                      <button
                        type="button"
                        onClick={() => toggleExpand(acct.account_id)}
                        className="flex w-full items-center justify-between bg-slate-50 px-3 py-2.5 hover:bg-slate-100 transition"
                      >
                        <div className="flex items-center gap-2 font-bold text-slate-800">
                          {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                          <span className="font-mono text-odoo-purple">{acct.code}</span>
                          <span>{locale === "ar" ? acct.name_ar : acct.name}</span>
                          <span className="rounded-full bg-white px-2 py-0.5 text-[10px] text-slate-500 border border-slate-200">
                            {acct.entries.length} lines
                          </span>
                        </div>
                        <div className="flex gap-5 tabular-nums font-semibold">
                          <span>Dr: {formatMoney(acct.total_debit)}</span>
                          <span>Cr: {formatMoney(acct.total_credit)}</span>
                          <span className="text-odoo-purple">Bal: {formatMoney(acct.closing_balance)}</span>
                        </div>
                      </button>

                      {isOpen && (
                        <table className="w-full text-start text-xs tabular-nums">
                          <thead className="border-y border-slate-200 bg-white text-[11px] uppercase text-slate-400">
                            <tr>
                              <th className="px-3 py-1.5">Date</th>
                              <th className="px-3 py-1.5">Document</th>
                              <th className="px-3 py-1.5">Partner</th>
                              <th className="px-3 py-1.5">Label</th>
                              <th className="px-3 py-1.5 text-end">Debit</th>
                              <th className="px-3 py-1.5 text-end">Credit</th>
                              <th className="px-3 py-1.5 text-end">Running Balance</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-100">
                            {acct.entries.map((e: any) => (
                              <tr
                                key={e.line_id}
                                onClick={() => onOpenMove?.(e.move_id)}
                                className="cursor-pointer hover:bg-purple-50/40"
                              >
                                <td className="px-3 py-2">{e.date}</td>
                                <td className="px-3 py-2 font-semibold text-odoo-purple underline-offset-2 hover:underline">
                                  {e.move_name}
                                </td>
                                <td className="px-3 py-2">{e.partner_name || "—"}</td>
                                <td className="px-3 py-2">{e.label}</td>
                                <td className="px-3 py-2 text-end">{formatMoney(e.debit)}</td>
                                <td className="px-3 py-2 text-end">{formatMoney(e.credit)}</td>
                                <td className="px-3 py-2 text-end font-semibold">{formatMoney(e.running_balance)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* 5. AGED RECEIVABLE & AGED PAYABLE */}
            {(tab === "aged_receivable" || tab === "aged_payable") && (
              <div className="overflow-x-auto">
                <table className="w-full text-start text-xs tabular-nums">
                  <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
                    <tr>
                      <th className="px-3 py-2">Partner</th>
                      <th className="px-3 py-2 text-end">Current</th>
                      <th className="px-3 py-2 text-end">1–30 Days</th>
                      <th className="px-3 py-2 text-end">31–60 Days</th>
                      <th className="px-3 py-2 text-end">61–90 Days</th>
                      <th className="px-3 py-2 text-end">90+ Days</th>
                      <th className="px-3 py-2 text-end">Total Open</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {report.partners.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="py-8 text-center text-slate-400">
                          No open balances as of {report.as_of_date}.
                        </td>
                      </tr>
                    ) : (
                      report.partners.map((p: any) => (
                        <tr key={p.partner_id} className="hover:bg-slate-50">
                          <td className="px-3 py-2.5 font-semibold text-slate-800">{p.partner_name}</td>
                          <td className="px-3 py-2.5 text-end">{formatMoney(p.current)}</td>
                          <td className="px-3 py-2.5 text-end">{formatMoney(p.days_1_30)}</td>
                          <td className="px-3 py-2.5 text-end text-amber-700 font-medium">{formatMoney(p.days_31_60)}</td>
                          <td className="px-3 py-2.5 text-end text-orange-700 font-medium">{formatMoney(p.days_61_90)}</td>
                          <td className="px-3 py-2.5 text-end text-rose-700 font-bold">{formatMoney(p.days_90_plus)}</td>
                          <td className="px-3 py-2.5 text-end font-bold text-slate-900">{formatMoney(p.total)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                  <tfoot className="border-t-2 border-slate-300 bg-slate-50 font-bold">
                    <tr>
                      <td className="px-3 py-2.5">TOTAL</td>
                      <td className="px-3 py-2.5 text-end">{formatMoney(report.totals.current)}</td>
                      <td className="px-3 py-2.5 text-end">{formatMoney(report.totals.days_1_30)}</td>
                      <td className="px-3 py-2.5 text-end">{formatMoney(report.totals.days_31_60)}</td>
                      <td className="px-3 py-2.5 text-end">{formatMoney(report.totals.days_61_90)}</td>
                      <td className="px-3 py-2.5 text-end">{formatMoney(report.totals.days_90_plus)}</td>
                      <td className="px-3 py-2.5 text-end text-odoo-purple">{formatMoney(report.totals.total)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
