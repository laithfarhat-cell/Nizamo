import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  BookOpen,
  CreditCard,
  FileSpreadsheet,
  Landmark,
  Plus,
  Receipt,
} from "lucide-react";
import { apiRequest, formatMoney } from "../api/client";
import type { AccountMove, JournalCard, Locale } from "../types/erp";
import { t } from "../i18n/translations";

interface AccountingDashboardProps {
  locale: Locale;
  onQuickCreate: (moveType: AccountMove["move_type"]) => void;
  onOpenJournalMoves: (journalId: number) => void;
  onOpenReports: () => void;
}

export const AccountingDashboard: React.FC<AccountingDashboardProps> = ({
  locale,
  onQuickCreate,
  onOpenJournalMoves,
  onOpenReports,
}) => {
  const { data, isLoading } = useQuery<{ journals: JournalCard[] }>({
    queryKey: ["accounting-dashboard"],
    queryFn: () => apiRequest<{ journals: JournalCard[] }>("/accounting/dashboard"),
  });

  const journals = data?.journals ?? [];

  const getVisuals = (jtype: string) => {
    switch (jtype) {
      case "SALE":
        return { icon: FileSpreadsheet, accent: "border-t-emerald-500", moveType: "out_invoice" as const, cta: "New Invoice" };
      case "PURCHASE":
        return { icon: Receipt, accent: "border-t-amber-500", moveType: "in_invoice" as const, cta: "New Bill" };
      case "BANK":
        return { icon: Landmark, accent: "border-t-sky-600", moveType: "entry" as const, cta: "New Entry" };
      case "CASH":
        return { icon: CreditCard, accent: "border-t-teal-600", moveType: "entry" as const, cta: "New Entry" };
      default:
        return { icon: BookOpen, accent: "border-t-odoo-purple", moveType: "entry" as const, cta: "New Entry" };
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900">{t(locale, "dashboard")}</h1>
          <p className="text-xs text-slate-500">
            Unified Double-Entry Journals · Real-time GL & Open Reconciliation Balances
          </p>
        </div>
        <button
          type="button"
          onClick={onOpenReports}
          className="inline-flex items-center gap-1.5 rounded-lg bg-odoo-teal px-4 py-2 text-xs font-semibold text-white shadow-sm hover:opacity-90 transition"
        >
          {t(locale, "reports")}
          <ArrowRight className="h-3.5 w-3.5 rtl:rotate-180" />
        </button>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="h-44 animate-pulse rounded-xl border border-slate-200 bg-white" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {journals.map((j) => {
            const { icon: Icon, accent, moveType, cta } = getVisuals(j.journal_type);
            return (
              <div
                key={j.journal_id}
                className={`flex flex-col justify-between rounded-xl border border-slate-200 border-t-4 ${accent} bg-white p-5 shadow-sm hover:shadow-md transition`}
              >
                <div>
                  <div className="flex items-start justify-between">
                    <div>
                      <span className="inline-block rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] font-bold text-slate-600">
                        {j.code} · {j.journal_type}
                      </span>
                      <h3 className="mt-1.5 text-base font-bold text-slate-900">
                        {locale === "ar" ? j.name_ar : j.name}
                      </h3>
                    </div>
                    <Icon className="h-5 w-5 text-slate-400" />
                  </div>

                  <div className="mt-5 flex items-baseline justify-between">
                    <div>
                      <div className="text-[11px] font-medium text-slate-400">
                        {t(locale, "openBalance")}
                      </div>
                      <div className="mt-0.5 text-2xl font-bold tracking-tight text-slate-900 tabular-nums">
                        {formatMoney(j.open_balance)}
                      </div>
                    </div>
                    <div className="text-end text-xs space-y-1">
                      <div className="text-slate-600">
                        <span className="font-bold text-odoo-purple">{j.unreconciled_count}</span>{" "}
                        {t(locale, "unreconciled")}
                      </div>
                      <div className="text-slate-500">
                        <span className="font-semibold">{j.draft_count}</span> {t(locale, "toValidate")}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="mt-5 flex items-center justify-between border-t border-slate-100 pt-3">
                  <button
                    type="button"
                    onClick={() => onQuickCreate(moveType)}
                    className="inline-flex items-center gap-1 rounded-lg bg-odoo-purple px-3 py-1.5 text-xs font-semibold text-white hover:bg-odoo-purpleDark transition"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    {cta}
                  </button>
                  <button
                    type="button"
                    onClick={() => onOpenJournalMoves(j.journal_id)}
                    className="text-xs font-semibold text-odoo-teal hover:underline"
                  >
                    View Entries →
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
