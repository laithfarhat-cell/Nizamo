import React, { useState } from "react";
import {
  BookOpen,
  Building2,
  FileSpreadsheet,
  Globe,
  Grid,
  Package,
  Settings,
  ShoppingBag,
  ShoppingCart,
  Users,
} from "lucide-react";
import type { AppKey, Company, Locale, UserInfo } from "../types/erp";
import { t } from "../i18n/translations";

interface AppSwitcherProps {
  activeApp: AppKey;
  onSelectApp: (app: AppKey) => void;
  companies: Company[];
  activeCompanyId: number | null;
  onChangeCompany: (companyId: number) => void;
  locale: Locale;
  onToggleLocale: () => void;
  user: UserInfo | null;
}

const APPS: { key: AppKey; icon: React.ComponentType<{ className?: string }>; color: string }[] = [
  { key: "accounting", icon: BookOpen, color: "bg-purple-600" },
  { key: "invoicing", icon: FileSpreadsheet, color: "bg-teal-600" },
  { key: "sales", icon: ShoppingCart, color: "bg-emerald-600" },
  { key: "purchase", icon: ShoppingBag, color: "bg-amber-600" },
  { key: "inventory", icon: Package, color: "bg-indigo-600" },
  { key: "contacts", icon: Users, color: "bg-sky-600" },
  { key: "configuration", icon: Settings, color: "bg-slate-700" },
];

export const AppSwitcher: React.FC<AppSwitcherProps> = ({
  activeApp,
  onSelectApp,
  companies,
  activeCompanyId,
  onChangeCompany,
  locale,
  onToggleLocale,
  user,
}) => {
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 border-b border-slate-800/40 bg-odoo-purple text-white shadow-sm">
      <div className="mx-auto flex h-12 max-w-[1600px] items-center justify-between px-4">
        {/* Left: App Grid Button + Brand + Inline App Tabs */}
        <div className="flex items-center gap-3">
          <div className="relative">
            <button
              type="button"
              onClick={() => setDrawerOpen((v) => !v)}
              className="flex h-8 w-8 items-center justify-center rounded-md bg-white/10 hover:bg-white/20 transition"
              title="App Switcher"
            >
              <Grid className="h-4 w-4" />
            </button>

            {drawerOpen && (
              <div className="absolute start-0 mt-2 w-72 rounded-xl border border-slate-200 bg-white p-3 text-slate-800 shadow-2xl">
                <div className="mb-2 px-1 text-xs font-semibold uppercase tracking-wider text-slate-400">
                  {t(locale, "brand")} Modules
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {APPS.map(({ key, icon: Icon, color }) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => {
                        onSelectApp(key);
                        setDrawerOpen(false);
                      }}
                      className={`flex flex-col items-center gap-1.5 rounded-lg p-2 text-xs font-medium transition ${
                        activeApp === key
                          ? "bg-purple-50 text-odoo-purple ring-1 ring-odoo-purple/30"
                          : "hover:bg-slate-100 text-slate-700"
                      }`}
                    >
                      <span className={`flex h-9 w-9 items-center justify-center rounded-lg text-white ${color}`}>
                        <Icon className="h-4 w-4" />
                      </span>
                      <span className="truncate">{t(locale, key)}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <span className="text-sm font-bold tracking-tight whitespace-nowrap">
            {t(locale, "brand")}
          </span>

          <nav className="hidden lg:flex items-center gap-1 ms-3">
            {APPS.map(({ key, icon: Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => onSelectApp(key)}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition ${
                  activeApp === key
                    ? "bg-white/20 text-white shadow-inner"
                    : "text-white/80 hover:bg-white/10 hover:text-white"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {t(locale, key)}
              </button>
            ))}
          </nav>
        </div>

        {/* Right: Multi-Company Switcher, LTR/RTL Language Toggle, User Badge */}
        <div className="flex items-center gap-2.5">
          {companies.length > 0 && (
            <div className="flex items-center gap-1.5 rounded-md bg-white/10 px-2 py-1 text-xs">
              <Building2 className="h-3.5 w-3.5 text-white/80" />
              <select
                aria-label="Company Switcher"
                value={activeCompanyId ?? ""}
                onChange={(e) => onChangeCompany(Number(e.target.value))}
                className="bg-transparent text-xs font-medium text-white outline-none cursor-pointer"
              >
                {companies.map((c) => (
                  <option key={c.id} value={c.id} className="text-slate-900">
                    {locale === "ar" && c.name_ar ? c.name_ar : c.name} ({c.currency_code})
                  </option>
                ))}
              </select>
            </div>
          )}

          <button
            type="button"
            onClick={onToggleLocale}
            className="flex items-center gap-1.5 rounded-md bg-white/10 px-2.5 py-1 text-xs font-semibold hover:bg-white/20 transition"
            title="Switch English LTR / Arabic RTL"
          >
            <Globe className="h-3.5 w-3.5" />
            <span>{locale === "en" ? "العربية (RTL)" : "English (LTR)"}</span>
          </button>

          {user && (
            <div className="hidden sm:flex items-center gap-2 border-s border-white/20 ps-2.5">
              <div className="flex h-7 w-7 items-center justify-center rounded-full bg-odoo-teal text-xs font-bold uppercase">
                {user.full_name.slice(0, 2)}
              </div>
              <div className="leading-tight">
                <div className="text-xs font-medium">{user.full_name}</div>
                <div className="text-[10px] text-white/70">{user.roles.join(" · ")}</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};
