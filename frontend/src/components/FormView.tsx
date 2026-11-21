import React from "react";
import { ArrowLeft } from "lucide-react";
import type { Locale } from "../types/erp";
import { ChatterPanel } from "./ChatterPanel";

export interface FormAction {
  id: string;
  label: string;
  variant?: "primary" | "secondary" | "danger" | "teal";
  disabled?: boolean;
  onClick: () => void;
}

export interface SmartButton {
  id: string;
  label: string;
  count: number | string;
  icon: React.ReactNode;
  onClick?: () => void;
}

interface FormViewProps {
  title: string;
  subtitle?: string;
  onBack: () => void;
  actions: FormAction[];
  pipelineSteps: { id: string; label: string }[];
  currentStepId: string;
  smartButtons?: SmartButton[];
  chatter?: { resModel: string; resId: number };
  locale: Locale;
  errorBanner?: string | null;
  children: React.ReactNode;
}

export const FormView: React.FC<FormViewProps> = ({
  title,
  subtitle,
  onBack,
  actions,
  pipelineSteps,
  currentStepId,
  smartButtons = [],
  chatter,
  locale,
  errorBanner,
  children,
}) => {
  const activeIdx = pipelineSteps.findIndex((s) => s.id === currentStepId);

  return (
    <div className="space-y-4">
      {/* Sticky Odoo Action Header + Statusbar Pipeline */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 transition"
          >
            <ArrowLeft className="h-3.5 w-3.5 rtl:rotate-180" />
          </button>

          {actions.map((act) => {
            const cls =
              act.variant === "primary"
                ? "bg-odoo-purple text-white hover:bg-odoo-purpleDark"
                : act.variant === "teal"
                ? "bg-odoo-teal text-white hover:opacity-90"
                : act.variant === "danger"
                ? "border border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100"
                : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50";
            return (
              <button
                key={act.id}
                type="button"
                disabled={act.disabled}
                onClick={act.onClick}
                className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold transition disabled:opacity-40 ${cls}`}
              >
                {act.label}
              </button>
            );
          })}
        </div>

        {/* Visual Statusbar Pipeline (Draft -> Posted -> Paid) */}
        <div className="flex items-center overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
          {pipelineSteps.map((step, idx) => {
            const isCurrent = step.id === currentStepId;
            const isPast = activeIdx >= 0 && idx < activeIdx;
            return (
              <div
                key={step.id}
                className={`relative px-3.5 py-1.5 text-xs font-semibold transition ${
                  isCurrent
                    ? "bg-odoo-purple text-white"
                    : isPast
                    ? "bg-purple-50 text-odoo-purple"
                    : "text-slate-400"
                } ${idx > 0 ? "border-s border-slate-200" : ""}`}
              >
                {step.label}
              </div>
            );
          })}
        </div>
      </div>

      {errorBanner && (
        <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-xs font-medium text-rose-800">
          {errorBanner}
        </div>
      )}

      {/* Main Sheet Canvas with Top-Right Smart Buttons */}
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4 border-b border-slate-100 pb-4">
          <div>
            {subtitle && (
              <div className="text-xs font-semibold uppercase tracking-wider text-odoo-teal">
                {subtitle}
              </div>
            )}
            <h2 className="mt-0.5 text-2xl font-bold tracking-tight text-slate-900">{title}</h2>
          </div>

          {smartButtons.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              {smartButtons.map((sb) => (
                <button
                  key={sb.id}
                  type="button"
                  onClick={sb.onClick}
                  className="flex items-center gap-2.5 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2 text-start hover:border-odoo-purple/40 hover:bg-purple-50/40 transition"
                >
                  <span className="text-odoo-purple">{sb.icon}</span>
                  <div>
                    <div className="text-sm font-bold leading-none text-slate-900 tabular-nums">
                      {sb.count}
                    </div>
                    <div className="mt-0.5 text-[11px] font-medium text-slate-500">{sb.label}</div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {children}
      </div>

      {/* Bottom Audit Chatter Timeline */}
      {chatter && chatter.resId > 0 && (
        <ChatterPanel resModel={chatter.resModel} resId={chatter.resId} locale={locale} />
      )}
    </div>
  );
};
